"""newsagent:filter — classify headlines as AI-relevant.

Three modes:
  1. classic (default): in-process LLM calls via call_prompt. Single engine.
     Use --engine to override (e.g. openrouter:google/gemini-2.5-flash).
  2. --prepare-batches: split headlines into batch prompt files for
     parallel Haiku subagent dispatch by the calling Claude session.
     Each batch file under runs/<SID>/filter-batches/batch-NN.json contains
     a self-contained system_prompt + user_prompt + items list.
  3. --apply-results DIR: read result JSONs (one per batch) from DIR,
     apply classifications to state and DB.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

import click
from pydantic import ValidationError

import lib.prompts  # noqa: F401 — register FILTER_URLS
from lib.llm import call_prompt, get_prompt
from lib.prompts.filter_urls import FilterUrlsInput, FilterUrlsOutput
from lib.state import NewsletterAgentState


_DEFAULT_BATCH = 25
_BATCHES_SUBDIR = "filter-batches"
_RESULTS_SUBDIR = "filter-results"


def _pending_items(state: NewsletterAgentState) -> list[dict]:
    """Headlines that still need is_ai classification."""
    return [
        {"id": str(i), "title": h["title"]}
        for i, h in enumerate(state.headline_data)
        if "is_ai" not in h
    ]


def _render_prompts(items: list[dict]) -> tuple[str, str]:
    cfg = get_prompt("filter_urls")
    validated = FilterUrlsInput.model_validate({"items": items})
    user = cfg.user_prompt.format(**validated.model_dump())
    return cfg.system_prompt, user


def _write_batches(
    session_id: str, items: list[dict], batch_size: int
) -> tuple[Path, list[Path]]:
    """Write per-batch JSON prompt files. Returns (batches_dir, batch_paths)."""
    runs_dir = Path("runs") / session_id
    batches_dir = runs_dir / _BATCHES_SUBDIR
    if batches_dir.exists():
        for p in batches_dir.glob("batch-*.json"):
            p.unlink()
    batches_dir.mkdir(parents=True, exist_ok=True)

    paths: list[Path] = []
    for batch_idx, start in enumerate(range(0, len(items), batch_size)):
        chunk = items[start:start + batch_size]
        system, user = _render_prompts(chunk)
        payload = {
            "batch_id": batch_idx,
            "session_id": session_id,
            "ids": [it["id"] for it in chunk],
            "items": chunk,
            "system_prompt": system,
            "user_prompt": user,
            "output_schema": FilterUrlsOutput.model_json_schema(),
        }
        path = batches_dir / f"batch-{batch_idx:03d}.json"
        path.write_text(json.dumps(payload, indent=2))
        paths.append(path)
    return batches_dir, paths


def _load_results(results_dir: Path, expected_ids: set[str]) -> tuple[dict[str, bool], list[str]]:
    """Load all result JSONs in results_dir.

    Returns (classifications, problems) where:
      - classifications maps id -> is_ai (bool)
      - problems is a list of human-readable error messages
        (missing files, malformed JSON, schema violations, missing/extra ids).
    """
    classifications: dict[str, bool] = {}
    problems: list[str] = []

    if not results_dir.exists():
        problems.append(f"results dir not found: {results_dir}")
        return {}, problems

    files = sorted(results_dir.glob("batch-*.json"))
    if not files:
        problems.append(f"no batch-*.json files in {results_dir}")
        return {}, problems

    seen_ids: set[str] = set()
    for f in files:
        try:
            raw = json.loads(f.read_text())
        except json.JSONDecodeError as exc:
            problems.append(f"{f.name}: invalid JSON ({exc})")
            continue
        try:
            parsed = FilterUrlsOutput.model_validate(raw)
        except ValidationError as exc:
            problems.append(f"{f.name}: schema mismatch ({exc.error_count()} errors)")
            continue
        for c in parsed.classifications:
            if c.id in seen_ids:
                problems.append(f"{f.name}: duplicate id {c.id!r}")
            seen_ids.add(c.id)
            classifications[c.id] = c.is_ai

    missing = expected_ids - seen_ids
    extra = seen_ids - expected_ids
    if missing:
        problems.append(f"missing classifications for ids: {sorted(missing)[:10]}...")
    if extra:
        problems.append(f"unexpected ids in results: {sorted(extra)[:10]}...")
    return classifications, problems


def _apply_classifications(
    state: NewsletterAgentState,
    classifications: dict[str, bool],
    keep_non_ai: bool,
    db_path: str,
) -> tuple[int, int]:
    """Returns (ai_count, kept_count)."""
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

    with sqlite3.connect(db_path) as conn:
        for h in state.headline_data:
            if "is_ai" in h:
                conn.execute(
                    "UPDATE urls SET isAI=? WHERE initial_url=?",
                    (1 if h["is_ai"] else 0, h["url"]),
                )
        conn.commit()
    return ai_count, len(state.headline_data)


def _write_report(session_id: str, total: int, ai_count: int, kept: int) -> None:
    runs_dir = Path("runs") / session_id
    runs_dir.mkdir(parents=True, exist_ok=True)
    (runs_dir / "filter.json").write_text(json.dumps({
        "session_id": session_id,
        "completed_at": datetime.now().isoformat(),
        "total": total,
        "ai": ai_count,
        "kept": kept,
    }, indent=2))


@click.command()
@click.option("--db", "db_path", default="newsletter_agent.db")
@click.option("--session", "session_id", required=True)
@click.option("--keep-non-ai", is_flag=True,
              help="Keep non-AI headlines in state (default: drop)")
@click.option("--engine", default=None,
              help="Override engine for classic mode (e.g. openrouter:google/gemini-2.5-flash)")
@click.option("--prepare-batches", is_flag=True,
              help="Write batches to runs/<SID>/filter-batches/ for subagent dispatch")
@click.option("--apply-results", "apply_results", default=None,
              help="Read result JSONs from this dir and apply to state")
@click.option("--batch-size", type=int, default=_DEFAULT_BATCH,
              show_default=True, help="Items per batch (prepare-batches mode)")
def cli(
    db_path: str,
    session_id: str,
    keep_non_ai: bool,
    engine: str | None,
    prepare_batches: bool,
    apply_results: str | None,
    batch_size: int,
) -> None:
    if prepare_batches and apply_results:
        raise click.UsageError("--prepare-batches and --apply-results are mutually exclusive")

    state = NewsletterAgentState(session_id=session_id, db_path=db_path).load_latest_from_db()
    if state is None:
        raise click.ClickException(f"No state found for session {session_id}")

    # ── prepare mode ───────────────────────────────────────────────
    if prepare_batches:
        state.start_step("filter")
        state.save_checkpoint("filter")
        items = _pending_items(state)
        if not items:
            click.echo("Nothing to filter.")
            return
        _, paths = _write_batches(session_id, items, batch_size)
        click.echo(f"Prepared {len(paths)} batches ({len(items)} items, batch size {batch_size}).")
        for p in paths:
            click.echo(f"  {p}")
        click.echo(f"\nDispatch one Haiku subagent per batch. Write each result to:")
        click.echo(f"  runs/{session_id}/{_RESULTS_SUBDIR}/batch-NNN.json")
        click.echo(f"Then run: python -m lib.steps.filter --session {session_id} "
                   f"--apply-results runs/{session_id}/{_RESULTS_SUBDIR}")
        return

    # ── apply mode ─────────────────────────────────────────────────
    if apply_results:
        items = _pending_items(state)
        expected_ids = {it["id"] for it in items}
        classifications, problems = _load_results(Path(apply_results), expected_ids)
        if problems:
            click.echo("Apply found problems:", err=True)
            for p in problems:
                click.echo(f"  - {p}", err=True)
            # Hard fail if no usable classifications at all
            if not classifications:
                raise click.ClickException("no usable classifications; redispatch failed batches")

        ai_count, kept = _apply_classifications(state, classifications, keep_non_ai, db_path)
        state.complete_step("filter", message=f"{ai_count}/{len(items)} AI-relevant")
        state.save_checkpoint("filter")
        _write_report(session_id, len(items), ai_count, kept)
        click.echo(f"Applied {len(classifications)} classifications: "
                   f"{ai_count}/{len(items)} AI-relevant, {kept} kept.")
        return

    # ── classic mode ───────────────────────────────────────────────
    state.start_step("filter")
    state.save_checkpoint("filter")

    items = _pending_items(state)
    if not items:
        state.complete_step("filter", message="nothing to filter")
        state.save_checkpoint("filter")
        click.echo("Nothing to filter.")
        return

    classifications: dict[str, bool] = {}
    for i in range(0, len(items), batch_size):
        batch = items[i:i + batch_size]
        result = call_prompt("filter_urls", {"items": batch}, engine=engine)
        assert isinstance(result, FilterUrlsOutput)
        for c in result.classifications:
            classifications[c.id] = c.is_ai

    ai_count, kept = _apply_classifications(state, classifications, keep_non_ai, db_path)
    state.complete_step("filter", message=f"{ai_count}/{len(items)} AI-relevant")
    state.save_checkpoint("filter")
    _write_report(session_id, len(items), ai_count, kept)
    click.echo(f"Filtered: {ai_count}/{len(items)} AI-relevant, {kept} kept.")


if __name__ == "__main__":  # pragma: no cover
    sys.exit(cli())
