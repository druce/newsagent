"""newsagent:summarize — extract bullet-point article summaries.

Three modes (mirror of lib/steps/filter.py):
  1. classic (default): in-process LLM calls via call_prompt. Single engine.
     Use --engine to override (e.g. openrouter:google/gemini-2.5-flash).
  2. --prepare-batches: split downloaded articles into batch prompt files
     under runs/<SID>/summarize-batches/batch-NNN.json. Each batch file is
     self-contained (system_prompt + user_prompt + items + output_schema)
     so a Sonnet subagent can run it with no further context.
  3. --apply-results DIR: read result JSONs (one per batch) from DIR,
     write summaries back to headline_data, complete the step.

The interactive (prepare + dispatch + apply) path avoids `claude -p`, which
is not covered under the Max plan. See feedback_no_claude_p.md memory.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

import click
from pydantic import ValidationError

import lib.prompts  # noqa: F401 — register EXTRACT_SUMMARIES
from lib.llm import call_prompt, get_prompt
from lib.prompts.extract_summaries import ExtractSummariesInput, ExtractSummariesOutput
from lib.state import NewsletterAgentState

_DEFAULT_BATCH = 15
_MAX_TEXT_CHARS = 20_000
_BATCHES_SUBDIR = "summarize-batches"
_RESULTS_SUBDIR = "summarize-results"


def _pending_items(state: NewsletterAgentState) -> tuple[list[dict], list[int]]:
    """Headlines with text_path but no summary.

    Returns (items, index_map) where items[i] corresponds to
    state.headline_data[index_map[i]].
    """
    items: list[dict] = []
    index_map: list[int] = []
    for i, h in enumerate(state.headline_data):
        if "text_path" not in h or "summary" in h:
            continue
        text_path = h["text_path"]
        try:
            text = Path(text_path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        text = text[:_MAX_TEXT_CHARS]
        items.append({"id": str(len(items)), "title": h.get("title", ""), "text": text})
        index_map.append(i)
    return items, index_map


def _render_prompts(items: list[dict]) -> tuple[str, str]:
    cfg = get_prompt("extract_summaries")
    validated = ExtractSummariesInput.model_validate({"items": items})
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
            "output_schema": ExtractSummariesOutput.model_json_schema(),
        }
        path = batches_dir / f"batch-{batch_idx:03d}.json"
        path.write_text(json.dumps(payload, indent=2))
        paths.append(path)
    return batches_dir, paths


def _load_results(
    results_dir: Path, expected_ids: set[str]
) -> tuple[dict[str, tuple[str, str]], list[str]]:
    """Load all result JSONs in results_dir.

    Returns (summaries, problems) where:
      - summaries maps batch-local id -> (short_summary, summary)
      - problems is a list of human-readable error messages
    """
    summaries: dict[str, tuple[str, str]] = {}
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
            parsed = ExtractSummariesOutput.model_validate(raw)
        except ValidationError as exc:
            problems.append(f"{f.name}: schema mismatch ({exc.error_count()} errors)")
            continue
        for s in parsed.summaries:
            if s.id in seen_ids:
                problems.append(f"{f.name}: duplicate id {s.id!r}")
            seen_ids.add(s.id)
            summaries[s.id] = (s.short_summary, s.summary)

    missing = expected_ids - seen_ids
    extra = seen_ids - expected_ids
    if missing:
        problems.append(f"missing summaries for ids: {sorted(missing)[:10]}...")
    if extra:
        problems.append(f"unexpected ids in results: {sorted(extra)[:10]}...")
    return summaries, problems


def _apply_summaries(
    state: NewsletterAgentState,
    summaries: dict[str, tuple[str, str]],
    index_map: list[int],
) -> int:
    applied = 0
    for item_pos, headline_idx in enumerate(index_map):
        key = str(item_pos)
        if key in summaries:
            short_summary, summary = summaries[key]
            state.headline_data[headline_idx]["short_summary"] = short_summary
            state.headline_data[headline_idx]["summary"] = summary
            applied += 1
    return applied


def _write_report(session_id: str, total: int, summarized: int) -> None:
    runs_dir = Path("runs") / session_id
    runs_dir.mkdir(parents=True, exist_ok=True)
    (runs_dir / "summarize.json").write_text(json.dumps({
        "session_id": session_id,
        "completed_at": datetime.now().isoformat(),
        "total": total,
        "summarized": summarized,
    }, indent=2))


@click.command()
@click.option("--db", "db_path", default="newsletter_agent.db")
@click.option("--session", "session_id", required=True)
@click.option("--engine", default=None,
              help="Override engine for classic mode (e.g. openrouter:google/gemini-2.5-flash)")
@click.option("--prepare-batches", is_flag=True,
              help="Write batches to runs/<SID>/summarize-batches/ for subagent dispatch")
@click.option("--apply-results", "apply_results", default=None,
              help="Read result JSONs from this dir and apply to state")
@click.option("--batch-size", type=int, default=_DEFAULT_BATCH,
              show_default=True, help="Articles per batch")
def cli(
    db_path: str,
    session_id: str,
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
        state.start_step("summarize")
        state.save_checkpoint("summarize")
        items, _ = _pending_items(state)
        if not items:
            click.echo("Nothing to summarize.")
            return
        _, paths = _write_batches(session_id, items, batch_size)
        click.echo(f"Prepared {len(paths)} batches ({len(items)} articles, batch size {batch_size}).")
        for p in paths:
            click.echo(f"  {p}")
        click.echo(f"\nDispatch one Sonnet subagent per batch. Write each result to:")
        click.echo(f"  runs/{session_id}/{_RESULTS_SUBDIR}/batch-NNN.json")
        click.echo(f"Then run: python -m lib.steps.summarize --session {session_id} "
                   f"--apply-results runs/{session_id}/{_RESULTS_SUBDIR}")
        return

    # ── apply mode ─────────────────────────────────────────────────
    if apply_results:
        items, index_map = _pending_items(state)
        expected_ids = {it["id"] for it in items}
        summaries, problems = _load_results(Path(apply_results), expected_ids)
        if problems:
            click.echo("Apply found problems:", err=True)
            for p in problems:
                click.echo(f"  - {p}", err=True)
            if not summaries:
                raise click.ClickException("no usable summaries; redispatch failed batches")

        applied = _apply_summaries(state, summaries, index_map)
        state.complete_step("summarize", message=f"{applied}/{len(items)} summarized")
        state.save_checkpoint("summarize")
        _write_report(session_id, len(items), applied)
        click.echo(f"Applied {applied}/{len(items)} summaries.")
        return

    # ── classic mode (in-process call_prompt loop) ─────────────────
    state.start_step("summarize")
    state.save_checkpoint("summarize")

    items, index_map = _pending_items(state)
    if not items:
        state.complete_step("summarize", message="nothing to summarize")
        state.save_checkpoint("summarize")
        click.echo("Nothing to summarize.")
        return

    summaries: dict[str, tuple[str, str]] = {}
    for batch_start in range(0, len(items), batch_size):
        batch = items[batch_start:batch_start + batch_size]
        result = call_prompt("extract_summaries", {"items": batch}, engine=engine)
        assert isinstance(result, ExtractSummariesOutput)
        for s in result.summaries:
            summaries[s.id] = (s.short_summary, s.summary)

    applied = _apply_summaries(state, summaries, index_map)
    state.complete_step("summarize", message=f"{applied}/{len(items)} summarized")
    state.save_checkpoint("summarize")
    _write_report(session_id, len(items), applied)
    click.echo(f"Summarized: {applied}/{len(items)} articles.")


if __name__ == "__main__":  # pragma: no cover
    sys.exit(cli())
