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

## Next step

`newsagent:select` — MMR-diverse top-K per cluster with LLM noise assignment and cluster merging.
