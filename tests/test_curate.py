"""Tests for lib/curate.py — curation window query, toggle, page renderer."""
import sqlite3
from datetime import datetime

from lib.curate import fetch_page, render_page, toggle_selected, window_start
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


def test_fetch_page_orders_cats_by_top_rating_then_rating(tmp_db):
    for cat, rating in [("Zeta", 1.0), ("Alpha", 0.5), ("Alpha", 0.9),
                        ("Other", 2.0), (None, 3.0), ("", 3.0), ("Alpha", None),
                        ("Beta", 0.2), ("Beta", 0.9), ("Gamma", None)]:
        _insert(tmp_db, cat=cat, rating=rating)
    r = fetch_page(tmp_db, SINCE, 1)
    # Cats by their best story (desc); ties by name; no-rating cats after
    # rated ones; Other, then blank, always last.
    assert [(x["cat"], x["rating"]) for x in r["rows"]][:8] == [
        ("Zeta", 1.0),
        ("Alpha", 0.9), ("Alpha", 0.5), ("Alpha", None),
        ("Beta", 0.9), ("Beta", 0.2),
        ("Gamma", None),
        ("Other", 2.0)]
    assert {x["cat"] for x in r["rows"][8:]} == {None, ""}


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


def row(**kw) -> dict:
    base = {"id": 1, "session_id": IN_SID, "url": "https://ex.com/a",
            "title": "Title", "short_summary": "Short", "summary": "- one\n- two",
            "site_name": "Example", "cat": "Alpha", "rating": 1.5, "selected": 0}
    base.update(kw)
    return base


def test_render_page_rows_headers_and_buttons():
    result = {"rows": [row(id=1, cat="Alpha", selected=1, short_summary="Big news",
                           url="https://a.com/x"),
                       row(id=2, cat="Alpha", selected=0, short_summary="no content",
                           title="Fallback"),
                       row(id=3, cat=None, selected=0)],
              "page": 1, "pages": 2, "total": 60, "selected": 1}
    html = render_page(result, SINCE)
    assert html.count("<h2") == 2 and "Uncategorized" in html
    assert 'href="https://a.com/x"' in html and "Big news" in html and "Fallback" in html
    assert 'data-id="1">✓ Selected</button>' in html and 'data-id="2">Select</button>' in html
    assert 'id="nsel">1</span> selected of <span id="ntotal">60</span>' in html and "page=2" in html and "page=0" not in html


def test_render_page_escapes_db_text():
    html = render_page({"rows": [row(id=1, short_summary="<script>x</script> & co",
                                     url='https://a.com/?q="><b>', summary="- a <i>")],
                        "page": 1, "pages": 1, "total": 1, "selected": 0}, SINCE)
    assert "<script>x</script>" not in html and "&lt;script&gt;" in html
    assert '"><b>' not in html and "<i>" not in html


def test_render_page_empty():
    html = render_page({"rows": [], "page": 1, "pages": 1, "total": 0, "selected": 0},
                       SINCE)
    assert f"No articles since {SINCE}" in html


def test_latest_session_ignores_non_date_ids(tmp_db):
    from lib.curate import latest_session
    for sid in ("2026-11-14-21-11-41", "2026-11-15-18-00-07", "test-session"):
        _insert(tmp_db, session_id=sid)
    assert latest_session(tmp_db) == "2026-11-15-18-00-07"


def test_latest_session_empty_db(tmp_db):
    from lib.curate import latest_session
    init_db(tmp_db)
    assert latest_session(tmp_db) is None


# ── CRUD ─────────────────────────────────────────────────────────────────────

import pytest

from lib.curate import (CurateError, create_article, delete_article, domain_source,
                        get_article, update_article)


def _raw(db, aid, cols):
    with sqlite3.connect(db) as conn:
        return conn.execute(
            f"SELECT {', '.join(cols)} FROM published_articles WHERE id = ?", (aid,)
        ).fetchone()


def test_update_article_changes_only_three_fields(tmp_db):
    aid = _insert(tmp_db, title="T", summary="S", cat="Alpha", rating=0.7,
                  selected=1, embedding="[1]", site_name="old")
    r = update_article(tmp_db, aid, " https://b.com/x ", "  New  ", " b.com ")
    assert (r["url"], r["short_summary"], r["site_name"]) == (
        "https://b.com/x", "New", "b.com")
    assert _raw(tmp_db, aid, ["url", "short_summary", "site_name"]) == (
        "https://b.com/x", "New", "b.com")
    assert _raw(tmp_db, aid, ["title", "summary", "cat", "rating", "selected",
                              "embedding"]) == ("T", "S", "Alpha", 0.7, 1, "[1]")


def test_update_article_duplicate_url_409(tmp_db):
    a = _insert(tmp_db, url="https://a.com/1")
    b = _insert(tmp_db, url="https://a.com/2", short_summary="B")
    with pytest.raises(CurateError) as e:
        update_article(tmp_db, b, "https://a.com/1", "B2", "")
    assert e.value.status == 409 and str(a) in str(e.value)
    assert _raw(tmp_db, b, ["url", "short_summary"]) == ("https://a.com/2", "B")


def test_update_article_same_url_ok(tmp_db):
    a = _insert(tmp_db, url="https://a.com/1")
    assert update_article(tmp_db, a, "https://a.com/1", "H", "")["short_summary"] == "H"


@pytest.mark.parametrize("url,head", [("https://a.com/z", "   "), ("ftp://x", "H"),
                                      ("", "H")])
def test_update_article_validation_400(tmp_db, url, head):
    aid = _insert(tmp_db)
    with pytest.raises(CurateError) as e:
        update_article(tmp_db, aid, url, head, "")
    assert e.value.status == 400


def test_update_article_unknown_404(tmp_db):
    init_db(tmp_db)
    with pytest.raises(CurateError) as e:
        update_article(tmp_db, 999, "https://a.com", "H", "")
    assert e.value.status == 404


def test_delete_article_removes_row(tmp_db):
    aid = _insert(tmp_db, selected=1)
    assert delete_article(tmp_db, aid)["selected"] == 1
    assert get_article(tmp_db, aid) is None


def test_delete_article_unknown_404(tmp_db):
    init_db(tmp_db)
    with pytest.raises(CurateError) as e:
        delete_article(tmp_db, 999)
    assert e.value.status == 404


def test_create_article_defaults(tmp_db):
    _insert(tmp_db, session_id="2026-11-14-07-00-13")
    _insert(tmp_db, session_id=IN_SID)
    r = create_article(tmp_db, "https://www.reuters.com/a", "Head", "")
    assert (r["session_id"], r["cat"], r["title"], r["summary"], r["rating"],
            r["selected"], r["site_name"]) == (
        IN_SID, "Other", "Head", "", None, 1, "reuters.com")
    assert _raw(tmp_db, r["id"], ["embedding"]) == (None,)


def test_create_article_keeps_given_source(tmp_db):
    init_db(tmp_db)
    r = create_article(tmp_db, "https://www.reuters.com/a", "Head", "Reuters",
                       now=datetime(2026, 11, 17, 9, 5, 3))
    assert r["site_name"] == "Reuters"


def test_create_article_empty_table_uses_now(tmp_db):
    init_db(tmp_db)
    r = create_article(tmp_db, "https://a.com/x", "Head",
                       now=datetime(2026, 11, 17, 9, 5, 3))
    assert r["session_id"] == "2026-11-17-09-05-03"


def test_create_article_duplicate_409(tmp_db):
    a = _insert(tmp_db, url="https://a.com/1")
    with pytest.raises(CurateError) as e:
        create_article(tmp_db, "https://a.com/1", "Head")
    assert e.value.status == 409 and str(a) in str(e.value)


def test_domain_source():
    assert domain_source("https://www.Reuters.com/x") == "reuters.com"
    assert domain_source("http://news.ycombinator.com/item") == "news.ycombinator.com"
    assert domain_source("not a url") == ""


def test_update_round_trips_special_chars(tmp_db):
    aid = _insert(tmp_db)
    update_article(tmp_db, aid, "https://a.com/?q=\"<x>", 'A <b> & "q"', "")
    assert _raw(tmp_db, aid, ["url", "short_summary"]) == (
        "https://a.com/?q=\"<x>", 'A <b> & "q"')


def test_render_row_has_escaped_data_attrs():
    from lib.curate import render_row
    html = render_row(row(id=7, short_summary='A "q" <b>', url="https://a.com/?x=1&y=2",
                          site_name="Ex & Co"))
    assert 'id="row-7"' in html and 'class="edit"' in html
    assert 'data-headline="A &quot;q&quot; &lt;b&gt;"' in html
    assert 'data-url="https://a.com/?x=1&amp;y=2"' in html
    assert 'data-source="Ex &amp; Co"' in html


def test_render_row_no_content_headline_falls_back_to_title():
    from lib.curate import render_row
    html = render_row(row(id=8, short_summary="no content", title="Real title"))
    assert 'data-headline="Real title"' in html


def test_render_page_has_add_button_and_total_span():
    html = render_page({"rows": [row()], "page": 1, "pages": 1, "total": 1,
                        "selected": 0}, SINCE)
    assert 'id="add"' in html and 'id="ntotal">1</span>' in html


def test_render_page_includes_crud_js():
    html = render_page({"rows": [row()], "page": 1, "pages": 1, "total": 1,
                        "selected": 0}, SINCE)
    assert "'/update'" in html and "'/delete'" in html and "'/create'" in html
    assert "confirm(" not in html and "alert(" not in html and "prompt(" not in html


def test_update_collapses_internal_whitespace(tmp_db):
    aid = _insert(tmp_db)
    r = update_article(tmp_db, aid, "https://a.com/q", "Line one\n  line   two", "")
    assert r["short_summary"] == "Line one line two"
