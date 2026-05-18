"""Tests for lib/bluesky/api.py — atproto auth + getAuthorFeed."""
import pytest
import respx
import httpx


def test_bsky_login_posts_correct_body_and_returns_session():
    session_data = {
        "accessJwt": "test-jwt-token",
        "refreshJwt": "test-refresh-token",
        "did": "did:plc:test123",
        "handle": "testuser.bsky.social",
    }
    with respx.mock:
        respx.post("https://bsky.social/xrpc/com.atproto.server.createSession").mock(
            return_value=httpx.Response(200, json=session_data)
        )
        from lib.bluesky.api import bsky_login
        result = bsky_login("testuser.bsky.social", "secret-password")

    assert result["accessJwt"] == "test-jwt-token"
    assert result["did"] == "did:plc:test123"
    assert result["handle"] == "testuser.bsky.social"


def test_bsky_get_author_feed_includes_auth_header_and_returns_items():
    feed_data = {
        "feed": [
            {"post": {"uri": "at://test/post/1", "record": {"text": "Hello Bluesky"}}},
            {"post": {"uri": "at://test/post/2", "record": {"text": "Another post"}}},
        ]
    }
    session = {"accessJwt": "my-bearer-token"}
    with respx.mock:
        route = respx.get(
            "https://bsky.social/xrpc/app.bsky.feed.getAuthorFeed",
        ).mock(return_value=httpx.Response(200, json=feed_data))
        from lib.bluesky.api import bsky_get_author_feed
        items = bsky_get_author_feed(session, "testuser.bsky.social")

    assert len(items) == 2
    assert items[0]["post"]["record"]["text"] == "Hello Bluesky"
    # Verify auth header was used
    assert route.called
    request = route.calls[0].request
    assert "Bearer my-bearer-token" in request.headers.get("authorization", "")


def test_bsky_login_http_error_raises():
    with respx.mock:
        respx.post("https://bsky.social/xrpc/com.atproto.server.createSession").mock(
            return_value=httpx.Response(401, json={"error": "Unauthorized"})
        )
        from lib.bluesky.api import bsky_login
        with pytest.raises(Exception):
            bsky_login("baduser", "badpassword")


def test_bsky_get_author_feed_http_error_raises():
    session = {"accessJwt": "some-token"}
    with respx.mock:
        respx.get(
            "https://bsky.social/xrpc/app.bsky.feed.getAuthorFeed",
        ).mock(return_value=httpx.Response(500, json={"error": "Server Error"}))
        from lib.bluesky.api import bsky_get_author_feed
        with pytest.raises(Exception):
            bsky_get_author_feed(session, "testuser.bsky.social")
