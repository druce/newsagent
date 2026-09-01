"""Systemwide (non-source) configuration, loaded from config.yaml.

sources.yaml describes *where headlines come from*; config.yaml holds
pipeline-wide operator settings. First resident: blocked_domains — domains
whose articles are dropped at gather time (initial URL) and again at
download time after redirects resolve (final URL).

A missing config.yaml is a silent no-op so fresh checkouts and tests run
without one.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from lib.sources import _bare_domain, _candidate_domains


@dataclass(frozen=True)
class AppConfig:
    blocked_domains: frozenset[str] = field(default_factory=frozenset)


def load_app_config(path: str | Path = "config.yaml") -> AppConfig:
    """Load config.yaml. Missing file → defaults; malformed → ValueError."""
    p = Path(path)
    if not p.exists():
        return AppConfig()
    data = yaml.safe_load(p.read_text()) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{p} must be a YAML mapping")
    raw = data.get("blocked_domains") or []
    if not isinstance(raw, list) or not all(isinstance(d, str) for d in raw):
        raise ValueError(f"{p}: blocked_domains must be a list of domain strings")
    domains = frozenset(_normalize(d) for d in raw if _normalize(d))
    return AppConfig(blocked_domains=domains)


def _normalize(domain: str) -> str:
    d = domain.strip().lower()
    if d.startswith("www."):
        d = d[4:]
    return d


def url_is_blocked(url: str, blocked: frozenset[str]) -> bool:
    """True if url's domain, or any parent domain, is in blocked.

    Suffix semantics per domain label: 'fool.com' blocks 'www.fool.com'
    and 'm.fool.com' but not 'notfool.com'.
    """
    if not blocked:
        return False
    return any(c in blocked for c in _candidate_domains(_bare_domain(url)))
