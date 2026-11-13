"""Per-batch auto-login for subscription sites whose login doesn't survive a
browser restart.

Most logins persist in the stealth Firefox profile (scripts/playwright_login.py).
Some sites keep the session in browser-session cookies (no expiry), which
Firefox drops when the context closes, so every headless download run starts
logged out. Digitimes is one: its ASPSESSIONID/PHPSESSID are session-only, and
articles come back as the ~250-char "requires paid subscription" teaser.

For those sites, `fetch_urls_html_batch_async` calls `perform_logins` once on
the shared context before fetching, only when the batch contains a URL on a
registered domain. Credentials come from .env; when they are missing the login
is skipped with a warning and the fetch proceeds as before. Login failures
never abort the batch.
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from typing import Any, Awaitable, Callable
from urllib.parse import urlparse


_LOGIN_TIMEOUT_MS = 30_000

DIGITIMES_LOGIN_URL = "https://www.digitimes.com/newregister/join.asp"


def _log(msg: str) -> None:
    sys.stderr.write(f"[login] {msg}\n")
    sys.stderr.flush()


async def _login_digitimes(ctx: Any, user: str, password: str) -> bool:
    """Sign in via join.asp (email + password on one form). Success = the POST
    redirects away from the login page (to the homepage)."""
    page = await ctx.new_page()
    try:
        await page.goto(DIGITIMES_LOGIN_URL, timeout=_LOGIN_TIMEOUT_MS,
                        wait_until="domcontentloaded")
        await page.fill("input[name=EMail]", user)
        await page.fill("input[name=Password]", password)
        await page.click("button[name=Submit]:has-text('Sign in')",
                         timeout=_LOGIN_TIMEOUT_MS)
        try:
            await page.wait_for_load_state("domcontentloaded",
                                           timeout=_LOGIN_TIMEOUT_MS)
        except Exception:
            pass
        await page.wait_for_timeout(2000)
        return "join.asp" not in page.url and "signin" not in page.url
    finally:
        try:
            await page.close()
        except Exception:
            pass


@dataclass(frozen=True)
class SiteLogin:
    domain: str
    user_env: str
    password_env: str
    login: Callable[[Any, str, str], Awaitable[bool]]

    def matches(self, url: str) -> bool:
        host = (urlparse(url).hostname or "").lower()
        return host == self.domain or host.endswith("." + self.domain)


SITE_LOGINS: tuple[SiteLogin, ...] = (
    SiteLogin("digitimes.com", "DIGITIMES_EMAIL", "DIGITIMES_PASSWORD",
              _login_digitimes),
)


def logins_for(urls: list[str]) -> list[SiteLogin]:
    """Registered logins needed for this batch of URLs (each at most once)."""
    return [s for s in SITE_LOGINS if any(s.matches(u) for u in urls)]


async def perform_logins(ctx: Any, urls: list[str]) -> list[str]:
    """Log `ctx` in to every registered site this batch touches.

    Returns the domains that logged in successfully. Never raises.
    """
    done: list[str] = []
    for site in logins_for(urls):
        user = os.environ.get(site.user_env, "")
        password = os.environ.get(site.password_env, "")
        if not user or not password:
            _log(f"{site.domain}: {site.user_env}/{site.password_env} not set; "
                 f"skipping login (expect paywalled extracts)")
            continue
        try:
            ok = await site.login(ctx, user, password)
        except Exception as exc:
            _log(f"{site.domain}: login error: {str(exc)[:120]}")
            continue
        if ok:
            _log(f"{site.domain}: logged in")
            done.append(site.domain)
        else:
            _log(f"{site.domain}: login failed (still on login page; "
                 f"check credentials)")
    return done
