---
name: news:diff
description: Compare two newsletter sessions side-by-side — step statuses, headline/section counts, newsletter titles, and Jaccard URL overlap of selected articles.
---

# news:diff

Loads the latest checkpoint for each of two sessions and outputs a markdown
comparison table. Useful for understanding how two runs diverged (e.g. after
changing filtering thresholds, LLM models, or source lists).

## How to invoke

```bash
python -m lib.steps.diff <SID1> <SID2> [--db newsletter_agent.db]
```

- `SID1`, `SID2` — session IDs to compare (required positional arguments).
- `--db` — path to SQLite database (default: `newsletter_agent.db`).

## Output

Two markdown tables printed to stdout:

**Summary table** — one row per metric:
- Newsletter title (from each session)
- Headline count (after gather/filter)
- Section count (items in `newsletter_section_data`)
- Final newsletter length in characters
- URL overlap: Jaccard similarity of the selected article URL sets, as `%`
- Shared URL count out of union total

**Step-by-step status table** — one row per workflow step:
- Status icon: `✓` complete, `✗` error, `▶` started, `⭕` not_started, `—` skipped

## When to use

- After re-running a pipeline with different settings to see what changed.
- When debugging why two sessions produced different newsletters.
- To quantify topic diversity change between runs.
- Before deciding which session to `send` when two are available.

## Errors

- **Exit code 1** if either session ID is not found in the database.  
  Error message goes to stderr: `Error: session(s) not found: <SID>`.
- Exit code 0 for all other cases, including sessions with empty section data.

## Notes

- The URL overlap metric compares the `link` field of items in
  `newsletter_section_data`. Items that only have `section_markdown` (post-draft
  shape) are silently skipped — overlap will show `0%` for fully-drafted sessions
  that did not populate `link`.
- Does not modify the database.
