"""Tests for lib.published.record_session_articles — one row per rated article,
written at the end of `select`, embeddings only for the newsletter picks."""
import json
import sqlite3
from unittest.mock import patch

from lib.db import init_db
from lib.published import record_session_articles
from lib.state import NewsletterAgentState


def _state(tmp_db, sid="s1"):
    init_db(tmp_db)
    state = NewsletterAgentState(session_id=sid, db_path=tmp_db)
    state.headline_data = [
        {"id": 0, "title": "Picked", "url": "https://agg.com/r", "final_url": "https://e.com/0",
         "short_summary": "One-liner zero.", "summary": "- bullet zero\n- more",
         "site_name": "E News", "rating": 7.5, "cluster_name": "Chips"},
        {"id": 1, "title": "Not picked", "url": "https://e.com/1",
         "short_summary": "One-liner one.", "summary": "- bullet one",
         "site_name": "E News", "rating": 2.0, "cluster_name": "Other"},
        {"id": 2, "title": "Also not picked", "url": "https://e.com/2",
         "short_summary": "One-liner two.", "summary": "- bullet two",
         "site_name": "F", "rating": 1.0, "cluster_name": "Chips"},
        {"id": 3, "title": "Never rated", "url": "https://e.com/3"},
    ]
    state.newsletter_section_data = [
        {"cat": "Semiconductors", "headline": "Picked", "link": "https://e.com/0",
         "site_name": "E News", "rating": 7.5, "summary": "- bullet zero", "id": 0},
    ]
    return state


def _rows(tmp_db):
    with sqlite3.connect(tmp_db) as conn:
        conn.row_factory = sqlite3.Row
        return {r["url"]: dict(r) for r in conn.execute("SELECT * FROM published_articles")}


def test_records_all_rated_embeds_only_selected(tmp_db):
    state = _state(tmp_db)
    calls = []

    def fake_embed(texts):
        calls.append(list(texts))
        return [[0.1, 0.2] for _ in texts]

    with patch("lib.published.embed_texts", side_effect=fake_embed):
        n_total, n_selected = record_session_articles(state, "s1", tmp_db)

    assert (n_total, n_selected) == (3, 1)
    assert calls == [["Picked\nOne-liner zero."]]
    rows = _rows(tmp_db)
    assert set(rows) == {"https://e.com/0", "https://e.com/1", "https://e.com/2"}

    sel = rows["https://e.com/0"]
    assert sel["selected"] == 1
    assert json.loads(sel["embedding"]) == [0.1, 0.2]
    assert sel["cat"] == "Semiconductors"          # section's cat
    assert sel["rating"] == 7.5 and sel["site_name"] == "E News"
    assert sel["short_summary"] == "One-liner zero."
    assert sel["summary"] == "- bullet zero\n- more"  # full bullets, not the one-liner
    assert sel["published_at"] is None              # send stamps it

    un = rows["https://e.com/1"]
    assert un["selected"] == 0 and un["embedding"] is None
    assert un["cat"] == "Other"
    assert un["short_summary"] == "One-liner one." and un["summary"] == "- bullet one"
    assert un["bsky_status"] == "none" and un["bsky_attempts"] == 0


def test_no_fallback_between_summary_fields(tmp_db):
    state = _state(tmp_db)
    del state.headline_data[1]["short_summary"]
    del state.headline_data[2]["summary"]
    with patch("lib.published.embed_texts", side_effect=lambda t: [[0.1] for _ in t]):
        record_session_articles(state, "s1", tmp_db)
    rows = _rows(tmp_db)
    assert rows["https://e.com/1"]["short_summary"] is None
    assert rows["https://e.com/1"]["summary"] == "- bullet one"
    assert rows["https://e.com/2"]["summary"] is None
    assert rows["https://e.com/2"]["short_summary"] == "One-liner two."


def test_explicit_published_at_stamps_selected_only(tmp_db):
    state = _state(tmp_db)
    with patch("lib.published.embed_texts", side_effect=lambda t: [[0.1] for _ in t]):
        record_session_articles(state, "s1", tmp_db, published_at="2026-09-10T07:00:00")
    rows = _rows(tmp_db)
    assert rows["https://e.com/0"]["published_at"] == "2026-09-10T07:00:00"
    assert rows["https://e.com/1"]["published_at"] is None


def test_selected_item_without_rating_is_still_recorded(tmp_db):
    state = _state(tmp_db)
    del state.headline_data[0]["rating"]
    with patch("lib.published.embed_texts", side_effect=lambda t: [[0.1] for _ in t]):
        n_total, n_selected = record_session_articles(state, "s1", tmp_db)
    assert (n_total, n_selected) == (3, 1)
    assert _rows(tmp_db)["https://e.com/0"]["rating"] == 7.5  # from the section item


def test_duplicate_url_guard_keeps_first(tmp_db, capsys):
    state = _state(tmp_db)
    state.headline_data[2]["url"] = "https://e.com/1"   # collides with id 1
    with patch("lib.published.embed_texts", side_effect=lambda t: [[0.1] for _ in t]):
        n_total, _ = record_session_articles(state, "s1", tmp_db)
    assert n_total == 2
    assert _rows(tmp_db)["https://e.com/1"]["title"] == "Not picked"
    assert "duplicate url" in capsys.readouterr().err


def test_rerun_with_changed_selection_updates_and_prunes(tmp_db):
    state = _state(tmp_db)
    with patch("lib.published.embed_texts", side_effect=lambda t: [[0.1] for _ in t]):
        record_session_articles(state, "s1", tmp_db)
        # re-select: article 1 now picked, article 2 fell out of the rated set
        state.newsletter_section_data = [
            {"cat": "Chips", "headline": "Not picked", "link": "https://e.com/1", "id": 1},
        ]
        del state.headline_data[2]["rating"]
        n_total, n_selected = record_session_articles(state, "s1", tmp_db)
    assert (n_total, n_selected) == (2, 1)
    rows = _rows(tmp_db)
    assert set(rows) == {"https://e.com/0", "https://e.com/1"}
    assert rows["https://e.com/0"]["selected"] == 0
    assert rows["https://e.com/0"]["embedding"] is None
    assert rows["https://e.com/1"]["selected"] == 1
