"""Tests for lib.sources.pretty_source — the unified, no-aggregator behavior."""
from __future__ import annotations

import sqlite3

import pytest

from lib.db import init_db
from lib.sources import pretty_source, reset_db_cache


def _seed_site(db_path: str, domain: str, name: str) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO sites(domain, name) VALUES(?, ?) "
            "ON CONFLICT(domain) DO UPDATE SET name=excluded.name",
            (domain, name),
        )
        conn.commit()
    reset_db_cache()


def test_known_domain_returns_db_name(tmp_db):
    init_db(tmp_db)
    _seed_site(tmp_db, "nytimes.com", "The New York Times")
    assert pretty_source(
        "https://www.nytimes.com/2026/05/26/foo",
        fallback_source="Hacker News",
        db_path=tmp_db,
    ) == "The New York Times"


def test_unknown_domain_falls_back_to_source_label(tmp_db):
    init_db(tmp_db)
    assert pretty_source(
        "https://obscure.example.org/post",
        fallback_source="Ars Technica",
        db_path=tmp_db,
    ) == "Ars Technica"


def test_unknown_domain_no_fallback_returns_bare_domain(tmp_db):
    init_db(tmp_db)
    assert pretty_source(
        "https://obscure.example.org/post",
        fallback_source=None,
        db_path=tmp_db,
    ) == "obscure.example.org"


def test_no_final_url_falls_back_to_source(tmp_db):
    init_db(tmp_db)
    assert pretty_source(
        None,
        fallback_source="Hacker News",
        db_path=tmp_db,
    ) == "Hacker News"


def test_no_final_url_no_source_returns_unknown(tmp_db):
    init_db(tmp_db)
    assert pretty_source(None, fallback_source=None, db_path=tmp_db) == "Unknown"


def test_subdomain_falls_through_to_parent(tmp_db):
    init_db(tmp_db)
    _seed_site(tmp_db, "yahoo.com", "Yahoo")
    assert pretty_source(
        "https://finance.yahoo.com/news/foo",
        fallback_source="NewsAPI",
        db_path=tmp_db,
    ) == "Yahoo"


def test_aggregator_source_no_longer_special_cased(tmp_db):
    """Old behavior preserved coincidentally: aggregator-gathered articles
    with a known publisher domain still resolve to the publisher name —
    but now for the same reason every other source does, not via a hard-coded set."""
    init_db(tmp_db)
    _seed_site(tmp_db, "nytimes.com", "The New York Times")
    assert pretty_source(
        "https://www.nytimes.com/foo",
        fallback_source="Techmeme",
        db_path=tmp_db,
    ) == "The New York Times"


def test_known_domain_overrides_direct_source_label(tmp_db):
    """If we have a domain in sites, trust the DB. The gather-time source
    label is the fallback, not the override."""
    init_db(tmp_db)
    _seed_site(tmp_db, "arstechnica.com", "Ars Technica")
    assert pretty_source(
        "https://arstechnica.com/post",
        fallback_source="Ars Technica feed v2",
        db_path=tmp_db,
    ) == "Ars Technica"
