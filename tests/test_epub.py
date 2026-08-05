from datetime import datetime, timedelta, timezone
from pathlib import Path

from articles import Article
from epub import group_into_sections

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
