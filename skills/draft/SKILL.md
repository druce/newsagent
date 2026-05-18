# news:draft

**Name:** `news:draft`
**Step in pipeline:** 9 of 11 (after `select`, before `rewrite`)

## What it does

For each topic cluster in `state.newsletter_section_data`, drafts a markdown newsletter section using a parallel critic-optimizer loop:

1. **Group** `newsletter_section_data` rows by `cat` (cluster name).
2. **Write** each section via the `write_section` prompt: inputs are the section title and the list of selected stories (headline, URL, summary, source hostname, rating).
3. **Critique + improve** each section via the `critique_section` / `improve_section` prompts, up to `--max-edits` iterations. Exits early if the critic scores >= 8.0 or sets `accept=True`.
4. **Parallel dispatch**: all sections run concurrently via `ThreadPoolExecutor(max_workers=--parallelism)`.
5. **Replaces** `state.newsletter_section_data` with one row per cluster: `{cat, section_markdown}`.
6. **Writes** `runs/<SID>/draft.json` with per-section transcript (iterations, scores, feedbacks, accepted).

## Invocation

```bash
python -m lib.steps.draft --session SID [--db PATH] [--max-edits N] [--parallelism K] [--engine ENGINE]
```

| Flag | Default | Description |
|---|---|---|
| `--session` | required | Session ID to load/save state |
| `--db` | `newsletter_agent.db` | Path to SQLite database |
| `--max-edits` | `2` | Max critic-optimizer iterations per section |
| `--parallelism` | `4` | Concurrent section drafters |
| `--engine` | prompt default | Override LLM engine for all prompts |

## Expected output

- `state.newsletter_section_data` becomes a list of `{cat: str, section_markdown: str}` dicts (one per cluster).
- `runs/<SID>/draft.json` exists and contains `sections` array with transcript fields.
- Step `draft` marked COMPLETE in workflow state.
- Console: `Draft: N sections (max_edits=M, parallelism=K).`

## Error cases

- **No state found for session**: session ID not in DB — run `news:init` first.
- **LLM engine failure**: propagated as exit code 1. Re-run after fixing engine config.
- **Empty newsletter_section_data**: step runs with zero sections; no LLM calls made.

## Artifacts written

| Path | Contents |
|---|---|
| `runs/<SID>/draft.json` | Per-section drafting transcripts |
| `newsletter_agent.db` | Updated `agent_state` row at step `draft` |
