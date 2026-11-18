"""Post one session's selected stories to Bluesky on a timer, then exit.

Reads `published_articles` (no web server, no `bsky_queue`): each round posts
the highest-rated `selected=1` row of the session whose `bsky_status` is still
'none', as a link-preview card (same `post_item` as v1), and records
'posted' / 'failed' on the row. Exits when nothing is left.

    .venv/bin/python -m lib.steps.bsky_share_v2                 # latest session
    .venv/bin/python -m lib.steps.bsky_share_v2 --session 2026-11-15-18-00-07
    .venv/bin/python -m lib.steps.bsky_share_v2 --dry-run       # list, don't post
"""
from __future__ import annotations

import fcntl
import os
import sqlite3
import time
from pathlib import Path

import click

from lib.bluesky.api import bsky_login
from lib.bsky_queue.poster import post_item
from lib.bsky_queue.published import mark_failed, mark_posted, next_unposted, unposted
from lib.curate import latest_session
from lib.db import init_db
from lib.steps.bsky_share import _IMAGE_DIR, _is_auth_error
from lib.utilities import _strip_no_content


def _text(row: dict) -> str:
    return _strip_no_content(row.get("short_summary")) or row.get("title") or row["url"]


def post_next(db_path: str, session_id: str, session: dict, relogin=None,
              image_dir: str | Path = _IMAGE_DIR) -> dict | None:
    """Post the session's next row; return it with `ok`/`error` keys, or None.

    On an auth-type failure, refreshes `session` in place via `relogin()` and
    retries once. Any other failure marks the row failed (never retried).
    """
    row = next_unposted(db_path, session_id)
    if row is None:
        return None
    item = {"url": row["url"], "title": _text(row)}
    try:
        try:
            uri = post_item(session, item, image_dir)
        except Exception as exc:  # noqa: BLE001
            if relogin is None or not _is_auth_error(exc):
                raise
            session.update(relogin())
            uri = post_item(session, item, image_dir)
    except Exception as exc:  # noqa: BLE001 — record any failure on the row
        mark_failed(db_path, row["id"], str(exc))
        return {**row, "ok": False, "error": str(exc)}
    _record_posted(db_path, row["id"], uri)
    return {**row, "ok": True, "uri": uri}


def _record_posted(db_path: str, row_id: int, uri: str, tries: int = 5) -> None:
    """Mark a row posted, hard: the post is already live, so an unrecorded row
    would be posted again on the next run. Retries a locked DB (a pipeline step
    may be writing) and finishes the write even if Ctrl-C lands mid-write."""
    for attempt in range(tries):
        try:
            mark_posted(db_path, row_id, uri)
            return
        except KeyboardInterrupt:
            mark_posted(db_path, row_id, uri)
            raise
        except sqlite3.OperationalError as exc:
            if attempt == tries - 1:
                raise click.ClickException(
                    f"Posted {uri} but could not record it on published_articles "
                    f"id={row_id} ({exc}). Run: UPDATE published_articles SET "
                    f"bsky_status='posted', bsky_uri='{uri}' WHERE id={row_id};"
                ) from exc
            time.sleep(2)


@click.command()
@click.option("--interval", default=180, show_default=True,
              help="Seconds between posts.")
@click.option("--session", "session_id", default=None,
              help="Session id to post from (default: most recent, at startup).")
@click.option("--db", "db_path", default="newsletter_agent.db", show_default=True)
@click.option("--dry-run", is_flag=True,
              help="List what would be posted, in order, and exit.")
def cli(interval: int, session_id: str | None, db_path: str, dry_run: bool) -> None:
    """Post a session's selected stories to Bluesky, best-rated first."""
    init_db(db_path)
    session_id = session_id or latest_session(db_path)
    if session_id is None:
        raise click.ClickException("No sessions in published_articles.")
    todo = unposted(db_path, session_id)
    click.echo(f"Session {session_id}: {len(todo)} to post, every {interval}s.")
    if not todo:
        click.echo("Nothing to post.")
        return
    if dry_run:
        for r in todo:
            rating = "-" if r["rating"] is None else f'{r["rating"]:.2f}'
            click.echo(f"{rating:>7}  {_text(r)}")
        return

    username = os.environ.get("BSKY_USERNAME")
    secret = os.environ.get("BSKY_SECRET")
    if not username or not secret:
        raise click.ClickException("BSKY_USERNAME and BSKY_SECRET env vars are required.")

    def login() -> dict:
        return bsky_login(username, secret)

    lock = _acquire_lock(db_path)  # held (open) until the process exits
    session = login()
    try:
        while (res := post_next(db_path, session_id, session, relogin=login)):
            left = len(unposted(db_path, session_id))
            if res["ok"]:
                click.echo(f'Posted: "{_text(res)}" — {left} left.')
            else:
                click.echo(f'Failed: "{_text(res)}": {res["error"]}')
            if not left:
                break
            time.sleep(interval)
    except KeyboardInterrupt:
        click.echo("\nStopped.")
    finally:
        lock.close()


def lock_path(db_path: str) -> str:
    """Per-DB pid/lock file: one poster per database, whatever the session."""
    return f"{db_path}.bsky_share_v2.pid"


def _acquire_lock(db_path: str):
    """Take an exclusive flock on the pid file, or exit if another run holds it.

    flock (not just the file's existence) is the lock, so the OS releases it
    when the process dies — a crash or kill -9 never leaves a stale lock.
    """
    f = open(lock_path(db_path), "a+")
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        f.seek(0)
        holder = f.read().strip() or "?"
        f.close()
        raise click.ClickException(
            f"bsky_share_v2 already running (pid {holder}); lock {lock_path(db_path)}")
    f.seek(0)
    f.truncate()
    f.write(str(os.getpid()))
    f.flush()
    return f


if __name__ == "__main__":
    cli()
