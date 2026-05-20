"""Interactive Playwright login helper.

Opens a headed Firefox window backed by the same stealth-configured
persistent profile (see lib/fetch/browser.py) that the gather/download
steps will use. Log in to any sites you want authenticated while
gathering, then close the window. Cookies/session data are saved to the
profile directory and picked up on subsequent headless runs.

Usage:
    .venv/bin/python scripts/playwright_login.py                 # opens Feedly
    .venv/bin/python scripts/playwright_login.py https://...     # opens given URL
"""
from __future__ import annotations

import sys

from playwright.sync_api import sync_playwright

from lib.fetch.browser import launch_stealth_context_sync, profile_dir


def main(start_url: str = "https://feedly.com/i/login") -> int:
    profile = profile_dir()
    print(f"Profile dir: {profile}")
    print(f"Opening: {start_url}")
    print("Log in manually, then close the browser window to save the session.")

    with sync_playwright() as p:
        ctx = launch_stealth_context_sync(p, headless=False)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(start_url)
        try:
            page.wait_for_event("close", timeout=0)
        except Exception:
            pass
        finally:
            ctx.close()

    print(f"Session saved to {profile}")
    return 0


if __name__ == "__main__":
    url = sys.argv[1] if len(sys.argv) > 1 else "https://feedly.com/i/login"
    sys.exit(main(url))
