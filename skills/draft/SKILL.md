---
name: draft
description: Draft markdown newsletter sections in parallel with a critic-optimizer loop. For each topic cluster, writes a section via write_section, then iteratively critiques and improves it (max-edits N, early exit at score >= 8.0).
---

# newsagent:draft

Step 10 of /newsagent:run. Two execution paths.

## Interactive mode — Sonnet subagent dispatch

The Agent runs the **entire critic-optimizer loop** inside its own context:
write → critique → if accept or score>=8.0 stop, else improve → next iteration,
up to `max_edits` iterations. Parent Claude just dispatches one Agent per
section in parallel and applies results when they're all back.

### Step 1: prepare batches

```bash
python -m lib.steps.draft --session SID --prepare-batches [--max-edits 2]
```

Writes one self-contained batch per section under
`runs/<SID>/draft-batches/batch-NNN.json`. Each batch contains:
- `cat`, `stories`
- `max_edits`, `accept_threshold`
- Three pre-rendered prompts: `write_*`, `critique_*`, `improve_*`
- `output_schema` (`DraftSectionResult`)

### Step 2: dispatch one Sonnet subagent per batch (all in ONE message)

Per-Agent config:

- `subagent_type: "general-purpose"`
- `model: "sonnet"`
- `description: "Draft section <cat>"`
- `prompt:` instructions of the shape:

  ```
  Read runs/<SID>/draft-batches/batch-NNN.json. It contains:
    - stories: the source material
    - max_edits, accept_threshold
    - write_system_prompt, write_user_prompt (already rendered, ready to send)
    - critique_system_prompt, critique_user_prompt (template; you will substitute
      {section_markdown} and {critique} as needed)
    - improve_system_prompt, improve_user_prompt (template; same)
    - output_schema: required JSON shape (DraftSectionResult)

  Run this loop in your own context:
    1. Call write: system_prompt=write_system_prompt, user_prompt=write_user_prompt
       → initial section markdown.
    2. For up to max_edits iterations:
       a. Substitute {section_markdown} into critique_user_prompt and call critique
          (system_prompt=critique_system_prompt). Read score (float) and accept (bool).
       b. If accept is true OR score >= accept_threshold: stop, record this iteration,
          mark accepted=true.
       c. Otherwise substitute {section_markdown} + {critique} into improve_user_prompt
          and call improve → new section markdown. Continue.

  Return ONLY a JSON object matching output_schema with:
    cat, final_section_markdown, iterations, scores, feedbacks, accepted.

  Write that JSON object to:
    runs/<SID>/draft-results/batch-NNN.json
  using the Write tool.

  Then validate by running:
    .venv/bin/python tools/check_batch.py runs/<SID>/draft-results/batch-NNN.json
  If it prints "FAIL", fix the issues and re-Write. If it prints "OK",
  report the path.

  IMPORTANT — the write/critique/improve loop runs ENTIRELY in your own
  thinking, not via tool calls. Produce each section draft, score, and
  feedback by reasoning directly. Do NOT spawn subprocesses, make HTTP
  calls, or invoke any LLM tool to "call write/critique/improve" — those
  are just labels for the steps you reason through.

  Use ONLY these tools: Read (once, for the batch file), Write (once, for
  the result file — or once more if check_batch reports FAIL), and the
  Bash invocation of `.venv/bin/python tools/check_batch.py`. Do not use
  sed, grep, inline python -c, or any other shell command. Do not read
  any other files (no lib/prompts/, no other batches, no other results).
  The batch file is fully self-contained.
  ```

Dispatch all N section Agents in a single parent message so they run in
parallel.

### Step 3: apply results

```bash
python -m lib.steps.draft --session SID \
  --apply-results runs/<SID>/draft-results
```

Validates each result file against `DraftSectionResult`, writes
`{cat, section_markdown}` entries into `state.newsletter_section_data`,
writes `runs/<SID>/draft.json` with the per-section transcripts.

### Step 4: retry on failure

If apply reports `missing sections: [...]` or `schema mismatch`:
re-dispatch only the failing section Agent(s), re-run apply.

## Classic mode (non-interactive)

```bash
python -m lib.steps.draft --session SID --engine openai:gpt-4o-mini \
  --max-edits 2 --parallelism 4
```

In-process `ThreadPoolExecutor` runs the critic loop via
`critic_optimizer_loop`. Do not use `--engine subagent` — it falls back to
`claude -p`.

## Output contract

- `state.newsletter_section_data` replaced with `[{cat, section_markdown}, ...]`.
- `runs/<SID>/draft.json` written with per-section transcript metadata.
- `draft` step marked COMPLETE.
