from lib.fetch.extract import extract_article_links


_HTML = """
<html><body>
<a href="https://example.com/news/2026/05/article-1">Article One Headline (Long Enough)</a>
<a href="https://example.com/news/2026/05/article-2">Article Two Headline (Long Enough)</a>
<a href="https://example.com/ads/banner-1">SponsoredAdShort</a>
<a href="https://other.com/x">Off-domain link</a>
<a href="/relative/path">Relative link should resolve via base</a>
<a href="https://example.com/news/short">x</a>
<a href="javascript:void(0)">js link</a>
<a href="mailto:hi@example.com">email</a>
</body></html>
"""


def test_extract_respects_include_pattern():
    cfg = {
        "url": "https://example.com/",
        "include": [r"^https://example\.com/news/"],
        "minlength": 10,
    }
    links = extract_article_links(_HTML, cfg, source_name="Example")
    urls = [a.url for a in links]
    assert "https://example.com/news/2026/05/article-1" in urls
    assert "https://example.com/news/2026/05/article-2" in urls
    assert "https://example.com/ads/banner-1" not in urls
    assert "https://other.com/x" not in urls


def test_extract_respects_exclude_pattern():
    cfg = {
        "url": "https://example.com/",
        "exclude": [r"^https://example\.com/ads/"],
        "minlength": 10,
    }
    links = extract_article_links(_HTML, cfg, source_name="Example")
    urls = [a.url for a in links]
    assert "https://example.com/ads/banner-1" not in urls


def test_extract_drops_short_titles():
    cfg = {"url": "https://example.com/", "minlength": 20}
    links = extract_article_links(_HTML, cfg, source_name="Example")
    titles = [a.title for a in links]
    assert "x" not in titles
    assert "SponsoredAdShort" not in titles  # under 20 chars


def test_extract_drops_javascript_and_mailto():
    cfg = {"url": "https://example.com/", "minlength": 1}
    links = extract_article_links(_HTML, cfg, source_name="Example")
    urls = [a.url for a in links]
    assert not any(u.startswith("javascript:") for u in urls)
    assert not any(u.startswith("mailto:") for u in urls)


def test_extract_resolves_relative_urls():
    cfg = {"url": "https://example.com/", "minlength": 1}
    links = extract_article_links(_HTML, cfg, source_name="Example")
    urls = [a.url for a in links]
    assert "https://example.com/relative/path" in urls


def test_extract_returns_article_objects():
    cfg = {"url": "https://example.com/",
           "include": [r"^https://example\.com/news/"], "minlength": 10}
    links = extract_article_links(_HTML, cfg, source_name="Example")
    assert all(a.source == "Example" for a in links)
    assert all(a.published is None for a in links)


# --- article body extraction (trafilatura + __NEXT_DATA__ fallback) ---------
import json as _json
import time

from lib.fetch.extract import article_body_from_next_data, extract_article_text


def _next_data_html(body: str) -> str:
    payload = {"props": {"pageProps": {"data": {"articleBody": body}}}}
    return (
        "<html><head>"
        '<script id="__NEXT_DATA__" type="application/json">'
        + _json.dumps(payload)
        + "</script></head><body>"
        "<div>byKaran Sehgal | About Author</div>"  # boilerplate only
        "</body></html>"
    )


def test_article_body_from_next_data_returns_articlebody():
    body = "Real article prose. " * 50
    html = _next_data_html(body)
    assert article_body_from_next_data(html) == body


def test_article_body_from_next_data_absent_returns_none():
    assert article_body_from_next_data("<html><body>no next data</body></html>") is None


def test_article_body_from_next_data_malformed_json_returns_none():
    html = (
        '<html><head><script id="__NEXT_DATA__" type="application/json">'
        "{not valid json}</script></head></html>"
    )
    assert article_body_from_next_data(html) is None


def test_extract_article_text_prefers_next_data_when_trafilatura_thin():
    # trafilatura would only see the tiny boilerplate div; the real body lives
    # in __NEXT_DATA__. extract_article_text must recover the full body.
    body = "Climate-risk intelligence has a representation problem. " * 40
    html = _next_data_html(body)
    assert extract_article_text(html) == body


def test_extract_article_text_uses_trafilatura_when_richer():
    # A normal server-rendered article (no __NEXT_DATA__) still extracts via
    # trafilatura.
    paras = "".join(
        f"<p>{'This is a substantial paragraph of real article content. ' * 5}</p>"
        for _ in range(8)
    )
    html = f"<html><body><article>{paras}</article></body></html>"
    text = extract_article_text(html)
    assert text and "substantial paragraph" in text


def test_extract_article_text_serializes_trafilatura_calls(monkeypatch):
    """Concurrent extraction must never enter trafilatura simultaneously.

    trafilatura parses through a single module-global lxml HTMLParser
    (trafilatura/utils.py: HTML_PARSER). lxml parsers are not safe to share
    across threads: concurrent use corrupts the parser's interned-name dict
    and aborts the process inside _fixHtmlDictNames (SIGABRT), which killed
    the download step's 8-way Phase 1.
    """
    import threading
    from concurrent.futures import ThreadPoolExecutor

    from lib.fetch import extract as extract_mod

    inside = 0
    overlaps = []
    guard = threading.Lock()

    def fake_extract(html, **kwargs):
        nonlocal inside
        with guard:
            inside += 1
            if inside > 1:
                overlaps.append(inside)
        time.sleep(0.01)  # widen the race window
        with guard:
            inside -= 1
        return "extracted body text"

    monkeypatch.setattr(extract_mod.trafilatura, "extract", fake_extract)

    html = "<html><body><article><p>hello</p></article></body></html>"
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: extract_article_text(html), range(32)))

    assert all(r == "extracted body text" for r in results)
    assert not overlaps, f"trafilatura.extract ran concurrently: {overlaps}"
