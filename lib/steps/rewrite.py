"""news:rewrite — whole-newsletter critic-optimizer pass + title generation.

Steps:
1. Load state. Concatenate section_markdown from newsletter_section_data.
2. Run critic_optimizer_loop with critique_newsletter + improve_newsletter.
3. Call generate_newsletter_title on final draft.
4. Write state.final_newsletter = "# {title}\\n\\n{body}" and state.newsletter_title.
5. Write runs/<SID>/rewrite.json with critic transcript + title.
6. Mark rewrite step complete.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

import click

import lib.prompts  # noqa: F401 — register all prompts
from lib.critic import critic_optimizer_loop
from lib.llm import call_prompt
from lib.state import NewsletterAgentState


@click.command()
@click.option("--db", "db_path", default="newsletter_agent.db")
@click.option("--session", "session_id", required=True)
@click.option("--max-edits", default=2, type=int,
              help="Max critic-optimizer iterations (default 2)")
@click.option("--engine", default=None, help="Override LLM engine for all prompts")
def cli(
    db_path: str,
    session_id: str,
    max_edits: int,
    engine: Optional[str],
) -> None:
    state = NewsletterAgentState(session_id=session_id, db_path=db_path).load_latest_from_db()
    if state is None:
        raise click.ClickException(f"No state found for session {session_id}")

    state.start_step("rewrite")
    state.save_checkpoint("rewrite")

    # ----- Step 1: concatenate section markdowns -----
    draft = "\n\n".join(
        s.get("section_markdown", "") for s in state.newsletter_section_data
    )

    # ----- Step 2: run critic loop -----
    transcript = critic_optimizer_loop(
        initial_draft=draft,
        critique_prompt_name="critique_newsletter",
        improve_prompt_name="improve_newsletter",
        critique_input_builder=lambda d: {"newsletter_markdown": d},
        improve_input_builder=lambda d, c: {"newsletter_markdown": d, "critique": c},
        draft_field="newsletter_markdown",
        max_edits=max_edits,
        engine=engine,
    )
    final_body = transcript.final_draft

    # ----- Step 3: generate title -----
    title_result = call_prompt(
        "generate_newsletter_title",
        {"newsletter_markdown": final_body},
        engine=engine,
    )
    title = title_result.title

    # ----- Step 4: assemble final newsletter -----
    state.final_newsletter = f"# {title}\n\n{final_body}"
    state.newsletter_title = title

    state.complete_step(
        "rewrite",
        message=f"Newsletter rewritten. Title: {title!r}. "
                f"Iterations: {transcript.iterations}. Accepted: {transcript.accepted}.",
    )
    state.save_checkpoint("rewrite")

    # ----- Step 5: write run artifact -----
    runs_dir = Path("runs") / session_id
    runs_dir.mkdir(parents=True, exist_ok=True)
    artifact = {
        "session_id": session_id,
        "completed_at": datetime.now().isoformat(),
        "title": title,
        "transcript": {
            "iterations": transcript.iterations,
            "scores": transcript.scores,
            "feedbacks": transcript.feedbacks,
            "accepted": transcript.accepted,
            "final_length": len(final_body),
        },
    }
    (runs_dir / "rewrite.json").write_text(json.dumps(artifact, indent=2))

    click.echo(
        f"Rewrite: '{title}' — {transcript.iterations} iteration(s), "
        f"accepted={transcript.accepted}."
    )


if __name__ == "__main__":  # pragma: no cover
    sys.exit(cli())
