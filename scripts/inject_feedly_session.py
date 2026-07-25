"""Inject a Feedly session token into the Playwright profile.

Feedly's web app stores its auth in localStorage under "feedly.session"
(a JSON blob with feedlyToken / feedlyRefreshToken). Google OAuth refuses
to run inside the automated browser ("this browser may not be secure"),
so log in with vanilla Chrome instead and transplant the session:

  1. In Chrome, log in at https://feedly.com
  2. DevTools console on a feedly.com tab:
       copy(localStorage.getItem('feedly.session'))
  3. Save the clipboard to a file:  pbpaste > feedly_session.json
  4. .venv/bin/python scripts/inject_feedly_session.py feedly_session.json

The script opens the same stealth persistent profile gather uses, writes
the value into localStorage on the feedly.com origin, then reloads the
logged-in app to verify the session took.

Usage:
    .venv/bin/python scripts/inject_feedly_session.py [feedly_session.json]
"""
from __future__ import annotations

import json
import sys

from playwright.sync_api import sync_playwright

from lib.fetch.browser import launch_stealth_context_sync, profile_dir


def main(token_path: str = "feedly_session.json") -> int:
    try:
        raw = open(token_path, encoding="utf-8").read().strip()
    except OSError as exc:
        print(f"ERROR: cannot read {token_path}: {exc}")
        return 1

    try:
        session = json.loads(raw)
    except json.JSONDecodeError as exc:
        print(f"ERROR: {token_path} is not valid JSON: {exc}")
        return 1
    if not isinstance(session, dict) or "feedlyToken" not in session:
        print("ERROR: JSON has no 'feedlyToken' key — copy the exact value of "
              "localStorage.getItem('feedly.session') from a logged-in feedly.com tab.")
        return 1

    print(f"Profile dir: {profile_dir()}")
    with sync_playwright() as p:
        ctx = launch_stealth_context_sync(p, headless=True)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto("https://feedly.com", wait_until="domcontentloaded")
        page.evaluate(
            "value => localStorage.setItem('feedly.session', value)", raw
        )
        # Reload the app and confirm it boots into a logged-in session
        # instead of bouncing to the marketing homepage.
        page.goto("https://feedly.com/i/my", wait_until="domcontentloaded")
        page.wait_for_timeout(8000)
        final_url = page.url
        still_set = page.evaluate(
            "() => !!localStorage.getItem('feedly.session')"
        )
        ctx.close()

    if not still_set or "/i/" not in final_url:
        print(f"ERROR: session did not take (landed on {final_url}). "
              "The token may be stale — re-copy it from a fresh Chrome tab.")
        return 1
    print(f"OK: logged-in session saved (landed on {final_url}).")
    print(f"Delete {token_path} now that it's injected.")
    return 0


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else "feedly_session.json"
    sys.exit(main(path))
