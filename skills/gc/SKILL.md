---
name: news:gc
description: Garbage-collect agent_state rows and runs/<SID>/ directories for sessions older than N days. Dry-run by default — requires --yes to actually delete.
---

# news:gc

Identifies sessions that have not been updated within the last N days and
(optionally) deletes their workflow state rows and scratch directories.

This is a **maintenance utility**. It is safe to run at any time; the default
dry-run mode never modifies anything.

## How to invoke

```bash
# Dry-run: list what would be deleted (default, no --yes flag)
python -m lib.steps.gc [--older-than 30] [--db newsletter_agent.db]

# Actually delete
python -m lib.steps.gc --older-than 30 --yes [--db newsletter_agent.db]
```

**Options:**

| Flag | Default | Description |
|---|---|---|
| `--older-than DAYS` | `30` | Sessions last updated more than DAYS days ago |
| `--db PATH` | `newsletter_agent.db` | SQLite database path |
| `--yes` | off (dry-run) | Perform deletions instead of just listing |

## What gets deleted

- All rows in `agent_state` for the matching `session_id`
- `runs/<SID>/` scratch directory (if it exists)

## What is NEVER touched

- `articles`, `urls`, `newsletters`, `sites` tables (content, not state)
- `out/*.html` — rendered newsletters
- `download/` — article text cache

## When to use

- Weekly or monthly maintenance to keep the database small.
- After a long experiment phase where many throwaway sessions were created.
- Before a fresh round of production runs to reduce noise in `news:sessions`.

## Dry-run output example

```
[DRY-RUN (use --yes to delete)] Found 2 session(s) older than 30 day(s):

  would delete  20250101-abc123  runs/20250101-abc123/ exists
  would delete  20250108-def456

Run with --yes to permanently delete these 2 session(s).
```

## Errors

- If the database does not exist, SQLite will raise an error.
- Exit code is always 0 on normal operation (including "nothing found").
