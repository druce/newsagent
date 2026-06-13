"""Tests for lib/bluesky/images.py — download + Pillow resize."""
import io
from pathlib import Path

import pytest
import respx
import httpx
from PIL import Image


# Build a real 100x200 PNG in memory
def _make_png(width: int = 100, height: int = 200) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), color=(128, 64, 32)).save(buf, "PNG")
    return buf.getvalue()


TINY_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000d49444154789c63000100000005000100200d010200000000049ae42100"
    "00000049454e44ae426082"
)


def test_download_image_saves_file(tmp_path):
    png_bytes = _make_png(50, 50)
    with respx.mock:
        respx.get("https://cdn.example.com/photo.jpg").mock(
            return_value=httpx.Response(200, content=png_bytes,
                                        headers={"content-type": "image/png"})
        )
        from lib.bluesky.images import download_image
        result = download_image("https://cdn.example.com/photo.jpg", tmp_path)

    assert result is not None
    assert result.exists()
    assert result.stat().st_size > 0


def test_resize_image_changes_height(tmp_path):
    # Create a 100x200 image and resize to height=50
    png_bytes = _make_png(100, 200)
    img_path = tmp_path / "test.png"
    img_path.write_bytes(png_bytes)

    from lib.bluesky.images import resize_image
    result_path = resize_image(img_path, desired_height=50)

    assert result_path == img_path
    img = Image.open(result_path)
    assert img.height == 50
    # Aspect ratio maintained: width should be ~25
    assert img.width == 25


def test_download_image_rejects_oversized(tmp_path):
    # Create content larger than max_bytes (we'll pass a small max_bytes)
    png_bytes = _make_png(100, 100)
    with respx.mock:
        respx.get("https://cdn.example.com/big.jpg").mock(
            return_value=httpx.Response(200, content=png_bytes,
                                        headers={"content-type": "image/png"})
        )
        from lib.bluesky.images import download_image
        result = download_image("https://cdn.example.com/big.jpg", tmp_path, max_bytes=10)

    assert result is None


def test_download_image_skips_non_image_content_type(tmp_path):
    """A non-image content type (e.g. an HTML error page) yields None, never a .img file."""
    with respx.mock:
        respx.get("https://cdn.example.com/notimg").mock(
            return_value=httpx.Response(200, content=b"<html>nope</html>",
                                        headers={"content-type": "text/html"})
        )
        from lib.bluesky.images import download_image
        result = download_image("https://cdn.example.com/notimg", tmp_path)

    assert result is None
    assert not list(Path(tmp_path).glob("*.img"))


def test_download_image_skips_svg(tmp_path):
    with respx.mock:
        respx.get("https://cdn.example.com/logo.svg").mock(
            return_value=httpx.Response(200, content=b"<svg></svg>",
                                        headers={"content-type": "image/svg+xml"})
        )
        from lib.bluesky.images import download_image
        result = download_image("https://cdn.example.com/logo.svg", tmp_path)

    assert result is None


def test_resize_image_rgba_to_jpeg_does_not_crash(tmp_path):
    """A .jpg target must be saved as RGB (real-run regression: 'cannot write mode … as JPEG')."""
    buf = io.BytesIO()
    Image.new("RGBA", (200, 100), (255, 0, 0, 128)).save(buf, "PNG")
    img_path = tmp_path / "photo.jpg"
    img_path.write_bytes(buf.getvalue())  # RGBA bytes behind a .jpg name

    from lib.bluesky.images import resize_image
    resize_image(img_path, desired_height=50)

    out = Image.open(img_path)
    assert out.height == 50
    assert out.mode == "RGB"
