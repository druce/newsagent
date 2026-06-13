"""Interactive page-capture helper.

Opens a headed Firefox window backed by the same stealth-configured
persistent profile (see lib/fetch/browser.py) as gather/download. Navigate
/ log in / clear any bot-wall, then CLOSE the browser window. The last
successfully rendered HTML is written to the given output path so gather
can read it in --cached-pages mode.

Usage:
    .venv/bin/python scripts/capture_page.py <url> <output.html>
"""
from __future__ import annotations

import sys
import time

from playwright.sync_api import sync_playwright

from lib.fetch.browser import launch_stealth_context_sync, profile_dir


def main(url: str, out_path: str) -> int:
    print(f"Profile dir: {profile_dir()}")
    print(f"Opening: {url}")
    print(f"Will save rendered HTML to: {out_path}")
    print("Log in / clear any wall, navigate to the headlines page,")
    print("then CLOSE the browser window to capture the final HTML.")

    last_html = ""
    with sync_playwright() as p:
        ctx = launch_stealth_context_sync(p, headless=False)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(url)
        # Poll the live page; keep the most recent good HTML. The loop ends
        # when the window/page is closed (content() raises).
        while True:
            try:
                html = page.content()
                if html:
                    last_html = html
                time.sleep(2)
            except Exception:
                break
        try:
            ctx.close()
        except Exception:
            pass

    if not last_html:
        print("ERROR: captured empty HTML.")
        return 1
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(last_html)
    print(f"Saved {len(last_html)} bytes to {out_path}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(2)
    sys.exit(main(sys.argv[1], sys.argv[2]))
