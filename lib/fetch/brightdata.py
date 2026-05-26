"""Bright Data Web Unlocker fetcher.

Routes URLs through the Bright Data Web Unlocker proxy and returns extracted
article text + raw HTML. Used for domains we know are paywalled or aggressively
anti-bot (Bloomberg, WSJ, CNN, FT, Forbes, Fast Company, ...). Domains opt in
via `sites.bright_data_enabled=1`.

The functions mirror `lib.steps.download._http_fetch`'s return shape
`(text, html, final_url, error)` so the download step can route either path
through the same persistence code.

Concurrency model:
  - `scrape_urls_brightdata(urls)` uses the async `BrightDataClient` and runs
    requests in parallel under an `asyncio.Semaphore` (default 8). The caller
    surface stays synchronous via `asyncio.run`.
  - `scrape_single_url_brightdata(url)` uses the sync client for one-shot
    calls; cheap when you only need one URL.
"""
from __future__ import annotations

import asyncio
import os
from typing import Any, Iterable, Optional

import trafilatura

try:
    from brightdata import BrightDataClient, SyncBrightDataClient
except ImportError:  # pragma: no cover - dep is in pyproject
    BrightDataClient = None  # type: ignore[assignment,misc]
    SyncBrightDataClient = None  # type: ignore[assignment,misc]


_MIN_TEXT_CHARS = 400
_DEFAULT_TIMEOUT = 90
_DEFAULT_CONCURRENCY = 8


def _extract_text(html: str) -> Optional[str]:
    return trafilatura.extract(html, include_comments=False, include_tables=False)


def _interpret_result(
    url: str, result: Any
) -> tuple[Optional[str], Optional[str], Optional[str], Optional[str]]:
    """Map a BD ScrapeResult into our (text, html, final_url, error) tuple."""
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


def _scrape_with(
    client: Any, url: str, zone: Optional[str]
) -> tuple[Optional[str], Optional[str], Optional[str], Optional[str]]:
    """Scrape `url` using an already-entered SYNC `client`."""
    try:
        result = client.scrape_url(url, zone=zone) if zone else client.scrape_url(url)
    except Exception as exc:
        return None, None, None, f"bright data error: {exc}"[:200]
    return _interpret_result(url, result)


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
    client setup and run concurrently.

    Returns:
        (text, html, final_url, error). On success: text/html/final_url are set
        and error is None. On failure: text is None and error is a short
        message; html may still be populated when extraction was too thin.

        `final_url` echoes back the input URL — Web Unlocker doesn't surface
        a post-redirect canonical URL. Downstream can parse
        `<link rel="canonical">` from the HTML if needed.
    """
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


async def _ascrape_one(
    client: Any,
    sem: asyncio.Semaphore,
    url: str,
    zone: Optional[str],
) -> tuple[Optional[str], Optional[str], Optional[str], Optional[str]]:
    async with sem:
        try:
            result = (
                await client.scrape_url(url, zone=zone)
                if zone
                else await client.scrape_url(url)
            )
        except Exception as exc:
            return None, None, None, f"bright data error: {exc}"[:200]
    return _interpret_result(url, result)


async def _ascrape_many(
    urls: list[str],
    *,
    api_key: str,
    zone: Optional[str],
    timeout: int,
    concurrency: int,
) -> dict[str, tuple[Optional[str], Optional[str], Optional[str], Optional[str]]]:
    assert BrightDataClient is not None  # gated by caller
    sem = asyncio.Semaphore(concurrency)
    async with BrightDataClient(
        token=api_key,
        timeout=timeout,
        web_unlocker_zone=zone,
        auto_create_zones=True,
        validate_token=False,
    ) as c:
        results = await asyncio.gather(
            *(_ascrape_one(c, sem, u, zone) for u in urls)
        )
    return {u: r for u, r in zip(urls, results)}


def scrape_urls_brightdata(
    urls: Iterable[str],
    *,
    api_key: Optional[str] = None,
    zone: Optional[str] = None,
    timeout: int = _DEFAULT_TIMEOUT,
    concurrency: int = _DEFAULT_CONCURRENCY,
) -> dict[str, tuple[Optional[str], Optional[str], Optional[str], Optional[str]]]:
    """Scrape many URLs concurrently via the async Bright Data client.

    Returns:
        Dict mapping each input URL to its `(text, html, final_url, error)`
        tuple. Order of the input is preserved by insertion order of the dict.
        Up to `concurrency` (default 8) requests run in parallel under an
        asyncio.Semaphore.
    """
    urls = list(urls)
    out: dict[str, tuple[Optional[str], Optional[str], Optional[str], Optional[str]]] = {}

    if not urls:
        return out

    if BrightDataClient is None:
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
        return asyncio.run(
            _ascrape_many(
                urls,
                api_key=api_key,
                zone=zone,
                timeout=timeout,
                concurrency=concurrency,
            )
        )
    except Exception as exc:
        err = f"bright data client error: {exc}"[:200]
        for u in urls:
            out.setdefault(u, (None, None, None, err))
    return out
