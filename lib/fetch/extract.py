"""Extract article links from a landing-page HTML document.

Ported from legacy ~/projects/OpenAIAgentsSDK/scrape.py:1144 (parse_source_file)
with one change: takes HTML content directly instead of reading from disk.
"""
from __future__ import annotations

import json
import re
from typing import Any, Optional
from urllib.parse import urljoin, urlparse

import trafilatura
from bs4 import BeautifulSoup

from lib.fetch.types import Article
from lib.utilities import clean_url


_DEFAULT_MINLENGTH = 28

_NEXT_DATA_RE = re.compile(
    r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
    re.DOTALL,
)


def article_body_from_next_data(html: str) -> Optional[str]:
    """Return ``props.pageProps.data.articleBody`` from a Next.js page, if present.

    Some sites (notably HackerNoon) render article prose only inside the
    ``__NEXT_DATA__`` JSON payload rather than as server-side HTML, so
    trafilatura sees only author-bio boilerplate. This recovers the real body.
    Returns None when the blob is missing, unparseable, or has no articleBody.
    """
    m = _NEXT_DATA_RE.search(html or "")
    if not m:
        return None
    try:
        data = json.loads(m.group(1))
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    body = (
        ((data.get("props") or {}).get("pageProps") or {}).get("data") or {}
    ).get("articleBody")
    if isinstance(body, str) and body.strip():
        return body
    return None


def extract_article_text(html: str) -> Optional[str]:
    """Extract article body text from a page's HTML.

    Tries trafilatura first, then falls back to the ``__NEXT_DATA__``
    ``articleBody`` (for JS-rendered sites like HackerNoon). Returns whichever
    source yields more text, so server-rendered pages keep their trafilatura
    extract while JS-only pages recover their real body.
    """
    traf = trafilatura.extract(html, include_comments=False, include_tables=False)
    next_data = article_body_from_next_data(html)
    if next_data and len(next_data) > len(traf or ""):
        return next_data
    return traf


def extract_article_links(
    html: str,
    source_cfg: dict[str, Any],
    source_name: str,
) -> list[Article]:
    """Parse landing-page HTML and return article links matching the source's include/exclude rules."""
    minlength = source_cfg.get("minlength") or _DEFAULT_MINLENGTH
    exclude: list[str] = source_cfg.get("exclude") or []
    include: list[str] = source_cfg.get("include") or []
    base_page_url = source_cfg.get("url", "")

    soup = BeautifulSoup(html, "html.parser")

    base_tag = soup.find("base")
    base_href = base_tag.get("href") if base_tag else None
    base_url: str = str(base_href) if base_href else base_page_url

    raw_links = soup.find_all("a")
    candidates: list[Article] = []
    for link in raw_links:
        text = link.get_text(strip=True)
        href_val = link.get("href")
        href: str = str(href_val) if href_val else ""
        if not text or not href:
            continue
        if re.match(r"^\d+$", text):  # bare numbers (comment counts on Ars, etc.)
            continue
        absolute = urljoin(base_url, href)
        absolute = clean_url(absolute)
        if not absolute.startswith("http"):  # drop javascript:, mailto:, etc.
            continue
        path = urlparse(absolute).path
        if len(path) <= 1:  # drop bare-domain links
            continue
        if len(text) < minlength:
            continue
        if exclude and any(re.match(p, absolute) for p in exclude):
            continue
        if include and not any(re.match(p, absolute) for p in include):
            continue
        candidates.append(Article(
            source=source_name,
            title=text,
            url=absolute,
        ))

    # Dedup within this page by URL. When the same href appears in multiple
    # <a> tags (common pattern: one anchor wraps a hero <img>+<figcaption>,
    # another wraps the actual headline text), keep the one with the LONGEST
    # title — the headline is almost always longer than image-caption strings
    # like "Credit: VentureBeat made with Midjourney".
    best: dict[str, Article] = {}
    order: list[str] = []
    for a in candidates:
        existing = best.get(a.url)
        if existing is None:
            best[a.url] = a
            order.append(a.url)
        elif len(a.title) > len(existing.title):
            best[a.url] = a
    return [best[u] for u in order]
