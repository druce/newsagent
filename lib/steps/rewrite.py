"""newsagent:rewrite — whole-newsletter critic-optimizer pass + title generation.

Three modes (mirror of lib/steps/filter.py):
  1. classic (default): in-process critic_optimizer_loop + generate_title.
     Use --engine to override (DO NOT use subagent).
  2. --prepare-batches: write one batch JSON containing the initial draft and
     all three pre-rendered prompts (critique/improve/title) so a single
     Sonnet Agent can run the critic loop and generate the title in its own
     context.
  3. --apply-results DIR: read the single result JSON, set state.final_newsletter
     and state.newsletter_title.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

import click
from pydantic import ValidationError

import lib.prompts  # noqa: F401 — register all prompts
from lib.critic import critic_optimizer_loop
from lib.llm import call_prompt, get_prompt
from lib.prompts._dispatch_schemas import RewriteResult
from lib.state import NewsletterAgentState
from lib.steps.send import deliver_newsletter


_BATCHES_SUBDIR = "rewrite-batches"


def _initial_draft(state: NewsletterAgentState) -> str:
    return "\n\n".join(
        s.get("section_markdown", "") for s in state.newsletter_section_data
    )


def _prepare_batch(session_id: str, draft: str, max_edits: int) -> Path:
    crit_cfg = get_prompt("critique_newsletter")
    imp_cfg = get_prompt("improve_newsletter")
    title_cfg = get_prompt("generate_newsletter_title")

    runs_dir = Path("runs") / session_id
    batches_dir = runs_dir / _BATCHES_SUBDIR
    if batches_dir.exists():
        for p in batches_dir.glob("batch-*.json"):
            p.unlink()
    batches_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "batch_id": 0,
        "session_id": session_id,
        "initial_draft": draft,
        "max_edits": max_edits,
        "accept_threshold": 8.0,
        "critique_system_prompt": crit_cfg.system_prompt,
        "critique_user_prompt": crit_cfg.user_prompt,
        "improve_system_prompt": imp_cfg.system_prompt,
        "improve_user_prompt": imp_cfg.user_prompt,
        "title_system_prompt": title_cfg.system_prompt,
        "title_user_prompt": title_cfg.user_prompt,
        "output_schema": RewriteResult.model_json_schema(),
    }
    path = batches_dir / "batch-000.json"
    path.write_text(json.dumps(payload, indent=2))
    return path


def _strip_leading_h1(body: str) -> str:
    """Drop a leading '# ...' H1 line if present — apply prepends '# {title}'."""
    stripped = body.lstrip()
    if stripped.startswith("# "):
        return stripped.split("\n", 1)[1].lstrip("\n") if "\n" in stripped else ""
    return body


def _build_result_from_work(
    body_path: str, title: str, scores_csv: str, feedbacks_csv: str, accepted: bool
) -> RewriteResult:
    """Mechanically assemble a RewriteResult from loop work files + scalars.

    Replaces the per-run 'assemble' subagent: the body and critique feedbacks
    already exist on disk, so no LLM is needed to package them.
    """
    body = _strip_leading_h1(Path(body_path).read_text())
    scores = [float(s) for s in scores_csv.split(",") if s.strip()]
    fb_paths = [p for p in feedbacks_csv.split(",") if p.strip()]
    feedbacks = [Path(p).read_text() for p in fb_paths]
    if len(scores) != len(feedbacks):
        raise click.ClickException(
            f"--scores ({len(scores)}) and --feedbacks ({len(feedbacks)}) count mismatch"
        )
    if not scores:
        raise click.ClickException("--finalize needs at least one score/feedback pair")
    return RewriteResult(
        final_newsletter_markdown=body,
        title=title,
        iterations=len(scores),
        scores=scores,
        feedbacks=feedbacks,
        accepted=accepted,
    )


def _apply_result(
    state: NewsletterAgentState,
    result: RewriteResult,
    session_id: str,
    db_path: str,
    email_to: Optional[str],
    no_email: bool,
) -> None:
    """Set final newsletter state, write the transcript, render + deliver."""
    state.final_newsletter = f"# {result.title}\n\n{result.final_newsletter_markdown}"
    state.newsletter_title = result.title
    state.complete_step(
        "rewrite",
        message=f"Newsletter rewritten. Title: {result.title!r}. "
                f"Iterations: {result.iterations}. Accepted: {result.accepted}.",
    )
    state.save_checkpoint("rewrite")

    runs_dir = Path("runs") / session_id
    runs_dir.mkdir(parents=True, exist_ok=True)
    (runs_dir / "rewrite.json").write_text(json.dumps({
        "session_id": session_id,
        "completed_at": datetime.now().isoformat(),
        "title": result.title,
        "transcript": {
            "iterations": result.iterations,
            "scores": result.scores,
            "feedbacks": result.feedbacks,
            "accepted": result.accepted,
            "final_length": len(result.final_newsletter_markdown),
        },
    }, indent=2))
    click.echo(f"Rewrite: '{result.title}' — {result.iterations} iteration(s), accepted={result.accepted}.")
    deliver_newsletter(
        session_id=session_id,
        db_path=db_path,
        to=email_to,
        no_email=no_email,
    )


def _load_result(results_dir: Path) -> tuple[Optional[RewriteResult], list[str]]:
    problems: list[str] = []
    if not results_dir.exists():
        problems.append(f"results dir not found: {results_dir}")
        return None, problems
    files = sorted(results_dir.glob("batch-*.json"))
    if not files:
        problems.append(f"no batch-*.json files in {results_dir}")
        return None, problems
    if len(files) > 1:
        problems.append(f"expected one result file, found {len(files)}; using first")
    f = files[0]
    try:
        raw = json.loads(f.read_text())
        parsed = RewriteResult.model_validate(raw)
        return parsed, problems
    except (json.JSONDecodeError, ValidationError) as exc:
        problems.append(f"{f.name}: {exc}")
        return None, problems


@click.command()
@click.option("--db", "db_path", default="newsletter_agent.db")
@click.option("--session", "session_id", required=True)
@click.option("--max-edits", default=2, type=int)
@click.option("--engine", default=None, help="Override LLM engine for classic mode")
@click.option("--prepare-batches", is_flag=True,
              help="Write batch to runs/<SID>/rewrite-batches/ for subagent dispatch")
@click.option("--apply-results", "apply_results", default=None,
              help="Read result JSON from this dir and apply to state")
@click.option("--finalize", is_flag=True,
              help="Mechanically assemble the result from loop work files (--body, "
                   "--title-file, --scores, --feedbacks, --accepted) and apply — no LLM.")
@click.option("--body", "body_path", default=None,
              help="[--finalize] path to the final newsletter body markdown.")
@click.option("--title-file", "title_file", default=None,
              help="[--finalize] path to a file containing the bare title.")
@click.option("--scores", default=None,
              help="[--finalize] CSV of per-iteration critique scores, e.g. 6.2,6.8")
@click.option("--feedbacks", default=None,
              help="[--finalize] CSV of critique feedback file paths, one per iteration.")
@click.option("--accepted/--not-accepted", "accepted", default=False,
              help="[--finalize] whether the critic loop accepted the final draft.")
@click.option("--to", "email_to", default=None,
              help="Email recipient for the auto-sent newsletter (defaults to GMAIL_USER).")
@click.option("--no-email", "no_email", is_flag=True,
              help="Render and write out/<date>.html but skip the Gmail send.")
def cli(
    db_path: str,
    session_id: str,
    max_edits: int,
    engine: Optional[str],
    prepare_batches: bool,
    apply_results: Optional[str],
    finalize: bool,
    body_path: Optional[str],
    title_file: Optional[str],
    scores: Optional[str],
    feedbacks: Optional[str],
    accepted: bool,
    email_to: Optional[str],
    no_email: bool,
) -> None:
    if sum(bool(x) for x in (prepare_batches, apply_results, finalize)) > 1:
        raise click.UsageError("--prepare-batches, --apply-results and --finalize are mutually exclusive")

    state = NewsletterAgentState(session_id=session_id, db_path=db_path).load_latest_from_db()
    if state is None:
        raise click.ClickException(f"No state found for session {session_id}")

    # ── prepare mode ───────────────────────────────────────────────
    if prepare_batches:
        state.start_step("rewrite")
        state.save_checkpoint("rewrite")
        draft = _initial_draft(state)
        if not draft.strip():
            click.echo("Nothing to rewrite (no section markdowns).")
            return
        path = _prepare_batch(session_id, draft, max_edits)
        # also materialize the initial draft as a standalone work file so the
        # critic loop's first pass and --finalize can always point at a file
        # (even when the loop accepts on iteration 0 and no improve draft exists).
        work_dir = Path("runs") / session_id / "rewrite-work"
        work_dir.mkdir(parents=True, exist_ok=True)
        (work_dir / "draft-init.md").write_text(draft)
        click.echo(f"Prepared rewrite batch (max_edits={max_edits}): {path}")
        click.echo(f"Initial draft body: runs/{session_id}/rewrite-work/draft-init.md")
        click.echo("\nDrive the critic loop (see skills/rewrite/SKILL.md): dispatch a")
        click.echo("CRITIQUE Agent then (if score < 8.0) an IMPROVE Agent per iteration,")
        click.echo("then a TITLE Agent. Each writes to runs/<SID>/rewrite-work/. Finish with:")
        click.echo(f"  python -m lib.steps.rewrite --session {session_id} --finalize \\")
        click.echo(f"    --body runs/{session_id}/rewrite-work/draft-<last>.md \\")
        click.echo(f"    --title-file runs/{session_id}/rewrite-work/title.txt \\")
        click.echo("    --scores <csv> --feedbacks <csv-of-critique-paths> --accepted|--not-accepted")
        return

    # ── finalize mode (mechanical assemble + apply from work files) ──
    if finalize:
        missing = [n for n, v in
                   (("--body", body_path), ("--title-file", title_file),
                    ("--scores", scores), ("--feedbacks", feedbacks)) if not v]
        if missing:
            raise click.UsageError(f"--finalize requires: {', '.join(missing)}")
        title = Path(title_file).read_text().strip()  # type: ignore[arg-type]
        result = _build_result_from_work(body_path, title, scores, feedbacks, accepted)  # type: ignore[arg-type]
        _apply_result(state, result, session_id, db_path, email_to, no_email)
        return

    # ── apply mode ─────────────────────────────────────────────────
    if apply_results:
        result, problems = _load_result(Path(apply_results))
        if problems:
            click.echo("Apply found problems:", err=True)
            for p in problems:
                click.echo(f"  - {p}", err=True)
        if result is None:
            raise click.ClickException("no usable rewrite result; redispatch")
        _apply_result(state, result, session_id, db_path, email_to, no_email)
        return

    # ── classic mode ───────────────────────────────────────────────
    state.start_step("rewrite")
    state.save_checkpoint("rewrite")
    draft = _initial_draft(state)
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
    title_result = call_prompt(
        "generate_newsletter_title",
        {"newsletter_markdown": final_body},
        engine=engine,
    )
    title = title_result.title
    state.final_newsletter = f"# {title}\n\n{final_body}"
    state.newsletter_title = title
    state.complete_step(
        "rewrite",
        message=f"Newsletter rewritten. Title: {title!r}. "
                f"Iterations: {transcript.iterations}. Accepted: {transcript.accepted}.",
    )
    state.save_checkpoint("rewrite")

    runs_dir = Path("runs") / session_id
    runs_dir.mkdir(parents=True, exist_ok=True)
    (runs_dir / "rewrite.json").write_text(json.dumps({
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
    }, indent=2))
    click.echo(f"Rewrite: '{title}' — {transcript.iterations} iteration(s), accepted={transcript.accepted}.")
    deliver_newsletter(
        session_id=session_id,
        db_path=db_path,
        to=email_to,
        no_email=no_email,
    )


if __name__ == "__main__":  # pragma: no cover
    sys.exit(cli())
