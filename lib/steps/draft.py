"""news:draft — parallel section drafting with critic-optimizer loop.

Steps:
1. Load state. Group newsletter_section_data by cat (cluster name).
2. For each cat, build input for write_section: {section_title, stories}.
3. ThreadPoolExecutor dispatches one task per section. Each task:
   - Calls write_section → initial markdown draft.
   - Runs critic_optimizer_loop with critique_section + improve_section.
   - Returns (cat, CriticTranscript).
4. Replaces state.newsletter_section_data with [{cat, section_markdown}] per cluster.
5. Writes runs/<SID>/draft.json with per-section transcript data.
6. Marks draft step complete.
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import urlparse

import click

import lib.prompts  # noqa: F401 — register all prompts
from lib.critic import critic_optimizer_loop, CriticTranscript
from lib.llm import call_prompt
from lib.state import NewsletterAgentState


def _hostname(url: str) -> str:
    """Extract a clean source name from URL hostname."""
    try:
        host = urlparse(url).hostname or url
        # Strip www. prefix
        if host.startswith("www."):
            host = host[4:]
        return host
    except Exception:
        return url


def _draft_section(
    cat: str,
    stories: List[dict],
    max_edits: int,
    engine: Optional[str],
) -> tuple:
    """Draft one section and run the critic loop. Returns (cat, CriticTranscript)."""
    # Build write_section input
    section_input = {
        "section_title": cat,
        "stories": [
            {
                "title": s.get("headline", ""),
                "url": s.get("link", ""),
                "summary": s.get("summary", ""),
                "source": _hostname(s.get("link", "")),
                "rating": s.get("rating", 0.0),
            }
            for s in stories
        ],
    }

    initial = call_prompt("write_section", section_input, engine=engine)
    initial_draft = initial.section_markdown

    transcript = critic_optimizer_loop(
        initial_draft=initial_draft,
        critique_prompt_name="critique_section",
        improve_prompt_name="improve_section",
        critique_input_builder=lambda d: {"section_markdown": d},
        improve_input_builder=lambda d, c: {"section_markdown": d, "critique": c},
        draft_field="section_markdown",
        max_edits=max_edits,
        engine=engine,
    )

    return cat, transcript


@click.command()
@click.option("--db", "db_path", default="newsletter_agent.db")
@click.option("--session", "session_id", required=True)
@click.option("--max-edits", default=2, type=int,
              help="Max critic-optimizer iterations per section (default 2)")
@click.option("--parallelism", default=4, type=int,
              help="Number of sections to draft concurrently (default 4)")
@click.option("--engine", default=None, help="Override LLM engine for all prompts")
def cli(
    db_path: str,
    session_id: str,
    max_edits: int,
    parallelism: int,
    engine: Optional[str],
) -> None:
    state = NewsletterAgentState(session_id=session_id, db_path=db_path).load_latest_from_db()
    if state is None:
        raise click.ClickException(f"No state found for session {session_id}")

    state.start_step("draft")
    state.save_checkpoint("draft")

    # ----- Group by cat -----
    by_cat: Dict[str, List[dict]] = defaultdict(list)
    for item in state.newsletter_section_data:
        by_cat[item["cat"]].append(item)

    cats = list(by_cat.keys())

    # ----- Parallel drafting -----
    results: Dict[str, CriticTranscript] = {}

    def _task(cat):
        return _draft_section(cat, by_cat[cat], max_edits=max_edits, engine=engine)

    with ThreadPoolExecutor(max_workers=max(1, parallelism)) as pool:
        futures = {pool.submit(_task, cat): cat for cat in cats}
        for fut in as_completed(futures):
            cat, transcript = fut.result()
            results[cat] = transcript

    # ----- Update state -----
    new_sections = []
    for cat in cats:
        t = results[cat]
        new_sections.append({
            "cat": cat,
            "section_markdown": t.final_draft,
        })
    state.newsletter_section_data = new_sections

    state.complete_step(
        "draft",
        message=f"{len(new_sections)} sections drafted",
    )
    state.save_checkpoint("draft")

    # ----- Write run artifact -----
    runs_dir = Path("runs") / session_id
    runs_dir.mkdir(parents=True, exist_ok=True)
    artifact = {
        "session_id": session_id,
        "completed_at": datetime.now().isoformat(),
        "sections_count": len(new_sections),
        "max_edits": max_edits,
        "sections": [
            {
                "cat": cat,
                "iterations": results[cat].iterations,
                "scores": results[cat].scores,
                "feedbacks": results[cat].feedbacks,
                "accepted": results[cat].accepted,
                "final_length": len(results[cat].final_draft),
            }
            for cat in cats
        ],
    }
    (runs_dir / "draft.json").write_text(json.dumps(artifact, indent=2))

    click.echo(
        f"Draft: {len(new_sections)} sections "
        f"(max_edits={max_edits}, parallelism={parallelism})."
    )


if __name__ == "__main__":  # pragma: no cover
    sys.exit(cli())
