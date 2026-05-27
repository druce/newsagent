import json
from pathlib import Path
from unittest.mock import patch
from click.testing import CliRunner
import lib.prompts  # register FILTER_URLS  # noqa: F401
from lib.db import init_db
from lib.state import NewsletterAgentState
from lib.steps.filter import cli as filter_cli
from lib.prompts.filter_urls import FilterUrlsOutput, HeadlineClassification


def _seed(tmp_db):
    init_db(tmp_db)
    state = NewsletterAgentState(session_id="f1", db_path=tmp_db)
    state.complete_step("start")
    state.complete_step("gather")
    state.headline_data = [
        {"source": "S", "title": "OpenAI ships GPT-6", "url": "https://e.com/a"},
        {"source": "S", "title": "Stock market roundup", "url": "https://e.com/b"},
    ]
    state.save_checkpoint("gather")


def test_filter_marks_is_ai_via_prompt(tmp_db, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    _seed(tmp_db)

    def fake_call_prompt(name, inputs, *, engine=None):
        assert name == "filter_urls"
        # inputs is a dict that conforms to FilterUrlsInput
        ids = [it["id"] for it in inputs["items"]]
        return FilterUrlsOutput(classifications=[
            HeadlineClassification(id=ids[0], is_ai=True),
            HeadlineClassification(id=ids[1], is_ai=False),
        ])

    with patch("lib.steps.filter.call_prompt", side_effect=fake_call_prompt):
        runner = CliRunner()
        result = runner.invoke(filter_cli, ["--db", tmp_db, "--session", "f1", "--keep-non-ai"])
    assert result.exit_code == 0, result.output

    state = NewsletterAgentState(session_id="f1", db_path=tmp_db).load_latest_from_db()
    flags = [h["is_ai"] for h in state.headline_data]
    assert flags == [True, False]


def test_filter_drops_non_ai_by_default(tmp_db, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    _seed(tmp_db)

    def fake_call_prompt(name, inputs, *, engine=None):
        ids = [it["id"] for it in inputs["items"]]
        return FilterUrlsOutput(classifications=[
            HeadlineClassification(id=ids[0], is_ai=True),
            HeadlineClassification(id=ids[1], is_ai=False),
        ])

    with patch("lib.steps.filter.call_prompt", side_effect=fake_call_prompt):
        runner = CliRunner()
        runner.invoke(filter_cli, ["--db", tmp_db, "--session", "f1"])

    state = NewsletterAgentState(session_id="f1", db_path=tmp_db).load_latest_from_db()
    assert len(state.headline_data) == 1
    assert state.headline_data[0]["is_ai"] is True


def test_filter_writes_report(tmp_db, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    _seed(tmp_db)

    def fake_call_prompt(name, inputs, *, engine=None):
        ids = [it["id"] for it in inputs["items"]]
        return FilterUrlsOutput(classifications=[
            HeadlineClassification(id=i, is_ai=True) for i in ids
        ])

    with patch("lib.steps.filter.call_prompt", side_effect=fake_call_prompt):
        runner = CliRunner()
        runner.invoke(filter_cli, ["--db", tmp_db, "--session", "f1"])

    report = json.loads(Path("runs/f1/filter.json").read_text())
    assert report["total"] == 2
    assert report["ai"] == 2


def test_filter_prepare_writes_self_contained_batches(tmp_db, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    _seed(tmp_db)

    runner = CliRunner()
    result = runner.invoke(filter_cli, [
        "--db", tmp_db, "--session", "f1",
        "--prepare-batches", "--batch-size", "1",
    ])
    assert result.exit_code == 0, result.output

    batches_dir = Path("runs/f1/filter-batches")
    files = sorted(batches_dir.glob("batch-*.json"))
    assert len(files) == 2  # one per headline at batch-size 1

    b0 = json.loads(files[0].read_text())
    assert b0["batch_id"] == 0
    assert b0["ids"] == ["0"]
    assert b0["items"][0]["title"] == "OpenAI ships GPT-6"
    # Self-contained prompt + schema for the subagent
    assert "Classify every headline below" in b0["user_prompt"]
    assert "system_prompt" in b0 and b0["system_prompt"]
    assert b0["output_schema"]["properties"].get("classifications") is not None


def test_filter_apply_reads_results_and_updates_state(tmp_db, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    _seed(tmp_db)

    # Simulate what a subagent would write
    results_dir = Path("runs/f1/filter-results")
    results_dir.mkdir(parents=True)
    (results_dir / "batch-000.json").write_text(json.dumps({
        "classifications": [
            {"id": "0", "is_ai": True},
            {"id": "1", "is_ai": False},
        ]
    }))

    runner = CliRunner()
    result = runner.invoke(filter_cli, [
        "--db", tmp_db, "--session", "f1",
        "--apply-results", str(results_dir),
    ])
    assert result.exit_code == 0, result.output

    state = NewsletterAgentState(session_id="f1", db_path=tmp_db).load_latest_from_db()
    assert state is not None
    # Default drops non-AI
    assert len(state.headline_data) == 1
    assert state.headline_data[0]["is_ai"] is True


def test_filter_apply_reports_missing_ids(tmp_db, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    _seed(tmp_db)

    results_dir = Path("runs/f1/filter-results")
    results_dir.mkdir(parents=True)
    # Only classify one of two headlines
    (results_dir / "batch-000.json").write_text(json.dumps({
        "classifications": [{"id": "0", "is_ai": True}]
    }))

    runner = CliRunner()
    result = runner.invoke(filter_cli, [
        "--db", tmp_db, "--session", "f1",
        "--apply-results", str(results_dir),
    ])
    # Partial success: applies what it has, but reports problem on stderr
    assert result.exit_code == 0, result.output
    combined = result.output + (result.stderr if result.stderr_bytes else "")
    assert "missing classifications" in combined


def test_filter_apply_fails_when_results_dir_empty(tmp_db, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    _seed(tmp_db)

    results_dir = Path("runs/f1/filter-results")
    results_dir.mkdir(parents=True)

    runner = CliRunner()
    result = runner.invoke(filter_cli, [
        "--db", tmp_db, "--session", "f1",
        "--apply-results", str(results_dir),
    ])
    assert result.exit_code != 0


def test_filter_rejects_both_prepare_and_apply(tmp_db, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    _seed(tmp_db)

    runner = CliRunner()
    result = runner.invoke(filter_cli, [
        "--db", tmp_db, "--session", "f1",
        "--prepare-batches", "--apply-results", "x",
    ])
    assert result.exit_code != 0
    assert "mutually exclusive" in (result.output + (result.stderr or ""))
