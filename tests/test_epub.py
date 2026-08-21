from datetime import datetime, timedelta, timezone
from pathlib import Path

import ebooklib
from ebooklib import epub as ebooklib_epub

from articles import Article
from epub import HELSINKI, build_edition, group_into_sections
from images import ImageCache

NOW = datetime(2026, 8, 5, 12, 0, tzinfo=timezone.utc)

SECTION_ORDER = ["Kotimaa", "Maailma", "Helsinki"]
FEED_SECTIONS = {
    "Yle Tuoreimmat": "Kotimaa",
    "HS Politiikka": "Kotimaa",
    "HS Maailma": "Maailma",
    "HS Helsinki": "Helsinki",
}


def article(article_id: str, feed: str, hours_ago: int = 1) -> Article:
    return Article(
        id=article_id,
        url=f"https://example.com/{article_id}",
        title=f"Title {article_id}",
        feed=feed,
        published=NOW - timedelta(hours=hours_ago),
        path=Path(f"/nonexistent/{article_id}.html"),
    )


def test_groups_articles_under_their_configured_section():
    groups = group_into_sections([article("a", "HS Maailma")], SECTION_ORDER, FEED_SECTIONS)

    assert groups == [("Maailma", [article("a", "HS Maailma")])]


def test_merges_multiple_feeds_into_one_section():
    articles = [article("a", "Yle Tuoreimmat"), article("b", "HS Politiikka", hours_ago=2)]

    groups = group_into_sections(articles, SECTION_ORDER, FEED_SECTIONS)

    assert [name for name, _ in groups] == ["Kotimaa"]
    assert [a.id for a in groups[0][1]] == ["a", "b"]


def test_sections_follow_configured_order_not_article_order():
    articles = [article("a", "HS Helsinki"), article("b", "Yle Tuoreimmat", hours_ago=2)]

    groups = group_into_sections(articles, SECTION_ORDER, FEED_SECTIONS)

    assert [name for name, _ in groups] == ["Kotimaa", "Helsinki"]


def test_empty_sections_are_omitted():
    groups = group_into_sections([article("a", "HS Maailma")], SECTION_ORDER, FEED_SECTIONS)

    assert [name for name, _ in groups] == ["Maailma"]


def test_unmapped_feed_lands_in_the_fallback_section():
    groups = group_into_sections([article("a", "Unknown Feed")], SECTION_ORDER, FEED_SECTIONS)

    assert groups[0][0] == "Muut"


def test_fallback_section_sorts_last():
    articles = [article("a", "Unknown Feed"), article("b", "HS Maailma", hours_ago=2)]

    groups = group_into_sections(articles, SECTION_ORDER, FEED_SECTIONS)

    assert [name for name, _ in groups] == ["Maailma", "Muut"]


def test_articles_within_a_section_are_newest_first():
    articles = [
        article("older", "Yle Tuoreimmat", hours_ago=5),
        article("newer", "HS Politiikka", hours_ago=1),
    ]

    groups = group_into_sections(articles, SECTION_ORDER, FEED_SECTIONS)

    assert [a.id for a in groups[0][1]] == ["newer", "older"]


def test_no_articles_produces_no_groups():
    assert group_into_sections([], SECTION_ORDER, FEED_SECTIONS) == []


def test_feed_mapped_to_invalid_section_lands_in_fallback():
    """If a feed is mapped to a section name not in section_order, route to fallback instead."""
    invalid_feed_sections = {
        "HS Maailma": "Maailma",
        "Unknown Feed": "Talous",  # "Talous" is not in SECTION_ORDER
    }
    groups = group_into_sections([article("a", "Unknown Feed")], SECTION_ORDER, invalid_feed_sections)

    assert groups[0][0] == "Muut"
    assert [a.id for a in groups[0][1]] == ["a"]


def test_invalid_section_articles_sort_with_other_fallback_articles():
    """Articles from invalid sections sort newest-first alongside unmapped feeds in fallback."""
    invalid_feed_sections = {
        "HS Maailma": "Maailma",
        "Unknown Feed": "Talous",  # "Talous" is not in SECTION_ORDER
    }
    articles = [
        article("from_invalid_section", "Unknown Feed", hours_ago=3),  # invalid section "Talous"
        article("from_true_unmapped", "Never Mapped", hours_ago=1),  # no mapping, uses fallback
    ]

    groups = group_into_sections(articles, SECTION_ORDER, invalid_feed_sections)

    assert groups[0][0] == "Muut"
    # Sorted newest-first: hours_ago=1 before hours_ago=3
    assert [a.id for a in groups[0][1]] == ["from_true_unmapped", "from_invalid_section"]


BUILT_AT = datetime(2026, 8, 5, 8, 17, tzinfo=timezone.utc)


def written_article(tmp_path: Path, article_id: str, feed: str, body: str, hours_ago: int = 1) -> Article:
    path = tmp_path / f"{article_id}.html"
    path.write_text(f"<html><body><article>{body}</article></body></html>", encoding="utf-8")
    return Article(
        id=article_id,
        url=f"https://example.com/{article_id}",
        title=f"Title {article_id}",
        feed=feed,
        published=NOW - timedelta(hours=hours_ago),
        path=path,
    )


def test_writes_a_readable_epub(tmp_path: Path):
    groups = [("Kotimaa", [written_article(tmp_path, "a", "Yle Tuoreimmat", "<p>Body</p>")])]
    out = tmp_path / "news.epub"

    build_edition(groups, out, built_at=BUILT_AT)

    assert ebooklib_epub.read_epub(str(out)) is not None


def test_title_is_the_stable_ascii_name(tmp_path: Path):
    groups = [("Kotimaa", [written_article(tmp_path, "a", "Yle Tuoreimmat", "<p>Body</p>")])]
    out = tmp_path / "news.epub"

    build_edition(groups, out, built_at=BUILT_AT)

    book = ebooklib_epub.read_epub(str(out))
    assert book.get_metadata("DC", "title")[0][0] == "News - Latest"


def test_every_article_becomes_a_document(tmp_path: Path):
    groups = [
        ("Kotimaa", [written_article(tmp_path, "a", "Yle Tuoreimmat", "<p>A</p>")]),
        ("Maailma", [written_article(tmp_path, "b", "HS Maailma", "<p>B</p>", hours_ago=2)]),
    ]
    out = tmp_path / "news.epub"

    build_edition(groups, out, built_at=BUILT_AT)

    book = ebooklib_epub.read_epub(str(out))
    names = [i.get_name() for i in book.get_items_of_type(ebooklib.ITEM_DOCUMENT)]
    assert "article_a.xhtml" in names
    assert "article_b.xhtml" in names


def test_toc_nests_articles_under_section_names(tmp_path: Path):
    groups = [
        ("Kotimaa", [written_article(tmp_path, "a", "Yle Tuoreimmat", "<p>A</p>")]),
        ("Maailma", [written_article(tmp_path, "b", "HS Maailma", "<p>B</p>", hours_ago=2)]),
    ]
    out = tmp_path / "news.epub"

    build_edition(groups, out, built_at=BUILT_AT)

    book = ebooklib_epub.read_epub(str(out))
    section_names = [entry[0].title for entry in book.toc if isinstance(entry, tuple)]
    assert section_names == ["Kotimaa", "Maailma"]


def test_article_body_appears_in_its_document(tmp_path: Path):
    groups = [("Kotimaa", [written_article(tmp_path, "a", "Yle Tuoreimmat", "<p>Distinctive body</p>")])]
    out = tmp_path / "news.epub"

    build_edition(groups, out, built_at=BUILT_AT)

    book = ebooklib_epub.read_epub(str(out))
    item = book.get_item_with_href("article_a.xhtml")
    assert b"Distinctive body" in item.get_content()


def test_masthead_carries_the_build_time(tmp_path: Path):
    groups = [("Kotimaa", [written_article(tmp_path, "a", "Yle Tuoreimmat", "<p>A</p>")])]
    out = tmp_path / "news.epub"

    build_edition(groups, out, built_at=BUILT_AT)

    book = ebooklib_epub.read_epub(str(out))
    masthead = book.get_item_with_href("masthead.xhtml").get_content().decode("utf-8")
    # 08:17 UTC is 11:17 in Helsinki in August
    assert "11:17" in masthead


def test_source_and_date_are_shown_for_each_article(tmp_path: Path):
    groups = [("Kotimaa", [written_article(tmp_path, "a", "Yle Tuoreimmat", "<p>A</p>")])]
    out = tmp_path / "news.epub"

    build_edition(groups, out, built_at=BUILT_AT)

    book = ebooklib_epub.read_epub(str(out))
    content = book.get_item_with_href("article_a.xhtml").get_content().decode("utf-8")
    assert "Yle Tuoreimmat" in content


def test_images_are_embedded_when_a_cache_is_given(tmp_path: Path):
    import io as _io

    from PIL import Image as _Image

    def fetcher(url: str) -> bytes:
        buf = _io.BytesIO()
        _Image.new("RGB", (100, 50), "blue").save(buf, format="PNG")
        return buf.getvalue()

    cache = ImageCache(tmp_path / "cache", max_width=480, fetcher=fetcher)
    body = '<p>A</p><img src="https://example.com/photo.png">'
    groups = [("Kotimaa", [written_article(tmp_path, "a", "Yle Tuoreimmat", body)])]
    out = tmp_path / "news.epub"

    build_edition(groups, out, built_at=BUILT_AT, image_cache=cache)

    book = ebooklib_epub.read_epub(str(out))
    images = list(book.get_items_of_type(ebooklib.ITEM_IMAGE))
    assert len(images) == 1


def test_shared_image_is_embedded_once_and_referenced_by_both_articles(tmp_path: Path):
    import io as _io

    from PIL import Image as _Image

    def fetcher(url: str) -> bytes:
        buf = _io.BytesIO()
        _Image.new("RGB", (100, 50), "blue").save(buf, format="PNG")
        return buf.getvalue()

    cache = ImageCache(tmp_path / "cache", max_width=480, fetcher=fetcher)
    body = '<p>A</p><img src="https://example.com/wire-photo.png">'
    groups = [
        ("Kotimaa", [written_article(tmp_path, "a", "Yle Tuoreimmat", body)]),
        ("Maailma", [written_article(tmp_path, "b", "HS Maailma", body, hours_ago=2)]),
    ]
    out = tmp_path / "news.epub"

    build_edition(groups, out, built_at=BUILT_AT, image_cache=cache)

    book = ebooklib_epub.read_epub(str(out))
    images = list(book.get_items_of_type(ebooklib.ITEM_IMAGE))
    assert len(images) == 1

    image_name = images[0].get_name().rsplit("/", 1)[-1]
    content_a = book.get_item_with_href("article_a.xhtml").get_content().decode("utf-8")
    content_b = book.get_item_with_href("article_b.xhtml").get_content().decode("utf-8")
    assert f"images/{image_name}" in content_a
    assert f"images/{image_name}" in content_b


def test_spine_is_masthead_then_articles_with_no_inline_toc_page(tmp_path: Path):
    groups = [
        ("Kotimaa", [written_article(tmp_path, "a", "Yle Tuoreimmat", "<p>A</p>")]),
        ("Maailma", [written_article(tmp_path, "b", "HS Maailma", "<p>B</p>", hours_ago=2)]),
    ]
    out = tmp_path / "news.epub"

    build_edition(groups, out, built_at=BUILT_AT)

    book = ebooklib_epub.read_epub(str(out))
    idrefs = [idref for idref, _linear in book.spine]
    assert idrefs == ["masthead", "section_0", "article_a", "section_1", "article_b"]


def test_section_pages_exist_and_toc_links_to_them(tmp_path: Path):
    groups = [
        ("Kotimaa", [written_article(tmp_path, "a", "Yle Tuoreimmat", "<p>A</p>")]),
        ("Maailma", [written_article(tmp_path, "b", "HS Maailma", "<p>B</p>", hours_ago=2)]),
    ]
    out = tmp_path / "news.epub"

    build_edition(groups, out, built_at=BUILT_AT)

    book = ebooklib_epub.read_epub(str(out))
    section_page = book.get_item_with_href("section_0.xhtml")
    assert b"Kotimaa" in section_page.get_content()
    # TOC parents must be links with an href: the device's TOC parsers drop
    # target-less labels, which flattened the TOC to bare article titles.
    parents = [entry[0] for entry in book.toc if isinstance(entry, tuple)]
    assert [p.href for p in parents] == ["section_0.xhtml", "section_1.xhtml"]


def test_feed_labels_are_appended_to_toc_titles(tmp_path: Path):
    groups = [
        ("Kotimaa", [
            written_article(tmp_path, "a", "HS Politiikka", "<p>A</p>"),
            written_article(tmp_path, "b", "Unlabeled Feed", "<p>B</p>", hours_ago=2),
        ]),
    ]
    out = tmp_path / "news.epub"

    build_edition(groups, out, built_at=BUILT_AT, feed_labels={"HS Politiikka": "HS"})

    book = ebooklib_epub.read_epub(str(out))
    children = [entry for section in book.toc if isinstance(section, tuple) for entry in section[1]]
    titles = {c.title for c in children}
    assert "HS: Title a" in titles           # labeled feed gets the prefix
    assert "Title b" in titles               # unlabeled feed stays plain
    # the article page's own heading keeps the plain title
    content = book.get_item_with_href("article_a.xhtml").get_content()
    assert b"<h1>Title a</h1>" in content


def test_without_image_cache_img_tags_are_stripped(tmp_path: Path):
    body = '<p>Before</p><img src="https://example.com/photo.jpg">\n<p>After</p>'
    groups = [("Kotimaa", [written_article(tmp_path, "a", "Yle Tuoreimmat", body)])]
    out = tmp_path / "news.epub"

    build_edition(groups, out, built_at=BUILT_AT)  # no image_cache

    content = ebooklib_epub.read_epub(str(out)).get_item_with_href("article_a.xhtml").get_content()
    assert b"<img" not in content
    assert b"Before" in content and b"After" in content


# --- Weather page ------------------------------------------------------------


def weather_report(**overrides):
    from weather import ForecastHour, Observation, WeatherReport

    def hour(offset: int, temperature: float, gust: float | None, symbol: int, rain: float = 0.0):
        return ForecastHour(
            time=datetime(2026, 8, 21, 8 + offset, tzinfo=timezone.utc).astimezone(HELSINKI),
            temperature=temperature,
            wind_speed=2.8,
            wind_gust=gust,
            wind_direction=270.0,
            precipitation=rain,
            symbol=symbol,
        )

    defaults = dict(
        place="Helsinki Kumpula",
        observation=Observation(
            time=datetime(2026, 8, 21, 4, 10, tzinfo=timezone.utc).astimezone(HELSINKI),
            temperature=12.1,
            dew_point=10.9,
            humidity=93.0,
            wind_speed=3.3,
            wind_gust=5.5,
            wind_direction=292.0,
            pressure=1003.8,
            cloud_cover=5.0,
            visibility=50000.0,
        ),
        hours=[
            hour(0, 13.31, 5.8, 4),
            hour(1, 14.7, 5.6, 4),
            hour(2, 15.84, 5.3, 2),
            hour(3, 17.04, 5.7, 1),
            hour(4, 17.74, 7.6, 1, rain=0.6),
            hour(5, 18.19, 8.5, 1),
        ],
        sunrise=datetime(2026, 8, 21, 2, 47, 43, tzinfo=timezone.utc).astimezone(HELSINKI),
        sunset=datetime(2026, 8, 21, 17, 57, 14, tzinfo=timezone.utc).astimezone(HELSINKI),
        day_length=910,
    )
    return WeatherReport(**{**defaults, **overrides})


def weather_page(tmp_path: Path, report=None) -> str:
    groups = [("Kotimaa", [written_article(tmp_path, "a", "Yle Tuoreimmat", "<p>A</p>")])]
    out = tmp_path / "news.epub"

    build_edition(groups, out, built_at=BUILT_AT, weather=report or weather_report())

    item = ebooklib_epub.read_epub(str(out)).get_item_with_href("weather.xhtml")
    return item.get_content().decode("utf-8")


def test_weather_page_comes_between_the_masthead_and_the_first_section(tmp_path: Path):
    groups = [("Kotimaa", [written_article(tmp_path, "a", "Yle Tuoreimmat", "<p>A</p>")])]
    out = tmp_path / "news.epub"

    build_edition(groups, out, built_at=BUILT_AT, weather=weather_report())

    book = ebooklib_epub.read_epub(str(out))
    idrefs = [idref for idref, _linear in book.spine]
    assert idrefs == ["masthead", "weather", "section_0", "article_a"]


def test_no_weather_page_when_fmi_gave_us_nothing(tmp_path: Path):
    groups = [("Kotimaa", [written_article(tmp_path, "a", "Yle Tuoreimmat", "<p>A</p>")])]
    out = tmp_path / "news.epub"

    build_edition(groups, out, built_at=BUILT_AT, weather=None)

    book = ebooklib_epub.read_epub(str(out))
    assert book.get_item_with_href("weather.xhtml") is None
    assert [idref for idref, _linear in book.spine] == ["masthead", "section_0", "article_a"]


def test_weather_is_the_first_toc_entry(tmp_path: Path):
    groups = [("Kotimaa", [written_article(tmp_path, "a", "Yle Tuoreimmat", "<p>A</p>")])]
    out = tmp_path / "news.epub"

    build_edition(groups, out, built_at=BUILT_AT, weather=weather_report())

    book = ebooklib_epub.read_epub(str(out))
    assert book.toc[0].title == "Sää"
    assert book.toc[0].href == "weather.xhtml"


def test_weather_page_names_the_station_and_the_observation_time(tmp_path: Path):
    content = weather_page(tmp_path)

    assert "Helsinki Kumpula" in content
    assert "7:10" in content  # 04:10Z in Helsinki


def test_weather_page_shows_the_current_conditions(tmp_path: Path):
    content = weather_page(tmp_path)

    assert "12.1" in content
    assert "Puolipilvistä" in content
    assert "lännestä" in content


def test_forecast_columns_are_the_coming_hours(tmp_path: Path):
    content = weather_page(tmp_path)

    for hour in ("11", "12", "13", "14", "15", "16"):  # 08-13Z in Helsinki
        assert f"<th>{hour}</th>" in content


def test_forecast_temperatures_are_rounded_to_whole_degrees(tmp_path: Path):
    content = weather_page(tmp_path)

    assert "<td>13</td>" in content  # 13.31
    assert "<td>18</td>" in content  # 18.19
    assert "13.31" not in content


def test_wind_and_gust_share_one_cell(tmp_path: Path):
    content = weather_page(tmp_path)

    assert "3/6" in content  # 2.8 m/s gusting 5.8


def test_wind_cell_holds_its_own_when_the_gust_is_missing(tmp_path: Path):
    report = weather_report(hours=[h.__class__(**{**h.__dict__, "wind_gust": None})
                                  for h in weather_report().hours])
    content = weather_page(tmp_path, report)

    assert "<td>→3</td>" in content
    assert "3/" not in content


def test_the_detail_line_opens_with_a_capital(tmp_path: Path):
    content = weather_page(tmp_path)

    assert "Kosteus 93 %" in content


def test_detail_line_capitalises_whatever_the_station_did_report(tmp_path: Path):
    """Humidity is often missing; the line still shouldn't start lowercase."""
    bare = weather_report()
    observation = bare.observation.__class__(**{**bare.observation.__dict__, "humidity": None})
    content = weather_page(tmp_path, weather_report(observation=observation))

    assert "Kastepiste 10.9" in content


def test_weather_page_reports_the_sun(tmp_path: Path):
    content = weather_page(tmp_path)

    assert "5:47" in content
    assert "20:57" in content
    assert "15 h 10 min" in content


def test_weather_page_carries_no_images(tmp_path: Path):
    """The X4 renders a text table far more reliably than icon art."""
    assert "<img" not in weather_page(tmp_path)


def test_dropped_rows_stay_out_of_the_forecast_table(tmp_path: Path):
    """Feels-like and humidity were cut to keep the table on one small page."""
    table = weather_page(tmp_path).split("<table")[1]

    assert "tuntuu" not in table
    assert "kosteus" not in table


def test_existing_file_is_replaced_atomically(tmp_path: Path):
    out = tmp_path / "news.epub"
    out.write_bytes(b"stale")
    groups = [("Kotimaa", [written_article(tmp_path, "a", "Yle Tuoreimmat", "<p>A</p>")])]

    build_edition(groups, out, built_at=BUILT_AT)

    assert out.read_bytes() != b"stale"
    assert [p.name for p in tmp_path.iterdir() if p.suffix == ".tmp"] == []
