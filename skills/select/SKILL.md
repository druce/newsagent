---
name: select
description: Mine HDBSCAN noise for new clusters, consolidate the full cluster-name list, reassign every headline to a final section (or "Other"), and run global MMR top-K (default 100). Three sequential subagent dispatch rounds + one apply step.
---

# newsagent:select

Step 9 of /newsagent:pipeline. Replaces the prior noise-assign + per-cluster MMR
flow with a three-round consolidation pipeline that produces a flat
8–18 named sections + `Other`, with exactly K items globally.

## Phases

| Phase | What | Batch shape | Subagent |
|---|---|---|---|
| A | Repechage: mine HDBSCAN noise for new themes | 50 noise items / batch | Sonnet |
| B | Consolidate: unify HDBSCAN + repechage names | 1 batch (all clusters) | Sonnet |
| C | Reassign: every headline → final name or "Other" | 25 headlines / batch | Haiku |
| D | Global MMR top-K (default 100, λ=0.7) | in-process math | — |
| E | Build sections from MMR survivors | in-process | — |

Phases A→C are sequentially dependent (B reads A's results; C reads B's
result), so the interactive flow is **three separate prepare/dispatch
rounds** plus one apply step.

## Interactive mode — three rounds then apply

### Round 1: repechage

```bash
python -m lib.steps.select --session SID --prepare-repechage
```

Writes `runs/<SID>/select-repechage-batches/batch-NNN.json` — each batch
holds ≤50 noise headlines (`id`, `title`, `short_summary`) plus the
self-contained `system_prompt`, `user_prompt`, and `output_schema` for
`extract_noise_clusters`.

Dispatch one Sonnet subagent per batch (all in parallel in ONE parent
message). Each subagent must:

```
Read runs/<SID>/select-repechage-batches/batch-NNN.json.
Follow system_prompt + user_prompt. Return ONLY a JSON object matching
output_schema (proposed_clusters[] of 2+ member_ids each, plus
unclustered_ids — every input id appears exactly once across both).
Write to runs/<SID>/select-repechage-results/batch-NNN.json using Write.
Then validate: .venv/bin/python tools/check_batch.py runs/<SID>/select-repechage-results/batch-NNN.json
If FAIL, fix and re-Write. If OK, report the path.
Use ONLY: Read, Write, and the validator Bash invocation.
```

### Round 2: consolidate

```bash
python -m lib.steps.select --session SID --prepare-consolidate
```

Reads `select-repechage-results/`, merges with HDBSCAN clusters (carried in
state from the cluster step), and writes a single
`runs/<SID>/select-consolidate-batches/batch-000.json` for
`consolidate_cluster_names`.

Dispatch **one** Sonnet subagent. Same skeleton as round 1 but writes to
`runs/<SID>/select-consolidate-results/batch-000.json`. The validator
handles the `{final_names, mapping}` shape automatically.

### Round 3: reassign

```bash
python -m lib.steps.select --session SID --prepare-reassign
```

Reads the consolidate result, builds final cluster choices, and writes
`runs/<SID>/select-reassign-batches/batch-NNN.json` — every rated headline
sharded 25 / batch with the same `clusters` choice list.

Dispatch one Haiku subagent per batch (all in parallel). Each writes to
`runs/<SID>/select-reassign-results/batch-NNN.json`. Validator works on the
standard `{id, assignment}` shape.

### Apply

```bash
python -m lib.steps.select --session SID --apply-results runs/<SID>
```

Loads reassign results, applies cluster_name to every headline (unmapped or
"Other" → routed to the `Other` bucket), runs global MMR top-K (default
100, override with `--k`), populates `state.newsletter_section_data` +
`state.clusters`, writes `runs/<SID>/select.json`.

## Classic mode (non-interactive)

```bash
python -m lib.steps.select --session SID --engine subagent
```

Runs all three LLM phases in-process via `lib.llm.call_prompt`, then
MMR/finalize. Useful for cron/CI when no parent Claude is dispatching.

## CLI flags

| Flag | Default | Meaning |
|---|---|---|
| `--k` | 100 | Global MMR top-K |
| `--lambda` | 0.7 | MMR relevance/diversity trade-off |
| `--repechage-batch-size` | 50 | Noise headlines per phase-A batch |
| `--reassign-batch-size` | 25 | Headlines per phase-C batch |
| `--engine` | (unset) | Classic mode engine override |
| `--prepare-repechage` | — | Phase A only |
| `--prepare-consolidate` | — | Phase B only |
| `--prepare-reassign` | — | Phase C only |
| `--apply-results DIR` | — | Phase D+E only |

The three prepare flags are mutually exclusive with each other and with
`--apply-results`.

## Output contract

- Every headline gets a `cluster_name` (one of the consolidated final names
  or the literal string `"Other"`).
- `state.newsletter_section_data` populated with exactly `--k` items
  (top by global MMR; default 100).
- `state.clusters` populated with `{section_name: [headline_index_str, ...]}`
  using only the survivors of MMR.
- `runs/<SID>/select.json` written with `n_selected`, per-section counts,
  `n_final_names`, K, λ.
- `select` step marked COMPLETE.

## Retry on failure

Per the filter/summarize pattern: re-dispatch only the failing batch(es)
and re-run the corresponding `--prepare-*` or `--apply-results` step. Each
phase is idempotent — overwriting a result file is safe.
