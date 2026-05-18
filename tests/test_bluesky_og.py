"""Tests for lib/bluesky/og_tags.py — OG metadata fetcher."""
import respx
import httpx


_HTML_WITH_OG = """<!DOCTYPE html>
<html>
<head>
  <meta property="og:title" content="Test Article Title" />
  <meta property="og:description" content="A compelling article description." />
  <meta property="og:image" content="https://example.com/image.jpg" />
  <meta property="og:url" content="https://example.com/article" />
</head>
<body><p>Content</p></body>
</html>"""

_HTML_WITHOUT_OG = """<!DOCTYPE html>
<html><head><title>Plain Page</title></head><body><p>No OG tags here.</p></body></html>"""


def test_get_og_tags_parses_og_fields():
    with respx.mock:
        respx.get("https://example.com/article").mock(
            return_value=httpx.Response(200, text=_HTML_WITH_OG,
                                        headers={"content-type": "text/html"})
        )
        from lib.bluesky.og_tags import get_og_tags
        result = get_og_tags("https://example.com/article")

    assert result["title"] == "Test Article Title"
    assert result["description"] == "A compelling article description."
    assert result["image"] == "https://example.com/image.jpg"
    assert result["url"] == "https://example.com/article"


def test_get_og_tags_returns_empty_on_http_error():
    with respx.mock:
        respx.get("https://example.com/broken").mock(
            return_value=httpx.Response(404, text="Not Found")
        )
        from lib.bluesky.og_tags import get_og_tags
        result = get_og_tags("https://example.com/broken")

    assert result == {}


def test_get_og_tags_returns_empty_when_no_og_tags():
    with respx.mock:
        respx.get("https://example.com/plain").mock(
            return_value=httpx.Response(200, text=_HTML_WITHOUT_OG,
                                        headers={"content-type": "text/html"})
        )
        from lib.bluesky.og_tags import get_og_tags
        result = get_og_tags("https://example.com/plain")

    assert result == {}
