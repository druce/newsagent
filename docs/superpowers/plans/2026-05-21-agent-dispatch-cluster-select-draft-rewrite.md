# Agent-Dispatch Conversion for cluster, select, draft, rewrite — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Convert the four pipeline steps that still default to the `subagent` engine (`claude -p`) over to the parallel Agent-dispatch pattern already used by `filter` (Haiku) and `summarize` (Sonnet), and rewrite `/newsagent:run` so parent Claude Code can drive the whole pipeline interactively without `claude -p`. Preserve classic `--engine` mode on every step for cron/CI.

**Architecture:** Every converted step gains `--prepare-batches` (writes self-contained batch JSONs under `runs/<SID>/<step>-batches/`), parent-driven parallel Agent dispatch (one model per step from a fixed table), and `--apply-results DIR` (validates Pydantic schemas, merges into state, marks step complete). For `draft`/`rewrite`, the full critic-optimizer loop runs **inside** each Agent's prompt — `lib.critic.critic_optimizer_loop` is bypassed in interactive mode. `/newsagent:run` SKILL.md is rewritten to drive this flow from a parent Claude Code session.

**Tech Stack:** Python 3.11+, Click, Pydantic v2, pytest, OpenAI/OpenRouter/Google engines (already present), Claude Code Agent tool (parent-session dispatch).

**Reference spec:** [docs/superpowers/specs/2026-05-21-agent-dispatch-cluster-select-draft-rewrite-design.md](../specs/2026-05-21-agent-dispatch-cluster-select-draft-rewrite-design.md)

**Reference implementations to mirror:** `lib/steps/filter.py` (prepare/apply pattern), `lib/steps/summarize.py` (same pattern with Sonnet model + items), `skills/filter/SKILL.md` and `skills/summarize/SKILL.md` (SKILL contract shape).

---

## File Structure

**Create:**
- `lib/prompts/name_topic_batch.py` — batched variant of `NAME_TOPIC` (one Agent names all clusters in one call).
- `lib/prompts/assign_noise_batch.py` — batched variant of `ASSIGN_NOISE` (one Agent assigns up to 25 noise headlines in one call).
- `lib/prompts/merge_clusters_batch.py` — batched variant of `MERGE_CLUSTERS` (one Agent decides all candidate pairs in one call).
- `lib/prompts/_dispatch_schemas.py` — shared Pydantic result schemas for draft/rewrite Agent outputs (`DraftSectionResult`, `RewriteResult`) — the full transcript shape an Agent must return after running the critic loop in its own context.
- `tests/test_interactive_pipeline.py` — end-to-end integration test driving `cluster → select → draft → rewrite` in prepare/apply mode with hand-written result JSONs.

**Modify:**
- `lib/prompts/__init__.py` — register the three new `*_batch` prompts.
- `lib/steps/cluster.py` — add `--prepare-batches`, `--apply-results`, `--engine`, `--batch-size` flags; refactor existing logic into prepare/apply functions; keep classic mode.
- `lib/steps/select.py` — add `--prepare-batches`, `--apply-results` flags (two result subdirs); keep classic mode.
- `lib/steps/draft.py` — add `--prepare-batches`, `--apply-results` flags; serialize all three prompts (write/critique/improve) into batch files; keep classic mode (the in-process critic loop).
- `lib/steps/rewrite.py` — add `--prepare-batches`, `--apply-results` flags; serialize critique/improve/title prompts into a single batch file; keep classic mode.
- `tests/test_step_cluster.py` — add prepare/apply tests.
- `tests/test_step_select.py` — add prepare/apply tests for both subdirs.
- `tests/test_step_draft.py` — add prepare/apply tests.
- `tests/test_step_rewrite.py` — add prepare/apply tests.
- `skills/cluster/SKILL.md` — document the interactive Haiku path alongside classic mode.
- `skills/select/SKILL.md` — document the two-subdir interactive Haiku path.
- `skills/draft/SKILL.md` — document the per-section Sonnet path with the full critic loop inside the Agent prompt.
- `skills/rewrite/SKILL.md` — document the single-Sonnet-Agent interactive path.
- `skills/run/SKILL.md` — rewrite to drive the parent-Claude end-to-end interactive flow.

**Do not modify:** `lib/critic.py` (still used by classic mode), `lib.steps.run` Python orchestrator (stays as cron/CI fallback; only its help text gets a one-line update).

---

## Conventions for every task

- Run tests with `.venv/bin/pytest` (NOT system pytest).
- Use `from __future__ import annotations` in new modules to match codebase style.
- New batch JSON files use `batch-NNN.json` (3-digit zero-padded), mirroring filter/summarize.
- Stringify ids consistently with `str(int)`.
- Each batch JSON embeds the rendered `system_prompt`, `user_prompt`, and `output_schema = <Model>.model_json_schema()` so Agents are fully self-contained.
- Commit after each task. Commit messages follow `feat(steps):`, `feat(prompts):`, `test(...)`, `docs(...)` style.

---

## Phase 1 — Batch prompt variants

### Task 1: Add `NAME_TOPIC_BATCH` prompt

**Files:**
- Create: `lib/prompts/name_topic_batch.py`
- Modify: `lib/prompts/__init__.py:2-37`
- Test: `tests/test_prompts_name_topic_batch.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_prompts_name_topic_batch.py
import lib.prompts  # register all
from lib.llm import get_prompt
from lib.prompts.name_topic_batch import (
    NameTopicBatchInput,
    NameTopicBatchOutput,
    ClusterToName,
    ClusterName,
)


def test_name_topic_batch_registered():
    cfg = get_prompt("name_topic_batch")
    assert cfg.name == "name_topic_batch"
    assert cfg.input_schema is NameTopicBatchInput
    assert cfg.output_schema is NameTopicBatchOutput


def test_name_topic_batch_input_renders():
    inp = NameTopicBatchInput(clusters=[
        ClusterToName(cluster_id="0", entities="OpenAI, GPT-6", headlines="OpenAI ships GPT-6\nGPT-6 benchmarks"),
        ClusterToName(cluster_id="1", entities="EU, AI Act", headlines="EU passes AI Act"),
    ])
    cfg = get_prompt("name_topic_batch")
    rendered = cfg.user_prompt.format(**inp.model_dump())
    assert "OpenAI, GPT-6" in rendered
    assert "EU passes AI Act" in rendered


def test_name_topic_batch_output_shape():
    out = NameTopicBatchOutput(names=[
        ClusterName(cluster_id="0", name="OpenAI ships GPT-6"),
        ClusterName(cluster_id="1", name="EU passes the AI Act"),
    ])
    assert out.names[0].cluster_id == "0"
    assert out.names[1].name == "EU passes the AI Act"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_prompts_name_topic_batch.py -v`
Expected: FAIL with `ModuleNotFoundError` for `lib.prompts.name_topic_batch`.

- [ ] **Step 3: Write minimal implementation**

```python
# lib/prompts/name_topic_batch.py
"""name_topic_batch — name multiple article clusters in a single Agent call.

Batched variant of NAME_TOPIC. Used by newsagent:cluster in --prepare-batches
mode so one Haiku Agent can name all of a session's non-noise clusters in one
round-trip instead of N separate calls.
"""
from __future__ import annotations

import json
from typing import List

from pydantic import BaseModel, computed_field

from lib.llm import PromptConfig, register_prompt


class ClusterToName(BaseModel):
    cluster_id: str
    entities: str
    headlines: str


class NameTopicBatchInput(BaseModel):
    clusters: List[ClusterToName]

    @computed_field
    @property
    def clusters_json(self) -> str:
        return json.dumps([c.model_dump() for c in self.clusters], indent=2)


class ClusterName(BaseModel):
    cluster_id: str
    name: str


class NameTopicBatchOutput(BaseModel):
    names: List[ClusterName]


_SYSTEM = """\
You are a newsletter editor naming several topic sections at once.

You will receive a JSON list of clusters, each with:
- cluster_id (string)
- entities (comma-separated central entities)
- headlines (newline-separated sample headlines, highest-rated first)

For EACH cluster, write a concise descriptive section title (4-8 words) that
captures the main story or theme. Do not use generic phrases like "AI News"
or "Tech Update". Each title must be unique.

Return ONLY a JSON object matching the provided schema, with exactly one entry
per input cluster_id (no duplicates, no extras)."""

_USER = """\
Clusters to name (JSON):
{clusters_json}

Return JSON matching the schema."""


NAME_TOPIC_BATCH = PromptConfig(
    name="name_topic_batch",
    system_prompt=_SYSTEM,
    user_prompt=_USER,
    input_schema=NameTopicBatchInput,
    output_schema=NameTopicBatchOutput,
    default_engine="subagent",
    reasoning_effort=3,
)

register_prompt(NAME_TOPIC_BATCH)
```

Then add to `lib/prompts/__init__.py`:

```python
# Add after the existing `from lib.prompts import name_topic  # noqa: F401`
from lib.prompts import name_topic_batch  # noqa: F401
```

And add `"name_topic_batch"` to the `__all__` list (alphabetical with siblings, or after `"name_topic"`).

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_prompts_name_topic_batch.py -v`
Expected: 3 PASS.

- [ ] **Step 5: Commit**

```bash
git add lib/prompts/name_topic_batch.py lib/prompts/__init__.py tests/test_prompts_name_topic_batch.py
git commit -m "feat(prompts): add NAME_TOPIC_BATCH for Agent-dispatch cluster naming"
```

---

### Task 2: Add `ASSIGN_NOISE_BATCH` prompt

**Files:**
- Create: `lib/prompts/assign_noise_batch.py`
- Modify: `lib/prompts/__init__.py`
- Test: `tests/test_prompts_assign_noise_batch.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_prompts_assign_noise_batch.py
import lib.prompts  # register
from lib.llm import get_prompt
from lib.prompts.assign_noise_batch import (
    AssignNoiseBatchInput,
    AssignNoiseBatchOutput,
    NoiseHeadline,
    NoiseAssignment,
)
from lib.prompts.assign_noise import ClusterDescriptor


def test_assign_noise_batch_registered():
    cfg = get_prompt("assign_noise_batch")
    assert cfg.input_schema is AssignNoiseBatchInput
    assert cfg.output_schema is AssignNoiseBatchOutput


def test_assign_noise_batch_input_renders():
    inp = AssignNoiseBatchInput(
        headlines=[
            NoiseHeadline(id="0", title="GPT-6 launches", summary="OpenAI ships GPT-6"),
            NoiseHeadline(id="1", title="Stock roundup", summary="Markets mixed"),
        ],
        clusters=[ClusterDescriptor(id="3", name="OpenAI", sample_headlines=["GPT-6"])],
        allow_new=True,
    )
    cfg = get_prompt("assign_noise_batch")
    rendered = cfg.user_prompt.format(**inp.model_dump())
    assert "GPT-6 launches" in rendered
    assert "Stock roundup" in rendered
    assert "OpenAI" in rendered


def test_assign_noise_batch_output_shape():
    out = AssignNoiseBatchOutput(assignments=[
        NoiseAssignment(id="0", assignment="3"),
        NoiseAssignment(id="1", assignment="none"),
    ])
    assert out.assignments[0].assignment == "3"
    assert out.assignments[1].assignment == "none"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_prompts_assign_noise_batch.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Write minimal implementation**

```python
# lib/prompts/assign_noise_batch.py
"""assign_noise_batch — assign multiple noise headlines to clusters in one call.

Batched variant of ASSIGN_NOISE. Used by newsagent:select in --prepare-batches
mode so one Haiku Agent handles up to N (default 25) noise-point assignments
in one round-trip.
"""
from __future__ import annotations

import json
from typing import List

from pydantic import BaseModel, computed_field

from lib.llm import PromptConfig, register_prompt
from lib.prompts.assign_noise import ClusterDescriptor


class NoiseHeadline(BaseModel):
    id: str
    title: str
    summary: str


class AssignNoiseBatchInput(BaseModel):
    headlines: List[NoiseHeadline]
    clusters: List[ClusterDescriptor]
    allow_new: bool = True

    @computed_field
    @property
    def headlines_json(self) -> str:
        return json.dumps([h.model_dump() for h in self.headlines], indent=2)

    @computed_field
    @property
    def clusters_json(self) -> str:
        return json.dumps([c.model_dump() for c in self.clusters], indent=2)


class NoiseAssignment(BaseModel):
    id: str
    assignment: str  # cluster id, "new", or "none"


class AssignNoiseBatchOutput(BaseModel):
    assignments: List[NoiseAssignment]


_SYSTEM = """\
You are an editor assigning unclustered news headlines to one of today's news clusters.

You will receive:
- A JSON list of headlines (each with id, title, summary)
- A JSON list of existing clusters (each with id, name, sample_headlines)

For EACH headline, decide:
- The id of the best-matching existing cluster, OR
- "new" if the headline is significantly different from all existing clusters
  but plausibly worth its own section (only if allow_new is true), OR
- "none" if the headline is unrelated to the newsletter's topic

Return ONLY a JSON object matching the provided schema, with exactly one
entry per input id (no duplicates, no extras)."""

_USER = """\
Headlines to assign (JSON):
{headlines_json}

Existing clusters (JSON):
{clusters_json}

Allow new cluster: {allow_new}

Return JSON matching the schema."""


ASSIGN_NOISE_BATCH = PromptConfig(
    name="assign_noise_batch",
    system_prompt=_SYSTEM,
    user_prompt=_USER,
    input_schema=AssignNoiseBatchInput,
    output_schema=AssignNoiseBatchOutput,
    default_engine="subagent",
    reasoning_effort=3,
)

register_prompt(ASSIGN_NOISE_BATCH)
```

Then register in `lib/prompts/__init__.py`:

```python
from lib.prompts import assign_noise_batch  # noqa: F401
```

Add `"assign_noise_batch"` to `__all__`.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_prompts_assign_noise_batch.py -v`
Expected: 3 PASS.

- [ ] **Step 5: Commit**

```bash
git add lib/prompts/assign_noise_batch.py lib/prompts/__init__.py tests/test_prompts_assign_noise_batch.py
git commit -m "feat(prompts): add ASSIGN_NOISE_BATCH for Agent-dispatch noise assignment"
```

---

### Task 3: Add `MERGE_CLUSTERS_BATCH` prompt

**Files:**
- Create: `lib/prompts/merge_clusters_batch.py`
- Modify: `lib/prompts/__init__.py`
- Test: `tests/test_prompts_merge_clusters_batch.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_prompts_merge_clusters_batch.py
import lib.prompts
from lib.llm import get_prompt
from lib.prompts.merge_clusters_batch import (
    MergeClustersBatchInput,
    MergeClustersBatchOutput,
    PairToDecide,
    MergeDecision,
)
from lib.prompts.merge_clusters import ClusterPair


def test_merge_clusters_batch_registered():
    cfg = get_prompt("merge_clusters_batch")
    assert cfg.input_schema is MergeClustersBatchInput
    assert cfg.output_schema is MergeClustersBatchOutput


def test_merge_clusters_batch_input_renders():
    inp = MergeClustersBatchInput(pairs=[
        PairToDecide(
            pair_id="0",
            a=ClusterPair(id="1", name="GPT-6 launch", top_headlines=["GPT-6 ships"]),
            b=ClusterPair(id="2", name="OpenAI ships GPT-6", top_headlines=["GPT-6 benchmarks"]),
        ),
    ])
    cfg = get_prompt("merge_clusters_batch")
    rendered = cfg.user_prompt.format(**inp.model_dump())
    assert "GPT-6 launch" in rendered
    assert "OpenAI ships GPT-6" in rendered


def test_merge_clusters_batch_output_shape():
    out = MergeClustersBatchOutput(decisions=[
        MergeDecision(pair_id="0", merge=True, merged_name="OpenAI ships GPT-6"),
        MergeDecision(pair_id="1", merge=False, merged_name=None),
    ])
    assert out.decisions[0].merge is True
    assert out.decisions[1].merged_name is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_prompts_merge_clusters_batch.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Write minimal implementation**

```python
# lib/prompts/merge_clusters_batch.py
"""merge_clusters_batch — decide multiple merge candidates in a single call.

Batched variant of MERGE_CLUSTERS. Used by newsagent:select in --prepare-batches
mode so one Haiku Agent decides all candidate near-duplicate pairs at once.
"""
from __future__ import annotations

import json
from typing import List, Optional

from pydantic import BaseModel, computed_field

from lib.llm import PromptConfig, register_prompt
from lib.prompts.merge_clusters import ClusterPair


class PairToDecide(BaseModel):
    pair_id: str
    a: ClusterPair
    b: ClusterPair


class MergeClustersBatchInput(BaseModel):
    pairs: List[PairToDecide]

    @computed_field
    @property
    def pairs_json(self) -> str:
        return json.dumps(
            [{"pair_id": p.pair_id, "a": p.a.model_dump(), "b": p.b.model_dump()}
             for p in self.pairs],
            indent=2,
        )


class MergeDecision(BaseModel):
    pair_id: str
    merge: bool
    merged_name: Optional[str] = None


class MergeClustersBatchOutput(BaseModel):
    decisions: List[MergeDecision]


_SYSTEM = """\
You are an editor deciding whether candidate cluster pairs cover the same story.

You will receive a JSON list of candidate pairs. Each pair has a pair_id and
two clusters (a, b), each with id, name, and top_headlines.

For EACH pair, decide:
- merge=true if both clusters cover substantively the same story or theme.
  Provide merged_name (the better of the two names, or a new title that
  better captures both).
- merge=false if they cover distinct stories that deserve separate sections.
  merged_name may be null.

Return ONLY a JSON object matching the provided schema, with exactly one
decision per input pair_id."""

_USER = """\
Pairs to decide (JSON):
{pairs_json}

Return JSON matching the schema."""


MERGE_CLUSTERS_BATCH = PromptConfig(
    name="merge_clusters_batch",
    system_prompt=_SYSTEM,
    user_prompt=_USER,
    input_schema=MergeClustersBatchInput,
    output_schema=MergeClustersBatchOutput,
    default_engine="subagent",
    reasoning_effort=4,
)

register_prompt(MERGE_CLUSTERS_BATCH)
```

Register in `lib/prompts/__init__.py`:

```python
from lib.prompts import merge_clusters_batch  # noqa: F401
```

Add `"merge_clusters_batch"` to `__all__`.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_prompts_merge_clusters_batch.py -v`
Expected: 3 PASS.

- [ ] **Step 5: Commit**

```bash
git add lib/prompts/merge_clusters_batch.py lib/prompts/__init__.py tests/test_prompts_merge_clusters_batch.py
git commit -m "feat(prompts): add MERGE_CLUSTERS_BATCH for Agent-dispatch merge decisions"
```

---

### Task 4: Add shared draft/rewrite Agent result schemas

**Files:**
- Create: `lib/prompts/_dispatch_schemas.py`
- Test: `tests/test_dispatch_schemas.py`

The draft and rewrite Agents must return a fixed transcript shape (the loop runs inside the Agent). Define this shape once so both step CLIs and tests reference the same Pydantic model.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_dispatch_schemas.py
from lib.prompts._dispatch_schemas import DraftSectionResult, RewriteResult


def test_draft_section_result_minimal():
    r = DraftSectionResult(
        cat="OpenAI",
        final_section_markdown="## OpenAI\n- ships GPT-6",
        iterations=1,
        scores=[7.5],
        feedbacks=["needs more detail"],
        accepted=False,
    )
    assert r.cat == "OpenAI"
    assert r.iterations == 1
    assert r.accepted is False


def test_rewrite_result_minimal():
    r = RewriteResult(
        final_newsletter_markdown="## OpenAI\n- ships GPT-6\n\n## EU\n- AI Act",
        title="AI Roundup",
        iterations=2,
        scores=[6.0, 8.2],
        feedbacks=["too short", "good"],
        accepted=True,
    )
    assert r.title == "AI Roundup"
    assert r.accepted is True
    assert r.scores == [6.0, 8.2]


def test_dispatch_schemas_serializable():
    r = DraftSectionResult(
        cat="X", final_section_markdown="## X", iterations=0,
        scores=[], feedbacks=[], accepted=False,
    )
    js = r.model_json_schema()
    assert "cat" in js["properties"]
    assert "final_section_markdown" in js["properties"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_dispatch_schemas.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Write minimal implementation**

```python
# lib/prompts/_dispatch_schemas.py
"""Result schemas for Agent-dispatch draft/rewrite outputs.

The Agent runs the full critic-optimizer loop inside its own context, so its
single result file must capture the entire transcript (final draft + per-
iteration scores and feedbacks).
"""
from __future__ import annotations

from typing import List

from pydantic import BaseModel


class DraftSectionResult(BaseModel):
    cat: str
    final_section_markdown: str
    iterations: int
    scores: List[float]
    feedbacks: List[str]
    accepted: bool


class RewriteResult(BaseModel):
    final_newsletter_markdown: str
    title: str
    iterations: int
    scores: List[float]
    feedbacks: List[str]
    accepted: bool
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_dispatch_schemas.py -v`
Expected: 3 PASS.

- [ ] **Step 5: Commit**

```bash
git add lib/prompts/_dispatch_schemas.py tests/test_dispatch_schemas.py
git commit -m "feat(prompts): shared DraftSectionResult / RewriteResult schemas"
```

---

## Phase 2 — `cluster` prepare/apply

### Task 5: `cluster --prepare-batches` writes one batch JSON with all clusters

**Files:**
- Modify: `lib/steps/cluster.py`
- Test: `tests/test_step_cluster.py`

The prepare path still runs UMAP + HDBSCAN + embeddings (these produce `cluster_id`s that become the Agent's inputs). After clustering, instead of calling `name_topic` N times in process, write one batch JSON with all clusters and stop. Apply will write the names back.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_step_cluster.py`:

```python
# At top of file, add to existing imports:
from pathlib import Path
from lib.prompts.name_topic_batch import NameTopicBatchOutput


def test_cluster_prepare_writes_single_batch(tmp_db, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    _seed(tmp_db, session_id="c2", n=6)

    # Stub UMAP reducer + HDBSCAN to force 2 clusters
    monkeypatch.setattr("lib.steps.cluster.load_umap_reducer", lambda _: _fake_reducer())
    monkeypatch.setattr(
        "lib.steps.cluster.apply_umap",
        lambda embeddings, reducer: [[float(i % 2), 0.0] for i in range(len(embeddings))],
    )
    monkeypatch.setattr(
        "lib.steps.cluster.optimize_hdbscan",
        lambda reduced, n_trials: ([0, 1, 0, 1, 0, 1], {"noise_ratio": 0.0, "best_params": {}}),
    )

    runner = CliRunner()
    result = runner.invoke(cluster_cli, [
        "--db", tmp_db, "--session", "c2", "--prepare-batches",
    ])
    assert result.exit_code == 0, result.output

    batches_dir = Path("runs/c2/cluster-batches")
    files = sorted(batches_dir.glob("batch-*.json"))
    assert len(files) == 1  # single batch for all clusters

    payload = json.loads(files[0].read_text())
    assert payload["batch_id"] == 0
    cluster_ids = sorted(c["cluster_id"] for c in payload["clusters"])
    assert cluster_ids == ["0", "1"]
    assert "system_prompt" in payload and payload["system_prompt"]
    assert "user_prompt" in payload and payload["user_prompt"]
    # Schema embedded
    assert payload["output_schema"]["properties"].get("names") is not None
```

You will also need to import `cluster_cli` and `json` if not already imported in the test module. Check the existing top of `tests/test_step_cluster.py` and add as needed:

```python
import json
from click.testing import CliRunner
from lib.steps.cluster import cli as cluster_cli
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_step_cluster.py::test_cluster_prepare_writes_single_batch -v`
Expected: FAIL — CLI has no `--prepare-batches` flag (Click rejects the option).

- [ ] **Step 3: Write minimal implementation**

Refactor `lib/steps/cluster.py` to add prepare/apply modes. Keep the existing classic mode. Replace the entire file with:

```python
"""newsagent:cluster — UMAP+HDBSCAN topic clustering with LLM-based cluster naming.

Three modes (mirror of lib/steps/filter.py):
  1. classic (default): single in-process NAME_TOPIC call per cluster.
     Use --engine to override.
  2. --prepare-batches: run UMAP+HDBSCAN, then write one
     runs/<SID>/cluster-batches/batch-000.json containing all clusters for
     a single Haiku Agent to name in one call.
  3. --apply-results DIR: read result JSON from DIR, write cluster_name back
     to state, complete the step.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Optional

import click
from pydantic import ValidationError

import lib.prompts  # noqa: F401 — register all prompts
from lib.clustering import apply_umap, load_umap_reducer, optimize_hdbscan
from lib.embeddings import embed_texts
from lib.llm import call_prompt, get_prompt
from lib.prompts.name_topic_batch import NameTopicBatchInput, NameTopicBatchOutput
from lib.state import NewsletterAgentState


_BATCHES_SUBDIR = "cluster-batches"
_RESULTS_SUBDIR = "cluster-results"


def _do_clustering(
    state: NewsletterAgentState,
    umap_path: str,
    n_trials: int,
):
    """Run UMAP+HDBSCAN and assign cluster_id to each candidate headline.

    Returns (cluster_to_headlines, metrics, candidates).
    cluster_to_headlines maps int cluster_id -> list of headline dicts (non-noise only).
    """
    candidates = [h for h in state.headline_data if h.get("summary")]
    if not candidates:
        return {}, {}, []

    missing = [h for h in candidates if not h.get("embedding")]
    if missing:
        texts = [(h.get("title", "") + " " + h.get("summary", "")).strip() for h in missing]
        vectors = embed_texts(texts)
        for h, v in zip(missing, vectors):
            h["embedding"] = v

    embeddings: List[List[float]] = [h["embedding"] for h in candidates]
    reducer = load_umap_reducer(umap_path)
    reduced = apply_umap(embeddings, reducer)
    labels, metrics = optimize_hdbscan(reduced, n_trials=n_trials)

    for h, label in zip(candidates, labels):
        h["cluster_id"] = int(label)

    cluster_to_headlines: dict[int, list[dict]] = {}
    for h in candidates:
        cid = h["cluster_id"]
        if cid >= 0:
            cluster_to_headlines.setdefault(cid, []).append(h)
    return cluster_to_headlines, metrics, candidates


def _build_batch_clusters(cluster_to_headlines: dict[int, list[dict]]) -> list[dict]:
    """Build the per-cluster input items for the batch JSON."""
    out: list[dict] = []
    for cid in sorted(cluster_to_headlines.keys()):
        hl = cluster_to_headlines[cid]
        sorted_hl = sorted(hl, key=lambda h: h.get("rating", 0.0), reverse=True)
        top5 = sorted_hl[:5]
        out.append({
            "cluster_id": str(cid),
            "entities": ", ".join(h.get("title", "")[:30] for h in top5),
            "headlines": "\n".join(h.get("title", "") for h in top5),
        })
    return out


def _render_prompts(clusters: list[dict]) -> tuple[str, str]:
    cfg = get_prompt("name_topic_batch")
    validated = NameTopicBatchInput.model_validate({"clusters": clusters})
    user = cfg.user_prompt.format(**validated.model_dump())
    return cfg.system_prompt, user


def _write_batch(session_id: str, clusters: list[dict]) -> Path:
    runs_dir = Path("runs") / session_id
    batches_dir = runs_dir / _BATCHES_SUBDIR
    if batches_dir.exists():
        for p in batches_dir.glob("batch-*.json"):
            p.unlink()
    batches_dir.mkdir(parents=True, exist_ok=True)
    system, user = _render_prompts(clusters)
    payload = {
        "batch_id": 0,
        "session_id": session_id,
        "ids": [c["cluster_id"] for c in clusters],
        "clusters": clusters,
        "system_prompt": system,
        "user_prompt": user,
        "output_schema": NameTopicBatchOutput.model_json_schema(),
    }
    path = batches_dir / "batch-000.json"
    path.write_text(json.dumps(payload, indent=2))
    return path


def _load_results(results_dir: Path, expected_ids: set[str]) -> tuple[dict[str, str], list[str]]:
    """Returns (names: cluster_id -> name, problems)."""
    names: dict[str, str] = {}
    problems: list[str] = []
    if not results_dir.exists():
        problems.append(f"results dir not found: {results_dir}")
        return {}, problems
    files = sorted(results_dir.glob("batch-*.json"))
    if not files:
        problems.append(f"no batch-*.json files in {results_dir}")
        return {}, problems
    seen: set[str] = set()
    for f in files:
        try:
            raw = json.loads(f.read_text())
        except json.JSONDecodeError as exc:
            problems.append(f"{f.name}: invalid JSON ({exc})")
            continue
        try:
            parsed = NameTopicBatchOutput.model_validate(raw)
        except ValidationError as exc:
            problems.append(f"{f.name}: schema mismatch ({exc.error_count()} errors)")
            continue
        for n in parsed.names:
            if n.cluster_id in seen:
                problems.append(f"{f.name}: duplicate cluster_id {n.cluster_id!r}")
            seen.add(n.cluster_id)
            names[n.cluster_id] = n.name
    missing = expected_ids - seen
    extra = seen - expected_ids
    if missing:
        problems.append(f"missing names for cluster_ids: {sorted(missing)[:10]}...")
    if extra:
        problems.append(f"unexpected cluster_ids in results: {sorted(extra)[:10]}...")
    return names, problems


def _apply_names(
    state: NewsletterAgentState,
    names: dict[str, str],
) -> tuple[int, int]:
    """Apply names to headlines + state.clusters. Returns (n_clusters, noise_count)."""
    state.clusters = {}
    cluster_to_headlines: dict[int, list[dict]] = {}
    for h in state.headline_data:
        cid = h.get("cluster_id")
        if cid is None:
            continue
        if cid >= 0:
            cluster_to_headlines.setdefault(cid, []).append(h)

    for cid_int, hl in cluster_to_headlines.items():
        name = names.get(str(cid_int))
        if name is None:
            continue
        for h in hl:
            h["cluster_name"] = name
        state.clusters[name] = [h["url"] for h in hl]

    n_clusters = len(state.clusters)
    noise_count = sum(1 for h in state.headline_data if h.get("cluster_id") == -1)
    return n_clusters, noise_count


def _write_report(
    session_id: str, n_candidates: int, n_clusters: int,
    noise_count: int, metrics: dict, cluster_summary: dict,
) -> None:
    runs_dir = Path("runs") / session_id
    runs_dir.mkdir(parents=True, exist_ok=True)
    (runs_dir / "cluster.json").write_text(json.dumps({
        "session_id": session_id,
        "completed_at": datetime.now().isoformat(),
        "n_candidates": n_candidates,
        "n_clusters": n_clusters,
        "noise_count": noise_count,
        "noise_ratio": metrics.get("noise_ratio", 0.0),
        "silhouette": metrics.get("silhouette"),
        "best_params": metrics.get("best_params", {}),
        "clusters": cluster_summary,
    }, indent=2))


@click.command()
@click.option("--db", "db_path", default="newsletter_agent.db")
@click.option("--session", "session_id", required=True)
@click.option("--n-trials", default=30, type=int, help="Optuna trials for HDBSCAN tuning")
@click.option("--umap-path", default="umap_reducer.pkl", help="Path to pretrained UMAP reducer")
@click.option("--engine", default=None,
              help="Override engine for classic mode")
@click.option("--prepare-batches", is_flag=True,
              help="Write batches to runs/<SID>/cluster-batches/ for subagent dispatch")
@click.option("--apply-results", "apply_results", default=None,
              help="Read result JSON from this dir and apply to state")
def cli(
    db_path: str,
    session_id: str,
    n_trials: int,
    umap_path: str,
    engine: Optional[str],
    prepare_batches: bool,
    apply_results: Optional[str],
) -> None:
    if prepare_batches and apply_results:
        raise click.UsageError("--prepare-batches and --apply-results are mutually exclusive")

    state = NewsletterAgentState(session_id=session_id, db_path=db_path).load_latest_from_db()
    if state is None:
        raise click.ClickException(f"No state found for session {session_id}")

    # ── prepare mode ───────────────────────────────────────────────
    if prepare_batches:
        state.start_step("cluster")
        state.save_checkpoint("cluster")
        cluster_to_headlines, metrics, candidates = _do_clustering(state, umap_path, n_trials)
        if not cluster_to_headlines:
            click.echo("Nothing to cluster (no non-noise clusters).")
            state.complete_step("cluster", message="no non-noise clusters")
            state.save_checkpoint("cluster")
            return
        # Persist cluster_id assignments now so apply can read them.
        state.save_checkpoint("cluster")
        clusters_for_batch = _build_batch_clusters(cluster_to_headlines)
        path = _write_batch(session_id, clusters_for_batch)
        click.echo(f"Prepared 1 batch ({len(clusters_for_batch)} clusters): {path}")
        click.echo(f"\nDispatch one Haiku subagent. Write result to:")
        click.echo(f"  runs/{session_id}/{_RESULTS_SUBDIR}/batch-000.json")
        click.echo(f"Then run: python -m lib.steps.cluster --session {session_id} "
                   f"--apply-results runs/{session_id}/{_RESULTS_SUBDIR}")
        return

    # ── apply mode ─────────────────────────────────────────────────
    if apply_results:
        # cluster_ids were already assigned during prepare; we just need to
        # know which ones are non-noise.
        expected_ids = {
            str(h["cluster_id"])
            for h in state.headline_data
            if h.get("cluster_id") is not None and h["cluster_id"] >= 0
        }
        names, problems = _load_results(Path(apply_results), expected_ids)
        if problems:
            click.echo("Apply found problems:", err=True)
            for p in problems:
                click.echo(f"  - {p}", err=True)
            if not names:
                raise click.ClickException("no usable names; redispatch failed batches")

        n_clusters, noise_count = _apply_names(state, names)
        state.complete_step("cluster", message=f"{n_clusters} clusters, {noise_count} noise points")
        state.save_checkpoint("cluster")

        cluster_summary = {
            name: {
                "name": name,
                "count": len(urls),
                "sample_titles": [],
            }
            for name, urls in state.clusters.items()
        }
        _write_report(session_id, len(state.headline_data), n_clusters, noise_count, {}, cluster_summary)
        click.echo(f"Cluster: {n_clusters} clusters, {noise_count} noise points.")
        return

    # ── classic mode ───────────────────────────────────────────────
    state.start_step("cluster")
    state.save_checkpoint("cluster")
    cluster_to_headlines, metrics, candidates = _do_clustering(state, umap_path, n_trials)
    if not cluster_to_headlines:
        click.echo("Nothing to cluster.")
        state.complete_step("cluster", message="no non-noise clusters")
        state.save_checkpoint("cluster")
        return

    state.clusters = {}
    cluster_summary: dict[str, dict] = {}
    for cid in sorted(cluster_to_headlines.keys()):
        hl = cluster_to_headlines[cid]
        sorted_hl = sorted(hl, key=lambda h: h.get("rating", 0.0), reverse=True)
        top5 = sorted_hl[:5]
        entities = ", ".join(h.get("title", "")[:30] for h in top5)
        headlines_str = "\n".join(h.get("title", "") for h in top5)
        result = call_prompt("name_topic", {"entities": entities, "headlines": headlines_str}, engine=engine)
        cluster_name = result.title
        for h in hl:
            h["cluster_name"] = cluster_name
        state.clusters[cluster_name] = [h["url"] for h in hl]
        cluster_summary[str(cid)] = {
            "name": cluster_name,
            "count": len(hl),
            "sample_titles": [h.get("title", "") for h in top5[:3]],
        }

    n_clusters = len(cluster_to_headlines)
    noise_count = sum(1 for h in candidates if h.get("cluster_id") == -1)
    state.complete_step("cluster", message=f"{n_clusters} clusters, {noise_count} noise points")
    state.save_checkpoint("cluster")
    _write_report(session_id, len(candidates), n_clusters, noise_count, metrics, cluster_summary)
    click.echo(
        f"Cluster: {n_clusters} clusters, {noise_count} noise points ({len(candidates)} candidates)."
    )


if __name__ == "__main__":  # pragma: no cover
    sys.exit(cli())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_step_cluster.py::test_cluster_prepare_writes_single_batch -v`
Expected: PASS.

Also run the existing cluster tests to make sure classic mode still works:

Run: `.venv/bin/pytest tests/test_step_cluster.py -v`
Expected: all PASS (4 existing + new prepare test).

- [ ] **Step 5: Commit**

```bash
git add lib/steps/cluster.py tests/test_step_cluster.py
git commit -m "feat(steps): cluster --prepare-batches writes single Haiku Agent batch"
```

---

### Task 6: `cluster --apply-results` writes names back to state

**Files:**
- Test: `tests/test_step_cluster.py`

The implementation already exists from Task 5 (the refactor included apply). This task adds the test that exercises apply on a session whose prepare has already run.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_step_cluster.py`:

```python
def test_cluster_apply_reads_results_and_names_clusters(tmp_db, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    _seed(tmp_db, session_id="c3", n=4)

    # Prepare path needs the UMAP+HDBSCAN stubs first
    monkeypatch.setattr("lib.steps.cluster.load_umap_reducer", lambda _: _fake_reducer())
    monkeypatch.setattr(
        "lib.steps.cluster.apply_umap",
        lambda embeddings, reducer: [[float(i % 2), 0.0] for i in range(len(embeddings))],
    )
    monkeypatch.setattr(
        "lib.steps.cluster.optimize_hdbscan",
        lambda reduced, n_trials: ([0, 1, 0, 1], {"noise_ratio": 0.0, "best_params": {}}),
    )

    runner = CliRunner()
    prep = runner.invoke(cluster_cli, [
        "--db", tmp_db, "--session", "c3", "--prepare-batches",
    ])
    assert prep.exit_code == 0, prep.output

    # Write fake Agent result
    results_dir = Path("runs/c3/cluster-results")
    results_dir.mkdir(parents=True)
    (results_dir / "batch-000.json").write_text(json.dumps({
        "names": [
            {"cluster_id": "0", "name": "OpenAI ships GPT-6"},
            {"cluster_id": "1", "name": "EU passes the AI Act"},
        ]
    }))

    apply_res = runner.invoke(cluster_cli, [
        "--db", tmp_db, "--session", "c3",
        "--apply-results", str(results_dir),
    ])
    assert apply_res.exit_code == 0, apply_res.output

    state = NewsletterAgentState(session_id="c3", db_path=tmp_db).load_latest_from_db()
    assert state is not None
    assert "OpenAI ships GPT-6" in state.clusters
    assert "EU passes the AI Act" in state.clusters

    # Each non-noise headline got a cluster_name
    named = [h for h in state.headline_data if h.get("cluster_name")]
    assert len(named) == 4


def test_cluster_apply_reports_missing_names(tmp_db, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    _seed(tmp_db, session_id="c4", n=4)

    monkeypatch.setattr("lib.steps.cluster.load_umap_reducer", lambda _: _fake_reducer())
    monkeypatch.setattr(
        "lib.steps.cluster.apply_umap",
        lambda embeddings, reducer: [[float(i % 2), 0.0] for i in range(len(embeddings))],
    )
    monkeypatch.setattr(
        "lib.steps.cluster.optimize_hdbscan",
        lambda reduced, n_trials: ([0, 1, 0, 1], {"noise_ratio": 0.0, "best_params": {}}),
    )

    runner = CliRunner()
    runner.invoke(cluster_cli, ["--db", tmp_db, "--session", "c4", "--prepare-batches"])

    results_dir = Path("runs/c4/cluster-results")
    results_dir.mkdir(parents=True)
    (results_dir / "batch-000.json").write_text(json.dumps({
        "names": [{"cluster_id": "0", "name": "Only cluster 0"}]
    }))

    result = runner.invoke(cluster_cli, [
        "--db", tmp_db, "--session", "c4",
        "--apply-results", str(results_dir),
    ])
    # Partial apply still succeeds
    assert result.exit_code == 0, result.output
    combined = result.output + (result.stderr if result.stderr_bytes else "")
    assert "missing names" in combined
```

- [ ] **Step 2: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_step_cluster.py::test_cluster_apply_reads_results_and_names_clusters tests/test_step_cluster.py::test_cluster_apply_reports_missing_names -v`
Expected: 2 PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/test_step_cluster.py
git commit -m "test(cluster): apply-results writes names and reports gaps"
```

---

### Task 7: Update `skills/cluster/SKILL.md` to document interactive flow

**Files:**
- Modify: `skills/cluster/SKILL.md`

Mirror `skills/filter/SKILL.md` and `skills/summarize/SKILL.md`.

- [ ] **Step 1: Replace contents**

Overwrite `skills/cluster/SKILL.md` with:

```markdown
---
name: cluster
description: Group summarized headlines into topical clusters using UMAP dimensionality reduction, Optuna-tuned HDBSCAN, and LLM-based cluster naming. Writes cluster_id and cluster_name onto each headline and populates state.clusters.
---

# newsagent:cluster

Step 8 of /newsagent:run. Two execution paths:

1. **Interactive** — preferred when running through Claude Code: parent Claude
   dispatches a single Haiku subagent that names every cluster in one call.
   Avoids `claude -p` (not covered under the Max plan).
2. **Classic** — for cron / CI: a single LLM engine names clusters one-by-one
   in-process via `call_prompt`. Requires `--engine` set to a non-`subagent`
   engine.

Both paths share `lib/prompts/name_topic_batch.py` (interactive) and
`lib/prompts/name_topic.py` (classic).

## Interactive mode — Haiku subagent dispatch

### Step 1: prepare batches

```bash
python -m lib.steps.cluster --session SID --prepare-batches
```

Runs UMAP + HDBSCAN, assigns `cluster_id` to each non-noise headline, and
writes a single `runs/<SID>/cluster-batches/batch-000.json` containing every
non-noise cluster (id, central entities, sample headlines) plus a pre-rendered
`system_prompt`, `user_prompt`, and `output_schema`.

If clustering produces no non-noise clusters, the step completes immediately
with `no non-noise clusters` — no batch file is written.

### Step 2: dispatch one Haiku subagent

Dispatch a single Agent call with:

- `subagent_type: "general-purpose"`
- `model: "haiku"`
- `description: "Name all clusters for session SID"`
- `prompt:` instructions of the shape:

  ```
  Read runs/<SID>/cluster-batches/batch-000.json. It contains:
    - system_prompt: cluster-naming guidance
    - user_prompt: pre-rendered task with the JSON clusters inline
    - ids: list of cluster_ids you MUST name
    - output_schema: required JSON shape (NameTopicBatchOutput)

  Follow system_prompt + user_prompt. Return ONLY a JSON object matching
  output_schema, with exactly one entry per cluster_id.

  Write that JSON object to:
    runs/<SID>/cluster-results/batch-000.json
  using the Write tool. Then report the path you wrote.
  ```

### Step 3: apply results

```bash
python -m lib.steps.cluster --session SID \
  --apply-results runs/<SID>/cluster-results
```

Validates the result file against `NameTopicBatchOutput`, writes
`cluster_name` to each headline, populates `state.clusters`, marks the step
complete, writes `runs/<SID>/cluster.json`.

### Step 4: retry on failure

If apply reports `missing names for cluster_ids: [...]` or
`schema mismatch`: re-dispatch the Haiku Agent (overwrite the result file),
re-run `--apply-results`. Apply exits non-zero only if zero names were usable.

## Classic mode (non-interactive)

```bash
python -m lib.steps.cluster --session SID --engine openai:gpt-4o-mini
```

Calls `name_topic` once per cluster. Do not use `--engine subagent` here — it
falls back to `claude -p` which costs API tokens outside the Max plan.

## Output contract

- `state.headline_data[i].cluster_id` set for every headline with a summary
  (or `-1` for HDBSCAN noise).
- `state.headline_data[i].cluster_name` set for every non-noise headline.
- `state.clusters` populated as `{name: [url, ...]}`.
- `runs/<SID>/cluster.json` written with metrics + cluster summary.
- `cluster` step marked COMPLETE.
```

- [ ] **Step 2: Commit**

```bash
git add skills/cluster/SKILL.md
git commit -m "docs(skills): cluster SKILL.md documents interactive Haiku path"
```

---

## Phase 3 — `select` prepare/apply

The `select` step has two LLM call-sites: noise assignment (sharded, many headlines) and merge decisions (single batch, few pairs). Prepare writes two subdirs; apply reads two subdirs.

### Task 8: Add `select --prepare-batches` (both subdirs)

**Files:**
- Modify: `lib/steps/select.py`
- Test: `tests/test_step_select.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_step_select.py`:

```python
import json
from pathlib import Path
from lib.steps.select import cli as select_cli  # if not already imported


def test_select_prepare_writes_both_subdirs(tmp_db, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    # Seed with: 2 named clusters + 3 noise headlines
    _seed_state(
        tmp_db,
        session_id="s_prep",
        headlines=[
            {"title": "GPT-6 ships", "summary": "OpenAI", "cluster_id": 0,
             "cluster_name": "OpenAI", "url": "https://x.com/1", "rating": 0.9},
            {"title": "AI Act passes", "summary": "EU", "cluster_id": 1,
             "cluster_name": "AI Act", "url": "https://x.com/2", "rating": 0.8},
            {"title": "Lone story 1", "summary": "X", "cluster_id": -1,
             "url": "https://x.com/3", "rating": 0.5},
            {"title": "Lone story 2", "summary": "Y", "cluster_id": -1,
             "url": "https://x.com/4", "rating": 0.4},
            {"title": "Lone story 3", "summary": "Z", "cluster_id": -1,
             "url": "https://x.com/5", "rating": 0.3},
        ],
    )

    runner = CliRunner()
    result = runner.invoke(select_cli, [
        "--db", tmp_db, "--session", "s_prep", "--prepare-batches",
    ])
    assert result.exit_code == 0, result.output

    # Noise-assign subdir: one batch with all 3 noise headlines (batch-size default 25)
    assign_dir = Path("runs/s_prep/select-assign-batches")
    assign_files = sorted(assign_dir.glob("batch-*.json"))
    assert len(assign_files) == 1
    assign_payload = json.loads(assign_files[0].read_text())
    assert len(assign_payload["headlines"]) == 3
    assert assign_payload["clusters"][0]["name"] in ("OpenAI", "AI Act")

    # Merge-pairs subdir: written only if cosine cluster-name similarities yield candidates;
    # for this seed the two cluster names differ, so 0 pairs → no merge batch file written.
    merge_dir = Path("runs/s_prep/select-merge-batches")
    if merge_dir.exists():
        # If anything was written it must be a single batch
        merge_files = sorted(merge_dir.glob("batch-*.json"))
        assert len(merge_files) <= 1
```

You may need to update `_seed_state` in this file (or write a thin local helper) so the test seeds the headlines listed above. Inspect the existing `_seed_state` signature in `tests/test_step_select.py:22` and either reuse it or write a `_seed_headlines(tmp_db, session_id, headlines)` helper above the test that calls `state.complete_step("rate")`, sets `state.headline_data = headlines`, and `state.save_checkpoint("rate")`.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_step_select.py::test_select_prepare_writes_both_subdirs -v`
Expected: FAIL (no `--prepare-batches` flag).

- [ ] **Step 3: Write minimal implementation**

The select step's prepare/apply is more involved than cluster's. Add two helper modules inside `lib/steps/select.py`. Replace the whole file with the refactored version below. The key shape: `_prepare_assign_batches`, `_prepare_merge_batch`, `_apply_assign_results`, `_apply_merge_results`, then run MMR in-process during apply.

```python
"""newsagent:select — LLM noise assignment + cluster merge + MMR diversity selection.

Three modes (mirror of lib/steps/filter.py):
  1. classic (default): in-process call_prompt loop for noise-assign and merge.
  2. --prepare-batches: write
       runs/<SID>/select-assign-batches/batch-NNN.json  (noise headlines, sharded)
       runs/<SID>/select-merge-batches/batch-000.json   (all merge-candidate pairs)
  3. --apply-results DIR: read result JSONs from
       <DIR>/../select-assign-results/ and <DIR>/../select-merge-results/,
     apply assignments + merges, then run MMR in-process.

For --apply-results the user passes the SESSION runs dir (e.g. runs/<SID>);
the step looks for both `select-assign-results/` and `select-merge-results/`
inside it.
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import click
import numpy as np
from pydantic import ValidationError

import lib.prompts  # noqa: F401 — register all prompts
from lib.embeddings import embed_texts
from lib.llm import call_prompt, get_prompt
from lib.mmr import mmr_select
from lib.prompts.assign_noise_batch import (
    AssignNoiseBatchInput,
    AssignNoiseBatchOutput,
)
from lib.prompts.assign_noise import ClusterDescriptor
from lib.prompts.merge_clusters_batch import (
    MergeClustersBatchInput,
    MergeClustersBatchOutput,
    PairToDecide,
)
from lib.prompts.merge_clusters import ClusterPair
from lib.state import NewsletterAgentState


_DEFAULT_K_PER_CLUSTER = 5
_MMR_LAMBDA = 0.7
_MERGE_SIM_THRESHOLD = 0.85
_DEFAULT_ASSIGN_BATCH = 25
_ASSIGN_BATCHES_SUBDIR = "select-assign-batches"
_ASSIGN_RESULTS_SUBDIR = "select-assign-results"
_MERGE_BATCHES_SUBDIR = "select-merge-batches"
_MERGE_RESULTS_SUBDIR = "select-merge-results"


# ──────────────────────────────────────────────────────────────────────
# Helpers shared by all three modes
# ──────────────────────────────────────────────────────────────────────

def _build_cluster_snapshot(state: NewsletterAgentState):
    by_cluster: Dict[int, List[int]] = defaultdict(list)
    for i, h in enumerate(state.headline_data):
        cid = h.get("cluster_id", -1)
        by_cluster[cid].append(i)
    cluster_names: Dict[int, str] = {}
    for cid, indices in by_cluster.items():
        if cid < 0:
            continue
        name = state.headline_data[indices[0]].get("cluster_name", f"Cluster {cid}")
        cluster_names[cid] = name
    return by_cluster, cluster_names


def _cluster_descriptors(state, by_cluster, cluster_names) -> list[dict]:
    out = []
    for cid in sorted(cluster_names.keys()):
        sample_titles = [state.headline_data[i]["title"] for i in by_cluster[cid][:3]]
        out.append({
            "id": str(cid),
            "name": cluster_names[cid],
            "sample_headlines": sample_titles,
        })
    return out


def _find_merge_candidates(state, by_cluster, cluster_names) -> list[tuple]:
    """Cosine-similar pairs of cluster names above _MERGE_SIM_THRESHOLD."""
    cids = sorted([cid for cid in by_cluster.keys() if cid >= 0])
    if len(cids) < 2:
        return []
    names_list = [cluster_names.get(cid, "") for cid in cids]
    name_embs = embed_texts(names_list)
    M = np.asarray(name_embs, dtype=np.float64)
    norms = np.linalg.norm(M, axis=1, keepdims=True)
    norms = np.where(norms == 0, 1.0, norms)
    N = M / norms
    sim = N @ N.T
    pairs: list[tuple] = []
    for i in range(len(cids)):
        for j in range(i + 1, len(cids)):
            if sim[i, j] >= _MERGE_SIM_THRESHOLD:
                pairs.append((cids[i], cids[j]))
    return pairs


def _run_mmr_and_finalize(
    state: NewsletterAgentState,
    by_cluster: Dict[int, List[int]],
    cluster_names: Dict[int, str],
    k_per_cluster: int,
    mmr_lambda: float,
):
    sections: List[dict] = []
    for cid, indices in sorted(by_cluster.items()):
        if cid < 0 or not indices:
            continue
        embs = [state.headline_data[i].get("embedding") for i in indices]
        has_all_embs = all(e is not None and len(e) > 0 for e in embs)
        if has_all_embs:
            relevance = [state.headline_data[i].get("rating", 0.0) for i in indices]
            chosen_local = mmr_select(embs, relevance, k=k_per_cluster, lambda_=mmr_lambda)
            picks = [indices[j] for j in chosen_local]
        else:
            sorted_by_rating = sorted(
                indices,
                key=lambda i: state.headline_data[i].get("rating", 0.0),
                reverse=True,
            )
            picks = sorted_by_rating[:k_per_cluster]
        cluster_label = cluster_names.get(cid, f"Cluster {cid}")
        for idx in picks:
            h = state.headline_data[idx]
            sections.append({
                "cat": cluster_label,
                "headline": h.get("title", ""),
                "link": h.get("url", ""),
                "rating": h.get("rating", 0.0),
                "summary": h.get("summary", ""),
                "id": idx,
            })
    state.newsletter_section_data = sections
    state.clusters = {}
    for cid, indices in by_cluster.items():
        if cid < 0:
            continue
        name = cluster_names.get(cid, f"Cluster {cid}")
        state.clusters[name] = [str(i) for i in indices]
    return sections


# ──────────────────────────────────────────────────────────────────────
# Prepare mode
# ──────────────────────────────────────────────────────────────────────

def _render_assign_prompts(headlines: list[dict], clusters: list[dict], allow_new: bool):
    cfg = get_prompt("assign_noise_batch")
    validated = AssignNoiseBatchInput.model_validate({
        "headlines": headlines,
        "clusters": clusters,
        "allow_new": allow_new,
    })
    user = cfg.user_prompt.format(**validated.model_dump())
    return cfg.system_prompt, user


def _render_merge_prompts(pairs: list[dict]):
    cfg = get_prompt("merge_clusters_batch")
    validated = MergeClustersBatchInput.model_validate({"pairs": pairs})
    user = cfg.user_prompt.format(**validated.model_dump())
    return cfg.system_prompt, user


def _prepare_assign_batches(
    session_id: str, state: NewsletterAgentState,
    by_cluster, cluster_names, batch_size: int,
) -> list[Path]:
    noise_indices = list(by_cluster.get(-1, []))
    if not noise_indices or not cluster_names:
        return []
    clusters = _cluster_descriptors(state, by_cluster, cluster_names)
    runs_dir = Path("runs") / session_id
    batches_dir = runs_dir / _ASSIGN_BATCHES_SUBDIR
    if batches_dir.exists():
        for p in batches_dir.glob("batch-*.json"):
            p.unlink()
    batches_dir.mkdir(parents=True, exist_ok=True)

    items = [
        {
            "id": str(idx),  # state.headline_data index, stringified
            "title": state.headline_data[idx].get("title", ""),
            "summary": state.headline_data[idx].get("summary", ""),
        }
        for idx in noise_indices
    ]
    paths: list[Path] = []
    for batch_idx, start in enumerate(range(0, len(items), batch_size)):
        chunk = items[start:start + batch_size]
        system, user = _render_assign_prompts(chunk, clusters, allow_new=True)
        payload = {
            "batch_id": batch_idx,
            "session_id": session_id,
            "ids": [it["id"] for it in chunk],
            "headlines": chunk,
            "clusters": clusters,
            "allow_new": True,
            "system_prompt": system,
            "user_prompt": user,
            "output_schema": AssignNoiseBatchOutput.model_json_schema(),
        }
        path = batches_dir / f"batch-{batch_idx:03d}.json"
        path.write_text(json.dumps(payload, indent=2))
        paths.append(path)
    return paths


def _prepare_merge_batch(
    session_id: str, state: NewsletterAgentState,
    by_cluster, cluster_names,
) -> Optional[Path]:
    candidate_pairs = _find_merge_candidates(state, by_cluster, cluster_names)
    if not candidate_pairs:
        return None
    pairs_payload = []
    for i, (cid_a, cid_b) in enumerate(candidate_pairs):
        a_titles = [state.headline_data[ix]["title"] for ix in by_cluster[cid_a][:5]]
        b_titles = [state.headline_data[ix]["title"] for ix in by_cluster[cid_b][:5]]
        pairs_payload.append({
            "pair_id": str(i),
            "a": {"id": str(cid_a), "name": cluster_names[cid_a], "top_headlines": a_titles},
            "b": {"id": str(cid_b), "name": cluster_names[cid_b], "top_headlines": b_titles},
        })
    runs_dir = Path("runs") / session_id
    batches_dir = runs_dir / _MERGE_BATCHES_SUBDIR
    if batches_dir.exists():
        for p in batches_dir.glob("batch-*.json"):
            p.unlink()
    batches_dir.mkdir(parents=True, exist_ok=True)
    system, user = _render_merge_prompts(pairs_payload)
    payload = {
        "batch_id": 0,
        "session_id": session_id,
        "ids": [p["pair_id"] for p in pairs_payload],
        "pairs": pairs_payload,
        "system_prompt": system,
        "user_prompt": user,
        "output_schema": MergeClustersBatchOutput.model_json_schema(),
    }
    path = batches_dir / "batch-000.json"
    path.write_text(json.dumps(payload, indent=2))
    return path


# ──────────────────────────────────────────────────────────────────────
# Apply mode
# ──────────────────────────────────────────────────────────────────────

def _load_assign_results(results_dir: Path) -> tuple[dict[str, str], list[str]]:
    out: dict[str, str] = {}
    problems: list[str] = []
    if not results_dir.exists():
        problems.append(f"assign results dir not found: {results_dir}")
        return {}, problems
    for f in sorted(results_dir.glob("batch-*.json")):
        try:
            raw = json.loads(f.read_text())
            parsed = AssignNoiseBatchOutput.model_validate(raw)
        except (json.JSONDecodeError, ValidationError) as exc:
            problems.append(f"{f.name}: {exc}")
            continue
        for a in parsed.assignments:
            out[a.id] = a.assignment
    return out, problems


def _load_merge_results(results_dir: Path) -> tuple[dict[str, tuple[bool, Optional[str]]], list[str]]:
    out: dict[str, tuple[bool, Optional[str]]] = {}
    problems: list[str] = []
    if not results_dir.exists():
        # Merge results are optional (no candidate pairs → no dir → no problem)
        return {}, problems
    for f in sorted(results_dir.glob("batch-*.json")):
        try:
            raw = json.loads(f.read_text())
            parsed = MergeClustersBatchOutput.model_validate(raw)
        except (json.JSONDecodeError, ValidationError) as exc:
            problems.append(f"{f.name}: {exc}")
            continue
        for d in parsed.decisions:
            out[d.pair_id] = (d.merge, d.merged_name)
    return out, problems


def _apply_assignments(
    state: NewsletterAgentState,
    by_cluster: Dict[int, List[int]],
    cluster_names: Dict[int, str],
    assignments: Dict[str, str],
) -> int:
    """Apply noise assignments. Returns count of headlines newly attached to a cluster."""
    noise_assigned = 0
    next_new_cid = (max(list(by_cluster.keys()) + [-1]) + 1)
    for idx_str, assignment in assignments.items():
        try:
            idx = int(idx_str)
        except ValueError:
            continue
        if idx >= len(state.headline_data):
            continue
        h = state.headline_data[idx]
        if h.get("cluster_id", -2) != -1:
            continue  # only operate on noise points

        if assignment == "none":
            h["cluster_id"] = -2  # drop marker
        elif assignment == "new":
            new_cid = next_new_cid
            next_new_cid += 1
            label = h.get("title", "")[:60]
            h["cluster_id"] = new_cid
            h["cluster_name"] = label
            by_cluster[new_cid].append(idx)
            cluster_names[new_cid] = label
            noise_assigned += 1
        else:
            try:
                target_cid = int(assignment)
            except ValueError:
                h["cluster_id"] = -2
                continue
            if target_cid in cluster_names:
                h["cluster_id"] = target_cid
                h["cluster_name"] = cluster_names[target_cid]
                by_cluster[target_cid].append(idx)
                noise_assigned += 1
            else:
                h["cluster_id"] = -2

    # Drop -2 markers
    state.headline_data = [h for h in state.headline_data if h.get("cluster_id", -2) != -2]

    # Rebuild by_cluster from cleaned headline list
    by_cluster.clear()
    for i, h in enumerate(state.headline_data):
        cid = h.get("cluster_id", -1)
        by_cluster[cid].append(i)
    return noise_assigned


def _apply_merges(
    state: NewsletterAgentState,
    by_cluster: Dict[int, List[int]],
    cluster_names: Dict[int, str],
    pairs_payload: list[dict],
    decisions: Dict[str, tuple[bool, Optional[str]]],
) -> int:
    merges_done = 0
    for pair in pairs_payload:
        pair_id = pair["pair_id"]
        cid_a = int(pair["a"]["id"])
        cid_b = int(pair["b"]["id"])
        if pair_id not in decisions:
            continue
        merge, merged_name_opt = decisions[pair_id]
        if not merge:
            continue
        if cid_a not in by_cluster or cid_b not in by_cluster:
            continue
        merged_name = merged_name_opt or cluster_names[cid_a]
        for idx in by_cluster[cid_b]:
            state.headline_data[idx]["cluster_id"] = cid_a
            state.headline_data[idx]["cluster_name"] = merged_name
        for idx in by_cluster[cid_a]:
            state.headline_data[idx]["cluster_name"] = merged_name
        by_cluster[cid_a].extend(by_cluster[cid_b])
        del by_cluster[cid_b]
        cluster_names[cid_a] = merged_name
        if cid_b in cluster_names:
            del cluster_names[cid_b]
        merges_done += 1
    return merges_done


# ──────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────

@click.command()
@click.option("--db", "db_path", default="newsletter_agent.db")
@click.option("--session", "session_id", required=True)
@click.option("--k", "k_per_cluster", default=_DEFAULT_K_PER_CLUSTER, type=int)
@click.option("--lambda", "mmr_lambda", default=_MMR_LAMBDA, type=float)
@click.option("--engine", default=None, help="Override LLM engine for classic mode")
@click.option("--no-noise-assign", is_flag=True, help="Skip LLM noise-point assignment")
@click.option("--no-merge", is_flag=True, help="Skip LLM cluster merge step")
@click.option("--prepare-batches", is_flag=True,
              help="Write batches to runs/<SID>/select-{assign,merge}-batches/ for subagent dispatch")
@click.option("--apply-results", "apply_results_dir", default=None,
              help="Read results from runs/<SID>/select-{assign,merge}-results/ and apply")
@click.option("--batch-size", type=int, default=_DEFAULT_ASSIGN_BATCH,
              show_default=True, help="Noise headlines per assign batch (prepare mode)")
def cli(
    db_path: str,
    session_id: str,
    k_per_cluster: int,
    mmr_lambda: float,
    engine: Optional[str],
    no_noise_assign: bool,
    no_merge: bool,
    prepare_batches: bool,
    apply_results_dir: Optional[str],
    batch_size: int,
) -> None:
    if prepare_batches and apply_results_dir:
        raise click.UsageError("--prepare-batches and --apply-results are mutually exclusive")

    state = NewsletterAgentState(session_id=session_id, db_path=db_path).load_latest_from_db()
    if state is None:
        raise click.ClickException(f"No state found for session {session_id}")

    by_cluster, cluster_names = _build_cluster_snapshot(state)

    # ── prepare mode ───────────────────────────────────────────────
    if prepare_batches:
        state.start_step("select")
        state.save_checkpoint("select")

        assign_paths = (
            [] if no_noise_assign else
            _prepare_assign_batches(session_id, state, by_cluster, cluster_names, batch_size)
        )
        merge_path = (
            None if no_merge else
            _prepare_merge_batch(session_id, state, by_cluster, cluster_names)
        )

        click.echo(f"Prepared {len(assign_paths)} assign batch(es).")
        for p in assign_paths:
            click.echo(f"  {p}")
        if merge_path is not None:
            click.echo(f"Prepared merge batch: {merge_path}")
        else:
            click.echo("No merge-candidate pairs found; merge batch skipped.")
        click.echo(f"\nDispatch one Haiku subagent per batch. Write each result to:")
        click.echo(f"  runs/{session_id}/{_ASSIGN_RESULTS_SUBDIR}/batch-NNN.json (assign)")
        click.echo(f"  runs/{session_id}/{_MERGE_RESULTS_SUBDIR}/batch-000.json (merge)")
        click.echo(f"Then run: python -m lib.steps.select --session {session_id} "
                   f"--apply-results runs/{session_id}")
        return

    # ── apply mode ─────────────────────────────────────────────────
    if apply_results_dir:
        root = Path(apply_results_dir)
        assignments, a_problems = _load_assign_results(root / _ASSIGN_RESULTS_SUBDIR)
        decisions, m_problems = _load_merge_results(root / _MERGE_RESULTS_SUBDIR)
        problems = a_problems + m_problems
        if problems:
            click.echo("Apply found problems:", err=True)
            for p in problems:
                click.echo(f"  - {p}", err=True)

        noise_assigned = _apply_assignments(state, by_cluster, cluster_names, assignments)

        # Re-derive merge pairs payload by reading the merge batch file
        merge_batch_file = Path("runs") / session_id / _MERGE_BATCHES_SUBDIR / "batch-000.json"
        pairs_payload = []
        if merge_batch_file.exists():
            pairs_payload = json.loads(merge_batch_file.read_text()).get("pairs", [])
        merges_done = _apply_merges(state, by_cluster, cluster_names, pairs_payload, decisions)

        sections = _run_mmr_and_finalize(state, by_cluster, cluster_names, k_per_cluster, mmr_lambda)

        state.complete_step(
            "select",
            message=f"{len(sections)} headlines across {len(state.clusters)} sections",
        )
        state.save_checkpoint("select")

        runs_dir = Path("runs") / session_id
        runs_dir.mkdir(parents=True, exist_ok=True)
        (runs_dir / "select.json").write_text(json.dumps({
            "session_id": session_id,
            "completed_at": datetime.now().isoformat(),
            "noise_assigned": noise_assigned,
            "merges_done": merges_done,
            "sections_count": len(state.clusters),
            "headlines_selected": len(sections),
            "k_per_cluster": k_per_cluster,
            "mmr_lambda": mmr_lambda,
        }, indent=2))
        click.echo(
            f"Select: {len(sections)} headlines across {len(state.clusters)} sections "
            f"(noise_assigned={noise_assigned}, merges={merges_done})."
        )
        return

    # ── classic mode ───────────────────────────────────────────────
    # (unchanged behavior — kept verbatim from current select.py for cron/CI)
    state.start_step("select")
    state.save_checkpoint("select")

    noise_indices = list(by_cluster.get(-1, []))
    noise_assigned = 0
    if not no_noise_assign and noise_indices and cluster_names:
        existing = _cluster_descriptors(state, by_cluster, cluster_names)
        for idx in noise_indices:
            h = state.headline_data[idx]
            try:
                result = call_prompt("assign_noise", {
                    "headline_title": h.get("title", ""),
                    "headline_summary": h.get("summary", ""),
                    "clusters": existing,
                    "allow_new": True,
                }, engine=engine)
                assignment = result.assignment
            except Exception:
                assignment = "none"

            if assignment == "none":
                h["cluster_id"] = -2
            elif assignment == "new":
                all_cids = list(by_cluster.keys())
                new_cid = max(all_cids) + 1 if all_cids else 0
                label = h.get("title", "")[:60]
                h["cluster_id"] = new_cid
                h["cluster_name"] = label
                by_cluster[new_cid].append(idx)
                cluster_names[new_cid] = label
                noise_assigned += 1
            else:
                try:
                    target_cid = int(assignment)
                except ValueError:
                    h["cluster_id"] = -2
                    continue
                if target_cid in cluster_names:
                    h["cluster_id"] = target_cid
                    h["cluster_name"] = cluster_names[target_cid]
                    by_cluster[target_cid].append(idx)
                    noise_assigned += 1
                else:
                    h["cluster_id"] = -2

    state.headline_data = [h for h in state.headline_data if h.get("cluster_id", -2) != -2]
    by_cluster.clear()
    for i, h in enumerate(state.headline_data):
        cid = h.get("cluster_id", -1)
        by_cluster[cid].append(i)

    merges_done = 0
    if not no_merge and len(cluster_names) >= 2:
        candidate_pairs = _find_merge_candidates(state, by_cluster, cluster_names)
        for cid_a, cid_b in candidate_pairs:
            if cid_a not in by_cluster or cid_b not in by_cluster:
                continue
            a_titles = [state.headline_data[i]["title"] for i in by_cluster[cid_a][:5]]
            b_titles = [state.headline_data[i]["title"] for i in by_cluster[cid_b][:5]]
            try:
                result = call_prompt("merge_clusters", {
                    "a": {"id": str(cid_a), "name": cluster_names[cid_a], "top_headlines": a_titles},
                    "b": {"id": str(cid_b), "name": cluster_names[cid_b], "top_headlines": b_titles},
                }, engine=engine)
            except Exception:
                continue
            if result.merge:
                merged_name = result.merged_name or cluster_names[cid_a]
                for idx in by_cluster[cid_b]:
                    state.headline_data[idx]["cluster_id"] = cid_a
                    state.headline_data[idx]["cluster_name"] = merged_name
                for idx in by_cluster[cid_a]:
                    state.headline_data[idx]["cluster_name"] = merged_name
                by_cluster[cid_a].extend(by_cluster[cid_b])
                del by_cluster[cid_b]
                cluster_names[cid_a] = merged_name
                if cid_b in cluster_names:
                    del cluster_names[cid_b]
                merges_done += 1

    sections = _run_mmr_and_finalize(state, by_cluster, cluster_names, k_per_cluster, mmr_lambda)

    state.complete_step("select", message=f"{len(sections)} headlines across {len(state.clusters)} sections")
    state.save_checkpoint("select")

    runs_dir = Path("runs") / session_id
    runs_dir.mkdir(parents=True, exist_ok=True)
    (runs_dir / "select.json").write_text(json.dumps({
        "session_id": session_id,
        "completed_at": datetime.now().isoformat(),
        "noise_assigned": noise_assigned,
        "merges_done": merges_done,
        "sections_count": len(state.clusters),
        "headlines_selected": len(sections),
        "k_per_cluster": k_per_cluster,
        "mmr_lambda": mmr_lambda,
    }, indent=2))
    click.echo(
        f"Select: {len(sections)} headlines across {len(state.clusters)} sections "
        f"(noise_assigned={noise_assigned}, merges={merges_done})."
    )


if __name__ == "__main__":  # pragma: no cover
    sys.exit(cli())
```

You will also need to stub embedding calls in the new test. In the same test file, monkeypatch `lib.steps.select.embed_texts` to return zero vectors so `_find_merge_candidates` doesn't hit OpenAI:

```python
# In test_select_prepare_writes_both_subdirs, before invoking the CLI:
monkeypatch.setattr(
    "lib.steps.select.embed_texts",
    lambda texts: [[0.0] * 8 for _ in texts],
)
```

Add this line to the test (just before the `runner = CliRunner()` line).

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_step_select.py::test_select_prepare_writes_both_subdirs -v`
Expected: PASS.

Then run the whole select test file:

Run: `.venv/bin/pytest tests/test_step_select.py -v`
Expected: all existing tests still PASS (the classic-mode code path is preserved verbatim).

- [ ] **Step 5: Commit**

```bash
git add lib/steps/select.py tests/test_step_select.py
git commit -m "feat(steps): select --prepare-batches writes assign + merge subdirs"
```

---

### Task 9: `select --apply-results` reads both subdirs and runs MMR

**Files:**
- Test: `tests/test_step_select.py`

Implementation was added in Task 8. This task adds the apply-side tests.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_step_select.py`:

```python
def test_select_apply_assigns_noise_and_runs_mmr(tmp_db, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    _seed_state(
        tmp_db,
        session_id="s_apply",
        headlines=[
            {"title": "GPT-6 ships", "summary": "OpenAI", "cluster_id": 0,
             "cluster_name": "OpenAI", "url": "https://x.com/1", "rating": 0.9,
             "embedding": [1.0, 0.0]},
            {"title": "OpenAI roadmap", "summary": "More", "cluster_id": 0,
             "cluster_name": "OpenAI", "url": "https://x.com/2", "rating": 0.8,
             "embedding": [0.9, 0.1]},
            {"title": "Lone noise", "summary": "OpenAI-related", "cluster_id": -1,
             "url": "https://x.com/3", "rating": 0.7,
             "embedding": [0.8, 0.2]},
        ],
    )

    monkeypatch.setattr(
        "lib.steps.select.embed_texts",
        lambda texts: [[0.0] * 8 for _ in texts],
    )

    runner = CliRunner()
    prep = runner.invoke(select_cli, [
        "--db", tmp_db, "--session", "s_apply", "--prepare-batches",
    ])
    assert prep.exit_code == 0, prep.output

    # Fake Agent assigns the noise headline (state index 2) to cluster 0
    assign_dir = Path("runs/s_apply/select-assign-results")
    assign_dir.mkdir(parents=True)
    (assign_dir / "batch-000.json").write_text(json.dumps({
        "assignments": [{"id": "2", "assignment": "0"}]
    }))

    apply_res = runner.invoke(select_cli, [
        "--db", tmp_db, "--session", "s_apply",
        "--apply-results", "runs/s_apply",
    ])
    assert apply_res.exit_code == 0, apply_res.output

    state = NewsletterAgentState(session_id="s_apply", db_path=tmp_db).load_latest_from_db()
    # Noise headline now part of cluster 0
    by_cluster = defaultdict(list)
    for h in state.headline_data:
        by_cluster[h.get("cluster_id", -1)].append(h)
    assert len(by_cluster[0]) == 3
    assert -1 not in by_cluster

    # newsletter_section_data populated
    assert len(state.newsletter_section_data) > 0
    assert state.newsletter_section_data[0]["cat"] == "OpenAI"
```

Add `from collections import defaultdict` at the top of the test file if not already imported.

- [ ] **Step 2: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_step_select.py::test_select_apply_assigns_noise_and_runs_mmr -v`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/test_step_select.py
git commit -m "test(select): apply-results assigns noise and runs MMR"
```

---

### Task 10: Update `skills/select/SKILL.md`

**Files:**
- Modify: `skills/select/SKILL.md`

- [ ] **Step 1: Replace contents**

Overwrite `skills/select/SKILL.md` with a parallel structure to `skills/cluster/SKILL.md`. Key differences: two batch subdirs, dispatch BOTH in one parent message.

```markdown
---
name: select
description: Select a diverse top-K set of headlines per topic cluster. Runs LLM noise assignment for cluster_id=-1 headlines, embedding-based cluster merge for similar clusters, and MMR selection per surviving cluster to balance rating with embedding diversity.
---

# newsagent:select

Step 9 of /newsagent:run. Two execution paths.

## Interactive mode — Haiku subagent dispatch

### Step 1: prepare batches

```bash
python -m lib.steps.select --session SID --prepare-batches
```

Writes two subdirs under `runs/<SID>/`:
- `select-assign-batches/batch-NNN.json` — noise headlines sharded 25 per
  batch. Each batch carries the same cluster descriptors plus the headlines
  to assign.
- `select-merge-batches/batch-000.json` — all candidate near-duplicate cluster
  pairs (post-cosine filter). Skipped if no candidates exist.

Each batch file is self-contained (system_prompt + user_prompt + output_schema
+ items). No further `lib/prompts/` access needed by subagents.

### Step 2: dispatch Haiku subagents (all in ONE parent message)

For each assign batch and (if present) the merge batch, dispatch one Agent
with `model: "haiku"`. Dispatch all of them in the same message so they run
in parallel.

Per-Agent prompt skeleton:

```
Read runs/<SID>/select-assign-batches/batch-NNN.json
  (or runs/<SID>/select-merge-batches/batch-000.json).
The file contains system_prompt, user_prompt, output_schema, and the items.
Follow system_prompt + user_prompt. Return ONLY a JSON object matching
output_schema, with exactly one entry per id (no duplicates, no extras).
Write to runs/<SID>/select-assign-results/batch-NNN.json
  (or runs/<SID>/select-merge-results/batch-000.json)
using the Write tool. Then report the path.
```

### Step 3: apply results

```bash
python -m lib.steps.select --session SID --apply-results runs/<SID>
```

Note: `--apply-results` takes the **session runs dir** (e.g. `runs/<SID>`),
not a specific subdir. The step looks for both
`select-assign-results/` and `select-merge-results/` inside it.

Applies assignments (`none` → drop, `new` → new singleton cluster,
`<cid>` → attach), applies merges, then runs MMR top-K per surviving cluster.

### Step 4: retry on failure

Per the filter/summarize pattern: re-dispatch only the failing batch(es) and
re-run `--apply-results`. Partial application is idempotent.

## Classic mode (non-interactive)

```bash
python -m lib.steps.select --session SID --engine openai:gpt-4o-mini
```

In-process calls for each noise headline and each candidate merge pair.

## Output contract

- HDBSCAN noise headlines either attached to an existing cluster, promoted to
  a new singleton, or dropped from `state.headline_data`.
- Similar clusters merged according to `merge_clusters_batch` decisions.
- `state.newsletter_section_data` populated by MMR top-K per cluster.
- `state.clusters` populated with `{name: [headline_index_str, ...]}`.
- `runs/<SID>/select.json` written with counts.
- `select` step marked COMPLETE.
```

- [ ] **Step 2: Commit**

```bash
git add skills/select/SKILL.md
git commit -m "docs(skills): select SKILL.md documents two-subdir interactive flow"
```

---

## Phase 4 — `draft` prepare/apply

The Agent runs the **full critic-optimizer loop** inside its own context. The batch JSON contains all three pre-rendered prompts plus `max_edits` and the output schema (`DraftSectionResult`).

### Task 11: Add `draft --prepare-batches` with all three prompts embedded

**Files:**
- Modify: `lib/steps/draft.py`
- Test: `tests/test_step_draft.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_step_draft.py`:

```python
import json
from pathlib import Path


def test_draft_prepare_writes_one_batch_per_section(tmp_db, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    _seed_state(tmp_db, session_id="d_prep")  # uses existing helper

    runner = CliRunner()
    result = runner.invoke(draft_cli, [
        "--db", tmp_db, "--session", "d_prep", "--prepare-batches",
        "--max-edits", "2",
    ])
    assert result.exit_code == 0, result.output

    batches_dir = Path("runs/d_prep/draft-batches")
    files = sorted(batches_dir.glob("batch-*.json"))
    # One batch per cat in the seeded state
    assert len(files) >= 1

    payload = json.loads(files[0].read_text())
    assert "cat" in payload
    assert "stories" in payload
    assert payload["max_edits"] == 2
    # All three prompts pre-rendered + present
    for key in ("write_system_prompt", "write_user_prompt",
                "critique_system_prompt", "critique_user_prompt",
                "improve_system_prompt", "improve_user_prompt"):
        assert key in payload and payload[key]
    # Output schema is DraftSectionResult
    schema = payload["output_schema"]
    assert "final_section_markdown" in schema["properties"]
    assert "iterations" in schema["properties"]
```

You will need to import `draft_cli` and `CliRunner` if not already in the test module:

```python
from click.testing import CliRunner
from lib.steps.draft import cli as draft_cli
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_step_draft.py::test_draft_prepare_writes_one_batch_per_section -v`
Expected: FAIL (no `--prepare-batches` flag).

- [ ] **Step 3: Write minimal implementation**

Replace `lib/steps/draft.py` with the refactored version. Keep classic mode (the existing critic loop in `ThreadPoolExecutor`). Add prepare/apply modes that bypass the in-process loop.

```python
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
from lib.prompts.critique_section import CritiqueSectionInput
from lib.prompts.improve_section import ImproveSectionInput
from lib.prompts._critic_schemas import CritiqueResult, SectionDraft
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
    results_dir: Path, expected_cats: set[str],
) -> tuple[dict[str, DraftSectionResult], list[str]]:
    out: dict[str, DraftSectionResult] = {}
    problems: list[str] = []
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
) -> tuple[str, CriticTranscript]:
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_step_draft.py::test_draft_prepare_writes_one_batch_per_section -v`
Expected: PASS.

Then run the whole draft test file:

Run: `.venv/bin/pytest tests/test_step_draft.py -v`
Expected: all existing tests still PASS.

- [ ] **Step 5: Commit**

```bash
git add lib/steps/draft.py tests/test_step_draft.py
git commit -m "feat(steps): draft --prepare-batches writes per-section Sonnet batches"
```

---

### Task 12: `draft --apply-results` reads section transcripts

**Files:**
- Test: `tests/test_step_draft.py`

Implementation was added in Task 11. This task adds the apply-side test.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_step_draft.py`:

```python
def test_draft_apply_reads_results_and_writes_sections(tmp_db, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    _seed_state(tmp_db, session_id="d_apply")

    runner = CliRunner()
    prep = runner.invoke(draft_cli, [
        "--db", tmp_db, "--session", "d_apply", "--prepare-batches",
    ])
    assert prep.exit_code == 0, prep.output

    # Figure out which cats were prepared
    batch_files = sorted(Path("runs/d_apply/draft-batches").glob("batch-*.json"))
    cats = [json.loads(f.read_text())["cat"] for f in batch_files]
    assert len(cats) >= 1

    results_dir = Path("runs/d_apply/draft-results")
    results_dir.mkdir(parents=True)
    for i, cat in enumerate(cats):
        (results_dir / f"batch-{i:03d}.json").write_text(json.dumps({
            "cat": cat,
            "final_section_markdown": f"## {cat}\n- fake headline",
            "iterations": 1,
            "scores": [7.5],
            "feedbacks": ["needs more"],
            "accepted": False,
        }))

    apply_res = runner.invoke(draft_cli, [
        "--db", tmp_db, "--session", "d_apply",
        "--apply-results", str(results_dir),
    ])
    assert apply_res.exit_code == 0, apply_res.output

    state = NewsletterAgentState(session_id="d_apply", db_path=tmp_db).load_latest_from_db()
    assert state is not None
    section_cats = {s["cat"] for s in state.newsletter_section_data}
    assert section_cats == set(cats)
    for s in state.newsletter_section_data:
        assert s["section_markdown"].startswith("## ")
```

- [ ] **Step 2: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_step_draft.py::test_draft_apply_reads_results_and_writes_sections -v`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/test_step_draft.py
git commit -m "test(draft): apply-results writes section markdowns from Agent transcripts"
```

---

### Task 13: Update `skills/draft/SKILL.md`

**Files:**
- Modify: `skills/draft/SKILL.md`

- [ ] **Step 1: Replace contents**

Overwrite `skills/draft/SKILL.md` with:

```markdown
---
name: draft
description: Draft markdown newsletter sections in parallel with a critic-optimizer loop. For each topic cluster, writes a section via write_section, then iteratively critiques and improves it (max-edits N, early exit at score >= 8.0).
---

# newsagent:draft

Step 10 of /newsagent:run. Two execution paths.

## Interactive mode — Sonnet subagent dispatch

The Agent runs the **entire critic-optimizer loop** inside its own context:
write → critique → if accept or score>=8.0 stop, else improve → next iteration,
up to `max_edits` iterations. Parent Claude just dispatches one Agent per
section in parallel and applies results when they're all back.

### Step 1: prepare batches

```bash
python -m lib.steps.draft --session SID --prepare-batches [--max-edits 2]
```

Writes one self-contained batch per section under
`runs/<SID>/draft-batches/batch-NNN.json`. Each batch contains:
- `cat`, `stories`
- `max_edits`, `accept_threshold`
- Three pre-rendered prompts: `write_*`, `critique_*`, `improve_*`
- `output_schema` (`DraftSectionResult`)

### Step 2: dispatch one Sonnet subagent per batch (all in ONE message)

Per-Agent config:

- `subagent_type: "general-purpose"`
- `model: "sonnet"`
- `description: "Draft section <cat>"`
- `prompt:` instructions of the shape:

  ```
  Read runs/<SID>/draft-batches/batch-NNN.json. It contains:
    - stories: the source material
    - max_edits, accept_threshold
    - write_system_prompt, write_user_prompt (already rendered, ready to send)
    - critique_system_prompt, critique_user_prompt (template; you will substitute
      {section_markdown} and {critique} as needed)
    - improve_system_prompt, improve_user_prompt (template; same)
    - output_schema: required JSON shape (DraftSectionResult)

  Run this loop in your own context:
    1. Call write: system_prompt=write_system_prompt, user_prompt=write_user_prompt
       → initial section markdown.
    2. For up to max_edits iterations:
       a. Substitute {section_markdown} into critique_user_prompt and call critique
          (system_prompt=critique_system_prompt). Read score (float) and accept (bool).
       b. If accept is true OR score >= accept_threshold: stop, record this iteration,
          mark accepted=true.
       c. Otherwise substitute {section_markdown} + {critique} into improve_user_prompt
          and call improve → new section markdown. Continue.

  Return ONLY a JSON object matching output_schema with:
    cat, final_section_markdown, iterations, scores, feedbacks, accepted.

  Write that JSON object to:
    runs/<SID>/draft-results/batch-NNN.json
  using the Write tool. Then report the path.
  ```

Dispatch all N section Agents in a single parent message so they run in
parallel.

### Step 3: apply results

```bash
python -m lib.steps.draft --session SID \
  --apply-results runs/<SID>/draft-results
```

Validates each result file against `DraftSectionResult`, writes
`{cat, section_markdown}` entries into `state.newsletter_section_data`,
writes `runs/<SID>/draft.json` with the per-section transcripts.

### Step 4: retry on failure

If apply reports `missing sections: [...]` or `schema mismatch`:
re-dispatch only the failing section Agent(s), re-run apply.

## Classic mode (non-interactive)

```bash
python -m lib.steps.draft --session SID --engine openai:gpt-4o-mini \
  --max-edits 2 --parallelism 4
```

In-process `ThreadPoolExecutor` runs the critic loop via
`critic_optimizer_loop`. Do not use `--engine subagent` — it falls back to
`claude -p`.

## Output contract

- `state.newsletter_section_data` replaced with `[{cat, section_markdown}, ...]`.
- `runs/<SID>/draft.json` written with per-section transcript metadata.
- `draft` step marked COMPLETE.
```

- [ ] **Step 2: Commit**

```bash
git add skills/draft/SKILL.md
git commit -m "docs(skills): draft SKILL.md documents full-loop-per-Agent path"
```

---

## Phase 5 — `rewrite` prepare/apply

Single Agent (Sonnet) handles the whole newsletter: critic loop + title.

### Task 14: Add `rewrite --prepare-batches` (single batch with all prompts)

**Files:**
- Modify: `lib/steps/rewrite.py`
- Test: `tests/test_step_rewrite.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_step_rewrite.py`:

```python
import json
from pathlib import Path


def test_rewrite_prepare_writes_single_batch(tmp_db, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    _seed_state(tmp_db, session_id="r_prep")  # existing helper

    runner = CliRunner()
    result = runner.invoke(rewrite_cli, [
        "--db", tmp_db, "--session", "r_prep", "--prepare-batches",
        "--max-edits", "2",
    ])
    assert result.exit_code == 0, result.output

    files = sorted(Path("runs/r_prep/rewrite-batches").glob("batch-*.json"))
    assert len(files) == 1

    payload = json.loads(files[0].read_text())
    assert "initial_draft" in payload and payload["initial_draft"]
    assert payload["max_edits"] == 2
    for key in (
        "critique_system_prompt", "critique_user_prompt",
        "improve_system_prompt", "improve_user_prompt",
        "title_system_prompt", "title_user_prompt",
    ):
        assert key in payload and payload[key]
    schema = payload["output_schema"]
    assert "final_newsletter_markdown" in schema["properties"]
    assert "title" in schema["properties"]
```

Import additions at top of `tests/test_step_rewrite.py` if needed:

```python
from click.testing import CliRunner
from lib.steps.rewrite import cli as rewrite_cli
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_step_rewrite.py::test_rewrite_prepare_writes_single_batch -v`
Expected: FAIL (no `--prepare-batches` flag).

- [ ] **Step 3: Write minimal implementation**

Replace `lib/steps/rewrite.py` with:

```python
"""newsagent:rewrite — whole-newsletter critic-optimizer pass + title generation.

Three modes (mirror of lib/steps/filter.py):
  1. classic (default): in-process critic_optimizer_loop + generate_title.
     Use --engine to override (DO NOT use subagent).
  2. --prepare-batches: write one batch JSON containing the initial draft and
     all three pre-rendered prompts (critique/improve/title) so a single
     Sonnet Agent can run the critic loop and generate the title in its own
     context.
  3. --apply-results DIR: read the single result JSON, set state.final_newsletter
     and state.newsletter_title.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

import click
from pydantic import ValidationError

import lib.prompts  # noqa: F401 — register all prompts
from lib.critic import critic_optimizer_loop
from lib.llm import call_prompt, get_prompt
from lib.prompts._dispatch_schemas import RewriteResult
from lib.state import NewsletterAgentState


_BATCHES_SUBDIR = "rewrite-batches"
_RESULTS_SUBDIR = "rewrite-results"


def _initial_draft(state: NewsletterAgentState) -> str:
    return "\n\n".join(
        s.get("section_markdown", "") for s in state.newsletter_section_data
    )


def _prepare_batch(session_id: str, draft: str, max_edits: int) -> Path:
    crit_cfg = get_prompt("critique_newsletter")
    imp_cfg = get_prompt("improve_newsletter")
    title_cfg = get_prompt("generate_newsletter_title")

    runs_dir = Path("runs") / session_id
    batches_dir = runs_dir / _BATCHES_SUBDIR
    if batches_dir.exists():
        for p in batches_dir.glob("batch-*.json"):
            p.unlink()
    batches_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "batch_id": 0,
        "session_id": session_id,
        "initial_draft": draft,
        "max_edits": max_edits,
        "accept_threshold": 8.0,
        "critique_system_prompt": crit_cfg.system_prompt,
        "critique_user_prompt": crit_cfg.user_prompt,
        "improve_system_prompt": imp_cfg.system_prompt,
        "improve_user_prompt": imp_cfg.user_prompt,
        "title_system_prompt": title_cfg.system_prompt,
        "title_user_prompt": title_cfg.user_prompt,
        "output_schema": RewriteResult.model_json_schema(),
    }
    path = batches_dir / "batch-000.json"
    path.write_text(json.dumps(payload, indent=2))
    return path


def _load_result(results_dir: Path) -> tuple[Optional[RewriteResult], list[str]]:
    problems: list[str] = []
    if not results_dir.exists():
        problems.append(f"results dir not found: {results_dir}")
        return None, problems
    files = sorted(results_dir.glob("batch-*.json"))
    if not files:
        problems.append(f"no batch-*.json files in {results_dir}")
        return None, problems
    if len(files) > 1:
        problems.append(f"expected one result file, found {len(files)}; using first")
    f = files[0]
    try:
        raw = json.loads(f.read_text())
        parsed = RewriteResult.model_validate(raw)
        return parsed, problems
    except (json.JSONDecodeError, ValidationError) as exc:
        problems.append(f"{f.name}: {exc}")
        return None, problems


@click.command()
@click.option("--db", "db_path", default="newsletter_agent.db")
@click.option("--session", "session_id", required=True)
@click.option("--max-edits", default=2, type=int)
@click.option("--engine", default=None, help="Override LLM engine for classic mode")
@click.option("--prepare-batches", is_flag=True,
              help="Write batch to runs/<SID>/rewrite-batches/ for subagent dispatch")
@click.option("--apply-results", "apply_results", default=None,
              help="Read result JSON from this dir and apply to state")
def cli(
    db_path: str,
    session_id: str,
    max_edits: int,
    engine: Optional[str],
    prepare_batches: bool,
    apply_results: Optional[str],
) -> None:
    if prepare_batches and apply_results:
        raise click.UsageError("--prepare-batches and --apply-results are mutually exclusive")

    state = NewsletterAgentState(session_id=session_id, db_path=db_path).load_latest_from_db()
    if state is None:
        raise click.ClickException(f"No state found for session {session_id}")

    # ── prepare mode ───────────────────────────────────────────────
    if prepare_batches:
        state.start_step("rewrite")
        state.save_checkpoint("rewrite")
        draft = _initial_draft(state)
        if not draft.strip():
            click.echo("Nothing to rewrite (no section markdowns).")
            return
        path = _prepare_batch(session_id, draft, max_edits)
        click.echo(f"Prepared rewrite batch (max_edits={max_edits}): {path}")
        click.echo(f"\nDispatch one Sonnet subagent. It runs the critic loop and")
        click.echo(f"generates the title in its own context. Write result to:")
        click.echo(f"  runs/{session_id}/{_RESULTS_SUBDIR}/batch-000.json")
        click.echo(f"Then run: python -m lib.steps.rewrite --session {session_id} "
                   f"--apply-results runs/{session_id}/{_RESULTS_SUBDIR}")
        return

    # ── apply mode ─────────────────────────────────────────────────
    if apply_results:
        result, problems = _load_result(Path(apply_results))
        if problems:
            click.echo("Apply found problems:", err=True)
            for p in problems:
                click.echo(f"  - {p}", err=True)
        if result is None:
            raise click.ClickException("no usable rewrite result; redispatch")

        state.final_newsletter = f"# {result.title}\n\n{result.final_newsletter_markdown}"
        state.newsletter_title = result.title
        state.complete_step(
            "rewrite",
            message=f"Newsletter rewritten. Title: {result.title!r}. "
                    f"Iterations: {result.iterations}. Accepted: {result.accepted}.",
        )
        state.save_checkpoint("rewrite")

        runs_dir = Path("runs") / session_id
        runs_dir.mkdir(parents=True, exist_ok=True)
        (runs_dir / "rewrite.json").write_text(json.dumps({
            "session_id": session_id,
            "completed_at": datetime.now().isoformat(),
            "title": result.title,
            "transcript": {
                "iterations": result.iterations,
                "scores": result.scores,
                "feedbacks": result.feedbacks,
                "accepted": result.accepted,
                "final_length": len(result.final_newsletter_markdown),
            },
        }, indent=2))
        click.echo(f"Rewrite: '{result.title}' — {result.iterations} iteration(s), accepted={result.accepted}.")
        return

    # ── classic mode ───────────────────────────────────────────────
    state.start_step("rewrite")
    state.save_checkpoint("rewrite")
    draft = _initial_draft(state)
    transcript = critic_optimizer_loop(
        initial_draft=draft,
        critique_prompt_name="critique_newsletter",
        improve_prompt_name="improve_newsletter",
        critique_input_builder=lambda d: {"newsletter_markdown": d},
        improve_input_builder=lambda d, c: {"newsletter_markdown": d, "critique": c},
        draft_field="newsletter_markdown",
        max_edits=max_edits,
        engine=engine,
    )
    final_body = transcript.final_draft
    title_result = call_prompt(
        "generate_newsletter_title",
        {"newsletter_markdown": final_body},
        engine=engine,
    )
    title = title_result.title
    state.final_newsletter = f"# {title}\n\n{final_body}"
    state.newsletter_title = title
    state.complete_step(
        "rewrite",
        message=f"Newsletter rewritten. Title: {title!r}. "
                f"Iterations: {transcript.iterations}. Accepted: {transcript.accepted}.",
    )
    state.save_checkpoint("rewrite")

    runs_dir = Path("runs") / session_id
    runs_dir.mkdir(parents=True, exist_ok=True)
    (runs_dir / "rewrite.json").write_text(json.dumps({
        "session_id": session_id,
        "completed_at": datetime.now().isoformat(),
        "title": title,
        "transcript": {
            "iterations": transcript.iterations,
            "scores": transcript.scores,
            "feedbacks": transcript.feedbacks,
            "accepted": transcript.accepted,
            "final_length": len(final_body),
        },
    }, indent=2))
    click.echo(f"Rewrite: '{title}' — {transcript.iterations} iteration(s), accepted={transcript.accepted}.")


if __name__ == "__main__":  # pragma: no cover
    sys.exit(cli())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_step_rewrite.py::test_rewrite_prepare_writes_single_batch -v`
Expected: PASS.

Run all rewrite tests:

Run: `.venv/bin/pytest tests/test_step_rewrite.py -v`
Expected: all existing classic-mode tests still PASS.

- [ ] **Step 5: Commit**

```bash
git add lib/steps/rewrite.py tests/test_step_rewrite.py
git commit -m "feat(steps): rewrite --prepare-batches writes single Sonnet batch"
```

---

### Task 15: `rewrite --apply-results` writes final newsletter

**Files:**
- Test: `tests/test_step_rewrite.py`

Implementation done in Task 14. This task adds the apply-side test.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_step_rewrite.py`:

```python
def test_rewrite_apply_writes_final_newsletter(tmp_db, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    _seed_state(tmp_db, session_id="r_apply")

    runner = CliRunner()
    prep = runner.invoke(rewrite_cli, [
        "--db", tmp_db, "--session", "r_apply", "--prepare-batches",
    ])
    assert prep.exit_code == 0, prep.output

    results_dir = Path("runs/r_apply/rewrite-results")
    results_dir.mkdir(parents=True)
    (results_dir / "batch-000.json").write_text(json.dumps({
        "final_newsletter_markdown": "## OpenAI\n- ships GPT-6\n\n## EU\n- AI Act",
        "title": "AI Weekly Roundup",
        "iterations": 1,
        "scores": [8.5],
        "feedbacks": ["good"],
        "accepted": True,
    }))

    apply_res = runner.invoke(rewrite_cli, [
        "--db", tmp_db, "--session", "r_apply",
        "--apply-results", str(results_dir),
    ])
    assert apply_res.exit_code == 0, apply_res.output

    state = NewsletterAgentState(session_id="r_apply", db_path=tmp_db).load_latest_from_db()
    assert state.newsletter_title == "AI Weekly Roundup"
    assert state.final_newsletter.startswith("# AI Weekly Roundup")
    assert "## OpenAI" in state.final_newsletter
```

- [ ] **Step 2: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_step_rewrite.py::test_rewrite_apply_writes_final_newsletter -v`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/test_step_rewrite.py
git commit -m "test(rewrite): apply-results writes final newsletter and title"
```

---

### Task 16: Update `skills/rewrite/SKILL.md`

**Files:**
- Modify: `skills/rewrite/SKILL.md`

- [ ] **Step 1: Replace contents**

Overwrite `skills/rewrite/SKILL.md` with:

```markdown
---
name: rewrite
description: Assemble section drafts into a whole newsletter, run a whole-newsletter critic-optimizer pass, generate the title, and store state.final_newsletter. Iterates up to --max-edits times with early exit at score >= 8.0.
---

# newsagent:rewrite

Step 11 of /newsagent:run. Two execution paths.

## Interactive mode — Sonnet subagent dispatch

The Agent runs the **critic-optimizer loop + title generation** inside its
own context. One Agent, one batch, one result file.

### Step 1: prepare batch

```bash
python -m lib.steps.rewrite --session SID --prepare-batches [--max-edits 2]
```

Writes a single `runs/<SID>/rewrite-batches/batch-000.json` with the initial
draft (concatenated section markdowns) and three pre-rendered prompts
(critique_newsletter / improve_newsletter / generate_newsletter_title) plus
`max_edits`, `accept_threshold`, and the `RewriteResult` schema.

### Step 2: dispatch one Sonnet subagent

Per-Agent config:

- `subagent_type: "general-purpose"`
- `model: "sonnet"`
- `description: "Rewrite newsletter for session SID"`
- `prompt:` instructions of the shape:

  ```
  Read runs/<SID>/rewrite-batches/batch-000.json. It contains:
    - initial_draft (concatenated section markdowns)
    - max_edits, accept_threshold
    - critique_system_prompt, critique_user_prompt (template with
      {newsletter_markdown} placeholder)
    - improve_system_prompt, improve_user_prompt (template with
      {newsletter_markdown} and {critique})
    - title_system_prompt, title_user_prompt (template with
      {newsletter_markdown})
    - output_schema: required JSON shape (RewriteResult)

  Run this loop in your own context:
    Let draft = initial_draft.
    For up to max_edits iterations:
      a. Substitute {newsletter_markdown}=draft into critique_user_prompt and
         call critique → score (float), feedback (str), accept (bool).
      b. If accept OR score >= accept_threshold: stop, mark accepted=true.
      c. Otherwise substitute {newsletter_markdown}=draft and {critique}=feedback
         into improve_user_prompt, call improve → new draft. Continue.
    Then substitute {newsletter_markdown}=final draft into title_user_prompt
    and call generate_newsletter_title → title.

  Return ONLY a JSON object matching output_schema with:
    final_newsletter_markdown (the final draft body),
    title,
    iterations, scores, feedbacks, accepted.

  Write to runs/<SID>/rewrite-results/batch-000.json using Write, then report
  the path.
  ```

### Step 3: apply result

```bash
python -m lib.steps.rewrite --session SID \
  --apply-results runs/<SID>/rewrite-results
```

Validates against `RewriteResult`, sets `state.final_newsletter = "# {title}\n\n{body}"`
and `state.newsletter_title`, writes `runs/<SID>/rewrite.json`.

### Step 4: retry on failure

If apply reports schema mismatch, re-dispatch the Sonnet Agent, re-run apply.

## Classic mode (non-interactive)

```bash
python -m lib.steps.rewrite --session SID --engine openai:gpt-4o-mini --max-edits 2
```

In-process critic loop + title call. Do not use `--engine subagent`.

## Output contract

- `state.final_newsletter = "# {title}\n\n{body}"` set.
- `state.newsletter_title` set.
- `runs/<SID>/rewrite.json` written with transcript.
- `rewrite` step marked COMPLETE.
```

- [ ] **Step 2: Commit**

```bash
git add skills/rewrite/SKILL.md
git commit -m "docs(skills): rewrite SKILL.md documents single-Sonnet interactive path"
```

---

## Phase 6 — Integration test

### Task 17: End-to-end interactive pipeline test

**Files:**
- Create: `tests/test_interactive_pipeline.py`

Drive `cluster → select → draft → rewrite` through prepare/apply mode with hand-written result JSONs (no real LLM calls). Confirms the four steps chain correctly.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_interactive_pipeline.py
"""End-to-end test of the interactive Agent-dispatch pipeline.

Drives cluster → select → draft → rewrite using --prepare-batches and
--apply-results with hand-written Agent result JSONs (no real LLM calls).
Asserts state shape after each step.
"""
import json
from collections import defaultdict
from pathlib import Path

import pytest
from click.testing import CliRunner

import lib.prompts  # register all prompts
from lib.db import init_db
from lib.state import NewsletterAgentState
from lib.steps.cluster import cli as cluster_cli
from lib.steps.select import cli as select_cli
from lib.steps.draft import cli as draft_cli
from lib.steps.rewrite import cli as rewrite_cli


@pytest.fixture
def tmp_db(tmp_path):
    db = str(tmp_path / "test.db")
    return db


def _seed_for_cluster(tmp_db, session_id):
    init_db(tmp_db)
    state = NewsletterAgentState(session_id=session_id, db_path=tmp_db)
    for step in ("init", "gather", "filter", "download", "dedupe", "summarize", "rate"):
        state.complete_step(step)
    headlines = []
    for i in range(6):
        headlines.append({
            "title": f"Headline {i}",
            "summary": f"Summary of headline {i}",
            "url": f"https://x.com/{i}",
            "rating": 0.5 + 0.1 * (i % 3),
            "embedding": [float(i % 2), float(i % 3), 0.0],
        })
    state.headline_data = headlines
    state.save_checkpoint("rate")
    return state


def test_interactive_pipeline_chains(tmp_db, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    _seed_for_cluster(tmp_db, session_id="e2e")

    # Stub cluster expensive paths
    monkeypatch.setattr("lib.steps.cluster.load_umap_reducer", lambda _: object())
    monkeypatch.setattr(
        "lib.steps.cluster.apply_umap",
        lambda embeddings, reducer: [[float(i % 2), 0.0] for i in range(len(embeddings))],
    )
    monkeypatch.setattr(
        "lib.steps.cluster.optimize_hdbscan",
        lambda reduced, n_trials: ([0, 1, 0, 1, -1, -1],
                                   {"noise_ratio": 0.33, "best_params": {}}),
    )

    # Stub select's embed_texts (used by _find_merge_candidates)
    monkeypatch.setattr(
        "lib.steps.select.embed_texts",
        lambda texts: [[0.0] * 8 for _ in texts],
    )

    runner = CliRunner()

    # --- cluster: prepare + apply ---
    r = runner.invoke(cluster_cli, ["--db", tmp_db, "--session", "e2e", "--prepare-batches"])
    assert r.exit_code == 0, r.output
    Path("runs/e2e/cluster-results").mkdir(parents=True)
    Path("runs/e2e/cluster-results/batch-000.json").write_text(json.dumps({
        "names": [
            {"cluster_id": "0", "name": "Cluster Zero"},
            {"cluster_id": "1", "name": "Cluster One"},
        ]
    }))
    r = runner.invoke(cluster_cli, [
        "--db", tmp_db, "--session", "e2e",
        "--apply-results", "runs/e2e/cluster-results",
    ])
    assert r.exit_code == 0, r.output

    state = NewsletterAgentState(session_id="e2e", db_path=tmp_db).load_latest_from_db()
    assert set(state.clusters.keys()) == {"Cluster Zero", "Cluster One"}

    # --- select: prepare + apply (assign 2 noise → one to cluster 0, drop one) ---
    r = runner.invoke(select_cli, ["--db", tmp_db, "--session", "e2e", "--prepare-batches"])
    assert r.exit_code == 0, r.output

    # Find noise headline indices from the prepared batch
    assign_batch = json.loads(Path("runs/e2e/select-assign-batches/batch-000.json").read_text())
    noise_ids = assign_batch["ids"]
    assert len(noise_ids) == 2
    Path("runs/e2e/select-assign-results").mkdir(parents=True)
    Path("runs/e2e/select-assign-results/batch-000.json").write_text(json.dumps({
        "assignments": [
            {"id": noise_ids[0], "assignment": "0"},
            {"id": noise_ids[1], "assignment": "none"},
        ]
    }))

    r = runner.invoke(select_cli, [
        "--db", tmp_db, "--session", "e2e",
        "--apply-results", "runs/e2e",
    ])
    assert r.exit_code == 0, r.output

    state = NewsletterAgentState(session_id="e2e", db_path=tmp_db).load_latest_from_db()
    # One noise headline dropped; the other absorbed into cluster 0.
    assert len(state.newsletter_section_data) > 0
    cats = {s["cat"] for s in state.newsletter_section_data}
    assert cats.issubset({"Cluster Zero", "Cluster One"})

    # --- draft: prepare + apply ---
    r = runner.invoke(draft_cli, ["--db", tmp_db, "--session", "e2e", "--prepare-batches"])
    assert r.exit_code == 0, r.output

    batch_files = sorted(Path("runs/e2e/draft-batches").glob("batch-*.json"))
    Path("runs/e2e/draft-results").mkdir(parents=True)
    for i, bf in enumerate(batch_files):
        cat = json.loads(bf.read_text())["cat"]
        Path(f"runs/e2e/draft-results/batch-{i:03d}.json").write_text(json.dumps({
            "cat": cat,
            "final_section_markdown": f"## {cat}\n- fake headline",
            "iterations": 1,
            "scores": [8.5],
            "feedbacks": ["good"],
            "accepted": True,
        }))

    r = runner.invoke(draft_cli, [
        "--db", tmp_db, "--session", "e2e",
        "--apply-results", "runs/e2e/draft-results",
    ])
    assert r.exit_code == 0, r.output

    state = NewsletterAgentState(session_id="e2e", db_path=tmp_db).load_latest_from_db()
    assert all("section_markdown" in s for s in state.newsletter_section_data)

    # --- rewrite: prepare + apply ---
    r = runner.invoke(rewrite_cli, ["--db", tmp_db, "--session", "e2e", "--prepare-batches"])
    assert r.exit_code == 0, r.output
    Path("runs/e2e/rewrite-results").mkdir(parents=True)
    Path("runs/e2e/rewrite-results/batch-000.json").write_text(json.dumps({
        "final_newsletter_markdown": "## Cluster Zero\n- fake\n\n## Cluster One\n- fake",
        "title": "End-to-end test newsletter",
        "iterations": 1,
        "scores": [9.0],
        "feedbacks": ["great"],
        "accepted": True,
    }))
    r = runner.invoke(rewrite_cli, [
        "--db", tmp_db, "--session", "e2e",
        "--apply-results", "runs/e2e/rewrite-results",
    ])
    assert r.exit_code == 0, r.output

    state = NewsletterAgentState(session_id="e2e", db_path=tmp_db).load_latest_from_db()
    assert state.newsletter_title == "End-to-end test newsletter"
    assert state.final_newsletter.startswith("# End-to-end test newsletter")
```

- [ ] **Step 2: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_interactive_pipeline.py -v`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/test_interactive_pipeline.py
git commit -m "test: end-to-end interactive pipeline (cluster→select→draft→rewrite)"
```

---

## Phase 7 — `/newsagent:run` SKILL rewrite

### Task 18: Rewrite `skills/run/SKILL.md` to drive interactive flow

**Files:**
- Modify: `skills/run/SKILL.md`

The SKILL is what parent Claude reads when the user types `/newsagent:run`. Today it just says "run `python -m lib.steps.run`". Now it must walk the workflow as a parent-Claude driver, invoking either the step's Python CLI (no-LLM steps) or driving prepare/dispatch/apply (LLM steps).

- [ ] **Step 1: Replace contents**

Overwrite `skills/run/SKILL.md` with:

```markdown
---
name: run
description: Top-level newsletter orchestrator. Drives the 12-step pipeline (init → send) end-to-end from a parent Claude Code session, dispatching parallel Agents for the seven LLM-using steps. Writes a session summary to runs/<SID>/summary.md.
---

# newsagent:run

This SKILL drives the full newsletter pipeline from a parent Claude Code
session. For no-LLM steps it invokes the step's Python CLI. For LLM-using
steps it follows the `--prepare-batches → parallel Agents → --apply-results`
pattern, dispatching all batches as parallel Agents in a single message.

For non-interactive use (cron, CI, or any context outside Claude Code), use
the Python orchestrator `lib.steps.run` directly with an `--engine` override.
See "Non-interactive fallback" below.

## Step plan

The pipeline runs these 12 steps in order:

```
init → gather → filter → download → dedupe → summarize →
rate → cluster → select → draft → rewrite → send
```

## Per-step model & batch table

| Step | Mode | Model | Batches |
|---|---|---|---|
| init | Python CLI | — | — |
| gather | Python CLI | — | — |
| filter | prepare/dispatch/apply | haiku | 25 headlines per batch |
| download | Python CLI | — | — |
| dedupe | Python CLI | — | — |
| summarize | prepare/dispatch/apply | sonnet | 15 articles per batch |
| rate | Python CLI (uses openai:gpt-4o-mini by default) | — | — |
| cluster | prepare/dispatch/apply | haiku | 1 batch (all clusters) |
| select | prepare/dispatch/apply | haiku | 25 noise per assign batch; 1 merge batch |
| draft | prepare/dispatch/apply | sonnet | 1 batch per section |
| rewrite | prepare/dispatch/apply | sonnet | 1 batch (whole newsletter) |
| send | Python CLI | — | — |

## Driver loop (what parent Claude does)

For each step in the plan:

1. **No-LLM steps** (init, gather, download, dedupe, rate, send):
   Run the Python CLI:
   ```bash
   python -m lib.steps.<step> --session SID [step-specific args]
   ```
   Abort the orchestrator on non-zero exit.

2. **LLM-using steps** (filter, summarize, cluster, select, draft, rewrite):
   ```bash
   python -m lib.steps.<step> --session SID --prepare-batches [step-specific args]
   ```
   Then dispatch ALL `runs/SID/<step>-batches/batch-*.json` files as Agents
   in a SINGLE parent message (one Agent tool call per batch file). Use the
   model from the table above for that step. Each Agent's prompt instructs
   it to read the batch file, run the step's logic in its own context, and
   write the result to `runs/SID/<step>-results/batch-NNN.json`.

   (For `select` there are two batch subdirs — `select-assign-batches/` and
   `select-merge-batches/` — dispatch Agents for both in the same message.)

   Wait for all Agents to return. Then:
   ```bash
   python -m lib.steps.<step> --session SID --apply-results runs/SID/<step>-results
   ```
   (For `select` use `--apply-results runs/SID` so it can find both result
   subdirs.)

   If apply reports problems on stderr (`missing ...`, `schema mismatch`),
   identify the failing batch numbers, re-dispatch only those Agents
   (overwriting their result files), and re-run apply.

After the `send` step completes, write `runs/SID/summary.md` (unless
`--no-summary` was passed) by calling `lib.run_summary.write_summary(SID)`.

## Per-step Agent prompt skeletons

See the individual SKILL.md files for the exact instructions each Agent
should receive:
- `skills/filter/SKILL.md`
- `skills/summarize/SKILL.md`
- `skills/cluster/SKILL.md`
- `skills/select/SKILL.md`
- `skills/draft/SKILL.md`
- `skills/rewrite/SKILL.md`

For every Agent dispatch:
- `subagent_type: "general-purpose"`
- `model:` from the table above
- `description:` brief, e.g. `"Classify headline batch 003"`
- `prompt:` skeleton from the step's SKILL.md, with the actual batch file
  path filled in

Dispatch ALL batches for a step in one parent message (multiple Agent tool
calls in the same message) so they run in parallel.

## Flags

| Flag | Default | Notes |
|---|---|---|
| `--db PATH` | `newsletter_agent.db` | SQLite DB path |
| `--sources PATH` | `sources.yaml` | YAML source list (used by init step) |
| `--session SID` | autogenerated | Session id to use or create |
| `--new` | off | Force a new session id |
| `--resume SID` | — | Resume an existing session from its first incomplete step |
| `--from STEP` | — | Start from STEP and run everything after it |
| `--only STEP` | — | Run exactly one step |
| `--max-edits N` | 2 | Critic-optimizer iterations for draft and rewrite |
| `--parallelism N` | 4 | Section drafters for classic-mode draft only |
| `--notify` | off | Send newsletter via email after the send step |
| `--no-summary` | off | Skip writing runs/<SID>/summary.md |

`--engine` from `lib.steps.run` is NOT supported in the interactive flow —
the model is fixed per step (table above). For full-pipeline engine override
use the non-interactive fallback below.

## Step plan resolution

1. `--only STEP` → run exactly that one step.
2. `--from STEP` → run STEP and every step that follows it in workflow order.
3. `--resume SID` → load state via `python -m lib.steps.status --session SID`,
   find the first non-complete step, run from there.
4. Default → run all 12 steps starting from init.

## Resuming / recovery

If a step fails mid-run, the orchestrator aborts. To recover:

```bash
python -m lib.steps.status --session SID
python -m lib.steps.reset --session SID --errors
# Then re-invoke /newsagent:run --resume SID
```

## Output

- `runs/<SID>/<step>-batches/` — prepared batch JSONs (per LLM step)
- `runs/<SID>/<step>-results/` — Agent result JSONs (per LLM step)
- `runs/<SID>/<step>.json` — per-step summary
- `runs/<SID>/summary.md` — top-level human-readable run report
- `out/YYYY-MM-DD.html` — final newsletter HTML (from send step)
- `out/latest.html` — symlink to latest

## Non-interactive fallback (cron / CI)

For automation outside Claude Code, run the Python orchestrator directly with
an `--engine` override (`subagent` is forbidden — it falls back to `claude -p`
which is not covered by the Max plan):

```bash
python -m lib.steps.run --sources sources.yaml \
  --engine openrouter:google/gemini-2.5-flash
```

This bypasses the Agent-dispatch flow entirely and uses classic mode on every
LLM step.
```

- [ ] **Step 2: Commit**

```bash
git add skills/run/SKILL.md
git commit -m "docs(skills): rewrite /newsagent:run SKILL.md to drive interactive flow"
```

---

### Task 19: Update `lib.steps.run` docstring

**Files:**
- Modify: `lib/steps/run.py:1-10`

Update the docstring at the top to mark this as the cron/CI fallback.

- [ ] **Step 1: Replace docstring**

In `lib/steps/run.py`, replace lines 1–10:

```python
"""newsagent:run — non-interactive newsletter orchestrator (cron / CI).

Sequences all 12 pipeline steps in order, supports resume/from/only flags,
and forwards engine overrides to the appropriate steps. Use this entry point
when you cannot drive parent-Claude Agent dispatch (cron, CI, headless
scripts). Requires --engine to avoid the subagent engine (which calls
claude -p and is not covered by the Max plan).

For interactive use from a parent Claude Code session, invoke /newsagent:run
(the SKILL at skills/run/SKILL.md) instead — that drives the Agent-dispatch
pattern across all LLM steps without using claude -p.

CLI: python -m lib.steps.run [--db ...] [--session SID] [--sources sources.yaml]
         [--from STEP] [--only STEP] [--resume SID]
         [--max-edits N] [--parallelism N] [--engine ENGINE]
         [--notify] [--no-summary]
"""
```

- [ ] **Step 2: Run the existing test for `lib.steps.run`**

Run: `.venv/bin/pytest tests/test_step_run.py -v`
Expected: all PASS (docstring change is non-functional).

- [ ] **Step 3: Commit**

```bash
git add lib/steps/run.py
git commit -m "docs(steps): lib.steps.run docstring marks it as cron/CI fallback"
```

---

## Phase 8 — Final verification

### Task 20: Run the full test suite and lint check

**Files:** none

- [ ] **Step 1: Run all tests**

Run: `.venv/bin/pytest tests/ -v`
Expected: all PASS. Previous run had 266 tests passing; this plan adds ~17 new tests, so expect 280+ PASS, 0 FAIL.

- [ ] **Step 2: Coverage check**

Run: `.venv/bin/pytest tests/ --cov=lib --cov-report=term`
Expected: coverage on `lib/` ≥ 88% (current baseline ~89%; new code is small and well-tested).

- [ ] **Step 3: Ensure prompts register cleanly**

Run: `.venv/bin/python -c "import lib.prompts; from lib.llm import get_prompt; \
  [print(get_prompt(n).name) for n in ['name_topic_batch','assign_noise_batch','merge_clusters_batch']]"`
Expected: prints the three new prompt names, no exceptions.

- [ ] **Step 4: Smoke-check the orchestrator help**

Run: `.venv/bin/python -m lib.steps.run --help`
Expected: docstring shows the updated "non-interactive newsletter orchestrator (cron / CI)" line.

- [ ] **Step 5: Commit any stragglers**

If anything is uncommitted at this point, identify what and commit it. Otherwise:

```bash
git status   # expect: clean
```

No commit needed if clean.

---

## Self-review notes (for plan author — do not include in execution)

This plan covers every section of the spec:

- *Per-step batch contracts* → Tasks 5, 8, 11, 14 (one prepare task per step).
- *Apply contracts* → embedded in same tasks plus dedicated apply-tests (6, 9, 12, 15).
- *Critic loop inside Agent* → schemas in Task 4; Agent-prompt instructions in SKILL.md tasks 13 and 16; prepare-side template embedding in Tasks 11 and 14.
- *Model dispatch table* → SKILL.md tasks 7, 10, 13, 16, 18.
- *`/newsagent:run` rewrite* → Task 18.
- *Error handling & retry* → covered in `_load_results` problem reporting (Tasks 5, 8, 11, 14) and SKILL.md re-dispatch instructions (Tasks 7, 10, 13, 16).
- *Integration test* → Task 17.
- *Classic mode preserved* → kept verbatim inside refactored CLIs in Tasks 5, 8, 11, 14.
- *Out of scope items* (no filter/summarize changes, no prompt rubric changes, no wave throttling, no state-schema changes) → respected.

Type consistency check: `DraftSectionResult` / `RewriteResult` defined once in `_dispatch_schemas.py` (Task 4) and referenced by `lib/steps/draft.py` (Task 11) and `lib/steps/rewrite.py` (Task 14). Batch JSON keys (`batch_id`, `session_id`, `ids`, embedded prompts, `output_schema`) match across steps. Subdirs named consistently (`<step>-batches/`, `<step>-results/`).
