"""Map article URLs to human-friendly source names.

Articles gathered from aggregators (Feedly, NewsAPI, Techmeme) carry the
aggregator name as their `source` field — but for display we want the
underlying publisher (e.g. "Reuters", "TechCrunch") derived from the
article's final URL.

Lookup order:
  1. SQLite `sites.name` row (DB is the single source of truth; ~4k+ domains
     seeded from the legacy newsletter_agent.db migration).
     Tries the exact bare domain first, then strips leading subdomains.
  2. Bare domain fallback (e.g. "calcalistech.com") if no row matches.

Articles gathered directly from a configured non-aggregator source
(e.g. Ars Technica, The Verge) already have the correct pretty name in
`source`; we don't override those.
"""
from __future__ import annotations

import sqlite3
from functools import lru_cache
from typing import Optional
from urllib.parse import urlparse


# Aggregator source names from sources.yaml whose articles should be
# re-attributed to the underlying publisher by URL.
_AGGREGATORS: set[str] = {"Feedly AI", "NewsAPI", "Techmeme"}


def _bare_domain(url: str | None) -> str:
    """Return the lowercase netloc minus a leading 'www.', or '' on parse failure."""
    if not url:
        return ""
    try:
        netloc = urlparse(url).netloc.lower()
    except (ValueError, TypeError):
        return ""
    if netloc.startswith("www."):
        netloc = netloc[4:]
    return netloc


def _candidate_domains(domain: str) -> list[str]:
    """Yield lookup candidates for a domain, most-specific first.

    e.g. 'blogs.nvidia.com' -> ['blogs.nvidia.com', 'nvidia.com']
         'finance.yahoo.com' -> ['finance.yahoo.com', 'yahoo.com']
         'news.bbc.co.uk'    -> ['news.bbc.co.uk', 'bbc.co.uk', 'co.uk']
                                (last is harmless — no .co.uk row in DB)
    """
    if not domain:
        return []
    parts = domain.split(".")
    out: list[str] = [domain]
    for i in range(1, len(parts) - 1):
        out.append(".".join(parts[i:]))
    return out


@lru_cache(maxsize=1)
def _load_db_map(db_path: str) -> dict[str, str]:
    """Return all domain -> name pairs from sites table. Cached per path."""
    try:
        with sqlite3.connect(db_path) as conn:
            rows = conn.execute(
                "SELECT domain, name FROM sites "
                "WHERE name IS NOT NULL AND name != ''"
            ).fetchall()
    except sqlite3.Error:
        return {}
    return {d.lower(): n for d, n in rows}


def _resolve_domain(domain: str, db_path: Optional[str]) -> str:
    """Look up the domain in the sites table, with subdomain stripping.
    Returns the pretty name, or the bare domain as fallback.
    """
    if not domain:
        return ""
    if not db_path:
        return domain
    db_map = _load_db_map(db_path)
    for c in _candidate_domains(domain):
        if c in db_map:
            return db_map[c]
    return domain


def pretty_source(
    final_url: str | None,
    fallback_source: str | None = None,
    *,
    db_path: Optional[str] = "newsletter_agent.db",
) -> str:
    """Best human-friendly source name for an article.

    Logic:
      - If `fallback_source` is a known aggregator (Feedly, NewsAPI, Techmeme),
        re-attribute to the publisher derived from `final_url`.
      - Otherwise prefer `fallback_source` (gather-time label is correct for
        direct sources like Ars Technica or The Verge).
      - Lookup chain: SQLite `sites.name` (with subdomain stripping)
        → bare domain → 'Unknown'.

    `db_path` is the SQLite database holding the `sites` table.
    Pass `None` to skip the DB lookup (useful in tests).
    """
    is_aggregator = fallback_source in _AGGREGATORS

    if is_aggregator:
        domain = _bare_domain(final_url)
        if domain:
            return _resolve_domain(domain, db_path=db_path)
        return fallback_source or "Unknown"

    if fallback_source:
        return fallback_source

    domain = _bare_domain(final_url)
    if domain:
        return _resolve_domain(domain, db_path=db_path)
    return "Unknown"


def reset_db_cache() -> None:
    """Clear the per-process DB cache. Call after migrating new rows."""
    _load_db_map.cache_clear()
