"""Bradley-Terry Swiss-paired rating for news articles.

Exports:
  swiss_pairing(items, battle_history, current_scores) -> list[(id, id)]
  bradley_terry_from_battles(ids, battles) -> dict[id, float]
  bradley_terry_scores(items, max_rounds, items_per_battle) -> dict[id, float]

The orchestrator (bradley_terry_scores) calls BATTLE_PROMPT via call_prompt_batch
for each round. The two pure helpers (swiss_pairing, bradley_terry_from_battles)
are unit-tested without any LLM involvement.
"""
from __future__ import annotations

import math
import re
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

import choix

from lib.engines.base import EngineError
from lib.llm import call_prompt

# Items per battle batch sent to the LLM judge
_BATTLE_BATCH_SIZE = 6

# Battle retry policy. Gemini occasionally returns transient 5xx during peak
# load; one failed call would otherwise kill the entire rate step (battles run
# in parallel via ThreadPoolExecutor.map which propagates the first exception).
_BATTLE_MAX_RETRIES = 2
_BATTLE_RETRY_PAUSE_SECONDS = 60
_BATTLE_PARALLELISM = 8
_5XX_RE = re.compile(r"\b5\d{2}\b")


def _is_5xx_error(exc: BaseException) -> bool:
    return isinstance(exc, EngineError) and bool(_5XX_RE.search(str(exc)))


def _call_battle_with_retry(item: Dict[str, Any]) -> Any:
    """Send one battle prompt; retry on 5xx with a fixed pause between attempts."""
    last_exc: Optional[BaseException] = None
    for attempt in range(_BATTLE_MAX_RETRIES + 1):
        try:
            return call_prompt("battle_prompt", item)
        except EngineError as exc:
            last_exc = exc
            if attempt < _BATTLE_MAX_RETRIES and _is_5xx_error(exc):
                time.sleep(_BATTLE_RETRY_PAUSE_SECONDS)
                continue
            raise
    assert last_exc is not None
    raise last_exc


def swiss_pairing(
    items: List[dict],
    battle_history: Set[Tuple[str, str]],
    current_scores: Dict[str, float],
) -> List[Tuple[str, str]]:
    """Generate Swiss-style pairings.

    Sort items by current_scores (descending), then greedily pair each item
    with the next-highest-scored item that has not yet been paired this round
    and has not already been battled.

    Args:
        items: List of dicts, each with an "id" key.
        battle_history: Set of (id_a, id_b) pairs already battled (both
            orderings should be present if the pair has been battled).
        current_scores: Mapping of id -> current Bradley-Terry score.

    Returns:
        List of (id_a, id_b) pairs for this round.
    """
    if len(items) < 2:
        return []

    # Sort by score descending; default to 0.0 for unknown ids
    sorted_items = sorted(
        items,
        key=lambda x: current_scores.get(x["id"], 0.0),
        reverse=True,
    )

    used: Set[str] = set()
    pairs: List[Tuple[str, str]] = []

    for i, item_a in enumerate(sorted_items):
        aid = item_a["id"]
        if aid in used:
            continue

        # Find best available opponent: highest-scored not yet used, not battled
        for item_b in sorted_items[i + 1:]:
            bid = item_b["id"]
            if bid in used:
                continue
            if (aid, bid) in battle_history or (bid, aid) in battle_history:
                continue

            pairs.append((aid, bid))
            used.add(aid)
            used.add(bid)
            break

    return pairs


def bradley_terry_from_battles(
    ids: List[str],
    battles: List[Tuple[str, str]],
) -> Dict[str, float]:
    """Compute Bradley-Terry scores from (winner, loser) outcomes.

    Wraps choix.opt_pairwise, which expects 0-based integer indices.

    Args:
        ids: List of all item ids (defines the index mapping).
        battles: List of (winner_id, loser_id) tuples.

    Returns:
        Dict mapping each id to its Bradley-Terry score (log-odds scale).
    """
    n = len(ids)
    id_to_idx = {aid: idx for idx, aid in enumerate(ids)}

    # Convert string id battles to integer index battles
    indexed: List[Tuple[int, int]] = []
    for winner_id, loser_id in battles:
        w_idx = id_to_idx.get(winner_id)
        l_idx = id_to_idx.get(loser_id)
        if w_idx is not None and l_idx is not None:
            indexed.append((w_idx, l_idx))

    if not indexed:
        # No battles — return equal scores
        return {aid: 0.0 for aid in ids}

    scores_array = choix.opt_pairwise(n, indexed)
    return {aid: float(scores_array[idx]) for aid, idx in id_to_idx.items()}


def bradley_terry_scores(
    items: List[dict],
    max_rounds: Optional[int] = None,
    items_per_battle: int = _BATTLE_BATCH_SIZE,
    progress_callback: Optional[Callable[[int, int, int], None]] = None,
) -> Dict[str, float]:
    """Run iterative Swiss-paired battles and return final Bradley-Terry scores.

    This is the high-level orchestrator that drives LLM battle calls. It is
    intentionally not unit-tested (requires LLM mocks); call the two pure helpers
    directly in tests.

    Args:
        items: List of dicts with "id", "title", "summary".
        max_rounds: Maximum Swiss rounds to run. Defaults to ceil((n-1)/(batch-1)).
        items_per_battle: Number of items per LLM battle call.

    Returns:
        Dict mapping id -> Bradley-Terry score (log-odds scale).
    """
    n = len(items)
    if n < 2:
        return {item["id"]: 0.0 for item in items}

    ids = [item["id"] for item in items]
    id_to_data = {item["id"]: item for item in items}

    if max_rounds is None:
        max_rounds = max(1, math.ceil((n - 1) / (items_per_battle - 1)))

    # Initialise scores: linearly from 1.0 (first) to 0.0 (last)
    current_scores: Dict[str, float] = {
        aid: 1.0 - (i / max(n - 1, 1))
        for i, aid in enumerate(ids)
    }

    battle_history: Set[Tuple[str, str]] = set()
    all_battles: List[Tuple[str, str]] = []

    for _round in range(max_rounds):
        pairs = swiss_pairing(items, battle_history, current_scores)
        if not pairs:
            break

        # Collect all unique ids involved in this round's pairs
        round_ids: List[str] = []
        seen: Set[str] = set()
        for aid, bid in pairs:
            for rid in (aid, bid):
                if rid not in seen:
                    round_ids.append(rid)
                    seen.add(rid)

        # Group into batches of items_per_battle
        batches: List[List[dict]] = []
        for i in range(0, len(round_ids), items_per_battle):
            chunk = [id_to_data[rid] for rid in round_ids[i: i + items_per_battle]
                     if rid in id_to_data]
            if len(chunk) >= 2:
                batches.append(chunk)

        if not batches:
            break

        # Run all batches in parallel; per-call retry on 5xx so a single
        # transient Gemini blip can't kill the whole step.
        inputs = [{"items": b} for b in batches]
        with ThreadPoolExecutor(max_workers=_BATTLE_PARALLELISM) as pool:
            results = list(pool.map(_call_battle_with_retry, inputs))

        # Extract pairwise outcomes from each ranking
        for result in results:
            ranking: List[str] = result.ranking  # type: ignore[attr-defined]
            for i in range(len(ranking) - 1):
                for j in range(i + 1, len(ranking)):
                    winner_id, loser_id = ranking[i], ranking[j]
                    battle_history.add((winner_id, loser_id))
                    battle_history.add((loser_id, winner_id))
                    all_battles.append((winner_id, loser_id))

        if all_battles:
            current_scores = bradley_terry_from_battles(ids, all_battles)

        if progress_callback is not None:
            progress_callback(_round + 1, max_rounds, len(batches))

    return current_scores
