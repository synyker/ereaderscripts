# RSS News Fetcher for KOReader on Kindle

Automatically fetch articles from RSS feeds, extract clean content, and sync them to your Kindle for reading with KOReader.

## Features

- **Multiple feeds**: Configure as many RSS feeds as you want
- **Clean content extraction**: Uses trafilatura and readability-lxml to remove boilerplate and extract article text
- **E-ink optimized HTML**: Generates clean, readable HTML files designed for e-ink displays
- **Per-feed age limits**: Customize how old articles can be for each feed (e.g., 3 days for news, 1 week for local news)
- **Automatic cleanup**: Remove old articles based on age
- **Kindle sync**: Push articles to your Kindle via SSH/SCP (no rsync required)
- **Article index**: Auto-generates an index.html listing all downloaded articles
- **Web scraping**: Fetch news from websites without RSS feeds

## Setup

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure Feeds

Edit `config.yaml` to add your RSS feeds:

```yaml
feeds:
  - name: "Feed Name"
    url: "https://example.com/feed.rss"
    limit: 10                    # Max articles to fetch per feed
    max_age_hours: 72            # Only include articles from last 72 hours (3 days)
```

**Per-feed age limits:**
- Local/city news: `168` (1 week)
- Regular news: `72` (3 days)
- Breaking news: `24` (1 day)

### 3. Set Up Kindle SSH (Optional, for `--sync`)

The `--sync` flag uses SCP to copy files to your Kindle. SCP is built into SSH and requires no additional tools on the Kindle.

1. **Enable SSH on Kindle:**
   - Open KOReader menu → Network → SSH server → Start
   - Note the IP address and port (default: 2222)

2. **Set up key-based authentication (optional, but recommended):**
   ```bash
   ssh-copy-id -p 2222 root@<kindle-ip>
   ```
   Replace `<kindle-ip>` with your Kindle's IP (e.g., `192.168.1.42`)

3. **Update config.yaml:**
   ```yaml
   kindle_host: root@192.168.1.42
   kindle_news_dir: /mnt/us/koreader/news
   kindle_ssh_port: 2222                      # KOReader SSH server uses port 2222
   kindle_ssh_key: ~/.ssh/kindle_id_ed25519   # Optional: path to SSH key
   ```

## Usage

### Basic Commands

```bash
# Fetch articles from configured feeds (no sync)
python fetch_news.py

# Fetch and sync to Kindle
python fetch_news.py --sync

# Only clean old articles (don't fetch new ones)
python fetch_news.py --clean-only

# Use custom config file
python fetch_news.py --config my-config.yaml

# Fetch a single feed by URL
python fetch_news.py --feed-url "https://example.com/feed.rss"

# Parse a local RSS file
python fetch_news.py --rss-file /path/to/file.rss
```

### Scheduling with Crontab

To fetch articles frequently but sync to Kindle only once a day:

```bash
crontab -e
```

Add these lines:

```cron
# Fetch articles every 6 hours (at 0:00, 6:00, 12:00, 18:00)
0 */6 * * * cd /path/to/ereaderscripts && python fetch_news.py

# Fetch and sync to Kindle at 07:30 every morning
30 7 * * * cd /path/to/ereaderscripts && python fetch_news.py --sync
```

This setup:
- Downloads fresh articles every 6 hours locally
- Syncs everything to Kindle once a day at 07:30
- Keeps articles stored locally based on their age limits

## How It Works

1. **Fetch**: Parse RSS feeds and download articles from the last N hours (configurable per feed)
2. **Extract**: Extract clean article content using trafilatura (with readability fallback)
3. **Generate**: Create e-ink friendly HTML files organized by feed
4. **Sync** (optional): Push articles to Kindle using SCP over SSH (no rsync required on Kindle)
5. **Cleanup**: Automatically remove articles older than the configured age limit
6. **Index**: Generate an index.html with links to all articles

## Output Structure

Articles are stored in the configured `output_dir` (default: `./ereader-news/`):

```
ereader-news/
├── index.html                    # Article index
├── Yle_Tuoreimmat/
│   ├── 20250327_abc123def456.html
│   └── 20250327_abc123def456.meta
├── HS_Politiikka/
│   ├── 20250327_xyz789uvw012.html
│   └── 20250327_xyz789uvw012.meta
└── Al_Jazeera/
    ├── 20250326_foo123bar456.html
    └── 20250326_foo123bar456.meta
```

Each article has:
- `.html`: The actual article content, formatted for e-ink reading
- `.meta`: Metadata (original URL, title, publication date, fetch timestamp)

## Configuration Reference

### Global Settings

```yaml
output_dir: ./ereader-news             # Where to store articles locally
max_age_days: 3                        # Cleanup articles older than this (applies to cleanup only)
max_articles_per_feed: 15              # Default limit per feed if not specified
kindle_host: root@192.168.1.x          # SSH connection for Kindle (used by --sync)
kindle_news_dir: /mnt/us/koreader/news # Target directory on Kindle
kindle_ssh_key: ~/.ssh/id_rsa          # SSH private key (optional, uses default if not specified)
kindle_ssh_port: 2222                  # SSH port (default: 22, Kindle uses 2222)
```

### Per-Feed Settings

```yaml
feeds:
  - name: "Feed Name"                  # Display name (used for output directory)
    url: "https://..."                 # RSS/Atom feed URL or webpage to scrape
    max_age_hours: 72                  # Only articles from last N hours (default: 72)
    type: "rss"                        # Feed type: "rss" (default) or "scrape"
    selector: '//*[@id="sisalto"]'     # XPath selector for scrape feeds (required for type: "scrape")
```

For scrape feeds (no RSS available), you need to specify:
- `type: "scrape"` - Enables web scraping instead of RSS parsing
- `selector` - XPath expression to find the HTML element containing articles
  - Example: `//*[@id="sisalto"]` - element with id="sisalto"
  - Example: `//div[@class="articles"]` - div with class="articles"
  - Example: `//main` - the main element
- `article_selector` (optional) - XPath to find individual article elements (relative to `selector`)
  - If specified, searches for links only within these article elements (avoids pagination links, etc.)
  - Example: `.//article` - article elements
  - Example: `.//div[@class="news-item"]` - divs with class="news-item"
- `date_selector` (optional) - XPath to find a `<time>` element with a `datetime` attribute
  - If not specified, falls back to regex pattern matching for dates in text (DD.MM.YYYY format)
  - Example: `.//time[@class="diks-date__published"]` - finds time element with class
  - Example: `.//time` - any time element in the article
- `datetime_format` (optional) - Format of the datetime string in the `datetime` attribute
  - `"iso8601"` - ISO 8601 format: `2026-04-04T11:07:05+03:00`
  - `"rfc2822"` - RFC 2822 format: `Thu, 01 Apr 2026 11:07:05 +0300`
  - Custom Python format string, e.g., `"%Y-%m-%d %H:%M:%S%z"` or `"%d.%m.%Y %H:%M"`
  - If not specified, tries ISO 8601 first, then RFC 2822

## Troubleshooting

### Articles not syncing to Kindle
- Verify SSH server is running on Kindle: Menu → Network → SSH server → Start
- Test SSH connection: `ssh root@<kindle-ip>`
- Check that `kindle_host` is correct in config.yaml

### Sync failing with "permission denied"
- Ensure you've run `ssh-copy-id root@<kindle-ip>` once
- Check that the SSH key has the right permissions: `chmod 600 ~/.ssh/id_rsa`

### No articles being fetched
- Check that feeds are properly configured in config.yaml
- Verify feed URLs are valid by opening them in a browser
- Check logs for specific error messages

### Articles deleted too quickly
- Adjust `max_age_hours` for that feed in config.yaml
- Or adjust the global `max_age_days` setting (applies during cleanup)

## License

MIT
