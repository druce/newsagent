---
name: backfill-published
description: Rebuild the published_articles cross-day dedup store from past sessions' select checkpoints and newsletters timestamps. Dry-run by default — requires --yes to write. Idempotent per session.
---

# newsagent:backfill-published

Reconstructs `published_articles` — the store `crossdedupe` compares today's
candidates against — for sessions that already went out.

This is a **maintenance utility**. The default dry-run never modifies anything,
and a session that already has rows is skipped, so it is safe to re-run.

## Why it exists

Until 2026-09-16 `draft` overwrote `state.newsletter_section_data` (select's
per-article list) with section markdown, so `send` recorded 0 rows for every
session and `crossdedupe` never had anything to suppress. The fix keeps the
list intact for new sessions; this skill fills in the history.

## Usage

```bash
# Dry-run: list sent sessions in the last 14 days and what would be recorded
.venv/bin/python -m lib.steps.backfill_published

# Write it
.venv/bin/python -m lib.steps.backfill_published --yes

# Wider window, or a single session
.venv/bin/python -m lib.steps.backfill_published --days 30 --yes
.venv/bin/python -m lib.steps.backfill_published --session 2026-09-15-07-00-40 --yes
```

Flags: `--days N` (default 14), `--session SID`, `--db PATH`, `--yes`.

## What it does per session

For each session with a `newsletters` row inside the window, oldest first:

1. **Skip if already recorded** (`published_articles` has rows for it).
2. **Load the `select` checkpoint** from `agent_state` — not the latest
   checkpoint, whose section list was overwritten by the pre-fix `draft`.
   Sessions with no `select` checkpoint (garbage-collected) are reported and
   skipped.
3. **Embed and insert** via the shared recorder `lib.published.record_session_articles`
   (all rated articles; only the selected ones embedded and stamped — OpenAI
   `text-embedding-3-large` of `title + short_summary`, needs
   `OPENAI_API_KEY`), stamping `published_at` with the newsletter's
   `created_at` so `crossdedupe`'s `--lookback-days` window sees the right day.

The backfilled set is what `select` chose (default 100 per session), which can
be slightly broader than what the drafter actually printed. For suppression
that errs on the safe side.

## Output

```
[DRY-RUN (use --yes to write)] 3 sent session(s):

  would record 100 article(s)  2026-09-14-07-00-32  (published 2026-09-14)
  skip      2026-09-14-18-00-15  already recorded (100 row(s))
  would record 100 article(s)  2026-09-15-07-00-40  (published 2026-09-15)
```

## Never touches

`agent_state`, `newsletters`, `urls`, `articles`, `sites`, `out/`, `download/`.

## Verify afterwards

```bash
sqlite3 newsletter_agent.db "select session_id, count(*), min(published_at) from published_articles group by 1 order by 1;"
```

The next pipeline run's `crossdedupe` should then report candidates instead of
"no candidates".
