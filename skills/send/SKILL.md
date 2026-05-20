---
name: send
description: Render the session's newsletter as styled HTML, write to out/YYYY-MM-DD.html, update out/latest.html symlink, and insert a row in the newsletters table. Phase 2: preview only — no Gmail.
---

# newsagent:send

Step 12 (final) of `/newsagent:run`.

## How to invoke

```bash
python -m lib.steps.send --db newsletter_agent.db --session SID
```

Phase 2 does NOT implement Gmail send. `--notify` will error.

## Output

- `out/<date>.html` — styled newsletter
- `out/latest.html` — symlink (or copy on platforms without symlinks)
- Row in `newsletters` table with title + html

## Behavior

Uses `state.final_newsletter` if set. Otherwise renders a "dummy" placeholder useful for testing the full pipeline before LLM steps are in place.
