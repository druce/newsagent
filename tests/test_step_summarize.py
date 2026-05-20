import json
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

import lib.prompts  # register all prompts
from lib.db import init_db
from lib.state import NewsletterAgentState
from lib.steps.summarize import cli as summarize_cli
from lib.prompts.extract_summaries import ExtractSummariesOutput, ArticleSummary


def _seed(tmp_db, tmp_path):
    """Seed a session with two headlines: one with text_path, one without."""
    init_db(tmp_db)

    # Write a fake article text file
    article_file = tmp_path / "article_0.txt"
    article_file.write_text("This is a detailed AI article about GPT-6 capabilities and market impact.")

    state = NewsletterAgentState(session_id="s1", db_path=tmp_db)
    state.complete_step("init")
    state.complete_step("gather")
    state.complete_step("filter")
    state.complete_step("download")
    state.headline_data = [
        {
            "source": "S",
            "title": "OpenAI ships GPT-6",
            "url": "https://e.com/a",
            "is_ai": True,
            "text_path": str(article_file),
        },
        {
            "source": "S",
            "title": "Stock market roundup",
            "url": "https://e.com/b",
            "is_ai": False,
            # No text_path — download failed or not applicable
        },
    ]
    state.save_checkpoint("download")
    return state


def test_summarize_writes_summaries(tmp_db, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    _seed(tmp_db, tmp_path)

    def fake_call_prompt(name, inputs, *, engine=None):
        assert name == "extract_summaries"
        ids = [it["id"] for it in inputs["items"]]
        return ExtractSummariesOutput(summaries=[
            ArticleSummary(
                id=ids[0],
                short_summary="OpenAI ships GPT-6 with strong market impact",
                summary="- GPT-6 launched\n- Strong market impact\n- Future outlook positive",
            ),
        ])

    with patch("lib.steps.summarize.call_prompt", side_effect=fake_call_prompt):
        runner = CliRunner()
        result = runner.invoke(summarize_cli, ["--db", tmp_db, "--session", "s1"])

    assert result.exit_code == 0, result.output

    state = NewsletterAgentState(session_id="s1", db_path=tmp_db).load_latest_from_db()
    # Headline with text_path should have both summary and short_summary
    headlines_with_summary = [h for h in state.headline_data if "summary" in h]
    assert len(headlines_with_summary) == 1
    assert "GPT-6" in headlines_with_summary[0]["summary"]
    assert "GPT-6" in headlines_with_summary[0]["short_summary"]


def test_summarize_skips_no_text_path(tmp_db, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    _seed(tmp_db, tmp_path)

    call_count = 0

    def fake_call_prompt(name, inputs, *, engine=None):
        nonlocal call_count
        call_count += 1
        ids = [it["id"] for it in inputs["items"]]
        return ExtractSummariesOutput(summaries=[
            ArticleSummary(id=i, short_summary="One-line headline", summary="- bullet") for i in ids
        ])

    with patch("lib.steps.summarize.call_prompt", side_effect=fake_call_prompt):
        runner = CliRunner()
        runner.invoke(summarize_cli, ["--db", tmp_db, "--session", "s1"])

    # Only the headline with text_path should be included in the prompt batch
    # The second headline (no text_path) is skipped
    state = NewsletterAgentState(session_id="s1", db_path=tmp_db).load_latest_from_db()
    no_text_path = [h for h in state.headline_data if "text_path" not in h]
    assert all("summary" not in h for h in no_text_path)


def test_summarize_writes_report(tmp_db, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    _seed(tmp_db, tmp_path)

    def fake_call_prompt(name, inputs, *, engine=None):
        ids = [it["id"] for it in inputs["items"]]
        return ExtractSummariesOutput(summaries=[
            ArticleSummary(
                id=i,
                short_summary="One-line headline for testing",
                summary="- bullet one\n- bullet two\n- bullet three",
            ) for i in ids
        ])

    with patch("lib.steps.summarize.call_prompt", side_effect=fake_call_prompt):
        runner = CliRunner()
        runner.invoke(summarize_cli, ["--db", tmp_db, "--session", "s1"])

    report = json.loads(Path("runs/s1/summarize.json").read_text())
    assert report["session_id"] == "s1"
    assert report["total"] == 1   # only 1 headline had text_path
    assert report["summarized"] == 1
