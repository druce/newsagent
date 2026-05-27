---
name: sessions
description: List the N most recent newsletter sessions in the database with session id, last-updated timestamp, current step, and completion percentage.
---

# newsagent:sessions

Browse recent sessions in `newsletter_agent.db`.

## How to invoke

```bash
python -m lib.steps.sessions [--db newsletter_agent.db] [--limit 10]
```

## Output

Four-column table:

| SESSION | UPDATED | STEP | PROGRESS |
|---|---|---|---|
| 2026-05-17-120000 | 2026-05-17T12:01:33 | gather | 9% |
| 2026-05-16-093015 | 2026-05-16T10:42:11 | done | 100% |

## When to use

- Find a session id to feed to `/newsagent:recover`, `/newsagent:progress`, or `/newsagent:show`.
- Audit recent runs at a glance.

## No-op cases

- Empty DB → "No sessions found." exit 0.
