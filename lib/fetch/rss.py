"""RSS feed fetcher.

Ported semantics from legacy ~/projects/OpenAIAgentsSDK/fetch.py:142 (Fetcher.fetch_rss).
Uses synchronous httpx + feedparser; caps at 50 entries per feed.
"""
from __future__ import annotations

import httpx
import feedparser

from lib.fetch.types import Article, FetchResult


_MAX_ENTRIES = 50
_TIMEOUT = httpx.Timeout(15.0, connect=5.0)


def fetch_rss(source: str, rss_url: str) -> FetchResult:
    """Fetch and parse an RSS feed.

    Args:
        source: Source name (top-level key from sources.yaml).
        rss_url: Feed URL.

    Returns:
        FetchResult with up to 50 Article entries.
    """
    try:
        with httpx.Client(timeout=_TIMEOUT, follow_redirects=True) as client:
            resp = client.get(rss_url)
    except httpx.HTTPError as exc:
        return FetchResult(source=source, ok=False, error=f"HTTP error: {exc}")

    if resp.status_code != 200:
        return FetchResult(
            source=source, ok=False,
            error=f"HTTP {resp.status_code} from {rss_url}",
        )

    feed = feedparser.parse(resp.content)
    articles: list[Article] = []
    for entry in feed.entries[:_MAX_ENTRIES]:
        title = entry.get("title", "")
        if not title:
            continue
        url = entry.get("link", "")
        if not url:
            continue
        articles.append(Article(
            source=source,
            title=title,
            url=url,
            published=entry.get("published") or None,
            rss_summary=entry.get("summary") or entry.get("description") or None,
        ))

    return FetchResult(source=source, articles=articles, ok=True)
