"""newsagent:draft — parallel section drafting with critic-optimizer loop.

Three modes (mirror of lib/steps/filter.py):
  1. classic (default): in-process critic_optimizer_loop per section,
     ThreadPoolExecutor parallel. Use --engine to override (DO NOT use
     subagent — it falls back to claude -p).
  2. --prepare-batches: write one batch JSON per section under
     runs/<SID>/draft-batches/batch-NNN.json. Each batch contains the section
     stories plus ALL THREE pre-rendered prompts (write/critique/improve) so
     a single Sonnet Agent can run the entire critic loop in its own context.
  3. --apply-results DIR: read result JSONs from DIR (one per section),
     write final_section_markdown back to state.newsletter_section_data.

In interactive mode the Agent runs the loop internally:
  write → critique → if accept or score>=8.0 stop, else improve → next iter,
  up to max_edits iterations. The result JSON captures the full transcript.
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
from pydantic import ValidationError

import lib.prompts  # noqa: F401 — register all prompts
from lib.critic import critic_optimizer_loop, CriticTranscript
from lib.llm import call_prompt, get_prompt
from lib.prompts._dispatch_schemas import DraftSectionResult
from lib.prompts.write_section import WriteSectionInput
from lib.state import NewsletterAgentState


_BATCHES_SUBDIR = "draft-batches"
_RESULTS_SUBDIR = "draft-results"


def _hostname(url: str) -> str:
    try:
        host = urlparse(url).hostname or url
        if host.startswith("www."):
            host = host[4:]
        return host
    except Exception:
        return url


def _section_input(cat: str, stories: List[dict]) -> dict:
    return {
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


def _render_section_prompts(cat: str, stories: List[dict]) -> dict:
    """Pre-render write/critique/improve prompts for one section."""
    write_cfg = get_prompt("write_section")
    crit_cfg = get_prompt("critique_section")
    imp_cfg = get_prompt("improve_section")

    write_in = WriteSectionInput.model_validate(_section_input(cat, stories))
    write_user = write_cfg.user_prompt.format(**write_in.model_dump())

    # critique/improve prompts use a section_markdown placeholder; the Agent
    # will substitute the actual draft at call time. We pass the template
    # strings (with the placeholder still present) so the Agent can format
    # them after the write call.
    return {
        "write_system_prompt": write_cfg.system_prompt,
        "write_user_prompt": write_user,
        "critique_system_prompt": crit_cfg.system_prompt,
        "critique_user_prompt": crit_cfg.user_prompt,
        "improve_system_prompt": imp_cfg.system_prompt,
        "improve_user_prompt": imp_cfg.user_prompt,
    }


def _prepare_batches(
    session_id: str, by_cat: Dict[str, List[dict]], max_edits: int,
) -> List[Path]:
    runs_dir = Path("runs") / session_id
    batches_dir = runs_dir / _BATCHES_SUBDIR
    if batches_dir.exists():
        for p in batches_dir.glob("batch-*.json"):
            p.unlink()
    batches_dir.mkdir(parents=True, exist_ok=True)

    paths: List[Path] = []
    cats = list(by_cat.keys())
    for batch_idx, cat in enumerate(cats):
        rendered = _render_section_prompts(cat, by_cat[cat])
        payload = {
            "batch_id": batch_idx,
            "session_id": session_id,
            "cat": cat,
            "stories": _section_input(cat, by_cat[cat])["stories"],
            "max_edits": max_edits,
            "accept_threshold": 8.0,
            **rendered,
            "output_schema": DraftSectionResult.model_json_schema(),
        }
        path = batches_dir / f"batch-{batch_idx:03d}.json"
        path.write_text(json.dumps(payload, indent=2))
        paths.append(path)
    return paths


def _load_results(
    results_dir: Path, expected_cats: set,
) -> tuple:
    out: dict = {}
    problems: list = []
    if not results_dir.exists():
        problems.append(f"results dir not found: {results_dir}")
        return {}, problems
    files = sorted(results_dir.glob("batch-*.json"))
    if not files:
        problems.append(f"no batch-*.json files in {results_dir}")
        return {}, problems
    for f in files:
        try:
            raw = json.loads(f.read_text())
            parsed = DraftSectionResult.model_validate(raw)
        except (json.JSONDecodeError, ValidationError) as exc:
            problems.append(f"{f.name}: {exc}")
            continue
        if parsed.cat in out:
            problems.append(f"{f.name}: duplicate cat {parsed.cat!r}")
        out[parsed.cat] = parsed
    missing = expected_cats - set(out.keys())
    extra = set(out.keys()) - expected_cats
    if missing:
        problems.append(f"missing sections: {sorted(missing)[:10]}...")
    if extra:
        problems.append(f"unexpected sections: {sorted(extra)[:10]}...")
    return out, problems


def _draft_section_classic(
    cat: str, stories: List[dict], max_edits: int, engine: Optional[str],
) -> tuple:
    """Single-section classic critic loop (call_prompt-based)."""
    initial = call_prompt("write_section", _section_input(cat, stories), engine=engine)
    transcript = critic_optimizer_loop(
        initial_draft=initial.section_markdown,
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
              help="Max critic-optimizer iterations per section")
@click.option("--parallelism", default=4, type=int,
              help="Section drafters in classic mode")
@click.option("--engine", default=None, help="Override LLM engine for classic mode")
@click.option("--prepare-batches", is_flag=True,
              help="Write batches to runs/<SID>/draft-batches/ for subagent dispatch")
@click.option("--apply-results", "apply_results", default=None,
              help="Read result JSONs from this dir and apply to state")
def cli(
    db_path: str,
    session_id: str,
    max_edits: int,
    parallelism: int,
    engine: Optional[str],
    prepare_batches: bool,
    apply_results: Optional[str],
) -> None:
    if prepare_batches and apply_results:
        raise click.UsageError("--prepare-batches and --apply-results are mutually exclusive")

    state = NewsletterAgentState(session_id=session_id, db_path=db_path).load_latest_from_db()
    if state is None:
        raise click.ClickException(f"No state found for session {session_id}")

    by_cat: Dict[str, List[dict]] = defaultdict(list)
    for item in state.newsletter_section_data:
        by_cat[item["cat"]].append(item)

    # ── prepare mode ───────────────────────────────────────────────
    if prepare_batches:
        state.start_step("draft")
        state.save_checkpoint("draft")
        if not by_cat:
            click.echo("Nothing to draft.")
            return
        paths = _prepare_batches(session_id, by_cat, max_edits)
        click.echo(f"Prepared {len(paths)} section batches (max_edits={max_edits}):")
        for p in paths:
            click.echo(f"  {p}")
        click.echo(f"\nDispatch one Sonnet subagent per batch. Each runs the full")
        click.echo(f"write→critique→improve loop in its own context. Write each result to:")
        click.echo(f"  runs/{session_id}/{_RESULTS_SUBDIR}/batch-NNN.json")
        click.echo(f"Then run: python -m lib.steps.draft --session {session_id} "
                   f"--apply-results runs/{session_id}/{_RESULTS_SUBDIR}")
        return

    # ── apply mode ─────────────────────────────────────────────────
    if apply_results:
        expected_cats = set(by_cat.keys())
        results, problems = _load_results(Path(apply_results), expected_cats)
        if problems:
            click.echo("Apply found problems:", err=True)
            for p in problems:
                click.echo(f"  - {p}", err=True)
            if not results:
                raise click.ClickException("no usable section drafts; redispatch failed batches")

        new_sections = []
        cats = list(by_cat.keys())
        transcripts_meta = []
        for cat in cats:
            if cat not in results:
                continue
            r = results[cat]
            new_sections.append({"cat": cat, "section_markdown": r.final_section_markdown})
            transcripts_meta.append({
                "cat": cat,
                "iterations": r.iterations,
                "scores": r.scores,
                "feedbacks": r.feedbacks,
                "accepted": r.accepted,
                "final_length": len(r.final_section_markdown),
            })

        state.newsletter_section_data = new_sections
        state.complete_step("draft", message=f"{len(new_sections)} sections drafted")
        state.save_checkpoint("draft")

        runs_dir = Path("runs") / session_id
        runs_dir.mkdir(parents=True, exist_ok=True)
        (runs_dir / "draft.json").write_text(json.dumps({
            "session_id": session_id,
            "completed_at": datetime.now().isoformat(),
            "sections_count": len(new_sections),
            "sections": transcripts_meta,
        }, indent=2))
        click.echo(f"Draft: {len(new_sections)}/{len(cats)} sections applied.")
        return

    # ── classic mode ───────────────────────────────────────────────
    state.start_step("draft")
    state.save_checkpoint("draft")

    cats = list(by_cat.keys())
    results_classic: Dict[str, CriticTranscript] = {}

    def _task(cat):
        return _draft_section_classic(cat, by_cat[cat], max_edits=max_edits, engine=engine)

    with ThreadPoolExecutor(max_workers=max(1, parallelism)) as pool:
        futures = {pool.submit(_task, cat): cat for cat in cats}
        for fut in as_completed(futures):
            cat, transcript = fut.result()
            results_classic[cat] = transcript

    new_sections = [
        {"cat": cat, "section_markdown": results_classic[cat].final_draft}
        for cat in cats
    ]
    state.newsletter_section_data = new_sections
    state.complete_step("draft", message=f"{len(new_sections)} sections drafted")
    state.save_checkpoint("draft")

    runs_dir = Path("runs") / session_id
    runs_dir.mkdir(parents=True, exist_ok=True)
    (runs_dir / "draft.json").write_text(json.dumps({
        "session_id": session_id,
        "completed_at": datetime.now().isoformat(),
        "sections_count": len(new_sections),
        "max_edits": max_edits,
        "sections": [
            {
                "cat": cat,
                "iterations": results_classic[cat].iterations,
                "scores": results_classic[cat].scores,
                "feedbacks": results_classic[cat].feedbacks,
                "accepted": results_classic[cat].accepted,
                "final_length": len(results_classic[cat].final_draft),
            }
            for cat in cats
        ],
    }, indent=2))
    click.echo(f"Draft: {len(new_sections)} sections (max_edits={max_edits}, parallelism={parallelism}).")


if __name__ == "__main__":  # pragma: no cover
    sys.exit(cli())
