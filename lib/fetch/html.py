"""Adaptive HTML fetcher: trafilatura+httpx first, Playwright fallback."""
from __future__ import annotations

from typing import Optional
import httpx

from lib.fetch.types import FetchResult
from lib.fetch.extract import extract_article_links
from lib.fetch.playwright_runner import fetch_url_html


_TIMEOUT = httpx.Timeout(20.0, connect=5.0)
_MIN_LINKS_FOR_SUCCESS = 3
_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) news-agent/0.1"


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
) -> tuple[FetchResult, str]:
    """Fetch a landing page and extract article links.

    Strategy:
      - scrape_method == "playwright" → skip httpx, go straight to Playwright.
      - Otherwise: try httpx; fall back to Playwright on HTTP failure or thin content.

    Returns:
      (FetchResult, used_method) where used_method ∈ {"http", "playwright"}.
      Caller persists used_method to sites.scrape_method.
    """
    url = source_cfg.get("url", "")
    if not url:
        return FetchResult(source=source_name, ok=False,
                           error="No URL in source config"), "http"

    if scrape_method == "playwright":
        return _try_playwright(source_name, source_cfg, url)

    # Try HTTP first
    html, _http_err = _http_fetch(url)
    if html is not None:
        articles = extract_article_links(html, source_cfg, source_name)
        if len(articles) >= _MIN_LINKS_FOR_SUCCESS:
            return FetchResult(source=source_name, articles=articles, ok=True), "http"

    # Fallback to Playwright
    return _try_playwright(source_name, source_cfg, url)


def _try_playwright(source_name: str, source_cfg: dict, url: str) -> tuple[FetchResult, str]:
    try:
        html = fetch_url_html(url)
    except Exception as exc:
        return FetchResult(source=source_name, ok=False,
                           error=f"Playwright fetch failed: {exc}"), "playwright"
    articles = extract_article_links(html, source_cfg, source_name)
    if not articles:
        return FetchResult(source=source_name, ok=False,
                           error="Playwright fetched but no links extracted"), "playwright"
    return FetchResult(source=source_name, articles=articles, ok=True), "playwright"
