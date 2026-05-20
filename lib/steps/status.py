"""newsagent:status — show workflow progress for a session.

CLI entry: python -m lib.steps.status [--db PATH] [--session SID]
"""
from __future__ import annotations

import sys

import click

from lib.state import NewsletterAgentState


@click.command()
@click.option("--db", "db_path", default="newsletter_agent.db",
              help="Path to SQLite database")
@click.option("--session", "session_id", default=None,
              help="Session ID (defaults to most recent)")
def cli(db_path: str, session_id: str | None) -> None:
    """Report status of the current or specified session."""
    if session_id is None:
        recents = NewsletterAgentState.list_recent_sessions(db_path, limit=1)
        if not recents:
            click.echo("No sessions found. Run /newsagent:init to create one.")
            return
        session_id = recents[0]

    state = NewsletterAgentState(session_id=session_id, db_path=db_path)
    loaded = state.load_latest_from_db()
    if loaded is None:
        click.echo(f"No state found for session {session_id}.")
        return

    status = loaded.get_status()
    click.echo(f"Session:  {session_id}")
    click.echo(
        f"Progress: {status['workflow']['progress_percentage']:.1f}% "
        f"({status['workflow']['completed_steps']}/{status['workflow']['total_steps']} steps)"
    )
    cur = status["workflow"]["current_step"]
    click.echo(f"Next:     {cur if cur else '(all complete)'}")
    click.echo(f"Headlines: {status['headlines']['total']}")
    click.echo(f"Clusters:  {status['topics']['cluster_topics']}")
    click.echo(f"Sections:  {status['processing']['newsletter_sections']}")
    click.echo("")
    click.echo(loaded.get_workflow_status_report(title="Workflow"))


if __name__ == "__main__":  # pragma: no cover
    sys.exit(cli())
