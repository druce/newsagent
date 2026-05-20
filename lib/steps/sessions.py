"""newsagent:sessions — list recent sessions in the database.

CLI entry: python -m lib.steps.sessions [--db PATH] [--limit N]
"""
from __future__ import annotations

import sqlite3
import sys

import click

from lib.db import AgentState
from lib.state import NewsletterAgentState


@click.command()
@click.option("--db", "db_path", default="newsletter_agent.db",
              help="Path to SQLite database")
@click.option("--limit", "limit", default=10, type=int,
              help="Max sessions to display")
def cli(db_path: str, limit: int) -> None:
    """List the most recent sessions."""
    recents = NewsletterAgentState.list_recent_sessions(db_path, limit=limit)
    if not recents:
        click.echo("No sessions found.")
        return

    click.echo(f"{'SESSION':<28} {'UPDATED':<26} {'STEP':<14} PROGRESS")
    click.echo("-" * 80)
    with sqlite3.connect(db_path) as conn:
        for sid in recents:
            latest = AgentState.get_latest_by_session(conn, sid)
            if latest is None:
                continue
            state = NewsletterAgentState(session_id=sid, db_path=db_path)
            loaded = state.load_latest_from_db()
            pct = f"{loaded.get_progress_percentage():.0f}%" if loaded else "?"
            cur = loaded.get_current_step() if loaded else "?"
            updated = latest.updated_at.isoformat() if latest.updated_at else "?"
            click.echo(f"{sid:<28} {updated:<26} {cur or 'done':<14} {pct}")


if __name__ == "__main__":  # pragma: no cover
    sys.exit(cli())
