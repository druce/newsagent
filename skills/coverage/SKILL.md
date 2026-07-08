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
