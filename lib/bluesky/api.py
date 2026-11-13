"""Bluesky atproto API client — auth + getAuthorFeed.

Uses direct HTTP via httpx (no atproto SDK) to mirror the legacy notebook approach.
"""
from __future__ import annotations

from pathlib import Path

import httpx

_BASE = "https://bsky.social/xrpc"
_TIMEOUT = 15.0


def bsky_login(identifier: str, password: str) -> dict:
    """POST createSession; returns {accessJwt, refreshJwt, did, handle}."""
    url = f"{_BASE}/com.atproto.server.createSession"
    with httpx.Client(timeout=_TIMEOUT) as client:
        resp = client.post(url, json={"identifier": identifier, "password": password})
        resp.raise_for_status()
        return resp.json()


def bsky_get_author_feed(
    session: dict,
    actor: str,
    filter: str = "posts_and_author_threads",
    limit: int = 80,
    stop_uri: str | None = None,
    max_pages: int = 5,
) -> list[dict]:
    """GET getAuthorFeed; returns list of feed items (each {post, reply?, reason?}).

    ``limit`` is the per-page size (the API caps it at 100). Without ``stop_uri``
    one page is fetched. With ``stop_uri`` (the cross-run dedup marker), cursors
    are followed until that post is seen — it is included as the last item so
    the caller's dedup cut still finds it — or the feed ends, or ``max_pages``
    pages have been read. This keeps a busy day (> limit new posts) from pushing
    the marker out of the window and silently dropping the oldest new posts.
    """
    url = f"{_BASE}/app.bsky.feed.getAuthorFeed"
    headers = {"Authorization": f"Bearer {session['accessJwt']}"}
    items: list[dict] = []
    cursor: str | None = None
    with httpx.Client(timeout=_TIMEOUT) as client:
        for _ in range(max_pages if stop_uri else 1):
            params = {"actor": actor, "filter": filter, "limit": limit}
            if cursor:
                params["cursor"] = cursor
            resp = client.get(url, headers=headers, params=params)
            resp.raise_for_status()
            data = resp.json()
            for item in data.get("feed", []):
                items.append(item)
                if stop_uri and (item.get("post") or {}).get("uri") == stop_uri:
                    return items
            cursor = data.get("cursor")
            if not cursor:
                break
    return items


def bsky_upload_blob(session: dict, image_path: str | Path, mime_type: str) -> dict:
    """POST uploadBlob with raw image bytes; returns the blob ref dict.

    The returned dict is the `blob` object suitable for embedding as an
    `app.bsky.embed.external` thumbnail.
    """
    url = f"{_BASE}/com.atproto.repo.uploadBlob"
    data = Path(image_path).read_bytes()
    headers = {
        "Authorization": f"Bearer {session['accessJwt']}",
        "Content-Type": mime_type,
    }
    with httpx.Client(timeout=_TIMEOUT) as client:
        resp = client.post(url, headers=headers, content=data)
        resp.raise_for_status()
        return resp.json()["blob"]


def bsky_create_external_post(
    session: dict,
    text: str,
    uri: str,
    title: str,
    description: str,
    created_at: str,
    thumb: dict | None = None,
) -> dict:
    """Create an app.bsky.feed.post with an external link-preview card.

    `created_at` is an ISO-8601 timestamp supplied by the caller (keeps this
    module free of wall-clock for testability). Returns the created record
    ({uri, cid}).
    """
    url = f"{_BASE}/com.atproto.repo.createRecord"
    external: dict = {"uri": uri, "title": title, "description": description}
    if thumb:
        external["thumb"] = thumb
    record = {
        "$type": "app.bsky.feed.post",
        "text": text[:300],  # Bluesky 300-grapheme limit
        "createdAt": created_at,
        "embed": {"$type": "app.bsky.embed.external", "external": external},
    }
    body = {
        "repo": session["did"],
        "collection": "app.bsky.feed.post",
        "record": record,
    }
    headers = {"Authorization": f"Bearer {session['accessJwt']}"}
    with httpx.Client(timeout=_TIMEOUT) as client:
        resp = client.post(url, headers=headers, json=body)
        resp.raise_for_status()
        return resp.json()
