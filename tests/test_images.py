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
