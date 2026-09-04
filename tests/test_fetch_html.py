import respx
import httpx
from unittest.mock import patch
from lib.fetch.html import fetch_html


_HTML_GOOD = """
<html><body>
<a href="/2026/05/story-one">Story One Headline About Something Long Enough</a>
<a href="/2026/05/story-two">Story Two Headline About Something Long Enough</a>
<a href="/2026/05/story-three">Story Three Headline About Something Long Enough</a>
<a href="/2026/05/story-four">Story Four Headline About Something Long Enough</a>
</body></html>
"""

_HTML_THIN = "<html><body><a href='/x'>x</a></body></html>"

_CFG_GOOD = {
    "url": "https://news.example.com/",
    "include": [r"^https://news\.example\.com/2026/"],
    "minlength": 10,
}


@respx.mock
def test_fetch_html_uses_httpx_when_method_is_none_or_http():
    respx.get("https://news.example.com/").mock(
        return_value=httpx.Response(200, text=_HTML_GOOD)
    )
    result, method, raw = fetch_html("Example", _CFG_GOOD, scrape_method=None)
    assert result.ok is True
    assert method == "http"
    assert raw == _HTML_GOOD
    assert len(result.articles) >= 3


@respx.mock
def test_fetch_html_falls_back_to_playwright_on_thin_content():
    respx.get("https://news.example.com/").mock(
        return_value=httpx.Response(200, text=_HTML_THIN)
    )
    with patch("lib.fetch.html.fetch_url_html", return_value=_HTML_GOOD) as mock_pw:
        result, method, raw = fetch_html("Example", _CFG_GOOD, scrape_method=None)
    assert method == "playwright"
    assert mock_pw.called
    assert raw == _HTML_GOOD
    assert len(result.articles) >= 3


@respx.mock
def test_fetch_html_falls_back_to_playwright_on_http_error():
    respx.get("https://news.example.com/").mock(
        return_value=httpx.Response(500, text="explode")
    )
    with patch("lib.fetch.html.fetch_url_html", return_value=_HTML_GOOD) as mock_pw:
        _result, method, _raw = fetch_html("Example", _CFG_GOOD, scrape_method=None)
    assert method == "playwright"
    assert mock_pw.called


def test_fetch_html_goes_straight_to_playwright_when_pinned():
    with patch("lib.fetch.html.fetch_url_html", return_value=_HTML_GOOD) as mock_pw:
        with respx.mock:  # ensure no HTTP call is made
            result, method, raw = fetch_html("Example", _CFG_GOOD, scrape_method="playwright")
    assert method == "playwright"
    assert mock_pw.called
    assert result.ok is True
    assert raw == _HTML_GOOD


@respx.mock
def test_fetch_html_reports_failure_if_both_methods_fail():
    respx.get("https://news.example.com/").mock(
        return_value=httpx.Response(500)
    )
    with patch("lib.fetch.html.fetch_url_html",
               side_effect=RuntimeError("pw broken")):
        result, method, raw = fetch_html("Example", _CFG_GOOD, scrape_method=None)
    assert result.ok is False
    assert method == "playwright"  # last attempted
    assert raw is None  # both methods failed, nothing to save


def test_try_playwright_threads_scroll_options():
    cfg = dict(_CFG_GOOD, scroll=10, scroll_div="#f", initial_sleep=20,
               scroll_wait=10, scroll_time_budget=200)
    with patch("lib.fetch.html.fetch_url_html", return_value=_HTML_GOOD) as mock_pw:
        fetch_html("Example", cfg, scrape_method="playwright")
    kw = mock_pw.call_args.kwargs
    assert kw["scroll"] == 10
    assert kw["scroll_div"] == "#f"
    assert kw["initial_sleep"] == 20.0
    assert kw["scroll_wait"] == 10.0
    assert kw["scroll_time_budget"] == 200.0


def test_try_playwright_default_scroll_wait():
    with patch("lib.fetch.html.fetch_url_html", return_value=_HTML_GOOD) as mock_pw:
        fetch_html("Example", _CFG_GOOD, scrape_method="playwright")
    kw = mock_pw.call_args.kwargs
    assert kw["scroll_wait"] == 8.0
    assert kw["scroll_time_budget"] == 180.0
