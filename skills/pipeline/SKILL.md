---
name: pipeline
description: Top-level newsletter orchestrator. Drives the 12-step pipeline (start → send) end-to-end from a parent Claude Code session, dispatching parallel Agents for the seven LLM-using steps. Writes a session summary to runs/<SID>/summary.md.
---

# newsagent:pipeline

This SKILL drives the full newsletter pipeline from a parent Claude Code
session. For no-LLM steps it invokes the step's Python CLI. For LLM-using
steps it follows the `--prepare-batches → parallel Agents → --apply-results`
pattern, dispatching all batches as parallel Agents in a single message.

For non-interactive use (cron, CI, or any context outside Claude Code), use
the Python orchestrator `lib.steps.pipeline` directly with an `--engine` override.
See "Non-interactive fallback" below.

## Step plan

The pipeline runs these 12 steps in order:

```
start → gather → filter → download → dedupe → summarize →
rate → cluster → select → draft → rewrite → send
```

## Per-step model & batch table

| Step | Mode | Model | Batches |
|---|---|---|---|
| start | Python CLI | — | — |
| gather | Python CLI | — | — |
| filter | prepare/dispatch/apply | haiku | 25 headlines per batch |
| download | Python CLI | — | — |
| dedupe | Python CLI | — | — |
| summarize | prepare/dispatch/apply | sonnet | 15 articles per batch |
| rate | Python CLI (uses openai:gpt-4o-mini by default) | — | — |
| cluster | prepare/dispatch/apply | haiku | 1 batch (all clusters) |
| select | prepare/dispatch/apply | haiku | 25 noise per assign batch; 1 merge batch |
| draft | prepare/dispatch/apply | sonnet | 1 batch per section |
| rewrite | prepare/**critic loop**/apply | sonnet | separate critique → improve → title → assemble dispatches |
| send | Python CLI | — | — |

## Command discipline (stay prompt-free)

The driver session runs under a permission allowlist (`.claude/settings.json`).
Only these run without an approval prompt, so use ONLY these:

- `.venv/bin/python -m lib.steps.* …` — every step CLI (prepare / apply / run).
  Always the `.venv/bin/python` form, never bare `python` (bare `python` is not
  allowlisted and will prompt).
- `.venv/bin/python tools/check_batch.py …`
- the **Read** and **Write** tools on `runs/**`, and **Read** on `download/**`.

To keep the whole run prompt-free:

- **Drive every step with its `.venv/bin/python -m lib.steps.<step> …` CLI.**
  Never substitute ad-hoc shell for what a step CLI already does.
- **Inspect files with the Read tool, never bash** — no `cat runs/…`, `tail`,
  `head`, `ls`, `wc`, or `echo`. Reading a step log or a batch/result JSON is a
  Read-tool call on the `runs/**` (or `download/**`) path.
- **Never run `sleep` and never poll.** The harness blocks chained sleeps. For
  a long no-LLM step (e.g. `download`), launch it with `run_in_background: true`
  and wait for the completion notification — do not babysit it with `tail`.
- **Enumerate batch files from the `--prepare-batches` stdout**, which prints
  the full path of every batch file it wrote. Do NOT `ls` the directory and do
  NOT pipe prepare's output through `head`/`tail` (truncating it reintroduces
  the zero-index off-by-one footgun). Dispatch exactly one Agent per path
  printed.

## Driver loop (what parent Claude does)

For each step in the plan:

1. **No-LLM steps** (start, gather, download, dedupe, rate, send):
   Run the Python CLI:
   ```bash
   .venv/bin/python -m lib.steps.<step> --session SID [step-specific args]
   ```
   Abort the orchestrator on non-zero exit. For long steps (`download`),
   launch with `run_in_background: true` and wait for the completion
   notification rather than polling.

2. **LLM-using fan-out steps** (filter, summarize, cluster, select, draft):
   ```bash
   .venv/bin/python -m lib.steps.<step> --session SID --prepare-batches [step-specific args]
   ```
   Then dispatch ALL `runs/SID/<step>-batches/batch-*.json` files as Agents
   in a SINGLE parent message (one Agent tool call per batch file). Use the
   model from the table above for that step. Each Agent's prompt instructs
   it to read the batch file, run the step's logic in its own context, and
   write the result to `runs/SID/<step>-results/batch-NNN.json`.

   **Enumerate batch files from the `--prepare-batches` stdout, which prints
   the full path of every batch file it wrote — do NOT assume the index
   range and do NOT `ls` the directory.** Batch files are ZERO-INDEXED
   (`batch-000.json` is the first one), and the count varies per step and per
   run. Counting from 1, or guessing N from the prepare output's "Prepared N
   batches" line, is a known footgun — it produced a silent off-by-one
   (skipping batch-000 and dispatching a non-existent batch-NNN) in past
   runs. Never pipe prepare's stdout through `head`/`tail` (that truncates
   the list); read the full path list it prints and dispatch one Agent per
   path.

   (For `select` there are two batch subdirs — `select-assign-batches/` and
   `select-merge-batches/` — dispatch Agents for both in the same message.)

   Wait for all Agents to return. Then:
   ```bash
   .venv/bin/python -m lib.steps.<step> --session SID --apply-results runs/SID/<step>-results
   ```
   (For `select` use `--apply-results runs/SID` so it can find both result
   subdirs.)

   If apply reports problems on stderr (`missing ...`, `schema mismatch`),
   identify the failing batch numbers, re-dispatch only those Agents
   (overwriting their result files), and re-run apply.

3. **`rewrite` is a sequential critic loop, NOT a fan-out.** After
   `--prepare-batches`, do NOT dispatch all batches at once. Instead parent
   Claude drives the loop from `skills/rewrite/SKILL.md`: dispatch a CRITIQUE
   Agent → read its score/accept → if score < 8.0 dispatch an IMPROVE Agent →
   re-critique → … (≤ `--max-edits`), then a TITLE Agent. Each pass is a
   separate dispatch with fresh context (genuine critic/improver separation);
   drafts and critiques thread through `runs/SID/rewrite-work/`. There is no
   assemble Agent — finish with one mechanical CLI call,
   `rewrite --finalize …`, which packages the work files and applies. Narrate
   each dispatch and what came back.

After the `send` step completes, write `runs/SID/summary.md` (unless
`--no-summary` was passed) with the allowlisted CLI:

```bash
.venv/bin/python -m lib.run_summary --session SID
```

## Per-step Agent prompt skeletons

See the individual SKILL.md files for the exact instructions each Agent
should receive:
- `skills/filter/SKILL.md`
- `skills/summarize/SKILL.md`
- `skills/cluster/SKILL.md`
- `skills/select/SKILL.md`
- `skills/draft/SKILL.md`
- `skills/rewrite/SKILL.md`

For every Agent dispatch:
- `subagent_type: "general-purpose"`
- `model:` from the table above
- `description:` brief, e.g. `"Classify headline batch 003"`
- `prompt:` skeleton from the step's SKILL.md, with the actual batch file
  path filled in

Dispatch ALL batches for a step in one parent message (multiple Agent tool
calls in the same message) so they run in parallel.

## Flags

| Flag | Default | Notes |
|---|---|---|
| `--db PATH` | `newsletter_agent.db` | SQLite DB path |
| `--sources PATH` | `sources.yaml` | YAML source list (used by start step) |
| `--session SID` | autogenerated | Session id to use or create |
| `--new` | off | Force a new session id |
| `--resume SID` | — | Resume an existing session from its first incomplete step |
| `--from STEP` | — | Start from STEP and run everything after it |
| `--only STEP` | — | Run exactly one step |
| `--max-edits N` | 2 | Critic-optimizer iterations for draft and rewrite |
| `--parallelism N` | 4 | Section drafters for classic-mode draft only |
| `--no-email` | off | Skip the Gmail send at the end of rewrite/send (email is on by default; requires `GMAIL_USER`/`GMAIL_PASSWORD`) |
| `--no-summary` | off | Skip writing runs/<SID>/summary.md |
| `--cached-pages` | off | Gather reads html sources from `runs/<SID>/pages/` (rss/rest still live). Use to resume a halted gather after dropping a manual landing page. |

`--engine` from `lib.steps.pipeline` is NOT supported in the interactive flow —
the model is fixed per step (table above). For full-pipeline engine override
use the non-interactive fallback below.

## Step plan resolution

1. `--only STEP` → run exactly that one step.
2. `--from STEP` → run STEP and every step that follows it in workflow order.
3. `--resume SID` → load state via `.venv/bin/python -m lib.steps.progress --session SID`,
   find the first non-complete step, run from there.
4. Default → run all 12 steps starting from start.

## Resuming / recovery

If a step fails mid-run, the orchestrator aborts. To recover:

```bash
.venv/bin/python -m lib.steps.progress --session SID
.venv/bin/python -m lib.steps.reset --session SID --errors
# Then re-invoke /newsagent:pipeline --resume SID
```

### Gather halt (0-URL source)

If `gather` reports `gather halted: N source(s) returned 0 URLs`, the run
stopped because a source extracted nothing (e.g. WSJ blocked). To recover:

1. For each html source named, download its landing page to the
   `runs/<SID>/pages/<source>.html` path printed in the halt message (e.g. via
   the headed browser `scripts/playwright_login.py` or Bright Data).
2. Resume in cached-pages mode (html from disk, rss/rest live):
   ```bash
   .venv/bin/python -m lib.steps.pipeline --resume SID --cached-pages
   ```
   Gather re-reads the cached html (including your manual file), finds the
   source non-empty, completes, and the pipeline continues.

For a halted rss/rest source there is no manual-file path — just resume
(`--resume SID`) once the feed/API is reachable again.

## Output

- `runs/<SID>/<step>-batches/` — prepared batch JSONs (per LLM step)
- `runs/<SID>/<step>-results/` — Agent result JSONs (per LLM step)
- `runs/<SID>/<step>.json` — per-step summary
- `runs/<SID>/summary.md` — top-level human-readable run report
- `out/YYYY-MM-DD.html` — final newsletter HTML (from send step)
- `out/latest.html` — symlink to latest

## Non-interactive fallback (cron / CI)

For automation outside Claude Code, run the Python orchestrator directly with
an `--engine` override (`subagent` is forbidden — it falls back to `claude -p`
which is not covered by the Max plan):

```bash
python -m lib.steps.pipeline --sources sources.yaml \
  --engine openrouter:google/gemini-2.5-flash
```

This bypasses the Agent-dispatch flow entirely and uses classic mode on every
LLM step.
