---
name: summarize
description: For each downloaded headline, produce both a bullet-point article summary AND a one-line headline-style short_summary (<=25 words) via the extract_summaries prompt. Reads article text from text_path, skips headlines without a downloaded text file, and writes `summary` and `short_summary` back to session state.
---

# newsagent:summarize

Step 6 of /newsagent:run (after `dedupe`, before `rate`). Two execution paths:

1. **Interactive** — use when running through Claude Code: parent Claude
   dispatches **all batches at once** as Sonnet subagents, one per batch of
   15 articles (typically 10–25 batches per run). This is the preferred path
   because it avoids `claude -p` (which is not covered under the Max plan).
2. **Classic** — use when running via cron / CI / any non-Claude-Code context:
   a single LLM engine processes batches sequentially through `lib.llm.call_prompt`.
   Requires `--engine` set to a non-`subagent` engine (e.g. `openai:gpt-4o-mini`
   or `openrouter:google/gemini-2.5-flash`) — `subagent` engine burns API tokens
   not covered by the Max plan.

Both paths use the same prompt — its single source of truth is
`lib/prompts/extract_summaries.py` (`EXTRACT_SUMMARIES = PromptConfig(...)`).
To change the summary rubric, edit that file; both paths pick it up automatically.

## Interactive mode — Sonnet subagent dispatch

### Step 1: prepare batches

```bash
python -m lib.steps.summarize --session SID --prepare-batches [--batch-size 15]
```

Writes one self-contained prompt file per batch:
`runs/<SID>/summarize-batches/batch-NNN.json` containing
`{batch_id, ids, items, system_prompt, user_prompt, output_schema}`. The
`system_prompt` and `user_prompt` are rendered from `EXTRACT_SUMMARIES` at
this point — subagents read only the batch file and need no further access
to `lib/prompts/`.

Each batch holds up to 15 articles. Each item carries `id`, `title`, and up
to 20,000 chars of trafilatura-extracted article body.

### Step 2: dispatch all batches as parallel Sonnet subagents in a single wave

For each `runs/<SID>/summarize-batches/batch-NNN.json`, dispatch one Agent
tool call — **all in a single message, all at once**. With typical batch
counts (10–25 per run) we just fan out everything. No wave throttling.

If we ever see API-side throttling or output-corruption issues on a large
run, drop back to waves of ~10 and revisit; but for current scale (≤25
batches, JSON output ≤5K tokens each), the parent-context cost and
API-side risk are both well within bounds.

Per-Agent-call config:

- `subagent_type: "general-purpose"`
- `model: "sonnet"`
- `description: "Summarize article batch NNN"`
- `prompt:` instructions of the shape:

  ```
  Read runs/<SID>/summarize-batches/batch-NNN.json. It contains:
    - system_prompt: summarization guidance
    - user_prompt: pre-rendered task with the JSON items inline
    - ids: list of article ids you MUST produce a summary for
    - output_schema: required JSON shape (ExtractSummariesOutput)

  Follow system_prompt + user_prompt. Return ONLY a JSON object matching
  output_schema, with exactly one entry per id (no duplicates, no extras).
  Each entry has BOTH a `short_summary` (one headline-style sentence,
  <=25 words, sentence case) AND a `summary` (3 bullets) per the
  system_prompt rules.

  Write that JSON object to:
    runs/<SID>/summarize-results/batch-NNN.json
  using the Write tool.

  Then validate by running:
    .venv/bin/python tools/check_batch.py runs/<SID>/summarize-results/batch-NNN.json
  If it prints "FAIL", fix the issues (missing ids, extras, duplicates, or
  short_summaries over 25 words) and re-Write. If it prints "OK", report
  the path you wrote.

  Use ONLY these tools: Read, Write, and the Bash invocation of
  `.venv/bin/python tools/check_batch.py`. Do not use sed, grep, inline
  python -c, or any other shell command — the validator covers everything
  you need to verify.
  ```

**Dispatch shape (orchestrator side):**

Send a single message containing N Agent tool calls (one per batch JSON,
typically 10–25). They run in parallel automatically. Wait for all to
return, then run apply-results.

Failures or schema misses surface at apply time and the affected batches can
be re-dispatched individually without redoing the rest.

### Step 3: apply results

```bash
python -m lib.steps.summarize --session SID \
  --apply-results runs/<SID>/summarize-results
```

Validates each `batch-*.json` against `ExtractSummariesOutput`, writes
`summary` back onto matching headlines in `state.headline_data`, marks the
step complete, writes `runs/<SID>/summarize.json`.

### Step 4: retry on failure

If apply reports `missing summaries for ids: [...]` or
`{batch_NN}: schema mismatch`:

1. Identify the failing batch(es) from the error output.
2. Re-dispatch only those Sonnet subagents (overwrite the bad result file).
3. Re-run `--apply-results`.

Apply exits non-zero only if **no** summaries were usable; partial results
are still applied so retries are additive.

## Classic mode (non-interactive)

```bash
python -m lib.steps.summarize --session SID --engine openai:gpt-4o-mini \
  [--batch-size 15]
```

Routes the `extract_summaries` prompt through one engine, batches processed
sequentially. **Do not use** `--engine subagent` here — it falls back to
`claude -p` which costs API tokens outside the Max plan. The other engines
(`openrouter:*`, `openai:*`, `google:*`) require their respective API keys.

## Output contract

- `state.headline_data[i].summary` set for every successfully summarized headline (multi-bullet article summary).
- `state.headline_data[i].short_summary` set for every successfully summarized headline (one headline-style sentence, ≤25 words, sentence case — used by downstream draft/rewrite and by the post-rate email).
- Headlines without `text_path` or that already had a `summary` are skipped (idempotent re-runs are safe).
- `runs/<SID>/summarize.json` written with `total / summarized` counts.
- `summarize` step marked COMPLETE.

## Error cases

- **No state found for session**: session ID not in DB — run `newsagent:init` first.
- **Unreadable text_path**: that article is silently skipped (the download step's failure surfaces in `runs/<SID>/download.json`).
- **Empty pending list**: step completes immediately with "nothing to summarize" — no batches written, no LLM calls made.
