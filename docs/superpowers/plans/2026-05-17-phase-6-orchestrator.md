# Phase 6 — `/news:run` Orchestrator + Run Summary + Engine Polish

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Top-level orchestrator that sequences all 11 pipeline steps in one command, supports resume/from/only flags, forwards engine overrides to every step, and emits a per-session summary report.

**Most engine-flexibility work is already done** (Phases 1, 3, 4 wired OpenRouter, OpenAI, Google engines + per-prompt `default_engine` + `NEWS_PROMPT_<NAME>_ENGINE` env vars + `--engine` flags on each step). Phase 6's deltas are: the orchestrator skill, a run-summary generator, and verifying engine override semantics end-to-end.

**Architecture:**
- `lib/steps/run.py` — Click CLI that imports each step's `cli` and invokes them in sequence by calling `step_cli.main(args, standalone_mode=False)`. No subprocess overhead. Each step's `start_step`/`complete_step`/`save_checkpoint` keeps state correct.
- `lib/run_summary.py` — reads `runs/<SID>/*.json` artifacts plus final state, writes `runs/<SID>/summary.md` with progress per step, key counts, and final newsletter title/length.
- `skills/run/SKILL.md` — agent-facing contract for `/news:run`.

**Pipeline order (11 steps, matching `WORKFLOW_STEPS`):**
```
init → gather → filter → download → summarize → rate → cluster → select → draft → rewrite → send
```

Plus the standalone `dedupe` (between summarize and rate by convention — not in `WORKFLOW_STEPS` but invoked between them when requested).

**Engine override semantics (verify already works):**
- `--engine <id>` applies to every prompt unless overridden by env var or per-prompt flag.
- `NEWS_PROMPT_<NAME>_ENGINE=<id>` overrides individual prompts.
- `--engine-<prompt-name> <id>` (e.g. `--engine-rate-quality openai:gpt-4o`) overrides one prompt. Implementation: convert the dash to underscore and set the corresponding env var for the duration of the run.

**Tech additions:** none.

## File structure

| Path | Purpose |
|---|---|
| `lib/steps/run.py` | Orchestrator CLI (~120 LOC) |
| `lib/run_summary.py` | Reads runs/<SID>/*.json + state → writes summary.md |
| `skills/run/SKILL.md` | Agent-facing contract |
| `tests/test_step_run.py` | Orchestrator: invokes steps in order, resume, from, only, engine forwarding |
| `tests/test_run_summary.py` | Summary generator |

---

## Task 1: `lib/run_summary.py` — summary generator

**Files:**
- Create: `lib/run_summary.py`
- Create: `tests/test_run_summary.py`

API:
```python
def generate_summary(session_id: str, db_path: str = "newsletter_agent.db",
                     runs_dir: str = "runs") -> str:
    """Return the summary markdown string for a session.
    Reads runs/<SID>/*.json + latest state from DB.
    """

def write_summary(session_id: str, ...) -> str:
    """Generate + write runs/<SID>/summary.md. Returns the file path."""
```

The summary contains:
- Session id, started/completed timestamps
- One line per workflow step: `step_name [status] (timing) — message`
- Counts: headlines gathered/filtered/downloaded/summarized; clusters; sections
- For draft: per-section critic transcript summary (iterations, final score, accepted)
- For rewrite: critic transcript + final title
- Per-step artifact links: `runs/<SID>/gather.json`, etc., if present

- [ ] **Step 1: Tests** — 3 tests:
  - Empty session: returns a minimal report
  - Session with partial progress: shows correct step statuses
  - Session with full progress: includes title, section count

- [ ] **Step 2: Implement** (~100 LOC). Use `NewsletterAgentState.load_latest_from_db()` for state + `Path("runs") / sid` for artifacts.

- [ ] **Step 3: Commit `feat: run_summary generator for /news:run`**

---

## Task 2: `lib/steps/run.py` — orchestrator

**Files:**
- Create: `lib/steps/run.py`
- Create: `tests/test_step_run.py`

API:
```bash
python -m lib.steps.run [--db ...] [--session SID|--new] [--sources sources.yaml] \
    [--from STEP] [--only STEP] [--resume SID] \
    [--max-edits N] [--parallelism N] [--engine ENGINE] [--engine-<prompt> ENGINE]... \
    [--notify] [--no-summary]
```

Behavior:
1. Resolve session: `--new` → call init internally and use its session id; `--session SID` or `--resume SID` → use existing.
2. Resolve step plan:
   - Default: run all 11 steps starting from `init`.
   - `--resume`: load state, start from first incomplete step.
   - `--from STEP`: run STEP and everything after.
   - `--only STEP`: run only this step.
3. For each step in plan: build args, invoke `step_cli.main(args, standalone_mode=False)`. If it raises (nonzero exit or exception), abort.
4. After all steps complete, generate `runs/<SID>/summary.md` unless `--no-summary`.
5. Echo the summary path.

Implementation sketch:
```python
import click
from lib.state import NewsletterAgentState, WORKFLOW_STEPS

# Map step_id → click command
from lib.steps import init as step_init, gather, filter as step_filter, download
from lib.steps import summarize, rate, cluster, select, draft, rewrite, send

_STEP_CLIS = {
    "init": step_init.cli,
    "gather": gather.cli,
    "filter": step_filter.cli,
    "download": download.cli,
    "summarize": summarize.cli,
    "rate": rate.cli,
    "cluster": cluster.cli,
    "select": select.cli,
    "draft": draft.cli,
    "rewrite": rewrite.cli,
    "send": send.cli,
}

_STEP_IDS = [step_id for step_id, *_ in WORKFLOW_STEPS]


def _build_args(step_id: str, session_id: str, db_path: str,
                sources_path: str, max_edits: int, parallelism: int,
                engine: str | None, notify: bool) -> list[str]:
    args = ["--db", db_path]
    if step_id == "init":
        args.extend(["--sources", sources_path, "--session", session_id])
    else:
        args.extend(["--session", session_id])
    if engine and step_id in ("filter", "summarize", "rate",
                              "cluster", "select", "draft", "rewrite"):
        args.extend(["--engine", engine])
    if step_id in ("draft", "rewrite"):
        args.extend(["--max-edits", str(max_edits)])
    if step_id == "draft":
        args.extend(["--parallelism", str(parallelism)])
    if step_id == "send" and notify:
        args.append("--notify")
    return args


@click.command()
@click.option("--db", "db_path", default="newsletter_agent.db")
@click.option("--session", "session_id", default=None)
@click.option("--new", is_flag=True, help="Create a new session via init")
@click.option("--sources", "sources_path", default="sources.yaml")
@click.option("--resume", "resume_sid", default=None)
@click.option("--from", "from_step", default=None)
@click.option("--only", "only_step", default=None)
@click.option("--max-edits", default=2, type=int)
@click.option("--parallelism", default=4, type=int)
@click.option("--engine", default=None)
@click.option("--notify", is_flag=True)
@click.option("--no-summary", is_flag=True)
def cli(db_path, session_id, new, sources_path, resume_sid, from_step, only_step,
        max_edits, parallelism, engine, notify, no_summary):
    """Top-level newsletter orchestrator."""

    # Resolve session
    if resume_sid:
        session_id = resume_sid
    elif new or session_id is None:
        # init creates a new session; we'll capture it from the latest session in DB after init runs
        session_id = None

    # Determine starting step
    if only_step:
        plan = [only_step]
    elif from_step:
        if from_step not in _STEP_IDS:
            raise click.ClickException(f"Unknown step: {from_step}")
        plan = _STEP_IDS[_STEP_IDS.index(from_step):]
    elif resume_sid:
        state = NewsletterAgentState(session_id=resume_sid, db_path=db_path).load_latest_from_db()
        if state is None:
            raise click.ClickException(f"No state for session {resume_sid}")
        current = state.get_current_step()
        if current is None:
            click.echo("All steps complete.")
            return
        plan = _STEP_IDS[_STEP_IDS.index(current):]
    else:
        plan = list(_STEP_IDS)

    # Need a session id before non-init steps run; if not provided, init creates one
    if session_id is None and plan[0] != "init":
        raise click.ClickException("Must provide --session or --resume or include init step")

    # Run init first if it's in the plan and session_id not set
    for step_id in plan:
        if step_id == "init" and session_id is None:
            # init needs special handling — it creates the session id
            from datetime import datetime
            session_id = datetime.now().strftime("%Y-%m-%d-%H%M%S")

        args = _build_args(step_id, session_id, db_path, sources_path,
                           max_edits, parallelism, engine, notify)
        click.echo(f"\n=== {step_id} ===")
        try:
            _STEP_CLIS[step_id].main(args, standalone_mode=False)
        except SystemExit as e:
            if e.code not in (None, 0):
                raise click.ClickException(f"Step {step_id} failed with exit code {e.code}")
        except Exception as e:
            raise click.ClickException(f"Step {step_id} failed: {e}")

    # Summary
    if not no_summary and session_id:
        from lib.run_summary import write_summary
        path = write_summary(session_id, db_path=db_path)
        click.echo(f"\nSummary written to {path}")


if __name__ == "__main__":
    import sys
    sys.exit(cli())
```

Tests (5):
- Runs all 11 steps in sequence with mocked step CLIs
- `--from cluster` skips earlier steps
- `--only filter` runs just one
- `--resume SID` starts from the first incomplete step
- `--engine openrouter:google/gemini-2.5-flash` propagates to LLM steps
- `--no-summary` skips summary write

Mock each step's `cli` via `patch("lib.steps.<step>.cli")` — verify which steps were called and with what args.

- [ ] **Step 1: Tests**
- [ ] **Step 2: Implement** (per above)
- [ ] **Step 3: Commit `feat(steps): news:run orchestrator with resume/from/only and engine forwarding`**

---

## Task 3: `skills/run/SKILL.md`

**Files:**
- Create: `skills/run/SKILL.md`

```markdown
---
name: news:run
description: Top-level newsletter orchestrator. Runs all 11 pipeline steps (init → send) in sequence with resume/from/only flags and per-step engine overrides. Writes a session summary to runs/<SID>/summary.md.
---

# news:run

## Invocation

```bash
# Fresh full run
python -m lib.steps.run --sources sources.yaml

# Resume a session
python -m lib.steps.run --resume 2026-05-17-120000

# Restart from a specific step
python -m lib.steps.run --session 2026-05-17-120000 --from cluster

# Run a single step on an existing session
python -m lib.steps.run --session 2026-05-17-120000 --only rate

# Force one engine for all LLM steps
python -m lib.steps.run --engine openrouter:google/gemini-2.5-flash

# Or override per prompt (any number of times)
NEWS_PROMPT_FILTER_URLS_ENGINE=openrouter:deepseek/deepseek-v3-chat \
NEWS_PROMPT_RATE_QUALITY_ENGINE=openai:gpt-4o-mini \
  python -m lib.steps.run
```

## Flags

| Flag | Default | Notes |
|---|---|---|
| `--db PATH` | `newsletter_agent.db` | SQLite DB |
| `--sources PATH` | `sources.yaml` | YAML source list (used by init) |
| `--session SID` | autogenerated | Use specific session id |
| `--resume SID` | — | Resume incomplete session |
| `--from STEP` | — | Start from STEP (and everything after) |
| `--only STEP` | — | Run STEP only |
| `--max-edits N` | 2 | Critic-optimizer iterations (draft/rewrite) |
| `--parallelism N` | 4 | Parallel section drafters |
| `--engine ID` | — | Override engine for all LLM prompts |
| `--notify` | off | Send via Gmail (not implemented in Phase 2) |
| `--no-summary` | off | Skip summary.md generation |

## Output

- All intermediate `runs/<SID>/*.json` artifacts from each step
- `runs/<SID>/summary.md` — human-readable run report (unless `--no-summary`)
- `out/YYYY-MM-DD.html` — final newsletter (from send step)

## Errors

If any step fails, the orchestrator aborts and the partial state is checkpointed. Use `/news:status <SID>` to inspect, `/news:resume <SID>` to continue, or `/news:reset <SID> --errors` then `/news:run --resume <SID>` to retry.
```

- [ ] Commit `docs(skills): news:run SKILL.md`

---

## Task 4: End-to-end Phase 6 verification

- [ ] **Step 1: Full pytest + coverage**

```bash
.venv/bin/pytest tests/ --cov=lib --cov-report=term
```

- [ ] **Step 2: Mock end-to-end orchestrator run**

A pure-mock test that runs `news:run --new --sources tmp.yaml --no-summary` with every step's `cli` mocked. Verify all 11 step CLIs were invoked in order.

- [ ] **Step 3: Tag**

```bash
git tag phase-6-complete
git log --oneline phase-5-complete..phase-6-complete | head
```

---

## Notes for the implementer

- **Click commands invoked programmatically via `cli.main(args, standalone_mode=False)`.** This avoids `sys.exit` so errors can be caught. The `standalone_mode=False` flag makes Click return the result instead of exiting.
- **Each step is responsible for its own checkpoint.** The orchestrator does not manage state — it just dispatches.
- **Engine override flag**: only forward `--engine` to steps that take `--engine` (filter, summarize, rate, cluster, select, draft, rewrite). Gather, init, download, send don't accept `--engine`.
- **`--engine-<prompt>` flag**: for Phase 6 v1, skip this. The user can use `NEWS_PROMPT_<NAME>_ENGINE` env var instead — that's already wired through `lib/llm.py:_resolve_engine`. Add the dash-flag variant later if needed.
- **`.venv/bin/pytest`** as always.
- **Pyright noise** stays — ignore.

## Out of scope

- Live API end-to-end smoke test (do separately, after the user verifies they have API keys set)
- Bluesky (Phase 7)
- `news:diff`, `news:gc`, `news:checkpoint` polish (Phase 8)
- True parallel pipelining (currently sequential between steps; only intra-step parallelism via ThreadPoolExecutor)
