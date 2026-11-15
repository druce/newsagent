"""Candidate images for edition items: og:image → download → 600px-wide resize.

Best-effort and deterministic (no LLM). Every item id maps to either
(image path, "") or (None, reason), reason ∈ {"no og:image", "fetch failed",
"logo", "too small"}. Images are cached in their own dir (default
download/edition-images/) so the Bluesky digest's 240px-tall cache is never touched.
"""
from __future__ import annotations

import hashlib
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urljoin

from PIL import Image

from lib.bluesky.images import download_image, resize_to_width
from lib.bluesky.og_tags import get_og_tags

IMAGE_WIDTH = 600
MIN_WIDTH = 200
DEFAULT_IMAGE_DIR = Path("download/edition-images")


def _og_image(url: str) -> str | None:
    try:
        img = (get_og_tags(url) or {}).get("image")
    except Exception:
        return None
    return urljoin(url, img) if img else None


def _cached(img_url: str, cache_dir: Path) -> Path | None:
    stem = hashlib.sha256(img_url.encode()).hexdigest()[:16]
    return next((p for p in sorted(cache_dir.glob(f"{stem}.*")) if p.is_file()), None)


def _fetch(img_url: str, cache_dir: Path) -> tuple[Path | None, str]:
    try:
        path = _cached(img_url, cache_dir) or download_image(img_url, cache_dir)
        if path is None:
            return None, "fetch failed"
        with Image.open(path) as im:
            width = im.size[0]
        if width < MIN_WIDTH:
            return None, "too small"
        return resize_to_width(path, IMAGE_WIDTH), ""
    except Exception:
        return None, "fetch failed"


def fetch_item_images(urls: dict[int, str], cache_dir: Path = DEFAULT_IMAGE_DIR,
                      workers: int = 16) -> dict[int, tuple[Path | None, str]]:
    """Map each item id to (resized image path, "") or (None, reason)."""
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    articles = sorted(set(urls.values()))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        og = dict(zip(articles, pool.map(_og_image, articles)))

    # Same og:image on 2+ distinct articles = a site default (logo), not a story image.
    by_image: dict[str, set[str]] = defaultdict(set)
    for art, img in og.items():
        if img:
            by_image[img].add(art)
    logos = {img for img, arts in by_image.items() if len(arts) >= 2}
    wanted = sorted(img for img in by_image if img not in logos)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        fetched = dict(zip(wanted, pool.map(lambda u: _fetch(u, cache_dir), wanted)))

    out: dict[int, tuple[Path | None, str]] = {}
    for item_id, art in urls.items():
        img = og.get(art)
        if not img:
            out[item_id] = (None, "no og:image")
        elif img in logos:
            out[item_id] = (None, "logo")
        else:
            out[item_id] = fetched[img]
    return out
