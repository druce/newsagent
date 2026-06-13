"""Post a single queued item to Bluesky as an external link-preview card.

Reuses the existing read-side Bluesky helpers (OG fetch + image resize) and the
new write-side api calls (uploadBlob + createRecord).
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from lib.bluesky.api import bsky_create_external_post, bsky_upload_blob
from lib.bluesky.images import download_image, resize_image
from lib.bluesky.og_tags import get_og_tags

_MIME_BY_SUFFIX = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".webp": "image/webp",
}


def _mime_for(path: Path) -> str:
    return _MIME_BY_SUFFIX.get(path.suffix.lower(), "image/jpeg")


def _build_thumb(session: dict, image_url: str, image_dir: Path) -> dict | None:
    """Best-effort: download → resize → uploadBlob. Returns blob ref or None."""
    try:
        local = download_image(image_url, image_dir)
        if not local:
            return None
        resize_image(local, desired_height=240)
        return bsky_upload_blob(session, local, _mime_for(local))
    except Exception:
        return None


def post_item(session: dict, item: dict, image_dir: str | Path) -> str:
    """Post one queue item; returns the created post's at:// uri.

    `item` has at least `url` and `title`. Fetches OG metadata for the card and
    uploads the OG image as a thumbnail when available; a thumbnail failure still
    posts a card without a picture.
    """
    url = item["url"]
    title = item["title"]
    og = get_og_tags(url) or {}

    thumb = None
    image_url = og.get("image")
    if image_url:
        thumb = _build_thumb(session, image_url, Path(image_dir))

    created_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    result = bsky_create_external_post(
        session,
        text=title,
        uri=url,
        title=og.get("title") or title,
        description=og.get("description", ""),
        created_at=created_at,
        thumb=thumb,
    )
    return result.get("uri", "")
