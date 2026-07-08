import json
from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner

import lib.prompts  # noqa: F401
from lib.db import init_db
from lib.state import NewsletterAgentState
from lib.steps.coverage import cli as coverage_cli
from lib.prompts.same_story_sameday import (
    SameStorySamedayOutput,
    SameStorySamedayVerdict,
)


def _seed(tmp_db, headlines):
    init_db(tmp_db)
    state = NewsletterAgentState(session_id="v1", db_path=tmp_db)
    state.complete_step("start")
    state.complete_step("summarize")
    state.complete_step("crossdedupe")
    state.headline_data = headlines
    state.save_checkpoint("crossdedupe")
    return state


_THREE = [
    {"title": "OpenAI delays IPO", "url": "https://a.com/1",
     "short_summary": "OpenAI pushed its IPO", "summary": "- OpenAI delayed IPO"},
    {"title": "OpenAI listing slips", "url": "https://b.com/2",
     "short_summary": "OpenAI IPO delayed", "summary": "- OpenAI offering delayed"},
    {"title": "Nvidia ships GPU", "url": "https://c.com/3",
     "short_summary": "Nvidia new GPU", "summary": "- Nvidia announced a GPU"},
]


def test_prepare_shortlists_only_near_pair(tmp_db, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    _seed(tmp_db, _THREE)

    def fake_embed(texts):
        # headlines 0 and 1 identical vectors; 2 orthogonal
        return [[1.0, 0.0], [1.0, 0.0], [0.0, 1.0]]

    with patch("lib.steps.coverage.embed_texts", side_effect=fake_embed):
        result = CliRunner().invoke(coverage_cli, [
            "--db", tmp_db, "--session", "v1", "--prepare-batches",
            "--shortlist-threshold", "0.7",
        ])
    assert result.exit_code == 0, result.output
    files = sorted(Path("runs/v1/coverage-batches").glob("batch-*.json"))
    assert len(files) == 1
    b0 = json.loads(files[0].read_text())
    assert b0["ids"] == ["0-1"]
    assert b0["pairs"][0]["a_summary"] == "- OpenAI delayed IPO"


def test_prepare_no_pairs_writes_no_batches(tmp_db, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    _seed(tmp_db, _THREE)

    def fake_embed(texts):
        return [[1.0, 0.0], [0.0, 1.0], [0.0, 0.0]]  # nothing ≥ 0.7 off-diagonal

    with patch("lib.steps.coverage.embed_texts", side_effect=fake_embed):
        result = CliRunner().invoke(coverage_cli, [
            "--db", tmp_db, "--session", "v1", "--prepare-batches",
            "--shortlist-threshold", "0.7",
        ])
    assert result.exit_code == 0, result.output
    d = Path("runs/v1/coverage-batches")
    files = list(d.glob("batch-*.json")) if d.exists() else []
    assert files == []


def test_apply_stamps_coverage_count_from_confirmed_group(tmp_db, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    _seed(tmp_db, _THREE)

    def fake_embed(texts):
        return [[1.0, 0.0], [1.0, 0.0], [0.0, 1.0]]

    with patch("lib.steps.coverage.embed_texts", side_effect=fake_embed):
        CliRunner().invoke(coverage_cli, [
            "--db", tmp_db, "--session", "v1", "--prepare-batches",
            "--shortlist-threshold", "0.7",
        ])

    results_dir = Path("runs/v1/coverage-results")
    results_dir.mkdir(parents=True)
    (results_dir / "batch-000.json").write_text(json.dumps({
        "results": [{"id": "0-1", "same": True}]
    }))

    result = CliRunner().invoke(coverage_cli, [
        "--db", tmp_db, "--session", "v1", "--apply-results", str(results_dir),
    ])
    assert result.exit_code == 0, result.output
    state = NewsletterAgentState(session_id="v1", db_path=tmp_db).load_latest_from_db()
    counts = [h.get("coverage_count") for h in state.headline_data]
    assert counts == [2, 2, 1]  # 0 and 1 grouped; 2 singleton


def test_apply_not_same_leaves_singletons(tmp_db, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    _seed(tmp_db, _THREE)

    def fake_embed(texts):
        return [[1.0, 0.0], [1.0, 0.0], [0.0, 1.0]]

    with patch("lib.steps.coverage.embed_texts", side_effect=fake_embed):
        CliRunner().invoke(coverage_cli, [
            "--db", tmp_db, "--session", "v1", "--prepare-batches",
            "--shortlist-threshold", "0.7",
        ])
    results_dir = Path("runs/v1/coverage-results")
    results_dir.mkdir(parents=True)
    (results_dir / "batch-000.json").write_text(json.dumps({
        "results": [{"id": "0-1", "same": False}]
    }))
    result = CliRunner().invoke(coverage_cli, [
        "--db", tmp_db, "--session", "v1", "--apply-results", str(results_dir),
    ])
    assert result.exit_code == 0, result.output
    state = NewsletterAgentState(session_id="v1", db_path=tmp_db).load_latest_from_db()
    assert [h.get("coverage_count") for h in state.headline_data] == [1, 1, 1]


def test_apply_no_candidates_completes_as_noop(tmp_db, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    _seed(tmp_db, [{"title": "x", "url": "u"}])  # no short_summary → no candidates
    result = CliRunner().invoke(coverage_cli, [
        "--db", tmp_db, "--session", "v1",
        "--apply-results", "runs/v1/coverage-results",
    ])
    assert result.exit_code == 0, result.output
    state = NewsletterAgentState(session_id="v1", db_path=tmp_db).load_latest_from_db()
    assert state.get_step("coverage").status.value == "complete"


def test_classic_mode_stamps_counts(tmp_db, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    _seed(tmp_db, _THREE)

    def fake_embed(texts):
        return [[1.0, 0.0], [1.0, 0.0], [0.0, 1.0]]

    def fake_call_prompt(name, inputs, *, engine=None):
        assert name == "same_story_sameday"
        ids = [p["id"] for p in inputs["pairs"]]
        return SameStorySamedayOutput(results=[
            SameStorySamedayVerdict(id=i, same=True) for i in ids
        ])

    with patch("lib.steps.coverage.embed_texts", side_effect=fake_embed), \
         patch("lib.steps.coverage.call_prompt", side_effect=fake_call_prompt):
        result = CliRunner().invoke(coverage_cli, [
            "--db", tmp_db, "--session", "v1",
            "--engine", "google:gemini-3.1-flash-lite",
            "--shortlist-threshold", "0.7",
        ])
    assert result.exit_code == 0, result.output
    state = NewsletterAgentState(session_id="v1", db_path=tmp_db).load_latest_from_db()
    assert [h.get("coverage_count") for h in state.headline_data] == [2, 2, 1]


def test_rejects_both_prepare_and_apply(tmp_db, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    _seed(tmp_db, [{"title": "A", "url": "u", "short_summary": "a", "summary": "b"}])
    result = CliRunner().invoke(coverage_cli, [
        "--db", tmp_db, "--session", "v1",
        "--prepare-batches", "--apply-results", "x",
    ])
    assert result.exit_code != 0
