# newsagent

Daily AI newsletter agent built as a Claude Code skill plugin (`newsagent:*`).

Gathers headlines from ~17 sources, filters by AI-relevance via LLM, downloads + summarizes articles, clusters by topic, scores via multi-axis ratings + Bradley-Terry pairwise battles, picks a diverse top-K per cluster (MMR with LLM noise-assignment and cluster-merge), and writes a polished newsletter through parallel critic-optimizer loops. Plus a standalone Beehiiv publishing pipeline.

**Status:** all build phases complete (`phase-0-complete` … `phase-8-complete`). 266 tests, ~89% coverage.

**Design doc:** [CLAUDE_REFACTOR.md](CLAUDE_REFACTOR.md) · **Phase plans:** [docs/superpowers/plans/](docs/superpowers/plans/) · **Conventions:** [CLAUDE.md](CLAUDE.md)

---

## Daily publishing workflow

The end-to-end daily loop, from raw sources to a ready-to-send beehiiv draft:

1. **`/newsagent:pipeline`** — Claude skill to run the full 12-step pipeline from scraping
   and gathering, to selecting items and writing proposed newsletter. Writes the newsletter to `out/latest.html` (fully AI-eddited issue) and the rated-bullet digest to `out/latest_short.html`
   (both also dated: `out/<date>.html`, `out/<date>_short.html`). Details under *How to run*.

3. **Post to Bluesky** — open the **local `file://` copy** of `out/latest.html`, start the
   share daemon (`python -m lib.steps.bsky_share`), and click the 🦋 link on each story from
   `latest.html` refined newsletter and `latest_short.html` bullet-point summaries you
   want to feature. Each posts to Bluesky as a link-preview card (post text = the title,
   card = the URL). Details under *Share newsletter items to Bluesky*. Bluesky is used a CMS
   to stage posts for Beehiiv.

5. **`/newsagent:bluesky`** — harvests the Bluesky druce.ai feed since the last run (per-handle dedup
   marker, capped at `--limit`), downloads + resize the post images, and reorders posts into
   topical groups ranked by a news-importance rubric. Writes `out/latest-bsky.html`. It also
   generates witty, pun-forward / alliterative hooks as a **separate**
   suggestion artifact (`runs/bsky-<handle>/titles.json`). Details under
   *Bluesky digest*.

6. **`/newsagent:beehiiv`** — Skill imports `out/latest-bsky.html` into a beehiiv
   **draft**, uploads every image and pastes the body (sections, links, descriptions, images in document order). You can
   apply suggested rewrites and do final edit in beehiiv. then **send from beehiiv
   yourself** — the skill only creates a draft, never publishes. Requires a
   `claude --chrome` session with a logged-in `app.beehiiv.com` tab. See
   [skills/beehiiv/README.md](skills/beehiiv/README.md). See also [final result after editing](https://skynetandchill.druce.ai/)

---

## Per-step reference

| Skill | What it does |
|---|---|
| `newsagent:start` | Create session, validate `sources.yaml`. |
| `newsagent:gather` | Fetch from RSS / HTML / REST. HTML uses adaptive scraping (httpx + BeautifulSoup → Playwright fallback, memoized in `sites.scrape_method`). |
| `newsagent:filter` | LLM-classify AI relevance; drop non-AI by default. |
| `newsagent:download` | Playwright fetch + trafilatura extract. |
| `newsagent:dedupe` | Cosine-similarity dedup on OpenAI embeddings (runs before `summarize` so duplicates aren't summarized). |
| `newsagent:summarize` | Per-article bullet summary. |
| `newsagent:rate` | Multi-axis confidence + Swiss-paired Bradley-Terry → composite rating. |
| `newsagent:cluster` | UMAP reduce → Optuna-tuned HDBSCAN → LLM cluster naming. |
| `newsagent:select` | LLM noise assignment → LLM cluster merge → MMR top-K per cluster. |
| `newsagent:draft` | Parallel section drafters; each runs a critic-optimizer loop. |
| `newsagent:rewrite` | Whole-newsletter critic-optimizer + title generation. |
| `newsagent:send` | Render HTML, write `out/<date>.html` + `out/latest.html`. |
| `newsagent:bluesky` | Standalone Bluesky digest. |
| `newsagent:beehiiv` | Import the Bluesky digest (`out/latest-bsky.html`) into a beehiiv draft via the Chrome extension. Draft only — never publishes. |
| `newsagent:pipeline` | Top-level orchestrator (`--resume`, `--from`, `--only`, `--engine`). |
| `newsagent:progress`, `sessions`, `show` | State inspection. |
| `newsagent:recover`, `reset` | Error recovery. |
| `newsagent:diff`, `gc`, `checkpoint` | Maintenance. |

---

## Quick start

```bash
# 1. Install Python deps
cp dot-env.txt .env                          # fill in API keys (optional, see below)
python -m venv .venv
.venv/bin/pip install -e ".[dev]"

# 2. Install Playwright's Firefox build (required — used by gather, download, and Bluesky image fetch).
#    Firefox is used instead of Chromium because Google SSO flags Chromium as insecure.
.venv/bin/python -m playwright install firefox

# 3. Get the pretrained UMAP reducer (~400 MB, gitignored)
cp ~/projects/OpenAIAgentsSDK/umap_reducer.pkl ./

# 4. Run the full pipeline (writes a newsletter to out/<date>.html)
.venv/bin/python -m lib.steps.pipeline --sources sources.yaml
```

## API keys

All optional except where you actually use the corresponding feature.

| Var | What for |
|---|---|
| `OPENROUTER_API_KEY` | OpenRouter engine (Gemini, DeepSeek, Llama, etc.) |
| `OPENAI_API_KEY` | OpenAI engine (GPT-4o, GPT-5, o-series) + embeddings (required for dedupe/cluster/select) |
| `GOOGLE_API_KEY` | Google engine (Gemini direct) |
| `NEWSAPI_API_KEY` | Only if the `NewsAPI` source is enabled in `sources.yaml` |
| `BSKY_USERNAME` / `BSKY_SECRET` | For `newsagent:bluesky` and the share-to-Bluesky daemon (`lib.steps.bsky_share`) |
| `BSKY_QUEUE_PORT` | Optional. Port for the share-to-Bluesky enqueue daemon (default `8765`); must match the port the newsletter's butterfly links point at |
| `GMAIL_USER` / `GMAIL_PASSWORD` | Only if you want the post-rate email digest (`rate` always writes the HTML; email send falls back to a warning when these are missing) |
| `BRIGHTDATA_API_KEY` (+ optional `BRIGHTDATA_ZONE`) | Only if any domain in the `sites` table has `bright_data_enabled=1` (paywall routing in `download`) |

The default engine for every prompt is `"subagent"` — that uses your Claude Code subscription (no API key). The other engines are opt-in.

**No Anthropic direct API.** Per project policy, Claude models route through the `subagent` engine, not `ANTHROPIC_API_KEY`.

---

## How to run

### Three ways: end-to-end, step-by-step, single-step

#### A. End-to-end (the orchestrator)

```bash
# Fresh full run — creates a new session, runs all 12 steps, writes out/<date>.html
.venv/bin/python -m lib.steps.pipeline --sources sources.yaml

# All steps with a specific engine forced for LLM prompts
.venv/bin/python -m lib.steps.pipeline --sources sources.yaml \
    --engine openrouter:google/gemini-2.5-flash

# Skip Gmail prompt (default — Phase 2 ships preview-only)
.venv/bin/python -m lib.steps.pipeline --sources sources.yaml --no-summary
```

When `run` finishes you get:
- `out/<YYYY-MM-DD>.html` — final newsletter, plus `out/latest.html` symlink
- `runs/<SID>/summary.md` — human-readable run report (table of step statuses, counts, draft critic transcripts)
- `runs/<SID>/*.json` — per-step artifacts (`gather.json`, `filter.json`, `cluster.json`, `draft.json`, etc.)

#### B. Step-by-step (run each step manually)

Useful when developing, debugging, or wanting to inspect state between steps.

```bash
# Create a session
.venv/bin/python -m lib.steps.start --sources sources.yaml --session demo-1
# → "Session created: demo-1"

# Fetch headlines from all enabled sources
.venv/bin/python -m lib.steps.gather --session demo-1
# → "Gathered N new headlines from M sources."

# Inspect what we just gathered
.venv/bin/python -m lib.steps.progress --session demo-1
.venv/bin/python -m lib.steps.show demo-1

# LLM-filter AI-relevant headlines
.venv/bin/python -m lib.steps.filter --session demo-1

# Download full article HTML, extract text via trafilatura
.venv/bin/python -m lib.steps.download --session demo-1 --max 50

# Summarize each article (bullet points)
.venv/bin/python -m lib.steps.summarize --session demo-1

# Cosine-similarity dedup on OpenAI embeddings
.venv/bin/python -m lib.steps.dedupe --session demo-1

# Rate each article (quality, on-topic, importance + Bradley-Terry).
# Also writes out/<date>_short.html + out/latest_short.html and emails the
# digest via Gmail SMTP (uses GMAIL_USER / GMAIL_PASSWORD; pass --no-email
# to skip sending, --to ADDR to override the recipient).
.venv/bin/python -m lib.steps.rate --session demo-1

# UMAP + HDBSCAN cluster + LLM cluster naming
.venv/bin/python -m lib.steps.cluster --session demo-1

# LLM noise assignment + cluster merge + MMR top-K
.venv/bin/python -m lib.steps.select --session demo-1 --k 5

# Parallel section drafters with critic-optimizer loop
.venv/bin/python -m lib.steps.draft --session demo-1 --max-edits 2 --parallelism 4

# Whole-newsletter critic-optimizer + title generation
.venv/bin/python -m lib.steps.rewrite --session demo-1 --max-edits 2

# Render HTML to out/
.venv/bin/python -m lib.steps.send --session demo-1
```

#### C. Single step on an existing session

```bash
# Rerun just one step (overwrites that step's outputs; later steps untouched)
.venv/bin/python -m lib.steps.filter --session demo-1
.venv/bin/python -m lib.steps.rate --session demo-1 --engine openai:gpt-4o-mini
```

### Bluesky digest (standalone)

```bash
# Requires BSKY_USERNAME + BSKY_SECRET env vars
.venv/bin/python -m lib.steps.bluesky --user yourhandle.bsky.social --limit 80
# → writes out/bsky-<date>.html
```

### Share newsletter items to Bluesky (queue + auto-poster)

Each item row in `out/latest.html` has a 🦋 link. Run the daemon, open the **local
`file://` copy** of the newsletter, and click 🦋 on any story — its title + URL are queued
and posted to your Bluesky as a link-preview card (post text = the title, card = the URL).

```bash
# Requires BSKY_USERNAME + BSKY_SECRET; serves the enqueue endpoint + drains the queue
.venv/bin/python -m lib.steps.bsky_share --interval 60
# --once  drains a single pending item and exits
# --port  overrides $BSKY_QUEUE_PORT (default 8765)
```

Notes: the 🦋 link only works from the local file (not the emailed copy) and only while the
daemon is running. Each URL is queued at most once, ever (permanent dedup). One item posts
per `--interval` for gentle rate-limiting; the queue lives in the `bsky_queue` table.

---

## How to check status

### Quick check (most recent session)

```bash
.venv/bin/python -m lib.steps.progress
```

Output:
```
Session:  2026-05-18-091533
Progress: 58.3% (7/12 steps)
Next:     cluster
Headlines: 412
Clusters:  0
Sections:  0

WORKFLOW
Progress: 58.3% (7/12 complete)
Next Step: Step 8: Cluster Topics
...
```

### List all recent sessions

```bash
.venv/bin/python -m lib.steps.sessions --limit 10
```

Output:
```
SESSION                      UPDATED                    STEP           PROGRESS
--------------------------------------------------------------------------------
2026-05-18-091533            2026-05-18T09:32:14.221      cluster        55%
2026-05-17-180012            2026-05-17T19:14:02.110      done           100%
2026-05-17-100503            2026-05-17T10:08:43.882      gather         18%
```

### Deep dive on one session

```bash
.venv/bin/python -m lib.steps.show 2026-05-18-091533
```

Dumps the full state record: workflow report, per-step checkpoints with timestamps, headline/cluster/section counts, newsletter title and length.

### Compare two sessions

```bash
.venv/bin/python -m lib.steps.diff 2026-05-17-180012 2026-05-18-091533
```

Shows side-by-side metrics and URL overlap (Jaccard).

---

## Recovering from errors

### A step errored mid-run

```bash
# See where it stopped
.venv/bin/python -m lib.steps.progress --session demo-1

# Clear error markers and print the next step to invoke
.venv/bin/python -m lib.steps.recover demo-1

# Re-enter the pipeline from the first incomplete step
.venv/bin/python -m lib.steps.pipeline --resume demo-1
```

### Force a specific step to redo

```bash
# Reset one step and everything after
.venv/bin/python -m lib.steps.reset demo-1 --from cluster --yes

# Or reset only error steps to NOT_STARTED
.venv/bin/python -m lib.steps.reset demo-1 --errors --yes

# Or reset everything (rare)
.venv/bin/python -m lib.steps.reset demo-1 --all --yes

# Then resume
.venv/bin/python -m lib.steps.pipeline --resume demo-1
```

### Continue with engine override

```bash
# E.g. the rate step keeps failing on subagent — try OpenRouter
.venv/bin/python -m lib.steps.rate --session demo-1 \
    --engine openrouter:google/gemini-2.5-flash
```

### Garbage-collect old sessions

```bash
# Dry-run (shows what would be deleted)
.venv/bin/python -m lib.steps.gc --older-than 30

# Actually delete
.venv/bin/python -m lib.steps.gc --older-than 30 --yes
```

Never touches `articles`, `urls`, `newsletters`, `sites` tables — those are content, not state.

---

## Pipeline reference

```
start → gather → filter → download → dedupe → summarize → rate → cluster → select → draft → rewrite → send
```

Plus the standalone Bluesky digest and recovery/maintenance skills.

---

## Engine configuration

Each `PromptConfig` declares its own `default_engine` and `reasoning_effort` (0–10). Resolution precedence:

1. `--engine <id>` CLI arg (per step)
2. `NEWS_PROMPT_<NAME>_ENGINE=<id>` env var (per prompt)
3. `PromptConfig.default_engine`
4. `"subagent"` (implicit fallback)

Engine identifier formats:

| Identifier | Auth |
|---|---|
| `subagent` | None — runs `claude -p` subprocess |
| `openrouter:<model>` | `OPENROUTER_API_KEY` |
| `openai:<model>` | `OPENAI_API_KEY` |
| `google:<model>` | `GOOGLE_API_KEY` |

`reasoning_effort` maps to provider-specific knobs:
- **OpenRouter:** `extra_body.reasoning.effort` (low/medium/high or disabled at 0)
- **OpenAI:** `reasoning_effort` API param (minimal/low/medium/high), silently dropped on non-reasoning models
- **Google:** `thinking_config.thinking_budget` (0 / 2048 / 8192 / 24576 tokens)
- **Subagent:** embedded as `"Reasoning effort: N/10"` in the prompt

### Per-prompt override examples

```bash
# One engine for all LLM prompts in a single run
.venv/bin/python -m lib.steps.pipeline --engine openai:gpt-4o-mini

# Override a single prompt globally via env var
NEWS_PROMPT_RATE_QUALITY_ENGINE=openai:gpt-4o-mini \
  .venv/bin/python -m lib.steps.pipeline

# Stack overrides (rate uses Gemini, everything else default subagent)
NEWS_PROMPT_RATE_QUALITY_ENGINE=google:gemini-2.5-flash \
NEWS_PROMPT_RATE_ON_TOPIC_ENGINE=google:gemini-2.5-flash \
NEWS_PROMPT_RATE_IMPORTANCE_ENGINE=google:gemini-2.5-flash \
  .venv/bin/python -m lib.steps.pipeline
```

---

## Architecture

- **SQLite source of truth:** `newsletter_agent.db` with tables `urls`, `articles`, `sites`, `newsletters`, `agent_state`. Every step checkpoints to `agent_state` keyed by `(session_id, step_name)`.
- **Pydantic state:** `NewsletterAgentState` extends a generic `WorkflowState` (12 ordered steps with status + timing).
- **Single LLM entry point:** `lib/llm.call_prompt(prompt_name, inputs, engine=...)`. Engines live in `lib/engines/`.
- **Per-prompt config:** each prompt is a separate module under `lib/prompts/` declaring model + reasoning_effort + schema.
- **Adaptive HTML scraping:** httpx first, Playwright on failure; per-site working method cached in `sites.scrape_method`. Parser differs by step — `gather` uses BeautifulSoup to harvest `<a>` tags from landing pages; `download` uses trafilatura to extract the main article body.
- **Critic-optimizer loops** (`lib/critic.py`): generic helper used by both `draft` (per section, parallel) and `rewrite` (whole newsletter).

## Layout

```
newsagent/
├── .claude-plugin/
│   ├── plugin.json
│   └── marketplace.json
├── lib/
│   ├── state.py              # NewsletterAgentState + WorkflowState
│   ├── db.py                 # SQLite schema + dataclasses
│   ├── llm.py                # call_prompt, registry
│   ├── critic.py             # critic-optimizer loop
│   ├── rating.py             # Swiss pairing + Bradley-Terry
│   ├── clustering.py         # UMAP + HDBSCAN + Optuna
│   ├── mmr.py                # MMR diversity selection
│   ├── embeddings.py         # OpenAI text-embedding-3-large
│   ├── run_summary.py        # runs/<SID>/summary.md generator
│   ├── config.py             # rating weights
│   ├── engines/              # subagent, openrouter, openai_chat, google
│   ├── fetch/                # RSS, HTML (adaptive), REST, Playwright runner
│   ├── prompts/              # ~17 PromptConfigs (one per file)
│   ├── steps/                # one CLI per workflow + recovery skill
│   └── bluesky/              # api, og_tags, images
├── skills/                   # SKILL.md per slash command (21 skills)
├── agents/                   # persona reference docs
├── tests/                    # 266 tests, ~89% coverage
├── docs/superpowers/plans/   # one phase plan per build phase
├── sources.yaml              # source feeds
├── umap_reducer.pkl          # 400 MB pretrained UMAP (gitignored)
└── newsletter_agent.db       # SQLite source of truth
```

## Tests

```bash
.venv/bin/pytest tests/                              # all
.venv/bin/pytest tests/ --cov=lib --cov-report=term  # with coverage
.venv/bin/pytest tests/test_step_filter.py -v        # one file
```

## Tech stack

Python 3.11 · Pydantic v2 · SQLite (stdlib) · Click · httpx · trafilatura · feedparser · BeautifulSoup · Playwright · OpenAI SDK · google-genai · UMAP · HDBSCAN · Optuna · scikit-learn · choix · Pillow · pytest + respx

## License

MIT

