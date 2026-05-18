"""Tests for lib/steps/run.py — top-level orchestrator."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest
from click.testing import CliRunner

from lib.db import init_db
from lib.state import NewsletterAgentState, WORKFLOW_STEPS

_ALL_STEP_IDS = [step_id for step_id, *_ in WORKFLOW_STEPS]
# Steps that accept --engine
_ENGINE_STEPS = {"filter", "summarize", "rate", "cluster", "select", "draft", "rewrite"}
# Steps that do NOT accept --engine
_NO_ENGINE_STEPS = {"init", "gather", "download", "send"}


# ---------------------------------------------------------------------------
# Helper: build a mock dict of step CLIs
# ---------------------------------------------------------------------------

def _make_mock_clis() -> dict:
    """Return a dict of step_id → MagicMock, each with a .main() method."""
    mocks = {}
    for sid in _ALL_STEP_IDS:
        m = MagicMock()
        m.main = MagicMock(return_value=0)
        mocks[sid] = m
    return mocks


# ---------------------------------------------------------------------------
# Test 1: Default run invokes all 11 steps in order
# ---------------------------------------------------------------------------

def test_run_all_steps_in_order(tmp_db, tmp_path, monkeypatch, sample_sources_yaml):
    """Running with --session + --sources calls all 11 step CLIs in workflow order."""
    monkeypatch.chdir(tmp_path)
    init_db(tmp_db)

    # Seed a minimal state so summary doesn't crash
    state = NewsletterAgentState(session_id="sess-all", db_path=tmp_db)
    state.save_checkpoint("init")

    mock_clis = _make_mock_clis()

    from lib.steps.run import cli as run_cli

    with patch("lib.steps.run._STEP_CLIS", mock_clis), \
         patch("lib.run_summary.write_summary", return_value=str(tmp_path / "summary.md")):
        runner = CliRunner()
        result = runner.invoke(run_cli, [
            "--db", tmp_db,
            "--session", "sess-all",
            "--sources", sample_sources_yaml,
            "--no-summary",
        ])

    assert result.exit_code == 0, result.output

    # All 11 steps called
    for step_id in _ALL_STEP_IDS:
        mock_clis[step_id].main.assert_called_once()

    # Verify order: each was called before the next
    call_order = []
    for step_id in _ALL_STEP_IDS:
        calls = mock_clis[step_id].main.call_args_list
        call_order.append((step_id, len(calls)))
    assert [sid for sid, n in call_order] == _ALL_STEP_IDS


# ---------------------------------------------------------------------------
# Test 2: --only filter runs just filter
# ---------------------------------------------------------------------------

def test_run_only_single_step(tmp_db, tmp_path, monkeypatch):
    """--only filter invokes only the filter step CLI."""
    monkeypatch.chdir(tmp_path)
    init_db(tmp_db)

    state = NewsletterAgentState(session_id="sess-only", db_path=tmp_db)
    state.save_checkpoint("init")

    mock_clis = _make_mock_clis()

    from lib.steps.run import cli as run_cli

    with patch("lib.steps.run._STEP_CLIS", mock_clis):
        runner = CliRunner()
        result = runner.invoke(run_cli, [
            "--db", tmp_db,
            "--session", "sess-only",
            "--only", "filter",
            "--no-summary",
        ])

    assert result.exit_code == 0, result.output

    # Only filter was called
    mock_clis["filter"].main.assert_called_once()

    # All other steps NOT called
    for step_id in _ALL_STEP_IDS:
        if step_id != "filter":
            mock_clis[step_id].main.assert_not_called()


# ---------------------------------------------------------------------------
# Test 3: --from cluster runs cluster and all subsequent steps
# ---------------------------------------------------------------------------

def test_run_from_step(tmp_db, tmp_path, monkeypatch):
    """--from cluster runs cluster, select, draft, rewrite, send (5 steps)."""
    monkeypatch.chdir(tmp_path)
    init_db(tmp_db)

    state = NewsletterAgentState(session_id="sess-from", db_path=tmp_db)
    state.save_checkpoint("init")

    mock_clis = _make_mock_clis()

    from lib.steps.run import cli as run_cli

    with patch("lib.steps.run._STEP_CLIS", mock_clis):
        runner = CliRunner()
        result = runner.invoke(run_cli, [
            "--db", tmp_db,
            "--session", "sess-from",
            "--from", "cluster",
            "--no-summary",
        ])

    assert result.exit_code == 0, result.output

    expected_called = ["cluster", "select", "draft", "rewrite", "send"]
    expected_skipped = ["init", "gather", "filter", "download", "summarize", "rate"]

    for step_id in expected_called:
        mock_clis[step_id].main.assert_called_once(), f"{step_id} should have been called"

    for step_id in expected_skipped:
        mock_clis[step_id].main.assert_not_called(), f"{step_id} should NOT have been called"


# ---------------------------------------------------------------------------
# Test 4: --engine propagates to LLM steps but NOT to init/gather/download/send
# ---------------------------------------------------------------------------

def test_run_engine_forwarded_to_llm_steps_only(tmp_db, tmp_path, monkeypatch, sample_sources_yaml):
    """--engine value appears in args for filter/summarize/rate/cluster/select/draft/rewrite,
    but NOT for init/gather/download/send."""
    monkeypatch.chdir(tmp_path)
    init_db(tmp_db)

    state = NewsletterAgentState(session_id="sess-eng", db_path=tmp_db)
    state.save_checkpoint("init")

    mock_clis = _make_mock_clis()

    from lib.steps.run import cli as run_cli

    engine = "openrouter:google/gemini-2.5-flash"

    with patch("lib.steps.run._STEP_CLIS", mock_clis):
        runner = CliRunner()
        result = runner.invoke(run_cli, [
            "--db", tmp_db,
            "--session", "sess-eng",
            "--sources", sample_sources_yaml,
            "--engine", engine,
            "--no-summary",
        ])

    assert result.exit_code == 0, result.output

    # Engine steps: --engine should appear in the args passed to .main()
    for step_id in _ENGINE_STEPS:
        call_args = mock_clis[step_id].main.call_args
        assert call_args is not None, f"{step_id}.main was not called"
        args_list = call_args[0][0]  # first positional arg to .main() is the args list
        assert "--engine" in args_list, (
            f"--engine not in args for {step_id}: {args_list}"
        )
        engine_idx = args_list.index("--engine")
        assert args_list[engine_idx + 1] == engine, (
            f"Wrong engine value for {step_id}: {args_list[engine_idx + 1]}"
        )

    # Non-engine steps: --engine must NOT appear
    for step_id in _NO_ENGINE_STEPS:
        call_args = mock_clis[step_id].main.call_args
        if call_args is None:
            continue  # step not called — that's fine (e.g., if a step was skipped)
        args_list = call_args[0][0]
        assert "--engine" not in args_list, (
            f"--engine incorrectly passed to {step_id}: {args_list}"
        )


# ---------------------------------------------------------------------------
# Test 5: --no-summary skips summary write
# ---------------------------------------------------------------------------

def test_run_no_summary_skips_write(tmp_db, tmp_path, monkeypatch):
    """--no-summary flag prevents write_summary from being called."""
    monkeypatch.chdir(tmp_path)
    init_db(tmp_db)

    state = NewsletterAgentState(session_id="sess-nosumm", db_path=tmp_db)
    state.save_checkpoint("init")

    mock_clis = _make_mock_clis()

    from lib.steps.run import cli as run_cli

    with patch("lib.steps.run._STEP_CLIS", mock_clis), \
         patch("lib.run_summary.write_summary") as mock_write:
        runner = CliRunner()
        result = runner.invoke(run_cli, [
            "--db", tmp_db,
            "--session", "sess-nosumm",
            "--only", "filter",
            "--no-summary",
        ])

    assert result.exit_code == 0, result.output
    mock_write.assert_not_called()
