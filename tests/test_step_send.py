import sqlite3
from pathlib import Path
from datetime import date
from click.testing import CliRunner

from lib.db import init_db
from lib.state import NewsletterAgentState
from lib.steps.send import cli as send_cli


def _setup(tmp_db, headline_count=2, final_newsletter=""):
    init_db(tmp_db)
    state = NewsletterAgentState(session_id="s1", db_path=tmp_db)
    state.complete_step("init")
    state.complete_step("gather")
    state.headline_data = [
        {"source": "S", "title": f"Title {i}", "url": f"https://e.com/{i}"}
        for i in range(headline_count)
    ]
    state.final_newsletter = final_newsletter
    state.newsletter_title = "Test Newsletter" if final_newsletter else ""
    state.save_checkpoint("gather")
    return state


def test_send_writes_html_to_out_dir_with_date(tmp_path, tmp_db, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _setup(tmp_db)
    runner = CliRunner()
    result = runner.invoke(send_cli, ["--db", tmp_db, "--session", "s1"])
    assert result.exit_code == 0, result.output

    today = date.today().isoformat()
    out_file = Path(f"out/{today}.html")
    assert out_file.exists()
    body = out_file.read_text()
    assert "<html" in body
    assert "Dummy" in body or "newsletter" in body.lower()


def test_send_writes_latest_symlink(tmp_path, tmp_db, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _setup(tmp_db)
    runner = CliRunner()
    runner.invoke(send_cli, ["--db", tmp_db, "--session", "s1"])

    latest = Path("out/latest.html")
    assert latest.is_symlink() or latest.exists()
    body = latest.read_text()
    assert "<html" in body


def test_send_inserts_newsletters_row(tmp_path, tmp_db, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _setup(tmp_db, final_newsletter="<p>my custom newsletter body</p>")
    runner = CliRunner()
    runner.invoke(send_cli, ["--db", tmp_db, "--session", "s1"])

    with sqlite3.connect(tmp_db) as conn:
        rows = conn.execute("SELECT session_id, title, html FROM newsletters").fetchall()
    assert len(rows) == 1
    assert rows[0][0] == "s1"
    assert "my custom newsletter" in rows[0][2]


def test_send_uses_final_newsletter_when_set(tmp_path, tmp_db, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _setup(tmp_db, final_newsletter="<p>real content</p>")
    runner = CliRunner()
    runner.invoke(send_cli, ["--db", tmp_db, "--session", "s1"])

    body = Path(f"out/{date.today().isoformat()}.html").read_text()
    assert "real content" in body


def test_send_notify_flag_errors_in_phase_2(tmp_path, tmp_db, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _setup(tmp_db)
    runner = CliRunner()
    result = runner.invoke(send_cli, ["--db", tmp_db, "--session", "s1", "--notify"])
    assert result.exit_code != 0
    assert "not implemented" in result.output.lower() or "phase" in result.output.lower()
