import respx
import httpx
from lib.fetch.rss import fetch_rss

_FEED_XML = """<?xml version="1.0"?>
<rss version="2.0">
<channel>
  <title>Example Feed</title>
  <item>
    <title>OpenAI ships GPT-6</title>
    <link>https://example.com/gpt6</link>
    <pubDate>Sun, 17 May 2026 10:00:00 GMT</pubDate>
    <description>Big AI news.</description>
  </item>
  <item>
    <title>Apple announces MacBook</title>
    <link>https://example.com/macbook</link>
    <pubDate>Sun, 17 May 2026 11:00:00 GMT</pubDate>
  </item>
</channel>
</rss>
"""


@respx.mock
def test_fetch_rss_parses_entries():
    respx.get("https://example.com/feed.xml").mock(
        return_value=httpx.Response(200, content=_FEED_XML.encode("utf-8"))
    )
    result = fetch_rss("Example", "https://example.com/feed.xml")
    assert result.ok is True
    assert result.source == "Example"
    assert len(result.articles) == 2
    assert result.articles[0].title == "OpenAI ships GPT-6"
    assert result.articles[0].url == "https://example.com/gpt6"
    assert result.articles[0].published is not None
    assert result.articles[0].rss_summary == "Big AI news."


@respx.mock
def test_fetch_rss_http_error_returns_not_ok():
    respx.get("https://example.com/feed.xml").mock(
        return_value=httpx.Response(500, text="server down")
    )
    result = fetch_rss("Example", "https://example.com/feed.xml")
    assert result.ok is False
    assert "500" in (result.error or "")
    assert result.articles == []


@respx.mock
def test_fetch_rss_caps_at_50():
    items = "".join(
        f"<item><title>Item {i}</title><link>https://example.com/{i}</link></item>"
        for i in range(100)
    )
    body = f"<?xml version='1.0'?><rss><channel>{items}</channel></rss>"
    respx.get("https://example.com/feed.xml").mock(
        return_value=httpx.Response(200, content=body.encode("utf-8"))
    )
    result = fetch_rss("Example", "https://example.com/feed.xml")
    assert len(result.articles) == 50
