"""Builds the news edition EPUB."""

import html as html_lib
import logging
import re
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from ebooklib import epub as ebooklib_epub

from articles import Article
from fsutil import atomic_write_bytes
from images import ImageCache, rewrite_images
from weather import (
    WeatherReport,
    direction_name,
    sky_description,
    symbol_label,
    wind_arrow,
)

log = logging.getLogger(__name__)

HELSINKI = ZoneInfo("Europe/Helsinki")


def group_into_sections(
    articles: list[Article],
    section_order: list[str],
    feed_sections: dict[str, str],
    fallback: str = "Muut",
) -> list[tuple[str, list[Article]]]:
    """Group articles into configured topic sections.

    Sections appear in `section_order`, with the fallback section last. Empty
    sections are omitted. Articles within a section are newest first.

    If a feed is configured for a section not in section_order and not the
    fallback, its articles are routed to the fallback section and a warning
    is logged to aid in diagnosing config errors.
    """
    valid_sections = set(section_order) | {fallback}
    buckets: dict[str, list[Article]] = {}
    for article in articles:
        section = feed_sections.get(article.feed, fallback)
        # If the section is not valid, route to fallback and warn
        if section not in valid_sections:
            log.warning(
                f"Feed '{article.feed}' is configured for unknown section '{section}'; "
                f"routing to fallback section '{fallback}'"
            )
            section = fallback
        buckets.setdefault(section, []).append(article)

    ordered = [name for name in section_order if name in buckets]
    if fallback in buckets:
        ordered.append(fallback)

    return [
        (name, sorted(buckets[name], key=lambda a: a.published, reverse=True))
        for name in ordered
    ]


STYLESHEET = """\
body { font-family: Georgia, 'Times New Roman', serif; line-height: 1.6;
       margin: 0; padding: 0.5em; color: #000; background: #fff;
       text-align: left; }
h1 { font-size: 1.3em; margin-bottom: 0.2em; }
h2 { font-size: 1.1em; margin: 0.5em 0; font-weight: bold; }
h3 { font-size: 1em; margin: 0.4em 0; font-weight: bold; }
.meta { font-size: 0.85em; color: #555; margin-bottom: 1em;
        border-bottom: 1px solid #ccc; padding-bottom: 0.5em; }
img { max-width: 100%; height: auto; }
p { margin: 0.8em 0; text-align: left; }
a { color: #000; text-decoration: underline; }
.reading { font-size: 1.7em; margin: 0.1em 0; }
.now { margin: 0.2em 0; }
.sun { font-size: 0.85em; color: #555; margin-top: 0.8em;
       border-top: 1px solid #ccc; padding-top: 0.4em; }
table.forecast { width: 100%; border-collapse: collapse; font-size: 0.85em;
                 margin: 0.8em 0; table-layout: fixed; }
table.forecast th, table.forecast td { text-align: center; padding: 0.3em 0.05em;
                                       border-bottom: 1px solid #ddd; }
table.forecast th.label { text-align: left; font-weight: normal; color: #555; }
"""

ARTICLE_TEMPLATE = """\
<h1>{title}</h1>
<div class="meta"><span>{feed}</span> &#183; <span>{published}</span></div>
{body}
"""

MASTHEAD_TEMPLATE = """\
<h1>News</h1>
<div class="meta">Rakennettu {built_at}</div>
<p>{summary}</p>
"""

SECTION_TEMPLATE = """\
<h1>{name}</h1>
<div class="meta">{count}</div>
"""

WEATHER_TEMPLATE = """\
<h1>Sää</h1>
<div class="meta">{place}havainto klo {observed_at}</div>
{now}
{table}
{sun}
"""

IMG_TAG_RE = re.compile(r"<img\b[^>]*>", re.IGNORECASE)


def render_weather(report: WeatherReport) -> str:
    """One page of weather: what it is now, then the hours ahead.

    The forecast is transposed — hours across, measurements down — because six
    labelled columns fit a 4" panel where six rows of prose do not.
    """
    return WEATHER_TEMPLATE.format(
        place=f"{html_lib.escape(report.place)} &#183; " if report.place else "",
        observed_at=report.observation.time.strftime("%-H:%M"),
        now="\n".join(_current_conditions(report.observation)),
        table=_forecast_table(report.hours),
        sun=_sun_line(report),
    )


def _current_conditions(observation) -> list[str]:
    """The station's own readings, skipping whatever it didn't report."""
    lines = []
    if observation.temperature is not None:
        lines.append(f'<p class="reading">{observation.temperature:.1f} &#176;C</p>')

    sky = sky_description(observation.cloud_cover)
    if sky:
        lines.append(f'<p class="now">{sky.capitalize()}</p>')

    if observation.wind_speed is not None:
        wind = f"Tuuli {observation.wind_speed:.1f} m/s"
        direction = direction_name(observation.wind_direction)
        if direction:
            wind += f" {direction}"
        if observation.wind_gust is not None:
            wind += f", puuskissa {observation.wind_gust:.1f} m/s"
        lines.append(f'<p class="now">{wind}</p>')

    details = []
    if observation.humidity is not None:
        details.append(f"kosteus {observation.humidity:.0f} %")
    if observation.dew_point is not None:
        details.append(f"kastepiste {observation.dew_point:.1f} &#176;C")
    if observation.pressure is not None:
        details.append(f"paine {observation.pressure:.0f} hPa")
    if observation.visibility is not None:
        details.append(_visibility(observation.visibility))
    if details:
        # Whichever reading survives leads the line, so capitalise after joining.
        line = " &#183; ".join(details)
        lines.append(f'<p class="now">{line[0].upper()}{line[1:]}</p>')

    return lines


def _visibility(metres: float) -> str:
    """The station tops out at 50 km, so anything far is reported as a floor."""
    if metres >= 20000:
        return "näkyvyys yli 20 km"
    return f"näkyvyys {metres / 1000:.0f} km"


def _forecast_table(hours: list) -> str:
    if not hours:
        return ""

    def row(label: str, values: list[str]) -> str:
        cells = "".join(f"<td>{value}</td>" for value in values)
        return f'<tr><th class="label">{label}</th>{cells}</tr>'

    headers = "".join(f"<th>{hour.time.strftime('%H')}</th>" for hour in hours)
    return (
        '<table class="forecast">\n'
        f'<tr><th class="label">klo</th>{headers}</tr>\n'
        + row("sää", [symbol_label(hour.symbol) for hour in hours]) + "\n"
        + row("&#176;C", [_round(hour.temperature) for hour in hours]) + "\n"
        + row("m/s", [_wind_cell(hour) for hour in hours]) + "\n"
        + row("mm", [_millimetres(hour.precipitation) for hour in hours]) + "\n"
        "</table>"
    )


def _round(value: float | None) -> str:
    return "-" if value is None else f"{round(value):.0f}"


def _millimetres(value: float | None) -> str:
    return "-" if value is None else f"{value:.1f}"


def _wind_cell(hour) -> str:
    """Wind and gust in one cell, "→3/6", to keep the table six columns wide."""
    if hour.wind_speed is None:
        return "-"
    cell = f"{wind_arrow(hour.wind_direction)}{round(hour.wind_speed)}"
    if hour.wind_gust is not None:
        cell += f"/{round(hour.wind_gust)}"
    return cell


def _sun_line(report: WeatherReport) -> str:
    sun = []
    if report.sunrise:
        sun.append(f"Aurinko nousee {report.sunrise.strftime('%-H:%M')}")
    if report.sunset:
        sun.append(f"laskee {report.sunset.strftime('%-H:%M')}")
    line = ", ".join(sun)

    if report.day_length:
        hours, minutes = divmod(report.day_length, 60)
        length = f"päivän pituus {hours} h {minutes} min"
        line = f"{line} &#183; {length}" if line else length

    return f'<div class="sun">{line}</div>' if line else ""


def build_edition(
    groups: list[tuple[str, list[Article]]],
    out_path: Path,
    *,
    built_at: datetime,
    image_cache: ImageCache | None = None,
    title: str = "News - Latest",
    feed_labels: dict[str, str] | None = None,
    weather: WeatherReport | None = None,
) -> Path:
    """Render one EPUB containing every article, grouped into sections.

    The title is deliberately plain ASCII: CrossPoint derives the SD-card
    filename from it, and sanitizes anything else away.
    """
    out_path = Path(out_path)
    local_built = built_at.astimezone(HELSINKI)

    book = ebooklib_epub.EpubBook()
    book.set_identifier(f"ereaderscripts-news-{local_built.strftime('%Y%m%d%H%M')}")
    book.set_title(title)
    book.set_language("fi")

    style = ebooklib_epub.EpubItem(
        uid="style", file_name="style.css", media_type="text/css", content=STYLESHEET
    )
    book.add_item(style)

    total = sum(len(items) for _, items in groups)
    masthead = _make_document(
        book,
        style,
        uid="masthead",
        file_name="masthead.xhtml",
        title="News",
        content=MASTHEAD_TEMPLATE.format(
            built_at=local_built.strftime("%-d.%-m.%Y %H:%M"),
            summary=f"{total} artikkelia, {len(groups)} osastoa.",
        ),
    )

    spine = [masthead]
    toc = []
    embedded_image_names: set[str] = set()

    # Weather leads the edition: it's what you check first over coffee, and a
    # failed FMI fetch simply leaves the page out rather than the whole build.
    if weather is not None:
        weather_page = _make_document(
            book,
            style,
            uid="weather",
            file_name="weather.xhtml",
            title="Sää",
            content=render_weather(weather),
        )
        spine.append(weather_page)
        toc.append(ebooklib_epub.Link(weather_page.file_name, "Sää", "weather"))

    for index, (section_name, articles) in enumerate(groups):
        # A real page per section: CrossPoint's TOC parsers only create entries
        # from links with an href, so a target-less ebooklib Section label is
        # silently dropped on the device. A section page gives the TOC entry a
        # target and marks the section boundary while reading.
        section_page = _make_document(
            book,
            style,
            uid=f"section_{index}",
            file_name=f"section_{index}.xhtml",
            title=section_name,
            content=SECTION_TEMPLATE.format(
                name=html_lib.escape(section_name),
                count=f"{len(articles)} artikkelia",
            ),
        )
        chapters = []
        for article in articles:
            body = article.body
            if image_cache is None:
                # Editions without embedded images shouldn't carry <img> tags
                # pointing at remote URLs the device can never load.
                body = IMG_TAG_RE.sub("", body)
            else:
                body, embedded = rewrite_images(body, image_cache)
                for name, data in embedded:
                    # Two articles can share the same wire photo; rewrite_images
                    # only dedupes within a single article, so dedup across the
                    # whole build here. Every article's <img src> is still
                    # rewritten to images/<name>, they just share one manifest
                    # item instead of colliding on it.
                    if name in embedded_image_names:
                        continue
                    embedded_image_names.add(name)
                    book.add_item(
                        ebooklib_epub.EpubImage(
                            uid=f"img_{name.rsplit('.', 1)[0]}",
                            file_name=f"images/{name}",
                            media_type="image/jpeg",
                            content=data,
                        )
                    )

            # Sections mix sources, so tag the TOC title with the feed's label
            # ("HS: Otsikko"). Prefixed, so it survives truncation on narrow
            # screens. The article page itself already names the feed.
            label = (feed_labels or {}).get(article.feed)
            toc_title = f"{label}: {article.title}" if label else article.title

            chapter = _make_document(
                book,
                style,
                uid=f"article_{article.id}",
                file_name=f"article_{article.id}.xhtml",
                title=toc_title,
                content=ARTICLE_TEMPLATE.format(
                    title=html_lib.escape(article.title),
                    feed=html_lib.escape(article.feed),
                    published=article.published.astimezone(HELSINKI).strftime("%-d.%-m. %H:%M"),
                    body=body,
                ),
            )
            chapters.append(chapter)

        spine.append(section_page)
        spine.extend(chapters)
        toc.append(
            (
                ebooklib_epub.Link(section_page.file_name, section_name, f"section_{index}"),
                tuple(chapters),
            )
        )

    book.toc = tuple(toc)
    # The nav document must exist in the book (EPUB 3), but keeping it out of
    # the spine stops readers from rendering the TOC as the first content page —
    # the device builds its own TOC from the same data.
    book.spine = spine
    book.add_item(ebooklib_epub.EpubNcx())
    book.add_item(ebooklib_epub.EpubNav())

    with tempfile.TemporaryDirectory() as workdir:
        staged = Path(workdir) / "edition.epub"
        ebooklib_epub.write_epub(str(staged), book)
        atomic_write_bytes(out_path, staged.read_bytes())

    return out_path


def _make_document(book, style, *, uid: str, file_name: str, title: str, content: str):
    document = ebooklib_epub.EpubHtml(uid=uid, title=title, file_name=file_name, lang="fi")
    document.content = f"<html><head><title>{html_lib.escape(title)}</title></head><body>{content}</body></html>"
    document.add_item(style)
    book.add_item(document)
    return document
