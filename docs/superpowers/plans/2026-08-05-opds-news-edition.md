# OPDS News Edition Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publish the existing RSS news pipeline to an Xteink X4 (CrossPoint firmware) as a single daily EPUB edition served through an OPDS catalog, deployed with Docker Compose behind the user's existing nginx.

**Architecture:** Extract the on-disk article store into `articles.py`, then build two pure consumers of it — `epub.py` (renders one EPUB with topic sections and a nested TOC) and `opds.py` (writes a one-entry OPDS 1.2 Atom catalog). `fetch_news.py` keeps feed parsing, extraction and the Kindle SCP sync, and gains a `--build-edition` flag. Everything is published to a bind-mounted directory that nginx serves directly — no upstream, no proxy.

**Tech Stack:** Python 3.14, ebooklib (EPUB), Pillow (grayscale image conversion), lxml (already used), PyYAML, pytest, Docker Compose, supercronic.

**Spec:** [docs/superpowers/specs/2026-08-05-opds-news-edition-design.md](../specs/2026-08-05-opds-news-edition-design.md)

## Global Constraints

- **Do not run any git command.** The user performs all git actions themselves. Every task ends with a checkpoint listing the exact files to stage and a suggested commit message — stop there and let the user commit.
- **Never commit `ereader-news/`, `config.yaml`, `.env`, `.venv/`, or `.image-cache/`.** `.gitignore` already covers these.
- No host-specific paths, IP addresses, usernames or base URLs in any tracked file. Deployment values come from `.env` (gitignored) and `config.yaml` (gitignored).
- Python 3.11+ syntax is fine (`str | None`, `zoneinfo`); the venv runs 3.14.2.
- All datetimes are timezone-aware. Store and compare in UTC; display in `Europe/Helsinki`.
- The EPUB title stays exactly `News - Latest` — ASCII only, no em dashes or parentheses. CrossPoint derives the SD-card filename from it.
- Every generated file that nginx serves is written via `atomic_write_bytes` so a device downloading mid-rebuild never sees a partial file.
- Tests live in `tests/`, run with `.venv/bin/pytest`.

---

## File Structure

| File | Responsibility |
|---|---|
| `fsutil.py` (new) | Atomic file replacement |
| `articles.py` (new) | `Article` record + `ArticleStore` over the `.html`/`.meta` pairs |
| `images.py` (new) | Download, grayscale, downscale and cache article images |
| `epub.py` (new) | Group articles into sections; render the edition EPUB |
| `opds.py` (new) | Render the OPDS 1.2 Atom catalog |
| `init.py` (new) | Bootstrap a fresh checkout or deployment |
| `fetch_news.py` (modify) | Feeds, extraction, Kindle sync; delegates storage; adds `--build-edition` |
| `config.example.yaml` (new) | Committed config template |
| `.env.example` (new) | Committed deployment-variable template |
| `Dockerfile`, `docker-compose.yml`, `crontab` (new) | Deployment |
| `docs/nginx.example.conf` (new) | nginx snippet (documentation — nginx has no env interpolation) |
| `LICENSE` (new) | MIT |
| `tests/` (new) | pytest suite for all new modules |

---

## Task 1: Test infrastructure and atomic writes

**Files:**
- Create: `fsutil.py`
- Create: `tests/__init__.py` (empty)
- Create: `tests/test_fsutil.py`
- Modify: `requirements.txt`

**Interfaces:**
- Consumes: nothing
- Produces: `fsutil.atomic_write_bytes(path: Path, data: bytes) -> None`

Note `requirements.txt` is currently missing `pyyaml`, which `fetch_news.py:58` imports. That is fixed here.

- [ ] **Step 1: Add the new dependencies**

Replace `requirements.txt` with:

```
feedparser>=6.0
trafilatura>=1.6
readability-lxml>=0.8
requests>=2.31
lxml>=4.9
pyyaml>=6.0
ebooklib>=0.18
pillow>=10.0
pytest>=8.0
```

- [ ] **Step 2: Install them**

Run: `.venv/bin/pip install -r requirements.txt`
Expected: ebooklib, pillow and pytest install successfully.

- [ ] **Step 3: Write the failing test**

Create `tests/__init__.py` as an empty file, then create `tests/test_fsutil.py`:

```python
from pathlib import Path

from fsutil import atomic_write_bytes


def test_creates_file_with_content(tmp_path: Path):
    target = tmp_path / "out.bin"

    atomic_write_bytes(target, b"hello")

    assert target.read_bytes() == b"hello"


def test_replaces_existing_file(tmp_path: Path):
    target = tmp_path / "out.bin"
    target.write_bytes(b"old")

    atomic_write_bytes(target, b"new")

    assert target.read_bytes() == b"new"


def test_leaves_no_temp_files_behind(tmp_path: Path):
    atomic_write_bytes(tmp_path / "out.bin", b"data")

    assert [p.name for p in tmp_path.iterdir()] == ["out.bin"]


def test_creates_parent_directories(tmp_path: Path):
    target = tmp_path / "nested" / "deeper" / "out.bin"

    atomic_write_bytes(target, b"data")

    assert target.read_bytes() == b"data"
```

- [ ] **Step 4: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_fsutil.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'fsutil'`

- [ ] **Step 5: Write the implementation**

Create `fsutil.py`:

```python
"""Filesystem helpers shared by the publishing modules."""

import os
import tempfile
from pathlib import Path


def atomic_write_bytes(path: Path, data: bytes) -> None:
    """Write data to path so readers never observe a partial file.

    Writes to a temporary file in the same directory, then renames it over the
    target. os.replace is atomic within a filesystem, so a device downloading
    the previous version keeps reading it until the rename completes.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_fsutil.py -v`
Expected: 4 passed

- [ ] **Step 7: Checkpoint — hand off to the user for commit**

Files to stage: `requirements.txt`, `fsutil.py`, `tests/__init__.py`, `tests/test_fsutil.py`
Suggested message: `feat: add atomic write helper and test infrastructure`

Do not run git. Report the file list and stop.

---

## Task 2: Article store

**Files:**
- Create: `articles.py`
- Create: `tests/test_articles.py`

**Interfaces:**
- Consumes: `fsutil.atomic_write_bytes`
- Produces:
  - `articles.Article` — frozen dataclass with fields `id: str`, `url: str`, `title: str`, `feed: str`, `published: datetime` (aware, UTC), `path: Path`, and property `body: str`
  - `articles.ArticleStore(root: Path)` with `has(article_id) -> bool`, `save(*, article_id, url, title, feed, published, html) -> Path`, `since(hours, now=None) -> list[Article]`, `prune(days, now=None) -> int`
  - `articles.feed_dir_name(feed: str) -> str` — module-level function, also used by `init.py`

Two notes on the `.meta` format. The existing format writes `date=` as a **Helsinki-local** display string, but `cleanup_old_articles` parses it as UTC — a silent 3-hour skew. This task adds a `published=` line holding an ISO-8601 UTC timestamp and reads that when present, falling back to parsing `date=` as Helsinki-local for files written by the old code. It also adds the `feed=` line from the spec, falling back to the directory name.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_articles.py`:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_articles.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'articles'`

- [ ] **Step 3: Write the implementation**

Create `articles.py`:

```python
"""On-disk store of extracted articles.

Layout is unchanged from the original script: one directory per feed, holding
`<YYYYMMDD>_<id>.html` and a matching `.meta` sidecar. This module is the only
code that knows that layout; renderers consume Article records.
"""

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from functools import cached_property
from pathlib import Path
from zoneinfo import ZoneInfo

from fsutil import atomic_write_bytes

HELSINKI = ZoneInfo("Europe/Helsinki")
ID_PATTERN = re.compile(r"_([a-f0-9]{12})\.html$")
ARTICLE_PATTERN = re.compile(r"<article>(.*?)</article>", re.DOTALL)


def feed_dir_name(feed: str) -> str:
    """Filesystem-safe directory name for a feed's display name."""
    return re.sub(r"[^\w\s-]", "", feed).strip().replace(" ", "_")


@dataclass(frozen=True)
class Article:
    id: str
    url: str
    title: str
    feed: str
    published: datetime
    path: Path

    @cached_property
    def body(self) -> str:
        """The contents of the <article> element, or the whole file if absent."""
        html = self.path.read_text(encoding="utf-8")
        match = ARTICLE_PATTERN.search(html)
        return match.group(1) if match else html


class ArticleStore:
    def __init__(self, root: Path):
        self.root = Path(root)

    def has(self, article_id: str) -> bool:
        return any(self.root.rglob(f"*_{article_id}.html"))

    def save(
        self,
        *,
        article_id: str,
        url: str,
        title: str,
        feed: str,
        published: datetime,
        html: str,
    ) -> Path:
        published = published.astimezone(timezone.utc)
        feed_dir = self.root / feed_dir_name(feed)
        feed_dir.mkdir(parents=True, exist_ok=True)

        local = published.astimezone(HELSINKI)
        path = feed_dir / f"{local.strftime('%Y%m%d')}_{article_id}.html"
        atomic_write_bytes(path, html.encode("utf-8"))

        meta = (
            f"url={url}\n"
            f"title={title}\n"
            f"feed={feed}\n"
            f"date={local.strftime('%Y-%m-%d %H:%M')}\n"
            f"published={published.isoformat()}\n"
            f"fetched={datetime.now(timezone.utc).isoformat()}\n"
        )
        atomic_write_bytes(path.with_suffix(".meta"), meta.encode("utf-8"))
        return path

    def since(self, hours: int, now: datetime | None = None) -> list[Article]:
        """Articles published within the last `hours`, newest first."""
        now = now or datetime.now(timezone.utc)
        cutoff = now - timedelta(hours=hours)
        found = [a for a in self._all() if a.published >= cutoff]
        return sorted(found, key=lambda a: a.published, reverse=True)

    def prune(self, days: int, now: datetime | None = None) -> int:
        """Delete articles older than `days`. Returns the number removed."""
        now = now or datetime.now(timezone.utc)
        cutoff = now - timedelta(days=days)

        deleted = 0
        for article in self._all():
            if article.published < cutoff:
                article.path.unlink(missing_ok=True)
                article.path.with_suffix(".meta").unlink(missing_ok=True)
                deleted += 1

        for subdir in self.root.iterdir():
            if subdir.is_dir() and not any(subdir.iterdir()):
                subdir.rmdir()

        return deleted

    def _all(self) -> list[Article]:
        articles = []
        for html_path in self.root.rglob("*.html"):
            if html_path.name in ("index.html", "all.html", "all_links.html"):
                continue
            match = ID_PATTERN.search(html_path.name)
            if not match:
                continue
            article = self._read(html_path, match.group(1))
            if article:
                articles.append(article)
        return articles

    def _read(self, html_path: Path, article_id: str) -> Article | None:
        meta_path = html_path.with_suffix(".meta")
        if not meta_path.exists():
            return None

        fields: dict[str, str] = {}
        for line in meta_path.read_text(encoding="utf-8").splitlines():
            key, _, value = line.partition("=")
            if value:
                fields[key] = value

        published = self._parse_published(fields)
        if not published:
            return None

        return Article(
            id=article_id,
            url=fields.get("url", ""),
            title=fields.get("title", html_path.stem),
            feed=fields.get("feed") or html_path.parent.name.replace("_", " "),
            published=published,
            path=html_path,
        )

    @staticmethod
    def _parse_published(fields: dict[str, str]) -> datetime | None:
        if "published" in fields:
            try:
                return datetime.fromisoformat(fields["published"]).astimezone(timezone.utc)
            except ValueError:
                pass
        # Legacy files: `date=` holds Helsinki-local wall time with no offset.
        if "date" in fields:
            try:
                naive = datetime.strptime(fields["date"], "%Y-%m-%d %H:%M")
                return naive.replace(tzinfo=HELSINKI).astimezone(timezone.utc)
            except ValueError:
                pass
        return None
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_articles.py -v`
Expected: 15 passed

- [ ] **Step 5: Checkpoint — hand off to the user for commit**

Files to stage: `articles.py`, `tests/test_articles.py`
Suggested message: `feat: extract article store from fetch_news`

---

## Task 3: Section grouping

**Files:**
- Create: `epub.py`
- Create: `tests/test_epub.py`

**Interfaces:**
- Consumes: `articles.Article`
- Produces: `epub.group_into_sections(articles: list[Article], section_order: list[str], feed_sections: dict[str, str], fallback: str = "Muut") -> list[tuple[str, list[Article]]]`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_epub.py`:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_epub.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'epub'`

- [ ] **Step 3: Write the implementation**

Create `epub.py` with only the grouping function for now:

```python
"""Builds the news edition EPUB."""

from articles import Article


def group_into_sections(
    articles: list[Article],
    section_order: list[str],
    feed_sections: dict[str, str],
    fallback: str = "Muut",
) -> list[tuple[str, list[Article]]]:
    """Group articles into configured topic sections.

    Sections appear in `section_order`, with the fallback section last. Empty
    sections are omitted. Articles within a section are newest first.
    """
    buckets: dict[str, list[Article]] = {}
    for article in articles:
        section = feed_sections.get(article.feed, fallback)
        buckets.setdefault(section, []).append(article)

    ordered = [name for name in section_order if name in buckets]
    if fallback in buckets:
        ordered.append(fallback)

    return [
        (name, sorted(buckets[name], key=lambda a: a.published, reverse=True))
        for name in ordered
    ]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_epub.py -v`
Expected: 8 passed

- [ ] **Step 5: Checkpoint — hand off to the user for commit**

Files to stage: `epub.py`, `tests/test_epub.py`
Suggested message: `feat: group articles into topic sections`

---

## Task 4: Image processing and cache

**Files:**
- Create: `images.py`
- Create: `tests/test_images.py`

**Interfaces:**
- Consumes: nothing from earlier tasks
- Produces:
  - `images.ImageCache(root: Path, max_width: int, fetcher: Callable[[str], bytes | None] | None = None)` with `get(url: str) -> tuple[str, bytes] | None` returning `(filename, jpeg_bytes)`
  - `images.rewrite_images(html: str, cache: ImageCache) -> tuple[str, list[tuple[str, bytes]]]`

The `fetcher` parameter exists so tests never touch the network. Production passes nothing and gets a `requests`-based default.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_images.py`:

```python
import io
from pathlib import Path

import pytest
from PIL import Image

from images import ImageCache, rewrite_images


def png_bytes(width: int = 1200, height: int = 800, color: str = "red") -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), color).save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture
def cache(tmp_path: Path) -> ImageCache:
    return ImageCache(tmp_path, max_width=480, fetcher=lambda url: png_bytes())


def test_returns_filename_and_bytes(cache: ImageCache):
    result = cache.get("https://example.com/photo.png")

    assert result is not None
    name, data = result
    assert name.endswith(".jpg")
    assert data[:2] == b"\xff\xd8"  # JPEG magic


def test_downscales_to_max_width(cache: ImageCache):
    _, data = cache.get("https://example.com/photo.png")

    assert Image.open(io.BytesIO(data)).width == 480


def test_does_not_upscale_small_images(tmp_path: Path):
    cache = ImageCache(tmp_path, max_width=480, fetcher=lambda url: png_bytes(width=200, height=100))

    _, data = cache.get("https://example.com/small.png")

    assert Image.open(io.BytesIO(data)).width == 200


def test_output_is_greyscale_with_at_most_four_levels(cache: ImageCache):
    _, data = cache.get("https://example.com/photo.png")

    image = Image.open(io.BytesIO(data))
    assert image.mode == "L"
    # JPEG is lossy, so allow a tolerance band around each of the 4 target levels
    levels = {round(value / 64) for value, count in enumerate(image.histogram()) if count}
    assert len(levels) <= 4


def test_same_url_is_fetched_only_once(tmp_path: Path):
    calls = []

    def fetcher(url: str) -> bytes:
        calls.append(url)
        return png_bytes()

    cache = ImageCache(tmp_path, max_width=480, fetcher=fetcher)
    cache.get("https://example.com/photo.png")
    cache.get("https://example.com/photo.png")

    assert len(calls) == 1


def test_failed_download_returns_none(tmp_path: Path):
    cache = ImageCache(tmp_path, max_width=480, fetcher=lambda url: None)

    assert cache.get("https://example.com/gone.png") is None


def test_undecodable_bytes_return_none(tmp_path: Path):
    cache = ImageCache(tmp_path, max_width=480, fetcher=lambda url: b"not an image")

    assert cache.get("https://example.com/broken.png") is None


def test_rewrite_replaces_src_with_local_filename(cache: ImageCache):
    html = '<p>Text</p><img src="https://example.com/photo.png">'

    rewritten, embedded = rewrite_images(html, cache)

    assert "https://example.com/photo.png" not in rewritten
    assert embedded[0][0] in rewritten


def test_rewrite_returns_bytes_for_embedding(cache: ImageCache):
    html = '<img src="https://example.com/photo.png">'

    _, embedded = rewrite_images(html, cache)

    assert len(embedded) == 1
    assert embedded[0][1][:2] == b"\xff\xd8"


def test_rewrite_drops_images_that_fail(tmp_path: Path):
    cache = ImageCache(tmp_path, max_width=480, fetcher=lambda url: None)
    html = "<p>Keep this</p><img src=\"https://example.com/gone.png\">"

    rewritten, embedded = rewrite_images(html, cache)

    assert "Keep this" in rewritten
    assert "<img" not in rewritten
    assert embedded == []


def test_rewrite_deduplicates_repeated_images(cache: ImageCache):
    html = '<img src="https://example.com/photo.png"><img src="https://example.com/photo.png">'

    _, embedded = rewrite_images(html, cache)

    assert len(embedded) == 1


def test_rewrite_leaves_html_without_images_alone(cache: ImageCache):
    html = "<p>Just text</p>"

    rewritten, embedded = rewrite_images(html, cache)

    assert "Just text" in rewritten
    assert embedded == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_images.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'images'`

- [ ] **Step 3: Write the implementation**

Create `images.py`:

```python
"""Fetches article images and prepares them for a 4-level greyscale e-ink panel."""

import hashlib
import io
import logging
from collections.abc import Callable
from pathlib import Path

import requests
from lxml import etree
from PIL import Image, ImageOps

log = logging.getLogger(__name__)

TIMEOUT = 15


def _download(url: str) -> bytes | None:
    try:
        response = requests.get(url, timeout=TIMEOUT)
        response.raise_for_status()
        return response.content
    except requests.RequestException as e:
        log.debug("Image download failed for %s: %s", url, e)
        return None


class ImageCache:
    """Downloads, converts and caches images keyed by URL hash.

    Conversion is greyscale, downscaled to max_width, posterized to 4 levels
    (2 bits) to match the panel, and re-encoded as JPEG.
    """

    def __init__(
        self,
        root: Path,
        max_width: int,
        fetcher: Callable[[str], bytes | None] | None = None,
    ):
        self.root = Path(root)
        self.max_width = max_width
        self.fetcher = fetcher or _download

    def get(self, url: str) -> tuple[str, bytes] | None:
        """Return (filename, jpeg_bytes) for a URL, or None if unusable."""
        name = hashlib.sha256(url.encode()).hexdigest()[:16] + ".jpg"
        cached = self.root / name
        if cached.exists():
            return name, cached.read_bytes()

        raw = self.fetcher(url)
        if not raw:
            return None

        converted = self._convert(raw)
        if not converted:
            return None

        self.root.mkdir(parents=True, exist_ok=True)
        cached.write_bytes(converted)
        return name, converted

    def _convert(self, raw: bytes) -> bytes | None:
        try:
            image = Image.open(io.BytesIO(raw))
            image = ImageOps.exif_transpose(image).convert("L")
            if image.width > self.max_width:
                height = round(image.height * self.max_width / image.width)
                image = image.resize((self.max_width, height), Image.LANCZOS)
            image = ImageOps.posterize(image, 2)

            buf = io.BytesIO()
            image.save(buf, format="JPEG", quality=80, optimize=True)
            return buf.getvalue()
        except (OSError, ValueError) as e:
            log.debug("Image conversion failed: %s", e)
            return None


def rewrite_images(html: str, cache: ImageCache) -> tuple[str, list[tuple[str, bytes]]]:
    """Point <img> tags at embedded copies; drop the ones that fail.

    Returns the rewritten HTML fragment and the (filename, bytes) pairs the
    caller must add to the EPUB.
    """
    if "<img" not in html:
        return html, []

    root = etree.HTML(f"<div>{html}</div>")
    if root is None:
        return html, []

    embedded: dict[str, bytes] = {}
    for img in list(root.iter("img")):  # materialise: the loop removes elements
        src = (img.get("src") or "").strip()
        result = cache.get(src) if src.startswith("http") else None
        if not result:
            img.getparent().remove(img)
            continue
        name, data = result
        embedded[name] = data
        img.set("src", f"images/{name}")
        img.attrib.pop("srcset", None)

    body = root.find(".//div")
    rewritten = "".join(
        [body.text or ""] + [etree.tostring(child, encoding="unicode", method="html") for child in body]
    )
    return rewritten, list(embedded.items())
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_images.py -v`
Expected: 12 passed

- [ ] **Step 5: Checkpoint — hand off to the user for commit**

Files to stage: `images.py`, `tests/test_images.py`
Suggested message: `feat: add greyscale image conversion and cache`

---

## Task 5: EPUB edition builder

**Files:**
- Modify: `epub.py` (append; keep `group_into_sections` as written)
- Modify: `tests/test_epub.py` (append)

**Interfaces:**
- Consumes: `articles.Article`, `epub.group_into_sections`, `images.ImageCache`, `images.rewrite_images`, `fsutil.atomic_write_bytes`
- Produces: `epub.build_edition(groups: list[tuple[str, list[Article]]], out_path: Path, *, built_at: datetime, image_cache: ImageCache | None = None, title: str = "News - Latest") -> Path`

`ebooklib` writes only to a real path, so the builder writes into a temporary directory and then publishes the bytes through `atomic_write_bytes`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_epub.py`:

```python
import ebooklib
from ebooklib import epub as ebooklib_epub

from epub import build_edition
from images import ImageCache

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


def test_existing_file_is_replaced_atomically(tmp_path: Path):
    out = tmp_path / "news.epub"
    out.write_bytes(b"stale")
    groups = [("Kotimaa", [written_article(tmp_path, "a", "Yle Tuoreimmat", "<p>A</p>")])]

    build_edition(groups, out, built_at=BUILT_AT)

    assert out.read_bytes() != b"stale"
    assert [p.name for p in tmp_path.iterdir() if p.suffix == ".tmp"] == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_epub.py -v`
Expected: the 8 grouping tests pass; the 9 new ones FAIL with `ImportError: cannot import name 'build_edition'`

- [ ] **Step 3: Write the implementation**

Append to `epub.py` (and add the imports at the top of the file):

```python
import html as html_lib
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from ebooklib import epub as ebooklib_epub

from fsutil import atomic_write_bytes
from images import ImageCache, rewrite_images

HELSINKI = ZoneInfo("Europe/Helsinki")

STYLESHEET = """\
body { font-family: Georgia, 'Times New Roman', serif; line-height: 1.6;
       margin: 0; padding: 0.5em; color: #000; background: #fff; }
h1 { font-size: 1.3em; margin-bottom: 0.2em; }
h2 { font-size: 1.1em; margin: 0.5em 0; font-weight: bold; }
h3 { font-size: 1em; margin: 0.4em 0; font-weight: bold; }
.meta { font-size: 0.85em; color: #555; margin-bottom: 1em;
        border-bottom: 1px solid #ccc; padding-bottom: 0.5em; }
img { max-width: 100%; height: auto; }
p { margin: 0.8em 0; }
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


def build_edition(
    groups: list[tuple[str, list[Article]]],
    out_path: Path,
    *,
    built_at: datetime,
    image_cache: ImageCache | None = None,
    title: str = "News - Latest",
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

    for section_name, articles in groups:
        chapters = []
        for article in articles:
            body = article.body
            if image_cache is not None:
                body, embedded = rewrite_images(body, image_cache)
                for name, data in embedded:
                    book.add_item(
                        ebooklib_epub.EpubImage(
                            uid=f"img_{name.rsplit('.', 1)[0]}",
                            file_name=f"images/{name}",
                            media_type="image/jpeg",
                            content=data,
                        )
                    )

            chapter = _make_document(
                book,
                style,
                uid=f"article_{article.id}",
                file_name=f"article_{article.id}.xhtml",
                title=article.title,
                content=ARTICLE_TEMPLATE.format(
                    title=html_lib.escape(article.title),
                    feed=html_lib.escape(article.feed),
                    published=article.published.astimezone(HELSINKI).strftime("%-d.%-m. %H:%M"),
                    body=body,
                ),
            )
            chapters.append(chapter)

        spine.extend(chapters)
        toc.append((ebooklib_epub.Section(section_name), tuple(chapters)))

    book.toc = tuple(toc)
    book.spine = ["nav"] + spine
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_epub.py -v`
Expected: 17 passed

- [ ] **Step 5: Checkpoint — hand off to the user for commit**

Files to stage: `epub.py`, `tests/test_epub.py`
Suggested message: `feat: render the news edition as an EPUB`

---

## Task 6: OPDS catalog

**Files:**
- Create: `opds.py`
- Create: `tests/test_opds.py`

**Interfaces:**
- Consumes: `fsutil.atomic_write_bytes`
- Produces:
  - `opds.CatalogEntry` — frozen dataclass with `id: str`, `title: str`, `updated: datetime`, `href: str`
  - `opds.build_catalog(entries: list[CatalogEntry], *, feed_id: str, feed_title: str, updated: datetime, self_href: str | None = None) -> bytes`
  - `opds.write_catalog(path: Path, entries: list[CatalogEntry], *, feed_id: str, feed_title: str, updated: datetime, self_href: str | None = None) -> Path`

The conformance tests encode CrossPoint's parser rules from `lib/OpdsParser/OpdsParser.cpp`. Violating any of them makes the catalog render as empty on the device, with no error shown — so these are the highest-value tests in the suite.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_opds.py`:

```python
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree

from opds import CatalogEntry, build_catalog, write_catalog

ATOM = "{http://www.w3.org/2005/Atom}"
UPDATED = datetime(2026, 8, 5, 8, 17, tzinfo=timezone.utc)

ENTRY = CatalogEntry(
    id="urn:ereaderscripts:news:latest",
    title="News - Latest",
    updated=UPDATED,
    href="https://news.example.com/news/news-latest.epub",
)


def parse(xml: bytes) -> ElementTree.Element:
    return ElementTree.fromstring(xml)


def catalog() -> ElementTree.Element:
    return parse(build_catalog([ENTRY], feed_id="urn:x:news", feed_title="News", updated=UPDATED))


def test_root_is_an_atom_feed():
    assert catalog().tag == f"{ATOM}feed"


def test_feed_has_id_title_and_updated():
    root = catalog()

    assert root.find(f"{ATOM}id").text == "urn:x:news"
    assert root.find(f"{ATOM}title").text == "News"
    assert root.find(f"{ATOM}updated").text.startswith("2026-08-05T08:17:00")


def test_entry_title_is_non_empty():
    """CrossPoint silently drops entries with an empty title."""
    entry = catalog().find(f"{ATOM}entry")

    assert entry.find(f"{ATOM}title").text == "News - Latest"


def test_acquisition_link_uses_the_exact_rel_the_firmware_matches():
    entry = catalog().find(f"{ATOM}entry")
    link = entry.find(f"{ATOM}link")

    assert "opds-spec.org/acquisition" in link.get("rel")


def test_acquisition_link_type_is_exactly_epub_zip():
    """The firmware compares this with strcmp — no parameters, no variations."""
    link = catalog().find(f"{ATOM}entry").find(f"{ATOM}link")

    assert link.get("type") == "application/epub+zip"


def test_href_is_absolute_and_contains_dot_epub():
    """The firmware prefers hrefs containing '.epub' or '/epub/'."""
    href = catalog().find(f"{ATOM}entry").find(f"{ATOM}link").get("href")

    assert href.startswith("https://")
    assert ".epub" in href


def test_entry_has_no_author_so_the_filename_stays_clean():
    """Filename is '<author> - <title>.epub'; no author yields '<title>.epub'."""
    entry = catalog().find(f"{ATOM}entry")

    assert entry.find(f"{ATOM}author") is None


def test_feed_declares_a_self_link_of_atom_type():
    links = catalog().findall(f"{ATOM}link")

    assert any(link.get("rel") == "self" for link in links)


def test_multiple_entries_are_all_present():
    second = CatalogEntry(
        id="urn:ereaderscripts:news:archive",
        title="News - 2026-08-04",
        updated=UPDATED,
        href="https://news.example.com/news/news-2026-08-04.epub",
    )

    root = parse(build_catalog([ENTRY, second], feed_id="urn:x:news", feed_title="News", updated=UPDATED))

    assert len(root.findall(f"{ATOM}entry")) == 2


def test_titles_are_xml_escaped():
    entry = CatalogEntry(id="urn:x:1", title="News & Weather", updated=UPDATED, href="https://x/a.epub")

    root = parse(build_catalog([entry], feed_id="urn:x:news", feed_title="News", updated=UPDATED))

    assert root.find(f"{ATOM}entry").find(f"{ATOM}title").text == "News & Weather"


def test_write_catalog_writes_parseable_xml(tmp_path: Path):
    out = tmp_path / "opds.xml"

    write_catalog(out, [ENTRY], feed_id="urn:x:news", feed_title="News", updated=UPDATED)

    assert parse(out.read_bytes()).tag == f"{ATOM}feed"


def test_write_catalog_replaces_an_existing_file(tmp_path: Path):
    out = tmp_path / "opds.xml"
    out.write_bytes(b"stale")

    write_catalog(out, [ENTRY], feed_id="urn:x:news", feed_title="News", updated=UPDATED)

    assert b"stale" not in out.read_bytes()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_opds.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'opds'`

- [ ] **Step 3: Write the implementation**

Create `opds.py`:

```python
"""Writes the OPDS 1.2 Atom catalog the Xteink X4 browses.

CrossPoint's parser (lib/OpdsParser/OpdsParser.cpp) recognises a book entry
only when it holds a <link> whose rel contains 'opds-spec.org/acquisition' and
whose type is exactly 'application/epub+zip'. Entries without a non-empty title
or an href are dropped silently, so this module keeps those invariants.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree

from fsutil import atomic_write_bytes

ATOM_NS = "http://www.w3.org/2005/Atom"
ACQUISITION_REL = "http://opds-spec.org/acquisition"
EPUB_TYPE = "application/epub+zip"
CATALOG_TYPE = "application/atom+xml;profile=opds-catalog;kind=acquisition"


@dataclass(frozen=True)
class CatalogEntry:
    id: str
    title: str
    updated: datetime
    href: str


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_catalog(
    entries: list[CatalogEntry],
    *,
    feed_id: str,
    feed_title: str,
    updated: datetime,
    self_href: str | None = None,
) -> bytes:
    ElementTree.register_namespace("", ATOM_NS)
    feed = ElementTree.Element(f"{{{ATOM_NS}}}feed")

    ElementTree.SubElement(feed, f"{{{ATOM_NS}}}id").text = feed_id
    ElementTree.SubElement(feed, f"{{{ATOM_NS}}}title").text = feed_title
    ElementTree.SubElement(feed, f"{{{ATOM_NS}}}updated").text = _iso(updated)
    ElementTree.SubElement(
        feed,
        f"{{{ATOM_NS}}}link",
        {"rel": "self", "type": CATALOG_TYPE, "href": self_href or "opds.xml"},
    )

    for entry in entries:
        element = ElementTree.SubElement(feed, f"{{{ATOM_NS}}}entry")
        ElementTree.SubElement(element, f"{{{ATOM_NS}}}id").text = entry.id
        ElementTree.SubElement(element, f"{{{ATOM_NS}}}title").text = entry.title
        ElementTree.SubElement(element, f"{{{ATOM_NS}}}updated").text = _iso(entry.updated)
        ElementTree.SubElement(
            element,
            f"{{{ATOM_NS}}}link",
            {"rel": ACQUISITION_REL, "type": EPUB_TYPE, "href": entry.href},
        )

    return ElementTree.tostring(feed, encoding="utf-8", xml_declaration=True)


def write_catalog(
    path: Path,
    entries: list[CatalogEntry],
    *,
    feed_id: str,
    feed_title: str,
    updated: datetime,
    self_href: str | None = None,
) -> Path:
    xml = build_catalog(
        entries, feed_id=feed_id, feed_title=feed_title, updated=updated, self_href=self_href
    )
    atomic_write_bytes(Path(path), xml)
    return Path(path)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_opds.py -v`
Expected: 12 passed

- [ ] **Step 5: Checkpoint — hand off to the user for commit**

Files to stage: `opds.py`, `tests/test_opds.py`
Suggested message: `feat: generate the OPDS catalog`

---

## Task 7: Wire the edition build into fetch_news.py

**Files:**
- Modify: `fetch_news.py` — `DEFAULT_CONFIG` (line 46-53), `process_feed` (line 526-609), `main` (line 1005-1092); delete `get_existing_article_ids` (line 612-620) and `cleanup_old_articles` (line 625-664)
- Create: `tests/test_edition_config.py`

**Interfaces:**
- Consumes: `articles.ArticleStore`, `epub.group_into_sections`, `epub.build_edition`, `images.ImageCache`, `opds.CatalogEntry`, `opds.write_catalog`
- Produces:
  - `fetch_news.feed_section_map(cfg: dict) -> dict[str, str]`
  - `fetch_news.build_edition_from_store(cfg: dict, store: ArticleStore, now: datetime) -> bool` — returns False when the window is empty and nothing was published
  - CLI flag `--build-edition`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_edition_config.py`:

```python
from datetime import datetime, timedelta, timezone
from pathlib import Path

from articles import ArticleStore
from fetch_news import build_edition_from_store, feed_section_map

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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_edition_config.py -v`
Expected: FAIL — `ImportError: cannot import name 'build_edition_from_store' from 'fetch_news'`

- [ ] **Step 3: Add the new imports and config defaults to fetch_news.py**

At the top of `fetch_news.py`, after the existing imports, add:

```python
from articles import ArticleStore
from epub import build_edition, group_into_sections
from images import ImageCache
from opds import CatalogEntry, write_catalog
```

Replace `DEFAULT_CONFIG` (currently lines 46-53) with:

```python
DEFAULT_CONFIG = {
    "output_dir": "ereader-news",
    "public_dir": "public",
    "public_base_url": "http://localhost:8000",
    "image_cache_dir": ".image-cache",
    "max_age_days": 3,
    "max_articles_per_feed": 15,
    "kindle_host": "root@192.168.1.x",
    "kindle_news_dir": "/mnt/us/koreader/news",
    "sections": [],
    "edition": {
        "window_hours": 24,
        "image_max_width": 480,
        "embed_images": True,
    },
    "feeds": [],
}
```

In `load_config`, after the existing `cfg["output_dir"] = os.path.expanduser(...)` line, add the environment overrides:

```python
    for key, env_var in (
        ("public_base_url", "PUBLIC_BASE_URL"),
        ("kindle_host", "KINDLE_HOST"),
        ("kindle_ssh_key", "KINDLE_SSH_KEY"),
    ):
        if os.environ.get(env_var):
            cfg[key] = os.environ[env_var]

    # Resolve relative paths against the project root, not the working
    # directory, so cron and `docker compose run` agree with init.py.
    project_root = Path(__file__).parent
    for key in ("output_dir", "public_dir", "image_cache_dir"):
        path = Path(os.path.expanduser(cfg[key]))
        cfg[key] = str(path if path.is_absolute() else project_root / path)
```

This replaces the existing `cfg["output_dir"] = os.path.expanduser(cfg["output_dir"])` line — delete that line, since the loop above now covers it.

- [ ] **Step 4: Add the edition builder to fetch_news.py**

Insert this after the `generate_all_links` function, before the `# --- Main ---` banner:

```python
# --- Edition (EPUB + OPDS) ---------------------------------------------------

def feed_section_map(cfg: dict) -> dict[str, str]:
    """Map each feed's display name to its configured topic section."""
    return {
        feed["name"]: feed["section"]
        for feed in cfg.get("feeds", [])
        if feed.get("section")
    }


def build_edition_from_store(cfg: dict, store: ArticleStore, now: datetime) -> bool:
    """Build the EPUB edition and OPDS catalog. False if there was nothing to publish."""
    edition_cfg = cfg.get("edition", {})
    window_hours = edition_cfg.get("window_hours", 24)

    selected = store.since(window_hours, now=now)
    if not selected:
        log.warning(
            "No articles in the last %dh; keeping the previous edition", window_hours
        )
        return False

    groups = group_into_sections(selected, cfg.get("sections", []), feed_section_map(cfg))

    image_cache = None
    if edition_cfg.get("embed_images", True):
        image_cache = ImageCache(
            Path(cfg["image_cache_dir"]), edition_cfg.get("image_max_width", 480)
        )

    public_dir = Path(cfg["public_dir"])
    epub_path = public_dir / "news-latest.epub"
    build_edition(groups, epub_path, built_at=now, image_cache=image_cache)
    log.info(
        "Built %s: %d articles in %d sections", epub_path.name, len(selected), len(groups)
    )

    base_url = cfg["public_base_url"].rstrip("/")
    write_catalog(
        public_dir / "opds.xml",
        [
            CatalogEntry(
                id="urn:ereaderscripts:news:latest",
                title="News - Latest",
                updated=now,
                href=f"{base_url}/news-latest.epub",
            )
        ],
        feed_id="urn:ereaderscripts:news",
        feed_title="News",
        updated=now,
        self_href=f"{base_url}/opds.xml",
    )
    log.info("Wrote OPDS catalog to %s", public_dir / "opds.xml")
    return True
```

- [ ] **Step 5: Replace the storage calls in process_feed**

Change the signature to `process_feed(feed_cfg: dict, store: ArticleStore) -> int`, returning the count of new articles. Delete these three lines from the middle of the function — `ArticleStore.save` now owns directory creation:

```python
    safe_name = re.sub(r'[^\w\s-]', '', feed_name).strip().replace(' ', '_')
    feed_dir = output_dir / safe_name
    feed_dir.mkdir(parents=True, exist_ok=True)
```

Then replace everything from `created_files = []` to the end of the function with:

```python
    created = 0

    for entry in entries:
        aid = article_id(entry["link"])

        if store.has(aid):
            log.info("  Skipping (already exists): %s", entry["title"][:60])
            continue

        log.info("  Fetching: %s", entry["title"][:60])

        raw_html = fetch_page(entry["link"])
        if not raw_html:
            continue

        extracted = extract_article(raw_html, entry["link"])
        if not extracted:
            log.warning("  Could not extract article content: %s", entry["link"])
            continue

        title = sanitize_content(clean_extracted_html(extracted["title"] or entry["title"]))
        cleaned_content = clean_extracted_html(extracted["content"])
        article_html = HTML_TEMPLATE.format(
            lang="fi",
            title=html.escape(title),
            source=html.escape(feed_name),
            date=html.escape(entry["date"]),
            content=sanitize_content(cleaned_content),
        )

        published = entry.get("published_at") or datetime.now(timezone.utc)
        path = store.save(
            article_id=aid,
            url=entry["link"],
            title=title,
            feed=feed_name,
            published=published,
            html=article_html,
        )
        created += 1
        log.info("  Saved: %s", path.name)

        # Be polite to servers
        time.sleep(1)

    return created
```

In `parse_feed` and `scrape_articles`, add the aware datetime to each entry dict alongside the display string — in both functions, change `entries.append({...})` to include `"published_at": pub_datetime`.

- [ ] **Step 6: Rewrite main()**

Replace `main()` with:

```python
def main():
    parser = argparse.ArgumentParser(
        description="Fetch RSS news as clean HTML and publish it to e-readers"
    )
    parser.add_argument("--config", default="config.yaml",
                        help="Path to YAML config file (default: config.yaml)")
    parser.add_argument("--sync", action="store_true",
                        help="Sync downloaded news to Kindle after fetching")
    parser.add_argument("--build-edition", action="store_true",
                        help="Build the EPUB edition and OPDS catalog after fetching")
    parser.add_argument("--clean-only", action="store_true",
                        help="Only clean old articles, don't fetch new ones")
    parser.add_argument("--feed-url", type=str,
                        help="Fetch a single feed URL (ignores config feeds)")
    parser.add_argument("--rss-file", type=str,
                        help="Parse a local RSS file instead of fetching from URL")
    args = parser.parse_args()

    script_dir = Path(__file__).parent
    config_path = args.config
    if not os.path.isabs(config_path):
        config_path = script_dir / config_path

    cfg = load_config(str(config_path))
    output_dir = Path(cfg["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    store = ArticleStore(output_dir)
    now = datetime.now(timezone.utc)

    log.info("Cleaning articles older than %d days...", cfg["max_age_days"])
    log.info("Deleted %d old articles", store.prune(cfg["max_age_days"], now=now))

    if args.clean_only:
        return

    feeds = cfg["feeds"]
    if args.feed_url:
        feeds = [{"name": "CLI Feed", "url": args.feed_url}]
    elif args.rss_file:
        rss_path = Path(args.rss_file).resolve()
        if not rss_path.exists():
            log.error("RSS file not found: %s", args.rss_file)
            sys.exit(1)
        feeds = [{"name": rss_path.stem, "url": str(rss_path)}]

    if not feeds:
        log.error("No feeds configured. Edit config.yaml to add RSS feed URLs.")
        sys.exit(1)

    total_new = sum(process_feed(feed_cfg, store) for feed_cfg in feeds)
    log.info("Fetched %d new articles total", total_new)

    generate_index(output_dir)
    generate_all_articles(output_dir)
    generate_all_links(output_dir)

    if args.build_edition:
        build_edition_from_store(cfg, store, now=now)

    if args.sync:
        sync_to_kindle_scp(
            output_dir,
            cfg["kindle_host"],
            cfg["kindle_news_dir"],
            cfg.get("kindle_ssh_key"),
            cfg.get("kindle_ssh_port", 22),
        )
```

Then delete `get_existing_article_ids` and `cleanup_old_articles` — `ArticleStore` replaces both.

- [ ] **Step 7: Run the whole suite**

Run: `.venv/bin/pytest tests/ -v`
Expected: all tests pass (5 new in `test_edition_config.py`)

- [ ] **Step 8: Verify the real pipeline still runs**

Run: `.venv/bin/python fetch_news.py --clean-only`
Expected: exits 0, logs a prune count, does not crash on the existing `ereader-news/` content.

- [ ] **Step 9: Checkpoint — hand off to the user for commit**

Files to stage: `fetch_news.py`, `tests/test_edition_config.py`
Suggested message: `feat: add --build-edition and delegate storage to ArticleStore`

---

## Task 8: Bootstrap script and example configuration

**Files:**
- Create: `init.py`
- Create: `config.example.yaml`
- Create: `.env.example`
- Create: `tests/test_init.py`
- Modify: `config.yaml` (local, gitignored — add the new keys so the local run keeps working)

**Interfaces:**
- Consumes: `articles.feed_dir_name`
- Produces: `init.bootstrap(config_path: Path, project_root: Path) -> list[str]` returning human-readable messages about what it did

Per-feed directories are already created at runtime by `process_feed`; this script exists because `config.yaml` and `.env` are gitignored and the `public/` and `.image-cache/` directories have no other creator.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_init.py`:

```python
from pathlib import Path

import pytest
import yaml

from init import bootstrap

CONFIG = {
    "output_dir": "articles",
    "public_dir": "public",
    "image_cache_dir": ".image-cache",
    "feeds": [{"name": "Yle Tuoreimmat", "url": "https://x/1"}],
}


def write_config(tmp_path: Path) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(CONFIG), encoding="utf-8")
    return path


def test_creates_the_output_directory(tmp_path: Path):
    bootstrap(write_config(tmp_path), tmp_path)

    assert (tmp_path / "articles").is_dir()


def test_creates_a_directory_per_feed(tmp_path: Path):
    bootstrap(write_config(tmp_path), tmp_path)

    assert (tmp_path / "articles" / "Yle_Tuoreimmat").is_dir()


def test_creates_public_and_cache_directories(tmp_path: Path):
    bootstrap(write_config(tmp_path), tmp_path)

    assert (tmp_path / "public").is_dir()
    assert (tmp_path / ".image-cache").is_dir()


def test_is_idempotent(tmp_path: Path):
    config_path = write_config(tmp_path)
    bootstrap(config_path, tmp_path)

    bootstrap(config_path, tmp_path)  # must not raise

    assert (tmp_path / "articles").is_dir()


def test_copies_the_example_config_when_missing(tmp_path: Path):
    (tmp_path / "config.example.yaml").write_text(yaml.safe_dump(CONFIG), encoding="utf-8")

    bootstrap(tmp_path / "config.yaml", tmp_path)

    assert (tmp_path / "config.yaml").exists()


def test_never_overwrites_an_existing_config(tmp_path: Path):
    (tmp_path / "config.example.yaml").write_text(yaml.safe_dump(CONFIG), encoding="utf-8")
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump({**CONFIG, "output_dir": "mine"}), encoding="utf-8")

    bootstrap(config_path, tmp_path)

    assert yaml.safe_load(config_path.read_text())["output_dir"] == "mine"


def test_copies_the_example_env_when_missing(tmp_path: Path):
    (tmp_path / ".env.example").write_text("EREADER_DATA_DIR=\n", encoding="utf-8")
    (tmp_path / "config.example.yaml").write_text(yaml.safe_dump(CONFIG), encoding="utf-8")

    bootstrap(tmp_path / "config.yaml", tmp_path)

    assert (tmp_path / ".env").exists()


def test_missing_config_and_no_example_is_an_error(tmp_path: Path):
    with pytest.raises(SystemExit):
        bootstrap(tmp_path / "config.yaml", tmp_path)


def test_unparseable_config_is_an_error(tmp_path: Path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("feeds: [unclosed", encoding="utf-8")

    with pytest.raises(SystemExit):
        bootstrap(config_path, tmp_path)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_init.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'init'`

- [ ] **Step 3: Write the implementation**

Create `init.py`:

```python
#!/usr/bin/env python3
"""Bootstrap a fresh checkout or deployment.

Copies the example config and env files if they are missing (they are
gitignored, so a fresh clone has neither), then creates every directory the
pipeline writes to. Safe to re-run.
"""

import argparse
import shutil
import sys
from pathlib import Path

import yaml

from articles import feed_dir_name


def bootstrap(config_path: Path, project_root: Path) -> list[str]:
    config_path = Path(config_path)
    project_root = Path(project_root)
    messages: list[str] = []

    if not config_path.exists():
        example = project_root / "config.example.yaml"
        if not example.exists():
            sys.exit(f"No {config_path.name} and no config.example.yaml to copy from.")
        shutil.copy(example, config_path)
        messages.append(f"Created {config_path.name} from config.example.yaml — edit it before running.")

    env_path = project_root / ".env"
    env_example = project_root / ".env.example"
    if env_example.exists() and not env_path.exists():
        shutil.copy(env_example, env_path)
        messages.append("Created .env from .env.example — set EREADER_DATA_DIR and PUBLIC_BASE_URL.")

    try:
        cfg = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        sys.exit(f"Could not parse {config_path}: {e}")

    if not isinstance(cfg, dict):
        sys.exit(f"{config_path} must contain a YAML mapping.")

    def resolve(key: str, default: str) -> Path:
        value = Path(cfg.get(key, default)).expanduser()
        return value if value.is_absolute() else project_root / value

    output_dir = resolve("output_dir", "ereader-news")
    for path in (output_dir, resolve("public_dir", "public"), resolve("image_cache_dir", ".image-cache")):
        path.mkdir(parents=True, exist_ok=True)
        messages.append(f"Ensured {path}")

    for feed in cfg.get("feeds", []):
        name = feed.get("name")
        if name:
            (output_dir / feed_dir_name(name)).mkdir(parents=True, exist_ok=True)
    messages.append(f"Ensured {len(cfg.get('feeds', []))} feed directories")

    return messages


def main():
    parser = argparse.ArgumentParser(description="Prepare directories and config for the news pipeline")
    parser.add_argument("--config", default="config.yaml", help="Path to the config file")
    args = parser.parse_args()

    project_root = Path(__file__).parent
    config_path = Path(args.config)
    if not config_path.is_absolute():
        config_path = project_root / config_path

    for message in bootstrap(config_path, project_root):
        print(message)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_init.py -v`
Expected: 9 passed

- [ ] **Step 5: Write the example config**

Create `config.example.yaml`. Copy the current `config.yaml` feed list verbatim, and replace every deployment-specific value:

```yaml
# RSS News Fetcher — copy to config.yaml and edit.
# config.yaml is gitignored; this file is the committed template.

# Where articles are stored. In Docker this is inside the bind mount.
output_dir: /data/articles

# Where the EPUB and OPDS catalog are published for nginx to serve.
public_dir: /data/public

# Public URL that maps to public_dir. Overridable with $PUBLIC_BASE_URL.
public_base_url: https://news.example.com/news

# Cache of downloaded, greyscale-converted article images.
image_cache_dir: /data/.image-cache

max_age_days: 3
max_articles_per_feed: 15

# --- Kindle sync (manual: docker compose run --rm ereader-news --sync) -------
kindle_host: root@KINDLE_IP          # e.g. root@192.168.1.42
kindle_news_dir: /mnt/us/koreader/news
kindle_ssh_key: /root/.ssh/id_ed25519
kindle_ssh_port: 2222

# --- Edition ----------------------------------------------------------------
edition:
  window_hours: 24        # "Latest" covers this many hours
  image_max_width: 480    # Downscale target; tune to the panel
  embed_images: true

# TOC section order. Each feed maps into one of these via its `section` key.
sections:
  - Kotimaa
  - Maailma
  - Helsinki
  - Kulttuuri

feeds:
  - name: "Yle Tuoreimmat"
    url: "https://yle.fi/rss/uutiset/tuoreimmat"
    section: "Kotimaa"
    max_age_hours: 24

  - name: "HS Politiikka"
    url: "http://www.hs.fi/rss/politiikka.xml"
    section: "Kotimaa"
    max_age_hours: 24

  - name: "HS Pääkirjoitukset"
    url: "http://www.hs.fi/rss/paakirjoitukset.xml"
    section: "Kotimaa"
    max_age_hours: 24

  - name: "HS Maailma"
    url: "http://www.hs.fi/rss/maailma.xml"
    section: "Maailma"
    max_age_hours: 24

  - name: "HS Helsinki"
    url: "http://www.hs.fi/rss/helsinki.xml"
    section: "Helsinki"
    max_age_hours: 24

  - name: "Helsingin kaupunki"
    url: "https://www.hel.fi/fi/uutiset/rss"
    section: "Helsinki"
    max_age_hours: 168

  - name: "Helsingin Uutiset"
    url: "https://www.helsinginuutiset.fi/uusimmat/"
    section: "Helsinki"
    type: "scrape"
    max_age_hours: 24
    selector: '//*[@id="sisalto"]'
    article_selector: './/article'
    date_selector: './/time[@class="diks-date__published"]'
    datetime_format: "iso8601"

  - name: "HS Kulttuuri"
    url: "http://www.hs.fi/rss/kulttuuri.xml"
    section: "Kulttuuri"
    max_age_hours: 24

  - name: "HS Lastenuutiset"
    url: "http://www.hs.fi/rss/lastenuutiset.xml"
    section: "Kulttuuri"
    max_age_hours: 24
```

- [ ] **Step 6: Write the env example**

Create `.env.example`:

```
# Copy to .env and fill in. .env is gitignored.

# Absolute path on the host where articles, the EPUB and the catalog live.
# nginx serves $EREADER_DATA_DIR/public/.
EREADER_DATA_DIR=/absolute/path/on/host

# Public URL that maps to $EREADER_DATA_DIR/public/
PUBLIC_BASE_URL=https://news.example.com/news

# Directory holding the SSH key for the manual Kindle sync.
EREADER_SSH_DIR=/home/you/.ssh

TZ=Europe/Helsinki
```

- [ ] **Step 7: Update the local config.yaml so the local run keeps working**

`config.yaml` is gitignored, so this change is local only. Add to it:

Use paths relative to the project root. Task 7 makes `load_config` resolve them against the script's own directory (matching `init.py`), so this works regardless of the working directory — and it keeps machine-specific paths out of every tracked file:

```yaml
public_dir: ./public
public_base_url: http://localhost:8000
image_cache_dir: ./.image-cache

edition:
  window_hours: 24
  image_max_width: 480
  embed_images: true

sections:
  - Kotimaa
  - Maailma
  - Helsinki
  - Kulttuuri
```

Then add a matching `section:` key to each entry in its `feeds:` list, using the same mapping as `config.example.yaml`.

- [ ] **Step 8: Run the bootstrap and a real edition build**

Run: `.venv/bin/python init.py`
Expected: prints the directories it ensured, exits 0.

Run: `.venv/bin/python fetch_news.py --build-edition`
Expected: builds `public/news-latest.epub` and `public/opds.xml` from the ~95 articles already on disk. Open the EPUB in any reader and confirm the sections and TOC look right.

- [ ] **Step 9: Checkpoint — hand off to the user for commit**

Files to stage: `init.py`, `config.example.yaml`, `.env.example`, `tests/test_init.py`
(`config.yaml` is gitignored and must not be staged.)
Suggested message: `feat: add bootstrap script and example configuration`

---

## Task 9: Docker deployment

**Files:**
- Create: `Dockerfile`
- Create: `docker-compose.yml`
- Create: `crontab`
- Create: `docs/nginx.example.conf`

**Interfaces:**
- Consumes: `init.py`, `fetch_news.py --build-edition`
- Produces: a container image whose `ENTRYPOINT` is `python /app/fetch_news.py`

- [ ] **Step 1: Write the Dockerfile**

```dockerfile
FROM python:3.14-slim

ENV PYTHONUNBUFFERED=1

# supercronic runs cron jobs as an unprivileged foreground process, which is
# what a container wants; system cron needs a daemon and swallows stdout.
ARG SUPERCRONIC_VERSION=v0.2.29
ADD https://github.com/aptible/supercronic/releases/download/${SUPERCRONIC_VERSION}/supercronic-linux-amd64 /usr/local/bin/supercronic
RUN chmod +x /usr/local/bin/supercronic

RUN apt-get update \
    && apt-get install -y --no-install-recommends openssh-client rsync \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY *.py ./
COPY crontab /app/crontab

ENTRYPOINT ["python", "/app/fetch_news.py"]
CMD []
```

Note the `ENTRYPOINT`/`CMD` split: with no arguments the container runs a single fetch and exits. The scheduled service overrides the entrypoint to run supercronic — see the compose file.

- [ ] **Step 2: Write the crontab**

Create `crontab`:

```
# Fetch feeds and rebuild the edition hourly, off the top of the hour.
17 * * * * python /app/fetch_news.py --build-edition
```

- [ ] **Step 3: Write the compose file**

Create `docker-compose.yml`:

```yaml
services:
  ereader-news:
    build: .
    image: ereader-news
    entrypoint: ["/usr/local/bin/supercronic", "/app/crontab"]
    volumes:
      - ./config.yaml:/app/config.yaml:ro
      - ${EREADER_DATA_DIR:?set EREADER_DATA_DIR in .env}:/data
    environment:
      TZ: ${TZ:-Europe/Helsinki}
      PUBLIC_BASE_URL: ${PUBLIC_BASE_URL:?set PUBLIC_BASE_URL in .env}
    restart: unless-stopped
```

The SSH key is deliberately **not** mounted into the scheduled service — nothing on the hourly path needs it, and a default value for an optional bind mount has no safe placeholder. The manual Kindle sync supplies it per-invocation and restores the image entrypoint:

```bash
docker compose run --rm \
  -v "${EREADER_SSH_DIR}:/root/.ssh:ro" \
  --entrypoint python \
  ereader-news /app/fetch_news.py --sync
```

Note the entrypoint form: `--entrypoint` takes a single executable, so the script path moves into the argument list. Passing `--entrypoint "python /app/fetch_news.py"` would look for a binary with a space in its name and fail.

- [ ] **Step 4: Write the nginx example**

Create `docs/nginx.example.conf`:

```nginx
# Add to an existing TLS server block. nginx has no environment interpolation,
# so substitute the alias path by hand — it is $EREADER_DATA_DIR/public/.
#
# The Xteink X4 verifies TLS against the ESP32 bundled CA roots, so a publicly
# trusted certificate (Let's Encrypt) is required — self-signed will not work.
# It sends HTTP Basic credentials preemptively, so auth_basic needs no tuning.

location /news/ {
    alias /path/to/ereader-news/public/;

    auth_basic           "News";
    auth_basic_user_file /etc/nginx/htpasswd/news;

    add_header Cache-Control "no-store";
    autoindex off;

    types {
        application/atom+xml  xml;
        application/epub+zip  epub;
    }
    default_type application/octet-stream;
}
```

- [ ] **Step 5: Verify the image builds and the CLI is reachable**

Run: `docker compose build`
Expected: builds without error.

Run: `docker compose run --rm --entrypoint python ereader-news /app/fetch_news.py --help`
Expected: prints the argparse help including `--build-edition`.

If Docker is not available on this machine, note that these two checks are deferred to the server and say so explicitly rather than marking the step done.

- [ ] **Step 6: Checkpoint — hand off to the user for commit**

Files to stage: `Dockerfile`, `docker-compose.yml`, `crontab`, `docs/nginx.example.conf`
Suggested message: `feat: add Docker Compose deployment`

---

## Task 10: Documentation and license

**Files:**
- Create: `LICENSE`
- Modify: `README.md`

**Interfaces:**
- Consumes: everything above
- Produces: no code

- [ ] **Step 1: Write the LICENSE file**

Create `LICENSE` with the standard MIT text, verbatim:

```
MIT License

Copyright (c) 2026 Jonne Airaksinen

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

- [ ] **Step 2: Retitle the README and describe both targets**

Change the title from "RSS News Fetcher for KOReader on Kindle" to "RSS News Fetcher for E-Readers", and rewrite the intro to cover both devices:

```markdown
# RSS News Fetcher for E-Readers

Fetch articles from RSS feeds, extract clean content, and read them on an e-reader.

Two delivery paths:

- **Xteink X4 (CrossPoint firmware)** — articles are bundled into a single EPUB
  edition with topic sections and a nested table of contents, published through an
  OPDS catalog the device browses over Wi-Fi.
- **Kindle (KOReader)** — articles are synced as individual HTML files over SSH.
```

- [ ] **Step 3: Document the quick start**

Add after the intro:

````markdown
## Quick start

```bash
python -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python init.py          # creates config.yaml, .env and the directories
$EDITOR config.yaml               # add your feeds and section mapping
.venv/bin/python fetch_news.py --build-edition
```

The EPUB and catalog land in `public_dir`. Point the X4 at
`<public_base_url>/opds.xml` under Settings → System → OPDS Servers.
````

- [ ] **Step 4: Document the Docker deployment**

Add this section:

````markdown
## Deploying with Docker

```bash
cp .env.example .env
$EDITOR .env                 # EREADER_DATA_DIR and PUBLIC_BASE_URL are required
docker compose up -d
```

The container rebuilds the edition hourly at :17. Publish it with the nginx snippet
in [docs/nginx.example.conf](docs/nginx.example.conf), which serves
`$EREADER_DATA_DIR/public/` directly — there is no upstream to proxy to.

Two requirements come from the device's HTTP client:

- **A publicly trusted TLS certificate.** CrossPoint verifies against the ESP32
  bundled CA roots and cannot be told to skip verification, so self-signed and
  private-CA certificates fail outright. Let's Encrypt works.
- **Basic auth, not Digest.** Credentials are sent preemptively on the first
  request. Create the password file with
  `htpasswd -c /etc/nginx/htpasswd/news <username>`.

Syncing to a Kindle instead is manual, since KOReader's SSH server is started by
hand on the device:

```bash
docker compose run --rm \
  -v "${EREADER_SSH_DIR}:/root/.ssh:ro" \
  --entrypoint python \
  ereader-news /app/fetch_news.py --sync
```
````

- [ ] **Step 5: Document the new configuration keys**

Add to the Configuration Reference section:

````markdown
### Edition and publishing

```yaml
public_dir: /data/public                          # Where the EPUB and catalog are written
public_base_url: https://news.example.com/news    # Public URL mapping to public_dir
image_cache_dir: /data/.image-cache               # Converted image cache

edition:
  window_hours: 24        # "Latest" covers articles from this many hours
  image_max_width: 480    # Downscale target in pixels
  embed_images: true      # false ships a text-only edition

sections:                 # TOC order; every feed maps into one of these
  - Kotimaa
  - Maailma
  - Helsinki
  - Kulttuuri
```

`public_base_url`, `kindle_host` and `kindle_ssh_key` can be overridden by the
environment variables `PUBLIC_BASE_URL`, `KINDLE_HOST` and `KINDLE_SSH_KEY`, which
take precedence over the file.

### Per-feed section

```yaml
feeds:
  - name: "HS Maailma"
    url: "http://www.hs.fi/rss/maailma.xml"
    section: "Maailma"      # Must match an entry in `sections`
```

A feed with no `section` lands in a trailing "Muut" section rather than being
dropped.
````

- [ ] **Step 6: Note the device's pull model**

Add to the README, because it surprises people:

```markdown
### How updates reach the device

CrossPoint has no background sync. The server rebuilds the edition hourly; the X4
downloads it when you open the OPDS catalog and select the entry. The title stays
`News - Latest` on purpose, so each download replaces the previous file on the SD
card instead of accumulating copies.
```

- [ ] **Step 7: Run the full suite one last time**

Run: `.venv/bin/pytest tests/ -v`
Expected: all tests pass.

- [ ] **Step 8: Checkpoint — hand off to the user for commit**

Files to stage: `LICENSE`, `README.md`
Suggested message: `docs: cover the OPDS path and add the MIT license`

---

## Spec coverage

| Spec section | Task |
|---|---|
| Article store, `.meta` `feed=` line | 2 |
| Topic sections, fallback "Muut" | 3 |
| Images: greyscale, downscale, cache, drop on failure | 4 |
| EPUB edition, nested TOC, masthead, stable ASCII title | 5 |
| OPDS 1.2 Atom, firmware parser conformance, absolute hrefs | 6 |
| 24h window, empty-window behavior, env overrides, `--build-edition` | 7 |
| Bootstrap script, `config.example.yaml`, `.env.example` | 8 |
| Dockerfile, compose, supercronic, nginx example | 9 |
| MIT license, README | 10 |
| Atomic writes | 1, 5, 6 |
| `.gitignore`, rename to `ereader-news` | already done before this plan |
