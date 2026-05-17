---
name: news:show
description: Dump full state record for a session — workflow status report, per-step checkpoints, headline/section counts, newsletter title and length. Optional --step to show a single step's checkpoint.
---

# news:show

```bash
python -m lib.steps.show <SID> [--db newsletter_agent.db] [--step STEP]
```

Useful for: deep diagnosis after a failed run, comparing session details, confirming what's in a checkpoint before resuming.
