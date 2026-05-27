---
name: send
description: Render the session's newsletter as styled HTML, write to out/YYYY-MM-DD.html, update out/latest.html symlink, insert a row in the newsletters table, and email via Gmail (GMAIL_USER/GMAIL_PASSWORD).
---

# newsagent:send

Step 12 (final) of `/newsagent:pipeline`. Also invoked automatically at the end of
`newsagent:rewrite` so every successful rewrite delivers the newsletter.

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

- `out/<date>.html` — styled newsletter
- `out/latest.html` — symlink (or copy on platforms without symlinks)
- Row in `newsletters` table with title + html
- Email to `--to` / `GMAIL_USER` (unless `--no-email`)

## Behavior

Uses `state.final_newsletter` if set. Otherwise renders a "dummy" placeholder useful for testing the full pipeline before LLM steps are in place.

**Idempotency:** the email send is skipped automatically if a `newsletters` row
already exists for this session. This prevents double-delivery when `rewrite`
has already triggered the send and the pipeline orchestrator then runs `send`
as step 12. Pass `--force-email` to override.
