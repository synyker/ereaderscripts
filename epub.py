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

IMG_TAG_RE = re.compile(r"<img\b[^>]*>", re.IGNORECASE)


def build_edition(
    groups: list[tuple[str, list[Article]]],
    out_path: Path,
    *,
    built_at: datetime,
    image_cache: ImageCache | None = None,
    title: str = "News - Latest",
    feed_labels: dict[str, str] | None = None,
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
            # ("Otsikko · HS"). The article page itself already names the feed.
            label = (feed_labels or {}).get(article.feed)
            toc_title = f"{article.title} · {label}" if label else article.title

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
