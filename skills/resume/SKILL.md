---
name: news:resume
description: Inspect a session, clear any ERROR steps back to NOT_STARTED, and print the next step to invoke. Phase 2 is print-only — the /news:run orchestrator (Phase 6) will actually re-enter the pipeline.
---

# news:resume

```bash
python -m lib.steps.resume <SID> [--db ...] [--no-clear]
```

## Behavior

1. Loads latest state.
2. By default, clears all ERROR steps to NOT_STARTED.
3. Prints the next step (first incomplete) and the exact command to invoke it.

Phase 2 doesn't yet auto-invoke; `/news:run` (Phase 6) will.
