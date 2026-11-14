---
name: send
description: Render the session's newsletter as styled HTML, write to out/YYYY-MM-DD-HHMMSS.html, update out/latest.html symlink, insert a row in the newsletters table, stamp this issue's articles as published in the published_articles cross-day dedup store, and email via Gmail (GMAIL_USER/GMAIL_PASSWORD).
---

# newsagent:send

Step 13 (final) of `/newsagent:pipeline`. Also invoked automatically at the end of
`newsagent:rewrite` so every successful rewrite delivers the newsletter.

On first delivery for a session, `send` stamps `published_at` on the session's
selected rows in `published_articles` (which `newsagent:select` already wrote,
with their `title + short_summary` OpenAI text-embedding-3-large vectors). Only
stamped, selected rows count as published, and that is what
`newsagent:crossdedupe` reads to suppress stories already published in the last
few days. If the session has no rows (its `select` ran before this existed),
`send` records and stamps them itself. Best-effort — a failure logs a warning
and never blocks the send.

## How to invoke

```bash
python -m lib.steps.send --db newsletter_agent.db --session SID
```

## Flags

| Flag | Default | Meaning |
|---|---|---|
| `--to ADDR` | `GMAIL_USER` | Email recipient |
| `--no-email` | off | Write the HTML but skip the Gmail send |
| `--force-email` | off | Send email even if a newsletter has already been delivered for this session (overrides the idempotency guard) |

`GMAIL_USER` and `GMAIL_PASSWORD` (Gmail app password) must be set in the
environment (typically via `.env`). If either is missing, the email step is
skipped with a stderr warning and the rest of the step still succeeds.

## Output

- `out/<stamp>.html` — styled newsletter (`<stamp>` = `YYYY-MM-DD-HHMMSS`, the session start time)
- `out/latest.html` — symlink (or copy on platforms without symlinks)
- Row in `newsletters` table with title + html
- Email to `--to` / `GMAIL_USER` (unless `--no-email`)

## Behavior

Uses `state.final_newsletter` if set. Otherwise renders a "dummy" placeholder useful for testing the full pipeline before LLM steps are in place.

**Idempotency:** the email send is skipped automatically if a `newsletters` row
already exists for this session. This prevents double-delivery when `rewrite`
has already triggered the send and the pipeline orchestrator then runs `send`
as step 12. Pass `--force-email` to override.
