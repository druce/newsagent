"""OG metadata fetcher — fetch og:title/description/image/url from a URL.

Best-effort: missing tags are absent keys, HTTP errors return {}.
"""
from __future__ import annotations

import httpx
from bs4 import BeautifulSoup

_TIMEOUT = 10.0


def get_og_tags(url: str, timeout: float = _TIMEOUT) -> dict:
    """Fetch URL, parse <meta property="og:..."> tags.

    Returns {title, description, image, url} dict.
    Best-effort — missing tags are absent keys. Returns {} on HTTP error.
    """
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            resp = client.get(url)
            resp.raise_for_status()
    except Exception:
        return {}

    soup = BeautifulSoup(resp.text, "html.parser")
    result: dict = {}
    for tag in soup.find_all("meta"):
        prop = tag.get("property", "")
        content = tag.get("content", "")
        if prop == "og:title" and content:
            result["title"] = content
        elif prop == "og:description" and content:
            result["description"] = content
        elif prop == "og:image" and content:
            result["image"] = content
        elif prop == "og:url" and content:
            result["url"] = content

    return result
