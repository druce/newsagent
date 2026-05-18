"""Image download + Pillow resize for Bluesky digest.

Images are cached in dest_dir using a hash of the URL as filename.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import httpx
from PIL import Image

_TIMEOUT = 15.0


def download_image(
    url: str,
    dest_dir: Path,
    max_bytes: int = 5_000_000,
) -> Path | None:
    """Download image to dest_dir using URL hash as filename.

    Returns saved path, or None on error or if image exceeds max_bytes.
    """
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)

    url_hash = hashlib.sha256(url.encode()).hexdigest()[:16]
    # Use a generic extension; we'll figure it out from content-type if needed
    dest_path = dest_dir / url_hash

    try:
        with httpx.Client(timeout=_TIMEOUT, follow_redirects=True) as client:
            resp = client.get(url)
            resp.raise_for_status()
            content = resp.content

        if len(content) > max_bytes:
            return None

        # Determine extension from content-type
        ct = resp.headers.get("content-type", "")
        if "jpeg" in ct or "jpg" in ct:
            dest_path = dest_path.with_suffix(".jpg")
        elif "png" in ct:
            dest_path = dest_path.with_suffix(".png")
        elif "gif" in ct:
            dest_path = dest_path.with_suffix(".gif")
        elif "webp" in ct:
            dest_path = dest_path.with_suffix(".webp")
        else:
            dest_path = dest_path.with_suffix(".img")

        dest_path.write_bytes(content)
        return dest_path

    except Exception:
        return None


def resize_image(input_path: Path, desired_height: int = 240) -> Path:
    """In-place resize maintaining aspect ratio. Returns the path."""
    input_path = Path(input_path)
    img = Image.open(input_path)
    orig_width, orig_height = img.size
    if orig_height == 0:
        return input_path
    ratio = desired_height / orig_height
    new_width = int(orig_width * ratio)
    resized = img.resize((new_width, desired_height), Image.LANCZOS)
    resized.save(input_path)
    return input_path
