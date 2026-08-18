# RSS News Fetcher for E-Readers

Fetch articles from RSS feeds, extract clean content, and read them on an e-reader.

The primary setup runs on a home server with Docker Compose: articles are bundled
into a single EPUB edition with topic sections and a nested table of contents,
published through an OPDS catalog that an **Xteink X4 (CrossPoint firmware)** — or
any other OPDS client — downloads over Wi-Fi.

Syncing individual HTML articles to a **Kindle running KOReader** over SSH is also
supported: see [docs/kindle.md](docs/kindle.md).

## Quick start (Docker)

On the server:

```bash
git clone <this-repo> && cd ereaderscripts

# 1. Deployment variables
cp .env.example .env
$EDITOR .env                 # EREADER_DATA_DIR and PUBLIC_BASE_URL are required

# 2. Application config
cp config.example.yaml config.yaml
$EDITOR config.yaml          # add feeds + sections, and UNCOMMENT the three
                             # /data/... paths (the ./relative defaults are for
                             # local runs — inside Docker they end up in the
                             # container, not in your bind mount)

# 3. Start the scheduler (rebuilds the edition hourly at :17)
docker compose up -d

# 4. Create the first edition now instead of waiting for the next :17
docker compose run --rm --entrypoint python ereader-news /app/fetch_news.py --build-edition
```

The EPUB and catalog land in `$EREADER_DATA_DIR/public/` on the host.

### Publish with nginx

Serve that directory directly with the snippet in
[docs/nginx.example.conf](docs/nginx.example.conf) — there is no upstream to proxy
to. Mind two classic mistakes: the `alias` must point at the `public/`
subdirectory and must end with a trailing slash, and every directory in the path
needs `+x` for the nginx worker user.

Two requirements come from the device's HTTP client:

- **A publicly trusted TLS certificate.** CrossPoint verifies against the ESP32
  bundled CA roots and cannot be told to skip verification, so self-signed and
  private-CA certificates fail outright. Let's Encrypt works.
- **Basic auth, not Digest.** Credentials are sent preemptively on the first
  request. Create the password file with
  `sudo htpasswd -c /etc/nginx/.htpasswd-news <username>` (re-run without `-c` to
  change a password), and reference it from `auth_basic_user_file`.

### Point the device at it

On the X4: Settings → System → OPDS Servers → add
`<public_base_url>/opds.xml` (e.g. `https://news.example.com/news/opds.xml`) with
the Basic auth username and password. Sanity-check the same URL first with
`curl -u <username> <public_base_url>/opds.xml`.

### How updates reach the device

CrossPoint has no background sync. The server rebuilds the edition hourly; the X4
downloads it when you open the OPDS catalog and select the entry. The title stays
`News - Latest` on purpose, so each download replaces the previous file on the SD
card instead of accumulating copies.

## Running locally (without Docker)

For development, or a machine where cron suffices:

```bash
python -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python init.py          # creates config.yaml, .env and the directories
$EDITOR config.yaml               # add your feeds and section mapping
.venv/bin/python fetch_news.py --build-edition
```

Here the relative defaults (`./ereader-news`, `./public`, `./.image-cache`) are
correct as-is; the EPUB and catalog land in `./public/`.

All CLI flags:

```bash
python fetch_news.py                  # fetch + clean only
python fetch_news.py --build-edition  # fetch + build EPUB and OPDS catalog
python fetch_news.py --sync           # fetch + sync to Kindle (see docs/kindle.md)
python fetch_news.py --clean-only     # only remove old articles
python fetch_news.py --config my.yaml # use a different config file
python fetch_news.py --feed-url URL   # fetch a single feed, ignore config feeds
python fetch_news.py --rss-file FILE  # parse a local RSS file
```

## Features

- **Multiple feeds**: Configure as many RSS feeds as you want
- **Web scraping**: Fetch news from websites without RSS feeds
- **Clean content extraction**: Uses trafilatura and readability-lxml to remove boilerplate and extract article text
- **EPUB edition**: Bundles the last 24 hours into a single EPUB with topic sections and a nested table of contents
- **OPDS catalog**: Publishes the edition for wireless download on the Xteink X4 (CrossPoint) and other OPDS clients
- **Greyscale image processing**: Downscales and converts embedded images for e-ink panels
- **Docker deployment**: Runs unattended with hourly edition rebuilds
- **Per-feed age limits**: Customize how old articles can be for each feed (e.g., 3 days for news, 1 week for local news)
- **Automatic cleanup**: Remove old articles based on age
- **Kindle sync**: Push articles as HTML to KOReader via SSH/SCP ([docs/kindle.md](docs/kindle.md))

## How It Works

1. **Fetch**: Parse RSS feeds (or scrape pages) and download articles from the last N hours per feed
2. **Extract**: Pull clean article content with trafilatura (readability fallback)
3. **Store**: Save each article as an HTML + metadata pair, deduplicated by URL
4. **Cleanup**: Remove articles older than the configured age limit
5. **Edition**: Build an EPUB from the last 24h, grouped into topic sections with a nested TOC, images converted to e-ink greyscale
6. **Catalog**: Write an OPDS catalog pointing at the EPUB — both written atomically, so a device downloading mid-rebuild never sees a partial file
7. **Device pull**: The X4 (or another OPDS client) downloads the edition on demand

Articles are stored in `output_dir`, one directory per feed, as
`<date>_<id>.html` + `.meta` pairs; the edition and `opds.xml` are written to
`public_dir`.

## Configuration Reference

### Edition and publishing

```yaml
output_dir: ./ereader-news        # Article store (/data/articles in Docker)
public_dir: ./public              # EPUB + catalog output (/data/public in Docker)
public_base_url: https://news.example.com/news    # Public URL mapping to public_dir
image_cache_dir: ./.image-cache   # Converted image cache (/data/.image-cache in Docker)

max_age_days: 3                   # Cleanup articles older than this

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

`public_base_url` can be overridden with the environment variable
`PUBLIC_BASE_URL`, which takes precedence over the file (the Docker deployment
sets it from `.env`). The Kindle keys (`kindle_host`, `kindle_ssh_key`, …) are
documented in [docs/kindle.md](docs/kindle.md).

### Feeds

```yaml
feeds:
  - name: "HS Maailma"
    url: "http://www.hs.fi/rss/maailma.xml"
    section: "Maailma"      # Must match an entry in `sections`
    max_age_hours: 24       # Only include articles from the last N hours
```

A feed with no `section` lands in a trailing "Muut" section rather than being
dropped. Suggested `max_age_hours`: `24` for news, `72` for slower feeds, `168`
for weekly/city announcements.

### Scrape feeds

For sites without RSS:

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

### The catalog URL returns 403
- The nginx `alias` must point at the `public/` subdirectory and end with a
  trailing slash: `alias /var/www/ereader-news/public/;`
- The nginx worker user needs `+x` on every directory in the path — verify with
  `sudo -u www-data namei /path/to/public/opds.xml`
- Check the file modes in `public/` are readable by the nginx worker

### The catalog URL returns 404
- No edition has been built yet — run the manual build command from the quick
  start, or wait for the next :17
- The URL must include `/opds.xml`; the bare directory path is not a catalog

### Wrote files, but they are not in $EREADER_DATA_DIR
- `config.yaml` still has the relative `./` paths active — inside Docker those
  resolve to `/app` in the container. Uncomment the `/data/...` values.

### The device shows an empty catalog
- The X4 shows an empty list (no error) when an entry violates its parser rules;
  regenerate with the shipped code and check `opds.xml` is intact XML

### No articles being fetched
- Check that feeds are properly configured in config.yaml
- Verify feed URLs are valid by opening them in a browser
- Check logs for specific error messages: `docker compose logs -f`

### Articles deleted too quickly
- Adjust `max_age_hours` for that feed in config.yaml
- Or adjust the global `max_age_days` setting (applies during cleanup)

### Kindle sync problems
See [docs/kindle.md](docs/kindle.md#troubleshooting).

## License

MIT — see [LICENSE](LICENSE)
