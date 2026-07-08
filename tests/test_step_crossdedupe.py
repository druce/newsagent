import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner

import lib.prompts  # noqa: F401 — register same_story
from lib.db import init_db, PublishedArticle
from lib.state import NewsletterAgentState
from lib.steps.crossdedupe import cli as crossdedupe_cli
from lib.prompts.same_story import SameStoryOutput, SameStoryVerdict


def _seed_state(tmp_db, headlines):
    init_db(tmp_db)
    state = NewsletterAgentState(session_id="c1", db_path=tmp_db)
    state.complete_step("start")
    state.complete_step("summarize")
    state.headline_data = headlines
    state.save_checkpoint("summarize")
    return state


def _add_prior(tmp_db, url, title, short_summary, embedding, days_ago=1):
    now = datetime.now()
    with sqlite3.connect(tmp_db) as conn:
        PublishedArticle(
            session_id="prev",
            url=url,
            title=title,
            short_summary=short_summary,
            embedding=embedding,
            published_at=(now - timedelta(days=days_ago)).isoformat(),
        ).insert(conn)


# ── prepare-batches ──────────────────────────────────────────────────

def test_prepare_shortlists_near_duplicate(tmp_db, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    _seed_state(tmp_db, [
        {"title": "CEO doubles revenue with AI", "url": "https://yahoo.com/x",
         "short_summary": "A CEO used Claude to double revenue."},
        {"title": "Nvidia ships new GPU", "url": "https://e.com/gpu",
         "short_summary": "Nvidia announced a new GPU."},
    ])
    # Prior matches headline 0 (vector [1,0]), not headline 1 ([0,1]).
    _add_prior(tmp_db, "https://fortune.com/x", "West Shore CEO used Claude",
               "A CEO doubled revenue using Claude despite a hallucination.",
               embedding=[1.0, 0.0])

    def fake_embed(texts):
        # candidate order: headline 0 then headline 1
        return [[1.0, 0.0], [0.0, 1.0]]

    with patch("lib.steps.crossdedupe.embed_texts", side_effect=fake_embed):
        runner = CliRunner()
        result = runner.invoke(crossdedupe_cli, [
            "--db", tmp_db, "--session", "c1", "--prepare-batches",
            "--shortlist-threshold", "0.7",
        ])
    assert result.exit_code == 0, result.output

    files = sorted(Path("runs/c1/crossdedupe-batches").glob("batch-*.json"))
    assert len(files) == 1
    b0 = json.loads(files[0].read_text())
    assert b0["ids"] == ["0"]
    pair = b0["pairs"][0]
    assert pair["id"] == "0"
    assert pair["b_title"] == "West Shore CEO used Claude"
    assert "system_prompt" in b0 and b0["system_prompt"]
    assert b0["output_schema"]["properties"].get("results") is not None


def test_prepare_no_priors_writes_no_batches(tmp_db, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    _seed_state(tmp_db, [
        {"title": "A", "url": "https://e.com/a", "short_summary": "a story"},
    ])

    def fake_embed(texts):
        return [[1.0, 0.0]]

    with patch("lib.steps.crossdedupe.embed_texts", side_effect=fake_embed):
        runner = CliRunner()
        result = runner.invoke(crossdedupe_cli, [
            "--db", tmp_db, "--session", "c1", "--prepare-batches",
        ])
    assert result.exit_code == 0, result.output
    files = list(Path("runs/c1/crossdedupe-batches").glob("batch-*.json")) \
        if Path("runs/c1/crossdedupe-batches").exists() else []
    assert files == []


def test_prepare_ignores_out_of_window_prior(tmp_db, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    _seed_state(tmp_db, [
        {"title": "A", "url": "https://e.com/a", "short_summary": "a story"},
    ])
    _add_prior(tmp_db, "https://e.com/old", "old", "old story",
               embedding=[1.0, 0.0], days_ago=10)

    def fake_embed(texts):
        return [[1.0, 0.0]]

    with patch("lib.steps.crossdedupe.embed_texts", side_effect=fake_embed):
        runner = CliRunner()
        result = runner.invoke(crossdedupe_cli, [
            "--db", tmp_db, "--session", "c1", "--prepare-batches",
            "--lookback-days", "4",
        ])
    assert result.exit_code == 0, result.output
    d = Path("runs/c1/crossdedupe-batches")
    files = list(d.glob("batch-*.json")) if d.exists() else []
    assert files == []


def test_prepare_skips_headline_without_short_summary(tmp_db, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    _seed_state(tmp_db, [
        {"title": "no summary", "url": "https://e.com/a"},  # no short_summary
    ])
    _add_prior(tmp_db, "https://e.com/p", "p", "prior", embedding=[1.0, 0.0])

    called = {}

    def fake_embed(texts):
        called["texts"] = texts
        return [[1.0, 0.0] for _ in texts]

    with patch("lib.steps.crossdedupe.embed_texts", side_effect=fake_embed):
        runner = CliRunner()
        result = runner.invoke(crossdedupe_cli, [
            "--db", tmp_db, "--session", "c1", "--prepare-batches",
        ])
    assert result.exit_code == 0, result.output
    # No candidates → embed never called with content (or called with empty)
    assert called.get("texts", []) == []


# ── apply-results ────────────────────────────────────────────────────

def _prepare_then(tmp_db, tmp_path):
    """Run prepare producing one batch with candidate id '0'."""
    _seed_state(tmp_db, [
        {"title": "CEO doubles revenue with AI", "url": "https://yahoo.com/x",
         "short_summary": "A CEO used Claude to double revenue."},
        {"title": "Nvidia ships new GPU", "url": "https://e.com/gpu",
         "short_summary": "Nvidia announced a new GPU."},
    ])
    _add_prior(tmp_db, "https://fortune.com/x", "West Shore CEO used Claude",
               "A CEO doubled revenue using Claude despite a hallucination.",
               embedding=[1.0, 0.0])

    def fake_embed(texts):
        return [[1.0, 0.0], [0.0, 1.0]]

    with patch("lib.steps.crossdedupe.embed_texts", side_effect=fake_embed):
        CliRunner().invoke(crossdedupe_cli, [
            "--db", tmp_db, "--session", "c1", "--prepare-batches",
            "--shortlist-threshold", "0.7",
        ])


def test_apply_drops_confirmed_duplicate(tmp_db, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    _prepare_then(tmp_db, tmp_path)

    results_dir = Path("runs/c1/crossdedupe-results")
    results_dir.mkdir(parents=True)
    (results_dir / "batch-000.json").write_text(json.dumps({
        "results": [{"id": "0", "same": True}]
    }))

    runner = CliRunner()
    result = runner.invoke(crossdedupe_cli, [
        "--db", tmp_db, "--session", "c1",
        "--apply-results", str(results_dir),
    ])
    assert result.exit_code == 0, result.output

    state = NewsletterAgentState(session_id="c1", db_path=tmp_db).load_latest_from_db()
    urls = [h["url"] for h in state.headline_data]
    assert urls == ["https://e.com/gpu"]  # duplicate headline 0 dropped


def test_apply_keeps_when_not_duplicate(tmp_db, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    _prepare_then(tmp_db, tmp_path)

    results_dir = Path("runs/c1/crossdedupe-results")
    results_dir.mkdir(parents=True)
    (results_dir / "batch-000.json").write_text(json.dumps({
        "results": [{"id": "0", "same": False}]
    }))

    runner = CliRunner()
    result = runner.invoke(crossdedupe_cli, [
        "--db", tmp_db, "--session", "c1",
        "--apply-results", str(results_dir),
    ])
    assert result.exit_code == 0, result.output

    state = NewsletterAgentState(session_id="c1", db_path=tmp_db).load_latest_from_db()
    assert len(state.headline_data) == 2


# ── classic mode ─────────────────────────────────────────────────────

def test_classic_mode_drops_duplicate(tmp_db, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    _seed_state(tmp_db, [
        {"title": "CEO doubles revenue with AI", "url": "https://yahoo.com/x",
         "short_summary": "A CEO used Claude to double revenue."},
        {"title": "Nvidia ships new GPU", "url": "https://e.com/gpu",
         "short_summary": "Nvidia announced a new GPU."},
    ])
    _add_prior(tmp_db, "https://fortune.com/x", "West Shore CEO used Claude",
               "A CEO doubled revenue using Claude.", embedding=[1.0, 0.0])

    def fake_embed(texts):
        return [[1.0, 0.0], [0.0, 1.0]]

    def fake_call_prompt(name, inputs, *, engine=None):
        assert name == "same_story"
        ids = [p["id"] for p in inputs["pairs"]]
        return SameStoryOutput(results=[
            SameStoryVerdict(id=i, same=True) for i in ids
        ])

    with patch("lib.steps.crossdedupe.embed_texts", side_effect=fake_embed), \
         patch("lib.steps.crossdedupe.call_prompt", side_effect=fake_call_prompt):
        runner = CliRunner()
        result = runner.invoke(crossdedupe_cli, [
            "--db", tmp_db, "--session", "c1",
            "--engine", "google:gemini-3.1-flash-lite",
            "--shortlist-threshold", "0.7",
        ])
    assert result.exit_code == 0, result.output

    state = NewsletterAgentState(session_id="c1", db_path=tmp_db).load_latest_from_db()
    urls = [h["url"] for h in state.headline_data]
    assert urls == ["https://e.com/gpu"]


def test_rejects_both_prepare_and_apply(tmp_db, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    _seed_state(tmp_db, [{"title": "A", "url": "u", "short_summary": "a"}])
    runner = CliRunner()
    result = runner.invoke(crossdedupe_cli, [
        "--db", tmp_db, "--session", "c1",
        "--prepare-batches", "--apply-results", "x",
    ])
    assert result.exit_code != 0
