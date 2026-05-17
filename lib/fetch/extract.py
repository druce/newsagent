"""Extract article links from a landing-page HTML document.

Ported from legacy ~/projects/OpenAIAgentsSDK/scrape.py:1144 (parse_source_file)
with one change: takes HTML content directly instead of reading from disk.
"""
from __future__ import annotations

import re
from typing import Any
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from lib.fetch.types import Article


_DEFAULT_MINLENGTH = 28


def _clean_url(url: str) -> str:
    """Strip fragments/empty queries; minimal normalization."""
    return url.split("#")[0]


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
        absolute = _clean_url(absolute)
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

    # Dedup within this page by URL, preserving order
    seen: set[str] = set()
    unique: list[Article] = []
    for a in candidates:
        if a.url not in seen:
            seen.add(a.url)
            unique.append(a)
    return unique
