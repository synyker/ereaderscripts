from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

from articles import ArticleStore
from fetch_news import build_edition_from_store, feed_section_map, load_config

NOW = datetime(2026, 8, 5, 12, 0, tzinfo=timezone.utc)


def make_config(tmp_path: Path) -> dict:
    return {
        "output_dir": str(tmp_path / "articles"),
        "public_dir": str(tmp_path / "public"),
        "public_base_url": "https://news.example.com/news",
        "sections": ["Kotimaa", "Maailma"],
        "edition": {"window_hours": 24, "image_max_width": 480, "embed_images": False},
        "feeds": [
            {"name": "Yle Tuoreimmat", "url": "https://x/1", "section": "Kotimaa"},
            {"name": "HS Maailma", "url": "https://x/2", "section": "Maailma"},
            {"name": "No Section", "url": "https://x/3"},
        ],
    }


def test_feed_section_map_reads_the_section_key():
    mapping = feed_section_map(make_config(Path("/tmp")))

    assert mapping["Yle Tuoreimmat"] == "Kotimaa"


def test_feed_section_map_omits_feeds_without_a_section():
    mapping = feed_section_map(make_config(Path("/tmp")))

    assert "No Section" not in mapping


def test_build_writes_both_epub_and_catalog(tmp_path: Path):
    cfg = make_config(tmp_path)
    store = ArticleStore(Path(cfg["output_dir"]))
    store.save(
        article_id="aaaaaaaaaaaa",
        url="https://example.com/a",
        title="Otsikko",
        feed="Yle Tuoreimmat",
        published=NOW - timedelta(hours=2),
        html="<html><body><article><p>Sisalto</p></article></body></html>",
    )

    assert build_edition_from_store(cfg, store, now=NOW) is True
    assert (tmp_path / "public" / "news-latest.epub").exists()
    assert (tmp_path / "public" / "opds.xml").exists()


def test_catalog_href_is_built_from_the_public_base_url(tmp_path: Path):
    cfg = make_config(tmp_path)
    store = ArticleStore(Path(cfg["output_dir"]))
    store.save(
        article_id="aaaaaaaaaaaa",
        url="https://example.com/a",
        title="Otsikko",
        feed="Yle Tuoreimmat",
        published=NOW - timedelta(hours=2),
        html="<article><p>Sisalto</p></article>",
    )

    build_edition_from_store(cfg, store, now=NOW)

    xml = (tmp_path / "public" / "opds.xml").read_text(encoding="utf-8")
    assert "https://news.example.com/news/news-latest.epub" in xml


def test_empty_window_keeps_the_previous_edition(tmp_path: Path):
    cfg = make_config(tmp_path)
    store = ArticleStore(Path(cfg["output_dir"]))
    public = tmp_path / "public"
    public.mkdir(parents=True)
    (public / "news-latest.epub").write_bytes(b"previous edition")

    assert build_edition_from_store(cfg, store, now=NOW) is False
    assert (public / "news-latest.epub").read_bytes() == b"previous edition"


def write_minimal_config(tmp_path: Path) -> Path:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "public_base_url": "https://file.example.com/news",
                "kindle_host": "root@file-host",
                "kindle_ssh_key": "/file/ssh/key",
            }
        ),
        encoding="utf-8",
    )
    return config_path


def test_env_overrides_win_over_file_values(tmp_path: Path, monkeypatch):
    config_path = write_minimal_config(tmp_path)
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://env.example.com/news")
    monkeypatch.setenv("KINDLE_HOST", "root@env-host")
    monkeypatch.setenv("KINDLE_SSH_KEY", "/env/ssh/key")

    cfg = load_config(str(config_path))

    assert cfg["public_base_url"] == "https://env.example.com/news"
    assert cfg["kindle_host"] == "root@env-host"
    assert cfg["kindle_ssh_key"] == "/env/ssh/key"


def test_file_values_used_when_env_unset(tmp_path: Path, monkeypatch):
    config_path = write_minimal_config(tmp_path)
    monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)
    monkeypatch.delenv("KINDLE_HOST", raising=False)
    monkeypatch.delenv("KINDLE_SSH_KEY", raising=False)

    cfg = load_config(str(config_path))

    assert cfg["public_base_url"] == "https://file.example.com/news"
    assert cfg["kindle_host"] == "root@file-host"
    assert cfg["kindle_ssh_key"] == "/file/ssh/key"
