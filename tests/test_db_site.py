import sqlite3
from lib.db import init_db, Site


def test_sites_schema_has_scrape_method_column(tmp_db):
    init_db(tmp_db)
    with sqlite3.connect(tmp_db) as conn:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(sites)").fetchall()]
    assert "scrape_method" in cols


def test_site_upsert_and_get(tmp_db):
    init_db(tmp_db)
    with sqlite3.connect(tmp_db) as conn:
        Site(domain="example.com", name="Example", scrape_method=None).upsert(conn)
        s = Site.get_by_domain(conn, "example.com")
    assert s is not None
    assert s.name == "Example"
    assert s.scrape_method is None


def test_site_upsert_updates_scrape_method(tmp_db):
    init_db(tmp_db)
    with sqlite3.connect(tmp_db) as conn:
        Site(domain="bloomberg.com", name="Bloomberg",
             scrape_method=None).upsert(conn)
        Site(domain="bloomberg.com", name="Bloomberg",
             scrape_method="playwright").upsert(conn)
        s = Site.get_by_domain(conn, "bloomberg.com")
    assert s.scrape_method == "playwright"


def test_site_get_missing(tmp_db):
    init_db(tmp_db)
    with sqlite3.connect(tmp_db) as conn:
        assert Site.get_by_domain(conn, "nope.com") is None
