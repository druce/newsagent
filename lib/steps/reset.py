"""news:reset — reset workflow step(s) for a session."""
from __future__ import annotations

import sys

import click

from lib.state import NewsletterAgentState, StepStatus, WORKFLOW_STEPS


@click.command()
@click.argument("session_id")
@click.option("--db", "db_path", default="newsletter_agent.db")
@click.option("--errors", is_flag=True, help="Reset only ERROR steps to NOT_STARTED")
@click.option("--from", "from_step", default=None, help="Reset this step and all after it")
@click.option("--all", "reset_all", is_flag=True, help="Reset every step to NOT_STARTED")
@click.option("--yes", is_flag=True, help="Skip confirmation prompt")
def cli(session_id: str, db_path: str, errors: bool, from_step: str | None,
        reset_all: bool, yes: bool) -> None:
    flags = [errors, bool(from_step), reset_all]
    if sum(1 for f in flags if f) != 1:
        raise click.ClickException(
            "Specify exactly one of: --errors, --from STEP, --all"
        )

    state = NewsletterAgentState(session_id=session_id, db_path=db_path).load_latest_from_db()
    if state is None:
        raise click.ClickException(f"No state found for session {session_id}")

    if errors:
        affected = state.get_failed_steps()
        action = "clear errors"
    elif from_step:
        ids = [sid for sid, *_ in WORKFLOW_STEPS]
        if from_step not in ids:
            raise click.ClickException(f"Unknown step: {from_step}")
        idx = ids.index(from_step)
        affected = ids[idx:]
        action = f"reset from {from_step}"
    else:
        affected = [s.id for s in state.steps]
        action = "reset ALL steps"

    if not yes:
        click.echo(f"About to {action} on session {session_id}:")
        for sid in affected:
            click.echo(f"  - {sid}")
        if not click.confirm("Proceed?"):
            click.echo("Aborted.")
            return

    if errors:
        state.clear_errors()
    elif from_step:
        for sid in affected:
            step = state.get_step(sid)
            if step:
                step.status = StepStatus.NOT_STARTED
                step.started_at = None
                step.completed_at = None
                step.error_message = ""
                step.status_message = ""
    else:
        state.reset()

    # Save checkpoint at whatever the current step is (or init if none)
    current = state.get_current_step() or "init"
    state.save_checkpoint(current)
    click.echo(f"Reset complete ({action}).")


if __name__ == "__main__":  # pragma: no cover
    sys.exit(cli())
