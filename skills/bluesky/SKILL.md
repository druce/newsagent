---
name: bluesky
description: Standalone Bluesky digest pipeline. Fetches recent posts from a Bluesky account, enriches with OG metadata and resized images, reorders by importance via LLM, and renders a flat HTML digest (post text linked to the article, source name appended) matching the legacy skynet.html format. Also generates punny headline rewrites as a separate artifact. Independent from the main 12-step newsletter pipeline.
---

# newsagent:bluesky

Two execution paths, mirroring the main pipeline's interactive/classic split:

1. **Staged (preferred in Claude Code)** — the two LLM prompts run via
   **in-session Agent dispatch**, so no `claude -p` and no external API engine.
   Five stages: two Python steps and two parent-dispatched Agents around them.
2. **Classic (cron / CI)** — one process runs everything in-process via
   `call_prompt`; requires a non-`subagent` `--engine` (otherwise it falls back
   to `claude -p`, which is not covered by the Max plan).

## Required Environment Variables (fetch + classic only)

| Variable | Description |
|---|---|
| `BSKY_USERNAME` | Bluesky login identifier (e.g. `yourname.bsky.social`) |
| `BSKY_SECRET` | Bluesky app password |

`--apply-reorder` and `--apply-headlines` do no network I/O and need no creds.

## Flags

| Flag | Default | Notes |
|---|---|---|
| `--user HANDLE` | (required) | Bluesky handle to fetch posts from |
| `--limit N` | 80 | Max posts to fetch via getAuthorFeed |
| `--no-dedup` | off | Ignore the cross-run dedup marker and render the full feed |
| `--fetch` | off | Staged stage 1 (Python): fetch + images → `fetch.json` + `reorder-request.json` |
| `--apply-reorder` | off | Staged stage 3 (Python): `reorder-result.json` → `ordered.json` + HTML + `headlines-request.json` |
| `--apply-headlines` | off | Staged stage 5 (Python): `headlines-result.json` → `headlines.json` / `headlines.txt` |
| `--engine ID` | — | Classic mode only: override engine for both prompts |

`--fetch`, `--apply-reorder`, `--apply-headlines` are mutually exclusive; with none
of them set, the command runs classic mode.

## Staged flow (in-session Agent dispatch)

All artifacts live under the per-handle workdir `runs/bsky-<handle>/`.

### Stage 1 — fetch (Python)

```bash
python -m lib.steps.bluesky --user <handle> --fetch
```

Logs in, fetches the author feed (newest first), applies cross-run dedup
(drops everything from the previously-seen post onward, per the marker
`download/bsky-state/<handle>.txt`), enriches OG metadata, and downloads +
resizes images to `download/bsky-images/`. Writes:
- `runs/bsky-<handle>/fetch.json` — feed items, og cache, image cache, `newest_uri`
- `runs/bsky-<handle>/reorder-request.json` — the rendered `bsky_reorder` prompt
  (`system_prompt`, `user_prompt` with posts inline, `output_schema`)

If nothing is new it prints "No new posts since last run.", advances the marker,
and stops (no request file written).

Fetch is the long network step. If you launch it with `run_in_background: true`,
wait ONLY for the task-completion notification — do **not** call `ScheduleWakeup`
to wait on it. A wakeup re-fires its `prompt`, so passing `/newsagent:bluesky` as
the wakeup prompt makes the whole digest re-run as a phantom second invocation.

### Stage 2 — dispatch the reorder Agent

Dispatch ONE Agent (`subagent_type: "general-purpose"`, `model: "sonnet"`,
`description: "Reorder bsky posts"`):

```
Read runs/bsky-<handle>/reorder-request.json. It contains a system_prompt, a
user_prompt (with the posts inline as JSON), and output_schema (BskyReorderOutput).
Follow system_prompt + user_prompt. Return ONLY a JSON object matching
output_schema: {"groups": [{"label": "<short topic label>", "indexes": [<post indexes>]}]}.
Cluster posts on the same company/person/tech/topic into the same group, order
groups most→least important, and include EVERY input index exactly once.
Write that JSON to runs/bsky-<handle>/reorder-result.json using the Write tool,
then report the path. Use ONLY Read and Write.
```

### Stage 3 — apply-reorder (Python)

```bash
python -m lib.steps.bluesky --user <handle> --apply-reorder
```

Validates `reorder-result.json` against `BskyReorderOutput`, materializes the
topical groups (defensive: out-of-bounds/duplicate indexes dropped, any omitted
index swept into a trailing "More headlines" group), then **flattens them into a
single ordered list** and renders the **flat HTML digest** (legacy skynet.html
format — **no section headers**; related posts are adjacent but unlabeled), and
advances the dedup marker. Each post is an optional image paragraph, then either
`<p><a href='URL'>post text</a>  - <em>source</em></p>` (source = `og:site_name`,
falling back to the domain) or — for link-less posts — a bare `<p>post text</p>`,
separated by `<hr />`, ending with a "Follow … on Bluesky" footer. Writes:
- `out/bsky-YYYY-MM-DD.html` (+ `out/latest-bsky.html` symlink) — the deliverable
- `runs/bsky-<handle>/ordered.json` — ordered groups (record + headlines input)
- `runs/bsky-<handle>/headlines-request.json` — the rendered `bsky_headlines` prompt

### Stage 4 — dispatch the headlines Agent

Dispatch ONE Agent (`subagent_type: "general-purpose"`, `model: "sonnet"`,
`description: "Punny bsky headline rewrites"`):

```
Read runs/bsky-<handle>/headlines-request.json. It contains a system_prompt, a
user_prompt (with the ordered headlines inline), and output_schema
(BskyHeadlinesOutput). Follow system_prompt + user_prompt. Return ONLY a JSON
object matching output_schema: {"headlines": ["<one punny 1-7 word rewrite per input
headline, in the same order>"]}.
Write that JSON to runs/bsky-<handle>/headlines-result.json using the Write tool,
then report the path. Use ONLY Read and Write.
```

### Stage 5 — apply-headlines (Python)

```bash
python -m lib.steps.bluesky --user <handle> --apply-headlines
```

Validates `headlines-result.json` against `BskyHeadlinesOutput` (pads with the
original headline if short), and writes `runs/bsky-<handle>/headlines.json`
(`{headlines, pairs:[{headline,rewrite}]}`) plus `runs/bsky-<handle>/headlines.txt` —
just the punny rewrites, one per line, for easy copy-paste. The punny rewrites are a
**separate artifact** and are NOT merged into the digest HTML (which uses the plain
post text, matching skynet.html).

## Classic mode (cron / CI)

```bash
python -m lib.steps.bluesky --user <handle> --engine openrouter:google/gemini-2.5-flash
```

Runs fetch → `bsky_reorder` → render flat digest → `bsky_headlines` (punny
rewrites side artifact) in one process. Do NOT use `--engine subagent` (falls back
to `claude -p`). The digest HTML is the same flat format as staged mode; the punny
rewrites go to `headlines.txt`, not into the HTML.

## Output Files

| Path | Description |
|---|---|
| `out/bsky-YYYY-MM-DD.html` | Flat ordered HTML digest (+ `out/latest-bsky.html` symlink) |
| `download/bsky-images/` | Cached resized post images |
| `download/bsky-state/<handle>.txt` | Cross-run dedup marker (newest-post URI from last run) |
| `runs/bsky-<handle>/` | Staged-mode artifacts: `fetch.json`, `reorder-request.json`, `reorder-result.json`, `ordered.json`, `headlines-request.json`, `headlines-result.json`, `headlines.json`, `headlines.txt` |

## LLM Prompts Used

| Prompt | Purpose |
|---|---|
| `bsky_reorder` | Clusters posts into topical groups and orders them by a 13-factor news-importance rubric (the groups are flattened at render time — no section headers in the digest) |
| `bsky_headlines` | Generates one witty, pun-forward 1–7 word rewrite per headline (a separate `headlines.txt` artifact, not injected into the digest) |

## Limitations / Out of Scope

- One handle per run (no multi-account aggregation).
- OG tag cache is in-memory per run (the staged `fetch.json` persists it for that run only).
- No email sending — preview only (open `out/latest-bsky.html` in a browser).
- Reply/repost filtering uses Bluesky's `filter=posts_and_author_threads` — no additional client-side filtering.
