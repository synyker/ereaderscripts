# Design: OPDS news edition for the Xteink X4

Date: 2026-08-05
Status: approved

## Goal

Deliver the existing RSS news pipeline to an Xteink X4 running CrossPoint firmware, as a
single EPUB "edition" published through an OPDS catalog. The pipeline moves to a Docker
Compose deployment on the home Ubuntu server, served over the internet through the
existing nginx with HTTP Basic auth. The Kindle SCP path is preserved and run manually.

## Background: what the device requires

Facts taken from the CrossPoint firmware source, not from documentation:

- **OPDS 1.x Atom XML only.** `lib/OpdsParser/OpdsParser.cpp` is an expat parser. OPDS 2.0
  JSON is not understood.
- **Book entries** are recognised only when an `<entry>` contains a `<link>` whose `rel`
  contains `opds-spec.org/acquisition` *and* whose `type` is exactly
  `application/epub+zip`. When several acquisition links exist, hrefs containing `.epub`
  or `/epub/` win.
- **Entries lacking a non-empty `<title>` or an href are silently dropped.**
- **Navigation entries** are entry links with `type` containing `application/atom+xml`.
- **Downloads land in the SD card root** named `<author> - <title>.epub`, sanitized
  (`src/activities/browser/OpdsBookBrowserActivity.cpp`). An empty author yields
  `<title>.epub`.
- **HTTPS is verified against the ESP32 bundled CA roots**; `CONFIG_ESP_TLS_INSECURE` is
  off, so self-signed and private-CA certificates cannot work. Let's Encrypt does.
- **HTTP Basic auth is sent preemptively** on the first request, so nginx `auth_basic`
  needs no special handling. Up to 5 redirects are followed.
- **There is no scheduler on the device.** The OPDS client is an interactive activity: the
  user opens the catalog, browses with the buttons, and selects one entry to download.
  Refresh cadence is therefore entirely a server-side concern.

## Design decisions

| Decision | Choice | Rationale |
|---|---|---|
| Edition contents | Every article published in the last 24h, uncapped | Volume is solved by navigation, not by dropping articles |
| Structure | Topic sections merging sources, nested TOC | Counteracts the habit of reading only Yle Tuoreimmat |
| Catalog | One entry, "News - Latest" | Nothing accumulates on the SD card; no pruning on device |
| Images | Embedded, 4-level grayscale, downscaled | Kept experimentally; secondary to text |
| Code layout | Extract an article store; leave the rest | The store is the one boundary the new feature genuinely needs |
| Kindle sync | Ships in the image, run manually | Cannot run on a schedule; KOReader's SSH server is started by hand |

## Architecture

```
hourly cron (supercronic, in container)
  └─ fetch_news.py
       ├─ feeds → extract → ArticleStore    /data/articles/<Feed>/<date>_<id>.{html,meta}
       ├─ prune articles older than max_age_days
       └─ build edition
            ├─ epub.py  → /data/public/news-latest.epub   (atomic replace)
            └─ opds.py  → /data/public/opds.xml           (atomic replace)

host nginx → ${EREADER_DATA_DIR}/public/ → X4 pulls on demand
```

The article store keeps its current on-disk form: `.html` + `.meta` pairs under a
per-feed directory, IDs hashed from the article URL. It is already the dedup and
retention mechanism and needs no change. The edition is a pure function of the store, so
any rebuild is reproducible and independent of prior runs.

## Components

### `articles.py` (new)

`ArticleStore` wrapping the existing directory layout:

- `has(article_id) -> bool` — dedup check, replaces `get_existing_article_ids`
- `save(article) -> Path` — writes the `.html` and `.meta` pair
- `since(hours) -> list[Article]` — the edition window
- `prune(days) -> int` — replaces `cleanup_old_articles`

`Article` is a dataclass: `id`, `url`, `title`, `feed`, `published` (aware datetime),
`body` (extracted HTML fragment).

Two additive changes to the `.meta` format:

- `feed=` records the feed's display name so it survives independently of the directory
  name. Files without it fall back to the directory name.
- `published=` records an ISO-8601 UTC timestamp. The existing `date=` line holds a
  *Helsinki-local* display string that `cleanup_old_articles` parses as UTC — a silent
  three-hour skew that matters more for a 24-hour edition window than it did for
  three-day retention. Legacy files without `published=` are read by parsing `date=` as
  Helsinki-local, which also fixes them.

Both fall back cleanly, so existing content stays readable.

### `epub.py` (new)

`build_edition(articles, sections, out_path, image_max_width)`.

Uses `ebooklib`. One XHTML document per article, each an EPUB chapter. A nested TOC:
section → articles. The existing e-ink CSS from `HTML_TEMPLATE` is reused verbatim. A
masthead page carries the build timestamp, keeping it out of the title.

Article ordering: sections in configured order; newest first within a section.

### `images.py` (new)

Fetch, convert to 4-level grayscale, downscale to `image_max_width`, re-encode as JPEG,
embed in the EPUB. Results cached on disk keyed by URL hash so hourly rebuilds do not
refetch. Any failure drops that `<img>` and keeps the article.

`image_max_width` is a config value; the X4's exact panel resolution could not be
confirmed from public source, so it is tuned empirically starting at 480px.

### `opds.py` (new)

`write_catalog(entries, base_url, out_path)` emitting OPDS 1.2 Atom with a single
acquisition entry:

```xml
<entry>
  <id>urn:ereaderscripts:news:latest</id>
  <title>News - Latest</title>
  <updated>2026-08-05T08:17:00Z</updated>
  <link rel="http://opds-spec.org/acquisition"
        type="application/epub+zip"
        href="https://news.example.com/news/news-latest.epub"/>
</entry>
```

No `<author>`, so the on-device filename is `News - Latest.epub`. The title is ASCII-safe
(no em dashes, no parentheses) because the firmware sanitizes filenames. hrefs are
absolute, built from `public_base_url`, so one generator serves both LAN and public
access.

### `fetch_news.py` (modified)

Keeps feed parsing, scraping, extraction and `sync_to_kindle_scp` as they are. Delegates
store operations to `articles.py`. Gains `--build-edition` to produce the EPUB and
catalog. The existing `--sync`, `--clean-only`, `--feed-url` and `--rss-file` flags are
unchanged.

`generate_index`, `generate_all_articles` and `generate_all_links` stay for the Kindle
target.

### Deployment files (new, at repo root)

`Dockerfile`, `docker-compose.yml` and a supercronic crontab. Compose lives at the root so
everything runs from the project root.

The image's `ENTRYPOINT` is `python fetch_news.py`, so any flag passed to
`docker compose run` reaches the CLI directly. The default `CMD` starts supercronic, whose
crontab holds a single hourly entry:

```
17 * * * * python /app/fetch_news.py --build-edition
```

In the container, `output_dir` points at `/data/articles` and `public_dir` at
`/data/public`, both inside the bind mount.

```yaml
services:
  ereader-news:
    build: .
    volumes:
      - ./config.yaml:/app/config.yaml:ro
      - ${EREADER_DATA_DIR:?set EREADER_DATA_DIR in .env}:/data
    environment:
      TZ: ${TZ:-Europe/Helsinki}
      PUBLIC_BASE_URL: ${PUBLIC_BASE_URL:?set PUBLIC_BASE_URL in .env}
    restart: unless-stopped
```

No host path appears in a committed file. `EREADER_DATA_DIR` and `PUBLIC_BASE_URL` use
Compose's `:?` form so a missing value fails at startup with a named error rather than
silently mounting the wrong thing.

nginx serves the published directory directly — no upstream, no proxy pass. nginx has no
environment interpolation, so this ships as `docs/nginx.example.conf` with a placeholder
the operator substitutes on their own host; it is documentation, not a deployed file:

```nginx
location /news/ {
    alias /path/to/ereader-news/public/;   # = $EREADER_DATA_DIR/public/
    auth_basic           "News";
    auth_basic_user_file /etc/nginx/htpasswd/news;
    add_header Cache-Control "no-store";
    types {
        application/atom+xml  xml;
        application/epub+zip  epub;
    }
    default_type application/octet-stream;
}
```

The SSH key is not mounted into the scheduled service — nothing on the hourly path needs
it, and an optional bind mount has no safe default value. Kindle sync supplies it
per-invocation, after starting the SSH server in KOReader:

```bash
docker compose run --rm -v "${EREADER_SSH_DIR}:/root/.ssh:ro" \
  --entrypoint python ereader-news /app/fetch_news.py --sync
```

## Configuration additions

`output_dir` changes from the current macOS path to `/data/articles`. There is a single
deployment — the server — so no second config is needed; the Kindle sits on the same LAN
and is reachable from the container. Both paths are container-internal, so they carry no
host detail and stay in the committed example config.

```yaml
output_dir: /data/articles

# Topic sections, in TOC order. Feeds map into these by name.
sections:
  - Kotimaa
  - Maailma
  - Helsinki
  - Kulttuuri

edition:
  window_hours: 24
  image_max_width: 480

public_base_url: https://news.example.com/news
public_dir: /data/public

feeds:
  - name: "Yle Tuoreimmat"
    section: "Kotimaa"
    ...
```

Proposed starting mapping, editable at will: Kotimaa ← Yle Tuoreimmat, HS Politiikka;
Maailma ← HS Maailma; Helsinki ← HS Helsinki, Helsingin Uutiset, Helsingin kaupunki;
Kulttuuri ← HS Kulttuuri, HS Pääkirjoitukset, HS Lastenuutiset.

A feed with no `section:` lands in a trailing "Muut" section rather than being dropped.

## Public repository hygiene

The repo is intended to be published, so no committed file may reference a specific
deployment. Deployment values live in a gitignored `.env` read by Compose, with a
committed `.env.example` documenting each key:

```
EREADER_DATA_DIR=/absolute/path/on/host
PUBLIC_BASE_URL=https://news.example.com/news
EREADER_SSH_DIR=/home/you/.ssh
TZ=Europe/Helsinki
```

Config splits the same way: `config.example.yaml` is committed, real `config.yaml` is
gitignored. The example keeps the feed list — that is the useful part of the example and
reveals nothing — but replaces the deployment-specific values:

```yaml
kindle_host: root@KINDLE_IP        # e.g. root@192.168.1.42
kindle_ssh_key: /root/.ssh/id_ed25519
kindle_ssh_port: 2222
public_base_url: https://news.example.com/news
```

`public_base_url`, `kindle_host` and `kindle_ssh_key` accept environment overrides
(`PUBLIC_BASE_URL`, `KINDLE_HOST`, `KINDLE_SSH_KEY`), env taking precedence over the file,
so a deployment can be configured entirely through `.env` without editing tracked files.

`.gitignore` covers `.env`, `config.yaml`, `.venv/`, `ereader-news/`, `.image-cache/`, and
`__pycache__/`. The `ereader-news/` directory (renamed from `kindle-news/`, since it now
serves two devices) holds the fetched articles and must never be committed.

Two things stay outside the repo entirely: the nginx htpasswd file, and the SSH key for
the Kindle. The repo has been initialised but has no commits and no remote, so the first
commit starts clean — there is no history to scrub.

### Pre-publication audit

Findings from a scan of everything outside `.venv/` and `ereader-news/`:

| Location | Issue | Action |
|---|---|---|
| `config.yaml:5` | `output_dir` carries username and local path | Placeholder in `config.example.yaml`; real file gitignored |
| `config.yaml:16` | `kindle_host: root@<real LAN IP>` | Placeholder |
| `config.yaml:22` | SSH key *path* (not the key) | Placeholder |
| `README.md:99,102` | Crontab examples with the author's home directory | Rewrite as relative paths |
| `ereader-news/` | ~95 verbatim articles from HS, Yle, Helsingin Uutiset | Gitignored — copyright, not privacy |
| `.venv/` | Untracked, embeds absolute paths | Gitignored |

`fetch_news.py` is clean: its only IP addresses are `192.168.1.42` in docstrings, which is
a documentation placeholder. No keys, tokens, passwords or credentials exist anywhere in
the tracked files.

Separately, `git config user.email` is currently a work address. Every commit to a public
repo carries it, so set a per-repo identity (or a GitHub noreply address) before the first
commit if that is not intended.

## Bootstrap script

`init.py` prepares a fresh checkout or a new deployment. Per-feed article directories are
already created at runtime by `process_feed`, so the script does not exist for that; it
exists because `config.yaml` and `.env` are gitignored and the new `public/` and
`.image-cache/` directories have no other creator.

It is idempotent and safe to re-run:

1. Copy `config.example.yaml` → `config.yaml` and `.env.example` → `.env` if missing,
   reporting which values still need editing. Never overwrites an existing file.
2. Create `output_dir`, one subdirectory per configured feed, `public_dir`, and the image
   cache directory.
3. Exit non-zero with a readable message when `config.yaml` is unparseable or a required
   key is absent.

Creating the per-feed directories up front is redundant with the fetch path but harmless,
and it makes a fresh deployment inspectable before the first run.

## License

MIT, which is already declared in the README but has no `LICENSE` file backing it. It
matches the stated intent exactly: anyone may use, modify and redistribute the code,
provided the copyright notice and license text travel with it — use permitted,
attribution required.

Considered and rejected: Apache-2.0 (same permissions plus a patent grant and a
change-notice requirement — more ceremony than a personal news fetcher warrants);
CC-BY-4.0 (built for attribution, but Creative Commons themselves advise against applying
it to software, as it addresses neither source distribution nor warranty); GPL or MPL
(copyleft restricts reuse, contrary to "you can use this").

A `LICENSE` file requires a named copyright holder, which is a deliberate disclosure —
worth deciding alongside the commit identity noted above.

## Failure behavior

- Per-feed fetch failures log and continue, as today.
- Image fetch or conversion failure drops that image; the article still ships.
- The EPUB and catalog are written to a temp path and `os.replace`d. A device downloading
  during a rebuild always receives a complete file.
- A failed edition build leaves the previous EPUB and catalog in place and served.
- An empty 24h window keeps the previous edition rather than publishing an empty book.

## Testing

The repo has no tests today. New tests cover the new code only, with pytest:

- **Section mapping** — feeds route to their configured section; unmapped feeds land in
  "Muut"; section order follows config.
- **Window selection** — the 24h boundary includes and excludes correctly.
- **Store** — dedup by ID, prune by age, `feed=` fallback to directory name.
- **EPUB structure** — reopen the built file with `ebooklib`; assert TOC nesting, section
  names, and that every input article appears exactly once.
- **OPDS conformance** — parse the generated XML and assert CrossPoint's parser rules:
  non-empty `<title>`, an acquisition link whose `rel` contains `opds-spec.org/acquisition`,
  `type` exactly `application/epub+zip`, an href that is absolute and contains `.epub`.
  This is the highest-value test: violating any of these makes the catalog appear empty on
  the device with no error shown.

Live feed fetching and the Kindle SCP path stay untested.

## Out of scope

- Per-topic or dated archive editions in the catalog
- OPDS search and pagination
- Server-side article selection, ranking or summarisation
- KOReader progress sync
- Removing the Kindle HTML rendering path
