"""Stealth-configured Playwright Firefox context.

Ports the anti-bot setup from the legacy scraper (scrape.py) so SSO flows
and DataDome-protected sites don't immediately flag us as automation:

  - playwright-stealth patches (navigator.webdriver, plugins, webgl, etc.)
  - User-Agent built from the *actually-bundled* Firefox version so the JS-
    side internals (rv:NNN) match the UA header
  - ignore_default_args=["--enable-automation"] to drop Playwright's flag
  - Randomized viewport / DPR / timezone / locale / color scheme per launch
  - Realistic Accept / Sec-Fetch-* / DNT headers
  - Persistent profile at $FIREFOX_PROFILE_PATH (falls back to
    ./.playwright-profile) so cookies and logged-in sessions survive
  - DataDome cookie purge before launch (WSJ etc. issue a long-lived block
    cookie when they flag a session)
  - Firefox prefs that open new windows as tabs (SSO popups)

Both the headless fetcher and the headed login helper go through here so
fingerprints stay consistent between login and gather.
"""
from __future__ import annotations

import logging
import os
import random
import sqlite3
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


def profile_dir() -> Path:
    """Resolve the Firefox user-data dir.

    Precedence: $FIREFOX_PROFILE_PATH → ./.playwright-profile. Always
    returns a Path; the caller is responsible for creating it if needed.
    """
    override = os.environ.get("FIREFOX_PROFILE_PATH")
    if override:
        return Path(override).expanduser()
    return Path.cwd() / ".playwright-profile"


def _viewport_and_dpr() -> tuple[dict[str, int], float]:
    viewport = random.choice([
        {"width": 1920, "height": 1080},
        {"width": 1366, "height": 768},
        {"width": 1440, "height": 900},
        {"width": 1536, "height": 864},
        {"width": 1280, "height": 720},
    ])
    dpr = random.choice([1, 1.25, 1.5, 1.75, 2])
    return viewport, float(dpr)


def _extra_headers(locale: str) -> dict[str, str]:
    lang = locale.split("-")[0]
    return {
        "Accept-Language": f"{lang},{locale};q=0.9",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "DNT": "1" if random.choice([True, False]) else "0",
    }


def _firefox_user_agent(p: Any, fallback: str = "141.0") -> str:
    """Build a Firefox-on-macOS UA that matches the actually-bundled binary.

    Firefox freezes the Mac platform token to "Macintosh; Intel Mac OS X
    10.15" regardless of CPU/OS for fingerprint resistance, so we hardcode
    that part. The major version is read from application.ini inside
    Playwright's bundled Firefox so the rv: token matches what the JS
    engine actually reports.
    """
    version = _detect_firefox_version(p) or fallback
    major = version.split(".")[0]
    rv = f"{major}.0"
    return (
        f"Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:{rv}) "
        f"Gecko/20100101 Firefox/{rv}"
    )


_version_cache: Optional[str] = None


def _detect_firefox_version(p: Any) -> Optional[str]:
    global _version_cache
    if _version_cache is not None:
        return _version_cache
    try:
        exe = Path(p.firefox.executable_path)
    except Exception:
        return None
    candidates = [
        exe.parent.parent / "Resources" / "application.ini",  # macOS .app
        exe.parent / "application.ini",                        # Linux/Windows
    ]
    ini = next((c for c in candidates if c.exists()), None)
    if ini is None:
        return None
    try:
        for line in ini.read_text().splitlines():
            if line.startswith("Version="):
                _version_cache = line.split("=", 1)[1].strip()
                return _version_cache
    except OSError:
        pass
    return None


def _purge_datadome_cookies(profile: Path) -> None:
    """Drop datadome anti-bot cookies before launch (Firefox locks the DB)."""
    cookies = profile / "cookies.sqlite"
    if not cookies.exists():
        return
    try:
        conn = sqlite3.connect(str(cookies), timeout=5)
        try:
            n = conn.execute("DELETE FROM moz_cookies WHERE name = 'datadome'").rowcount
            conn.commit()
            if n:
                logger.info("Purged %d datadome cookie(s) from Firefox profile", n)
        finally:
            conn.close()
    except sqlite3.Error as exc:
        logger.warning("Could not purge datadome cookies: %s", exc)


def _prepare_profile(profile: Path) -> None:
    """Make the profile dir, and remove compatibility.ini to prevent "newer
    profile" errors when Playwright's bundled Firefox version changes."""
    profile.mkdir(parents=True, exist_ok=True)
    compat = profile / "compatibility.ini"
    if compat.exists():
        try:
            compat.unlink()
        except OSError:
            pass
    _purge_datadome_cookies(profile)


def _launch_kwargs(p: Any, headless: bool) -> dict[str, Any]:
    viewport, dpr = _viewport_and_dpr()
    timezone_id = random.choice([
        "America/New_York", "Europe/London", "Europe/Paris",
        "Asia/Tokyo", "Australia/Sydney", "America/Los_Angeles",
    ])
    color_scheme = random.choice(["light", "dark", "no-preference"])
    locale = random.choice(["en-US", "en-GB"])
    return dict(
        headless=headless,
        viewport=viewport,
        device_scale_factor=dpr,
        timezone_id=timezone_id,
        color_scheme=color_scheme,
        locale=locale,
        extra_http_headers=_extra_headers(locale),
        ignore_default_args=["--enable-automation"],
        args=["--disable-gpu", "--no-sandbox", "--disable-dev-shm-usage"],
        firefox_user_prefs={
            "browser.link.open_newwindow": 3,            # new windows as tabs
            "browser.link.open_newwindow.restriction": 0,
            "browser.tabs.loadInBackground": False,
        },
        user_agent=_firefox_user_agent(p),
        accept_downloads=True,
    )


async def launch_stealth_context_async(p: Any, headless: bool = True) -> Any:
    """Async: open a persistent, stealth-configured Firefox context."""
    from playwright_stealth import Stealth

    profile = profile_dir()
    _prepare_profile(profile)
    ctx = await p.firefox.launch_persistent_context(
        user_data_dir=str(profile),
        **_launch_kwargs(p, headless=headless),
    )
    page = await ctx.new_page()
    try:
        await Stealth().apply_stealth_async(page)
    finally:
        await page.close()
    return ctx


def launch_stealth_context_sync(p: Any, headless: bool = False) -> Any:
    """Sync: open a persistent, stealth-configured Firefox context.

    Headed by default — this is what the interactive login helper uses.
    """
    from playwright_stealth import Stealth

    profile = profile_dir()
    _prepare_profile(profile)
    ctx = p.firefox.launch_persistent_context(
        user_data_dir=str(profile),
        **_launch_kwargs(p, headless=headless),
    )
    page = ctx.new_page()
    try:
        Stealth().apply_stealth_sync(page)
    finally:
        page.close()
    return ctx
