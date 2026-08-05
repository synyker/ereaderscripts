"""Fetches article images and prepares them for a 4-level greyscale e-ink panel."""

import hashlib
import io
import logging
from collections.abc import Callable
from pathlib import Path

import requests
from lxml import etree
from PIL import Image, ImageOps

from fsutil import atomic_write_bytes

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

        atomic_write_bytes(cached, converted)
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
            # Preserve the text that follows the <img> (its tail) when removing it
            parent = img.getparent()
            if img.tail:
                prev = img.getprevious()
                if prev is not None:
                    prev.tail = (prev.tail or "") + img.tail
                else:
                    parent.text = (parent.text or "") + img.tail
            parent.remove(img)
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
