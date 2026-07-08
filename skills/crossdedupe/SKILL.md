---
name: crossdedupe
description: Drop stories already published in the last N days (cross-day, story-level de-duplication). Runs after summarize, before rate. Embeds title+short_summary with OpenAI text-embedding-3-large, shortlists candidates against the published_articles store by cosine, then a Haiku judge confirms same/different per pair. Interactive mode dispatches parallel Haiku subagents (25 pairs per batch); classic mode runs the same_story prompt through one engine. Catches the same event reported by a different outlet on a different day (Yahoo-today / Fortune-yesterday) that in-session dedupe never sees.
---

# newsagent:crossdedupe

Step after `summarize` (before `rate`) in /newsagent:pipeline. Unlike
`dedupe` — which removes syndicated reprints *within one run* by full-body
cosine ≥ 0.95 — this step removes stories the newsletter **already published in
the last few days**, even when today's arrival is a different outlet at a
different URL with different prose (which never approaches the syndication
threshold).

**Signal:** OpenAI `text-embedding-3-large` of `title + short_summary`. Today's
candidates are compared against the `published_articles` store (articles that
survived into a final newsletter, recorded at `send`). A permissive cosine
shortlist (`--shortlist-threshold`, default 0.70) picks candidate/prior pairs; a
Haiku judge (`same_story` prompt) makes the real same/different call. Only
confirmed duplicates are dropped from `state.headline_data`.

Prompt source of truth: `lib/prompts/same_story.py`. Two execution paths, same
prompt:

1. **Interactive** — parent Claude dispatches parallel Haiku subagents, one per
   batch of 25 pairs.
2. **Classic** — one LLM engine processes batches via `lib.llm.call_prompt`
   (cron / CI).

**Common case: zero batches.** Most days no candidate clears the shortlist, so
`--prepare-batches` writes **no** batch files. That is normal — dispatch nothing
and go straight to `--apply-results` (a no-op that completes the step).

## Interactive mode — Haiku subagent dispatch

### Step 1: prepare batches

```bash
python -m lib.steps.crossdedupe --session SID --prepare-batches \
  [--lookback-days 4] [--shortlist-threshold 0.70] [--batch-size 25]
```

Embeds candidate `title+short_summary`, loads the last `--lookback-days` of
`published_articles`, and writes one self-contained prompt file per batch of
shortlisted pairs: `runs/<SID>/crossdedupe-batches/batch-NNN.json` containing
`{batch_id, ids, pairs, system_prompt, user_prompt, output_schema}`. Each pair
holds the candidate (`a_title`, `a_short_summary`) and the matched prior
(`b_title`, `b_short_summary`, `b_url`). If it prints "No cross-day duplicate
candidates to check.", skip to Step 3.

### Step 2: dispatch parallel Haiku subagents

For each `runs/<SID>/crossdedupe-batches/batch-NNN.json` printed by prepare,
dispatch an Agent in parallel (single message, multiple Agent tool calls):

- `subagent_type: "general-purpose"`
- `model: "haiku"`
- `description: "Judge cross-day dup batch NNN"`
- `prompt:` of the shape:

  ```
  Read runs/<SID>/crossdedupe-batches/batch-NNN.json. It contains:
    - system_prompt: same-story judging guidance
    - user_prompt: pre-rendered task with the JSON pairs inline
    - ids: list of pair ids you MUST judge
    - output_schema: required JSON shape ({"results":[{"id","same"}]})

  Follow system_prompt + user_prompt. For each pair decide whether A (today's
  candidate) and B (an already-published story) report the SAME underlying news
  event. Return ONLY a JSON object matching output_schema, with exactly one
  entry per id (no duplicates, no extras).
  Write that JSON object to:
    runs/<SID>/crossdedupe-results/batch-NNN.json
  using the Write tool.

  Then validate by running:
    .venv/bin/python tools/check_batch.py runs/<SID>/crossdedupe-results/batch-NNN.json
  If it prints "FAIL", fix the issues and re-Write. If it prints "OK", report
  the path you wrote.

  Use ONLY these tools: Read, Write, and the Bash invocation of
  `.venv/bin/python tools/check_batch.py`. Do not use sed, grep, inline
  python -c, or any other shell command.
  ```

Run all batches in parallel — they are independent.

### Step 3: apply results

```bash
python -m lib.steps.crossdedupe --session SID \
  --apply-results runs/<SID>/crossdedupe-results
```

Reads each `batch-*.json` (validated against `SameStoryOutput`), drops every
candidate confirmed `same=True` from `state.headline_data`, marks the step
complete, and writes `runs/<SID>/crossdedupe.json` with `considered / dropped`
counts. Safe to run when no batches were prepared (completes as a no-op).

### Step 4: retry on failure

If apply reports `missing verdicts for ids: [...]` or a schema mismatch,
re-dispatch only the failing batch(es) (overwrite the result file) and re-run
`--apply-results`.

## Classic mode (non-interactive)

```bash
python -m lib.steps.crossdedupe --session SID [--engine ENGINE] \
  [--lookback-days 4] [--shortlist-threshold 0.70] [--batch-size 25]
```

Routes the `same_story` prompt through one engine (default
`google:gemini-3.1-flash-lite`). Override via `--engine` or
`NEWS_PROMPT_SAME_STORY_ENGINE`.

## Output contract

- Duplicate headlines dropped from `state.headline_data` before `rate`.
- `runs/<SID>/crossdedupe.json` written with `considered / dropped` counts.
- `crossdedupe` step marked COMPLETE.
- Depends on the `published_articles` store, which `send` populates from each
  delivered newsletter — protection is live once at least one prior newsletter
  has been sent within the lookback window.
