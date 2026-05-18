"""news:diff — compare two sessions side-by-side.

CLI entry: python -m lib.steps.diff <SID1> <SID2> [--db PATH]

Outputs a markdown table comparing:
- Headline count, section count, newsletter length
- Per-step status for each workflow step
- Jaccard URL overlap of selected newsletter headlines
- Newsletter titles
"""
from __future__ import annotations

import sys

import click

from lib.state import NewsletterAgentState, StepStatus, WORKFLOW_STEPS


_STATUS_ICONS = {
    StepStatus.COMPLETE: "✓",
    StepStatus.ERROR: "✗",
    StepStatus.STARTED: "▶",
    StepStatus.NOT_STARTED: "⭕",
    StepStatus.SKIPPED: "—",
}


def _extract_urls(state: NewsletterAgentState) -> set[str]:
    """Extract selected article URLs from newsletter_section_data.

    The section_data can take two shapes:
    - post-select: [{"link": url, "headline": ..., "cat": ...}, ...]
    - post-draft:  [{"cat": ..., "section_markdown": ...}, ...]
    """
    urls: set[str] = set()
    for item in state.newsletter_section_data:
        if isinstance(item, dict) and "link" in item:
            urls.add(item["link"])
    return urls


def _jaccard(a: set, b: set) -> float:
    if not a and not b:
        return 1.0
    union = a | b
    if not union:
        return 1.0
    return len(a & b) / len(union)


def _load_state(session_id: str, db_path: str) -> NewsletterAgentState | None:
    state = NewsletterAgentState(session_id=session_id, db_path=db_path)
    return state.load_latest_from_db()


@click.command()
@click.argument("session_id_1")
@click.argument("session_id_2")
@click.option("--db", "db_path", default="newsletter_agent.db",
              help="Path to SQLite database")
def cli(session_id_1: str, session_id_2: str, db_path: str) -> None:
    """Compare two sessions side-by-side."""
    s1 = _load_state(session_id_1, db_path)
    s2 = _load_state(session_id_2, db_path)

    missing = []
    if s1 is None:
        missing.append(session_id_1)
    if s2 is None:
        missing.append(session_id_2)
    if missing:
        click.echo(f"Error: session(s) not found: {', '.join(missing)}", err=True)
        sys.exit(1)

    urls1 = _extract_urls(s1)
    urls2 = _extract_urls(s2)
    jaccard = _jaccard(urls1, urls2)
    overlap_pct = f"{jaccard * 100:.1f}%"

    # --- Summary table ---
    click.echo(f"## Session diff: {session_id_1} vs {session_id_2}\n")
    click.echo(f"| Metric | {session_id_1} | {session_id_2} |")
    click.echo("|---|---|---|")
    click.echo(f"| Title | {s1.newsletter_title or '(none)'} | {s2.newsletter_title or '(none)'} |")
    click.echo(f"| Headline count | {len(s1.headline_data)} | {len(s2.headline_data)} |")
    click.echo(f"| Section count | {len(s1.newsletter_section_data)} | {len(s2.newsletter_section_data)} |")
    click.echo(f"| Newsletter length | {len(s1.final_newsletter)} chars | {len(s2.final_newsletter)} chars |")
    click.echo(f"| URL overlap (Jaccard) | {overlap_pct} | — |")
    click.echo(f"| URLs in common | {len(urls1 & urls2)} of {len(urls1 | urls2)} total | — |")

    # --- Per-step status table ---
    click.echo(f"\n## Step-by-step status\n")
    click.echo(f"| Step | {session_id_1} | {session_id_2} |")
    click.echo("|---|---|---|")
    for step_id, step_name, _ in WORKFLOW_STEPS:
        step1 = s1.get_step(step_id)
        step2 = s2.get_step(step_id)
        icon1 = _STATUS_ICONS.get(step1.status, "?") if step1 else "?"
        icon2 = _STATUS_ICONS.get(step2.status, "?") if step2 else "?"
        click.echo(f"| {step_name} ({step_id}) | {icon1} | {icon2} |")


if __name__ == "__main__":  # pragma: no cover
    sys.exit(cli())
