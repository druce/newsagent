"""Tests for lib/steps/rate.py — per-axis confidence + Bradley-Terry composite."""
from unittest.mock import patch
from click.testing import CliRunner
import lib.prompts  # noqa: F401 — register prompts
from lib.db import init_db
from lib.state import NewsletterAgentState
from lib.steps.rate import cli as rate_cli
from lib.prompts._rating_schemas import RatingOutput, StoryConfidence


def _seed(tmp_db, n=3):
    init_db(tmp_db)
    state = NewsletterAgentState(session_id="r1", db_path=tmp_db)
    for s in ("init", "gather", "filter", "download", "summarize"):
        state.complete_step(s)
    state.headline_data = [
        {"id": i, "title": f"T{i}", "url": f"https://e.com/{i}",
         "summary": f"S{i}", "is_ai": True}
        for i in range(n)
    ]
    state.save_checkpoint("summarize")


def test_rate_writes_per_signal_and_composite(tmp_db, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    _seed(tmp_db, n=3)

    def fake_call_prompt(name, inputs, *, engine=None):
        ids = [it["id"] for it in inputs["items"]]
        # Per-axis: quality=0.1 (low low-quality), on_topic=0.9, importance=0.8
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
        # Give h0 the highest BT score so both importance AND bt_z favour h0 over h2
        n = len(ids)
        return {aid: float(n - j) for j, aid in enumerate(ids)}

    with patch("lib.steps.rate.call_prompt", side_effect=fake_call_prompt):
        with patch("lib.steps.rate.bradley_terry_scores", side_effect=fake_bt):
            runner = CliRunner()
            result = runner.invoke(rate_cli, ["--db", tmp_db, "--session", "r1"])
    assert result.exit_code == 0, result.output

    state = NewsletterAgentState(session_id="r1", db_path=tmp_db).load_latest_from_db()
    for h in state.headline_data:
        for key in ["quality_low", "on_topic", "importance", "bt_z", "rating"]:
            assert key in h, f"missing {key} on {h}"

    # h0 had higher importance (0.8) AND higher BT score — should have higher rating than h2
    ratings = {h["id"]: h["rating"] for h in state.headline_data}
    assert ratings[0] > ratings[2]


def test_rate_empty_state_noop(tmp_db, monkeypatch, tmp_path):
    """Session with no headlines completes without calling LLM."""
    monkeypatch.chdir(tmp_path)
    init_db(tmp_db)
    state = NewsletterAgentState(session_id="empty", db_path=tmp_db)
    for s in ("init", "gather", "filter", "download", "summarize"):
        state.complete_step(s)
    state.headline_data = []
    state.save_checkpoint("summarize")

    runner = CliRunner()
    result = runner.invoke(rate_cli, ["--db", tmp_db, "--session", "empty"])
    assert result.exit_code == 0
    assert "nothing" in result.output.lower() or "0" in result.output


def test_rate_skips_headlines_without_summary(tmp_db, monkeypatch, tmp_path):
    """Headlines missing 'summary' are skipped; rated ones get all signal keys."""
    monkeypatch.chdir(tmp_path)
    init_db(tmp_db)
    state = NewsletterAgentState(session_id="s2", db_path=tmp_db)
    for s in ("init", "gather", "filter", "download", "summarize"):
        state.complete_step(s)
    state.headline_data = [
        {"id": 0, "title": "Has summary", "url": "https://e.com/a", "summary": "Summary here", "is_ai": True},
        {"id": 1, "title": "No summary", "url": "https://e.com/b", "is_ai": True},  # no summary
    ]
    state.save_checkpoint("summarize")

    call_count = {"n": 0}

    def fake_call_prompt(name, inputs, *, engine=None):
        call_count["n"] += 1
        ids = [it["id"] for it in inputs["items"]]
        return RatingOutput(results_list=[
            StoryConfidence(id=i, confidence=0.5) for i in ids
        ])

    def fake_bt(items, **_):
        return {item["id"]: 0.0 for item in items}

    with patch("lib.steps.rate.call_prompt", side_effect=fake_call_prompt):
        with patch("lib.steps.rate.bradley_terry_scores", side_effect=fake_bt):
            runner = CliRunner()
            result = runner.invoke(rate_cli, ["--db", tmp_db, "--session", "s2"])
    assert result.exit_code == 0, result.output

    state = NewsletterAgentState(session_id="s2", db_path=tmp_db).load_latest_from_db()
    # Headline with summary gets rating; headline without summary does not
    rated = [h for h in state.headline_data if "rating" in h]
    assert len(rated) == 1
    assert rated[0]["id"] == 0
