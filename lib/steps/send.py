"""newsagent:send — render newsletter as HTML, write to out/, email via Gmail."""
from __future__ import annotations

import re
import sqlite3
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Optional

import click
import markdown as md
from dotenv import load_dotenv

from lib.state import NewsletterAgentState
from lib.utilities import send_gmail


_OUT_DIR = Path("out")
_LEADING_H1_RE = re.compile(r"\A(?:\s*#\s+[^\n]*\n+)+", re.MULTILINE)


def _markdown_to_html(body_md: str) -> str:
    """Convert the rewrite step's markdown body into HTML.

    Strips any leading top-level `# Title` lines (the rewrite step often
    emits the title at the top of the body even though the template wrapper
    already renders it as <h1>), then runs python-markdown with the
    extra/sane_lists/smarty extensions so [link](url), `**bold**`, and
    `- bullet` lists render as real HTML elements.
    """
    body = _LEADING_H1_RE.sub("", body_md or "")
    return md.markdown(
        body,
        extensions=["extra", "sane_lists", "smarty"],
        output_format="html",
    )


def _render_html(title: str, body_md: str, headline_count: int) -> str:
    body_html = _markdown_to_html(body_md)
    safe_title = title or "AI Newsletter"
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>{safe_title}</title>
  <style>
    body {{ font-family: -apple-system, sans-serif; max-width: 720px; margin: 2em auto; padding: 0 1em; line-height: 1.5; color: #222; }}
    h1 {{ border-bottom: 2px solid #333; padding-bottom: .3em; }}
    h2 {{ margin-top: 1.8em; border-bottom: 1px solid #ddd; padding-bottom: .2em; }}
    a {{ color: #0366d6; text-decoration: none; }}
    a:hover {{ text-decoration: underline; }}
    ul {{ padding-left: 1.2em; }}
    li {{ margin-bottom: .5em; }}
    .meta {{ color: #666; font-size: .9em; }}
  </style>
</head>
<body>
  <h1>{safe_title}</h1>
  <p class="meta">{datetime.now().isoformat()} — {headline_count} headlines</p>
  {body_html}
</body>
</html>
"""


def _already_sent(db_path: str, session_id: str) -> bool:
    """True if a newsletters row already exists for this session."""
    try:
        with sqlite3.connect(db_path) as conn:
            row = conn.execute(
                "SELECT 1 FROM newsletters WHERE session_id = ? LIMIT 1",
                (session_id,),
            ).fetchone()
    except sqlite3.Error:
        return False
    return row is not None


def deliver_newsletter(
    session_id: str,
    db_path: str = "newsletter_agent.db",
    *,
    to: Optional[str] = None,
    no_email: bool = False,
    force_email: bool = False,
    echo: bool = True,
) -> Path:
    """Render + write the newsletter HTML and (optionally) email it.

    Idempotent: re-renders the HTML and updates `out/<date>.html` every
    call. The Gmail send is skipped automatically if a `newsletters` row
    already exists for this session (so calling from both `rewrite` and
    `send` does not double-deliver). Pass `force_email=True` to override.

    Returns the path to the written HTML file. Raises ClickException on
    missing state. Email failures are reported but do not raise.
    """
    load_dotenv()
    state = NewsletterAgentState(session_id=session_id, db_path=db_path).load_latest_from_db()
    if state is None:
        raise click.ClickException(f"No state found for session {session_id}")

    body = state.final_newsletter or (
        "<p><em>Dummy newsletter — pipeline ran through gather/download/send "
        "without the LLM steps.</em></p>"
    )
    title = state.newsletter_title or "AI Newsletter"
    html = _render_html(title, body, len(state.headline_data))

    _OUT_DIR.mkdir(exist_ok=True)
    today = date.today().isoformat()
    out_file = _OUT_DIR / f"{today}.html"
    out_file.write_text(html)

    latest = _OUT_DIR / "latest.html"
    if latest.exists() or latest.is_symlink():
        latest.unlink()
    try:
        latest.symlink_to(out_file.name)
    except OSError:
        latest.write_text(html)

    previously_sent = _already_sent(db_path, session_id)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO newsletters (session_id, title, html, sent_at, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (session_id, title, html, None, datetime.now().isoformat()),
        )
        conn.commit()

    if echo:
        click.echo(f"Newsletter written to {out_file}")

    if no_email:
        return out_file
    if previously_sent and not force_email:
        if echo:
            click.echo(
                "Email skipped: newsletter already delivered for this session "
                "(pass --force-email to re-send)."
            )
        return out_file

    subject = f"{title} — {today}"
    try:
        send_gmail(subject, html, to=to)
        if echo:
            click.echo(f"Emailed newsletter to {to or '<GMAIL_USER>'}.")
    except RuntimeError as e:
        if echo:
            click.echo(
                f"Skipped email ({e}). Pass --to or set GMAIL_USER/GMAIL_PASSWORD.",
                err=True,
            )
    return out_file


@click.command()
@click.option("--db", "db_path", default="newsletter_agent.db")
@click.option("--session", "session_id", required=True)
@click.option("--to", "email_to", default=None,
              help="Email recipient for the newsletter (defaults to GMAIL_USER).")
@click.option("--no-email", "no_email", is_flag=True,
              help="Write out/<date>.html but skip sending email.")
@click.option("--force-email", "force_email", is_flag=True,
              help="Send email even if a newsletter has already been delivered for this session.")
def cli(
    db_path: str,
    session_id: str,
    email_to: Optional[str],
    no_email: bool,
    force_email: bool,
) -> None:
    state = NewsletterAgentState(session_id=session_id, db_path=db_path).load_latest_from_db()
    if state is None:
        raise click.ClickException(f"No state found for session {session_id}")

    state.start_step("send")
    state.save_checkpoint("send")

    out_file = deliver_newsletter(
        session_id=session_id,
        db_path=db_path,
        to=email_to,
        no_email=no_email,
        force_email=force_email,
    )

    state.complete_step("send", message=f"wrote {out_file}")
    state.save_checkpoint("send")


if __name__ == "__main__":  # pragma: no cover
    sys.exit(cli())
