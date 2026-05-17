"""news:rate — multi-axis confidence ratings + Bradley-Terry composite score.

For each article with a summary:
  1. Run three per-axis confidence prompts (rate_quality, rate_on_topic, rate_importance).
  2. Run Swiss-paired Bradley-Terry battles (lib.rating.bradley_terry_scores).
  3. Z-score the BT values.
  4. Compute composite rating = w*(1-quality_low) + w*on_topic + w*importance + w*bt_z.
  5. Add optional recency bonus (exp decay over 48 hours).
  6. Write all signals back to headline_data[i].
"""
from __future__ import annotations

import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List

import click
import numpy as np

import lib.prompts  # noqa: F401 — register all prompts including rating prompts
from lib.config import (
    RATING_WEIGHTS,
    RECENCY_BONUS_WEIGHT,
)
from lib.llm import call_prompt
from lib.rating import bradley_terry_scores
from lib.state import NewsletterAgentState

_BATCH = 25


def _collect_confidences(
    items: List[dict],
    prompt_name: str,
    engine: str | None,
) -> Dict[str, float]:
    """Call the given rating prompt in batches; return {id: confidence}."""
    result: Dict[str, float] = {}
    for batch_start in range(0, len(items), _BATCH):
        batch = items[batch_start:batch_start + _BATCH]
        out = call_prompt(prompt_name, {"items": batch}, engine=engine)
        for sc in out.results_list:
            result[sc.id] = sc.confidence
    return result


def _z_score(scores: Dict[str, float]) -> Dict[str, float]:
    """Z-score a dict of float values. Returns 0.0 for all if std == 0."""
    values = list(scores.values())
    if not values:
        return {}
    mean = float(np.mean(values))
    std = float(np.std(values))
    if std == 0:
        return {k: 0.0 for k in scores}
    return {k: (v - mean) / std for k, v in scores.items()}


@click.command()
@click.option("--db", "db_path", default="newsletter_agent.db")
@click.option("--session", "session_id", required=True)
@click.option("--engine", default=None, help="Override engine for rating prompts")
def cli(db_path: str, session_id: str, engine: str | None) -> None:
    state = NewsletterAgentState(session_id=session_id, db_path=db_path).load_latest_from_db()
    if state is None:
        raise click.ClickException(f"No state found for session {session_id}")

    state.start_step("rate")
    state.save_checkpoint("rate")

    # Only rate headlines that have a summary
    rated_indices: List[int] = []
    items: List[dict] = []
    for i, h in enumerate(state.headline_data):
        if h.get("summary"):
            input_text = f"{h.get('title', '')}\n{h['summary']}"
            items.append({"id": str(i), "input_text": input_text})
            rated_indices.append(i)

    if not items:
        state.complete_step("rate", message="nothing to rate")
        state.save_checkpoint("rate")
        click.echo("Nothing to rate.")
        return

    # --- Per-axis confidence scores ---
    quality_dict = _collect_confidences(items, "rate_quality", engine)
    on_topic_dict = _collect_confidences(items, "rate_on_topic", engine)
    importance_dict = _collect_confidences(items, "rate_importance", engine)

    # --- Bradley-Terry scores ---
    bt_items = [
        {
            "id": str(i),
            "title": state.headline_data[i].get("title", ""),
            "summary": state.headline_data[i]["summary"],
        }
        for i in rated_indices
    ]
    raw_bt = bradley_terry_scores(bt_items)

    # Z-score BT values
    bt_z = _z_score(raw_bt)

    # --- Composite rating ---
    w = RATING_WEIGHTS
    now = datetime.now(tz=timezone.utc)

    for idx in rated_indices:
        h = state.headline_data[idx]
        key = str(idx)

        quality_low = quality_dict.get(key, 0.5)
        on_topic = on_topic_dict.get(key, 0.5)
        importance = importance_dict.get(key, 0.5)
        btz = bt_z.get(key, 0.0)

        rating = (
            w["quality_low"] * (1.0 - quality_low)
            + w["on_topic"] * on_topic
            + w["importance"] * importance
            + w["bt_z"] * btz
        )

        # Recency bonus: exp(-hours_since_published / 48) * weight
        published = h.get("published")
        if published:
            try:
                if isinstance(published, str):
                    published_dt = datetime.fromisoformat(published)
                    # Make timezone-aware if naive
                    if published_dt.tzinfo is None:
                        published_dt = published_dt.replace(tzinfo=timezone.utc)
                else:
                    published_dt = published
                hours_since = (now - published_dt).total_seconds() / 3600.0
                recency = math.exp(-hours_since / 48.0)
                rating += RECENCY_BONUS_WEIGHT * recency
            except (ValueError, TypeError, OSError):
                pass  # unparseable date — skip bonus

        # Write all signals
        h["quality_low"] = quality_low
        h["on_topic"] = on_topic
        h["importance"] = importance
        h["bt_z"] = btz
        h["bt_score"] = raw_bt.get(key, 0.0)
        h["rating"] = rating

    state.complete_step("rate", message=f"{len(rated_indices)} articles rated")
    state.save_checkpoint("rate")

    # Write run artifact
    ratings = [state.headline_data[i]["rating"] for i in rated_indices]
    runs_dir = Path("runs") / session_id
    runs_dir.mkdir(parents=True, exist_ok=True)
    (runs_dir / "rate.json").write_text(json.dumps({
        "session_id": session_id,
        "completed_at": datetime.now().isoformat(),
        "total_rated": len(rated_indices),
        "rating_distribution": {
            "min": float(min(ratings)),
            "max": float(max(ratings)),
            "mean": float(np.mean(ratings)),
        },
    }, indent=2))

    click.echo(f"Rated: {len(rated_indices)} articles. "
               f"Rating range: [{min(ratings):.3f}, {max(ratings):.3f}].")


if __name__ == "__main__":  # pragma: no cover
    sys.exit(cli())
