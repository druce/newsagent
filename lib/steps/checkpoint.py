"""newsagent:checkpoint — force a manual state checkpoint for a session.

CLI entry: python -m lib.steps.checkpoint <SID> <STEP> [--db PATH]

Escape-hatch: loads the latest state for a session and calls
state.save_checkpoint(STEP). Useful when a step crashed AFTER doing
useful work but BEFORE its own checkpoint.
"""
from __future__ import annotations

import sys

import click

from lib.state import NewsletterAgentState


@click.command()
@click.argument("session_id")
@click.argument("step_name")
@click.option("--db", "db_path", default="newsletter_agent.db",
              help="Path to SQLite database")
def cli(session_id: str, step_name: str, db_path: str) -> None:
    """Force a checkpoint for SESSION_ID at STEP_NAME."""
    state = NewsletterAgentState(session_id=session_id, db_path=db_path)
    loaded = state.load_latest_from_db()

    if loaded is None:
        click.echo(f"Error: no state found for session '{session_id}'", err=True)
        sys.exit(1)

    loaded.save_checkpoint(step_name)
    click.echo(f"Checkpoint saved: session={session_id}, step={step_name}")


if __name__ == "__main__":  # pragma: no cover
    sys.exit(cli())
