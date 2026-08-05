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
