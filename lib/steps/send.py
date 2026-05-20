"""newsagent:send — render newsletter as HTML and write to out/."""
from __future__ import annotations

import sqlite3
import sys
from datetime import date, datetime
from pathlib import Path

import click

from lib.state import NewsletterAgentState


_OUT_DIR = Path("out")


def _render_html(title: str, body_html: str, headline_count: int) -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>{title or 'AI Newsletter'}</title>
  <style>
    body {{ font-family: -apple-system, sans-serif; max-width: 720px; margin: 2em auto; padding: 0 1em; line-height: 1.5; }}
    h1 {{ border-bottom: 2px solid #333; padding-bottom: .3em; }}
    .meta {{ color: #666; font-size: .9em; }}
  </style>
</head>
<body>
  <h1>{title or 'AI Newsletter'}</h1>
  <p class="meta">{datetime.now().isoformat()} — {headline_count} headlines</p>
  {body_html}
</body>
</html>
"""


@click.command()
@click.option("--db", "db_path", default="newsletter_agent.db")
@click.option("--session", "session_id", required=True)
@click.option("--notify", is_flag=True, help="Send via Gmail (not implemented in Phase 2)")
def cli(db_path: str, session_id: str, notify: bool) -> None:
    if notify:
        raise click.ClickException(
            "--notify Gmail send is not implemented in Phase 2. "
            "Use Phase 2b or send the file manually."
        )

    state = NewsletterAgentState(session_id=session_id, db_path=db_path).load_latest_from_db()
    if state is None:
        raise click.ClickException(f"No state found for session {session_id}")

    state.start_step("send")
    state.save_checkpoint("send")

    body = state.final_newsletter or (
        "<p><em>Dummy newsletter — pipeline ran through gather/download/send "
        "without the LLM steps.</em></p>"
    )
    html = _render_html(state.newsletter_title, body, len(state.headline_data))

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
        # Symlinks may fail on some platforms; copy as fallback
        latest.write_text(html)

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO newsletters (session_id, title, html, sent_at, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (session_id, state.newsletter_title or "AI Newsletter", html,
             None, datetime.now().isoformat()),
        )
        conn.commit()

    state.complete_step("send", message=f"wrote {out_file}")
    state.save_checkpoint("send")
    click.echo(f"Newsletter written to {out_file}")


if __name__ == "__main__":  # pragma: no cover
    sys.exit(cli())
