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
from typing import Optional

from lib.fetch.browser import launch_stealth_context_async


_DEFAULT_NAV_TIMEOUT_MS = 30_000
_BLOCKED_TYPES_DEFAULT = frozenset({"image", "media", "font"})


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

    async with async_playwright() as p:
        ctx = await launch_stealth_context_async(p, headless=True)
        try:
            async def _one(u: str) -> None:
                async with sem:
                    page = await ctx.new_page()
                    try:
                        if block_resources:
                            await _enable_fast_mode(page)
                        await page.goto(u, timeout=timeout_ms, wait_until="domcontentloaded")
                        html = await page.content()
                        results[u] = (html, page.url, None)
                    except Exception as exc:
                        results[u] = (None, None, str(exc)[:300])
                    finally:
                        try:
                            await page.close()
                        except Exception:
                            pass
            await asyncio.gather(*(_one(u) for u in urls))
        finally:
            await ctx.close()
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
