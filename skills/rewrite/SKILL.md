---
name: rewrite
description: Assemble section drafts into a whole newsletter, run a whole-newsletter critic-optimizer pass, generate the title, and store state.final_newsletter. Iterates up to --max-edits times with early exit at score >= 8.0.
---

# newsagent:rewrite

Step 11 of /newsagent:pipeline. Two execution paths.

## Interactive mode — Sonnet subagent dispatch

The Agent runs the **critic-optimizer loop + title generation** inside its
own context. One Agent, one batch, one result file.

### Step 1: prepare batch

```bash
python -m lib.steps.rewrite --session SID --prepare-batches [--max-edits 2]
```

Writes a single `runs/<SID>/rewrite-batches/batch-000.json` with the initial
draft (concatenated section markdowns) and three pre-rendered prompts
(critique_newsletter / improve_newsletter / generate_newsletter_title) plus
`max_edits`, `accept_threshold`, and the `RewriteResult` schema.

### Step 2: dispatch one Sonnet subagent

Per-Agent config:

- `subagent_type: "general-purpose"`
- `model: "sonnet"`
- `description: "Rewrite newsletter for session SID"`
- `prompt:` instructions of the shape:

  ```
  Read runs/<SID>/rewrite-batches/batch-000.json. It contains:
    - initial_draft (concatenated section markdowns)
    - max_edits, accept_threshold
    - critique_system_prompt, critique_user_prompt (template with
      {newsletter_markdown} placeholder)
    - improve_system_prompt, improve_user_prompt (template with
      {newsletter_markdown} and {critique})
    - title_system_prompt, title_user_prompt (template with
      {newsletter_markdown})
    - output_schema: required JSON shape (RewriteResult)

  Run this loop in your own context:
    Let draft = initial_draft.
    For up to max_edits iterations:
      a. Substitute {newsletter_markdown}=draft into critique_user_prompt and
         call critique → score (float), feedback (str), accept (bool).
      b. If accept OR score >= accept_threshold: stop, mark accepted=true.
      c. Otherwise substitute {newsletter_markdown}=draft and {critique}=feedback
         into improve_user_prompt, call improve → new draft. Continue.
    Then substitute {newsletter_markdown}=final draft into title_user_prompt
    and call generate_newsletter_title → title.

  Return ONLY a JSON object matching output_schema with:
    final_newsletter_markdown (the final draft body),
    title,
    iterations, scores, feedbacks, accepted.

  Write to runs/<SID>/rewrite-results/batch-000.json using Write, then report
  the path.
  ```

### Step 3: apply result

```bash
python -m lib.steps.rewrite --session SID \
  --apply-results runs/<SID>/rewrite-results
```

Validates against `RewriteResult`, sets `state.final_newsletter = "# {title}\n\n{body}"`
and `state.newsletter_title`, writes `runs/<SID>/rewrite.json`.

### Step 4: retry on failure

If apply reports schema mismatch, re-dispatch the Sonnet Agent, re-run apply.

## Classic mode (non-interactive)

```bash
python -m lib.steps.rewrite --session SID --engine openai:gpt-4o-mini --max-edits 2
```

In-process critic loop + title call. Do not use `--engine subagent`.

## Output contract

- `state.final_newsletter = "# {title}\n\n{body}"` set.
- `state.newsletter_title` set.
- `runs/<SID>/rewrite.json` written with transcript.
- `rewrite` step marked COMPLETE.
- **Auto-send:** at the end of both `--apply-results` and classic mode, rewrite
  invokes `lib.steps.send.deliver_newsletter(...)` which renders
  `out/<date>.html`, inserts a row in `newsletters`, and emails the result via
  Gmail (defaults to `GMAIL_USER`). Pass `--no-email` to suppress the send,
  `--to ADDR` to override the recipient. The send step (step 12) is idempotent
  and will not re-email when invoked again for the same session.
