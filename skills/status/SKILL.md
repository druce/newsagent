---
name: status
description: Report workflow progress for the current or specified newsletter session — step list, status per step, error messages, headline/cluster/section counts. Defaults to the most recent session.
---

# newsagent:status

Reads the latest checkpoint row for a session and prints a human-readable progress report.

## How to invoke

```bash
python -m lib.steps.status [--db newsletter_agent.db] [--session SID]
```

- `--session` defaults to the most-recently-updated session in `agent_state`.

## Output

- Session id, overall progress %, next step
- Counts: headlines, clusters, sections
- Per-step table: index, name, status, status_message or error_message

## When to use

- After `/newsagent:init` to confirm setup.
- During a run to see what's running / errored.
- Before `/newsagent:resume` to decide what to re-execute.

## No-op cases

- Empty DB or no sessions → prints "No sessions found." and exits 0.
- Session exists but no checkpoint rows → prints "No state found for session SID."
