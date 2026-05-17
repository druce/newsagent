from unittest.mock import patch
from click.testing import CliRunner
import lib.prompts  # register FILTER_URLS
from lib.db import init_db
from lib.state import NewsletterAgentState
from lib.steps.filter import cli as filter_cli
from lib.prompts.filter_urls import FilterUrlsOutput, HeadlineClassification


def _seed(tmp_db):
    init_db(tmp_db)
    state = NewsletterAgentState(session_id="f1", db_path=tmp_db)
    state.complete_step("init")
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

    import json
    from pathlib import Path
    report = json.loads(Path("runs/f1/filter.json").read_text())
    assert report["total"] == 2
    assert report["ai"] == 2
