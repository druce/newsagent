"""newsagent:resume — clear errors and report the next step to invoke."""
from __future__ import annotations

import sys

import click

from lib.state import NewsletterAgentState


@click.command()
@click.argument("session_id")
@click.option("--db", "db_path", default="newsletter_agent.db")
@click.option("--no-clear", is_flag=True, help="Don't auto-clear ERROR steps")
def cli(session_id: str, db_path: str, no_clear: bool) -> None:
    state = NewsletterAgentState(session_id=session_id, db_path=db_path).load_latest_from_db()
    if state is None:
        click.echo(f"No state found for session {session_id}")
        return

    cleared = []
    if not no_clear:
        cleared = state.get_failed_steps()
        if cleared:
            state.clear_errors()
            state.save_checkpoint(state.get_current_step() or "init")

    cur = state.get_current_step()
    click.echo(f"Session: {session_id}")
    if cleared:
        click.echo(f"Cleared {len(cleared)} error step(s): {', '.join(cleared)}")
    if cur is None:
        click.echo("All steps complete. Nothing to resume.")
        return
    click.echo(f"Next step: {cur}")
    click.echo(f"To continue: python -m lib.steps.{cur} --db {db_path} --session {session_id}")


if __name__ == "__main__":  # pragma: no cover
    sys.exit(cli())
