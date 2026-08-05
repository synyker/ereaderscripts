from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from articles import Article, ArticleStore

NOW = datetime(2026, 8, 5, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def store(tmp_path: Path) -> ArticleStore:
    return ArticleStore(tmp_path)


def save(store: ArticleStore, article_id: str, *, hours_ago: int = 1, feed: str = "Yle Tuoreimmat"):
    return store.save(
        article_id=article_id,
        url=f"https://example.com/{article_id}",
        title=f"Title {article_id}",
        feed=feed,
        published=NOW - timedelta(hours=hours_ago),
        html="<html><body><article><p>Body text</p></article></body></html>",
    )


def test_has_is_false_for_unknown_id(store: ArticleStore):
    assert store.has("deadbeef1234") is False


def test_has_is_true_after_save(store: ArticleStore):
    save(store, "deadbeef1234")

    assert store.has("deadbeef1234") is True


def test_save_writes_html_and_meta_pair(store: ArticleStore):
    path = save(store, "deadbeef1234")

    assert path.suffix == ".html"
    assert path.with_suffix(".meta").exists()


def test_save_uses_feed_subdirectory_with_safe_name(store: ArticleStore):
    path = save(store, "deadbeef1234", feed="HS Pääkirjoitukset")

    assert path.parent.name == "HS_Pääkirjoitukset"


def test_since_returns_articles_inside_the_window(store: ArticleStore):
    save(store, "aaaaaaaaaaaa", hours_ago=1)

    found = store.since(24, now=NOW)

    assert [a.id for a in found] == ["aaaaaaaaaaaa"]


def test_since_excludes_articles_outside_the_window(store: ArticleStore):
    save(store, "aaaaaaaaaaaa", hours_ago=25)

    assert store.since(24, now=NOW) == []


def test_since_includes_article_exactly_at_the_boundary(store: ArticleStore):
    save(store, "aaaaaaaaaaaa", hours_ago=24)

    assert [a.id for a in store.since(24, now=NOW)] == ["aaaaaaaaaaaa"]


def test_since_returns_newest_first(store: ArticleStore):
    save(store, "older0000000", hours_ago=5)
    save(store, "newer0000000", hours_ago=1)

    assert [a.id for a in store.since(24, now=NOW)] == ["newer0000000", "older0000000"]


def test_article_carries_feed_name_from_meta(store: ArticleStore):
    save(store, "aaaaaaaaaaaa", feed="HS Maailma")

    assert store.since(24, now=NOW)[0].feed == "HS Maailma"


def test_article_feed_falls_back_to_directory_name(store: ArticleStore, tmp_path: Path):
    legacy_dir = tmp_path / "Legacy_Feed"
    legacy_dir.mkdir()
    (legacy_dir / "20260805_ffffffffffff.html").write_text(
        "<html><body><article><p>Legacy</p></article></body></html>", encoding="utf-8"
    )
    (legacy_dir / "20260805_ffffffffffff.meta").write_text(
        "url=https://example.com/legacy\ntitle=Legacy\ndate=2026-08-05 14:00\n", encoding="utf-8"
    )

    found = store.since(24, now=NOW)

    assert found[0].feed == "Legacy Feed"


def test_legacy_date_is_read_as_helsinki_local_time(store: ArticleStore, tmp_path: Path):
    legacy_dir = tmp_path / "Legacy_Feed"
    legacy_dir.mkdir()
    (legacy_dir / "20260805_ffffffffffff.html").write_text("<article><p>x</p></article>", encoding="utf-8")
    # 14:00 Helsinki in August (UTC+3) is 11:00 UTC
    (legacy_dir / "20260805_ffffffffffff.meta").write_text(
        "url=https://example.com/legacy\ntitle=Legacy\ndate=2026-08-05 14:00\n", encoding="utf-8"
    )

    found = store.since(24, now=NOW)

    assert found[0].published == datetime(2026, 8, 5, 11, 0, tzinfo=timezone.utc)


def test_body_returns_article_element_contents(store: ArticleStore):
    save(store, "aaaaaaaaaaaa")

    assert store.since(24, now=NOW)[0].body.strip() == "<p>Body text</p>"


def test_body_keeps_content_after_a_nested_article_element(store: ArticleStore):
    store.save(
        article_id="aaaaaaaaaaaa",
        url="https://example.com/aaaaaaaaaaaa",
        title="Title aaaaaaaaaaaa",
        feed="Yle Tuoreimmat",
        published=NOW - timedelta(hours=1),
        html=(
            "<html><body><article><p>Start</p>"
            "<article><p>Inner</p></article>"
            "<p>After inner</p></article></body></html>"
        ),
    )

    body = store.since(24, now=NOW)[0].body

    assert "Inner" in body
    assert "After inner" in body


def test_prune_deletes_old_pairs_and_reports_the_count(store: ArticleStore):
    save(store, "old000000000", hours_ago=24 * 5)
    save(store, "new000000000", hours_ago=2)

    deleted = store.prune(3, now=NOW)

    assert deleted == 1
    assert store.has("old000000000") is False
    assert store.has("new000000000") is True


def test_prune_removes_emptied_feed_directories(store: ArticleStore, tmp_path: Path):
    save(store, "old000000000", hours_ago=24 * 5, feed="Quiet Feed")

    store.prune(3, now=NOW)

    assert not (tmp_path / "Quiet_Feed").exists()
