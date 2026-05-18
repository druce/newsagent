# Phase 8 — Polish (`diff`, `gc`, `checkpoint`, README)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Final polish — three small recovery/maintenance skills (`diff`, `gc`, `checkpoint`), plus a top-level README that documents the system end-to-end.

**Architecture:** Each polish skill is a thin wrapper over existing state methods. None of them need new tests beyond their own CLI smoke checks.

## File structure

| Path | Purpose |
|---|---|
| `lib/steps/diff.py` | news:diff |
| `lib/steps/gc.py` | news:gc |
| `lib/steps/checkpoint.py` | news:checkpoint |
| `skills/{diff,gc,checkpoint}/SKILL.md` | contracts |
| `tests/test_step_diff.py`, `test_step_gc.py`, `test_step_checkpoint.py` |  |
| `README.md` | rewrite to cover the new architecture |

---

## Task 1: `news:diff` — compare two sessions

**Files:**
- Create: `lib/steps/diff.py`, `tests/test_step_diff.py`, `skills/diff/SKILL.md`

CLI: `python -m lib.steps.diff <SID1> <SID2> [--db ...]`

Output: a markdown table comparing the two sessions:
- URL count, article count, section count
- Per-step status: ✓/✗/▶
- Overlap of selected newsletter headlines (Jaccard of URL sets in `newsletter_section_data`)
- Diff of newsletter titles + section counts

Implementation: ~70 LOC. Use `NewsletterAgentState.load_latest_from_db()` for each session.

Tests (3):
- Two sessions with overlap → diff includes overlap %
- Missing session → error
- Empty sessions → minimal diff

- [ ] Tests → fail → implement → pass → commit `feat(steps): news:diff compares two sessions`
- [ ] SKILL.md → commit `docs(skills): news:diff SKILL.md`

---

## Task 2: `news:gc` — garbage-collect old sessions

**Files:**
- Create: `lib/steps/gc.py`, `tests/test_step_gc.py`, `skills/gc/SKILL.md`

CLI: `python -m lib.steps.gc [--older-than DAYS] [--db ...] [--yes]`

Default: dry-run (lists what would be deleted). With `--yes`: deletes.

What gets deleted:
- Rows in `agent_state` for sessions older than `--older-than` days (default 30)
- Corresponding `runs/<SID>/` scratch directories

What is NEVER touched:
- `articles`, `urls`, `newsletters`, `sites` tables (those are content, not state)
- `out/*.html` (final newsletters)
- `download/*` (article texts cache)

Implementation: ~80 LOC. Use `AgentState.delete_session()` + `shutil.rmtree("runs/<SID>")`.

Tests (3):
- Dry-run lists candidates without deleting
- `--yes` deletes both `agent_state` rows and `runs/<SID>/`
- `--older-than 0` deletes everything (dry-run mode)

- [ ] Tests → fail → implement → pass → commit `feat(steps): news:gc garbage-collects old sessions`
- [ ] SKILL.md → commit `docs(skills): news:gc SKILL.md`

---

## Task 3: `news:checkpoint` — manual state persist

**Files:**
- Create: `lib/steps/checkpoint.py`, `tests/test_step_checkpoint.py`, `skills/checkpoint/SKILL.md`

CLI: `python -m lib.steps.checkpoint <SID> <STEP> [--db ...]`

Escape-hatch: forces `state.save_checkpoint(STEP)` for a session. Used when a step crashed AFTER doing useful work but BEFORE its own checkpoint. Rare.

Implementation: ~30 LOC.

Tests (2):
- Persists state for a session at a specific step
- Errors cleanly if session doesn't exist

- [ ] Tests → fail → implement → pass → commit `feat(steps): news:checkpoint manual state persist`
- [ ] SKILL.md → commit `docs(skills): news:checkpoint SKILL.md`

---

## Task 4: README rewrite

**Files:**
- Modify: `README.md`

Content sections:

```markdown
# news_agent

Daily AI newsletter agent built as a Claude Code skill plugin.

## What this does

[1-2 paragraphs: gathers headlines from RSS/HTML/REST sources, filters via LLM,
downloads + summarizes articles, clusters by topic, scores via multi-axis ratings
+ Bradley-Terry, picks a diverse top-K per cluster (MMR), writes a polished
newsletter via parallel section drafters + critic loops, renders HTML.]

## Quick start

[install, .env setup, copy umap_reducer.pkl, single command for full run]

## Architecture

[diagram: 11 steps init → send, plus standalone bluesky digest]

## Per-step reference

[table mapping each `/news:*` skill to what it does + when to use it standalone]

## Engine configuration

[explain default_engine per prompt, NEWS_PROMPT_<NAME>_ENGINE env vars,
--engine flag forwarding]

## Recovery

[news:resume, news:reset, news:status, news:show, news:diff, news:gc]

## Tech stack

[Python 3.11, Pydantic v2, SQLite, Click, OpenAI/Google/OpenRouter, UMAP+HDBSCAN+choix, etc.]

## License

MIT
```

- [ ] Write the new README. Reference existing CLAUDE.md + plans as needed.
- [ ] Commit `docs: rewrite README for new architecture`.

---

## Task 5: Final verification + tag

- [ ] Full `.venv/bin/pytest tests/ --cov=lib --cov-report=term`. All pass.
- [ ] Tag `phase-8-complete`.
- [ ] Note final commit count, test count, coverage in commit message.

---

## Notes for the implementer

- **Polish skills are thin.** Don't over-engineer. Each is ~50-100 LOC.
- **`news:gc` is the most dangerous.** Default to dry-run; require `--yes` to actually delete.
- **README:** keep it tight. Link to CLAUDE.md + CLAUDE_REFACTOR.md for deeper detail.
