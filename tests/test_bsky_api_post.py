"""Tests for the posting additions in lib/bluesky/api.py — uploadBlob + createRecord."""
import json

import httpx
import respx

from lib.bluesky.api import bsky_create_external_post, bsky_upload_blob


def test_upload_blob_sends_raw_bytes_and_mime_and_returns_blob(tmp_path):
    img = tmp_path / "thumb.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\nFAKEPNGDATA")
    session = {"accessJwt": "tok", "did": "did:plc:me"}
    blob = {"$type": "blob", "ref": {"$link": "bafy123"}, "mimeType": "image/png", "size": 22}

    with respx.mock:
        route = respx.post(
            "https://bsky.social/xrpc/com.atproto.repo.uploadBlob"
        ).mock(return_value=httpx.Response(200, json={"blob": blob}))
        result = bsky_upload_blob(session, img, "image/png")

    assert result == blob
    req = route.calls[0].request
    assert req.headers["content-type"] == "image/png"
    assert "Bearer tok" in req.headers["authorization"]
    assert req.content == b"\x89PNG\r\n\x1a\nFAKEPNGDATA"


def test_create_external_post_builds_card_with_thumb():
    session = {"accessJwt": "tok", "did": "did:plc:me"}
    blob = {"$type": "blob", "ref": {"$link": "bafy123"}}

    with respx.mock:
        route = respx.post(
            "https://bsky.social/xrpc/com.atproto.repo.createRecord"
        ).mock(return_value=httpx.Response(200, json={"uri": "at://x/post/1", "cid": "c1"}))
        result = bsky_create_external_post(
            session,
            text="My headline",
            uri="https://news.com/story",
            title="OG title",
            description="OG desc",
            created_at="2026-06-02T00:00:00.000Z",
            thumb=blob,
        )

    assert result["uri"] == "at://x/post/1"
    body = json.loads(route.calls[0].request.content)
    assert body["repo"] == "did:plc:me"
    assert body["collection"] == "app.bsky.feed.post"
    rec = body["record"]
    assert rec["text"] == "My headline"
    assert rec["createdAt"] == "2026-06-02T00:00:00.000Z"
    ext = rec["embed"]["external"]
    assert rec["embed"]["$type"] == "app.bsky.embed.external"
    assert ext["uri"] == "https://news.com/story"
    assert ext["title"] == "OG title"
    assert ext["description"] == "OG desc"
    assert ext["thumb"] == blob


def test_create_external_post_omits_thumb_when_none_and_truncates_text():
    session = {"accessJwt": "tok", "did": "did:plc:me"}
    long_text = "x" * 400

    with respx.mock:
        route = respx.post(
            "https://bsky.social/xrpc/com.atproto.repo.createRecord"
        ).mock(return_value=httpx.Response(200, json={"uri": "at://x/post/2"}))
        bsky_create_external_post(
            session,
            text=long_text,
            uri="https://news.com/s",
            title="t",
            description="d",
            created_at="2026-06-02T00:00:00.000Z",
            thumb=None,
        )

    rec = json.loads(route.calls[0].request.content)["record"]
    assert "thumb" not in rec["embed"]["external"]
    assert len(rec["text"]) == 300
