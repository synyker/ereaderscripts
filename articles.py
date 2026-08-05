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
ID_PATTERN = re.compile(r"_([a-z0-9]{12})\.html$")
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
