"""news:filter — classify headlines as AI-relevant."""
from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

import click

import lib.prompts  # noqa: F401 — register FILTER_URLS
from lib.llm import call_prompt
from lib.state import NewsletterAgentState


_BATCH = 50


@click.command()
@click.option("--db", "db_path", default="newsletter_agent.db")
@click.option("--session", "session_id", required=True)
@click.option("--keep-non-ai", is_flag=True, help="Keep non-AI headlines in state (default: drop)")
@click.option("--engine", default=None, help="Override engine (e.g. openrouter:google/gemini-2.5-flash)")
def cli(db_path: str, session_id: str, keep_non_ai: bool, engine: str | None) -> None:
    state = NewsletterAgentState(session_id=session_id, db_path=db_path).load_latest_from_db()
    if state is None:
        raise click.ClickException(f"No state found for session {session_id}")

    state.start_step("filter")
    state.save_checkpoint("filter")

    # Each headline gets a stable index id for prompt round-trip
    items = [{"id": str(i), "title": h["title"]}
             for i, h in enumerate(state.headline_data) if "is_ai" not in h]
    if not items:
        state.complete_step("filter", message="nothing to filter")
        state.save_checkpoint("filter")
        click.echo("Nothing to filter.")
        return

    classifications: dict[str, bool] = {}

    for i in range(0, len(items), _BATCH):
        batch = items[i:i + _BATCH]
        result = call_prompt("filter_urls", {"items": batch}, engine=engine)
        for c in result.classifications:
            classifications[c.id] = c.is_ai

    # Apply results
    kept: list[dict] = []
    ai_count = 0
    for i, h in enumerate(state.headline_data):
        key = str(i)
        if key in classifications:
            h["is_ai"] = classifications[key]
        is_ai = h.get("is_ai", False)
        if is_ai:
            ai_count += 1
        if is_ai or keep_non_ai or "is_ai" not in h:
            kept.append(h)
    state.headline_data = kept

    # Update urls.isAI for cross-session dedup
    with sqlite3.connect(db_path) as conn:
        for h in state.headline_data:
            if "is_ai" in h:
                conn.execute(
                    "UPDATE urls SET isAI=? WHERE initial_url=?",
                    (1 if h["is_ai"] else 0, h["url"]),
                )
        conn.commit()

    state.complete_step("filter", message=f"{ai_count}/{len(items)} AI-relevant")
    state.save_checkpoint("filter")

    runs_dir = Path("runs") / session_id
    runs_dir.mkdir(parents=True, exist_ok=True)
    (runs_dir / "filter.json").write_text(json.dumps({
        "session_id": session_id,
        "completed_at": datetime.now().isoformat(),
        "total": len(items),
        "ai": ai_count,
        "kept": len(state.headline_data),
    }, indent=2))

    click.echo(f"Filtered: {ai_count}/{len(items)} AI-relevant, {len(state.headline_data)} kept.")


if __name__ == "__main__":  # pragma: no cover
    sys.exit(cli())
