# Agent-Dispatch Conversion for cluster, select, draft, rewrite

**Date:** 2026-05-21
**Status:** Design — awaiting user approval before plan
**Scope:** Convert the four pipeline steps that currently default to the `subagent` engine (claude -p) over to the parallel Agent-dispatch pattern already used by `filter` (Haiku) and `summarize` (Sonnet). Rewrite `/newsagent:run` so a parent Claude Code session can drive the whole pipeline interactively without `claude -p`. Preserve classic `--engine` mode for cron/CI.

## Motivation

Per `feedback_no_claude_p.md`, the `subagent` engine subprocesses `claude -p`, which is not covered by the user's Max plan. Today four steps still default to `subagent`:

| Step | Subagent-default prompts |
|---|---|
| `cluster` | `name_topic` |
| `select` | `assign_noise`, `merge_clusters` |
| `draft` | `write_section`, `critique_section`, `improve_section` |
| `rewrite` | `critique_newsletter`, `improve_newsletter`, `generate_title` |

`filter` and `summarize` already solve this problem with the **prepare-batches → dispatch parallel Agents → apply-results** pattern (`skills/filter/SKILL.md`, `skills/summarize/SKILL.md`). This spec generalizes that pattern to the remaining four steps and rewrites the `/newsagent:run` orchestrator so parent Claude Code can drive the whole pipeline end-to-end.

## Architecture

Every converted step gains the same three-mode shape as `filter`/`summarize`:

1. **`--prepare-batches`** — writes self-contained batch JSON files under `runs/<SID>/<step>-batches/batch-NNN.json`. Each file embeds the pre-rendered `system_prompt`, `user_prompt`, item ids, and `output_schema` (Pydantic JSON Schema). Subagents need no further access to `lib/prompts/`.
2. **Parent-driven Agent dispatch** — parent Claude Code reads the batch files, dispatches one Agent per file in a single message (all in parallel), each Agent writes its result to `runs/<SID>/<step>-results/batch-NNN.json`.
3. **`--apply-results DIR`** — validates each result file against the Pydantic output schema, applies usable batches to state, marks the step COMPLETE, writes `runs/<SID>/<step>.json`.

Classic mode (`--engine ENGINE` with in-process `call_prompt`) is preserved on every step for cron/CI use. The four steps already have `--engine` flags; only `cluster` needs one added.

`lib.critic.critic_optimizer_loop` is **not called inside the step CLI in interactive mode for `draft`/`rewrite`**. The entire write→critique→improve loop runs inside each Agent's own context. The Agent reads the batch file, executes the three prompts in sequence up to `max_edits`, short-circuits on `accept=True` or `score >= 8.0`, and writes the final draft plus transcript. Classic mode still uses the in-process helper.

`/newsagent:run` SKILL.md is rewritten to walk the workflow as a parent-Claude driver. For each step, it either invokes the step's Python CLI (no-LLM steps) or drives `--prepare-batches → parallel Agents → --apply-results` (LLM steps). `lib.steps.run` (the Python orchestrator) is retained as the non-interactive cron/CI entry point.

## Per-Step Batch Contracts

### `cluster` — Haiku, `name_topic` only

UMAP + HDBSCAN + embeddings still run **inside `--prepare-batches`** (they produce the cluster ids that become inputs to the Agent). `--apply-results` only writes names back.

- **Prepare:** writes a single `runs/<SID>/cluster-batches/batch-000.json`:
  ```json
  {
    "batch_id": 0,
    "session_id": "<SID>",
    "clusters": [
      {"cluster_id": "0", "entities": "...", "headlines": "..."},
      ...
    ],
    "system_prompt": "<rendered from NAME_TOPIC>",
    "user_prompt": "<rendered>",
    "output_schema": <NameTopicBatchOutput JSON schema>
  }
  ```
  Skipped if no non-noise clusters exist (step completes immediately).
- **Agent:** one Haiku Agent reads the batch, returns `{names: [{cluster_id, name}]}`, writes to `runs/<SID>/cluster-results/batch-000.json`.
- **Apply:** assigns `cluster_name` to each headline, populates `state.clusters`, writes `runs/<SID>/cluster.json` with per-cluster metrics.

Note: `name_topic` prompt currently takes a single cluster's `entities` + `headlines`. For batch mode, either (a) add a new `NAME_TOPIC_BATCH` prompt variant that handles a list of clusters in one call, or (b) keep the existing prompt and have the Agent run it sequentially for each cluster in its batch. Option (a) is preferred: one prompt round-trip per Agent, simpler Agent instructions.

### `select` — Haiku, two call-sites, two batch subdirs

- **Prepare:** writes two sets of batch files:
  - `runs/<SID>/select-assign-batches/batch-NNN.json` — noise headlines (`cluster_id == -1`) sharded **25 per batch**. Each batch carries the same `clusters` descriptor (id, name, sample headlines) plus the headlines to assign. Output schema: `AssignNoiseBatchOutput {assignments: [{id, assignment}]}` where `assignment ∈ {"none", "new", "<cluster_id>"}`.
  - `runs/<SID>/select-merge-batches/batch-000.json` — all candidate near-duplicate pairs (post-cosine-filter; embedding work runs in prepare). One batch for all pairs. Output schema: `MergeClustersBatchOutput {decisions: [{pair_id, merge, merged_name}]}`.
- **Agents:** assign-noise Agents dispatch in parallel (one per batch, Haiku). Merge-clusters Agent dispatches as a single Haiku Agent. All can be dispatched in the same parent message.
- **Apply:** reads both result subdirs, applies noise assignments first (`none` → drop, `new` → new singleton cluster, `<cid>` → assign), then applies merge decisions, then runs MMR top-K per surviving cluster (pure-numpy, in-process). Writes `state.newsletter_section_data` + `state.clusters` + `runs/<SID>/select.json`.

### `draft` — Sonnet, full critic loop inside each Agent

- **Prepare:** groups `state.newsletter_section_data` by `cat`. Writes one batch per section: `runs/<SID>/draft-batches/batch-NNN.json`:
  ```json
  {
    "batch_id": N,
    "cat": "<section_name>",
    "stories": [...],
    "max_edits": 2,
    "write_system_prompt": "...", "write_user_prompt": "...",
    "critique_system_prompt": "...", "critique_user_prompt": "...",
    "improve_system_prompt": "...", "improve_user_prompt": "...",
    "output_schema": <DraftSectionResult JSON schema>
  }
  ```
- **Agent:** one Sonnet Agent per section runs the critic loop internally:
  1. Render `write_user_prompt` with the stories. Call write → initial draft.
  2. Loop up to `max_edits` times: critique → if `accept=True` or `score >= 8.0` stop, else improve → next iteration.
  3. Write `runs/<SID>/draft-results/batch-NNN.json` with `{cat, final_section_markdown, iterations, scores, feedbacks, accepted}`.
- **Apply:** writes `state.newsletter_section_data = [{cat, section_markdown}]` and `runs/<SID>/draft.json` with transcripts. Sections without a result file (e.g. Agent failure) are flagged as problems; partial apply preserves successful sections.

### `rewrite` — Sonnet, single Agent, full critic loop + title

- **Prepare:** writes one `runs/<SID>/rewrite-batches/batch-000.json` with the concatenated draft, all three prompts (critique/improve/title) pre-rendered, `max_edits`, output schema `{final_newsletter_markdown, title, iterations, scores, feedbacks, accepted}`.
- **Agent:** one Sonnet Agent runs the critic loop, then the title prompt. Writes a single result file.
- **Apply:** writes back `state.final_newsletter = "# {title}\n\n{body}"`, `state.newsletter_title`, plus `runs/<SID>/rewrite.json`.

## Model Dispatch Table

Codified in `/newsagent:run` SKILL.md:

| Step | Model | Batches |
|---|---|---|
| filter | haiku | 25 headlines per batch |
| summarize | sonnet | 15 articles per batch |
| cluster | haiku | 1 batch (all clusters) |
| select | haiku | 25 noise headlines per assign batch; 1 batch for all merge pairs |
| draft | sonnet | 1 batch per section |
| rewrite | sonnet | 1 batch (whole newsletter) |

## `/newsagent:run` SKILL Rewrite

The skill is rewritten to drive the workflow from a parent Claude Code session. Skeleton:

```
For each step in [init, gather, filter, download, dedupe, summarize, rate,
                  cluster, select, draft, rewrite, send]:
  case step in {init, gather, download, dedupe, rate, send}:
    Bash: python -m lib.steps.<step> --session SID [...]
  case step in {filter, summarize, cluster, select, draft, rewrite}:
    Bash: python -m lib.steps.<step> --session SID --prepare-batches
    Dispatch ALL batch-NNN.json files as parallel Agents in ONE message
      (model per step from the table above)
    Bash: python -m lib.steps.<step> --session SID \
            --apply-results runs/SID/<step>-results
    If apply reports problems: re-dispatch failing batches, re-apply.
  Abort and report if any step errors.
After send: write runs/SID/summary.md (or skip if --no-summary).
```

The skill keeps the existing flag surface (resume/from/only/session/max-edits/parallelism). `--engine` is honored only when the user explicitly invokes `lib.steps.run` (the cron/CI path); inside the SKILL flow, `--engine` is ignored — Agent dispatch is the path.

`lib.steps.run` retains its current behavior with a docstring/help update: "non-interactive only; for the Agent-dispatch path use `/newsagent:run`."

## Error Handling & Retry

Three failure surfaces per step:

1. **Batch JSON invalid / schema mismatch.** `--apply-results` collects all such errors, applies any usable batches, then exits non-zero only if **zero** batches were usable. Partial application is idempotent (batches keyed by ids/cluster_ids). Re-dispatch loop in the SKILL: identify failing batch numbers from stderr, dispatch only those Agents again, re-run apply.

2. **Missing ids in results.** Detected by `expected_ids - seen_ids` and `seen_ids - expected_ids` (same pattern as filter/summarize). Same partial-apply + re-dispatch recovery.

3. **Step CLI crash mid-prepare or mid-apply.** Checkpoints in `agent_state` are written at `start_step` and `complete_step`. On crash before `complete_step`, the step is left in `IN_PROGRESS`. The SKILL surfaces the exception and aborts. User runs `/newsagent:reset --session SID --errors`, then re-invokes `/newsagent:run --resume SID`.

**Critic-loop-specific:** if an Agent finishes with `iterations == max_edits` and `accepted == False`, that is **not a failure** — apply still accepts the final draft (matches today's `critic_optimizer_loop` semantics). The transcript captures `accepted=False` for observability.

**Determinism guards in apply mode:**
- All Pydantic schemas live in `lib/prompts/*.py`.
- `--prepare-batches` embeds `output_schema = <Model>.model_json_schema()` inside each batch file.
- Result files validated with `<OutputModel>.model_validate(raw)` — same exact-shape contract as filter/summarize.
- Ids used as batch keys are stringified consistently (`str(int)`).

## Testing

TDD discipline per implementation task. Per-step test additions, matching existing `tests/test_step_filter.py` and `tests/test_step_summarize.py`:

- `tests/test_step_cluster.py` — extend with `--prepare-batches` (asserts batch file shape, schema embedded, cluster ids preserved) and `--apply-results` (assigns cluster_name, populates `state.clusters`, handles missing cluster_ids).
- `tests/test_step_select.py` — extend with `--prepare-batches` (both assign-noise and merge-pairs subdirs with correct sharding) and `--apply-results` (noise assignments + merge decisions + MMR).
- `tests/test_step_draft.py` — extend with `--prepare-batches` (one batch per section with all three prompts pre-rendered + schema) and `--apply-results` (reads transcripts, updates `newsletter_section_data`).
- `tests/test_step_rewrite.py` — extend with `--prepare-batches` (single batch with critique/improve/title prompts) and `--apply-results` (sets `state.final_newsletter` + `state.newsletter_title`).

Classic-mode tests are preserved — the in-process `call_prompt` path is unchanged.

**Integration test:** `tests/test_interactive_pipeline.py` drives `cluster → select → draft → rewrite` in prepare/apply mode with hand-written result JSONs (no real LLM calls), asserting state shape end-to-end.

Coverage target: maintain ~89% on `lib/`.

**SKILL.md** is not unit-tested. The spec includes a manual smoke-test checklist: run `/newsagent:run --resume <SID>` on a session that has reached `cluster`, confirm each prepare/dispatch/apply transition works.

## Out of Scope

- Migrating `filter` or `summarize` (already converted).
- Changing prompts' content/rubrics — only adding `*_BATCH` variants where needed for cluster/select.
- Replacing the existing `subagent` engine implementation. It stays in the codebase; new steps just don't use it by default.
- Wave throttling. Per `feedback_no_claude_p.md`, dispatch all batches at once. If a real run surfaces problems, revisit.
- Changing `state` schema or `agent_state` checkpoint format.

## Implementation Order (sketch — full plan goes to writing-plans)

1. Add `*_BATCH` prompt variants where required (cluster's `NAME_TOPIC_BATCH`, select's `ASSIGN_NOISE_BATCH` and `MERGE_CLUSTERS_BATCH`). Output schemas + Pydantic models. Tests.
2. `cluster` — add prepare/apply modes. Tests. Keep classic mode.
3. `select` — add prepare/apply modes (two subdirs). Tests. Keep classic mode.
4. `draft` — add prepare/apply modes (full critic loop in Agent prompt). Tests. Keep classic mode.
5. `rewrite` — add prepare/apply modes. Tests. Keep classic mode.
6. Integration test for the four-step interactive chain.
7. Rewrite `/newsagent:run` SKILL.md to drive the parent-Claude interactive flow.
8. Smoke-test on a real session.
