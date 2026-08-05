from datetime import datetime, timedelta, timezone
from pathlib import Path

import ebooklib
from ebooklib import epub as ebooklib_epub

from articles import Article
from epub import build_edition, group_into_sections
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


def test_spine_order_is_nav_masthead_then_articles_in_section_order(tmp_path: Path):
    groups = [
        ("Kotimaa", [written_article(tmp_path, "a", "Yle Tuoreimmat", "<p>A</p>")]),
        ("Maailma", [written_article(tmp_path, "b", "HS Maailma", "<p>B</p>", hours_ago=2)]),
    ]
    out = tmp_path / "news.epub"

    build_edition(groups, out, built_at=BUILT_AT)

    book = ebooklib_epub.read_epub(str(out))
    idrefs = [idref for idref, _linear in book.spine]
    assert idrefs == ["nav", "masthead", "article_a", "article_b"]


def test_existing_file_is_replaced_atomically(tmp_path: Path):
    out = tmp_path / "news.epub"
    out.write_bytes(b"stale")
    groups = [("Kotimaa", [written_article(tmp_path, "a", "Yle Tuoreimmat", "<p>A</p>")])]

    build_edition(groups, out, built_at=BUILT_AT)

    assert out.read_bytes() != b"stale"
    assert [p.name for p in tmp_path.iterdir() if p.suffix == ".tmp"] == []
