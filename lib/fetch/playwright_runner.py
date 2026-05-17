"""Thin Playwright wrapper for fetching rendered HTML.

For Phase 2 we don't port the full legacy scrape.py — no rate limiting, no
fingerprinting, no stealth scripts. Just open chromium, navigate, return HTML.
Failure modes (timeout, no network, etc.) propagate as Exceptions; callers
decide how to handle.
"""
from __future__ import annotations

import asyncio


_DEFAULT_NAV_TIMEOUT_MS = 30_000


async def fetch_url_html_async(url: str, timeout_ms: int = _DEFAULT_NAV_TIMEOUT_MS) -> str:
    """Open chromium headless, navigate to URL, return rendered HTML body."""
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        try:
            context = await browser.new_context()
            page = await context.new_page()
            await page.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")
            html = await page.content()
        finally:
            await browser.close()
    return html


def fetch_url_html(url: str, timeout_ms: int = _DEFAULT_NAV_TIMEOUT_MS) -> str:
    """Sync facade for fetch_url_html_async."""
    return asyncio.run(fetch_url_html_async(url, timeout_ms=timeout_ms))
