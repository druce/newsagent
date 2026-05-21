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
