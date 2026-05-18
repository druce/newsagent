"""lib/run_summary.py — Generate a markdown summary for a newsletter run session.

Reads state from DB + artifacts from runs/<SID>/*.json and writes
runs/<SID>/summary.md.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from lib.state import NewsletterAgentState, WORKFLOW_STEPS


def _fmt_duration(started, completed) -> str:
    """Return human-readable duration from two datetimes, or '-' if either is None."""
    if started is None or completed is None:
        return "-"
    delta = (completed - started).total_seconds()
    if delta < 60:
        return f"{delta:.1f}s"
    minutes = int(delta // 60)
    seconds = delta % 60
    return f"{minutes}m {seconds:.1f}s"


def generate_summary(
    session_id: str,
    db_path: str = "newsletter_agent.db",
    runs_dir: str = "runs",
) -> str:
    """Return summary markdown for a session.

    Reads the latest state from the DB and lists artifacts in runs/<SID>/.
    Returns a minimal report even if no steps have completed.
    """
    # Load state
    state = NewsletterAgentState(session_id=session_id, db_path=db_path).load_latest_from_db()

    lines: list[str] = []
    lines.append(f"# Newsletter Run Summary — {session_id}")
    lines.append("")
    lines.append(f"**Database:** {db_path}")

    if state is not None:
        # Started = first step with started_at
        first_started = next(
            (s.started_at for s in state.steps if s.started_at is not None), None
        )
        last_completed = None
        for s in reversed(state.steps):
            if s.completed_at is not None:
                last_completed = s.completed_at
                break

        if first_started:
            lines.append(f"**Started:** {first_started.isoformat(timespec='seconds')}")
        else:
            lines.append("**Started:** —")

        if last_completed:
            lines.append(f"**Completed:** {last_completed.isoformat(timespec='seconds')}")
        else:
            lines.append("**Completed:** —")
    else:
        lines.append("**Started:** —")
        lines.append("**Completed:** —")

    lines.append("")

    # Workflow table
    lines.append("## Workflow")
    lines.append("")
    lines.append("| Step | Status | Timing | Message |")
    lines.append("|---|---|---|---|")

    if state is not None:
        step_map = {s.id: s for s in state.steps}
        for step_id, step_name, _ in WORKFLOW_STEPS:
            step = step_map.get(step_id)
            if step is None:
                lines.append(f"| {step_id} | — | — | — |")
                continue
            status = step.status.value
            timing = _fmt_duration(step.started_at, step.completed_at)
            msg = step.status_message or step.error_message or ""
            # Escape pipe characters in message
            msg = msg.replace("|", "\\|")
            lines.append(f"| {step_id} | {status} | {timing} | {msg} |")
    else:
        for step_id, step_name, _ in WORKFLOW_STEPS:
            lines.append(f"| {step_id} | not_started | — | — |")

    lines.append("")

    # Counts section
    lines.append("## Counts")
    lines.append("")
    if state is not None:
        headline_count = len(state.headline_data) if state.headline_data else 0
        cluster_count = len(state.cluster_names) if state.cluster_names else 0
        section_count = len(set(
            s.get("cat", "") for s in state.newsletter_section_data
        )) if state.newsletter_section_data else 0
        newsletter_len = len(state.final_newsletter) if state.final_newsletter else 0
        newsletter_title = state.newsletter_title or ""

        lines.append(f"- Headlines: {headline_count} total")
        lines.append(f"- Clusters: {cluster_count}")
        lines.append(f"- Sections: {section_count}")
        if newsletter_len:
            lines.append(f"- Final newsletter: {newsletter_len} chars, title: \"{newsletter_title}\"")
        else:
            lines.append("- Final newsletter: not yet generated")
    else:
        lines.append("- No data available")

    lines.append("")

    # Artifacts section
    runs_path = Path(runs_dir) / session_id
    artifact_paths = sorted(runs_path.glob("*.json")) if runs_path.exists() else []

    lines.append("## Artifacts")
    lines.append("")
    if artifact_paths:
        for p in artifact_paths:
            lines.append(f"- {runs_dir}/{session_id}/{p.name}")
    else:
        lines.append("- None yet")

    lines.append("")

    # Draft sections section (if draft.json exists)
    draft_artifact = runs_path / "draft.json"
    if draft_artifact.exists():
        lines.append("## Draft Sections")
        lines.append("")
        try:
            draft_data = json.loads(draft_artifact.read_text())
            sections = draft_data.get("sections", [])
            for sec in sections:
                cat = sec.get("cat", "Unknown")
                iterations = sec.get("iterations", 0)
                scores = sec.get("scores", [])
                final_score = scores[-1] if scores else 0.0
                accepted = "✓" if sec.get("accepted", False) else "✗"
                lines.append(
                    f"- {cat}: {iterations} iteration(s), "
                    f"final score {final_score:.1f}, accepted {accepted}"
                )
        except Exception:
            lines.append("- (could not parse draft.json)")

        lines.append("")

    return "\n".join(lines)


def write_summary(
    session_id: str,
    db_path: str = "newsletter_agent.db",
    runs_dir: str = "runs",
) -> str:
    """Generate summary markdown, write to runs/<SID>/summary.md, return the file path."""
    content = generate_summary(session_id, db_path=db_path, runs_dir=runs_dir)

    out_dir = Path(runs_dir) / session_id
    out_dir.mkdir(parents=True, exist_ok=True)

    out_path = out_dir / "summary.md"
    out_path.write_text(content, encoding="utf-8")

    return str(out_path)
