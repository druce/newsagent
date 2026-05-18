---
name: news:bluesky
description: Standalone Bluesky digest pipeline. Fetches recent posts from a Bluesky account, enriches with OG metadata and resized images, reorders by importance via LLM, generates punny section titles, and renders an HTML digest. Independent from the main 11-step newsletter pipeline.
---

# news:bluesky

## Invocation

```bash
# Fetch and render a digest for a Bluesky account
python -m lib.steps.bluesky --user <handle>

# Custom limit and group size
python -m lib.steps.bluesky --user ai.bsky.social --limit 50 --group-size 4

# Override LLM engine for both prompts
python -m lib.steps.bluesky --user ai.bsky.social --engine openrouter:google/gemini-2.5-flash
```

## Required Environment Variables

| Variable | Description |
|---|---|
| `BSKY_USERNAME` | Bluesky login identifier (e.g. `yourname.bsky.social`) |
| `BSKY_SECRET` | Bluesky app password |

## Flags

| Flag | Default | Notes |
|---|---|---|
| `--user HANDLE` | (required) | Bluesky handle to fetch posts from |
| `--limit N` | 80 | Max posts to fetch via getAuthorFeed |
| `--group-size N` | 5 | Number of posts per digest section |
| `--engine ID` | — | Override LLM engine for both bsky_reorder and bsky_section_titles prompts |

## Pipeline Steps

1. **Auth** — POST `createSession` with `BSKY_USERNAME`/`BSKY_SECRET` → JWT session.
2. **Fetch** — GET `getAuthorFeed?actor=<handle>&limit=<limit>` → list of feed items.
3. **Enrich OG** — For each embedded URL: `get_og_tags(url)` → `{title, description, image, url}`. Best-effort, cached in memory for the run.
4. **Download images** — For posts with `og:image`: download + Pillow-resize to 240px height. Cached under `download/bsky-images/`.
5. **Reorder** — Call `bsky_reorder` prompt → ordered post indexes (most to least important).
6. **Group** — Chunk ordered posts into groups of `--group-size`.
7. **Title** — Call `bsky_section_titles` prompt → one punny 4-8 word title per group.
8. **Render** — Build HTML with H1 title, per-section H2 headers, post cards with text + image + OG link.
9. **Write** — `out/bsky-YYYY-MM-DD.html` + symlink `out/latest-bsky.html`.

## Output Files

| Path | Description |
|---|---|
| `out/bsky-YYYY-MM-DD.html` | Full rendered HTML digest |
| `out/latest-bsky.html` | Symlink to most recently written digest |
| `download/bsky-images/` | Cached resized post images (reused on same-day re-runs) |

## LLM Prompts Used

| Prompt | Purpose | Engine |
|---|---|---|
| `bsky_reorder` | Orders post indexes by news importance | `subagent` (default) |
| `bsky_section_titles` | Generates punny 4-8 word section titles | `subagent` (default) |

Override either prompt's engine via env var:
```bash
NEWS_PROMPT_BSKY_REORDER_ENGINE=openrouter:google/gemini-2.5-flash \
  python -m lib.steps.bluesky --user ai.bsky.social
```

## Limitations / Out of Scope

- One handle per run (no multi-account aggregation).
- OG tag cache is in-memory only (not persisted across runs).
- No email sending — preview only (open `out/latest-bsky.html` in a browser).
- Reply/repost filtering uses Bluesky's `filter=posts_and_author_threads` — no additional client-side filtering.
