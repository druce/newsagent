# news_agent

Daily AI newsletter agent built as a Claude Code skill plugin (`news:*`).

Gathers headlines from ~17 sources, filters by AI-relevance via LLM, downloads + summarizes articles, clusters by topic, scores via multi-axis ratings + Bradley-Terry pairwise battles, picks a diverse top-K per cluster (MMR with LLM noise-assignment and cluster-merge), and writes a polished newsletter through parallel critic-optimizer loops. Plus a standalone Bluesky digest pipeline.

**Design doc:** [CLAUDE_REFACTOR.md](CLAUDE_REFACTOR.md) — full architectural spec.
**Phase plans:** [docs/superpowers/plans/](docs/superpowers/plans/) — one plan per build phase.

## Quick start

```bash
# 1. Install
cp dot-env.txt .env                          # fill in API keys
python -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/python -m playwright install chromium

# 2. Get the pretrained UMAP reducer (~400 MB, gitignored)
cp ~/projects/OpenAIAgentsSDK/umap_reducer.pkl ./

# 3. Run the full pipeline
.venv/bin/python -m lib.steps.run --sources sources.yaml
```

## API keys

All optional except where you actually use the corresponding feature.

| Var | What for |
|---|---|
| `OPENROUTER_API_KEY` | OpenRouter engine (Gemini, DeepSeek, Llama, etc.) |
| `OPENAI_API_KEY` | OpenAI engine (GPT-4o, GPT-5, o-series) + embeddings (required for dedupe/cluster/select) |
| `GOOGLE_API_KEY` | Google engine (Gemini direct) |
| `NEWSAPI_API_KEY` | Only if the `NewsAPI` source is enabled in `sources.yaml` |
| `BSKY_USERNAME` / `BSKY_SECRET` | Only for `news:bluesky` |

The default engine for every prompt is `"subagent"` — that uses your Claude Code subscription (no API key). The other engines are opt-in.

**No Anthropic direct API.** Per project policy, Claude models route through the `subagent` engine, not `ANTHROPIC_API_KEY`.

## Pipeline

```
init → gather → filter → download → summarize → rate → cluster → select → draft → rewrite → send
```

Plus a standalone Bluesky digest (`news:bluesky`) and seven recovery/maintenance skills.

### Per-step reference

| Skill | Phase | What it does |
|---|---|---|
| `/news:init` | 0 | Create session, validate `sources.yaml`. |
| `/news:gather` | 2 | Fetch from RSS/HTML/REST. HTML uses adaptive scraping (trafilatura+httpx → Playwright fallback, memoized in `sites.scrape_method`). |
| `/news:filter` | 3 | LLM-classify AI relevance; drop non-AI by default. |
| `/news:download` | 2 | Playwright fetch + trafilatura extract. |
| `/news:summarize` | 3 | Per-article bullet summary. |
| `/news:dedupe` | 3 | Cosine-similarity dedup on OpenAI embeddings (`text-embedding-3-large`). |
| `/news:rate` | 3 | Multi-axis confidence (quality/on-topic/importance) + Swiss-paired Bradley-Terry battles → composite rating. |
| `/news:cluster` | 4 | UMAP reduce → Optuna-tuned HDBSCAN → LLM cluster naming. |
| `/news:select` | 4 | LLM noise assignment → LLM cluster merge → MMR top-K per cluster. |
| `/news:draft` | 5 | Parallel section drafters; each runs a critic-optimizer loop (`write_section → critique_section → improve_section`). |
| `/news:rewrite` | 5 | Whole-newsletter critic-optimizer loop + title generation. |
| `/news:send` | 2 | Render HTML, write `out/<date>.html` + `out/latest.html`. Gmail send not in this version. |
| `/news:bluesky` | 7 | Standalone Bluesky digest: feed → OG enrichment → LLM reorder → LLM section titles → HTML. |
| `/news:run` | 6 | Top-level orchestrator. Sequences all 11 steps with `--resume`, `--from`, `--only`, `--engine` flags. |
| `/news:status`, `sessions`, `show` | 0/2 | State inspection. |
| `/news:resume`, `reset` | 2 | Error recovery. |
| `/news:diff`, `gc`, `checkpoint` | 8 | Maintenance. |

## Common workflows

```bash
# Full run, fresh session
.venv/bin/python -m lib.steps.run --sources sources.yaml

# Resume a session that errored mid-pipeline
.venv/bin/python -m lib.steps.resume SID
.venv/bin/python -m lib.steps.run --resume SID

# Rerun one step on an existing session
.venv/bin/python -m lib.steps.filter --session SID

# Force one engine for all LLM calls in a run
.venv/bin/python -m lib.steps.run --engine openrouter:google/gemini-2.5-flash

# Override a single prompt's engine
NEWS_PROMPT_RATE_QUALITY_ENGINE=openai:gpt-4o-mini \
  .venv/bin/python -m lib.steps.run

# Compare two runs
.venv/bin/python -m lib.steps.diff SID1 SID2

# Garbage-collect sessions older than 30 days
.venv/bin/python -m lib.steps.gc --older-than 30 --yes
```

## Engine configuration

Each `PromptConfig` declares its own `default_engine` and `reasoning_effort` (0–10 scale). Resolution precedence:

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
- OpenRouter: `extra_body.reasoning.effort` (low/medium/high or disabled at 0)
- OpenAI: `reasoning_effort` API param (minimal/low/medium/high), silently dropped on non-reasoning models
- Google: `thinking_config.thinking_budget` (0 / 2048 / 8192 / 24576 tokens)
- Subagent: embedded as `"Reasoning effort: N/10"` in the prompt

## Architecture

- **SQLite source of truth:** `newsletter_agent.db` with tables `urls`, `articles`, `sites`, `newsletters`, `agent_state`. Every step checkpoints to `agent_state` keyed by `(session_id, step_name)`.
- **Pydantic state:** `NewsletterAgentState` extends a generic `WorkflowState` (11 ordered steps with status + timing).
- **Single LLM entry point:** `lib/llm.call_prompt(prompt_name, inputs, engine=...)`. Engines live in `lib/engines/`.
- **Per-prompt config:** each prompt is a separate module under `lib/prompts/` declaring model + reasoning_effort + schema.
- **Adaptive HTML scraping:** httpx + trafilatura first, Playwright on failure; per-site working method cached in `sites.scrape_method`.

## Layout

```
news_agent/
├── plugin.json
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
│   ├── engines/              # openrouter, openai, google, subagent
│   ├── fetch/                # RSS, HTML (adaptive), REST, Playwright runner
│   ├── prompts/              # PromptConfig per file (~20 prompts)
│   ├── steps/                # one CLI per workflow step
│   └── bluesky/              # api, og_tags, images
├── skills/                   # SKILL.md per slash command (~20 skills)
├── agents/                   # persona reference docs
├── tests/                    # ~270 tests, ~90% coverage
├── sources.yaml              # source feeds
├── umap_reducer.pkl          # 400 MB, gitignored
└── newsletter_agent.db       # SQLite source of truth
```

## Tech stack

Python 3.11 · Pydantic v2 · SQLite (stdlib) · Click · httpx · trafilatura · feedparser · BeautifulSoup · Playwright · OpenAI SDK · google-genai · UMAP · HDBSCAN · Optuna · scikit-learn · choix · Pillow · pytest + respx

## License

MIT
