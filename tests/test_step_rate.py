"""Tests for lib/steps/rate.py — additive composite + published-date fallback chain."""
import math
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from click.testing import CliRunner

import lib.prompts  # noqa: F401 — register prompts
from lib.config import MAX_ARTICLE_AGE_DAYS, RATING_COEFFS
from lib.db import Site, init_db
from lib.prompts._rating_schemas import RatingOutput, StoryConfidence
from lib.state import NewsletterAgentState
from lib.steps.rate import (
    _adjusted_len,
    _date_from_url,
    _parse_date_string,
    _recency_score,
    _resolve_published,
    cli as rate_cli,
)


# ── seed helpers ──────────────────────────────────────────

def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


def _seed(tmp_db, n=3, *, published_iso=None):
    init_db(tmp_db)
    state = NewsletterAgentState(session_id="r1", db_path=tmp_db)
    for s in ("start", "gather", "filter", "download", "summarize"):
        state.complete_step(s)
    pub = published_iso or _now().isoformat()
    state.headline_data = [
        {"id": i, "title": f"T{i}", "url": f"https://example.com/{i}",
         "summary": f"S{i}", "is_ai": True, "published": pub}
        for i in range(n)
    ]
    state.save_checkpoint("summarize")


# ── _recency_score ────────────────────────────────────────

def test_recency_curve_matches_legacy():
    # 2*exp(-ln2 * age) - 1  → +1 at age 0, 0 at age 1, -0.5 at age 2
    assert _recency_score(0.0) == pytest.approx(1.0, abs=1e-9)
    assert _recency_score(1.0) == pytest.approx(0.0, abs=1e-9)
    assert _recency_score(2.0) == pytest.approx(-0.5, abs=1e-9)
    # Asymptotic floor at -1
    assert _recency_score(50.0) == pytest.approx(-1.0, abs=1e-3)


def test_recency_score_clamps_negative_age():
    # Future-published articles should not get >1 boost
    assert _recency_score(-3.0) == _recency_score(0.0)


# ── _adjusted_len ─────────────────────────────────────────

def test_adjusted_len_endpoints():
    assert _adjusted_len(0) == 0.0
    assert _adjusted_len(1000) == pytest.approx(0.0, abs=1e-9)  # log10(1000) - 3 = 0
    assert _adjusted_len(10_000) == pytest.approx(1.0, abs=1e-9)
    assert _adjusted_len(100_000) == pytest.approx(2.0, abs=1e-9)
    # Clipping at upper bound
    assert _adjusted_len(10_000_000) == 2.0


# ── _parse_date_string ────────────────────────────────────

def test_parse_date_handles_rfc2822_and_iso():
    rfc = "Thu, 21 May 2026 14:01:37 +0000"
    iso = "2026-05-21T14:01:37+00:00"
    bad = "not a date"
    assert _parse_date_string(rfc) is not None
    assert _parse_date_string(iso) is not None
    assert _parse_date_string(bad) is None
    assert _parse_date_string("") is None


# ── _date_from_url ────────────────────────────────────────

def test_date_from_url_slash_pattern():
    dt = _date_from_url("https://news.com/ai/2026/05/21/something")
    assert dt is not None
    assert dt.year == 2026 and dt.month == 5 and dt.day == 21


def test_date_from_url_dash_pattern():
    dt = _date_from_url("https://news.com/ai/2026-05-21-some-title")
    assert dt is not None
    assert (dt.year, dt.month, dt.day) == (2026, 5, 21)


def test_date_from_url_no_match():
    assert _date_from_url("https://news.com/ai/something") is None
    assert _date_from_url("") is None


# ── _resolve_published fallback chain ─────────────────────

def test_resolve_published_prefers_feed_date():
    now = _now()
    h = {"published": "Thu, 21 May 2026 14:00:00 +0000",
         "url": "https://news.com/2020/01/01/old-url"}
    dt, src = _resolve_published(h, now)
    assert src == "feed"
    assert dt.year == 2026 and dt.month == 5


def test_resolve_published_uses_url_when_no_feed_or_html(tmp_path):
    now = _now()
    h = {"url": "https://news.com/ai/2026/05/21/title"}
    dt, src = _resolve_published(h, now)
    assert src == "url"
    assert (dt.year, dt.month, dt.day) == (2026, 5, 21)
    # Side effect: h["published"] enriched
    assert h["published"].startswith("2026-05-21")


def test_resolve_published_skips_mtime_fallback(tmp_path):
    """File mtime must NOT be used as a publish-date fallback — it's when we
    downloaded, not when the article was published. An old article we fetched
    today should land on the sentinel, not on mtime."""
    now = _now()
    f = tmp_path / "cached.html"
    f.write_text("<html></html>")  # no parseable date inside
    # mtime set to 6h ago — must NOT influence the result.
    import os
    six_hours_ago = (now - timedelta(hours=6)).timestamp()
    os.utime(f, (six_hours_ago, six_hours_ago))

    h = {"url": "https://news.com/no-date-here", "html_path": str(f)}
    dt, src = _resolve_published(h, now)
    assert src == "sentinel"


def test_resolve_published_sentinel_last_resort():
    now = _now()
    h = {"url": "https://news.com/no-date"}
    dt, src = _resolve_published(h, now)
    assert src == "sentinel"
    # Sentinel is now - 1d
    assert abs((now - dt).total_seconds() - 86400) < 5


# ── reputation lookup via sites table ─────────────────────

def test_reputation_used_in_composite(tmp_db, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    init_db(tmp_db)
    state = NewsletterAgentState(session_id="rep", db_path=tmp_db)
    for s in ("start", "gather", "filter", "download", "summarize"):
        state.complete_step(s)
    # Two identical headlines on different domains; only "good.com" has reputation.
    pub = _now().isoformat()
    state.headline_data = [
        {"id": 0, "title": "T", "url": "https://good.com/a", "summary": "S",
         "is_ai": True, "published": pub},
        {"id": 1, "title": "T", "url": "https://meh.com/a", "summary": "S",
         "is_ai": True, "published": pub},
    ]
    state.save_checkpoint("summarize")

    import sqlite3
    with sqlite3.connect(tmp_db) as conn:
        Site(domain="good.com", name="Good", reputation=2.0).upsert(conn)

    def fake_call_prompt(name, inputs, *, engine=None):
        ids = [it["id"] for it in inputs["items"]]
        return RatingOutput(results_list=[StoryConfidence(id=i, confidence=0.5) for i in ids])

    def fake_bt(items, **_):
        return {item["id"]: 0.0 for item in items}

    with patch("lib.steps.rate.call_prompt", side_effect=fake_call_prompt):
        with patch("lib.steps.rate.bradley_terry_scores", side_effect=fake_bt):
            runner = CliRunner()
            result = runner.invoke(rate_cli, ["--db", tmp_db, "--session", "rep", "--no-email"])
    assert result.exit_code == 0, result.output

    state = NewsletterAgentState(session_id="rep", db_path=tmp_db).load_latest_from_db()
    ratings = {h["id"]: h["rating"] for h in state.headline_data}
    # good.com rating should be ~2.0 higher (reputation coeff 1.0 × reputation 2.0)
    assert ratings[0] - ratings[1] == pytest.approx(2.0, abs=1e-6)
    # And the reputation field is persisted on the rated headline
    h0 = next(h for h in state.headline_data if h["id"] == 0)
    assert h0["reputation"] == 2.0


# ── 7-day drop ────────────────────────────────────────────

def test_articles_older_than_7d_dropped(tmp_db, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    init_db(tmp_db)
    state = NewsletterAgentState(session_id="old", db_path=tmp_db)
    for s in ("start", "gather", "filter", "download", "summarize"):
        state.complete_step(s)
    fresh = _now().isoformat()
    stale = (_now() - timedelta(days=MAX_ARTICLE_AGE_DAYS + 1)).isoformat()
    state.headline_data = [
        {"id": 0, "title": "fresh", "url": "https://e.com/a", "summary": "S",
         "is_ai": True, "published": fresh},
        {"id": 1, "title": "stale", "url": "https://e.com/b", "summary": "S",
         "is_ai": True, "published": stale},
    ]
    state.save_checkpoint("summarize")

    def fake_call_prompt(name, inputs, *, engine=None):
        ids = [it["id"] for it in inputs["items"]]
        return RatingOutput(results_list=[StoryConfidence(id=i, confidence=0.5) for i in ids])

    def fake_bt(items, **_):
        return {item["id"]: 0.0 for item in items}

    with patch("lib.steps.rate.call_prompt", side_effect=fake_call_prompt):
        with patch("lib.steps.rate.bradley_terry_scores", side_effect=fake_bt):
            runner = CliRunner()
            result = runner.invoke(rate_cli, ["--db", tmp_db, "--session", "old", "--no-email"])
    assert result.exit_code == 0, result.output

    state = NewsletterAgentState(session_id="old", db_path=tmp_db).load_latest_from_db()
    rated_ids = [h["id"] for h in state.headline_data if "rating" in h]
    assert rated_ids == [0]


# ── composite shape: additive, larger than legacy weighted ─

def test_composite_is_additive_legacy_shape(tmp_db, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    _seed(tmp_db, n=3)

    def fake_call_prompt(name, inputs, *, engine=None):
        ids = [it["id"] for it in inputs["items"]]
        if name == "rate_quality":
            scores = [0.1, 0.1, 0.1]
        elif name == "rate_on_topic":
            scores = [0.9, 0.9, 0.9]
        elif name == "rate_importance":
            scores = [0.8, 0.5, 0.2]
        else:
            scores = [0.5] * len(ids)
        return RatingOutput(results_list=[
            StoryConfidence(id=i, confidence=s) for i, s in zip(ids, scores)
        ])

    def fake_bt(items, **_):
        ids = [item["id"] for item in items]
        n = len(ids)
        return {aid: float(n - j) for j, aid in enumerate(ids)}

    with patch("lib.steps.rate.call_prompt", side_effect=fake_call_prompt):
        with patch("lib.steps.rate.bradley_terry_scores", side_effect=fake_bt):
            runner = CliRunner()
            result = runner.invoke(rate_cli, ["--db", tmp_db, "--session", "r1", "--no-email"])
    assert result.exit_code == 0, result.output

    state = NewsletterAgentState(session_id="r1", db_path=tmp_db).load_latest_from_db()
    h0 = next(h for h in state.headline_data if h["id"] == 0)

    # Manually re-compute the additive composite from persisted per-axis values.
    c = RATING_COEFFS
    expected = (
        c["reputation"]   * h0["reputation"]
        + c["adjusted_len"] * h0["adjusted_len"]
        + c["on_topic"]     * h0["on_topic"]
        + c["importance"]   * h0["importance"]
        - c["quality_low"]  * h0["quality_low"]
        + c["bt_z"]         * h0["bt_z"]
        + c["recency"]      * h0["recency_score"]
    )
    assert h0["rating"] == pytest.approx(expected, abs=1e-9)

    # Higher importance + higher BT must rank h0 above h2
    ratings = {h["id"]: h["rating"] for h in state.headline_data}
    assert ratings[0] > ratings[2]

    # And the ratings are now meaningfully larger than the old weighted average
    # (which would have stayed ≤ ~1). With on_topic=0.9 + importance=0.8 alone
    # we already exceed the legacy ceiling.
    assert ratings[0] > 1.0


# ── empty + missing-summary preserved ─────────────────────

def test_rate_empty_state_noop(tmp_db, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    init_db(tmp_db)
    state = NewsletterAgentState(session_id="empty", db_path=tmp_db)
    for s in ("start", "gather", "filter", "download", "summarize"):
        state.complete_step(s)
    state.headline_data = []
    state.save_checkpoint("summarize")

    runner = CliRunner()
    result = runner.invoke(rate_cli, ["--db", tmp_db, "--session", "empty", "--no-email"])
    assert result.exit_code == 0
    assert "nothing" in result.output.lower() or "0" in result.output


def test_rate_skips_headlines_without_summary(tmp_db, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    init_db(tmp_db)
    state = NewsletterAgentState(session_id="s2", db_path=tmp_db)
    for s in ("start", "gather", "filter", "download", "summarize"):
        state.complete_step(s)
    pub = _now().isoformat()
    state.headline_data = [
        {"id": 0, "title": "Has summary", "url": "https://e.com/a", "summary": "Summary here",
         "is_ai": True, "published": pub},
        {"id": 1, "title": "No summary", "url": "https://e.com/b", "is_ai": True, "published": pub},
    ]
    state.save_checkpoint("summarize")

    def fake_call_prompt(name, inputs, *, engine=None):
        ids = [it["id"] for it in inputs["items"]]
        return RatingOutput(results_list=[
            StoryConfidence(id=i, confidence=0.5) for i in ids
        ])

    def fake_bt(items, **_):
        return {item["id"]: 0.0 for item in items}

    with patch("lib.steps.rate.call_prompt", side_effect=fake_call_prompt):
        with patch("lib.steps.rate.bradley_terry_scores", side_effect=fake_bt):
            runner = CliRunner()
            result = runner.invoke(rate_cli, ["--db", tmp_db, "--session", "s2", "--no-email"])
    assert result.exit_code == 0, result.output

    state = NewsletterAgentState(session_id="s2", db_path=tmp_db).load_latest_from_db()
    rated = [h for h in state.headline_data if "rating" in h]
    assert len(rated) == 1
    assert rated[0]["id"] == 0
