"""newsagent:gc — garbage-collect old sessions from the database and filesystem.

CLI entry: python -m lib.steps.gc [--older-than DAYS] [--db PATH] [--yes]

Default: dry-run (lists what would be deleted). Use --yes to actually delete.

What gets deleted:
- agent_state rows for sessions older than --older-than days
- runs/<SID>/ scratch directories

What is NEVER touched:
- articles, urls, newsletters, sites tables
- out/*.html (final newsletters)
- download/* (article text cache)
"""
from __future__ import annotations

import shutil
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

import click

from lib.db import AgentState


@click.command()
@click.option("--older-than", "older_than", default=30, type=int, show_default=True,
              help="Delete sessions whose last update is older than this many days")
@click.option("--db", "db_path", default="newsletter_agent.db",
              help="Path to SQLite database")
@click.option("--yes", "do_delete", is_flag=True, default=False,
              help="Actually delete (default is dry-run)")
def cli(older_than: int, db_path: str, do_delete: bool) -> None:
    """Garbage-collect old sessions (dry-run by default; use --yes to delete)."""
    cutoff = datetime.now() - timedelta(days=older_than)

    with sqlite3.connect(db_path) as conn:
        # List all sessions (no limit)
        all_sessions = AgentState.list_sessions(conn, n_records=None)

        candidates: list[str] = []
        for sid in all_sessions:
            latest = AgentState.get_latest_by_session(conn, sid)
            if latest is None:
                continue
            updated = latest.updated_at
            if updated is None or updated <= cutoff:
                candidates.append(sid)

    if not candidates:
        click.echo(f"No sessions older than {older_than} day(s) found.")
        return

    mode = "DELETING" if do_delete else "DRY-RUN (use --yes to delete)"
    click.echo(f"[{mode}] Found {len(candidates)} session(s) older than {older_than} day(s):\n")

    for sid in candidates:
        run_dir = Path("runs") / sid
        has_dir = run_dir.exists()

        if do_delete:
            # Delete agent_state rows
            with sqlite3.connect(db_path) as conn:
                deleted = AgentState.delete_session(conn, sid)

            # Delete runs/<SID>/ directory
            if has_dir:
                shutil.rmtree(run_dir)
                click.echo(f"  deleted  {sid}  ({deleted} state row(s), runs/{sid}/ removed)")
            else:
                click.echo(f"  deleted  {sid}  ({deleted} state row(s), no run dir)")
        else:
            dir_note = f"  runs/{sid}/ exists" if has_dir else ""
            click.echo(f"  would delete  {sid}{dir_note}")

    if not do_delete:
        click.echo(f"\nRun with --yes to permanently delete these {len(candidates)} session(s).")


if __name__ == "__main__":  # pragma: no cover
    sys.exit(cli())
