# Phase 4 — `cluster` + `select` (UMAP/HDBSCAN + MMR + LLM Noise Assignment & Merge)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Group articles into topical clusters (`news:cluster`) and pick a diverse top-K per cluster for newsletter sections (`news:select`). After Phase 4 the pipeline runs `init → gather → filter → download → summarize → rate → cluster → select → (skip draft/rewrite) → send`.

**Architecture:**
- `news:cluster`: builds a short-summary text per headline, embeds (reuses `lib/embeddings.py`), applies the pretrained `umap_reducer.pkl` (legacy artifact), runs Optuna-tuned HDBSCAN, names each cluster via `NAME_TOPIC` prompt. Writes `cluster_id` + `cluster_name` onto each headline; also writes `state.clusters` map.
- `news:select`: (1) LLM-assigns HDBSCAN noise points (cluster_id=-1) to existing clusters or a new cluster, (2) LLM-merges near-duplicate clusters, (3) runs **MMR** (maximal marginal relevance) on each cluster to pick top-K headlines balancing rating + embedding diversity. Writes `state.newsletter_section_data`.

**Hard constraints (from memory):**
- No Anthropic API.
- Reuse OpenAI embeddings from Phase 3 (already attached to headlines by `dedupe`).
- PromptConfig binds to model + reasoning_effort per legacy style.

**Tech additions:** `umap-learn>=0.5`, `hdbscan>=0.8`, `optuna>=3.6`, `scikit-learn>=1.5`.

**Reference (read, don't import):**
- `~/projects/OpenAIAgentsSDK/do_cluster.py` — clustering math
- `~/projects/OpenAIAgentsSDK/prompts.py:1365` — `NAME_TOPIC`
- `~/projects/OpenAIAgentsSDK/umap_reducer.pkl` — 396 MB pretrained reducer

## File structure (new files)

| Path | Purpose |
|---|---|
| `umap_reducer.pkl` (root) | Copied from legacy; matches `text-embedding-3-large` 3072-dim inputs |
| `lib/clustering.py` | UMAP transform + Optuna-tuned HDBSCAN |
| `lib/mmr.py` | Maximal Marginal Relevance diversity selection |
| `lib/prompts/name_topic.py` | Port of `NAME_TOPIC` |
| `lib/prompts/assign_noise.py` | NEW: noise-point → existing cluster |
| `lib/prompts/merge_clusters.py` | NEW: should-merge yes/no |
| `lib/steps/cluster.py` | news:cluster CLI |
| `lib/steps/select.py` | news:select CLI |
| `skills/cluster/SKILL.md`, `skills/select/SKILL.md` | Agent-facing contracts |
| `tests/test_clustering.py` |  |
| `tests/test_mmr.py` |  |
| `tests/test_prompt_name_topic.py` |  |
| `tests/test_prompt_assign_noise.py` |  |
| `tests/test_prompt_merge_clusters.py` |  |
| `tests/test_step_cluster.py` |  |
| `tests/test_step_select.py` |  |

---

## Task 1: Copy `umap_reducer.pkl` from legacy

**Files:**
- Copy: `~/projects/OpenAIAgentsSDK/umap_reducer.pkl` → `./umap_reducer.pkl`

- [ ] **Step 1: Copy + verify**

```bash
cp ~/projects/OpenAIAgentsSDK/umap_reducer.pkl /Users/drucev/projects/news_agent/umap_reducer.pkl
ls -lh /Users/drucev/projects/news_agent/umap_reducer.pkl
# Expect 396M
```

`.gitignore` already excludes `*.pkl` so this won't be committed (intended).

- [ ] **Step 2: Sanity load**

```bash
cd /Users/drucev/projects/news_agent
.venv/bin/python -c "
import pickle
with open('umap_reducer.pkl', 'rb') as f:
    r = pickle.load(f)
print('UMAP reducer loaded; n_components =', r.n_components)
"
```
Expected: prints n_components (typically 690).

If loading fails because `umap-learn` isn't installed yet, that's fine — Task 2 installs it.

- [ ] **Step 3: No commit** — the pickle is gitignored. Mention in the next commit.

---

## Task 2: Add clustering dependencies

**Files:**
- Modify: `pyproject.toml`, `requirements.txt`

- [ ] **Step 1: Update deps**

In `pyproject.toml` `dependencies`:
```toml
  "umap-learn>=0.5",
  "hdbscan>=0.8",
  "optuna>=3.6",
  "scikit-learn>=1.5",
```

Mirror into `requirements.txt`.

- [ ] **Step 2: Install + verify**

```bash
cd /Users/drucev/projects/news_agent
.venv/bin/pip install -e ".[dev]"
.venv/bin/python -c "import umap, hdbscan, optuna, sklearn; print('OK')"
```

If `hdbscan` install fails on macOS (it sometimes needs `LDFLAGS`/`CFLAGS`), document the workaround and continue.

- [ ] **Step 3: Sanity load with umap-learn installed**

```bash
.venv/bin/python -c "
import pickle
with open('umap_reducer.pkl', 'rb') as f:
    r = pickle.load(f)
print('loaded UMAP, n_components =', r.n_components)
"
```

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml requirements.txt
git commit -m "chore: add umap-learn, hdbscan, optuna, scikit-learn"
```

---

## Task 3: Port `NAME_TOPIC` prompt

**Files:**
- Create: `lib/prompts/name_topic.py`
- Modify: `lib/prompts/__init__.py`
- Create: `tests/test_prompt_name_topic.py`

Verbatim text from `~/projects/OpenAIAgentsSDK/prompts.py:1365-1379`.

Schema:
- Input: `{entities: str, headlines: str}` (both pre-joined strings — keeps the prompt template simple)
- Output: `{title: str}` (single section title, 4–8 words)

PromptConfig:
- `default_engine="subagent"`, `reasoning_effort=3` (matches legacy)

- [ ] **Step 1: Tests**

Three tests in `tests/test_prompt_name_topic.py`:
- registered, default engine set, reasoning_effort=3
- input schema accepts the two string fields
- system prompt mentions key phrases ("section title", "headlines")

Use the same `_ensure_registered` fixture pattern as `test_prompt_extract_summaries.py`.

- [ ] **Step 2: Implement**

```python
# lib/prompts/name_topic.py
"""name_topic — name an article cluster.

Ported from ~/projects/OpenAIAgentsSDK/prompts.py:1365 (NAME_TOPIC).
"""
from __future__ import annotations

from pydantic import BaseModel

from lib.llm import PromptConfig, register_prompt


class NameTopicInput(BaseModel):
    entities: str
    headlines: str


class NameTopicOutput(BaseModel):
    title: str


_SYSTEM = """\
You are a newsletter editor naming a topic section. You will receive the central entities and a sample of today's headlines for a news cluster. Write a concise, descriptive section title (4-8 words) that captures the main story or theme. Do not use generic phrases like \"AI News\" or \"Tech Update\". Return only the title, nothing else."""

_USER = """\
Central entities: {entities}

Sample headlines:
{headlines}

Section title:"""


NAME_TOPIC = PromptConfig(
    name="name_topic",
    system_prompt=_SYSTEM,
    user_prompt=_USER,
    input_schema=NameTopicInput,
    output_schema=NameTopicOutput,
    default_engine="subagent",
    reasoning_effort=3,
)

register_prompt(NAME_TOPIC)
```

Update `lib/prompts/__init__.py` to import `name_topic`.

- [ ] **Step 3: Tests, commit**

```bash
.venv/bin/pytest tests/test_prompt_name_topic.py -v
git add lib/prompts/name_topic.py lib/prompts/__init__.py tests/test_prompt_name_topic.py
git commit -m "feat(prompts): port NAME_TOPIC for cluster naming"
```

---

## Task 4: `lib/clustering.py` — UMAP transform + Optuna-tuned HDBSCAN

**Files:**
- Create: `lib/clustering.py`
- Create: `tests/test_clustering.py`

API (kept narrow):
```python
def load_umap_reducer(path: str = "umap_reducer.pkl") -> Any:
    """Load the pickled UMAP reducer. Raises FileNotFoundError if missing."""

def apply_umap(embeddings: list[list[float]], reducer) -> np.ndarray:
    """Apply the reducer to a list of full-dimension embeddings, return reduced 2D array."""

def optimize_hdbscan(reduced: np.ndarray, n_trials: int = 30,
                     timeout: int | None = None) -> tuple[np.ndarray, dict]:
    """Run Optuna over HDBSCAN hyperparameters. Return (cluster_labels, metrics_dict)."""

def cluster_quality_metrics(embeddings: np.ndarray, labels: np.ndarray) -> dict:
    """Compute silhouette, n_clusters, noise_ratio, etc."""
```

Reference legacy `do_cluster.py:165-280` (`calculate_clustering_metrics`) and `:430-548` (`optimize_hdbscan`). Drop legacy verbosity (Optuna logging, IPython display) — just return the labels + metrics.

**Test strategy:** synthetic embeddings (3 well-separated Gaussian blobs in 2-3 dims, no UMAP). Verify HDBSCAN produces ~3 clusters with low noise. UMAP loading test reads the actual pickle if it exists (skip if missing).

- [ ] **Step 1: Tests**

```python
# tests/test_clustering.py
import numpy as np
import pytest
from pathlib import Path
from lib.clustering import (
    optimize_hdbscan, cluster_quality_metrics, apply_umap, load_umap_reducer,
)


def _three_blobs(n_per_blob: int = 8, seed: int = 42) -> np.ndarray:
    rng = np.random.default_rng(seed)
    blobs = [
        rng.normal(loc=[0, 0], scale=0.05, size=(n_per_blob, 2)),
        rng.normal(loc=[5, 0], scale=0.05, size=(n_per_blob, 2)),
        rng.normal(loc=[0, 5], scale=0.05, size=(n_per_blob, 2)),
    ]
    return np.vstack(blobs)


def test_optimize_hdbscan_finds_three_clusters():
    X = _three_blobs()
    labels, metrics = optimize_hdbscan(X, n_trials=10)
    n_clusters = len(set(labels)) - (1 if -1 in labels else 0)
    assert 2 <= n_clusters <= 4
    assert metrics.get("noise_ratio", 1.0) < 0.5


def test_cluster_quality_metrics_keys():
    X = _three_blobs()
    labels = np.array([0]*8 + [1]*8 + [2]*8)
    m = cluster_quality_metrics(X, labels)
    assert "n_clusters" in m
    assert "noise_ratio" in m
    assert m["n_clusters"] == 3
    assert m["noise_ratio"] == 0.0


def test_optimize_hdbscan_empty_returns_no_clusters():
    X = np.zeros((1, 2))
    labels, _ = optimize_hdbscan(X, n_trials=2)
    # Single point can't cluster
    assert len(labels) == 1


@pytest.mark.skipif(not Path("umap_reducer.pkl").exists(),
                    reason="umap_reducer.pkl not present")
def test_load_umap_reducer_succeeds():
    reducer = load_umap_reducer("umap_reducer.pkl")
    assert hasattr(reducer, "n_components")
    assert reducer.n_components > 0


def test_load_umap_reducer_missing_raises():
    with pytest.raises(FileNotFoundError):
        load_umap_reducer("nonexistent.pkl")
```

- [ ] **Step 2: Implement `lib/clustering.py`**

```python
"""UMAP + Optuna-tuned HDBSCAN clustering.

Math ported from ~/projects/OpenAIAgentsSDK/do_cluster.py with verbosity dropped.
Pretrained UMAP reducer expected at ./umap_reducer.pkl (3072-dim → ~690-dim).
"""
from __future__ import annotations

import logging
import pickle
from pathlib import Path
from typing import Any, Optional

import hdbscan
import numpy as np
import optuna
from sklearn.metrics import silhouette_score

# Suppress Optuna's INFO logging — we don't need its spam
optuna.logging.set_verbosity(optuna.logging.WARNING)
logging.getLogger("hdbscan").setLevel(logging.WARNING)

_RANDOM_STATE = 42


def load_umap_reducer(path: str = "umap_reducer.pkl"):
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"UMAP reducer not found at {path}")
    with p.open("rb") as f:
        return pickle.load(f)


def apply_umap(embeddings: list[list[float]], reducer) -> np.ndarray:
    M = np.asarray(embeddings, dtype=np.float32)
    return reducer.transform(M).astype(np.float64)


def cluster_quality_metrics(embeddings: np.ndarray, labels: np.ndarray) -> dict:
    n = len(labels)
    unique = set(labels.tolist())
    noise_count = int(np.sum(labels == -1))
    n_clusters = len(unique) - (1 if -1 in unique else 0)
    noise_ratio = noise_count / max(n, 1)

    silhouette = float("nan")
    if n_clusters >= 2 and noise_ratio < 1.0:
        mask = labels != -1
        if mask.sum() >= 2 and len(set(labels[mask].tolist())) >= 2:
            try:
                silhouette = float(silhouette_score(embeddings[mask], labels[mask]))
            except Exception:
                silhouette = float("nan")

    return {
        "n_clusters": n_clusters,
        "noise_ratio": noise_ratio,
        "silhouette": silhouette,
    }


def optimize_hdbscan(
    reduced: np.ndarray,
    n_trials: int = 30,
    timeout: Optional[int] = None,
) -> tuple[np.ndarray, dict]:
    if len(reduced) < 3:
        # Not enough points; return all-noise.
        return np.full(len(reduced), -1), {"n_clusters": 0, "noise_ratio": 1.0, "silhouette": float("nan")}

    def objective(trial: optuna.Trial) -> float:
        min_cluster_size = trial.suggest_int("min_cluster_size", 2, max(3, len(reduced) // 4))
        min_samples = trial.suggest_int("min_samples", 1, min_cluster_size)
        clusterer = hdbscan.HDBSCAN(
            min_cluster_size=min_cluster_size,
            min_samples=min_samples,
            metric="euclidean",
            cluster_selection_method="eom",
        )
        labels = clusterer.fit_predict(reduced)
        metrics = cluster_quality_metrics(reduced, labels)
        n_clusters = metrics["n_clusters"]
        if n_clusters < 2 or metrics["noise_ratio"] > 0.8:
            return -1.0
        # Composite: more clusters good (up to ~10), low noise good, high silhouette good
        sil = metrics["silhouette"] if not np.isnan(metrics["silhouette"]) else 0.0
        score = (
            0.4 * sil
            + 0.3 * (1.0 - metrics["noise_ratio"])
            + 0.3 * min(n_clusters / 10.0, 1.0)
        )
        return -score

    study = optuna.create_study(
        direction="minimize",
        sampler=optuna.samplers.TPESampler(seed=_RANDOM_STATE),
    )
    study.optimize(objective, n_trials=n_trials, timeout=timeout)

    best_params = study.best_params if study.best_trial is not None else {
        "min_cluster_size": 3, "min_samples": 2,
    }
    clusterer = hdbscan.HDBSCAN(
        min_cluster_size=best_params["min_cluster_size"],
        min_samples=best_params["min_samples"],
        metric="euclidean",
        cluster_selection_method="eom",
    )
    labels = clusterer.fit_predict(reduced)
    metrics = cluster_quality_metrics(reduced, labels)
    metrics["best_params"] = best_params
    return labels, metrics
```

- [ ] **Step 3: Tests, commit**

```bash
.venv/bin/pytest tests/test_clustering.py -v
# Expect 5 passed (or 4 if umap_reducer.pkl is missing)
git add lib/clustering.py tests/test_clustering.py
git commit -m "feat(clustering): UMAP + Optuna-tuned HDBSCAN"
```

---

## Task 5: `news:cluster` step + SKILL.md

**Files:**
- Create: `lib/steps/cluster.py`
- Create: `skills/cluster/SKILL.md`
- Create: `tests/test_step_cluster.py`

Logic:
1. Load state. Require headlines with both `summary` and `embedding`.
2. If embeddings are missing on any headlines (e.g. dedupe was skipped), embed them now via `embed_texts`.
3. Stack embeddings, load reducer, apply UMAP, run `optimize_hdbscan` (default `--n-trials=30`).
4. Assign `cluster_id` to each headline.
5. For each non-noise cluster, sample top 5 headlines by rating, call `call_prompt("name_topic", {...})` to get a section title. Store as `cluster_name` on each headline AND in `state.clusters[name] = [headline_ids...]`.
6. Write per-cluster metrics to `runs/<SID>/cluster.json`.

Tests: mock `optimize_hdbscan` and `call_prompt` so we don't need a real UMAP reducer.

- [ ] **Step 1: Tests** (3-4 tests, similar to other step tests)
- [ ] **Step 2: Implement**
- [ ] **Step 3: SKILL.md**
- [ ] **Step 4: Tests, commit**

```bash
.venv/bin/pytest tests/test_step_cluster.py -v
git add lib/steps/cluster.py skills/cluster/SKILL.md tests/test_step_cluster.py
git commit -m "feat(steps): news:cluster UMAP+HDBSCAN+NAME_TOPIC"
```

---

## Task 6: NEW prompt — `ASSIGN_NOISE`

**Files:**
- Create: `lib/prompts/assign_noise.py`
- Create: `tests/test_prompt_assign_noise.py`
- Modify: `lib/prompts/__init__.py`

Design from scratch — no legacy equivalent.

Input: `{headline: {title, summary}, clusters: [{id, name, sample_headlines}], allow_new: bool}`
Output: `{assignment: str}` where `assignment` is either an existing `cluster.id` string OR `"new"` OR `"none"` (truly unrelated).

`default_engine="subagent"`, `reasoning_effort=3`.

System prompt (write fresh):
```
You are an editor assigning an unclustered news headline to one of the day's news clusters.

You will receive:
- One headline (title + summary)
- A list of existing clusters with names and sample headlines

For each headline, decide:
- The id of the best-matching existing cluster, OR
- "new" if the headline is significantly different from all existing clusters but plausibly worth its own section
- "none" if the headline is unrelated to the newsletter's topic

Return only a JSON object matching the provided schema with field "assignment".
```

User prompt: paste headline + cluster list as JSON. Schemas:
```python
class ClusterDescriptor(BaseModel):
    id: str
    name: str
    sample_headlines: List[str]

class AssignNoiseInput(BaseModel):
    headline_title: str
    headline_summary: str
    clusters: List[ClusterDescriptor]
    allow_new: bool = True

    @computed_field
    @property
    def clusters_json(self) -> str:
        return json.dumps([c.model_dump() for c in self.clusters])

class AssignNoiseOutput(BaseModel):
    assignment: str  # cluster id, "new", or "none"
```

- [ ] **Step 1: Tests** — registered, default engine, schema accepts valid input, system prompt mentions "cluster" + "assign"
- [ ] **Step 2: Implement**
- [ ] **Step 3: Tests, commit `feat(prompts): NEW assign_noise for noise-point cluster assignment`**

---

## Task 7: NEW prompt — `MERGE_CLUSTERS`

**Files:**
- Create: `lib/prompts/merge_clusters.py`
- Create: `tests/test_prompt_merge_clusters.py`
- Modify: `lib/prompts/__init__.py`

Input:
```python
class ClusterPair(BaseModel):
    id: str
    name: str
    top_headlines: List[str]  # 3-5

class MergeClustersInput(BaseModel):
    a: ClusterPair
    b: ClusterPair
```

Output:
```python
class MergeClustersOutput(BaseModel):
    merge: bool
    merged_name: str | None  # if merge=True, the better name for the combined cluster
```

`default_engine="subagent"`, `reasoning_effort=4`.

System prompt:
```
You are an editor deciding whether two news clusters cover the same story and should be merged into one newsletter section.

You will receive two clusters, each with a name and 3-5 representative headlines.

Decide:
- merge=true if both clusters cover substantively the same story or theme. Provide merged_name (the better of the two names, or a new title that better captures both).
- merge=false if they cover distinct stories that deserve separate sections.

Return only a JSON object matching the provided schema.
```

- [ ] **Step 1: Tests** — registered, schema accepts two ClusterPair inputs
- [ ] **Step 2: Implement**
- [ ] **Step 3: Tests, commit `feat(prompts): NEW merge_clusters for cluster deduplication`**

---

## Task 8: `lib/mmr.py` — MMR diversity

**Files:**
- Create: `lib/mmr.py`
- Create: `tests/test_mmr.py`

Standard MMR algorithm:
```
Given: items with embeddings + relevance scores, λ ∈ [0,1], k
Initialize: selected = []
While len(selected) < k:
  best_score, best_idx = -inf, None
  for i in candidates not in selected:
    sim_to_selected = max(cosine(item[i], item[j]) for j in selected) if selected else 0
    score = λ * relevance[i] - (1-λ) * sim_to_selected
    if score > best_score: ...
  selected.append(best_idx)
Return selected indices.
```

API:
```python
def mmr_select(
    embeddings: list[list[float]] | np.ndarray,
    relevance: list[float] | np.ndarray,
    k: int,
    lambda_: float = 0.7,
) -> list[int]:
    """Return indices of MMR-selected items."""
```

- [ ] **Step 1: Tests** — three tests:
  - Returns k indices when k < n
  - Returns all indices when k >= n
  - λ=1.0 returns top-k by relevance (no diversity)
  - λ=0.0 selects most diverse (lowest similarity to selected)
  - Empty input returns []

- [ ] **Step 2: Implement** (pure numpy; ~30 LOC)

- [ ] **Step 3: Tests, commit `feat: MMR diversity selection`**

---

## Task 9: `news:select` step + SKILL.md

**Files:**
- Create: `lib/steps/select.py`
- Create: `skills/select/SKILL.md`
- Create: `tests/test_step_select.py`

Logic:
1. Load state. Require `cluster_id` on each headline (from `news:cluster`).
2. **Assign noise points:** for each headline with `cluster_id == -1`, build a snapshot of existing clusters (id, name, 3 sample headlines), call `call_prompt("assign_noise", ...)`. If `assignment` matches an existing cluster id, set headline's `cluster_id` + `cluster_name`. If `"new"`, create a new cluster (single-member; will probably get filtered in MMR). If `"none"`, drop the headline from `headline_data`.
3. **Merge near-duplicate clusters:** for each pair of clusters with similar names (e.g. embedding cosine sim ≥ 0.85 on their names), call `call_prompt("merge_clusters", ...)`. If `merge=true`, reassign all headlines from cluster B to cluster A (keeping `merged_name`).
4. **MMR selection:** for each surviving cluster, run `mmr_select(embeddings, ratings, k=K_PER_CLUSTER, lambda_=0.7)` to pick the top headlines. Default `K_PER_CLUSTER=5` (override via `--k`).
5. Write `state.newsletter_section_data = [{cat: cluster_name, headline, link, rating, ...}, ...]`.
6. Mark `select` complete, save checkpoint, write `runs/<SID>/select.json` with cluster summary.

Tests: 3-4 tests — mock `call_prompt` for both new prompts. Verify noise assignment, merge, and MMR top-K.

- [ ] **Step 1: Tests**
- [ ] **Step 2: Implement**
- [ ] **Step 3: SKILL.md**
- [ ] **Step 4: Tests, commit `feat(steps): news:select MMR + LLM noise-assign + cluster-merge`**

---

## Task 10: End-to-end Phase 4 verification

**Files:** none modified.

- [ ] **Step 1: Full pytest + coverage**

```bash
cd /Users/drucev/projects/news_agent
.venv/bin/pytest tests/ --cov=lib --cov-report=term
```
Expected: all pass. Coverage on new modules ≥ 75%.

- [ ] **Step 2: Engine resolution sanity**

```bash
.venv/bin/python -c "
import lib.prompts
from lib.llm import get_prompt
for name in ('name_topic', 'assign_noise', 'merge_clusters'):
    cfg = get_prompt(name)
    print(f'{name:<20} default={cfg.default_engine:<30} effort={cfg.reasoning_effort}')
"
```

- [ ] **Step 3: Tag**

```bash
git tag phase-4-complete
git log --oneline phase-3-complete..phase-4-complete | head -20
```

---

## Notes for the implementer

- **`umap_reducer.pkl` was fit with `text-embedding-3-large` (3072 dims).** Don't substitute another embedding model — the reducer won't accept the wrong dimensions.
- **`numpy` and `scipy` are already installed** from Phase 3.
- **HDBSCAN install can be fussy on macOS.** If it fails, try `LDFLAGS="-L/opt/homebrew/lib" CFLAGS="-I/opt/homebrew/include" pip install hdbscan` or wait for a binary wheel.
- **Don't port the legacy Optuna verbosity.** Set `optuna.logging.set_verbosity(WARNING)` once and move on.
- **Pure-numpy MMR is preferred** — don't pull in another retrieval lib.
- **`select` step is where the new design shines.** The legacy did plain top-K-by-rating. The new step adds LLM-assisted noise assignment + cluster merge + MMR. Don't shortcut these — they're the reason this phase exists.

## Out of scope for Phase 4

- `draft` + `rewrite` steps (Phase 5)
- `/news:run` orchestrator (Phase 6)
- Bluesky pipeline (Phase 7)
- Tuning the cluster-merge similarity threshold via embeddings (use a static threshold for v1)
