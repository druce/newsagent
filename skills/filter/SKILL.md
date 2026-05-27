---
name: filter
description: Classify each gathered headline as AI-related. Interactive mode dispatches parallel Haiku subagents (default 25 headlines per batch). Non-interactive mode runs the filter_urls prompt through a single LLM engine. Default drops non-AI headlines from state and updates urls.isAI for cross-session dedup.
---

# newsagent:filter

Step 3 of /newsagent:pipeline. Two execution paths:

1. **Interactive** — use when running through Claude Code: parent Claude
   dispatches N parallel Haiku subagents, one per batch of 25 headlines.
   Fast + cheap + parallel (no per-call API latency for the parent).
2. **Classic** — use when running via cron / CI / any non-Claude-Code context:
   a single LLM engine processes batches sequentially through `lib.llm.call_prompt`.

Both paths use the same prompt — its single source of truth is
`lib/prompts/filter_urls.py` (`FILTER_URLS = PromptConfig(...)`). To change
the AI-relevance rubric, edit that file; both paths pick it up automatically.

## Interactive mode — Haiku subagent dispatch

### Step 1: prepare batches

```bash
python -m lib.steps.filter --session SID --prepare-batches [--batch-size 25]
```

Writes one self-contained prompt file per batch:
`runs/<SID>/filter-batches/batch-NNN.json` containing `{batch_id, ids, items,
system_prompt, user_prompt, output_schema}`. The `system_prompt` and
`user_prompt` are rendered from `FILTER_URLS` at this point — subagents read
only the batch file and need no further access to `lib/prompts/`.

### Step 2: dispatch parallel Haiku subagents

For each `runs/<SID>/filter-batches/batch-NNN.json`, dispatch an Agent in
parallel (single message, multiple Agent tool calls) with:

- `subagent_type: "general-purpose"`
- `model: "haiku"`
- `description: "Classify headline batch NNN"`
- `prompt:` instructions of the shape:

  ```
  Read runs/<SID>/filter-batches/batch-NNN.json. It contains:
    - system_prompt: classifier guidance
    - user_prompt: pre-rendered task with the JSON items inline
    - ids: list of headline ids you MUST classify
    - output_schema: required JSON shape

  Follow system_prompt + user_prompt. Return ONLY a JSON object matching
  output_schema, with exactly one entry per id (no duplicates, no extras).
  Write that JSON object to:
    runs/<SID>/filter-results/batch-NNN.json
  using the Write tool.

  Then validate by running:
    .venv/bin/python tools/check_batch.py runs/<SID>/filter-results/batch-NNN.json
  If it prints "FAIL", fix the issues (missing ids, extras, duplicates) and
  re-Write. If it prints "OK", report the path you wrote.

  Use ONLY these tools: Read, Write, and the Bash invocation of
  `.venv/bin/python tools/check_batch.py`. Do not use sed, grep, inline
  python -c, or any other shell command — the validator covers everything
  you need to verify.
  ```

Run all batches in parallel — they are independent. Failures or schema misses
will be flagged at apply time and can be re-dispatched.

### Step 3: apply results

```bash
python -m lib.steps.filter --session SID \
  --apply-results runs/<SID>/filter-results [--keep-non-ai]
```

Validates each `batch-*.json` against `FilterUrlsOutput`, applies `is_ai` to
state, updates `urls.isAI`, marks the step complete, writes
`runs/<SID>/filter.json`.

### Step 4: retry on failure

If apply reports `missing classifications for ids: [...]` or
`{batch_NN}: schema mismatch`:

1. Identify the failing batch(es) from the error output.
2. Re-dispatch only those Haiku subagents (overwrite the bad result file).
3. Re-run `--apply-results`.

Apply exits non-zero only if **no** classifications were usable; partial
results are still applied so retries are additive.

## Classic mode (non-interactive)

```bash
python -m lib.steps.filter --session SID [--engine ENGINE] [--keep-non-ai] [--batch-size 25]
```

Routes the `filter_urls` prompt through one engine. Defaults to
`subagent` (claude -p). Overrides via `--engine` or
`NEWS_PROMPT_FILTER_URLS_ENGINE` env var. Supports:

- `openrouter:<model>` — e.g. `openrouter:anthropic/claude-haiku-4.5`
- `openai:<model>` — e.g. `openai:gpt-4o-mini`
- `google:<model>` — e.g. `google:gemini-2.5-flash`
- `subagent` — default; `claude -p` via the user's Claude Code subscription

## Output contract

- `state.headline_data[i].is_ai` set for every classified headline.
- Non-AI headlines dropped from state unless `--keep-non-ai`.
- `urls.isAI` updated in the DB for cross-session dedup.
- `runs/<SID>/filter.json` written with `total / ai / kept` counts.
- `filter` step marked COMPLETE.
