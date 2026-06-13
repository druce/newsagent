"""OG metadata fetcher — fetch og:title/description/image/url from a URL.

Best-effort: missing tags are absent keys, HTTP errors return {}.

Paywalled / bot-blocked hosts (WSJ, Bloomberg, FT, …) reject a direct fetch
with a 401/403, so a direct parse yields nothing. For those we fall back to
CardyB (`cardyb.bsky.app`), Bluesky's own link-card extractor — it has a
crawler publishers allow and re-hosts the preview image at a downloadable URL,
which is exactly what produces a rich image card instead of a bare text card.
"""
from __future__ import annotations

import httpx
from bs4 import BeautifulSoup

_TIMEOUT = 10.0
_CARDYB_ENDPOINT = "https://cardyb.bsky.app/v1/extract"


def _get_og_tags_direct(url: str, timeout: float) -> dict:
    """Fetch URL and parse <meta property="og:..."> tags. {} on HTTP error."""
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


def _get_og_tags_cardyb(url: str, timeout: float) -> dict:
    """Resolve metadata via Bluesky's CardyB extractor. {} on any failure."""
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            resp = client.get(_CARDYB_ENDPOINT, params={"url": url})
            resp.raise_for_status()
            data = resp.json()
    except Exception:
        return {}

    # CardyB returns empty strings for fields it could not resolve; keep only
    # the populated ones so callers can treat absence uniformly.
    return {k: data[k] for k in ("title", "description", "image", "url")
            if isinstance(data, dict) and data.get(k)}


def get_og_tags(url: str, timeout: float = _TIMEOUT) -> dict:
    """Fetch OG metadata for `url` as a {title, description, image, url} dict.

    Tries a direct fetch first (gives the publisher's own OG image for normal
    sites); when that yields no image — e.g. a paywalled host blocked the
    request — falls back to CardyB. Best-effort: returns {} if neither source
    produces anything.
    """
    direct = _get_og_tags_direct(url, timeout)
    if direct.get("image"):
        return direct

    cardyb = _get_og_tags_cardyb(url, timeout)
    if cardyb.get("image"):
        return cardyb

    # Neither had an image; prefer whichever carried any metadata at all.
    return direct or cardyb
