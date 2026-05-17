"""news:summarize — extract bullet-point article summaries."""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

import click

import lib.prompts  # noqa: F401 — register EXTRACT_SUMMARIES
from lib.llm import call_prompt
from lib.state import NewsletterAgentState

_BATCH = 10
_MAX_TEXT_CHARS = 20_000


@click.command()
@click.option("--db", "db_path", default="newsletter_agent.db")
@click.option("--session", "session_id", required=True)
@click.option("--engine", default=None, help="Override engine (e.g. openrouter:google/gemini-2.5-flash)")
def cli(db_path: str, session_id: str, engine: str | None) -> None:
    state = NewsletterAgentState(session_id=session_id, db_path=db_path).load_latest_from_db()
    if state is None:
        raise click.ClickException(f"No state found for session {session_id}")

    state.start_step("summarize")
    state.save_checkpoint("summarize")

    # Build list of items needing summarization: have text_path but no summary
    items: list[dict] = []
    index_map: list[int] = []  # maps item position to headline_data index

    for i, h in enumerate(state.headline_data):
        if "text_path" not in h or "summary" in h:
            continue
        text_path = h["text_path"]
        try:
            text = Path(text_path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        # Cap text to avoid huge prompts
        text = text[:_MAX_TEXT_CHARS]
        items.append({"id": str(len(items)), "title": h.get("title", ""), "text": text})
        index_map.append(i)

    if not items:
        state.complete_step("summarize", message="nothing to summarize")
        state.save_checkpoint("summarize")
        click.echo("Nothing to summarize.")
        return

    summaries: dict[str, str] = {}

    for batch_start in range(0, len(items), _BATCH):
        batch = items[batch_start:batch_start + _BATCH]
        result = call_prompt("extract_summaries", {"items": batch}, engine=engine)
        for s in result.summaries:
            summaries[s.id] = s.summary

    # Write summaries back to headline_data
    summarized_count = 0
    for item_pos, headline_idx in enumerate(index_map):
        key = str(item_pos)
        if key in summaries:
            state.headline_data[headline_idx]["summary"] = summaries[key]
            summarized_count += 1

    state.complete_step("summarize", message=f"{summarized_count}/{len(items)} summarized")
    state.save_checkpoint("summarize")

    runs_dir = Path("runs") / session_id
    runs_dir.mkdir(parents=True, exist_ok=True)
    (runs_dir / "summarize.json").write_text(json.dumps({
        "session_id": session_id,
        "completed_at": datetime.now().isoformat(),
        "total": len(items),
        "summarized": summarized_count,
    }, indent=2))

    click.echo(f"Summarized: {summarized_count}/{len(items)} articles.")


if __name__ == "__main__":  # pragma: no cover
    sys.exit(cli())
