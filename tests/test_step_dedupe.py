from unittest.mock import patch
from click.testing import CliRunner
from lib.db import init_db
from lib.state import NewsletterAgentState
from lib.steps.dedupe import cli as dedupe_cli


def _seed(tmp_db, tmp_path):
    init_db(tmp_db)
    from pathlib import Path
    dl = Path(tmp_path) / "download"
    dl.mkdir(exist_ok=True)
    body_a = dl / "a.txt"; body_a.write_text("Story A about chip supply chain " * 50)
    body_b = dl / "b.txt"; body_b.write_text("Story A about chip supply chain " * 50)
    body_c = dl / "c.txt"; body_c.write_text("Different story about coral reefs " * 50)
    state = NewsletterAgentState(session_id="d1", db_path=tmp_db)
    state.complete_step("init")
    state.complete_step("gather")
    state.complete_step("filter")
    state.complete_step("download")
    state.headline_data = [
        {"id": 0, "title": "T1", "url": "https://e.com/a",
         "summary": "alpha beta gamma", "text_path": str(body_a)},
        {"id": 1, "title": "T1b", "url": "https://e.com/b",
         "summary": "alpha beta gamma", "text_path": str(body_b)},  # near-dupe body
        {"id": 2, "title": "T2", "url": "https://e.com/c",
         "summary": "different topic", "text_path": str(body_c)},
    ]
    state.save_checkpoint("download")


def test_dedupe_drops_near_duplicates(tmp_db, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    _seed(tmp_db, tmp_path)
    # First two are identical vectors; third is orthogonal
    vectors = [[1.0, 0.0], [1.0, 0.0], [0.0, 1.0]]
    with patch("lib.steps.dedupe.embed_texts", return_value=vectors):
        runner = CliRunner()
        result = runner.invoke(dedupe_cli, ["--db", tmp_db, "--session", "d1"])
    assert result.exit_code == 0, result.output
    state = NewsletterAgentState(session_id="d1", db_path=tmp_db).load_latest_from_db()
    urls = [h["url"] for h in state.headline_data]
    assert len(urls) == 2
    assert "https://e.com/c" in urls


def test_dedupe_preserves_embeddings(tmp_db, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    _seed(tmp_db, tmp_path)
    vectors = [[1.0, 0.0], [0.5, 0.5], [0.0, 1.0]]  # none near-duplicate
    with patch("lib.steps.dedupe.embed_texts", return_value=vectors):
        runner = CliRunner()
        runner.invoke(dedupe_cli, ["--db", tmp_db, "--session", "d1"])
    state = NewsletterAgentState(session_id="d1", db_path=tmp_db).load_latest_from_db()
    assert all("embedding" in h for h in state.headline_data)
    assert len(state.headline_data) == 3


def test_dedupe_no_summaries_noop(tmp_db, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    init_db(tmp_db)
    state = NewsletterAgentState(session_id="empty", db_path=tmp_db)
    state.complete_step("init")
    state.save_checkpoint("init")
    runner = CliRunner()
    result = runner.invoke(dedupe_cli, ["--db", tmp_db, "--session", "empty"])
    assert result.exit_code == 0
    assert "nothing" in result.output.lower() or "0" in result.output
