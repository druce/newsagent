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
    state.complete_step("start")
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
    result = runner.invoke(send_cli, ["--db", tmp_db, "--session", "s1", "--no-email"])
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
    runner.invoke(send_cli, ["--db", tmp_db, "--session", "s1", "--no-email"])

    latest = Path("out/latest.html")
    assert latest.is_symlink() or latest.exists()
    body = latest.read_text()
    assert "<html" in body


def test_send_inserts_newsletters_row(tmp_path, tmp_db, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _setup(tmp_db, final_newsletter="<p>my custom newsletter body</p>")
    runner = CliRunner()
    runner.invoke(send_cli, ["--db", tmp_db, "--session", "s1", "--no-email"])

    with sqlite3.connect(tmp_db) as conn:
        rows = conn.execute("SELECT session_id, title, html FROM newsletters").fetchall()
    assert len(rows) == 1
    assert rows[0][0] == "s1"
    assert "my custom newsletter" in rows[0][2]


def test_send_uses_final_newsletter_when_set(tmp_path, tmp_db, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _setup(tmp_db, final_newsletter="<p>real content</p>")
    runner = CliRunner()
    runner.invoke(send_cli, ["--db", tmp_db, "--session", "s1", "--no-email"])

    body = Path(f"out/{date.today().isoformat()}.html").read_text()
    assert "real content" in body


def test_send_calls_gmail_when_creds_set(tmp_path, tmp_db, monkeypatch):
    """With GMAIL_USER + GMAIL_PASSWORD set, send_gmail is invoked with the title."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GMAIL_USER", "me@example.com")
    monkeypatch.setenv("GMAIL_PASSWORD", "app-password")
    _setup(tmp_db, final_newsletter="<p>real content</p>")

    calls = []

    def fake_send_gmail(subject, html, *, to=None):
        calls.append({"subject": subject, "html": html, "to": to})

    monkeypatch.setattr("lib.steps.send.send_gmail", fake_send_gmail)

    runner = CliRunner()
    result = runner.invoke(send_cli, ["--db", tmp_db, "--session", "s1"])
    assert result.exit_code == 0, result.output
    assert len(calls) == 1
    assert "Test Newsletter" in calls[0]["subject"]
    assert "real content" in calls[0]["html"]
    assert calls[0]["to"] is None  # defaults to GMAIL_USER inside send_gmail


def test_send_skips_email_when_no_creds(tmp_path, tmp_db, monkeypatch):
    """Without GMAIL_USER/GMAIL_PASSWORD, send_gmail raises and we log + continue."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GMAIL_USER", raising=False)
    monkeypatch.delenv("GMAIL_PASSWORD", raising=False)
    # Prevent load_dotenv() from walking up and finding the project .env
    monkeypatch.setattr("lib.steps.send.load_dotenv", lambda *a, **kw: None)
    _setup(tmp_db, final_newsletter="<p>content</p>")

    runner = CliRunner()
    result = runner.invoke(send_cli, ["--db", tmp_db, "--session", "s1"])
    assert result.exit_code == 0, result.output
    assert "Skipped email" in result.output


def test_send_skips_email_on_second_call_for_same_session(tmp_path, tmp_db, monkeypatch):
    """Second invocation for the same session must not re-email (avoids pipeline double-send)."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GMAIL_USER", "me@example.com")
    monkeypatch.setenv("GMAIL_PASSWORD", "app-password")
    _setup(tmp_db, final_newsletter="<p>content</p>")

    calls = []
    monkeypatch.setattr(
        "lib.steps.send.send_gmail",
        lambda subject, html, *, to=None: calls.append((subject, to)),
    )

    runner = CliRunner()
    runner.invoke(send_cli, ["--db", tmp_db, "--session", "s1"])
    runner.invoke(send_cli, ["--db", tmp_db, "--session", "s1"])
    assert len(calls) == 1, f"expected exactly one email, got {len(calls)}"


def test_send_force_email_overrides_idempotency_guard(tmp_path, tmp_db, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GMAIL_USER", "me@example.com")
    monkeypatch.setenv("GMAIL_PASSWORD", "app-password")
    _setup(tmp_db, final_newsletter="<p>content</p>")

    calls = []
    monkeypatch.setattr(
        "lib.steps.send.send_gmail",
        lambda subject, html, *, to=None: calls.append((subject, to)),
    )

    runner = CliRunner()
    runner.invoke(send_cli, ["--db", tmp_db, "--session", "s1"])
    runner.invoke(send_cli, ["--db", tmp_db, "--session", "s1", "--force-email"])
    assert len(calls) == 2
