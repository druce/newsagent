"""Extract a same-domain <link rel="canonical"> from an article HTML page.

The publisher's canonical URL is the right dedup/identification key when it
exists — it strips tracking params and resolves CDN variants. We accept a
canonical only when its bare domain (minus a leading 'www.') equals the
post-redirect URL's bare domain. Anything else (cross-domain canonicals,
subdomain canonicals, junk schemes) is rejected, so a hostile publisher can't
hijack one of our cache entries.
"""
from __future__ import annotations

from typing import Optional
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup


def _bare(host: str) -> str:
    host = (host or "").lower()
    return host[4:] if host.startswith("www.") else host


def extract_canonical_url(html: str, page_url: str) -> Optional[str]:
    """Return a validated canonical URL or None.

    Validation rules:
      - <link rel="canonical"> present with a non-empty href.
      - href resolves (via urljoin) to an http(s) absolute URL.
      - Resolved URL's bare host equals the page_url's bare host
        (case-insensitive, www-prefix-tolerant). Cross-domain or
        subdomain-mismatch canonicals are rejected.
    """
    try:
        soup = BeautifulSoup(html, "html.parser")
    except Exception:
        return None

    page_host = _bare(urlparse(page_url).hostname or "")
    if not page_host:
        return None

    for link in soup.find_all("link", rel="canonical"):
        href = (link.get("href") or "").strip()
        if not href:
            continue
        try:
            resolved = urljoin(page_url, href)
            parsed = urlparse(resolved)
        except Exception:
            continue
        if parsed.scheme not in ("http", "https"):
            continue
        if _bare(parsed.hostname or "") != page_host:
            continue
        return resolved
    return None
