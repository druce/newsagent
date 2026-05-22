# Implement Newsletter Agent as `news:*` Skill Plugin (from scratch)

## Context

We are building a daily AI newsletter agent as a **Claude Code skill plugin** in this directory, from scratch. There is no migration: the new code lives only here. Each pipeline step is its own skill (`/news:gather`, `/news:filter`, …); a top-level `/news:run` skill sequences them; per-prompt LLM calls go through a single `call_prompt(engine=...)` layer that defaults to subagents and is toggleable to OpenRouter / Anthropic SDK / etc.

A working reference implementation exists at `~/projects/OpenAIAgentsSDK/` (a monolithic Python orchestrator: `news_agent.py` + 9 `Tool` classes + `run_agent.py`). It is the **specification** — workflow boundaries, state schema, prompt content, and gnarly scraping/clustering code can be read from it directly. Do NOT import from it; copy/port the bits you need so this project is self-contained.

Why we're rebuilding rather than refactoring in place:
- Claude-native orchestration (skills + subagents) replaces the OpenAI-Agents-SDK Tool plumbing.
- Lets us improve specific weak points without preserving back-compat: selection diversity (MMR), recency/authority weighting in rating, parallel critic-optimizer drafting, observability, resume.
- Forces a clean `call_prompt` abstraction instead of inheriting `llm.py`'s vendor-specific tangle.
- Python stays only where it earns its keep: Playwright scraping, embeddings, UMAP/HDBSCAN, Bradley-Terry math, dedup cosine similarity, SQLite, email.

SQLite (`newsletter_agent.db`) is the source of truth — same table layout as legacy (`urls`, `articles`, `sites`, `newsletters`, `agent_state`) so resume semantics carry over and an optional parity diff against the legacy code is possible.

## Architecture

```
~/projects/news_agent/
├── plugin.json
├── skills/                          # one SKILL.md per skill
│   ├── init/                                          # pipeline steps
│   ├── gather/        filter/       download/        summarize/
│   ├── rate/          cluster/      select/          draft/
│   ├── rewrite/       send/         bluesky/
│   ├── run/                         # top-level orchestrator
│   ├── status/        sessions/     show/            # state inspection
│   └── resume/        reset/        checkpoint/      diff/        gc/    # recovery / maintenance
├── lib/                             # Python helpers (CLI-callable per step)
│   ├── state.py                     # Pydantic state + SQLite persistence
│   ├── db.py                        # schema (urls, articles, sites, newsletters, agent_state)
│   ├── llm.py                       # unified call_prompt(engine=...) layer
│   ├── prompts/                     # one file per PromptConfig
│   └── steps/                       # gather.py filter.py ... (one main() each)
├── agents/                          # subagent prompt templates
│   ├── section-drafter.md
│   ├── section-critic.md
│   └── newsletter-critic.md
├── tests/
├── sources.yaml
├── dot-env.txt
└── newsletter_agent.db              # created on first run
```

**Key abstraction — `call_prompt`:** Every LLM call in the pipeline goes through a single helper:

```python
news.lib.llm.call_prompt(
    prompt_name: str,           # registered in lib/prompts/*.py
    inputs: dict | list[dict],  # single or batched
    engine: str = None,         # None=default; "subagent"|"anthropic:claude-..."|"openrouter:..."
    schema: type[BaseModel] = None,
)
```

Each `PromptConfig` declares its default engine. Users override per-invocation via skill flag (`--engine openrouter:google/gemini-2.5-pro`) or env var (`NEWS_PROMPT_<NAME>_ENGINE=...`). High-volume per-item prompts (filter, summarize, rate) are batched into chunks of N items and dispatched as K parallel subagents (or async SDK calls when `engine=openrouter:...`).

## Skills

### Per-step skills (one slash command each)

Each per-step skill is invocable on its own (`/news:filter --session SID`) and is also called in sequence by `news:run`. The "What it does" column is an at-a-glance overview of the work; "Notes / improvements vs. legacy" lists deltas from the reference implementation at `~/projects/OpenAIAgentsSDK/news_agent.py`.

| Skill | What it does | Python helper | LLM work | Notes / improvements vs. legacy |
|---|---|---|---|---|
| `news:init` | Create a new session row in `agent_state`, load `sources.yaml`, validate source configs, initialize the 12-step workflow, print a dry-run summary of what will be fetched. | `lib/steps/init.py` | none | Explicit session create (legacy did this implicitly in `run_agent.py`); validates `sources.yaml` before any network work. |
| `news:gather` | Fetch headlines/URLs from all configured RSS/HTML/REST sources in parallel, dedup against the `urls` table, write new URLs into the session's `headline_data`. | `lib/steps/gather.py` (port `fetch.py` + `scrape.py`) | none | Per-source success/failure report written to `runs/<session>/gather.json`; identify stale sources (0 hits N runs in a row). |
| `news:filter` | Classify each candidate headline as AI-relevant or not, drop duplicates of already-seen articles in the DB, mark borderline cases. | `lib/steps/filter.py` | classify AI-relevance (batched subagents, default Haiku) | Combine dedup-vs-DB and AI-classification in one pass; emit "borderline" set for human review. |
| `news:download` | Fetch full article HTML via Playwright/trafilatura for every kept URL, store extracted text under `download/`, dedup near-duplicates by embedding cosine similarity. | `lib/steps/download.py` (port `scrape.py` + `do_dedupe.py`) | none | Add a domain-level rate-limit guard and a cap on bytes/run. |
| `news:summarize` | Read each downloaded article, generate a bullet-point summary and a one-line headline, write both back to `headline_data`. | `lib/steps/summarize.py` | per-article bullet + 1-line headline (batched subagents, default Sonnet) | Produce **both** outputs in one call (single tightened `PromptConfig`); fall back to RSS summary when article scrape failed. |
| `news:rate` | Score each article on multiple axes (quality, on-topic, importance, spam) and run Swiss-paired Bradley-Terry battles to produce a composite rating. | `lib/steps/rate.py` (port `do_rating.py` — Swiss pairing + `choix.opt_pairwise`) | quality, on-topic, importance, spam, pairwise battles | **New signals**: spamminess classifier; recency bonus (decay over hours); source-authority bonus from `sites.reputation`. Combine via configurable weights in `lib/config.py`. Output single composite `rating` plus per-signal columns for debugging. |
| `news:cluster` | Embed articles, reduce to low-dim via UMAP, run HDBSCAN to assign cluster labels, name each cluster via LLM. | `lib/steps/cluster.py` (port `do_cluster.py`) | cluster naming only | No major change to the math; emit cluster-quality metrics (silhouette etc.) to `runs/<session>/cluster.json` for tuning. Requires `umap_reducer.pkl` (regenerate via legacy `Tune HDBSCAN.ipynb` if missing). |
| `news:select` | Assign HDBSCAN noise points to clusters, merge near-duplicate clusters, pick a diverse top-K of articles per section using MMR over embeddings+rating, write `newsletter_section_data`. | `lib/steps/select.py` | noise-point assignment + cluster merge + MMR diversity | **New vs legacy**: LLM assigns HDBSCAN noise points (label=-1) to existing clusters or proposes new ones; merge near-duplicate clusters; **MMR** on embeddings+rating to pick a diverse top-K per section. |
| `news:draft` | Spawn one subagent per section to write that section's narrative; each subagent runs an internal critic-optimizer loop (draft → critique → revise) up to `--max-edits` iterations. | `lib/steps/draft.py` (subagent dispatcher) | section drafts via parallel subagents with critic-optimizer | Dispatch one `section-drafter` per section in parallel; each runs an internal critic-optimizer loop with `section-critic`. The Claude Code parallel-agent sweet spot. |
| `news:rewrite` | Assemble all section drafts into a single newsletter, then critique the whole thing for flow/redundancy/headline punch and revise; produce `final_newsletter` HTML and `newsletter_title`. | `lib/steps/rewrite.py` | full-newsletter critic-optimizer | Single subagent assembles sections, then `newsletter-critic` reviews holistically; revise loop capped at `--max-edits`. |
| `news:send` | Render the final newsletter as styled HTML, write to `out/` with a date-stamped symlink, optionally send via Gmail (or just preview). | `lib/steps/send.py` (port `utilities.format_newsletter_email` + `send_gmail`) | none | Dry-run/preview mode by default; `--notify` to actually send. |
| `news:run` | Top-level orchestrator: sequences all per-step skills for a fresh or resumed session, with flags to start from / limit to specific steps and override LLM engines. | orchestrator skill body | n/a | Supports `--resume SID`, `--from STEP`, `--only STEP`, `--engine <override>`. |
| `news:bluesky` | Standalone Bluesky digest pipeline: pull recent atproto posts, enrich with OpenGraph metadata, resize images, LLM-reorder by importance, generate punny section titles, render HTML. | `lib/steps/bluesky.py` (port `Compose newsletter from BlueSky posts.ipynb`) | reorder + section-headers | Separate pipeline from the main newsletter; uses the same `call_prompt` layer. |

### State-inspection and recovery skills (read-mostly)

These skills wrap methods on the state class (`lib/state.py`) and `agent_state` DB helpers so users can observe and unblock sessions without dropping into Python. All take an optional `--session SID` (defaults to most recent).

| Skill | What it does | Python helper | Underlying state methods |
|---|---|---|---|
| `news:status` | Show the current step, progress percent, per-step status (complete/started/error/not-started), error messages, and headline/section counts for a session. Defaults to the most recent session. | `lib/steps/status.py` | `load_latest_from_db()`, `get_workflow_status_report()`, `get_status()` |
| `news:sessions` | List the N most recent sessions in `agent_state` with session id, started/updated timestamps, current step, completion %, and final-newsletter length. Supports `--limit N` and `--since DATE`. | `lib/steps/sessions.py` | `AgentState.list_recent_sessions()` (new helper: distinct `session_id` rows ordered by `updated_at DESC`) |
| `news:show <SID>` | Dump the full state record for a session: per-step timing, status messages, error messages, headline counts, cluster names, section list, and any saved artifacts under `runs/<SID>/`. Optional `--step STEP` to show a single step's checkpoint. | `lib/steps/show.py` | `load_from_db(step)`, `list_session_steps()`, `print_workflow_status()` |
| `news:resume <SID>` | Inspect a session, clear any ERROR steps back to NOT_STARTED, and re-enter `news:run --resume SID` starting at the first incomplete step. Prints what will be re-executed before proceeding (`--yes` to skip the prompt). | `lib/steps/resume.py` | `load_latest_from_db()`, `clear_errors()`, `get_current_step()`, `save_checkpoint()` |
| `news:reset` | Reset specific steps on a session so they can be re-run. `--errors` clears only ERROR steps; `--from STEP` resets a step and everything after it; `--all` calls `state.reset()`. Confirms before writing. Useful when a step succeeded but produced bad output. | `lib/steps/reset.py` | `clear_errors()`, `reset()`, direct step mutation + `save_checkpoint()` |
| `news:checkpoint <SID> <STEP>` | Manually persist the in-memory state for a session/step (rare; mostly an escape hatch when a step crashed after doing useful work but before its own checkpoint). | `lib/steps/checkpoint.py` | `serialize_to_db(step_name)`, `save_checkpoint()` |
| `news:diff <SID1> <SID2>` | Compare two sessions side-by-side: counts of URLs/articles/sections, overlap of selected articles, diff of final newsletter titles and sections. Used for the optional legacy-vs-new parity check. | `lib/steps/diff.py` | `load_latest_from_db()` on each session id |
| `news:gc` | Garbage-collect old sessions: with `--older-than DAYS` deletes `agent_state` rows and their `runs/<SID>/` scratch dirs. Dry-run by default; `--yes` to actually delete. Never touches `articles`, `urls`, `newsletters` (those are content, not state). | `lib/steps/gc.py` | `AgentState.delete_by_session` (new), filesystem `rm -rf runs/<SID>` |

### Orchestrator skill (`news:run`)

- No args → fresh session, run all 12 steps (`init → send`).
- `--resume SID` → load latest state for SID, resume from next incomplete step.
- `--from STEP` / `--only STEP` → step selection.
- `--nofetch`, `--concurrency`, `--max-edits`, `--timeout`, `--notify` → standard run options.
- `--engine <override>` and `--engine-<prompt> <override>` → forwarded to `call_prompt` for any LLM call in the run.

The orchestrator skill body is a checklist: invoke each child skill in order, abort if one returns non-zero, write `runs/<session>/summary.md` at end.

## Shared state and contracts

- **Source of truth:** `newsletter_agent.db`. Tables: `urls`, `articles`, `sites`, `newsletters`, `agent_state`. All skills read/write here; no JSON-as-state.
- **Per-run scratch dir:** `runs/<session_id>/` for human-readable artifacts (gather report, cluster metrics, draft critique transcripts). Not load-bearing — for observability/debugging.
- **State class:** `lib/state.py` ports `~/projects/OpenAIAgentsSDK/newsletter_state.py` — keep the `WorkflowStep` / `WorkflowState` / `NewsletterAgentState` hierarchy, the StepStatus enum, and the serialize/load/checkpoint methods. Drop the old `step_XX` migration code (we're starting clean).
- **Step contract:** each step skill
  1. reads from DB by `session_id`,
  2. writes outputs to the same `NewsletterAgentState` columns the per-step contract specifies,
  3. calls `state.serialize_to_db(step_name)` at the end,
  4. prints a one-paragraph human summary.
- **Idempotency:** re-running a step on the same session overwrites that step's outputs but leaves later steps untouched.

## Build phases

1. **Phase 0 — scaffold.** Create `plugin.json`, `lib/state.py` (port from legacy `newsletter_state.py`, drop migration code), `lib/db.py` (port schema only), `skills/init/SKILL.md`, `skills/status/SKILL.md`, `skills/sessions/SKILL.md`. Verify Claude Code loads the plugin and `news:init` + `news:status` work end-to-end on an empty DB.
2. **Phase 1 — `call_prompt` layer.** Build `lib/llm.py` with the engine-override layer. Implement the `"subagent"` engine first; stub OpenRouter / Anthropic SDK. Port one `PromptConfig` end-to-end (e.g. the AI-relevance classifier) to validate the contract.
3. **Phase 2 — non-LLM pipeline.** Build `gather`, `download`, `send` (and the inspection/recovery skills they enable: `show`, `resume`, `reset`). Port `fetch.py`, `scrape.py`, `do_dedupe.py`, `utilities.send_email` from legacy. End-to-end at this point: gather → download → (skip rest) → send dummy newsletter.
4. **Phase 3 — LLM steps.** Build `filter`, `summarize`, `rate` using `call_prompt`. Port one prompt at a time from `~/projects/OpenAIAgentsSDK/prompts.py` into `lib/prompts/`. Each becomes a single tightened `PromptConfig` with one combined output schema (avoid the legacy's parallel-variant prompts).
5. **Phase 4 — clustering + selection.** Build `cluster` (port `do_cluster.py` UMAP/HDBSCAN — needs `umap_reducer.pkl`) and `select` (the **new** MMR + LLM noise-assignment + cluster-merge logic; this is a step up from legacy's plain top-K-by-rating).
6. **Phase 5 — drafting.** Build `draft` (parallel subagent dispatcher: one `section-drafter` per section, each running a critic-optimizer loop with `section-critic`) and `rewrite` (single subagent assembles + `newsletter-critic` revise loop). Add the agent prompt templates under `agents/`.
7. **Phase 6 — engine flexibility + observability.** Wire OpenRouter / Anthropic SDK engines into `call_prompt`. Add the `--engine` and per-prompt env-var override paths. Emit cluster/quality metrics to `runs/<SID>/` for every step.
8. **Phase 7 — Bluesky.** Port the notebook (`~/projects/OpenAIAgentsSDK/Compose newsletter from BlueSky posts.ipynb`) into `news:bluesky` via the same `call_prompt` + helper pattern.
9. **Phase 8 — polish.** `news:diff`, `news:gc`, `news:checkpoint`, full test suite, README updates.

Each phase ships independently. After Phase 5 the system can produce a full newsletter end-to-end.

## Reference / port plan

| Behavior we need | Read from `~/projects/OpenAIAgentsSDK/` | Target location here |
|---|---|---|
| Pipeline workflow + step semantics | `news_agent.py`, `run_agent.py` | `skills/run/SKILL.md` (orchestrator) + per-step `SKILL.md`s + `lib/steps/*.py` |
| State schema + serialize/load/checkpoint | `newsletter_state.py` | `lib/state.py` (drop legacy `step_XX` migration code) |
| DB schema (urls/articles/sites/newsletters/agent_state) | `db.py` | `lib/db.py` |
| Source fetching (RSS / HTML / REST) | `fetch.py` | `lib/steps/gather.py` |
| Playwright scraping + content extraction | `scrape.py` | `lib/steps/download.py` |
| UMAP + HDBSCAN clustering | `do_cluster.py` | `lib/steps/cluster.py` |
| Bradley-Terry rating + Swiss pairing | `do_rating.py` (uses `choix.opt_pairwise`) | `lib/steps/rate.py` |
| Embedding cosine dedup | `do_dedupe.py` | `lib/steps/download.py` (called from there) |
| Email + HTML format + run summary | `utilities.py` | `lib/steps/send.py` |
| Prompt content (the biggest asset to port) | `prompts.py` | `lib/prompts/<name>.py` (one file per prompt; declare default engine + schema) |
| Vendor / rate-limit knowledge | `llm.py`, `config.py` (`MODEL_FAMILY`, `VENDOR_RPM_LIMITS`) | `lib/llm.py` (rewrite around `call_prompt`; keep async token-bucket for SDK paths) |
| Bluesky digest | `Compose newsletter from BlueSky posts.ipynb` | `lib/steps/bluesky.py` |
| Sources config | `sources.yaml` | **already copied** to `./sources.yaml` |
| Env template | `dot-env.txt` | **already copied** to `./dot-env.txt` |
| UMAP reducer | `umap_reducer.pkl` (414 MB, not in git) | regenerate via legacy `Tune HDBSCAN.ipynb` and place at `./umap_reducer.pkl` |

Things we are deliberately NOT porting:
- The `Tool`-class wrapper layer (`news_agent.py` step classes) — replaced by SKILL.md + `lib/steps/*.py main()` pairs.
- The OpenAI-Agents-SDK orchestrator loop — replaced by `news:run` skill.
- The legacy `step_XX → new_id` state-migration code in `newsletter_state.py._migrate_old_state_format` — we're starting clean.

## Verification

1. **Phase 0 sanity.** `news:init` creates a session; `news:sessions` lists it; `news:status` reports 0% progress and the first step as next. Re-running `news:init` creates a second session, both visible in `news:sessions`.
2. **Resume test.** Start `news:run`, kill mid-`cluster`, run `news:status` to confirm `cluster` is in STARTED/ERROR, then `news:resume <SID>` (or `news:reset --errors <SID>` + `news:run --resume <SID>`). Confirm only `cluster..send` re-execute and the final newsletter equals an uninterrupted run.
3. **Step isolation.** Run `news:filter --only` on a session whose `gather` is done; confirm it reads `headline_data`, writes `is_ai`, and doesn't touch downstream columns.
4. **Engine override.** `news:summarize --engine openrouter:google/gemini-2.5-pro` — confirm the prompt routes through the OpenRouter path while other steps stay on default.
5. **Subagent parallelism.** `news:draft` on a 10-section newsletter spawns ~10 concurrent `section-drafter` subagents (visible in Claude Code agent list); each writes a critic transcript to `runs/<SID>/sections/<n>/`.
6. **Bluesky.** `news:bluesky` on a fresh day produces an HTML file equivalent to the legacy notebook's `skynet.html` for the same input post set.
7. **Optional legacy parity.** Run `python ~/projects/OpenAIAgentsSDK/run_agent.py` and `claude code -p "/news:run"` against the same `sources.yaml` on the same day; use `news:diff` to compare. Up through Phase 5 we expect roughly equivalent article sets (modulo MMR diversity); from Phase 6 the new pipeline is intentionally divergent.
8. **Tests.** `pytest tests/` passes. Coverage target: `lib/state.py`, `lib/llm.py` engine-override layer, each `lib/steps/*` CLI on a fixture DB.

## Open questions to resolve early

These are not blockers but should be decided before Phase 2:

- **Engine identifiers.** Lock the `engine` string format: `"subagent"`, `"anthropic:claude-opus-4-7"`, `"openrouter:google/gemini-2.5-pro"`, `"cli:claude"` (legacy `*-cli` Max-plan path). Decide whether to support `engine=None` as "use PromptConfig default" or require explicit.
- **Subagent batch sizing.** For per-item prompts (filter, summarize, rate), pick default batch size N and parallelism K. Legacy uses ~10 concurrent. Subagents have different cost/latency tradeoffs.
- **`umap_reducer.pkl` provenance.** Decide whether to copy the 414 MB file from legacy, regenerate from scratch, or train a fresh one as part of Phase 4 (which means `cluster` is blocked until then).
- **Plugin scope.** Is this a project-local plugin (lives only in `~/projects/news_agent/`) or do we eventually publish it for cross-machine use? Affects `plugin.json` shape.
