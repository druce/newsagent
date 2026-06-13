---
name: bluesky
description: Standalone Bluesky digest pipeline. Fetches recent posts from a Bluesky account, enriches with OG metadata and resized images, groups + reorders by importance via LLM, generates punny section titles, and renders an HTML digest. Independent from the main 12-step newsletter pipeline.
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

`--apply-reorder` and `--apply-titles` do no network I/O and need no creds.

## Flags

| Flag | Default | Notes |
|---|---|---|
| `--user HANDLE` | (required) | Bluesky handle to fetch posts from |
| `--limit N` | 80 | Max posts to fetch via getAuthorFeed |
| `--no-dedup` | off | Ignore the cross-run dedup marker and render the full feed |
| `--fetch` | off | Staged stage 1 (Python): fetch + images → `fetch.json` + `reorder-request.json` |
| `--apply-reorder` | off | Staged stage 3 (Python): `reorder-result.json` → `ordered.json` + HTML + `titles-request.json` |
| `--apply-titles` | off | Staged stage 5 (Python): `titles-result.json` → `titles.json` |
| `--engine ID` | — | Classic mode only: override engine for both prompts |

`--fetch`, `--apply-reorder`, `--apply-titles` are mutually exclusive; with none
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
index swept into a trailing "More headlines" group), renders the ordered HTML
(neutral topic labels as the H2 headers at this stage), and advances the dedup
marker. Writes:
- `out/bsky-YYYY-MM-DD.html` (+ `out/latest-bsky.html` symlink) — the deliverable
- `runs/bsky-<handle>/ordered.json` — ordered groups (record + titles input)
- `runs/bsky-<handle>/titles-request.json` — the rendered `bsky_section_titles` prompt

### Stage 4 — dispatch the titles Agent

Dispatch ONE Agent (`subagent_type: "general-purpose"`, `model: "sonnet"`,
`description: "Punny bsky section titles"`):

```
Read runs/bsky-<handle>/titles-request.json. It contains a system_prompt, a
user_prompt (with the topical groups inline), and output_schema
(BskySectionTitlesOutput). Follow system_prompt + user_prompt. Return ONLY a JSON
object matching output_schema: {"titles": ["<one witty 1-7 word title per group, in order>"]}.
Write that JSON to runs/bsky-<handle>/titles-result.json using the Write tool,
then report the path. Use ONLY Read and Write.
```

### Stage 5 — apply-titles (Python)

```bash
python -m lib.steps.bluesky --user <handle> --apply-titles
```

Validates `titles-result.json` against `BskySectionTitlesOutput` (pads with the
neutral label if short), and writes `runs/bsky-<handle>/titles.json`
(`{titles, sections:[{label,title}]}`). **Legacy parity:** the punny titles are a
separate artifact and are NOT merged back into the ordered HTML.

## Classic mode (cron / CI)

```bash
python -m lib.steps.bluesky --user <handle> --engine openrouter:google/gemini-2.5-flash
```

Runs fetch → `bsky_reorder` → `bsky_section_titles` → render in one process.
Do NOT use `--engine subagent` (falls back to `claude -p`). In classic mode the
punny titles ARE used as the section headers.

## Output Files

| Path | Description |
|---|---|
| `out/bsky-YYYY-MM-DD.html` | Ordered HTML digest (+ `out/latest-bsky.html` symlink) |
| `download/bsky-images/` | Cached resized post images |
| `download/bsky-state/<handle>.txt` | Cross-run dedup marker (newest-post URI from last run) |
| `runs/bsky-<handle>/` | Staged-mode artifacts: `fetch.json`, `reorder-request.json`, `reorder-result.json`, `ordered.json`, `titles-request.json`, `titles-result.json`, `titles.json` |

## LLM Prompts Used

| Prompt | Purpose |
|---|---|
| `bsky_reorder` | Clusters posts into topical groups and orders the groups by a 13-factor news-importance rubric |
| `bsky_section_titles` | Generates one witty, pun-forward 1–7 word title per topical group |

## Limitations / Out of Scope

- One handle per run (no multi-account aggregation).
- OG tag cache is in-memory per run (the staged `fetch.json` persists it for that run only).
- No email sending — preview only (open `out/latest-bsky.html` in a browser).
- Reply/repost filtering uses Bluesky's `filter=posts_and_author_threads` — no additional client-side filtering.
