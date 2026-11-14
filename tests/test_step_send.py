import re
import sqlite3
import json
from pathlib import Path
from datetime import date
from unittest.mock import patch
from click.testing import CliRunner

from lib.db import init_db
from lib.state import NewsletterAgentState
from lib.steps.send import cli as send_cli


def _only_newsletter() -> Path:
    """The single stamped newsletter in out/, asserting there is exactly one."""
    written = list(Path("out").glob(f"{date.today().isoformat()}-*.html"))
    assert len(written) == 1, f"expected one stamped newsletter, got {written}"
    return written[0]


def _setup(tmp_db, headline_count=2, final_newsletter="", session_id="s1"):
    init_db(tmp_db)
    state = NewsletterAgentState(session_id=session_id, db_path=tmp_db)
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
    written = [p for p in Path("out").glob(f"{today}-*.html")]
    assert len(written) == 1, f"expected one stamped newsletter, got {written}"
    out_file = written[0]
    assert re.fullmatch(rf"{today}-\d{{6}}\.html", out_file.name)
    body = out_file.read_text()
    assert "<html" in body
    assert "Dummy" in body or "newsletter" in body.lower()


def test_send_filename_carries_the_session_stamp(tmp_path, tmp_db, monkeypatch):
    """A standard session id pins the filename to the session's start time."""
    monkeypatch.chdir(tmp_path)
    sid = "2026-07-27-18-00-07"
    _setup(tmp_db, session_id=sid)
    runner = CliRunner()
    result = runner.invoke(send_cli, ["--db", tmp_db, "--session", sid, "--no-email"])
    assert result.exit_code == 0, result.output
    assert Path("out/2026-07-27-180007.html").exists()


def test_send_rerun_overwrites_rather_than_duplicating(tmp_path, tmp_db, monkeypatch):
    monkeypatch.chdir(tmp_path)
    sid = "2026-07-27-18-00-07"
    _setup(tmp_db, session_id=sid)
    runner = CliRunner()
    for _ in range(2):
        runner.invoke(send_cli, ["--db", tmp_db, "--session", sid, "--no-email"])
    assert len(list(Path("out").glob("2026-*.html"))) == 1


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

    body = _only_newsletter().read_text()
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


def test_render_item_includes_bluesky_share_link():
    from lib.steps.send import _render_item, _QUEUE_PORT

    html = _render_item({
        "headline": "Anthropic raises a round",
        "sources": [("NYT", "https://nyt.com/a?x=1")],
    })
    # butterfly glyph + enqueue link to the local daemon, url-encoded.
    # Literal 127.0.0.1, never `localhost` — that resolves to ::1 as well and
    # the daemon binds IPv4 only.
    assert "\U0001f98b" in html
    assert f"http://127.0.0.1:{_QUEUE_PORT}/enqueue" in html
    assert "u=https%3A%2F%2Fnyt.com%2Fa%3Fx%3D1" in html
    assert "t=Anthropic%20raises%20a%20round" in html


def test_render_item_omits_share_link_without_source():
    from lib.steps.send import _render_item

    html = _render_item({"headline": "No source story", "sources": []})
    assert "/enqueue" not in html
    assert "\U0001f98b" not in html


def test_render_html_embeds_the_share_client_script():
    """The newsletter shell ships the shared client once, after the rows."""
    from lib.steps.send import _render_html, _QUEUE_PORT

    html = _render_html("T", "## Section\n- A story — [NYT](https://nyt.com/a)")
    assert "__bskyQ" in html
    assert "bsky-daemon-banner" in html
    assert ".venv/bin/python -m lib.steps.bsky_share" in html
    assert f'var PORT = "{_QUEUE_PORT}"' in html      # matches the anchor port
    assert html.index("__bskyQ") < html.index("</body>")


def test_render_html_omits_script_when_no_share_links():
    """No 🦋 anchors => no client, so a stopped daemon can't pin a banner on a
    page with nothing to queue."""
    from lib.steps.send import _render_html

    html = _render_html("T", "## Section\n- A story with no source")
    assert "__bskyQ" not in html
    assert "bsky-daemon-banner" not in html


# ── cross-day dedup store: recording survivors ──────────────────────

def _setup_with_sections(tmp_db):
    init_db(tmp_db)
    state = NewsletterAgentState(session_id="s1", db_path=tmp_db)
    state.complete_step("start")
    state.headline_data = [
        {"title": "Title 0", "url": "https://e.com/0",
         "short_summary": "Story zero about AI."},
        {"title": "Title 1", "url": "https://e.com/1",
         "short_summary": "Story one about AI."},
    ]
    state.newsletter_section_data = [
        {"cat": "AI", "headline": "Title 0", "link": "https://e.com/0",
         "summary": "s0", "id": 0},
        {"cat": "AI", "headline": "Title 1", "link": "https://e.com/1",
         "summary": "s1", "id": 1},
    ]
    state.final_newsletter = "<p>body</p>"
    state.newsletter_title = "Test Newsletter"
    state.save_checkpoint("start")
    return state


def test_send_records_published_articles(tmp_path, tmp_db, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _setup_with_sections(tmp_db)

    def fake_embed(texts):
        return [[0.1, 0.2] for _ in texts]

    with patch("lib.published.embed_texts", side_effect=fake_embed):
        runner = CliRunner()
        result = runner.invoke(send_cli, ["--db", tmp_db, "--session", "s1", "--no-email"])
    assert result.exit_code == 0, result.output

    with sqlite3.connect(tmp_db) as conn:
        rows = conn.execute(
            "SELECT url, title, short_summary, embedding FROM published_articles "
            "ORDER BY url"
        ).fetchall()
    assert len(rows) == 2
    assert rows[0][0] == "https://e.com/0"
    assert rows[0][2] == "Story zero about AI."
    assert json.loads(rows[0][3]) == [0.1, 0.2]  # embedding stored, non-null


def test_send_recording_failure_is_swallowed(tmp_path, tmp_db, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _setup_with_sections(tmp_db)

    def boom(texts):
        raise RuntimeError("no OpenAI key")

    with patch("lib.published.embed_texts", side_effect=boom):
        runner = CliRunner()
        result = runner.invoke(send_cli, ["--db", tmp_db, "--session", "s1", "--no-email"])
    # Send still succeeds; recording error is a warning, not a failure.
    assert result.exit_code == 0, result.output
    assert _only_newsletter().exists()
    with sqlite3.connect(tmp_db) as conn:
        n = conn.execute("SELECT COUNT(*) FROM published_articles").fetchone()[0]
    assert n == 0


def test_send_records_articles_after_a_real_draft(tmp_path, tmp_db, monkeypatch):
    """Regression: draft used to overwrite newsletter_section_data with
    {cat, section_markdown} dicts, so send's recorder found no ids/links and
    silently wrote 0 rows — crossdedupe was a no-op for every session."""
    from lib.steps.draft import cli as draft_cli

    monkeypatch.chdir(tmp_path)
    state = _setup_with_sections(tmp_db)
    for step in ("gather", "filter", "download", "summarize", "rate", "cluster", "select"):
        state.complete_step(step)
    state.save_checkpoint("select")

    runner = CliRunner()
    r = runner.invoke(draft_cli, ["--db", tmp_db, "--session", "s1", "--prepare-batches"])
    assert r.exit_code == 0, r.output
    results_dir = Path("runs/s1/draft-results")
    results_dir.mkdir(parents=True)
    (results_dir / "batch-000.json").write_text(json.dumps({
        "cat": "AI", "final_section_markdown": "## AI\n- fake headline",
        "iterations": 1, "scores": [9.0], "feedbacks": ["ok"], "accepted": True,
    }))
    r = runner.invoke(draft_cli, ["--db", tmp_db, "--session", "s1",
                                  "--apply-results", str(results_dir)])
    assert r.exit_code == 0, r.output

    with patch("lib.published.embed_texts", side_effect=lambda t: [[0.5] for _ in t]):
        r = runner.invoke(send_cli, ["--db", tmp_db, "--session", "s1", "--no-email"])
    assert r.exit_code == 0, r.output
    assert "Recorded 2 article(s)" in r.output

    with sqlite3.connect(tmp_db) as conn:
        urls = [row[0] for row in conn.execute(
            "SELECT url FROM published_articles ORDER BY url")]
    assert urls == ["https://e.com/0", "https://e.com/1"]


def test_send_warns_when_nothing_is_recorded(tmp_path, tmp_db, monkeypatch):
    """A non-empty newsletter that records zero articles is a bug in the
    upstream state, not a quiet success — say so on stderr."""
    monkeypatch.chdir(tmp_path)
    state = _setup_with_sections(tmp_db)
    state.newsletter_section_data = [{"cat": "AI", "section_markdown": "## AI\n- x"}]
    state.save_checkpoint("start")

    with patch("lib.published.embed_texts", side_effect=lambda t: [[0.5] for _ in t]):
        runner = CliRunner()
        result = runner.invoke(send_cli, ["--db", tmp_db, "--session", "s1", "--no-email"])
    assert result.exit_code == 0, result.output
    assert "Warning" in result.output and "0 article" in result.output


def test_send_stamps_rows_recorded_by_select(tmp_path, tmp_db, monkeypatch):
    """select already wrote the session's rows (published_at NULL); send only
    stamps the selected ones — no re-embedding."""
    from lib.published import record_session_articles

    monkeypatch.chdir(tmp_path)
    state = _setup_with_sections(tmp_db)
    state.headline_data.append({"title": "Unpicked", "url": "https://e.com/9",
                                "short_summary": "nine", "rating": 0.5})
    state.save_checkpoint("start")
    with patch("lib.published.embed_texts", side_effect=lambda t: [[0.3] for _ in t]):
        record_session_articles(state, "s1", tmp_db)

    def no_embed(texts):
        raise AssertionError("send must not re-embed")

    with patch("lib.published.embed_texts", side_effect=no_embed):
        runner = CliRunner()
        r1 = runner.invoke(send_cli, ["--db", tmp_db, "--session", "s1", "--no-email"])
        assert r1.exit_code == 0, r1.output
        assert "Recorded 2 article(s)" in r1.output
        with sqlite3.connect(tmp_db) as conn:
            first = dict(conn.execute("SELECT url, published_at FROM published_articles"))
        r2 = runner.invoke(send_cli, ["--db", tmp_db, "--session", "s1", "--no-email"])
        assert r2.exit_code == 0, r2.output
    with sqlite3.connect(tmp_db) as conn:
        second = dict(conn.execute("SELECT url, published_at FROM published_articles"))
    assert first["https://e.com/0"] and first["https://e.com/1"]
    assert first["https://e.com/9"] is None          # unselected never stamped
    assert second == first                           # second send doesn't re-stamp
