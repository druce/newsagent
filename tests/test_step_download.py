import json
import sqlite3
from pathlib import Path
from unittest.mock import patch
from click.testing import CliRunner

from lib.db import init_db
from lib.state import NewsletterAgentState
from lib.steps.download import cli as download_cli


def _setup(tmp_db, monkeypatch_chdir):
    init_db(tmp_db)
    state = NewsletterAgentState(session_id="d1", db_path=tmp_db)
    state.complete_step("init")
    state.complete_step("gather")
    state.headline_data = [
        {"source": "S", "title": "T1", "url": "https://example.com/a"},
        {"source": "S", "title": "T2", "url": "https://example.com/b"},
    ]
    state.save_checkpoint("gather")
    return state


def test_download_fetches_and_extracts_each_article(tmp_path, tmp_db, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _setup(tmp_db, monkeypatch)

    html_a = "<html><body><article>Body text A long enough</article></body></html>"
    html_b = "<html><body><article>Body text B long enough</article></body></html>"

    def fake_fetch(url, **_):
        return html_a if url.endswith("/a") else html_b

    with patch("lib.steps.download.fetch_url_html", side_effect=fake_fetch):
        runner = CliRunner()
        result = runner.invoke(download_cli, ["--db", tmp_db, "--session", "d1"])
    assert result.exit_code == 0, result.output

    state = NewsletterAgentState(session_id="d1", db_path=tmp_db).load_latest_from_db()
    assert all("text_path" in h and h["text_path"] for h in state.headline_data)
    for h in state.headline_data:
        p = Path(h["text_path"])
        assert p.exists()
        assert "Body text" in p.read_text()


def test_download_respects_max_flag(tmp_path, tmp_db, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _setup(tmp_db, monkeypatch)

    with patch("lib.steps.download.fetch_url_html",
               return_value="<html><article>txt long enough</article></html>") as pw:
        runner = CliRunner()
        runner.invoke(download_cli, ["--db", tmp_db, "--session", "d1", "--max", "1"])
    assert pw.call_count == 1


def test_download_skips_already_downloaded(tmp_path, tmp_db, monkeypatch):
    monkeypatch.chdir(tmp_path)
    state = _setup(tmp_db, monkeypatch)
    state.headline_data[0]["text_path"] = "download/existing.txt"
    state.save_checkpoint("gather")
    Path("download").mkdir()
    Path("download/existing.txt").write_text("already there")

    with patch("lib.steps.download.fetch_url_html",
               return_value="<html><article>txt long enough</article></html>") as pw:
        runner = CliRunner()
        runner.invoke(download_cli, ["--db", tmp_db, "--session", "d1"])
    assert pw.call_count == 1  # only the second one


def test_download_writes_report(tmp_path, tmp_db, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _setup(tmp_db, monkeypatch)

    with patch("lib.steps.download.fetch_url_html",
               return_value="<html><article>txt long enough</article></html>"):
        runner = CliRunner()
        runner.invoke(download_cli, ["--db", tmp_db, "--session", "d1"])

    report = json.loads(Path("runs/d1/download.json").read_text())
    assert report["downloaded"] == 2
