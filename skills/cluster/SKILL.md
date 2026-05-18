# news:cluster

Group summarized headlines into topical clusters using UMAP dimensionality reduction, Optuna-tuned HDBSCAN, and LLM-based cluster naming.

## What it does

1. Loads the newsletter agent state for the given session.
2. Filters to headlines that have a `summary` field set (from `news:summarize`).
3. For any headlines missing an `embedding`, embeds them now (title + summary concatenated) using `lib/embeddings.py` (OpenAI `text-embedding-3-large`).
4. Applies the pretrained UMAP reducer (`umap_reducer.pkl`) to project embeddings to a lower-dimensional space.
5. Runs Optuna-tuned HDBSCAN to find the optimal hyperparameters and assigns a `cluster_id` to each headline (-1 = noise).
6. For each non-noise cluster, samples the top-5 headlines by rating and calls the `name_topic` prompt to generate a descriptive section title.
7. Writes `cluster_id` and `cluster_name` onto each headline in `state.headline_data`.
8. Populates `state.clusters = {cluster_name: [url, ...]}`.
9. Marks the `cluster` workflow step complete and saves a checkpoint.
10. Writes `runs/<SID>/cluster.json` with per-cluster metrics and names.

## Prerequisites

- `news:summarize` must be complete (headlines need `summary`).
- `umap_reducer.pkl` must exist at the project root (pretrained on `text-embedding-3-large` 3072-dim embeddings).
- `news:rate` should ideally be complete so headlines have `rating` for top-5 sampling; falls back to order-of-appearance.

## Invocation

```bash
python -m lib.steps.cluster --session <SID>
python -m lib.steps.cluster --session <SID> --n-trials 50
python -m lib.steps.cluster --session <SID> --umap-path /path/to/umap_reducer.pkl
```

## Options

| Flag | Default | Description |
|---|---|---|
| `--session` | required | Session ID |
| `--db` | `newsletter_agent.db` | SQLite DB path |
| `--n-trials` | `30` | Optuna trials for HDBSCAN hyperparameter search |
| `--umap-path` | `umap_reducer.pkl` | Path to pretrained UMAP reducer pickle |

## State changes

- `state.headline_data[i]["cluster_id"]` — int, -1 for noise
- `state.headline_data[i]["cluster_name"]` — str, absent on noise points
- `state.clusters` — `{cluster_name: [url, ...]}` for all non-noise clusters
- Workflow step `cluster` marked COMPLETE

## Artifacts

- `runs/<SID>/cluster.json` — cluster metrics, names, sample titles

## Next step

`news:select` — MMR-diverse top-K per cluster with LLM noise assignment and cluster merging.
