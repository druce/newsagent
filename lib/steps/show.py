"""news:show — dump full state record for a session."""
from __future__ import annotations

import sys

import click

from lib.state import NewsletterAgentState


@click.command()
@click.argument("session_id")
@click.option("--db", "db_path", default="newsletter_agent.db")
@click.option("--step", default=None, help="Show only one step's checkpoint")
def cli(session_id: str, db_path: str, step: str | None) -> None:
    state = NewsletterAgentState(session_id=session_id, db_path=db_path)
    loaded = state.load_from_db(step) if step else state.load_latest_from_db()
    if loaded is None:
        click.echo(f"No state found for session {session_id}"
                   + (f" at step {step}" if step else ""))
        return

    click.echo(f"Session: {session_id}")
    click.echo(loaded.get_workflow_status_report())
    click.echo("")
    click.echo("Steps written:")
    for s in loaded.list_session_steps():
        click.echo(f"  - {s['step_name']:<14} {s['updated_at']}")
    click.echo("")
    click.echo(f"Headlines: {len(loaded.headline_data)}")
    click.echo(f"Sections:  {len(loaded.newsletter_section_data)}")
    click.echo(f"Newsletter title: {loaded.newsletter_title or '(none)'}")
    click.echo(f"Newsletter length: {len(loaded.final_newsletter)} chars")


if __name__ == "__main__":  # pragma: no cover
    sys.exit(cli())
