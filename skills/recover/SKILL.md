---
name: recover
description: Inspect a session, clear any ERROR steps back to NOT_STARTED, and print the next step to invoke. Use after a failed step to unblock `/newsagent:pipeline --resume`.
---

# newsagent:recover

```bash
python -m lib.steps.recover <SID> [--db ...] [--no-clear]
```

## Behavior

1. Loads latest state.
2. By default, clears all ERROR steps to NOT_STARTED.
3. Prints the next step (first incomplete) and the exact command to invoke it.

Re-enter the pipeline with `/newsagent:pipeline --resume <SID>` after running this.
