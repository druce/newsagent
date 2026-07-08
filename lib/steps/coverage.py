"""newsagent:coverage — count same-day same-event coverage and boost ratings.

Between `crossdedupe` and `rate`. Embeds `title + short_summary` for every
summarized headline, builds the full pairwise cosine matrix, shortlists pairs
above a cosine threshold, and asks a Haiku `same_story_sameday` judge (shown the
FULL bullet `summary`) whether each shortlisted pair reports the same event.
Confirmed-same pairs are grouped by union-find; every headline is stamped
`coverage_count = size of its component`. Nothing is dropped or merged — `rate`
turns the count into a `log2(count)` importance boost and `select`'s MMR keeps
one representative.

Three modes, mirroring `crossdedupe`:
  1. classic (default): in-process `same_story_sameday` calls via `call_prompt`.
  2. --prepare-batches: write 25-pair batches for Haiku subagent dispatch.
  3. --apply-results DIR: read verdicts, group, stamp coverage_count.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

import click
import numpy as np
from pydantic import ValidationError

import lib.prompts  # noqa: F401 — register same_story_sameday
from lib.embeddings import embed_texts
from lib.llm import call_prompt, get_prompt
from lib.prompts.same_story_sameday import (
    SameStorySamedayInput,
    SameStorySamedayOutput,
)
from lib.state import NewsletterAgentState


_DEFAULT_BATCH = 25
_DEFAULT_SHORTLIST_THRESHOLD = 0.70
_BATCHES_SUBDIR = "coverage-batches"
_RESULTS_SUBDIR = "coverage-results"


# ── candidate selection + shortlist ──────────────────────────────────

def _candidates(state: NewsletterAgentState) -> list[tuple[int, dict]]:
    """(index, headline) for headlines carrying a non-empty short_summary."""
    return [
        (i, h) for i, h in enumerate(state.headline_data)
        if (h.get("short_summary") or "").strip()
    ]


def _candidate_text(h: dict) -> str:
    return (h.get("title", "") + "\n" + (h.get("short_summary") or "")).strip()


def _shortlist_pairs(
    cand_vecs: list[list[float]], threshold: float
) -> list[tuple[int, int]]:
    """Candidate-local index pairs (i<j) with cosine ≥ threshold."""
    if len(cand_vecs) < 2:
        return []
    M = np.array(cand_vecs, dtype=float)
    norms = np.linalg.norm(M, axis=1, keepdims=True)
    M = M / np.where(norms == 0, 1.0, norms)
    sims = M @ M.T
    pairs: list[tuple[int, int]] = []
    n = sims.shape[0]
    for i in range(n):
        for j in range(i + 1, n):
            if float(sims[i, j]) >= threshold:
                pairs.append((i, j))
    return pairs


def _build_pairs(
    state: NewsletterAgentState,
    cand_index: list[int],
    shortlist: list[tuple[int, int]],
) -> list[dict]:
    """One pair dict per shortlisted pair; `id` encodes both global indices."""
    pairs: list[dict] = []
    for i, j in shortlist:
        gi, gj = cand_index[i], cand_index[j]
        a = state.headline_data[gi]
        b = state.headline_data[gj]
        pairs.append({
            "id": f"{gi}-{gj}",
            "a_title": a.get("title", ""),
            "a_summary": a.get("summary", ""),
            "b_title": b.get("title", ""),
            "b_summary": b.get("summary", ""),
        })
    return pairs


def _group_counts(
    cand_index: list[int], same_pair_ids: set[str]
) -> dict[int, int]:
    """Union-find over global indices; return global_index -> component size."""
    parent = {gi: gi for gi in cand_index}

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for pid in same_pair_ids:
        try:
            a_str, b_str = pid.split("-", 1)
            a, b = int(a_str), int(b_str)
        except (ValueError, KeyError):
            continue
        if a in parent and b in parent:
            union(a, b)

    sizes: dict[int, int] = {}
    for gi in cand_index:
        root = find(gi)
        sizes[root] = sizes.get(root, 0) + 1
    return {gi: sizes[find(gi)] for gi in cand_index}
