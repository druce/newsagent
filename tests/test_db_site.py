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


def test_sites_schema_has_bright_data_column(tmp_db):
    init_db(tmp_db)
    with sqlite3.connect(tmp_db) as conn:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(sites)").fetchall()]
    assert "bright_data_enabled" in cols


def test_init_db_seeds_bright_data_default_domains(tmp_db):
    init_db(tmp_db)
    with sqlite3.connect(tmp_db) as conn:
        for d in ("bloomberg.com", "www.bloomberg.com", "wsj.com", "www.wsj.com",
                  "cnn.com", "fastcompany.com", "forbes.com"):
            s = Site.get_by_domain(conn, d)
            assert s is not None, f"missing seed for {d}"
            assert s.bright_data_enabled is True, f"{d} should have BD enabled"


def test_site_upsert_round_trips_bright_data_flag(tmp_db):
    init_db(tmp_db)
    with sqlite3.connect(tmp_db) as conn:
        Site(domain="example.com", name="Example",
             bright_data_enabled=True).upsert(conn)
        s = Site.get_by_domain(conn, "example.com")
    assert s is not None
    assert s.bright_data_enabled is True


def test_init_db_migrates_existing_sites_table_without_column(tmp_db):
    # Build a "legacy" sites table without bright_data_enabled to simulate
    # an existing DB from before this change.
    with sqlite3.connect(tmp_db) as conn:
        conn.execute(
            "CREATE TABLE sites ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "domain TEXT NOT NULL UNIQUE, "
            "name TEXT, "
            "reputation REAL DEFAULT 0.0, "
            "scrape_method TEXT, "
            "last_seen TEXT)"
        )
        conn.execute(
            "INSERT INTO sites (domain, name) VALUES ('legacy.example', 'Legacy')"
        )
        conn.commit()

    init_db(tmp_db)

    with sqlite3.connect(tmp_db) as conn:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(sites)").fetchall()]
        assert "bright_data_enabled" in cols
        # Pre-existing row is preserved with flag=0
        legacy = Site.get_by_domain(conn, "legacy.example")
        assert legacy is not None
        assert legacy.bright_data_enabled is False
        # Newly seeded BD-enabled domain is present
        wsj = Site.get_by_domain(conn, "wsj.com")
        assert wsj is not None and wsj.bright_data_enabled is True


def test_site_upsert_without_bright_data_preserves_existing_flag(tmp_db):
    """An upsert that doesn't pass bright_data_enabled must NOT clobber an
    existing row's flag (matters in the download step's commit path)."""
    init_db(tmp_db)  # seeds wsj.com with bright_data_enabled=1
    with sqlite3.connect(tmp_db) as conn:
        # No bright_data_enabled kwarg — should leave the seeded 1 in place.
        Site(domain="wsj.com", scrape_method="http",
             last_seen="2026-05-19T00:00:00").upsert(conn)
        s = Site.get_by_domain(conn, "wsj.com")
    assert s is not None
    assert s.bright_data_enabled is True  # seed preserved
    assert s.scrape_method == "http"  # new field written


def test_init_db_seeding_preserves_existing_row(tmp_db):
    # If wsj.com already exists with a custom scrape_method, init_db should
    # set bright_data_enabled=1 without clobbering other fields.
    init_db(tmp_db)
    with sqlite3.connect(tmp_db) as conn:
        Site(domain="wsj.com", name="WSJ",
             scrape_method="playwright").upsert(conn)
    # Re-run init_db (idempotent seed).
    init_db(tmp_db)
    with sqlite3.connect(tmp_db) as conn:
        wsj = Site.get_by_domain(conn, "wsj.com")
    assert wsj is not None
    assert wsj.bright_data_enabled is True
    assert wsj.name == "WSJ"
    assert wsj.scrape_method == "playwright"
