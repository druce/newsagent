"""Map article URLs to human-friendly source names.

Resolution order:
  1. SQLite `sites.name` row (DB is the single source of truth; ~4k+ domains
     seeded from the legacy newsletter_agent.db migration).
     Tries the exact bare domain first, then strips leading subdomains.
  2. Gather-time source label (`fallback_source`), if provided.
  3. Bare domain of `final_url`.
  4. "Unknown".
"""
from __future__ import annotations

import sqlite3
from functools import lru_cache
from typing import Optional
from urllib.parse import urlparse


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


def _resolve_domain(domain: str, db_path: Optional[str]) -> Optional[str]:
    """Look up the domain in the sites table, with subdomain stripping.
    Returns the pretty name, or None if no row matches.
    """
    if not domain:
        return None
    if not db_path:
        return None
    db_map = _load_db_map(db_path)
    for c in _candidate_domains(domain):
        if c in db_map:
            return db_map[c]
    return None


def pretty_source(
    final_url: str | None,
    fallback_source: str | None = None,
    *,
    db_path: Optional[str] = "newsletter_agent.db",
) -> str:
    """Best human-friendly source name for an article.

    Resolution order:
      1. If `final_url` resolves to a domain present in `sites.name`
         (with subdomain stripping), return that name. This is the
         authoritative path — the publisher is whoever's serving the URL.
      2. Else, return `fallback_source` if provided. This covers headlines
         that failed to download (no final_url) and unknown publishers where
         the gather-time source label is the best we have.
      3. Else, the bare domain of `final_url`.
      4. Else, "Unknown".

    `db_path` is the SQLite database holding the `sites` table.
    Pass `None` to skip the DB lookup (useful in tests).
    """
    domain = _bare_domain(final_url)
    if domain:
        name = _resolve_domain(domain, db_path=db_path)
        if name:
            return name
    if fallback_source:
        return fallback_source
    if domain:
        return domain
    return "Unknown"


def reset_db_cache() -> None:
    """Clear the per-process DB cache. Call after migrating new rows."""
    _load_db_map.cache_clear()
