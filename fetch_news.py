#!/usr/bin/env python3
"""
RSS News Fetcher for KOReader on Kindle

Fetches articles from RSS feeds, extracts clean article content,
generates e-ink friendly HTML files, syncs to Kindle, and auto-cleans old articles.

Usage:
    python fetch_news.py                  # Fetch news + clean old articles
    python fetch_news.py --sync           # Fetch + sync to Kindle
    python fetch_news.py --clean-only     # Only remove old articles
    python fetch_news.py --config my.yaml # Use custom config file
"""

import argparse
import hashlib
import html
import logging
import os
import re
import subprocess
import sys
import time
import unicodedata
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import feedparser
import requests
import trafilatura
from lxml import etree
from readability import Document

from articles import ArticleStore
from epub import build_edition, group_into_sections
from images import ImageCache
from opds import CatalogEntry, write_catalog

log_level = logging.DEBUG if os.environ.get("DEBUG") else logging.INFO
logging.basicConfig(
    level=log_level,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# --- Configuration -----------------------------------------------------------

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


def load_config(config_path: str) -> dict:
    """Load YAML config, falling back to defaults for missing keys."""
    import yaml

    config_file = Path(config_path)
    if not config_file.exists():
        log.warning("Config file %s not found, using defaults", config_path)
        return DEFAULT_CONFIG

    with open(config_file) as f:
        user_cfg = yaml.safe_load(f) or {}

    cfg = {**DEFAULT_CONFIG, **user_cfg}

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

    return cfg


# --- HTML Template -----------------------------------------------------------

HTML_TEMPLATE = """\
<!DOCTYPE html>
<html lang="{lang}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
  body {{
    font-family: Georgia, 'Times New Roman', serif;
    line-height: 1.6;
    max-width: 100%;
    margin: 0;
    padding: 0.5em;
    color: #000;
    background: #fff;
  }}
  h1 {{
    font-size: 1.3em;
    margin-bottom: 0.2em;
  }}
  h2 {{
    font-size: 1.1em;
    margin: 0.5em 0;
    font-weight: bold;
  }}
  h3 {{
    font-size: 1em;
    margin: 0.4em 0;
    font-weight: bold;
  }}
  .meta {{
    font-size: 0.85em;
    color: #555;
    margin-bottom: 1em;
    border-bottom: 1px solid #ccc;
    padding-bottom: 0.5em;
  }}
  img {{
    max-width: 100%;
    height: auto;
  }}
  p {{
    margin: 0.8em 0;
  }}
  a {{
    color: #000;
    text-decoration: underline;
  }}
</style>
</head>
<body>
<h1>{title}</h1>
<div class="meta">
  <span>{source}</span> &middot; <span>{date}</span>
</div>
<article>
{content}
</article>
</body>
</html>
"""


# --- Article Fetching and Extraction -----------------------------------------

def clean_extracted_html(html_content: str) -> str:
    """Clean up malformed HTML from trafilatura/readability extraction.

    Fixes common issues like:
    - Void elements with closing tags (source, br, hr, etc.)
    - Non-standard tags like graphic
    - Nested html/body tags from extraction
    """
    if not html_content:
        return html_content

    # Remove nested <html> and <body> tags that shouldn't be in extracted content
    html_content = re.sub(r'</?html\s*>', '', html_content, flags=re.IGNORECASE)
    html_content = re.sub(r'</?body\s*>', '', html_content, flags=re.IGNORECASE)

    # List of void elements that shouldn't have closing tags
    void_elements = ['source', 'img', 'br', 'hr', 'input', 'meta', 'link']

    for element in void_elements:
        # Remove closing tags for void elements: </source> → nothing
        html_content = re.sub(f'</{element}\\s*>', '', html_content, flags=re.IGNORECASE)

    # Replace non-standard graphic tags with img
    html_content = re.sub(r'<graphic\b([^>]*)>', r'<img\1>', html_content, flags=re.IGNORECASE)
    html_content = re.sub(r'</graphic>', '', html_content, flags=re.IGNORECASE)

    return html_content


def sanitize_content(text: str) -> str:
    """Remove problematic Unicode characters that KOReader might not render.

    Normalizes Unicode, removes zero-width characters, RTL marks, and other
    invisible/ambiguous characters that can cause rendering issues.
    """
    if not text:
        return text

    # Normalize Unicode to NFKD form (compatibility decomposition)
    text = unicodedata.normalize('NFKD', text)

    # Remove problematic Unicode characters
    # Zero-width space, zero-width joiner, right-to-left mark, etc.
    problematic_chars = [
        '\u200b',  # Zero-width space
        '\u200c',  # Zero-width non-joiner
        '\u200d',  # Zero-width joiner
        '\u200e',  # Left-to-right mark
        '\u200f',  # Right-to-left mark
        '\ufeff',  # Zero-width no-break space
        '\u202a',  # Left-to-right embedding
        '\u202b',  # Right-to-left embedding
        '\u202c',  # Pop directional formatting
        '\u202d',  # Left-to-right override
        '\u202e',  # Right-to-left override
    ]

    for char in problematic_chars:
        text = text.replace(char, '')

    return text


def fetch_page(url: str, timeout: int = 30, is_feed: bool = False) -> str | None:
    """Download a webpage, return its HTML content."""
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; KindleNewsFetcher/1.0)",
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "fi,en;q=0.9",
    }

    # Add cache-busting headers for feeds
    if is_feed:
        headers["Cache-Control"] = "no-cache"
        headers["Pragma"] = "no-cache"

    try:
        resp = requests.get(url, headers=headers,
                            timeout=timeout, allow_redirects=True)
        resp.raise_for_status()
        resp.encoding = resp.apparent_encoding or "utf-8"
        return resp.text
    except requests.RequestException as e:
        log.warning("Failed to fetch %s: %s", url, e)
        return None


def extract_article(raw_html: str, url: str) -> dict | None:
    """
    Extract clean article content from raw HTML.
    Uses trafilatura as primary extractor, readability-lxml as fallback.
    Returns dict with 'title', 'content' (HTML), 'text' keys or None.
    """
    # Primary: trafilatura — best at removing boilerplate
    content = trafilatura.extract(
        raw_html,
        url=url,
        output_format="html",
        include_images=True,
        include_links=True,
        include_tables=True,
        favor_recall=True,
    )

    title = None
    # Try to get title from trafilatura metadata
    metadata = trafilatura.extract_metadata(raw_html, default_url=url)
    if metadata:
        title = metadata.title

    if content and len(content) > 200:
        return {"title": title, "content": content}

    # Fallback: readability-lxml
    log.info("  Falling back to readability for %s", url)
    try:
        doc = Document(raw_html, url=url)
        content = doc.summary()
        title = title or doc.short_title()

        if content and len(content) > 100:
            # Clean up readability output
            content = _clean_readability_html(content)
            return {"title": title, "content": content}
    except Exception as e:
        log.warning("  Readability failed for %s: %s", url, e)

    return None


def _clean_readability_html(content_html: str) -> str:
    """Remove wrapper divs and unnecessary attributes from readability output."""
    try:
        tree = etree.HTML(content_html)
        body = tree.find(".//body")
        if body is None:
            return content_html

        # Remove class/id attributes that are just noise
        for elem in body.iter():
            for attr in ["class", "id", "style", "onclick", "onload"]:
                if attr in elem.attrib:
                    del elem.attrib[attr]

        # Serialize back
        parts = []
        for child in body:
            parts.append(
                etree.tostring(child, encoding="unicode", method="html")
            )
        return "\n".join(parts)
    except Exception:
        return content_html


# --- Feed Processing ---------------------------------------------------------

def article_id(url: str) -> str:
    """Generate a short filesystem-safe ID from a URL."""
    return hashlib.sha256(url.encode()).hexdigest()[:12]


def parse_feed(feed_url: str, max_age_hours: int = 24) -> list[dict]:
    """Parse an RSS/Atom feed, return list of entries with title, link, date.

    Only includes entries published within max_age_hours of now.
    """
    feed = feedparser.parse(feed_url)
    entries = []
    cutoff_time = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)

    for entry in feed.entries:
        link = entry.get("link", "")
        # Strip tracking params like ?origin=rss
        link = re.sub(r'\?origin=rss$', '', link)

        pub_date = ""
        pub_datetime = None

        # Try to parse timezone-aware datetime from the original published field
        helsinki_tz = ZoneInfo("Europe/Helsinki")

        if hasattr(entry, "published") and entry.published:
            try:
                pub_datetime = parsedate_to_datetime(entry.published)
                # If timezone is naive or UTC, keep as UTC for comparison; convert to Helsinki for display
                if pub_datetime.tzinfo is None:
                    pub_datetime = pub_datetime.replace(tzinfo=timezone.utc)
                pub_date = pub_datetime.astimezone(helsinki_tz).strftime("%Y-%m-%d %H:%M")
            except (TypeError, ValueError):
                # Fallback to parsed version if string parsing fails
                if hasattr(entry, "published_parsed") and entry.published_parsed:
                    pub_datetime = datetime(
                        *entry.published_parsed[:6], tzinfo=timezone.utc)
                    pub_date = pub_datetime.astimezone(helsinki_tz).strftime("%Y-%m-%d %H:%M")
        elif hasattr(entry, "updated") and entry.updated:
            try:
                pub_datetime = parsedate_to_datetime(entry.updated)
                # If timezone is naive or UTC, keep as UTC for comparison; convert to Helsinki for display
                if pub_datetime.tzinfo is None:
                    pub_datetime = pub_datetime.replace(tzinfo=timezone.utc)
                pub_date = pub_datetime.astimezone(helsinki_tz).strftime("%Y-%m-%d %H:%M")
            except (TypeError, ValueError):
                # Fallback to parsed version
                if hasattr(entry, "updated_parsed") and entry.updated_parsed:
                    pub_datetime = datetime(
                        *entry.updated_parsed[:6], tzinfo=timezone.utc)
                    pub_date = pub_datetime.astimezone(helsinki_tz).strftime("%Y-%m-%d %H:%M")

        # Skip entries older than cutoff
        if pub_datetime and pub_datetime < cutoff_time:
            continue

        entries.append({
            "title": entry.get("title", "Untitled"),
            "link": link,
            "date": pub_date,
            "summary": entry.get("summary", ""),
            "published_at": pub_datetime,
        })
    return entries


def scrape_articles(page_url: str, max_age_hours: int = 24, selector: str = '//*[@id="sisalto"]', article_selector: str | None = None, date_selector: str | None = None, datetime_format: str | None = None) -> list[dict]:
    """Scrape articles from a webpage (for sites without RSS feeds).

    Finds articles in a specified element and extracts links with dates.
    Returns entries in the same format as parse_feed().

    Args:
        page_url: URL of the page to scrape
        max_age_hours: Only include articles from the last N hours
        selector: XPath selector for the content element (default: //*[@id="sisalto"])
        article_selector: XPath selector for individual article elements (relative to content element).
                         If provided, searches for links only within article elements.
                         E.g., './/article' or './/div[@class="news-item"]'
        date_selector: XPath selector for date element (relative to article). If provided,
                      extracts datetime attribute. E.g., './/time[@class="diks-date__published"]'
        datetime_format: Format of the datetime string. Options:
                        - "iso8601": ISO 8601 format (e.g., "2026-04-04T11:07:05+03:00")
                        - "rfc2822": RFC 2822 format (e.g., "Thu, 01 Apr 2026 11:07:05 +0300")
                        - None (default): Try ISO 8601 first, then RFC 2822
                        - Custom Python format string (e.g., "%Y-%m-%d %H:%M:%S%z")
    """
    entries = []
    cutoff_time = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)

    raw_html = fetch_page(page_url, is_feed=False)
    if not raw_html:
        return entries

    try:
        tree = etree.HTML(raw_html)

        # Find the main content element using the provided selector
        content_elem = tree.xpath(selector)
        if not content_elem:
            log.warning("Could not find element with selector '%s'", selector)
            return entries

        # Extract articles from content area
        if article_selector:
            # Find individual article elements first, then links within them
            articles = content_elem[0].xpath(article_selector)
            if not articles:
                log.warning("Could not find articles with selector '%s'", article_selector)
                return entries
            link_list = []
            for article in articles:
                link_elems = article.xpath('.//a[@href]')
                link_list.extend(link_elems)
        else:
            # Extract all article links from the content area
            link_list = content_elem[0].xpath('.//a[@href]')

        for link_elem in link_list:
            href = link_elem.get("href", "").strip()
            if not href or href.startswith("#"):
                continue

            # Make absolute URL if relative
            if href.startswith("/"):
                from urllib.parse import urljoin
                href = urljoin(page_url, href)
            elif not href.startswith("http"):
                from urllib.parse import urljoin
                href = urljoin(page_url, href)

            # Get title from link text
            title = "".join(link_elem.itertext()).strip()
            if not title:
                continue

            # Extract date from article
            pub_date = ""
            pub_datetime = None
            helsinki_tz = ZoneInfo("Europe/Helsinki")

            if date_selector:
                # Try to find date using the provided date_selector
                # Search for date element starting from link and going up the tree
                article_elem = link_elem
                found_date = False

                # Try up to 5 levels up the DOM tree
                for _ in range(5):
                    date_elems = article_elem.xpath(date_selector)
                    if date_elems:
                        datetime_attr = date_elems[0].get("datetime", "")
                        if datetime_attr:
                            pub_datetime = None

                            # Parse based on configured format
                            if datetime_format == "iso8601":
                                pub_datetime = datetime.fromisoformat(datetime_attr)
                            elif datetime_format == "rfc2822":
                                pub_datetime = parsedate_to_datetime(datetime_attr)
                            elif datetime_format:
                                # Custom format string - don't catch exceptions, let them fail loudly
                                pub_datetime = datetime.strptime(datetime_attr, datetime_format)
                                # If no timezone, assume UTC
                                if pub_datetime.tzinfo is None:
                                    pub_datetime = pub_datetime.replace(tzinfo=timezone.utc)
                            else:
                                # Auto-detect: try ISO 8601 first, then RFC 2822
                                try:
                                    pub_datetime = datetime.fromisoformat(datetime_attr)
                                except ValueError:
                                    pub_datetime = parsedate_to_datetime(datetime_attr)

                            if pub_datetime:
                                # Convert to Helsinki timezone for display
                                pub_date = pub_datetime.astimezone(helsinki_tz).strftime("%Y-%m-%d %H:%M")
                                # For comparison, keep in UTC
                                pub_datetime = pub_datetime.astimezone(timezone.utc)
                                found_date = True
                                log.debug("Found date for article: %s -> %s", title[:50], pub_date)
                                break

                    parent = article_elem.getparent()
                    if parent is None:
                        break
                    article_elem = parent

                if not found_date:
                    log.debug("Date not found for article: %s (selector: %s)", title[:50], date_selector)
            else:
                # Fallback: look for date patterns in nearby text
                parent = link_elem.getparent()
                if parent is not None:
                    parent_text = "".join(parent.itertext())
                    date_match = re.search(r'(\d{1,2}\.\d{1,2}\.\d{4})', parent_text)
                    if date_match:
                        date_str = date_match.group(1)
                        # Convert from DD.MM.YYYY to YYYY-MM-DD HH:MM format
                        try:
                            day, month, year = date_str.split(".")
                            pub_datetime = datetime(
                                int(year), int(month), int(day),
                                tzinfo=timezone.utc
                            )
                            pub_date = pub_datetime.strftime("%Y-%m-%d %H:%M")
                        except ValueError:
                            pass

            # Skip articles older than cutoff
            if pub_datetime and pub_datetime < cutoff_time:
                continue

            if not pub_date:
                # If no date found, assume it's recent
                pub_date = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")

            entries.append({
                "title": title,
                "link": href,
                "date": pub_date,
                "summary": "",
                "published_at": pub_datetime,
            })

    except Exception as e:
        log.warning("Error scraping %s: %s", page_url, e)

    return entries


def process_feed(feed_cfg: dict, store: ArticleStore) -> int:
    """Process a single feed: fetch articles, extract content, write HTML files."""
    feed_name = feed_cfg["name"]
    feed_url = feed_cfg["url"]
    max_age_hours = feed_cfg.get("max_age_hours", 72)  # Default: 3 days
    feed_type = feed_cfg.get("type", "rss")  # 'rss' or 'scrape'

    log.info("Processing feed: %s", feed_name)

    # Use scraper or RSS parser based on feed type
    if feed_type == "scrape":
        selector = feed_cfg.get("selector", '//*[@id="sisalto"]')
        article_selector = feed_cfg.get("article_selector")
        date_selector = feed_cfg.get("date_selector")
        datetime_format = feed_cfg.get("datetime_format")
        entries = scrape_articles(feed_url, max_age_hours=max_age_hours,
                                 selector=selector, article_selector=article_selector,
                                 date_selector=date_selector, datetime_format=datetime_format)
    else:
        entries = parse_feed(feed_url, max_age_hours=max_age_hours)

    log.info("  Found %d entries", len(entries))

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


# --- Kindle Sync (SSH) -------------------------------------------------------

def sync_to_kindle_scp(output_dir: Path, kindle_host: str, kindle_news_dir: str, kindle_ssh_key: str | None = None, kindle_ssh_port: int = 22) -> bool:
    """Sync news HTML files to Kindle via SCP, preserving folder structure.

    Uses scp -r to copy all files and directories to Kindle.

    Args:
        output_dir: Local directory with news files
        kindle_host: SSH host (e.g., "root@192.168.1.42")
        kindle_news_dir: Target directory on Kindle
        kindle_ssh_key: Path to SSH private key (optional, uses default if not specified)
        kindle_ssh_port: SSH port (default: 22, common for Kindle: 2222)
    """
    log.info("Syncing to Kindle via SCP: %s:%s", kindle_host, kindle_news_dir)

    # Build SCP command to copy entire directory recursively
    scp_cmd = ["scp", "-r"]

    # Add SSH key if specified
    if kindle_ssh_key:
        scp_cmd.extend(["-i", kindle_ssh_key])

    # Add port if not default
    if kindle_ssh_port != 22:
        scp_cmd.extend(["-P", str(kindle_ssh_port)])

    # Copy entire directory with structure preserved
    # Source: local output_dir, Destination: kindle_host:kindle_news_dir
    scp_cmd.append(str(output_dir) + "/")
    scp_cmd.append(f"{kindle_host}:{kindle_news_dir}/")

    log.info("Syncing entire directory with folder structure preserved")
    result = subprocess.run(scp_cmd, capture_output=True, text=True)

    if result.returncode == 0:
        log.info("Sync complete: directory synced to Kindle")
        return True
    else:
        log.error("Sync failed (exit %d):\n%s", result.returncode, result.stderr)
        return False


def sync_to_kindle(output_dir: Path, kindle_host: str, kindle_news_dir: str, kindle_ssh_key: str | None = None, kindle_ssh_port: int = 22) -> bool:
    """Sync news HTML files to Kindle via rsync over SSH.

    Uses rsync with --delete to keep Kindle in sync and remove old articles.
    Requires SSH key auth to be set up (ssh-copy-id root@<kindle-ip>).

    Args:
        output_dir: Local directory with news files
        kindle_host: SSH host (e.g., "root@192.168.1.42")
        kindle_news_dir: Target directory on Kindle
        kindle_ssh_key: Path to SSH private key (optional, uses default if not specified)
        kindle_ssh_port: SSH port (default: 22, common for Kindle: 2222)
    """
    # rsync the html files, delete files on Kindle that no longer exist locally
    source = str(output_dir).rstrip("/") + "/"
    dest = f"{kindle_host}:{kindle_news_dir}/"

    cmd = [
        "rsync", "-avz", "--delete",
        "--include=*/",
        "--include=*.html",
        "--exclude=*.meta",
    ]

    # Build SSH command with key and port
    ssh_cmd = "ssh"
    if kindle_ssh_key:
        ssh_cmd += f" -i {kindle_ssh_key}"
    if kindle_ssh_port != 22:
        ssh_cmd += f" -p {kindle_ssh_port}"

    # The -e flag expects the entire SSH command as a single argument
    cmd.extend(["-e", ssh_cmd, source, dest])

    log.info("Syncing to Kindle: %s", dest)
    log.info("Running: %s", " ".join(cmd))

    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode == 0:
        log.info("Sync complete:\n%s", result.stdout)
        return True
    else:
        log.error("Sync failed (exit %d):\n%s",
                  result.returncode, result.stderr)
        return False


# --- Index Page --------------------------------------------------------------

def generate_index(output_dir: Path) -> None:
    """Generate an index.html listing all downloaded articles grouped by feed."""
    feeds = {}
    for html_file in sorted(output_dir.rglob("*.html"), reverse=True):
        if html_file.name in ("index.html", "all.html", "all_links.html"):
            continue
        feed_name = html_file.parent.name
        meta_file = html_file.with_suffix(".meta")
        title = html_file.stem
        date = ""
        if meta_file.exists():
            for line in meta_file.read_text(encoding="utf-8").splitlines():
                if line.startswith("title="):
                    title = line.split("=", 1)[1]
                elif line.startswith("date="):
                    date = line.split("=", 1)[1]

        feeds.setdefault(feed_name, []).append({
            "title": title,
            "date": date,
            "path": html_file.relative_to(output_dir),
        })

    items_html = []
    items_html.append('<div style="margin-bottom: 1.5em;">')
    items_html.append(
        '<p><strong><a href="all.html">→ View all news in one document (sorted by date)</a></strong></p>')
    items_html.append(
        '<p><strong><a href="all_links.html">→ View all news links (sorted by date)</a></strong></p>')
    items_html.append('</div>')
    items_html.append("")

    for feed_name, articles in sorted(feeds.items()):
        items_html.append(
            f'<h2 style="font-size: 1.3em;">{html.escape(feed_name.replace("_", " "))}</h2>')
        items_html.append("<ul>")
        # Sort articles by date (newest first)
        for art in sorted(articles, key=lambda x: x["date"], reverse=True):
            items_html.append(
                f'<li><a href="{art["path"]}">{html.escape(art["title"])}</a>'
                f' <small>({html.escape(art["date"])})</small></li>'
            )
        items_html.append("</ul>")

    index_html = HTML_TEMPLATE.format(
        lang="fi",
        title="News",
        source="KindleNewsFetcher",
        date=datetime.now().strftime("%Y-%m-%d %H:%M"),
        content="\n".join(items_html),
    )

    (output_dir / "index.html").write_text(index_html, encoding="utf-8")
    log.info("Generated index.html")


def generate_all_articles(output_dir: Path) -> None:
    """Generate all.html with full content of all articles combined and sorted by publish date."""
    articles = []

    # Scan all HTML files and collect content + metadata
    for html_file in output_dir.rglob("*.html"):
        if html_file.name in ("index.html", "all.html", "all_links.html"):
            continue

        meta_file = html_file.with_suffix(".meta")
        title = html_file.stem
        date = ""
        feed_name = html_file.parent.name

        if meta_file.exists():
            for line in meta_file.read_text(encoding="utf-8").splitlines():
                if line.startswith("title="):
                    title = line.split("=", 1)[1]
                elif line.startswith("date="):
                    date = line.split("=", 1)[1]

        # Extract article content from the HTML file
        article_content = ""
        try:
            html_content = html_file.read_text(encoding="utf-8")
            # Extract the <article> element content (includes h1, h2, h3, p, etc.)
            article_match = re.search(
                r'<article>(.*?)</article>', html_content, re.DOTALL)
            if article_match:
                article_content = article_match.group(1).strip()

            if not article_content:
                log.debug("No article content found in %s", html_file.name)
        except Exception as e:
            log.warning("Could not extract content from %s: %s", html_file, e)

        # Clean up extracted HTML first, then sanitize Unicode
        cleaned_content = clean_extracted_html(article_content)
        articles.append({
            "title": title,
            "date": date,
            "feed": feed_name.replace("_", " "),
            "content": sanitize_content(cleaned_content),
        })

    # Sort by date (newest first, assuming ISO format)
    articles.sort(key=lambda x: x["date"], reverse=True)

    # Generate content with custom styles for article h2s
    items_html = []
    items_html.append("<style>h2 { font-size: 1.1em; }</style>")

    for art in articles:
        items_html.append(
            f'<div style="margin-bottom: 3em; padding-bottom: 2em; border-bottom: 2px solid #ccc;">'
            f'<h2>{html.escape(art["title"])}</h2>'
            f'<p style="font-size: 0.85em; color: #666; margin: 0.5em 0;">'
            f'<strong>{html.escape(art["feed"])}</strong> · {html.escape(art["date"])}'
            f'</p>'
            f'{art["content"]}'
            f'</div>'
        )

    # Create a custom HTML without the <article> wrapper since KOReader might not handle it well
    all_html = f"""<!DOCTYPE html>
<html lang="fi">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>All News</title>
<style>
  body {{
    font-family: Georgia, 'Times New Roman', serif;
    line-height: 1.6;
    max-width: 100%;
    margin: 0;
    padding: 0.5em;
    color: #000;
    background: #fff;
  }}
  h1 {{
    font-size: 1.3em;
    margin-bottom: 0.2em;
  }}
  h2 {{
    font-size: 1.1em;
    margin: 0.5em 0;
    font-weight: bold;
  }}
  h3 {{
    font-size: 1em;
    margin: 0.4em 0;
    font-weight: bold;
  }}
  .meta {{
    font-size: 0.85em;
    color: #555;
    margin-bottom: 1em;
    border-bottom: 1px solid #ccc;
    padding-bottom: 0.5em;
  }}
  img {{
    max-width: 100%;
    height: auto;
  }}
  p {{
    margin: 0.8em 0;
  }}
  a {{
    color: #000;
    text-decoration: underline;
  }}
</style>
</head>
<body>
<h1>All News</h1>
<div class="meta">
  <span>KindleNewsFetcher</span> &middot; <span>{datetime.now().strftime("%Y-%m-%d %H:%M")}</span>
</div>
{"".join(items_html) if items_html else "<p>No articles yet.</p>"}
</body>
</html>
"""

    (output_dir / "all.html").write_text(all_html, encoding="utf-8")
    log.info("Generated all.html with %d articles", len(articles))


def generate_all_links(output_dir: Path) -> None:
    """Generate all_links.html with links to all articles sorted by publish date."""
    articles = []

    # Scan all HTML files and collect metadata
    for html_file in output_dir.rglob("*.html"):
        if html_file.name in ("index.html", "all.html", "all_links.html"):
            continue

        meta_file = html_file.with_suffix(".meta")
        title = html_file.stem
        date = ""
        feed_name = html_file.parent.name

        if meta_file.exists():
            for line in meta_file.read_text(encoding="utf-8").splitlines():
                if line.startswith("title="):
                    title = line.split("=", 1)[1]
                elif line.startswith("date="):
                    date = line.split("=", 1)[1]

        articles.append({
            "title": title,
            "date": date,
            "feed": feed_name.replace("_", " "),
            "path": html_file.relative_to(output_dir),
        })

    # Sort by date (newest first)
    articles.sort(key=lambda x: x["date"], reverse=True)

    # Generate content
    items_html = []
    for art in articles:
        # Use path with Unix-style separators for web links
        link_path = str(art["path"]).replace("\\", "/")
        items_html.append(
            f'<div style="margin-bottom: 1em; padding-bottom: 0.5em; border-bottom: 1px solid #ddd;">'
            f'<p style="margin: 0;">'
            f'<a href="{link_path}">{html.escape(art["title"])}</a>'
            f'</p>'
            f'<p style="font-size: 0.85em; color: #666; margin: 0.3em 0;">'
            f'<strong>{html.escape(art["feed"])}</strong> · {html.escape(art["date"])}'
            f'</p>'
            f'</div>'
        )

    all_links_html = HTML_TEMPLATE.format(
        lang="fi",
        title="All News - Links",
        source="KindleNewsFetcher",
        date=datetime.now().strftime("%Y-%m-%d %H:%M"),
        content="\n".join(items_html) if items_html else "<p>No articles yet.</p>",
    )

    (output_dir / "all_links.html").write_text(all_links_html, encoding="utf-8")
    log.info("Generated all_links.html with %d articles", len(articles))


# --- Edition (EPUB + OPDS) ---------------------------------------------------

def feed_section_map(cfg: dict) -> dict[str, str]:
    """Map each feed's display name to its configured topic section."""
    return {
        feed["name"]: feed["section"]
        for feed in cfg.get("feeds", [])
        if feed.get("section")
    }


def apply_edition_limits(articles: list, cfg: dict) -> list:
    """Cap each feed's articles in the edition to its `edition_limit`, keeping the newest.

    High-churn feeds (Yle Tuoreimmat rotates through dozens of articles a day)
    otherwise drown out feeds that publish a handful. The cap applies at edition
    time, not fetch time, because hourly fetching accumulates the full firehose
    in the store regardless.
    """
    limits = {
        feed["name"]: feed["edition_limit"]
        for feed in cfg.get("feeds", [])
        if feed.get("edition_limit")
    }
    if not limits:
        return articles

    counts: dict[str, int] = {}
    kept = []
    for article in articles:  # newest first, so the cap keeps the newest
        seen = counts.get(article.feed, 0)
        if article.feed in limits and seen >= limits[article.feed]:
            continue
        counts[article.feed] = seen + 1
        kept.append(article)

    dropped = len(articles) - len(kept)
    if dropped:
        log.info("Edition limits dropped %d articles (%s)",
                 dropped, ", ".join(f"{k}={v}" for k, v in limits.items()))
    return kept


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

    selected = apply_edition_limits(selected, cfg)

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


# --- Main --------------------------------------------------------------------

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


if __name__ == "__main__":
    main()
