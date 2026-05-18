"""Bluesky atproto API client — auth + getAuthorFeed.

Uses direct HTTP via httpx (no atproto SDK) to mirror the legacy notebook approach.
"""
from __future__ import annotations

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
) -> list[dict]:
    """GET getAuthorFeed; returns list of feed items (each {post, reply?, reason?})."""
    url = f"{_BASE}/app.bsky.feed.getAuthorFeed"
    headers = {"Authorization": f"Bearer {session['accessJwt']}"}
    params = {"actor": actor, "filter": filter, "limit": limit}
    with httpx.Client(timeout=_TIMEOUT) as client:
        resp = client.get(url, headers=headers, params=params)
        resp.raise_for_status()
        data = resp.json()
        return data.get("feed", [])
