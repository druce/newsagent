"""Bright Data Web Unlocker fetcher.

Routes URLs through the Bright Data Web Unlocker proxy and returns extracted
article text + raw HTML. Used for domains we know are paywalled or aggressively
anti-bot (Bloomberg, WSJ, CNN, FT, Forbes, Fast Company, ...). Domains opt in
via `sites.bright_data_enabled=1`.

The function mirrors `lib.steps.download._http_fetch`'s return shape
`(text, html, final_url, error)` so the download step can route either path
through the same persistence code.

Uses Bright Data's official SDK (`brightdata-sdk`) via `SyncBrightDataClient`
so we get auto-zone-creation, token validation, and retries for free.

Concurrency notes:
  - `SyncBrightDataClient` is documented as NOT thread-safe; each call must
    happen on the same thread that entered the `with` block.
  - The client maintains a persistent event loop and is expensive to create.
  - Therefore the download step should use `scrape_urls_brightdata(urls)`
    which enters the client once and processes URLs sequentially.
  - `scrape_single_url_brightdata(url)` is also provided for one-shot use;
    it opens and closes a fresh client around the single call.
"""
from __future__ import annotations

import os
from typing import Any, Iterable, Optional

import trafilatura

try:
    from brightdata import SyncBrightDataClient
except ImportError:  # pragma: no cover - dep is in pyproject
    SyncBrightDataClient = None  # type: ignore[assignment,misc]


_MIN_TEXT_CHARS = 500
_DEFAULT_TIMEOUT = 90


def _extract_text(html: str) -> Optional[str]:
    return trafilatura.extract(html, include_comments=False, include_tables=False)


def _scrape_with(
    client: Any, url: str, zone: Optional[str]
) -> tuple[Optional[str], Optional[str], Optional[str], Optional[str]]:
    """Scrape `url` using an already-entered `client`.

    Returns (text, html, final_url, error).
    """
    try:
        result = client.scrape_url(url, zone=zone) if zone else client.scrape_url(url)
    except Exception as exc:
        return None, None, None, f"bright data error: {exc}"[:200]

    status = getattr(result, "status", None)
    data = getattr(result, "data", None)
    if status and status != "ready":
        return None, None, url, f"bright data status={status}"
    html = data if isinstance(data, str) else (str(data) if data else "")
    if not html:
        return None, None, url, "bright data returned empty body"

    text = _extract_text(html)
    if not text or len(text) < _MIN_TEXT_CHARS:
        return None, html, url, f"thin extract ({len(text) if text else 0} chars)"

    return text, html, url, None


def scrape_single_url_brightdata(
    url: str,
    *,
    api_key: Optional[str] = None,
    zone: Optional[str] = None,
    timeout: int = _DEFAULT_TIMEOUT,
    client: Optional[Any] = None,
) -> tuple[Optional[str], Optional[str], Optional[str], Optional[str]]:
    """Fetch a single URL via Bright Data Web Unlocker. Convenience one-shot
    wrapper — for multiple URLs, prefer `scrape_urls_brightdata` to amortize
    client setup.

    Returns:
        (text, html, final_url, error). On success: text/html/final_url are set
        and error is None. On failure: text is None and error is a short
        message; html may still be populated when extraction was too thin.

        `final_url` echoes back the input URL — Web Unlocker doesn't surface
        a post-redirect canonical URL. Downstream can parse
        `<link rel="canonical">` from the HTML if needed.
    """
    # Test-injected client: caller has already entered the context.
    if client is not None:
        zone = zone or os.environ.get("BRIGHTDATA_ZONE") or None
        return _scrape_with(client, url, zone)

    if SyncBrightDataClient is None:
        return None, None, None, "brightdata-sdk not installed"

    api_key = api_key or os.environ.get("BRIGHTDATA_API_KEY")
    if not api_key:
        return None, None, None, "BRIGHTDATA_API_KEY not set"

    zone = zone or os.environ.get("BRIGHTDATA_ZONE") or None

    try:
        with SyncBrightDataClient(
            token=api_key,
            timeout=timeout,
            web_unlocker_zone=zone,
            auto_create_zones=True,
            validate_token=False,
        ) as c:
            return _scrape_with(c, url, zone)
    except Exception as exc:
        return None, None, None, f"bright data client error: {exc}"[:200]


def scrape_urls_brightdata(
    urls: Iterable[str],
    *,
    api_key: Optional[str] = None,
    zone: Optional[str] = None,
    timeout: int = _DEFAULT_TIMEOUT,
) -> dict[str, tuple[Optional[str], Optional[str], Optional[str], Optional[str]]]:
    """Scrape multiple URLs through a single Bright Data client.

    Returns:
        Dict mapping each input URL to its `(text, html, final_url, error)`
        tuple. Order of the input is preserved by insertion order of the dict.
        URLs are processed sequentially (SyncBrightDataClient is not
        thread-safe per its docstring); proxy latency dominates anyway.
    """
    urls = list(urls)
    out: dict[str, tuple[Optional[str], Optional[str], Optional[str], Optional[str]]] = {}

    if not urls:
        return out

    if SyncBrightDataClient is None:
        for u in urls:
            out[u] = (None, None, None, "brightdata-sdk not installed")
        return out

    api_key = api_key or os.environ.get("BRIGHTDATA_API_KEY")
    if not api_key:
        for u in urls:
            out[u] = (None, None, None, "BRIGHTDATA_API_KEY not set")
        return out

    zone = zone or os.environ.get("BRIGHTDATA_ZONE") or None

    try:
        with SyncBrightDataClient(
            token=api_key,
            timeout=timeout,
            web_unlocker_zone=zone,
            auto_create_zones=True,
            validate_token=False,
        ) as c:
            for u in urls:
                out[u] = _scrape_with(c, u, zone)
    except Exception as exc:
        err = f"bright data client error: {exc}"[:200]
        for u in urls:
            out.setdefault(u, (None, None, None, err))
    return out
