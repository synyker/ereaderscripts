# Kindle (KOReader) sync

The original delivery path: articles are pushed to a jailbroken Kindle running
[KOReader](https://koreader.rocks/) as individual HTML files over SSH/SCP. No
rsync or other tools are needed on the Kindle — SCP is built into its SSH server.

Unlike the OPDS path, this is push-based and manual by design: KOReader's SSH
server has to be started by hand on the device before each sync.

## Setup

1. **Enable SSH on the Kindle:**
   - Open KOReader menu → Network → SSH server → Start
   - Note the IP address and port (default: 2222)

2. **Set up key-based authentication (optional, but recommended):**
   ```bash
   ssh-copy-id -p 2222 root@<kindle-ip>
   ```
   Replace `<kindle-ip>` with your Kindle's IP (e.g., `192.168.1.42`)

3. **Configure the connection in `config.yaml`:**
   ```yaml
   kindle_host: root@192.168.1.42
   kindle_news_dir: /mnt/us/koreader/news
   kindle_ssh_port: 2222                      # KOReader SSH server uses port 2222
   kindle_ssh_key: ~/.ssh/id_ed25519          # Optional: path to SSH key
   ```

   `kindle_host` and `kindle_ssh_key` can be overridden with the environment
   variables `KINDLE_HOST` and `KINDLE_SSH_KEY`, which take precedence over the
   file.

## Syncing

Bare metal:

```bash
python fetch_news.py --sync
```

From the Docker deployment (the SSH key directory is mounted per-invocation; the
scheduled container never has it):

```bash
docker compose run --rm \
  -v "${EREADER_SSH_DIR}:/root/.ssh:ro" \
  --entrypoint python \
  ereader-news /app/fetch_news.py --sync
```

The sync copies the whole article tree — per-feed folders plus the generated
`index.html`, `all.html` and `all_links.html` — to `kindle_news_dir`, preserving
structure. Open `index.html` in KOReader to browse by feed, or `all.html` to read
everything in one document sorted by date.

## Scheduling with cron (bare metal)

To fetch articles frequently but sync to the Kindle only once a day:

```cron
# Fetch articles every 6 hours (at 0:00, 6:00, 12:00, 18:00)
0 */6 * * * cd /path/to/ereaderscripts && python fetch_news.py

# Fetch and sync to Kindle at 07:30 every morning
30 7 * * * cd /path/to/ereaderscripts && python fetch_news.py --sync
```

Remember the SSH server must be running on the Kindle when the sync fires.

## Output structure

```
ereader-news/
├── index.html                    # Article index grouped by feed
├── all.html                      # Every article in one document, newest first
├── all_links.html                # Links-only listing, newest first
├── Yle_Tuoreimmat/
│   ├── 20250327_abc123def456.html
│   └── 20250327_abc123def456.meta
└── HS_Politiikka/
    ├── 20250327_xyz789uvw012.html
    └── 20250327_xyz789uvw012.meta
```

Each article has:
- `.html`: The article content, formatted for e-ink reading
- `.meta`: Metadata (original URL, title, publication date, fetch timestamp)

Only `.html` files are copied to the Kindle; `.meta` sidecars stay on the server.

## Troubleshooting

### Articles not syncing to Kindle
- Verify the SSH server is running on the Kindle: Menu → Network → SSH server → Start
- Test the connection: `ssh -p 2222 root@<kindle-ip>`
- Check that `kindle_host` and `kindle_ssh_port` are correct in config.yaml

### Sync failing with "permission denied"
- Ensure you've run `ssh-copy-id -p 2222 root@<kindle-ip>` once
- Check the SSH key permissions: `chmod 600 <key file>`
- In Docker, confirm `EREADER_SSH_DIR` points at the directory holding the key
  and that `kindle_ssh_key` in config.yaml uses the container-side path
  (`/root/.ssh/<key file>`)
