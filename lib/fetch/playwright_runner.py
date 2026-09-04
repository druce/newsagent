"""Thin Playwright wrapper for fetching rendered HTML.

Uses a stealth-configured Firefox persistent context (see lib/fetch/browser.py)
so SSO flows, DataDome-protected sites, and other automation-detecting pages
treat us like a returning human user. Supports SPA-friendly options
(`scroll`, `scroll_div`, `initial_sleep`, `scroll_wait`, `scroll_time_budget`,
`block_resources`) for sites like Feedly that infinite-scroll. Scrolling is a
convergence loop (`_scroll_until_settled`): after each scroll-to-bottom it
polls the in-page link count until it grows, nudges + retries when stalled,
and stops at `scroll` pages, end-of-feed, or the time budget — a fixed count
of blind scrolls raced the lazy loader and intermittently captured only the
first page (Feedly 2026-09-01 / 09-04: 40 of ~250 entries). Failure modes
(timeout, no network, etc.) propagate as Exceptions; callers decide how to
handle.
"""
from __future__ import annotations

import asyncio
import random
import sys
import time
from dataclasses import dataclass
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


# Indirections so tests can substitute a deterministic clock / instant sleep /
# fixed jitter without monkeypatching asyncio or time globally (asyncio's own
# event loop uses time.monotonic).
_now = time.monotonic
_sleep = asyncio.sleep
_uniform = random.uniform

# The selector is passed as an evaluate() ARGUMENT, never interpolated into
# the JS text (the old `%r` template broke on quotes/backslashes).
_JS_SCROLL = """([sel, nudge]) => {
  const el = sel ? document.querySelector(sel) : null;
  const t = el || document.scrollingElement || document.body;
  if (nudge) { t.scrollTop = Math.max(0, t.scrollTop - 400); }
  t.scrollTop = t.scrollHeight;
  if (!el) window.scrollTo(0, document.body.scrollHeight);
  return !!el;
}"""
_JS_METRIC = """(sel) => {
  const root = (sel && document.querySelector(sel)) || document;
  return root.querySelectorAll('a[href]').length;
}"""
_SCROLL_DIV_WAIT_MS = 10_000
_POLL_INTERVAL_S = 0.5


@dataclass
class ScrollStats:
    pages: int      # initial page + scroll passes that produced growth
    links: int      # final in-container <a href> count
    stopped: str    # "max_pages" | "end_of_feed" | "time_budget" | "no_scroll"


async def _scroll_to_bottom(page, scroll_div: str = "", *, nudge: bool = False) -> bool:
    """Scroll the container (or window) to the bottom. `nudge` first backs up
    ~400px so a container already sitting at the bottom re-fires its scroll
    event (re-assigning an unchanged scrollTop fires nothing). Returns whether
    `scroll_div` matched an element."""
    return bool(await page.evaluate(_JS_SCROLL, [scroll_div, nudge]))


async def _count_links(page, scroll_div: str = "") -> int:
    try:
        return int(await page.evaluate(_JS_METRIC, scroll_div))
    except Exception:
        return -1


async def _wait_for_growth(page, scroll_div: str, baseline: int,
                           scroll_wait: float, deadline: float) -> int:
    """Poll the link count until it exceeds `baseline`, `scroll_wait` seconds
    elapse, or `deadline` passes. Returns the last count seen (>= baseline)."""
    t_end = min(_now() + scroll_wait, deadline)
    last = baseline
    while True:
        n = await _count_links(page, scroll_div)
        if n > baseline:
            return n
        if n >= 0:
            last = max(last, n)
        if _now() >= t_end:
            return last
        await _sleep(_POLL_INTERVAL_S)


async def _scroll_until_settled(
    page,
    *,
    scroll_div: str,
    max_pages: int,
    scroll_wait: float,
    initial_sleep: float,
    time_budget: float,
    max_retries: int = 2,
) -> ScrollStats:
    """Convergence loop for infinite-scroll pages. `max_pages` is the `scroll`
    config key: the cap on pages collected (initial page counts as 1)."""
    if max_pages <= 0 and initial_sleep <= 0:
        return ScrollStats(pages=1, links=0, stopped="no_scroll")
    t0 = _now()
    deadline = t0 + time_budget
    # Blind minimum settle, as before (Feedly's SPA needs ~20s to auth+render)…
    await _sleep(initial_sleep + _uniform(0.5, 1.5))
    # …then a bounded wait for the container so a slow frame doesn't silently
    # degrade into scrolling the window.
    if scroll_div:
        try:
            await page.wait_for_selector(scroll_div, timeout=_SCROLL_DIV_WAIT_MS)
        except Exception:
            _plog(f"scroll: {scroll_div!r} not found after {_SCROLL_DIV_WAIT_MS // 1000}s; window fallback")
    before = await _wait_for_growth(page, scroll_div, 0, scroll_wait, deadline)
    pages = 1
    stopped = "max_pages"
    while pages < max_pages:
        if _now() >= deadline:
            stopped = "time_budget"
            break
        after = before
        grew = False
        for attempt in range(max_retries + 1):
            await _scroll_to_bottom(page, scroll_div, nudge=attempt > 0)
            after = await _wait_for_growth(page, scroll_div, before, scroll_wait, deadline)
            if after > before:
                grew = True
                break
            _plog(f"scroll {pages}/{max_pages}: no growth ({before} links), "
                  f"nudge retry {attempt + 1}/{max_retries}")
            if _now() >= deadline:
                break
        if not grew:
            stopped = "time_budget" if _now() >= deadline else "end_of_feed"
            break
        pages += 1
        _plog(f"scroll {pages}/{max_pages}: {before} -> {after} links (+{after - before})")
        before = after
        await _sleep(_uniform(0.3, 0.8))
    _plog(f"scroll done: {pages} pages, {before} links, {stopped}, {_now() - t0:.1f}s")
    return ScrollStats(pages=pages, links=before, stopped=stopped)


async def fetch_url_html_async(
    url: str,
    timeout_ms: int = _DEFAULT_NAV_TIMEOUT_MS,
    *,
    scroll: int = 0,
    scroll_div: str = "",
    initial_sleep: float = 0.0,
    block_resources: bool = True,
    scroll_wait: float = 8.0,
    scroll_time_budget: float = 180.0,
) -> str:
    """Open firefox headless with stealth + persistent profile, return HTML.

    Args:
        url: page to load.
        timeout_ms: navigation timeout.
        scroll: max pages to collect via scroll-to-bottom (infinite-scroll SPAs);
            the loop stops early at end-of-feed.
        scroll_div: CSS selector of a scroll container; empty = window scroll.
        initial_sleep: seconds to wait after DCL before scrolling (jittered).
        block_resources: drop images/media/fonts to speed loads + reduce noise.
        scroll_wait: seconds to wait for new links after each scroll pass.
        scroll_time_budget: hard ceiling on total scrolling time.
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
            await _scroll_until_settled(
                page, scroll_div=scroll_div, max_pages=scroll,
                scroll_wait=scroll_wait, initial_sleep=initial_sleep,
                time_budget=scroll_time_budget,
            )
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
    scroll_wait: float = 8.0,
    scroll_time_budget: float = 180.0,
) -> str:
    """Sync facade for fetch_url_html_async."""
    return asyncio.run(fetch_url_html_async(
        url, timeout_ms=timeout_ms,
        scroll=scroll, scroll_div=scroll_div,
        initial_sleep=initial_sleep, block_resources=block_resources,
        scroll_wait=scroll_wait, scroll_time_budget=scroll_time_budget,
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
