---
name: checkpoint
description: Force a manual state checkpoint for a session at a specific step. Escape-hatch for when a step crashed after doing work but before saving its own checkpoint.
---

# newsagent:checkpoint

Loads the most recent state for a session and calls `state.save_checkpoint(STEP)`,
writing the current in-memory state to the `agent_state` table under the given
step name.

This is a **recovery escape-hatch** for rare crash scenarios. Under normal
operation each step saves its own checkpoint; you only need this when that
automatic save didn't happen.

## How to invoke

```bash
python -m lib.steps.checkpoint <SID> <STEP> [--db newsletter_agent.db]
```

**Arguments:**

| Argument | Description |
|---|---|
| `SID` | Session ID to checkpoint |
| `STEP` | Step name to save (e.g. `gather`, `filter`, `download`) |

**Options:**

| Flag | Default | Description |
|---|---|---|
| `--db PATH` | `newsletter_agent.db` | SQLite database path |

## What it does

1. Loads the latest `agent_state` row for `SID`.
2. Writes that state back under the key `(SID, STEP)` (upsert).

This is equivalent to the step completing and calling `state.save_checkpoint(STEP)`
itself, but triggered manually from the CLI.

## When to use

- A step ran to completion but crashed before its final `save_checkpoint()` call.
- You manually edited state (e.g. via SQLite CLI) and want to persist the result.
- You need to re-point `newsagent:resume` to a different step without re-running the step.

## Output

```
Checkpoint saved: session=my-session, step=gather
```

## Errors

- **Exit code 1** if the session ID is not found in the database.  
  Error message goes to stderr: `Error: no state found for session '<SID>'`.
- Exit code 0 on success.

## Notes

- If a row for `(SID, STEP)` already exists it is overwritten (upsert semantics).
- The `STEP` argument is free-form text — it does not have to be one of the 11
  canonical workflow step names, but using a canonical name keeps `newsagent:status`
  and `newsagent:resume` consistent.
