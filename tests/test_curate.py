"""Tests for lib/curate.py — curation window query, toggle, page renderer."""
import sqlite3
from datetime import datetime

from lib.curate import fetch_page, toggle_selected, window_start
from lib.db import init_db

SINCE = "2026-11-14-12-00-00"
IN_SID = "2026-11-15-07-00-14"

_n = 0


def _insert(db, **cols) -> int:
    global _n
    init_db(db)
    _n += 1
    row = {"session_id": IN_SID, "url": f"https://ex.com/{_n}",
           "title": f"T{_n}", "selected": 0}
    row.update(cols)
    keys = ", ".join(row)
    qs = ", ".join("?" for _ in row)
    with sqlite3.connect(db) as conn:
        cur = conn.execute(
            f"INSERT INTO published_articles ({keys}) VALUES ({qs})",
            tuple(row.values()),
        )
        return cur.lastrowid


def test_window_start_is_noon_previous_day():
    assert window_start(datetime(2026, 11, 15, 8, 30)) == "2026-11-14-12-00-00"
    assert window_start(datetime(2026, 11, 1, 0, 5)) == "2026-10-31-12-00-00"


def test_fetch_page_window_excludes_older_and_non_date_sessions(tmp_db):
    for sid in ("2026-11-14-07-00-13", "2026-11-14-21-11-41",
                "2026-11-15-07-00-14", "test-session"):
        _insert(tmp_db, session_id=sid)
    r = fetch_page(tmp_db, SINCE, 1)
    assert {x["session_id"] for x in r["rows"]} == {
        "2026-11-14-21-11-41", "2026-11-15-07-00-14"}


def test_fetch_page_orders_cat_then_rating(tmp_db):
    for cat, rating in [("Zeta", 1.0), ("Alpha", 0.5), ("Alpha", 0.9),
                        ("Other", 2.0), (None, 3.0), ("", 3.0), ("Alpha", None)]:
        _insert(tmp_db, cat=cat, rating=rating)
    r = fetch_page(tmp_db, SINCE, 1)
    assert [(x["cat"], x["rating"]) for x in r["rows"]][:5] == [
        ("Alpha", 0.9), ("Alpha", 0.5), ("Alpha", None), ("Zeta", 1.0),
        ("Other", 2.0)]
    assert {x["cat"] for x in r["rows"][5:]} == {None, ""}


def test_fetch_page_paginates_and_clamps(tmp_db):
    for i in range(120):
        _insert(tmp_db, selected=1 if i < 30 else 0, rating=float(i))
    r = fetch_page(tmp_db, SINCE, 3)
    assert (len(r["rows"]), r["page"], r["pages"], r["total"], r["selected"]) == (
        20, 3, 3, 120, 30)
    assert fetch_page(tmp_db, SINCE, 99)["page"] == 3
    assert fetch_page(tmp_db, SINCE, 0)["page"] == 1
    assert fetch_page(tmp_db, SINCE, -4)["page"] == 1


def test_fetch_page_empty_window(tmp_db):
    init_db(tmp_db)
    r = fetch_page(tmp_db, SINCE, 1)
    assert (r["rows"], r["page"], r["pages"], r["total"]) == ([], 1, 1, 0)


def test_toggle_selected_flips_and_reports(tmp_db):
    aid = _insert(tmp_db, selected=1)
    assert toggle_selected(tmp_db, aid) == 0
    assert toggle_selected(tmp_db, aid) == 1
    assert toggle_selected(tmp_db, 999999) is None
