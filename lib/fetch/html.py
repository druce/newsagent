"""Adaptive HTML fetcher for landing pages: httpx + BeautifulSoup first,
Playwright fallback. Parses `<a>` tags via `lib.fetch.extract` — does NOT use
trafilatura (that's the download step, which extracts article body text).
"""
from __future__ import annotations

from typing import Optional
import httpx

from lib.fetch.types import FetchResult
from lib.fetch.extract import extract_article_links
from lib.fetch.playwright_runner import fetch_url_html


_TIMEOUT = httpx.Timeout(20.0, connect=5.0)
_MIN_LINKS_FOR_SUCCESS = 3
_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) newsagent/0.1"


def _http_fetch(url: str) -> tuple[Optional[str], Optional[str]]:
    """Returns (html, error). One of them is None."""
    try:
        with httpx.Client(timeout=_TIMEOUT, follow_redirects=True,
                          headers={"User-Agent": _UA}) as client:
            resp = client.get(url)
    except httpx.HTTPError as exc:
        return None, f"HTTP error: {exc}"
    if resp.status_code != 200:
        return None, f"HTTP {resp.status_code}"
    return resp.text, None


def fetch_html(
    source_name: str,
    source_cfg: dict,
    *,
    scrape_method: Optional[str],
) -> tuple[FetchResult, str, Optional[str]]:
    """Fetch a landing page and extract article links.

    Strategy:
      - scrape_method == "playwright" → skip httpx, go straight to Playwright.
      - Otherwise: try httpx; fall back to Playwright on HTTP failure or thin content.

    Returns:
      (FetchResult, used_method, raw_html) where used_method ∈ {"http", "playwright"}
      and raw_html is the page contents that was parsed (or None if fetch failed).
      Caller persists used_method to sites.scrape_method and may persist raw_html.
    """
    url = source_cfg.get("url", "")
    if not url:
        return FetchResult(source=source_name, ok=False,
                           error="No URL in source config"), "http", None

    if scrape_method == "playwright":
        return _try_playwright(source_name, source_cfg, url)

    # Try HTTP first
    html, _http_err = _http_fetch(url)
    if html is not None:
        articles = extract_article_links(html, source_cfg, source_name)
        if len(articles) >= _MIN_LINKS_FOR_SUCCESS:
            return FetchResult(source=source_name, articles=articles, ok=True), "http", html

    # Fallback to Playwright
    return _try_playwright(source_name, source_cfg, url)


def _try_playwright(
    source_name: str, source_cfg: dict, url: str
) -> tuple[FetchResult, str, Optional[str]]:
    scroll = int(source_cfg.get("scroll", 0) or 0)
    scroll_div = source_cfg.get("scroll_div", "") or ""
    initial_sleep = float(source_cfg.get("initial_sleep", 0) or 0)
    try:
        html = fetch_url_html(
            url,
            scroll=scroll,
            scroll_div=scroll_div,
            initial_sleep=initial_sleep,
        )
    except Exception as exc:
        return FetchResult(source=source_name, ok=False,
                           error=f"Playwright fetch failed: {exc}"), "playwright", None
    articles = extract_article_links(html, source_cfg, source_name)
    if not articles:
        return FetchResult(source=source_name, ok=False,
                           error="Playwright fetched but no links extracted"), "playwright", html
    return FetchResult(source=source_name, articles=articles, ok=True), "playwright", html
