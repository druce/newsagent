# Coverage-Count Rating Boost — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `coverage` pipeline step that counts how many of today's articles report the same event and boosts each member's rating by `log₂(group_size)`, so widely-covered stories rank higher.

**Architecture:** A new step between `crossdedupe` and `rate` embeds `title+short_summary`, builds a full pairwise cosine matrix, shortlists near pairs (≥0.70), and confirms each with a Haiku `same_story_sameday` judge shown the full `summary`. Confirmed-same pairs are grouped by union-find; every headline is stamped `coverage_count = component size`. `rate` adds `c_coverage · log₂(coverage_count)` to its additive composite. `select`'s existing MMR keeps exactly one (boosted) representative — no drops or merges are introduced.

**Tech Stack:** Python 3.11, Click CLIs, Pydantic schemas, numpy, OpenAI `text-embedding-3-large`, `google:gemini-3.1-flash-lite` judge (interactive path uses Haiku subagents), pytest.

## Global Constraints

- **No Anthropic direct API / no `claude -p`.** The judge default engine is `google:gemini-3.1-flash-lite`; the interactive path dispatches Haiku subagents. Never `--engine subagent`.
- **Embeddings = OpenAI `text-embedding-3-large`** via `lib.embeddings.embed_texts`. No substitute.
- **One `PromptConfig` per file** under `lib/prompts/`, registered in `lib/prompts/__init__.py`.
- **Each step CLI** is a Click command at `lib/steps/<step>.py:cli`.
- **`.venv/bin/pytest`**, not system pytest.
- **TDD:** write test → confirm fail → implement → confirm pass → commit, every task.
- **`coverage_count = N` = size of the same-story connected component (includes the article itself).** Singleton ⇒ `N=1` ⇒ `log₂(1)=0` ⇒ no boost.
- Default coefficient `c_coverage = 1.0`; shortlist threshold `0.70`; batch size `25`.

---

### Task 1: `same_story_sameday` judge prompt

**Files:**
- Create: `lib/prompts/same_story_sameday.py`
- Modify: `lib/prompts/__init__.py`
- Test: `tests/test_prompt_same_story_sameday.py`

**Interfaces:**
- Produces: prompt registered under name `"same_story_sameday"`; classes `SameStorySamedayInput` (field `pairs: List[SameStorySamedayPair]`, each pair `{id, a_title, a_summary, b_title, b_summary}`), `SameStorySamedayOutput` (field `results: List[SameStorySamedayVerdict]`, each `{id: str, same: bool}`). `SameStorySamedayInput` exposes computed `input_text` (JSON of the pairs).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_prompt_same_story_sameday.py
import lib.prompts  # noqa: F401 — registers same_story_sameday
from lib.llm import get_prompt
from lib.prompts.same_story_sameday import (
    SameStorySamedayInput,
    SameStorySamedayOutput,
)


def test_prompt_registered_with_cheap_engine():
    cfg = get_prompt("same_story_sameday")
    assert cfg.name == "same_story_sameday"
    assert cfg.default_engine == "google:gemini-3.1-flash-lite"


def test_input_renders_full_summaries_into_user_prompt():
    cfg = get_prompt("same_story_sameday")
    validated = SameStorySamedayInput.model_validate({
        "pairs": [{
            "id": "3-7",
            "a_title": "OpenAI delays IPO",
            "a_summary": "- OpenAI pushed its IPO to next year\n- Valuation questioned",
            "b_title": "OpenAI listing slips",
            "b_summary": "- The OpenAI public offering is delayed\n- Investors cautious",
        }],
    })
    user = cfg.user_prompt.format(**validated.model_dump())
    assert "OpenAI pushed its IPO" in user
    assert "Investors cautious" in user


def test_output_schema_roundtrip():
    out = SameStorySamedayOutput.model_validate(
        {"results": [{"id": "3-7", "same": True}]}
    )
    assert out.results[0].id == "3-7"
    assert out.results[0].same is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_prompt_same_story_sameday.py -v`
Expected: FAIL — `ModuleNotFoundError: lib.prompts.same_story_sameday`.

- [ ] **Step 3: Write the prompt module**

```python
# lib/prompts/same_story_sameday.py
"""same_story_sameday — decide whether two of TODAY's articles report the same event.

Used by `newsagent:coverage` to count how many outlets independently covered the
same story today. Unlike `same_story` (cross-day, one-line short_summaries, framed
A=new / B=already-published), this judge is symmetric: A and B are two articles
from the same day's batch, and it is shown each article's FULL bullet `summary`.
The judge returns one boolean per pair.
"""
from __future__ import annotations

import json
from typing import List

from pydantic import BaseModel, Field, computed_field

from lib.llm import PromptConfig, register_prompt


class SameStorySamedayPair(BaseModel):
    id: str
    a_title: str
    a_summary: str
    b_title: str
    b_summary: str


class SameStorySamedayInput(BaseModel):
    pairs: List[SameStorySamedayPair] = Field(min_length=1)

    @computed_field
    @property
    def input_text(self) -> str:
        return json.dumps([p.model_dump() for p in self.pairs])


class SameStorySamedayVerdict(BaseModel):
    id: str
    same: bool


class SameStorySamedayOutput(BaseModel):
    results: List[SameStorySamedayVerdict]


_SYSTEM = """\
# ROLE AND OBJECTIVE
You are a **news coverage judge** for an AI newsletter.
For each pair of items (A and B are two articles from TODAY's batch), decide
whether A and B report the **same underlying news event**. The goal is to count
how many outlets independently covered the same story.

# WHAT COUNTS AS THE SAME STORY
- The same event covered by different outlets, with different wording, headline,
  angle, or length → **same** (this is exactly the case to catch: independent
  editorial coverage of one event).
- A piece that only restates the same event with no material new development
  → **same**.

# WHAT IS NOT THE SAME STORY
- A genuinely new development, announcement, or milestone — even about the same
  company, people, or product → **different**.
- Two stories that merely share a topic, company, or theme but describe distinct
  events → **different**.

When uncertain, prefer **different** (do not merge two stories unless you are
confident they are the same event).

# INPUT
A JSON array of pairs, each with: id, a_title, a_summary, b_title, b_summary.
The summaries are multi-bullet article summaries.

# OUTPUT
Return one verdict per input pair, echoing its id, in the provided JSON schema.
Think step-by-step **silently**; reveal only the JSON output."""

_USER = """\
Judge each pair below. Return exactly one verdict per pair, echoing each id, in \
the provided JSON schema.
{input_text}"""


SAME_STORY_SAMEDAY_PROMPT = PromptConfig(
    name="same_story_sameday",
    system_prompt=_SYSTEM,
    user_prompt=_USER,
    input_schema=SameStorySamedayInput,
    output_schema=SameStorySamedayOutput,
    default_engine="google:gemini-3.1-flash-lite",
    reasoning_effort=2,
)

register_prompt(SAME_STORY_SAMEDAY_PROMPT)
```

- [ ] **Step 4: Register the prompt**

In `lib/prompts/__init__.py`, add the import after the `same_story` line (line 19):

```python
from lib.prompts import same_story  # noqa: F401
from lib.prompts import same_story_sameday  # noqa: F401
```

And add `"same_story_sameday",` to the `__all__` list after `"same_story",`.

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_prompt_same_story_sameday.py -v`
Expected: PASS (3 tests).

- [ ] **Step 6: Commit**

```bash
git add lib/prompts/same_story_sameday.py lib/prompts/__init__.py tests/test_prompt_same_story_sameday.py
git commit -m "feat(coverage): add same_story_sameday judge prompt"
```

---

### Task 2: `coverage` term in the rating composite

**Files:**
- Modify: `lib/config.py` (add coefficient + docstring line)
- Modify: `lib/steps/rate.py` (add `import math` if absent; add composite term + stamp)
- Test: `tests/test_rating_coverage.py`

**Interfaces:**
- Consumes: each `state.headline_data[i]` may carry `coverage_count: int` (stamped by Task 4; absent ⇒ treated as 1).
- Produces: `RATING_COEFFS["coverage"]` (float, default `1.0`); `rate` adds `c["coverage"] * log2(max(coverage_count, 1))` to `rating` and stamps `h["coverage_count"]` and `h["coverage_score"]`.

- [ ] **Step 1: Write the failing test**

The composite summation is internal to `rate.cli`, so test the coefficient's presence and the boost formula directly (pure math) to avoid standing up the full rate pipeline.

```python
# tests/test_rating_coverage.py
import math

from lib.config import RATING_COEFFS


def test_coverage_coefficient_present_and_unit_scale():
    assert "coverage" in RATING_COEFFS
    assert RATING_COEFFS["coverage"] == 1.0


def _coverage_boost(coverage_count: int) -> float:
    # Mirror the exact expression added in rate.py.
    c = RATING_COEFFS
    return c["coverage"] * math.log2(max(int(coverage_count), 1))


def test_singleton_gets_no_boost():
    assert _coverage_boost(1) == 0.0


def test_boost_is_log2_of_group_size():
    assert _coverage_boost(2) == 1.0
    assert _coverage_boost(4) == 2.0
    assert _coverage_boost(8) == 3.0


def test_missing_or_zero_count_defaults_to_no_boost():
    assert _coverage_boost(0) == 0.0  # clamped to 1 → log2(1)=0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_rating_coverage.py -v`
Expected: FAIL on `test_coverage_coefficient_present_and_unit_scale` — `KeyError`/assert (`"coverage"` not in `RATING_COEFFS`).

- [ ] **Step 3: Add the coefficient**

In `lib/config.py`, add to the `RATING_COEFFS` dict (after the `"recency"` line):

```python
    "recency": 1.0,      # 2 * exp(-ln2 * age_days) - 1  in [-1, +1]
    "coverage": 1.0,     # c_coverage * log2(coverage_count); singleton (N=1) -> 0
```

And add to the docstring formula block (after the `+ c_recency * recency_score` line):

```
           + c_coverage     * log2(coverage_count)
```

- [ ] **Step 4: Add the composite term in `rate.py`**

At the top of `lib/steps/rate.py`, ensure `import math` is present (add it with the other stdlib imports if missing).

In the composite loop (currently `lib/steps/rate.py:314-348`), read the count before the `rating = (...)` expression:

```python
        content_len = _content_length(h.get("text_path"))
        adjusted_len = _adjusted_len(content_len)
        reputation = _lookup_reputation(db_path, h.get("final_url") or h.get("url", ""))
        recency = _recency_score(h.get("age_days", 1.0))
        coverage_count = max(int(h.get("coverage_count", 1) or 1), 1)
        coverage_score = math.log2(coverage_count)

        rating = (
            c["reputation"]   * reputation
            + c["adjusted_len"] * adjusted_len
            + c["on_topic"]     * on_topic
            + c["importance"]   * importance
            - c["quality_low"]  * quality_low
            + c["bt_z"]         * btz
            + c["recency"]      * recency
            + c["coverage"]     * coverage_score
        )
```

And stamp it alongside the other component fields (after `h["recency_score"] = recency`):

```python
        h["recency_score"] = recency
        h["coverage_count"] = coverage_count
        h["coverage_score"] = coverage_score
        h["rating"] = rating
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_rating_coverage.py -v`
Expected: PASS (4 tests).

- [ ] **Step 6: Commit**

```bash
git add lib/config.py lib/steps/rate.py tests/test_rating_coverage.py
git commit -m "feat(coverage): add log2 coverage-count term to rating composite"
```

---

### Task 3: `coverage` step — pure helpers (candidates, shortlist, grouping)

**Files:**
- Create: `lib/steps/coverage.py` (helpers only in this task; CLI added in Task 4)
- Test: `tests/test_coverage_helpers.py`

**Interfaces:**
- Produces (imported by Task 4 and tests):
  - `_candidates(state) -> list[tuple[int, dict]]` — `(headline_index, headline)` for headlines with a non-empty `short_summary`.
  - `_candidate_text(h: dict) -> str` — `title + "\n" + short_summary`.
  - `_shortlist_pairs(cand_vecs: list[list[float]], threshold: float) -> list[tuple[int, int]]` — candidate-local index pairs `(i, j)`, `i < j`, cosine ≥ threshold.
  - `_build_pairs(state, cand_index: list[int], shortlist: list[tuple[int,int]]) -> list[dict]` — one pair dict per shortlisted pair with `id = f"{gi}-{gj}"` (global headline indices), plus `a_title, a_summary, b_title, b_summary`.
  - `_group_counts(cand_index: list[int], same_pair_ids: set[str]) -> dict[int, int]` — union-find over global indices; returns `global_index -> component_size` for every candidate.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_coverage_helpers.py
from lib.state import NewsletterAgentState
from lib.steps.coverage import (
    _candidates,
    _candidate_text,
    _shortlist_pairs,
    _build_pairs,
    _group_counts,
)


def _state(headlines):
    s = NewsletterAgentState(session_id="t", db_path=":memory:")
    s.headline_data = headlines
    return s


def test_candidates_skip_missing_short_summary():
    s = _state([
        {"title": "A", "url": "a", "short_summary": "sa"},
        {"title": "B", "url": "b"},                       # no short_summary
        {"title": "C", "url": "c", "short_summary": "  "},  # blank
        {"title": "D", "url": "d", "short_summary": "sd"},
    ])
    idxs = [i for i, _ in _candidates(s)]
    assert idxs == [0, 3]


def test_candidate_text_joins_title_and_short_summary():
    assert _candidate_text({"title": "T", "short_summary": "S"}) == "T\nS"


def test_shortlist_pairs_upper_triangle_by_threshold():
    # v0 == v1 (cosine 1.0); v2 orthogonal to both.
    vecs = [[1.0, 0.0], [1.0, 0.0], [0.0, 1.0]]
    assert _shortlist_pairs(vecs, 0.7) == [(0, 1)]


def test_build_pairs_uses_global_indices_and_full_summary():
    s = _state([
        {"title": "A", "url": "a", "short_summary": "sa", "summary": "- full a"},
        {"title": "B", "url": "b", "short_summary": "sb", "summary": "- full b"},
    ])
    pairs = _build_pairs(s, cand_index=[0, 1], shortlist=[(0, 1)])
    assert len(pairs) == 1
    p = pairs[0]
    assert p["id"] == "0-1"
    assert p["a_summary"] == "- full a"
    assert p["b_summary"] == "- full b"


def test_group_counts_transitive_component():
    # candidates global indices 0,1,2,5 ; confirmed 0-1 and 1-2 → {0,1,2} size 3
    counts = _group_counts([0, 1, 2, 5], same_pair_ids={"0-1", "1-2"})
    assert counts == {0: 3, 1: 3, 2: 3, 5: 1}


def test_group_counts_all_singletons_when_no_pairs():
    counts = _group_counts([0, 1, 2], same_pair_ids=set())
    assert counts == {0: 1, 1: 1, 2: 1}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_coverage_helpers.py -v`
Expected: FAIL — `ModuleNotFoundError: lib.steps.coverage`.

- [ ] **Step 3: Write the helpers**

```python
# lib/steps/coverage.py
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_coverage_helpers.py -v`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add lib/steps/coverage.py tests/test_coverage_helpers.py
git commit -m "feat(coverage): shortlist + union-find grouping helpers"
```

---

### Task 4: `coverage` step — CLI (prepare / apply / classic)

**Files:**
- Modify: `lib/steps/coverage.py` (append CLI + batch I/O below the Task 3 helpers)
- Test: `tests/test_step_coverage.py`

**Interfaces:**
- Consumes: helpers from Task 3; `SameStorySamedayInput/Output` from Task 1.
- Produces: `cli` (Click command). `--prepare-batches` writes `runs/<SID>/coverage-batches/batch-NNN.json`; `--apply-results DIR` stamps `coverage_count` on every candidate headline and completes the `coverage` step; classic mode (`--engine`) runs the judge in-process. Writes `runs/<SID>/coverage.json`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_step_coverage.py
import json
from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner

import lib.prompts  # noqa: F401
from lib.db import init_db
from lib.state import NewsletterAgentState
from lib.steps.coverage import cli as coverage_cli
from lib.prompts.same_story_sameday import (
    SameStorySamedayOutput,
    SameStorySamedayVerdict,
)


def _seed(tmp_db, headlines):
    init_db(tmp_db)
    state = NewsletterAgentState(session_id="v1", db_path=tmp_db)
    state.complete_step("start")
    state.complete_step("summarize")
    state.complete_step("crossdedupe")
    state.headline_data = headlines
    state.save_checkpoint("crossdedupe")
    return state


_THREE = [
    {"title": "OpenAI delays IPO", "url": "https://a.com/1",
     "short_summary": "OpenAI pushed its IPO", "summary": "- OpenAI delayed IPO"},
    {"title": "OpenAI listing slips", "url": "https://b.com/2",
     "short_summary": "OpenAI IPO delayed", "summary": "- OpenAI offering delayed"},
    {"title": "Nvidia ships GPU", "url": "https://c.com/3",
     "short_summary": "Nvidia new GPU", "summary": "- Nvidia announced a GPU"},
]


def test_prepare_shortlists_only_near_pair(tmp_db, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    _seed(tmp_db, _THREE)

    def fake_embed(texts):
        # headlines 0 and 1 identical vectors; 2 orthogonal
        return [[1.0, 0.0], [1.0, 0.0], [0.0, 1.0]]

    with patch("lib.steps.coverage.embed_texts", side_effect=fake_embed):
        result = CliRunner().invoke(coverage_cli, [
            "--db", tmp_db, "--session", "v1", "--prepare-batches",
            "--shortlist-threshold", "0.7",
        ])
    assert result.exit_code == 0, result.output
    files = sorted(Path("runs/v1/coverage-batches").glob("batch-*.json"))
    assert len(files) == 1
    b0 = json.loads(files[0].read_text())
    assert b0["ids"] == ["0-1"]
    assert b0["pairs"][0]["a_summary"] == "- OpenAI delayed IPO"


def test_prepare_no_pairs_writes_no_batches(tmp_db, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    _seed(tmp_db, _THREE)

    def fake_embed(texts):
        return [[1.0, 0.0], [0.0, 1.0], [0.0, 0.0]]  # nothing ≥ 0.7 off-diagonal

    with patch("lib.steps.coverage.embed_texts", side_effect=fake_embed):
        result = CliRunner().invoke(coverage_cli, [
            "--db", tmp_db, "--session", "v1", "--prepare-batches",
            "--shortlist-threshold", "0.7",
        ])
    assert result.exit_code == 0, result.output
    d = Path("runs/v1/coverage-batches")
    files = list(d.glob("batch-*.json")) if d.exists() else []
    assert files == []


def test_apply_stamps_coverage_count_from_confirmed_group(tmp_db, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    _seed(tmp_db, _THREE)

    def fake_embed(texts):
        return [[1.0, 0.0], [1.0, 0.0], [0.0, 1.0]]

    with patch("lib.steps.coverage.embed_texts", side_effect=fake_embed):
        CliRunner().invoke(coverage_cli, [
            "--db", tmp_db, "--session", "v1", "--prepare-batches",
            "--shortlist-threshold", "0.7",
        ])

    results_dir = Path("runs/v1/coverage-results")
    results_dir.mkdir(parents=True)
    (results_dir / "batch-000.json").write_text(json.dumps({
        "results": [{"id": "0-1", "same": True}]
    }))

    result = CliRunner().invoke(coverage_cli, [
        "--db", tmp_db, "--session", "v1", "--apply-results", str(results_dir),
    ])
    assert result.exit_code == 0, result.output
    state = NewsletterAgentState(session_id="v1", db_path=tmp_db).load_latest_from_db()
    counts = [h.get("coverage_count") for h in state.headline_data]
    assert counts == [2, 2, 1]  # 0 and 1 grouped; 2 singleton


def test_apply_not_same_leaves_singletons(tmp_db, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    _seed(tmp_db, _THREE)

    def fake_embed(texts):
        return [[1.0, 0.0], [1.0, 0.0], [0.0, 1.0]]

    with patch("lib.steps.coverage.embed_texts", side_effect=fake_embed):
        CliRunner().invoke(coverage_cli, [
            "--db", tmp_db, "--session", "v1", "--prepare-batches",
            "--shortlist-threshold", "0.7",
        ])
    results_dir = Path("runs/v1/coverage-results")
    results_dir.mkdir(parents=True)
    (results_dir / "batch-000.json").write_text(json.dumps({
        "results": [{"id": "0-1", "same": False}]
    }))
    result = CliRunner().invoke(coverage_cli, [
        "--db", tmp_db, "--session", "v1", "--apply-results", str(results_dir),
    ])
    assert result.exit_code == 0, result.output
    state = NewsletterAgentState(session_id="v1", db_path=tmp_db).load_latest_from_db()
    assert [h.get("coverage_count") for h in state.headline_data] == [1, 1, 1]


def test_apply_no_candidates_completes_as_noop(tmp_db, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    _seed(tmp_db, [{"title": "x", "url": "u"}])  # no short_summary → no candidates
    result = CliRunner().invoke(coverage_cli, [
        "--db", tmp_db, "--session", "v1",
        "--apply-results", "runs/v1/coverage-results",
    ])
    assert result.exit_code == 0, result.output
    state = NewsletterAgentState(session_id="v1", db_path=tmp_db).load_latest_from_db()
    assert state.get_step("coverage").status.value == "complete"


def test_classic_mode_stamps_counts(tmp_db, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    _seed(tmp_db, _THREE)

    def fake_embed(texts):
        return [[1.0, 0.0], [1.0, 0.0], [0.0, 1.0]]

    def fake_call_prompt(name, inputs, *, engine=None):
        assert name == "same_story_sameday"
        ids = [p["id"] for p in inputs["pairs"]]
        return SameStorySamedayOutput(results=[
            SameStorySamedayVerdict(id=i, same=True) for i in ids
        ])

    with patch("lib.steps.coverage.embed_texts", side_effect=fake_embed), \
         patch("lib.steps.coverage.call_prompt", side_effect=fake_call_prompt):
        result = CliRunner().invoke(coverage_cli, [
            "--db", tmp_db, "--session", "v1",
            "--engine", "google:gemini-3.1-flash-lite",
            "--shortlist-threshold", "0.7",
        ])
    assert result.exit_code == 0, result.output
    state = NewsletterAgentState(session_id="v1", db_path=tmp_db).load_latest_from_db()
    assert [h.get("coverage_count") for h in state.headline_data] == [2, 2, 1]


def test_rejects_both_prepare_and_apply(tmp_db, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    _seed(tmp_db, [{"title": "A", "url": "u", "short_summary": "a", "summary": "b"}])
    result = CliRunner().invoke(coverage_cli, [
        "--db", tmp_db, "--session", "v1",
        "--prepare-batches", "--apply-results", "x",
    ])
    assert result.exit_code != 0
```

> Note: `state.get_step("coverage").status.value` reads the step status — `get_step` is defined at `lib/state.py:84` and returns a `WorkflowStep` whose `.status` is a `StepStatus` enum (`.value` → the string).

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_step_coverage.py -v`
Expected: FAIL — `ImportError: cannot import name 'cli' from lib.steps.coverage`.

- [ ] **Step 3: Append the CLI + batch I/O to `lib/steps/coverage.py`**

```python
# ── batch prepare / apply ────────────────────────────────────────────

def _render_prompts(pairs: list[dict]) -> tuple[str, str]:
    cfg = get_prompt("same_story_sameday")
    validated = SameStorySamedayInput.model_validate({"pairs": pairs})
    user = cfg.user_prompt.format(**validated.model_dump())
    return cfg.system_prompt, user


def _compute_pairs(
    state: NewsletterAgentState, threshold: float
) -> tuple[list[dict], list[int]]:
    """Embed candidates, shortlist by cosine, build judge pairs.

    Returns (pairs, cand_index) — cand_index is every candidate's global index,
    needed at grouping time so singletons still get coverage_count = 1.
    """
    cands = _candidates(state)
    cand_index = [i for i, _ in cands]
    if len(cands) < 2:
        return [], cand_index
    cand_vecs = embed_texts([_candidate_text(h) for _, h in cands])
    shortlist = _shortlist_pairs(cand_vecs, threshold)
    return _build_pairs(state, cand_index, shortlist), cand_index


def _write_batches(
    session_id: str, pairs: list[dict], batch_size: int
) -> list[Path]:
    batches_dir = Path("runs") / session_id / _BATCHES_SUBDIR
    if batches_dir.exists():
        for p in batches_dir.glob("batch-*.json"):
            p.unlink()
    if not pairs:
        return []
    batches_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for batch_idx, start in enumerate(range(0, len(pairs), batch_size)):
        chunk = pairs[start:start + batch_size]
        system, user = _render_prompts(chunk)
        payload = {
            "batch_id": batch_idx,
            "session_id": session_id,
            "ids": [p["id"] for p in chunk],
            "pairs": chunk,
            "system_prompt": system,
            "user_prompt": user,
            "output_schema": SameStorySamedayOutput.model_json_schema(),
        }
        path = batches_dir / f"batch-{batch_idx:03d}.json"
        path.write_text(json.dumps(payload, indent=2))
        paths.append(path)
    return paths


def _load_batch_pair_ids(session_id: str) -> set[str]:
    """All pair ids that were prepared (so apply knows what was judged)."""
    batches_dir = Path("runs") / session_id / _BATCHES_SUBDIR
    out: set[str] = set()
    if not batches_dir.exists():
        return out
    for f in sorted(batches_dir.glob("batch-*.json")):
        try:
            batch = json.loads(f.read_text())
        except json.JSONDecodeError:
            continue
        for pair in batch.get("pairs", []):
            out.add(str(pair["id"]))
    return out


def _load_results(
    results_dir: Path, expected_ids: set[str]
) -> tuple[dict[str, bool], list[str]]:
    verdicts: dict[str, bool] = {}
    problems: list[str] = []
    if not results_dir.exists():
        return {}, [f"results dir not found: {results_dir}"]
    files = sorted(results_dir.glob("batch-*.json"))
    if not files:
        return {}, [f"no batch-*.json files in {results_dir}"]
    seen: set[str] = set()
    for f in files:
        try:
            raw = json.loads(f.read_text())
        except json.JSONDecodeError as exc:
            problems.append(f"{f.name}: invalid JSON ({exc})")
            continue
        try:
            parsed = SameStorySamedayOutput.model_validate(raw)
        except ValidationError as exc:
            problems.append(f"{f.name}: schema mismatch ({exc.error_count()} errors)")
            continue
        for v in parsed.results:
            if v.id in seen:
                problems.append(f"{f.name}: duplicate id {v.id!r}")
            seen.add(v.id)
            verdicts[v.id] = v.same
    missing = expected_ids - seen
    if missing:
        problems.append(f"missing verdicts for ids: {sorted(missing)[:10]}...")
    return verdicts, problems


def _stamp_counts(
    state: NewsletterAgentState, cand_index: list[int], same_pair_ids: set[str]
) -> dict[int, int]:
    """Stamp coverage_count on every headline (candidates by component, else 1)."""
    counts = _group_counts(cand_index, same_pair_ids)
    for i, h in enumerate(state.headline_data):
        h["coverage_count"] = counts.get(i, 1)
    return counts


def _write_report(
    session_id: str, n_candidates: int, n_pairs: int,
    n_confirmed: int, counts: dict[int, int]
) -> None:
    runs_dir = Path("runs") / session_id
    runs_dir.mkdir(parents=True, exist_ok=True)
    groups = sorted({c for c in counts.values() if c > 1}, reverse=True)
    (runs_dir / "coverage.json").write_text(json.dumps({
        "session_id": session_id,
        "completed_at": datetime.now().isoformat(),
        "n_candidates": n_candidates,
        "n_shortlisted_pairs": n_pairs,
        "n_confirmed_pairs": n_confirmed,
        "n_boosted": sum(1 for c in counts.values() if c > 1),
        "max_coverage": max(counts.values()) if counts else 1,
        "multi_group_sizes": groups,
    }, indent=2))


# ── CLI ──────────────────────────────────────────────────────────────

@click.command()
@click.option("--db", "db_path", default="newsletter_agent.db")
@click.option("--session", "session_id", required=True)
@click.option("--engine", default=None,
              help="Override engine for classic mode (e.g. google:gemini-3.1-flash-lite)")
@click.option("--prepare-batches", is_flag=True,
              help="Write pair batches to runs/<SID>/coverage-batches/ for dispatch")
@click.option("--apply-results", "apply_results", default=None,
              help="Read result JSONs from this dir, group, stamp coverage_count")
@click.option("--batch-size", type=int, default=_DEFAULT_BATCH, show_default=True,
              help="Pairs per batch (prepare-batches mode)")
@click.option("--shortlist-threshold", type=float,
              default=_DEFAULT_SHORTLIST_THRESHOLD, show_default=True,
              help="Cosine cutoff for shortlisting pairs for the judge")
def cli(
    db_path: str,
    session_id: str,
    engine: str | None,
    prepare_batches: bool,
    apply_results: str | None,
    batch_size: int,
    shortlist_threshold: float,
) -> None:
    if prepare_batches and apply_results:
        raise click.UsageError("--prepare-batches and --apply-results are mutually exclusive")

    state = NewsletterAgentState(session_id=session_id, db_path=db_path).load_latest_from_db()
    if state is None:
        raise click.ClickException(f"No state found for session {session_id}")

    # ── prepare mode ───────────────────────────────────────────────
    if prepare_batches:
        state.start_step("coverage")
        state.save_checkpoint("coverage")
        pairs, _ = _compute_pairs(state, shortlist_threshold)
        paths = _write_batches(session_id, pairs, batch_size)
        if not paths:
            click.echo("No same-day coverage pairs to check.")
            return
        click.echo(f"Prepared {len(paths)} batch(es) ({len(pairs)} candidate pairs).")
        for p in paths:
            click.echo(f"  {p}")
        click.echo("\nDispatch one Haiku subagent per batch. Write each result to:")
        click.echo(f"  runs/{session_id}/{_RESULTS_SUBDIR}/batch-NNN.json")
        click.echo(f"Then run: python -m lib.steps.coverage --session {session_id} "
                   f"--apply-results runs/{session_id}/{_RESULTS_SUBDIR}")
        return

    # ── apply mode ─────────────────────────────────────────────────
    if apply_results:
        cand_index = [i for i, _ in _candidates(state)]
        expected_ids = _load_batch_pair_ids(session_id)
        if not expected_ids:
            counts = _stamp_counts(state, cand_index, same_pair_ids=set())
            state.complete_step("coverage", message="no coverage pairs")
            state.save_checkpoint("coverage")
            _write_report(session_id, len(cand_index), 0, 0, counts)
            click.echo("No coverage pairs; all stories singleton (coverage_count=1).")
            return
        verdicts, problems = _load_results(Path(apply_results), expected_ids)
        for p in problems:
            click.echo(f"  - {p}", err=True)
        same_ids = {i for i, same in verdicts.items() if same}
        counts = _stamp_counts(state, cand_index, same_pair_ids=same_ids)
        n_boosted = sum(1 for c in counts.values() if c > 1)
        state.complete_step(
            "coverage",
            message=f"{n_boosted} stories boosted (max coverage "
                    f"{max(counts.values()) if counts else 1})",
        )
        state.save_checkpoint("coverage")
        _write_report(session_id, len(cand_index), len(expected_ids), len(same_ids), counts)
        click.echo(f"Coverage: {len(same_ids)}/{len(expected_ids)} pairs confirmed; "
                   f"{n_boosted} stories boosted.")
        return

    # ── classic mode ───────────────────────────────────────────────
    state.start_step("coverage")
    state.save_checkpoint("coverage")
    pairs, cand_index = _compute_pairs(state, shortlist_threshold)
    if not pairs:
        counts = _stamp_counts(state, cand_index, same_pair_ids=set())
        state.complete_step("coverage", message="no coverage pairs")
        state.save_checkpoint("coverage")
        _write_report(session_id, len(cand_index), 0, 0, counts)
        click.echo("No same-day coverage pairs; all singleton.")
        return

    verdicts: dict[str, bool] = {}
    for i in range(0, len(pairs), batch_size):
        chunk = pairs[i:i + batch_size]
        result = call_prompt("same_story_sameday", {"pairs": chunk}, engine=engine)
        assert isinstance(result, SameStorySamedayOutput)
        for v in result.results:
            verdicts[v.id] = v.same

    same_ids = {i for i, same in verdicts.items() if same}
    counts = _stamp_counts(state, cand_index, same_pair_ids=same_ids)
    n_boosted = sum(1 for c in counts.values() if c > 1)
    state.complete_step(
        "coverage",
        message=f"{n_boosted} stories boosted (max coverage "
                f"{max(counts.values()) if counts else 1})",
    )
    state.save_checkpoint("coverage")
    _write_report(session_id, len(cand_index), len(pairs), len(same_ids), counts)
    click.echo(f"Coverage: {len(same_ids)}/{len(pairs)} pairs confirmed; "
               f"{n_boosted} stories boosted.")


if __name__ == "__main__":  # pragma: no cover
    sys.exit(cli())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_step_coverage.py -v`
Expected: PASS (7 tests). If `get_step(...)` errors, adjust that one assertion to the status idiom used in `tests/test_step_crossdedupe.py`, then re-run.

- [ ] **Step 5: Commit**

```bash
git add lib/steps/coverage.py tests/test_step_coverage.py
git commit -m "feat(coverage): coverage step CLI (prepare/apply/classic)"
```

---

### Task 5: Wire `coverage` into the workflow + orchestrator + docs

**Files:**
- Modify: `lib/state.py:25-39` (WORKFLOW_STEPS)
- Modify: `lib/steps/pipeline.py:30-63` (import, `_STEP_CLIS`, `_ENGINE_STEPS`)
- Modify: `tests/test_step_pipeline.py:16` (`_ENGINE_STEPS`)
- Create: `skills/coverage/SKILL.md`
- Modify: `skills/pipeline/SKILL.md` (13→14 steps, table row, step plan)
- Modify: `CLAUDE.md` (step count, workflow line, new prompt/step blurb)
- Test: reuse `tests/test_step_pipeline.py` + full suite

**Interfaces:**
- Consumes: `coverage.cli` from Task 4.
- Produces: `WORKFLOW_STEPS` contains `("coverage", ...)` between `crossdedupe` and `rate`; `_STEP_CLIS["coverage"]` and `_STEP_IDS` include it; orchestrator runs it in order.

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_step_pipeline.py
from lib.state import WORKFLOW_STEPS


def test_coverage_step_between_crossdedupe_and_rate():
    ids = [sid for sid, *_ in WORKFLOW_STEPS]
    assert "coverage" in ids
    assert ids.index("crossdedupe") < ids.index("coverage") < ids.index("rate")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_step_pipeline.py::test_coverage_step_between_crossdedupe_and_rate -v`
Expected: FAIL — `"coverage"` not in `ids`.

- [ ] **Step 3: Insert the step in `WORKFLOW_STEPS`**

In `lib/state.py`, add between the `crossdedupe` and `rate` rows (after line 32):

```python
    ("crossdedupe", "Cross-day Dedupe", "Drop stories already published in the last N days"),
    ("coverage",  "Coverage Count",    "Count same-day same-event coverage; boost ratings"),
    ("rate",      "Rate Articles",     "Multi-axis rating + Bradley-Terry composite"),
```

- [ ] **Step 4: Wire the orchestrator**

In `lib/steps/pipeline.py`, add the import after the `crossdedupe` import (line 36):

```python
from lib.steps import crossdedupe
from lib.steps import coverage
```

Add to `_STEP_CLIS` after the `crossdedupe` entry (line 51):

```python
    "crossdedupe": crossdedupe.cli,
    "coverage": coverage.cli,
```

Add `"coverage"` to `_ENGINE_STEPS` (line 63):

```python
_ENGINE_STEPS = frozenset({"filter", "summarize", "crossdedupe", "coverage", "rate", "cluster", "select", "draft", "rewrite"})
```

Mirror the same addition in `tests/test_step_pipeline.py:16`:

```python
_ENGINE_STEPS = {"filter", "summarize", "crossdedupe", "coverage", "rate", "cluster", "select", "draft", "rewrite"}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_step_pipeline.py -v`
Expected: PASS (including the new ordering test).

- [ ] **Step 6: Write `skills/coverage/SKILL.md`**

```markdown
---
name: coverage
description: Count how many of today's articles report the same event and boost each member's rating by log2 of the group size. Runs after crossdedupe, before rate. Embeds title+short_summary, shortlists near pairs by cosine, and a Haiku same_story_sameday judge (shown the full summary) confirms same/different. Union-find grouping stamps coverage_count on every headline; rate turns it into a log2 importance boost and MMR keeps one representative. No drops or merges.
---

# newsagent:coverage

Step 7 of /newsagent:pipeline (after `crossdedupe`, before `rate`). Restores the
legacy "widely-covered stories are more important" signal that the split of
merge-vs-rank dropped. Two execution paths.

## Interactive mode — Haiku subagent dispatch

### Step 1: prepare batches

```bash
python -m lib.steps.coverage --session SID --prepare-batches [--shortlist-threshold 0.70] [--batch-size 25]
```

Embeds `title + short_summary` for every summarized headline, builds the full
pairwise cosine matrix, and writes one 25-pair batch per
`runs/<SID>/coverage-batches/batch-NNN.json` for every pair at/above the cosine
threshold. Each pair carries both articles' **full `summary`**. Frequently
**zero** batches (most stories are singletons) — that is the normal no-op case;
run `--apply-results` straight away.

### Step 2: dispatch parallel Haiku subagents

For each `runs/<SID>/coverage-batches/batch-NNN.json`, dispatch an Agent
(`subagent_type: "general-purpose"`, `model: "haiku"`,
`description: "Judge coverage pairs batch NNN"`):

  ```
  Read runs/<SID>/coverage-batches/batch-NNN.json. It contains:
    - system_prompt, user_prompt (pre-rendered same_story_sameday task)
    - ids: the pair ids you MUST judge
    - output_schema: required JSON shape (results[] of {id, same})

  Follow system_prompt + user_prompt. Return ONLY a JSON object matching
  output_schema, one verdict per id (echo each id, no extras, no dupes).
  Write it to runs/<SID>/coverage-results/batch-NNN.json using the Write tool.
  Then report the path. Use ONLY Read and Write.
  ```

### Step 3: apply results

```bash
python -m lib.steps.coverage --session SID --apply-results runs/<SID>/coverage-results
```

Groups confirmed-same pairs by union-find and stamps
`coverage_count = component size` onto every headline (singletons → 1). Writes
`runs/<SID>/coverage.json`, marks the step complete. `rate` then adds
`c_coverage * log2(coverage_count)` to the composite.

### Step 4: retry on failure

If apply reports `missing verdicts for ids: [...]` or `schema mismatch`,
re-dispatch only the failing batch(es) (overwrite the result file), re-run apply.

## Classic mode (non-interactive)

```bash
python -m lib.steps.coverage --session SID --engine google:gemini-3.1-flash-lite
```

Runs the `same_story_sameday` judge in-process. Do not use `--engine subagent`.

## Output contract

- `state.headline_data[i].coverage_count` set for every headline (≥1).
- `runs/<SID>/coverage.json` with candidate/pair/boost counts.
- `coverage` step marked COMPLETE.
- Nothing dropped or merged — ranking effect happens in `rate`, dedup of the
  boosted near-duplicates happens in `select` (MMR).
```

- [ ] **Step 7: Update `skills/pipeline/SKILL.md`**

Change the step-plan block (around line 21) to 14 steps:

```
start → gather → filter → download → dedupe → summarize → crossdedupe →
coverage → rate → cluster → select → draft → rewrite → send
```

Add a row to the per-step table immediately after the `crossdedupe` row:

```
| coverage | prepare/dispatch/apply | haiku | 25 pairs per batch (often 0 batches — no candidates) |
```

Add `coverage` to the fan-out step list in the driver-loop section (the line
enumerating "filter, summarize, crossdedupe, cluster, draft") and update the two
"13 steps" mentions (the intro list and step-plan-resolution line ~237) to "14
steps".

- [ ] **Step 8: Update `CLAUDE.md`**

- In the Workflow section, change the ordered step line to include `coverage`
  between `crossdedupe` and `rate`, and note it as a full member of
  `WORKFLOW_STEPS`.
- In the steps/prompts inventory, add `coverage.py` (19→20 steps) and
  `same_story_sameday.py`.
- Add a one-line description mirroring the `crossdedupe` blurb:
  "`coverage` runs between `crossdedupe` and `rate`: it counts how many of
  today's articles report the same event (title+short_summary cosine shortlist +
  Haiku `same_story_sameday` judge on full summaries, union-find grouping) and
  stamps `coverage_count`; `rate` adds `c_coverage * log2(coverage_count)` so
  widely-covered stories rank higher, and `select`'s MMR keeps one representative."

- [ ] **Step 9: Run the full suite**

Run: `.venv/bin/pytest tests/ -q`
Expected: all pass (previous 266 + the new coverage/rating/prompt tests). If any
test hard-codes a 13-step count or step-index math, update it to 14 and note it
in the commit.

- [ ] **Step 10: Commit**

```bash
git add lib/state.py lib/steps/pipeline.py tests/test_step_pipeline.py skills/coverage/SKILL.md skills/pipeline/SKILL.md CLAUDE.md
git commit -m "feat(coverage): wire coverage step into workflow + orchestrator + docs"
```

---

### Task 6: End-to-end smoke on a real session

**Files:** none (verification only)

**Interfaces:** Consumes the completed step; exercises prepare → (judge) → apply → rate on real data.

- [ ] **Step 1: Prepare on the most recent completed session (dry check)**

Reuse an existing summarized session (e.g. `2026-07-07-08-06-53`) on a scratch copy of the DB, or run a fresh pipeline through `summarize`/`crossdedupe`. Then:

Run: `.venv/bin/python -m lib.steps.coverage --session <SID> --prepare-batches`
Expected: either "No same-day coverage pairs to check." or a list of batch files under `runs/<SID>/coverage-batches/`.

- [ ] **Step 2: Judge + apply**

If batches were written, dispatch Haiku agents per `skills/coverage/SKILL.md`
(or run classic: `--engine google:gemini-3.1-flash-lite`). Then:

Run: `.venv/bin/python -m lib.steps.coverage --session <SID> --apply-results runs/<SID>/coverage-results`
Expected: "Coverage: N/M pairs confirmed; K stories boosted." and a
`runs/<SID>/coverage.json`.

- [ ] **Step 3: Confirm the boost reaches rate**

Run `rate` on that session and read `runs/<SID>/rate.json` / the per-headline
`coverage_count` + `coverage_score`. Expected: multi-outlet stories show
`coverage_count > 1` and a positive `coverage_score = log2(count)`; singletons
show `coverage_count == 1`, `coverage_score == 0`.

- [ ] **Step 4: Final commit (if any doc tweaks surfaced)**

```bash
git add -A && git commit -m "docs(coverage): notes from end-to-end smoke"
```

---

## Self-Review

**Spec coverage:**
- New `coverage` step after crossdedupe, before rate → Tasks 3–5. ✓
- Embed title+short_summary, full pairwise cosine, shortlist ≥0.70 → Task 3 (`_shortlist_pairs`), Task 4 (`_compute_pairs`). ✓
- Haiku `same_story_sameday` judge on full `summary`, 25/batch, prepare/apply/classic → Tasks 1, 4. ✓
- Union-find grouping, `coverage_count = component size`, no drops/merges → Task 3 (`_group_counts`), Task 4 (`_stamp_counts`). ✓
- `RATING_COEFFS["coverage"]` + `log₂` term, safe default 1 → Task 2. ✓
- Workflow/orchestrator/pipeline-SKILL/CLAUDE/observability → Task 5; `coverage.json` → Task 4 (`_write_report`). ✓
- No change to dedupe / same_story / mmr / select / presentation → nothing in those files is touched. ✓

**Placeholder scan:** No TBD/TODO; every code step shows full code; SKILL.md content is inline. ✓

**Type consistency:** `coverage_count` (int) written in Task 4 `_stamp_counts`, read in Task 2 rate term; `SameStorySamedayOutput`/`SameStorySamedayVerdict` defined in Task 1, imported in Task 4; `_group_counts`/`_shortlist_pairs`/`_candidate_text`/`_build_pairs` signatures identical across Tasks 3 and 4. Pair `id` format `"{gi}-{gj}"` produced in `_build_pairs` and parsed in `_group_counts`. ✓
