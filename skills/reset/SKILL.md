---
name: reset
description: Reset workflow step(s) on a session. --errors clears ERROR steps; --from STEP resets a step and everything after; --all resets every step. Confirms by default; --yes to skip.
---

# newsagent:reset

```bash
python -m lib.steps.reset <SID> [--db ...] (--errors | --from STEP | --all) [--yes]
```

## Use cases

- `--errors`: an LLM-step retry needs a clean slate after a transient failure.
- `--from gather`: starting fresh from gather but keeping init.
- `--all`: rerun the whole session from scratch (rare).
