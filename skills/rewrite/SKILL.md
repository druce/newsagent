---
name: rewrite
description: Assemble section drafts into a whole newsletter, run a whole-newsletter critic-optimizer pass, generate the title, and store state.final_newsletter. Iterates up to --max-edits times with early exit at score >= 8.0.
---

# rewrite

**Step in pipeline:** 11 of 12 (after `draft`, before `send`)

## What it does

Assembles all section drafts into a single newsletter, runs a whole-newsletter critic-optimizer pass, generates the title, and writes the final newsletter artifact.

1. **Concatenate** all `section_markdown` values from `state.newsletter_section_data` (joined by `\n\n`).
2. **Critique + improve** the full newsletter body via the `critique_newsletter` / `improve_newsletter` prompts, up to `--max-edits` iterations. Exits early if the critic scores >= 8.0 or sets `accept=True`.
3. **Generate title** via `generate_newsletter_title` — produces a 6-12 word factual title in active voice.
4. **Write** `state.final_newsletter = "# {title}\n\n{body}"` and `state.newsletter_title = title`.
5. **Write** `runs/<SID>/rewrite.json` with the critic transcript and title.

## Invocation

```bash
python -m lib.steps.rewrite --session SID [--db PATH] [--max-edits N] [--engine ENGINE]
```

| Flag | Default | Description |
|---|---|---|
| `--session` | required | Session ID to load/save state |
| `--db` | `newsletter_agent.db` | Path to SQLite database |
| `--max-edits` | `2` | Max critic-optimizer iterations |
| `--engine` | prompt default | Override LLM engine for all prompts |

## Expected output

- `state.final_newsletter`: markdown string starting with `# <title>` followed by all section bodies.
- `state.newsletter_title`: the generated title string.
- `runs/<SID>/rewrite.json`: JSON with `title`, `transcript` (iterations, scores, feedbacks, accepted).
- Step `rewrite` marked COMPLETE in workflow state.
- Console: `Rewrite: '<title>' — N iteration(s), accepted=True/False.`

## Error cases

- **No state found for session**: session ID not in DB — run prior steps first.
- **Empty newsletter_section_data**: concatenation produces an empty string; the critic loop runs on an empty draft.
- **LLM engine failure**: propagated as exit code 1. Re-run after fixing engine config.

## Artifacts written

| Path | Contents |
|---|---|
| `runs/<SID>/rewrite.json` | Critic transcript + title |
| `newsletter_agent.db` | Updated `agent_state` row at step `rewrite` |

## State fields updated

| Field | Value |
|---|---|
| `state.final_newsletter` | `# <title>\n\n<body>` (full newsletter markdown) |
| `state.newsletter_title` | Title string (6-12 words) |
