"""Thin Playwright wrapper for fetching rendered HTML.

Uses a stealth-configured Firefox persistent context (see lib/fetch/browser.py)
so SSO flows, DataDome-protected sites, and other automation-detecting pages
treat us like a returning human user. Supports SPA-friendly options
(`scroll`, `scroll_div`, `initial_sleep`, `block_resources`) for sites like
Feedly that infinite-scroll. Failure modes (timeout, no network, etc.)
propagate as Exceptions; callers decide how to handle.
"""
from __future__ import annotations

import asyncio
import random
import sys
import time
from typing import Optional
from urllib.parse import urlparse

from lib.fetch.browser import launch_stealth_context_async


_DEFAULT_NAV_TIMEOUT_MS = 30_000
# Hard ceiling on total time spent on a single URL inside the batch, over and
# above the navigation timeout. Bounds the otherwise-unbounded awaits
# (page.content(), route handling) so one stuck page can't hang asyncio.gather
# — and therefore the whole download step — forever.
_PER_URL_SLACK_MS = 40_000


def _plog(msg: str) -> None:
    """Per-URL progress line to stderr (flushed) so a hang is visible live."""
    sys.stderr.write(f"[pw] {msg}\n")
    sys.stderr.flush()
_BLOCKED_TYPES_DEFAULT = frozenset({"image", "media", "font"})

# Hosts whose pages do a JS-based redirect to the real article after DCL.
# When the post-DCL URL is still on one of these hosts, wait briefly for
# the URL to navigate away before snapshotting HTML.
_AGGREGATOR_HOSTS = frozenset({
    "news.google.com",
    "t.co",
    "lnkd.in",
    "link.medium.com",
    "l.facebook.com",
    "out.reddit.com",
})
_AGGREGATOR_REDIRECT_TIMEOUT_MS = 10_000

# Hosts whose article body is rendered by client-side JS hydration AFTER
# domcontentloaded fires. Snapshotting at DCL yields only nav chrome (~250
# chars); waiting for the `load` event gets the full hydrated DOM (~13K chars
# on a typical HackerNoon post). Bounded timeout so a long-polling resource
# can't hang the whole batch.
_HYDRATING_HOSTS = frozenset({
    "hackernoon.com",
})
_HYDRATION_TIMEOUT_MS = 8_000


def _is_aggregator(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return host in _AGGREGATOR_HOSTS


def _needs_hydration_wait(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    # Match bare domain plus any subdomain (e.g. www.hackernoon.com).
    if host in _HYDRATING_HOSTS:
        return True
    for h in _HYDRATING_HOSTS:
        if host.endswith("." + h):
            return True
    return False


async def _await_aggregator_redirect(page) -> None:
    """If the page is still on a known wrapper host, wait for the JS
    redirect to navigate away. Swallow timeout — caller will capture whatever
    HTML is present."""
    if not _is_aggregator(page.url):
        return
    try:
        await page.wait_for_url(
            lambda u: not _is_aggregator(u),
            timeout=_AGGREGATOR_REDIRECT_TIMEOUT_MS,
        )
    except Exception:
        pass


async def _await_hydration(page) -> None:
    """Wait for the `load` event on hosts where article body renders post-DCL
    (Next.js SSR + client hydration). Bounded — if `load` never fires we just
    capture whatever the DOM looks like after the timeout."""
    if not _needs_hydration_wait(page.url):
        return
    try:
        await page.wait_for_load_state("load", timeout=_HYDRATION_TIMEOUT_MS)
    except Exception:
        pass


async def _enable_fast_mode(page, blocked_types=_BLOCKED_TYPES_DEFAULT) -> None:
    """Abort heavy resources (images/fonts/media) to speed up page loads."""
    async def _handler(route):
        try:
            if route.request.resource_type in blocked_types:
                await route.abort()
            else:
                await route.continue_()
        except Exception:
            pass
    await page.route("**/*", _handler)


async def _scroll_to_bottom(page, scroll_div: str = "") -> None:
    if scroll_div:
        await page.evaluate(
            """
            const el = document.querySelector(%r);
            if (el) { el.scrollTop = el.scrollHeight; }
            else    { window.scrollTo(0, document.body.scrollHeight); }
            """ % scroll_div
        )
    else:
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight);")


async def fetch_url_html_async(
    url: str,
    timeout_ms: int = _DEFAULT_NAV_TIMEOUT_MS,
    *,
    scroll: int = 0,
    scroll_div: str = "",
    initial_sleep: float = 0.0,
    block_resources: bool = True,
) -> str:
    """Open firefox headless with stealth + persistent profile, return HTML.

    Args:
        url: page to load.
        timeout_ms: navigation timeout.
        scroll: number of scrollTo-bottom passes (for infinite-scroll SPAs).
        scroll_div: CSS selector of a scroll container; empty = window scroll.
        initial_sleep: seconds to wait after DCL before scrolling (jittered).
        block_resources: drop images/media/fonts to speed loads + reduce noise.
    """
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        ctx = await launch_stealth_context_async(p, headless=True)
        try:
            page = await ctx.new_page()
            if block_resources:
                await _enable_fast_mode(page)
            await page.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")
            await _await_aggregator_redirect(page)
            await _await_hydration(page)
            if initial_sleep > 0 or scroll > 0:
                await asyncio.sleep(initial_sleep + random.uniform(0.5, 1.5))
            for _ in range(scroll):
                await _scroll_to_bottom(page, scroll_div=scroll_div)
                await asyncio.sleep(random.uniform(1.0, 2.5))
            html = await page.content()
        finally:
            await ctx.close()
    return html


def fetch_url_html(
    url: str,
    timeout_ms: int = _DEFAULT_NAV_TIMEOUT_MS,
    *,
    scroll: int = 0,
    scroll_div: str = "",
    initial_sleep: float = 0.0,
    block_resources: bool = True,
) -> str:
    """Sync facade for fetch_url_html_async."""
    return asyncio.run(fetch_url_html_async(
        url, timeout_ms=timeout_ms,
        scroll=scroll, scroll_div=scroll_div,
        initial_sleep=initial_sleep, block_resources=block_resources,
    ))


async def fetch_urls_html_batch_async(
    urls: list[str],
    *,
    parallel: int = 8,
    timeout_ms: int = _DEFAULT_NAV_TIMEOUT_MS,
    block_resources: bool = True,
) -> dict[str, tuple[Optional[str], Optional[str], Optional[str]]]:
    """Fetch many URLs concurrently sharing one stealth context.

    Returns a mapping {original_url: (html, final_url, error)} where:
      - on success: html and final_url (page.url after navigation/redirects) set,
        error is None
      - on failure: html and final_url are None, error is a short message
    """
    from playwright.async_api import async_playwright

    if not urls:
        return {}

    results: dict[str, tuple[Optional[str], Optional[str], Optional[str]]] = {}
    sem = asyncio.Semaphore(max(1, parallel))
    ceiling_s = (timeout_ms + _PER_URL_SLACK_MS) / 1000.0
    total = len(urls)
    done = 0

    async def _fetch_one(u: str):
        """Inner fetch; raises on any Playwright error (caught by caller)."""
        page = await ctx.new_page()
        try:
            if block_resources:
                await _enable_fast_mode(page)
            await page.goto(u, timeout=timeout_ms, wait_until="domcontentloaded")
            await _await_aggregator_redirect(page)
            await _await_hydration(page)
            html = await page.content()
            return html, page.url
        finally:
            try:
                await page.close()
            except Exception:
                pass

    async with async_playwright() as p:
        ctx = await launch_stealth_context_async(p, headless=True)
        try:
            async def _one(u: str) -> None:
                nonlocal done
                async with sem:
                    _plog(f"→ {u}")
                    t0 = time.monotonic()
                    try:
                        # Hard per-URL ceiling: bounds page.content() and any
                        # other unbounded await so one stuck page can't hang
                        # the whole batch.
                        html, final_url = await asyncio.wait_for(
                            _fetch_one(u), timeout=ceiling_s
                        )
                        results[u] = (html, final_url, None)
                        done += 1
                        _plog(
                            f"✓ {u} ({len(html)} chars, "
                            f"{time.monotonic() - t0:.1f}s) [{done}/{total}]"
                        )
                    except asyncio.TimeoutError:
                        results[u] = (None, None, f"per-url timeout after {ceiling_s:.0f}s")
                        done += 1
                        _plog(
                            f"✗ TIMEOUT {u} after {time.monotonic() - t0:.1f}s "
                            f"[{done}/{total}]"
                        )
                    except Exception as exc:
                        results[u] = (None, None, str(exc)[:300])
                        done += 1
                        _plog(
                            f"✗ {u}: {str(exc)[:120]} "
                            f"({time.monotonic() - t0:.1f}s) [{done}/{total}]"
                        )
            await asyncio.gather(*(_one(u) for u in urls))
        finally:
            try:
                await asyncio.wait_for(ctx.close(), timeout=30.0)
            except Exception:
                pass
    return results


def fetch_urls_html_batch(
    urls: list[str],
    *,
    parallel: int = 8,
    timeout_ms: int = _DEFAULT_NAV_TIMEOUT_MS,
    block_resources: bool = True,
) -> dict[str, tuple[Optional[str], Optional[str], Optional[str]]]:
    """Sync facade for fetch_urls_html_batch_async."""
    return asyncio.run(fetch_urls_html_batch_async(
        urls, parallel=parallel, timeout_ms=timeout_ms,
        block_resources=block_resources,
    ))
