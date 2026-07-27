# CLAUDE.md — newsagent

## What this project is

A daily AI newsletter agent, built from scratch as a `newsagent:*` Claude Code skill plugin. **All 9 build phases (0–8) are complete.** Tags: `phase-0-complete` through `phase-8-complete`.

The legacy implementation at `~/projects/OpenAIAgentsSDK/` is read-only reference — used during porting for prompt text, scraping logic, and Bradley-Terry math. Do not import from it.

**Design spec:** [CLAUDE_REFACTOR.md](CLAUDE_REFACTOR.md) — the original architectural plan (mostly historical now).
**Per-phase plans:** `docs/superpowers/plans/` — one detailed implementation plan per phase, with full code and tests. Kept locally only; not tracked in the repo.
**Top-level overview:** [README.md](README.md) — end-user docs.

## Hard constraints (locked in via memory)

These were set by the user during the build and are non-negotiable in future work:

1. **No Anthropic direct API.** No `anthropic` SDK, no `ANTHROPIC_API_KEY`. Claude models route through the `subagent` engine (which subprocesses `claude -p` against the user's Claude Code subscription). Allowed engines: `subagent`, `openrouter:<m>`, `openai:<m>`, `google:<m>`.
2. **Embeddings = OpenAI `text-embedding-3-large`.** Matches the legacy `umap_reducer.pkl` dimensions. Do not substitute another provider.
3. **PromptConfig per legacy style.** Each prompt binds to a specific `default_engine` and `reasoning_effort` (0–10 scale). Subagent is the implicit fallback default if not specified.
4. **Adaptive HTML scraping (runtime fallback) + operator-curated pins.** Each step adapts at fetch time: `gather` tries `httpx + BeautifulSoup` first (extracts `<a>` tags from landing pages), falls back to Playwright on failure; `download` follows the same `httpx → Playwright` shape but uses trafilatura on article pages. **`sites.scrape_method` is operator-curated config (a hard pin), NOT a heuristic cache** — gather/download never auto-write it. When httpx fails and Playwright recovers, the step logs a "consider pinning" notice; the operator decides whether to set `UPDATE sites SET scrape_method='playwright' WHERE domain=...`. Aggregator hosts with JS-based redirects (`news.google.com`, `t.co`, `lnkd.in`, `link.medium.com`, `l.facebook.com`, `out.reddit.com`) get an extra 10s `wait_for_url` after DCL in Playwright so the redirect resolves before we snapshot HTML. Domains marked `sites.bright_data_enabled=1` (Bloomberg, WSJ, CNN, Forbes, Fast Company by default) skip httpx/Playwright and route through the Bright Data Web Unlocker (`BRIGHTDATA_API_KEY` required, async + Semaphore(8)); URLs that fail both http and Playwright also get a final BD attempt as a fallback.

See `~/.claude/projects/-Users-drucev-projects-newsagent/memory/` for the full memory store.

## Reading the reference implementation

When tweaking ported behavior, the legacy implementation at `~/projects/OpenAIAgentsSDK/` is still the source of truth on intent. See the `legacy-reference` skill for the topic→file map.

Do not import from that path; do not modify it.

## Workflow

`start → gather → filter → download → dedupe → summarize → crossdedupe → coverage → rate → cluster → select → draft → rewrite → send`

Plus the standalone `newsagent:bluesky` and seven recovery/maintenance skills.

`dedupe` runs between `download` and `summarize` so duplicate articles aren't summarized twice. It's a full member of `WORKFLOW_STEPS` (see `lib/state.py:25`).

The three de-duplication/coverage steps are easy to confuse — `dedupe` is within-session syndicated-reprint removal on full bodies, `crossdedupe` is cross-day story-level suppression against `published_articles`, `coverage` counts same-day duplicate reporting without dropping anything. Details in `skills/crossdedupe/SKILL.md` and `skills/coverage/SKILL.md`.

## Engine layer

`lib.llm.call_prompt(name, inputs, engine=...)` is the only entry point. Resolution precedence:
1. `engine=` kwarg
2. `NEWS_PROMPT_<NAME>_ENGINE` env var
3. `PromptConfig.default_engine`
4. `"subagent"` (implicit)

Each engine accepts `reasoning_effort: int = 4` and maps it to provider-specific knobs (see `lib/engines/*.py`).

`call_prompt_batch` runs the same prompt over many inputs in parallel via `ThreadPoolExecutor` (both engines are blocking I/O).

## Critic-optimizer loop

`lib/critic.py:critic_optimizer_loop` is the generic helper. Both `newsagent:draft` (per section, parallel via ThreadPoolExecutor) and `newsagent:rewrite` (whole newsletter) use it. Short-circuits when critic returns `accept=True` OR `score >= 8.0`. Default `max_edits=2`.

## State and data storage

- `newsletter_agent.db` — SQLite source of truth. Tables: `urls`, `articles`, `sites` (with `scrape_method`), `newsletters`, `agent_state`, `bsky_queue`, `published_articles`. Every step checkpoints to `agent_state` keyed by `(session_id, step_name)`. `bsky_queue` backs the share-to-Bluesky daemon (`url` UNIQUE → permanent dedup). `published_articles` records what each newsletter actually published (url, title, short_summary, `title+short_summary` embedding, `published_at`) — written by `send`, read by `crossdedupe` for cross-day story-level suppression.
- `download/` — article text cache (sha256-of-url filename).
- `download/bsky-images/` — Bluesky image cache.
- `runs/<SID>/` — per-step JSON artifacts (gather.json, filter.json, cluster.json, draft.json, etc.) + `summary.md` from `newsagent:pipeline`.
- `out/YYYY-MM-DD.html` — final newsletter (+ `out/latest.html` symlink).
- `out/YYYY-MM-DD_short.html` — rated-digest bullets written by `rate` (+ `out/latest_short.html` symlink).
- `out/bsky-YYYY-MM-DD.html` — Bluesky digest (+ `out/latest-bsky.html`).

## Environment

Copy `dot-env.txt` to `.env` — it lists every key. All are optional except the ones you actually use. Non-obvious ones:

- `BSKY_QUEUE_PORT` — optional, port for the share-to-Bluesky enqueue daemon (default `8765`). `send.py` bakes this port into the 🦋 enqueue links, so render-time and runtime must agree.
- `GMAIL_USER` / `GMAIL_PASSWORD` — only if you want the post-rate email digest. `rate` writes `out/<date>_short.html` + `out/latest_short.html` unconditionally; the email send is auto-attempted but degrades to a warning when these are missing. Use `--no-email` to skip explicitly.

**Do NOT set or expect `ANTHROPIC_API_KEY`.** Claude models go through the `subagent` engine via your Claude Code subscription.

## Commands

Every step is `.venv/bin/python -m lib.steps.<step>` (see `lib/steps/` for the list, `--help` for flags). The non-obvious ones:

```bash
# Orchestrator — fresh run, or resume
.venv/bin/python -m lib.steps.pipeline --sources sources.yaml
.venv/bin/python -m lib.steps.pipeline --resume SID
.venv/bin/python -m lib.steps.pipeline --from cluster --session SID

# Install (Playwright browser is a separate step)
.venv/bin/pip install -e ".[dev]"
.venv/bin/python -m playwright install firefox
```

See [README.md](README.md) for full end-user workflows and engine override examples.

## Conventions

- **One `PromptConfig` per file** under `lib/prompts/`. Each declares `default_engine`, `reasoning_effort`, and Pydantic input/output schemas.
- **Each step CLI** is a Click command at `lib/steps/<step>.py:cli`. The `cli` is what gets invoked when you `python -m lib.steps.<step>`.
- **SKILL.md** is the agent-facing contract under `skills/<name>/SKILL.md`. Python logic lives in `lib/steps/<name>.py`.
- **Idempotency:** re-running a step on the same session overwrites that step's outputs but leaves later steps untouched.
- **Per-run artifacts** go to `runs/<SID>/<step>.json`. Not load-bearing; for observability.
- **TDD discipline:** every implementation task in the phase plans follows write-tests → confirm-fail → implement → confirm-pass → commit.
- **`.venv/bin/pytest`** not system pytest. Use the project virtualenv.
- **Pyright "could not be resolved" warnings** are an IDE config issue, not real bugs. Tests pass via pytest.
- **Never edit `~/projects/OpenAIAgentsSDK/`.** It's read-only reference.
