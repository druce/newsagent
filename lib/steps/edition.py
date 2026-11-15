"""newsagent:edition — draft today's issue in house style from the curated picks.

Reads `published_articles` rows with selected=1 from sessions since noon
yesterday (the curate page's window), and drives two subagent passes:

    .venv/bin/python -m lib.steps.edition --prepare-sections [--kickers 3] [--since SID]
    #   → dispatch one Opus agent per runs/<RUN>/edition-sections-batches/batch-NNN
    .venv/bin/python -m lib.steps.edition --apply-sections RUN
    #   → validates, then writes the final-edit batch; dispatch one Opus agent
    .venv/bin/python -m lib.steps.edition --apply-edit RUN
    #   → out/edition-<stamp>.md + out/latest-edition.md
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import click

from lib.curate import selected_rows, window_start
from lib.edition import (
    EDIT_BATCHES,
    EDIT_RESULTS,
    apply_edit,
    apply_sections,
    build_edit_batch,
    build_section_batches,
    recent_kickers,
    validate_issue,
)
from lib.prompts._edition_common import load_style_docs

_RUNS = Path("runs")
_OUT = Path("out")


def _kb(p: Path) -> str:
    return f"{p.stat().st_size / 1024:.0f}KB"


@click.command()
@click.option("--prepare-sections", is_flag=True,
              help="Write one section batch per category from the curated picks.")
@click.option("--apply-sections", "apply_sections_run", default=None, metavar="RUN",
              help="Validate section results and write the final-edit batch.")
@click.option("--apply-edit", "apply_edit_run", default=None, metavar="RUN",
              help="Validate the final edit and render out/edition-<stamp>.md.")
@click.option("--kickers", "kicker_count", default=3, show_default=True, type=int,
              help="Alternative kickers suggested under each item.")
@click.option("--since", default=None,
              help="Earliest session id (default: noon yesterday).")
@click.option("--db", "db_path", default="newsletter_agent.db", show_default=True)
def cli(prepare_sections: bool, apply_sections_run: str | None,
        apply_edit_run: str | None, kicker_count: int, since: str | None,
        db_path: str) -> None:
    """Draft the issue from published_articles picks (see skills/edition/SKILL.md)."""
    modes = [prepare_sections, bool(apply_sections_run), bool(apply_edit_run)]
    if sum(modes) != 1:
        raise click.UsageError(
            "pass exactly one of --prepare-sections, --apply-sections, --apply-edit")
    now = datetime.now()

    if prepare_sections:
        if kicker_count < 1:
            raise click.UsageError("--kickers must be >= 1")
        since = since or window_start(now)
        rows = selected_rows(db_path, since)
        if not rows:
            raise click.ClickException(f"no selected articles since {since}")
        run = f"edition-{now:%Y-%m-%d-%H-%M-%S}"
        paths = build_section_batches(rows, _RUNS / run, kicker_count, load_style_docs())
        click.echo(f"Run: {run}  ({len(rows)} selected since {since}, "
                   f"{len(paths)} sections, {kicker_count} alt kickers each)")
        for p in paths:
            meta = json.loads(p.read_text())
            md = p.with_suffix(".md")
            click.echo(f"  {p}  prompt {md} ({_kb(md)})  "
                       f"{len(meta['ids'])} items  {meta['cat']}")
        return

    if apply_sections_run:
        run_dir = _RUNS / apply_sections_run
        sections, problems = apply_sections(run_dir)
        if problems:
            click.echo("Section results not ready — re-dispatch these batches:")
            for p in problems:
                click.echo(f"  - {p}")
            raise SystemExit(1)
        edit = build_edit_batch(run_dir, load_style_docs(), recent_kickers(_OUT, now))
        n = sum(len(s["items"]) for s in sections)
        md = edit.with_suffix(".md")
        click.echo(f"Applied {len(sections)} sections ({n} items).")
        click.echo(f"Final-edit batch: {edit}  prompt {md} ({_kb(md)})")
        return

    assert apply_edit_run is not None
    run_dir = _RUNS / apply_edit_run
    result_path = run_dir / EDIT_RESULTS / "batch-000.json"
    if not result_path.exists():
        raise click.ClickException(f"no final-edit result at {result_path}")
    batch = json.loads((run_dir / EDIT_BATCHES / "batch-000.json").read_text())
    result = json.loads(result_path.read_text())
    _, warnings = validate_issue(result, batch)
    try:
        path = apply_edit(run_dir, db_path, _OUT, now=now)
    except ValueError as exc:
        raise click.ClickException(str(exc))
    n = sum(len(c["items"]) for c in result["clusters"])
    flags = sum(1 for c in result["clusters"] for it in c["items"] if it.get("flag"))
    click.echo(f"Wrote {path} (and {_OUT / 'latest-edition.md'}): "
               f"{len(result['clusters'])} clusters, {n} items, {flags} flagged.")
    click.echo(f"Subtitle: {result['subtitle']}")
    for note in result.get("notes") or []:
        click.echo(f"  note: {note}")
    for w in warnings:
        click.echo(f"  warning: {w}")


if __name__ == "__main__":
    cli()
