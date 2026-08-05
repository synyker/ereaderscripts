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
