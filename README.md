# newsagent

Daily AI newsletter agent built as a Claude Code skill plugin (`newsagent:*`).

Gathers headlines from ~18 sources, filters by AI-relevance via LLM, downloads + summarizes articles, drops stories already published in recent days, boosts stories with wide same-day coverage, scores via multi-axis ratings + Bradley-Terry pairwise battles, clusters by topic, and picks a diverse top-K (MMR with LLM noise-assignment and cluster-merge). You curate the picks on a local page, then `edition` drafts the issue in house style and imports it into a beehiiv draft for final human editing.

**Status:** all build phases complete (`phase-0-complete` … `phase-8-complete`), plus curate / edition / fix / GUI since. 875 tests, ~87% coverage.

**Design doc:** [CLAUDE_REFACTOR.md](CLAUDE_REFACTOR.md) (mostly historical) · **Conventions:** [CLAUDE.md](CLAUDE.md) · **House style:** [docs/style/](docs/style/)

---

## Daily publishing workflow

1. **`/newsagent:pipeline`** — runs the 14-step pipeline, from scraping to a machine-written
   newsletter. Run it from a Claude Code session: it dispatches parallel Agents for the LLM steps.
   Its outputs:
   - every rated article goes into the `published_articles` table, with the MMR picks marked
     `selected=1` (the input to step 2)
   - `out/latest_short.html`: the rated-bullet digest, written by `rate`
   - `out/latest.html`: the fully AI-written issue, written by `send`

   Both are also stamped `out/<stamp>_short.html` and `out/<stamp>.html`, where `<stamp>` is the
   session start time, `YYYY-MM-DD-HHMMSS`. Run it as often as you like (e.g. morning and evening).
   Details under *How to run*.

2. **Curate** — `.venv/bin/python -m lib.steps.curate` opens http://127.0.0.1:8766/, a page over
   `published_articles` for every session since noon yesterday. **Select** toggles a story in or
   out of the issue. **✎ Edit** fixes the URL, headline or source, **Delete** removes the row, and
   **+ Add story** adds your own. Details under *Curate stories*.

3. **`/newsagent:edition`** — drafts the issue in house style from your selected stories:
   Opus agents merge categories, write each item (kicker + one-sentence summary + source, plus
   alternative kickers), then do a final edit (order, copy edit, section titles, subtitle). It
   fetches a candidate image per item and writes `out/edition-<stamp>.md`
   (+ `out/latest-edition.md`). It then runs **`/newsagent:finalize edition`** automatically,
   which imports the markdown into a **beehiiv draft**. That needs a `claude --chrome` session
   with a logged-in `app.beehiiv.com` tab. Details under *Draft the issue from your picks*.

4. **Human editing in beehiiv** — choose among the alternative kickers and titles, delete the
   `- Label:` note bullets and unwanted images, and rewrite whatever needs it. Edit the beehiiv
   draft, not the local `.md`: the draft is the source of truth from here on.

5. **Final AI review: CUT, CHECK, FIX** — before sending:
   - **CUT:** an AI pass over the draft that flags stories to cut, using the selection rules in
     [house-style.md §5](docs/style/house-style.md) (vendor PR, small funding rounds,
     duplicates, …).
   - **CHECK:** an AI pass that proofs against the pre-send checklist in
     [house-style.md §6](docs/style/house-style.md) (subtitle, names, source labels, broken
     markdown, grammar, tracking params). It produces a list of fixes.
   - **FIX:** paste that list into **`/newsagent:fix`**. It applies each find → replace directly
     to the live beehiiv draft and keeps link/italic marks, so edited link text stays linked.
     It never touches the local `.md` and never publishes.

   Then **send from beehiiv yourself**. No skill publishes or schedules.
   See the [published newsletter](https://skynetandchill.druce.ai/).

The older Bluesky-based roundup (🦋 share → `/newsagent:bluesky` → `/newsagent:beehiiv`, or
`/newsagent:finalize` with no argument) still works; see *Bluesky tools* below.

---

## Per-step reference

| Skill | What it does |
|---|---|
| `newsagent:start` | Create session, validate `sources.yaml`. |
| `newsagent:gather` | Fetch from RSS / HTML / REST. HTML uses adaptive scraping (httpx + BeautifulSoup → Playwright fallback). `sites.scrape_method` is an operator-set pin, never auto-written. |
| `newsagent:filter` | LLM-classify AI relevance (parallel Haiku subagents); drop non-AI by default. |
| `newsagent:download` | httpx → Playwright fetch + trafilatura extract; Bright Data Web Unlocker for `bright_data_enabled` domains and as a last-resort fallback. |
| `newsagent:dedupe` | Cosine-similarity dedup (threshold 0.95) on OpenAI embeddings of full bodies, to remove syndicated reprints. Runs before `summarize` so duplicates aren't summarized. |
| `newsagent:summarize` | Per-article bullet summary + ≤25-word one-line headline. |
| `newsagent:crossdedupe` | Drop stories already published in the last N days (default 4), even from a different outlet/URL — cosine shortlist on `title+short_summary` embeddings + an Opus same-story judge. |
| `newsagent:coverage` | Count same-day articles reporting the same event; `rate` boosts each by log2 of the group size. Drops nothing. |
| `newsagent:rate` | Yes/no predicates per axis (quality, on-topic, importance) via the OpenAI Decisions API + Swiss-paired Bradley-Terry battles → composite rating. Writes the `_short.html` digest and emails it. |
| `newsagent:cluster` | UMAP reduce → Optuna-tuned HDBSCAN → LLM cluster naming. |
| `newsagent:select` | LLM noise mining → cluster-name consolidation → reassignment → global MMR top-K (default 60). Writes `published_articles`. |
| `newsagent:draft` | Parallel section drafters; each runs a critic-optimizer loop. |
| `newsagent:rewrite` | Whole-newsletter critic-optimizer + title generation. |
| `newsagent:send` | Render HTML, write `out/<stamp>.html` + `out/latest.html`, stamp `published_at`, email via Gmail. |
| `newsagent:pipeline` | Top-level orchestrator for the 14 steps above. |
| `newsagent:edition` | House-style issue draft from curated picks → `out/edition-<stamp>.md` → beehiiv draft. |
| `newsagent:finalize` | `finalize edition`: import `out/latest-edition.md` into beehiiv. No argument: run `bluesky` then `beehiiv`. Chrome/beehiiv-login pre-flight first; never falls back to a headless browser. |
| `newsagent:fix` | Apply a pasted copy-edit fix list to the live beehiiv draft. |
| `newsagent:beehiiv` | Import the Bluesky digest (or an edition `.md`) into a beehiiv draft via the Chrome extension. Draft only — never publishes. |
| `newsagent:bluesky` | Standalone Bluesky digest. |
| `newsagent:gui` | Local pipeline GUI with live diagram, logs, and chat/run buttons. |
| `newsagent:progress`, `sessions`, `show` | State inspection. |
| `newsagent:recover`, `reset`, `checkpoint` | Error recovery. |
| `newsagent:diff`, `gc`, `backfill-published` | Maintenance. |

Curation (`lib.steps.curate`) and the Bluesky share daemons (`lib.steps.bsky_share`,
`lib.steps.bsky_share_v2`) are plain CLIs with no skill.

---

## Quick start

```bash
# 1. Install Python deps — Python 3.11 only (3.12+ breaks unpickling umap_reducer.pkl)
cp dot-env.txt .env                          # fill in API keys (see below)
python3.11 -m venv .venv
.venv/bin/pip install -e ".[dev]"

# 2. Install Playwright's Firefox build (required — used by gather, download, and Bluesky image fetch).
#    Firefox is used instead of Chromium because Google SSO flags Chromium as insecure.
.venv/bin/python -m playwright install firefox

# 3. Get the pretrained UMAP reducer (~400 MB, gitignored)
cp ~/projects/OpenAIAgentsSDK/umap_reducer.pkl ./

# 4. Run the pipeline from a Claude Code session in this repo
/newsagent:pipeline
```

## Systemwide config

Systemwide settings live in `config.yaml` (separate from `sources.yaml`,
which defines feed sources). `blocked_domains` lists content-farm domains
to drop everywhere — checked against the initial URL at gather time and
re-checked against the final URL at download time, so aggregator redirects
(Google News, HN links) can't smuggle a banned domain through.

## API keys

All optional except where you actually use the corresponding feature.

| Var | What for |
|---|---|
| `OPENAI_API_KEY` | Embeddings (required for dedupe/crossdedupe/coverage/cluster/select) + the Decisions API rating axes in `rate` + `openai:` engine |
| `GOOGLE_API_KEY` | Google engine — default for Bradley-Terry battles, the classic-mode coverage/crossdedupe judges, and site-name lookup |
| `OPENROUTER_API_KEY` | OpenRouter engine (Gemini, DeepSeek, Llama, etc.), opt-in |
| `NEWSAPI_API_KEY` | Only if the `NewsAPI` source is enabled in `sources.yaml` |
| `BSKY_USERNAME` / `BSKY_SECRET` | For `newsagent:bluesky` and the share-to-Bluesky daemons |
| `BSKY_QUEUE_PORT` | Optional. Port for the share-to-Bluesky enqueue daemon (default `8765`); must match the port the newsletter's butterfly links point at |
| `CURATE_PORT` / `CURATE_HOST` | Optional. Port / bind address for the curation page (default `8766` / `127.0.0.1`) |
| `GUI_PORT` | Optional. Port for the pipeline GUI (default `8767`) |
| `GMAIL_USER` / `GMAIL_PASSWORD` | Only if you want the emailed digest (`rate`) and newsletter (`send`); both fall back to a warning when these are missing |
| `BRIGHTDATA_API_KEY` (+ optional `BRIGHTDATA_ZONE`) | Paywall routing in `download` for domains with `sites.bright_data_enabled=1`, and the last-resort fallback |

**No Anthropic direct API.** Per project policy, Claude models run as Agents dispatched by the
skills inside your Claude Code session (or, in classic mode, the `subagent` engine). Never
`ANTHROPIC_API_KEY`.

---

## How to run

### Interactive vs. classic mode

The LLM steps (filter, summarize, crossdedupe, coverage, cluster, select, draft, rewrite) have
two modes:

- **Interactive (the normal path):** the skill runs `--prepare-batches`, dispatches parallel
  Claude Agents from your Claude Code session over the batch files, then runs
  `--apply-results <dir>`. `/newsagent:pipeline` and the per-step `/newsagent:<step>` skills do
  this for you.
- **Classic:** run the step CLI without those flags and it calls each prompt through its
  configured engine in-process (see *Engine configuration*). The fallback `subagent` engine
  shells out to `claude -p`.

### A. End-to-end

```bash
/newsagent:pipeline                      # fresh run (interactive)
/newsagent:pipeline --resume SID         # pick up from the first incomplete step
/newsagent:pipeline --from cluster --session SID
```

The Python orchestrator runs classic mode:

```bash
.venv/bin/python -m lib.steps.pipeline --sources sources.yaml
.venv/bin/python -m lib.steps.pipeline --resume SID
.venv/bin/python -m lib.steps.pipeline --only rate --session SID
.venv/bin/python -m lib.steps.pipeline --engine openrouter:google/gemini-2.5-flash
# --no-email      skip the Gmail sends
# --no-summary    skip writing runs/<SID>/summary.md
# --cached-pages  read html sources from runs/<SID>/pages/ during gather
```

When a run finishes you get:
- `out/<YYYY-MM-DD-HHMMSS>.html` — final newsletter, plus `out/latest.html` symlink.
  The time is the session's start time, so two runs on the same day don't collide.
- `out/<YYYY-MM-DD-HHMMSS>_short.html` + `out/latest_short.html` — the rated digest.
- `published_articles` rows for every rated article (picks marked `selected=1`).
- `runs/<SID>/summary.md` — human-readable run report (`.venv/bin/python -m lib.run_summary --session SID`).
- `runs/<SID>/*.json` and `runs/<SID>/<step>.log` — per-step artifacts and logs.

#### B. Step-by-step (classic mode)

Useful when developing, debugging, or wanting to inspect state between steps.

```bash
.venv/bin/python -m lib.steps.start --sources sources.yaml --session demo-1
.venv/bin/python -m lib.steps.gather --session demo-1
.venv/bin/python -m lib.steps.progress --session demo-1     # inspect
.venv/bin/python -m lib.steps.show demo-1
.venv/bin/python -m lib.steps.filter --session demo-1
.venv/bin/python -m lib.steps.download --session demo-1 --max 50
.venv/bin/python -m lib.steps.dedupe --session demo-1
.venv/bin/python -m lib.steps.summarize --session demo-1
.venv/bin/python -m lib.steps.crossdedupe --session demo-1 --lookback-days 4
.venv/bin/python -m lib.steps.coverage --session demo-1
# rate also writes out/<stamp>_short.html + out/latest_short.html and emails the digest
# (--no-email to skip, --to ADDR to override the recipient)
.venv/bin/python -m lib.steps.rate --session demo-1
.venv/bin/python -m lib.steps.cluster --session demo-1
.venv/bin/python -m lib.steps.select --session demo-1 --k 60
.venv/bin/python -m lib.steps.draft --session demo-1 --max-edits 2 --parallelism 4
.venv/bin/python -m lib.steps.rewrite --session demo-1 --max-edits 2
.venv/bin/python -m lib.steps.send --session demo-1
```

#### C. Single step on an existing session

```bash
# Rerun just one step (overwrites that step's outputs; later steps untouched)
/newsagent:filter --session demo-1
.venv/bin/python -m lib.steps.rate --session demo-1 --no-email
```

### Curate stories (pick for the newsletter)

A local web page over the `published_articles` table: every article from sessions since
noon of the previous calendar day, grouped by category (alphabetical, then "Other", then
uncategorized) and sorted by rating, 50 per page. Each row shows the headline link and
summary bullets plus a **Select** button that flips `published_articles.selected` on the
spot (no reload).

Each row also has a **✎ Edit** button that opens an inline form for the URL, headline
(`short_summary`) and source (`site_name`) — e.g. to repoint an aggregator link at the
underlying story. No other column changes, and embeddings are not recomputed. The form has
**Delete** (hard-deletes the row, with an inline yes/no). **+ Add story** at the top inserts a
row into the most recent session with category "Other", title = headline, source defaulting
to the URL's domain, and `selected=1`. A URL that already exists is rejected.

```bash
.venv/bin/python -m lib.steps.curate                 # http://127.0.0.1:8766/
# --since 2026-11-14-12-00-00   earliest session id to show (default: noon yesterday)
# --latest                      only the most recent session
# --port / --host               override $CURATE_PORT / $CURATE_HOST
```

Note: unselecting an article that `send` already stamped (`published_at`) also removes it
from crossdedupe's "already published" set — your picks are the record of what went out.

### Draft the issue from your picks (`/newsagent:edition`)

After curating, `/newsagent:edition` drafts the issue in house style from the selected
articles:
1. One Opus agent merges the sessions' overlapping categories into ~15–25 clusters and flags
   likely duplicate stories.
2. One Opus agent per cluster writes each item: a kicker, a one-sentence summary, a source
   label and N alternative kickers.
3. One Opus agent does the final edit: issue order, copy edit, kicker de-duplication, a
   `## ` title (plus N alternatives) for each cluster, and the subtitle.
4. Python fetches a candidate image for every item (the article's og:image, resized to
   600px wide, cached in `download/edition-images/`) and puts it above the item. Missing
   ones show as `- No image: reason`.
5. `/newsagent:finalize edition` runs automatically and imports the md into a beehiiv
   draft (Chrome extension + beehiiv login required).

The output is `out/edition-<stamp>.md` (and `out/latest-edition.md`). Alternatives, flags,
possible duplicates and merge notes appear as `- Label: text` bullets, which become bulleted
lists in the beehiiv draft; delete them there before sending. The style is defined by
`docs/style/style-guide.md`, `gold-headlines.md` and `house-style.md`; edit those to
change it.

```bash
/newsagent:edition                 # 3 alternative kickers per item
/newsagent:edition --kickers 10    # more alternatives
```

### Apply copy-edit fixes to the beehiiv draft (`/newsagent:fix`)

Paste a fix list after the command, e.g. `#12 Nvidia says…: "old" → "new"` or
`Old: "…" New: "…"`. Each fix must match exactly once in the live draft. The batch is applied
all-or-nothing through the editor model (`skills/beehiiv/scripts/fix_text.js`), so link and
italic marks survive. It also handles consistency passes such as curly → straight quotes.
Requires the same Chrome + beehiiv login as `beehiiv`. See
[skills/fix/SKILL.md](skills/fix/SKILL.md).

### Watch a run in the pipeline GUI (`/newsagent:gui`)

A local read-only page showing a session's 14 steps as a live diagram: status, metric
line and duration on each node, with draft → rewrite drawn as the critic loop. Click a
node for **Doc** (its SKILL.md), **Code** (step module + prompts), **Log** and **State**
(the step's checkpoint as a sortable table, plus `runs/<SID>/<step>.json`). The bottom
pane follows the running step's log (every step writes `runs/<SID>/<step>.log`).

To chat with Claude and run steps from the page, start a connected Claude Code session
in a second terminal. Chat messages and the **▶ Run next** / **▶▶ Run all** buttons go
into that session through a Claude Code channel (`lib/gui_channel.py`), and Claude's
replies (and any tool-permission prompts, with Allow/Deny) appear in the chat pane.
**■ Stop** kills the running step's process and tells Claude not to continue;
**⟲ Reset** starts a fresh session. Channels need an interactive session logged in
with claude.ai (not `claude -p`). Details in `skills/gui/SKILL.md`.

```bash
.venv/bin/python -m lib.steps.gui                    # http://127.0.0.1:8767/
# --session SID   session to open first (default: most recent; the page has a picker)
tools/claude-gui.sh                                  # connected Claude Code session
```

### Bluesky tools

The older roundup used Bluesky as a CMS for staging posts:
🦋-share stories from `out/latest.html` / `out/latest_short.html`, then `/newsagent:bluesky`
harvests the [druce.ai feed](https://bsky.app/profile/druce.ai) and `/newsagent:beehiiv`
imports the digest into beehiiv. `/newsagent:finalize` (no argument) runs those last two back
to back and stops without touching beehiiv when there are no new posts.

#### Bluesky digest (`/newsagent:bluesky`)

Fetches posts since the last run (per-handle dedup marker, capped at `--limit`, default and API
max 100), downloads + resizes post images, and reorders posts into topical groups ranked by a
news-importance rubric. Writes `out/bsky-<stamp>.html` + `out/latest-bsky.html` (wall-clock
stamp, so each render is a new file). Pun-forward headline suggestions go to a separate
artifact, `runs/bsky-<handle>/titles.json`.

```bash
# Requires BSKY_USERNAME + BSKY_SECRET; default handle is druce.ai
/newsagent:bluesky
```

#### Share newsletter items to Bluesky (🦋 queue + auto-poster)

Each item row in `out/latest.html` and `out/latest_short.html` has a 🦋 link. Run the
daemon, open the **local `file://` copy**, and click 🦋 on any story — its title + URL are
queued and posted to your Bluesky as a link-preview card (post text = the title, card =
the URL).

```bash
# Requires BSKY_USERNAME + BSKY_SECRET; serves the enqueue endpoint + drains the queue
.venv/bin/python -m lib.steps.bsky_share --interval 60
# --once  drains a single pending item and exits
# --port  overrides $BSKY_QUEUE_PORT (default 8765)
# curl http://127.0.0.1:8765/health   -> {"status":"ok",...}
```

**You always know whether a click landed.** The pages probe the daemon on load (and again
whenever the tab regains focus):

| What you see | What it means |
|---|---|
| Red banner at the top of the page | Daemon unreachable — clicks are **not** being saved directly. The banner names the start command and disappears on its own once you start the daemon and switch back to the tab. |
| 🦋 → grey ✓ | Queued. The daemon also prints `Queued: "<title>"` (or `Dup:` if you already queued that URL). |
| 🦋 → red ✗ | The direct call failed. Hover for the reason. One small popup window opens showing the daemon's own reply — **all** failures reuse that same window, never one per click. Clicking a red ✗ again navigates in that window, which no browser policy can block. |

Notes: the 🦋 link only works from the local file — email clients strip the script, leaving
a plain link that only resolves on the daemon's own machine. Links point at literal
`127.0.0.1`, not `localhost`, because the latter also resolves to `::1` where the
IPv4-only daemon isn't listening. Each URL is queued at most once, ever (permanent dedup).
One item posts per `--interval` for gentle rate-limiting; the queue lives in the
`bsky_queue` table (`sqlite3 newsletter_agent.db "select status, title from bsky_queue"`).

#### Auto-post a session's picks (v2, no clicking)

Posts the session's `selected=1` stories from `published_articles`, highest rating first,
one every `--interval` seconds, and exits when none are left. Post text is the
`short_summary` one-liner; each row's `bsky_status` becomes `posted` or `failed` (failures
are not retried). Independent of the 🦋 queue.

```bash
.venv/bin/python -m lib.steps.bsky_share_v2                 # most recent session, every 180s
.venv/bin/python -m lib.steps.bsky_share_v2 --session SID --interval 120
.venv/bin/python -m lib.steps.bsky_share_v2 --dry-run       # list posting order only
```

---

## How to check status

### Quick check (most recent session)

```bash
.venv/bin/python -m lib.steps.progress
```

Output:
```
Session:  2026-05-18-091533
Progress: 64.3% (9/14 steps)
Next:     cluster
Headlines: 412
Clusters:  0
Sections:  0

WORKFLOW
Progress: 64.3% (9/14 complete)
Next Step: Step 10: Cluster Topics
...
```

### List all recent sessions

```bash
.venv/bin/python -m lib.steps.sessions --limit 10
```

### Deep dive on one session

```bash
.venv/bin/python -m lib.steps.show 2026-05-18-091533
.venv/bin/python -m lib.steps.show 2026-05-18-091533 --step rate   # one step's checkpoint
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
/newsagent:pipeline --resume demo-1
```

### Force a specific step to redo

```bash
# Reset one step and everything after
.venv/bin/python -m lib.steps.reset demo-1 --from cluster --yes

# Or reset only error steps to NOT_STARTED
.venv/bin/python -m lib.steps.reset demo-1 --errors --yes

# Or reset everything (rare)
.venv/bin/python -m lib.steps.reset demo-1 --all --yes
```

If a step crashed after doing its work but before saving its checkpoint,
`.venv/bin/python -m lib.steps.checkpoint SID STEP` marks it complete by hand.

### Garbage-collect old sessions

```bash
# Dry-run (shows what would be deleted)
.venv/bin/python -m lib.steps.gc --older-than 30

# Actually delete
.venv/bin/python -m lib.steps.gc --older-than 30 --yes
```

Deletes `agent_state` rows and `runs/<SID>/` directories only. Never touches the `articles`, `urls`, `newsletters`, `sites` tables — those are content, not state.

### Rebuild the cross-day dedup store

`.venv/bin/python -m lib.steps.backfill_published --days 14` (dry-run; add `--yes` to write)
rebuilds `published_articles` from past `select` checkpoints.

---

## Pipeline reference

```
start → gather → filter → download → dedupe → summarize → crossdedupe → coverage → rate → cluster → select → draft → rewrite → send
```

Then, outside the session pipeline: curate → edition → finalize edition → (human edit) → fix.

---

## Engine configuration

Each `PromptConfig` declares its own `default_engine` and `reasoning_effort` (0–10). Most
prompts default to `subagent`; the rating axes default to `openai-decisions:gpt-6-luna`, and
battles, site-name lookup and the classic-mode coverage/crossdedupe judges default to Gemini
Flash-Lite. Resolution precedence:

1. `--engine <id>` CLI arg (per step)
2. `NEWS_PROMPT_<NAME>_ENGINE=<id>` env var (per prompt)
3. `PromptConfig.default_engine`
4. `"subagent"` (implicit fallback)

Engine identifier formats:

| Identifier | Auth |
|---|---|
| `subagent` | None — runs a `claude -p` subprocess |
| `openrouter:<model>` | `OPENROUTER_API_KEY` |
| `openai:<model>` | `OPENAI_API_KEY` |
| `openai-decisions:<model>` | `OPENAI_API_KEY` — OpenAI Decisions API, yes/no predicate → probability (the `rate_*` axes) |
| `google:<model>` | `GOOGLE_API_KEY` |

`reasoning_effort` maps to provider-specific knobs:
- **OpenRouter:** `extra_body.reasoning.effort` (low/medium/high, or disabled at 0)
- **OpenAI:** `reasoning_effort` API param (minimal/low/medium/high), silently dropped on non-reasoning models
- **Google:** `thinking_config.thinking_level` (minimal at 0, low 1–3, medium 4–6, high 7–10)
- **Subagent:** `claude --effort` level (0–3 low, 4–6 medium, 7–8 high, 9–10 xhigh)

### Per-prompt override examples

```bash
# One engine for all LLM prompts in a classic run
.venv/bin/python -m lib.steps.pipeline --engine openrouter:google/gemini-2.5-flash

# Override a single prompt via env var
NEWS_PROMPT_BATTLE_ENGINE=openrouter:google/gemini-2.5-flash \
  .venv/bin/python -m lib.steps.rate --session demo-1
```

---

## Architecture

- **SQLite source of truth:** `newsletter_agent.db` with tables `urls`, `articles`, `sites`, `newsletters`, `published_articles`, `agent_state`, `bsky_queue`. Every step checkpoints to `agent_state` keyed by `(session_id, step_name)`. `published_articles` (one row per URL) feeds curation, edition, crossdedupe and the v2 Bluesky poster.
- **Pydantic state:** `NewsletterAgentState` extends a generic `WorkflowState` (14 ordered steps with status + timing).
- **Single LLM entry point:** `lib/llm.call_prompt(prompt_name, inputs, engine=...)`. Engines live in `lib/engines/`.
- **Per-prompt config:** each prompt is a separate module under `lib/prompts/` declaring engine + reasoning_effort + schema.
- **Adaptive HTML scraping:** httpx first, Playwright on failure. `sites.scrape_method` is an operator-curated pin; a step only logs a "consider pinning" notice. Parser differs by step — `gather` uses BeautifulSoup to harvest `<a>` tags from landing pages; `download` uses trafilatura to extract the main article body.
- **Critic-optimizer loops** (`lib/critic.py`): generic helper used by both `draft` (per section, parallel) and `rewrite` (whole newsletter). Stops at `accept=True` or score ≥ 8.0; default `max_edits=2`.

## Layout

```
newsagent/
├── .claude-plugin/           # plugin.json, marketplace.json
├── lib/
│   ├── state.py              # NewsletterAgentState + WorkflowState
│   ├── db.py                 # SQLite schema + dataclasses
│   ├── llm.py                # call_prompt, registry
│   ├── critic.py             # critic-optimizer loop
│   ├── rating.py             # Swiss pairing + Bradley-Terry
│   ├── clustering.py         # UMAP + HDBSCAN + Optuna
│   ├── mmr.py                # MMR diversity selection
│   ├── embeddings.py         # OpenAI text-embedding-3-large
│   ├── published.py          # published_articles store
│   ├── curate.py             # curation page
│   ├── edition.py, edition_images.py   # house-style issue draft + og:image fetch
│   ├── gui*.py, gui_page.html          # pipeline GUI + Claude Code channel
│   ├── steplog.py            # per-step runs/<SID>/<step>.log
│   ├── run_summary.py        # runs/<SID>/summary.md generator
│   ├── appconfig.py          # config.yaml loader
│   ├── config.py             # rating weights
│   ├── engines/              # subagent, openrouter, openai_chat, openai_decisions, google
│   ├── fetch/                # RSS, HTML (adaptive), REST, Playwright, Bright Data, site logins
│   ├── prompts/              # 25 PromptConfigs (one per file)
│   ├── steps/                # one CLI per step + curate/edition/gui/bsky/maintenance
│   └── bluesky/              # api, og_tags, images
├── skills/                   # SKILL.md per slash command (30 skills)
├── agents/                   # section-drafter, section-critic, newsletter-critic
├── docs/style/               # style-guide, house-style, gold-headlines
├── tools/                    # check_batch.py, claude-gui.sh, gui-channel.mcp.json
├── tests/                    # 875 tests, ~87% coverage
├── config.yaml               # systemwide settings (blocked_domains)
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
