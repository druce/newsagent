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


def _page(uris, cursor=None):
    d = {"feed": [{"post": {"uri": u, "record": {"text": u}}} for u in uris]}
    if cursor:
        d["cursor"] = cursor
    return d


def test_bsky_get_author_feed_paginates_until_stop_uri():
    """With stop_uri, follow cursors until the marker post is seen (inclusive)."""
    session = {"accessJwt": "t"}
    pages = [
        httpx.Response(200, json=_page(["a1", "a2"], cursor="c1")),
        httpx.Response(200, json=_page(["b1", "MARK", "b3"], cursor="c2")),
    ]
    with respx.mock:
        route = respx.get("https://bsky.social/xrpc/app.bsky.feed.getAuthorFeed").mock(
            side_effect=pages
        )
        from lib.bluesky.api import bsky_get_author_feed
        items = bsky_get_author_feed(session, "u", limit=2, stop_uri="MARK")

    assert [i["post"]["uri"] for i in items] == ["a1", "a2", "b1", "MARK"]
    assert route.call_count == 2
    assert route.calls[1].request.url.params["cursor"] == "c1"


def test_bsky_get_author_feed_stops_at_max_pages_or_no_cursor():
    session = {"accessJwt": "t"}
    with respx.mock:
        route = respx.get("https://bsky.social/xrpc/app.bsky.feed.getAuthorFeed").mock(
            side_effect=[
                httpx.Response(200, json=_page(["a"], cursor="c1")),
                httpx.Response(200, json=_page(["b"], cursor="c2")),
                httpx.Response(200, json=_page(["c"], cursor="c3")),
            ]
        )
        from lib.bluesky.api import bsky_get_author_feed
        items = bsky_get_author_feed(session, "u", limit=1, stop_uri="X", max_pages=2)
    assert [i["post"]["uri"] for i in items] == ["a", "b"]
    assert route.call_count == 2

    with respx.mock:
        route = respx.get("https://bsky.social/xrpc/app.bsky.feed.getAuthorFeed").mock(
            return_value=httpx.Response(200, json=_page(["a"]))
        )
        items = bsky_get_author_feed(session, "u", limit=1, stop_uri="X")
    assert route.call_count == 1


def test_bsky_get_author_feed_without_stop_uri_is_single_page():
    session = {"accessJwt": "t"}
    with respx.mock:
        route = respx.get("https://bsky.social/xrpc/app.bsky.feed.getAuthorFeed").mock(
            return_value=httpx.Response(200, json=_page(["a"], cursor="c1"))
        )
        from lib.bluesky.api import bsky_get_author_feed
        bsky_get_author_feed(session, "u", limit=1)
    assert route.call_count == 1
