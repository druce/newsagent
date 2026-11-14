"""newsagent:backfill-published — rebuild the `published_articles` cross-day
dedup store from past sessions.

CLI entry: python -m lib.steps.backfill_published [--days N] [--session SID]
           [--db PATH] [--yes]

Default: dry-run (lists what would be recorded). Use --yes to write.

Why this exists: until 2026-09-16 `draft` overwrote the per-article list that
`send` records from, so every session sent 0 rows and `crossdedupe` never had
anything to compare against. Each session's `select` checkpoint in
`agent_state` still holds that list (plus `headline_data` with every
short_summary), and the `newsletters` table holds when the issue went out — so
the store can be reconstructed exactly as `send` would have written it.

Per session with a `newsletters` row inside the window (oldest first):
- skip if `published_articles` already has rows for it (idempotent);
- load the `select` checkpoint (NOT the latest — post-draft checkpoints from
  before the fix carry section markdown instead of articles);
- embed title + short_summary and insert via send's recorder, stamping
  `published_at` with the newsletter's `created_at`.

Never touches agent_state, newsletters, urls, articles, or out/.
"""
from __future__ import annotations

import sqlite3
import sys
from datetime import datetime, timedelta
from typing import Optional

import click
from dotenv import load_dotenv

from lib.state import NewsletterAgentState
from lib.published import record_session_articles

_DEFAULT_DAYS = 14


def _sent_sessions(
    conn: sqlite3.Connection, since: datetime, session_id: Optional[str]
) -> list[tuple[str, str]]:
    """(session_id, first newsletters.created_at) for sessions sent since
    `since`, oldest first. `send` (and `rewrite`'s preview) insert a row each,
    so take the earliest per session."""
    sql = ("SELECT session_id, MIN(created_at) AS first_at FROM newsletters "
           "GROUP BY session_id HAVING first_at >= ?")
    params: list = [since.isoformat()]
    if session_id:
        sql = ("SELECT session_id, MIN(created_at) AS first_at FROM newsletters "
               "WHERE session_id = ? GROUP BY session_id")
        params = [session_id]
    return [(r[0], r[1]) for r in conn.execute(sql + " ORDER BY first_at", params)]


def _already_recorded(conn: sqlite3.Connection, session_id: str) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM published_articles WHERE session_id = ?", (session_id,)
    ).fetchone()[0]


def _recordable(state: NewsletterAgentState) -> int:
    """Selected items the recorder would write (anything with a resolvable url).
    Zero means the checkpoint has no newsletter picks, so nothing to backfill;
    the actual write also records the session's unselected rated articles."""
    n = 0
    for it in state.newsletter_section_data or []:
        idx = it.get("id")
        h = (state.headline_data[idx]
             if isinstance(idx, int) and 0 <= idx < len(state.headline_data) else {})
        if it.get("link") or h.get("final_url") or h.get("url"):
            n += 1
    return n


@click.command()
@click.option("--days", default=_DEFAULT_DAYS, type=int, show_default=True,
              help="Backfill sessions whose newsletter was written in the last N days")
@click.option("--session", "session_id", default=None,
              help="Backfill only this session (ignores --days)")
@click.option("--db", "db_path", default="newsletter_agent.db",
              help="Path to SQLite database")
@click.option("--yes", "do_write", is_flag=True, default=False,
              help="Actually write rows (default is dry-run)")
def cli(days: int, session_id: Optional[str], db_path: str, do_write: bool) -> None:
    """Rebuild published_articles from past sessions' select checkpoints
    (dry-run by default; use --yes to write)."""
    load_dotenv()  # OPENAI_API_KEY for the embeddings
    since = datetime.now() - timedelta(days=days)
    with sqlite3.connect(db_path) as conn:
        sessions = _sent_sessions(conn, since, session_id)

    if not sessions:
        click.echo(f"No sent sessions found in the last {days} day(s).")
        return

    mode = "WRITING" if do_write else "DRY-RUN (use --yes to write)"
    click.echo(f"[{mode}] {len(sessions)} sent session(s):\n")

    total = 0
    for sid, published_at in sessions:
        with sqlite3.connect(db_path) as conn:
            existing = _already_recorded(conn, sid)
        if existing:
            click.echo(f"  skip      {sid}  already recorded ({existing} row(s))")
            continue

        state = NewsletterAgentState(session_id=sid, db_path=db_path).load_from_db("select")
        if state is None:
            click.echo(f"  skip      {sid}  no select checkpoint")
            continue
        n = _recordable(state)
        if not n:
            click.echo(f"  skip      {sid}  select checkpoint has no articles")
            continue

        day = published_at[:10]
        if do_write:
            written, n_sel = record_session_articles(
                state, sid, db_path, published_at=published_at)
            total += written
            click.echo(f"  recorded {written} article(s) ({n_sel} selected)  {sid}  "
                       f"(published {day})")
        else:
            click.echo(f"  would record {n} selected article(s)  {sid}  (published {day})")

    if do_write:
        click.echo(f"\nWrote {total} row(s) to published_articles.")
    else:
        click.echo("\nRun with --yes to write these rows.")


if __name__ == "__main__":  # pragma: no cover
    sys.exit(cli())
