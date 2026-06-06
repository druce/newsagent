# Gather: halt-on-empty + cached-pages recovery — design

**Date:** 2026-06-06
**Status:** Approved (pending spec review)

## Problem

`gather` fetches every configured source, and when a source comes up empty
(e.g. WSJ blocked by DataDome, or its landing-page selectors changed so 0 `<a>`
links match), it silently records `FAIL`/0 and the pipeline proceeds — that
source contributes no headlines to the newsletter, with no chance to intervene.

The operator wants to:

1. **Halt** the run when any source extracts 0 URLs, so they can fix it.
2. **Manually download** the failed source's landing page and drop it in.
3. **Resume** the session from where it stopped, reusing what was already
   gathered rather than re-fetching everything.

## Goals

- Stop the pipeline when any enabled source yields 0 extracted URLs, after
  fetching all other sources first.
- Provide a `--cached-pages` mode where **html** sources are read from
  `runs/<SID>/pages/<source>.html` instead of the network, while **RSS/REST are
  fetched live as usual**.
- Let the operator recover by dropping the manual landing-page file and resuming
  the existing session from its first incomplete step.

## Non-goals

- Manual-file recovery for RSS/REST sources. Those are recovered by resuming
  (live re-fetch). There is no dropped-feed-file path. (html-only manual files.)
- Auto-pinning `sites.scrape_method` or auto-routing to Bright Data on failure.
  Operator-curated pins remain manual.
- Caching RSS/REST raw responses to disk. `pages/` stays html-only.

## Definitions

- **Extracted count** — `len(result.articles)`, the links parsed off the
  landing page, *before* dedup against the `urls` table.
- **Empty source** — an enabled source where `result.ok is False` **or**
  extracted count `== 0`. Note: "0 new after dedup" is **not** empty — a source
  that fetched fine but whose links were all already in the DB is healthy.

## Design

### 1. Halt-on-empty (in `lib/steps/gather.py`)

Behavior is unchanged up through fetching all sources, saving each html source's
raw page to `runs/<SID>/pages/<source>.html`, inserting new URLs (committed),
and extending `state.headline_data`. Then:

- Compute the empty set across **all enabled sources, any type** (`html`,
  `rss`, `rest`).
- If the empty set is non-empty:
  - Persist the partial `headline_data` (already done before the completion
    branch).
  - Record the empty sources in `runs/<SID>/gather.json` (e.g. an
    `empty_sources: [{source, type, error, page_path}]` field; `page_path` is
    the expected drop location for html sources, `null` otherwise).
  - `state.error_step("gather", <message>)` and `save_checkpoint("gather")`.
  - Print per-source recovery instructions (see Recovery), then exit non-zero.
- If the empty set is empty: `complete_step("gather", …)` exactly as today.

Because the step is left in `ERROR`, `WorkflowState.next_step` (first
non-`COMPLETE` step) returns `gather`, making it the natural resume point. No
new step-status states are introduced.

### 2. `--cached-pages` flag

Added to **`gather`** and plumbed through **`lib.steps.pipeline`** (passed to
the gather step only).

When set:

- **html** sources: skip the network. Read
  `runs/<SID>/pages/<source>.html` and run it through the existing
  `extract_article_links` (same include/exclude rules from `sources.yaml`). A
  missing cache file counts as an **empty source** (→ halt).
- **rss / rest** sources: fetched live, exactly as in a normal run.

This is usable standalone (`pipeline --cached-pages` re-runs from already-saved
pages without re-hitting html sites) and as the recovery vehicle (below).

`extract_article_links` already operates on an HTML string, so the cached path
reuses it directly; only the source of the HTML string changes
(disk vs. `fetch_html`).

### 3. Recovery workflow

1. Normal run. WSJ extracts 0 → gather finishes the other sources, then halts:
   ```
   gather halted: 1 source(s) returned 0 URLs.
     - WSJ (html): download the landing page to
       runs/2026-06-06/pages/WSJ.html, then resume with:
         python -m lib.steps.pipeline --resume 2026-06-06 --cached-pages
   ```
2. Operator downloads the WSJ AI landing page (headed browser, or Bright Data)
   and saves it to `runs/2026-06-06/pages/WSJ.html`.
3. `python -m lib.steps.pipeline --resume 2026-06-06 --cached-pages`:
   - Resume resolves the first incomplete step = the ERROR'd `gather`.
   - gather (cached-pages) reads all html from `pages/` (including the manual
     WSJ.html), fetches RSS/REST live, finds WSJ non-empty.
   - No empty sources remain → `complete_step` → pipeline continues to filter…
   - Dedup against the `urls` table makes re-reading the already-processed html
     pages harmless (0 new for those).

If a halted source is RSS/REST, step 2 is skipped: the operator just resumes
(`--resume SID`, with or without `--cached-pages`) and the feed/API is re-fetched
live.

## Component changes

| Component | Change |
|---|---|
| `lib/steps/gather.py` | Add `--cached-pages` option. After fetching all sources, compute empty set; on non-empty, `error_step` + write `empty_sources` to `gather.json` + print recovery instructions + non-zero exit. In cached mode, html sources read from `pages/<source>.html` (missing file → empty). |
| `lib/steps/pipeline.py` | Add `--cached-pages` flag; `_build_args` passes it to the `gather` step only. Ensure a non-zero gather exit aborts the orchestrator (existing behavior). |
| `skills/pipeline/SKILL.md` | Document the gather-halt outcome and the `--resume SID --cached-pages` recovery for the interactive driver. |
| `skills/gather/SKILL.md` | Document `--cached-pages` and halt semantics. |

## Error handling

- **Missing cache file in `--cached-pages`**: that html source is treated as
  empty → contributes to the halt with a message naming the missing path.
- **Manual file present but still 0 extracted** (e.g. wrong page saved): source
  is still empty → halts again with the same instructions. Idempotent.
- **All sources empty / network down**: halts listing all of them; URLs table
  unchanged for those.
- **Re-running after a successful complete**: unchanged idempotency — gather
  overwrites its own outputs, dedup prevents duplicate URLs.

## Testing (TDD)

Mock the three fetchers (`fetch_html`, `fetch_rss`, `fetch_rest`) so tests are
hermetic:

1. **Halt on empty**: one source returns 0 → `gather` exits non-zero, step
   status is `ERROR`, `gather.json` lists the empty source, working sources'
   URLs are still inserted.
2. **No halt when only 0-new-after-dedup**: source returns links that are all
   already in `urls` → step `COMPLETE` (extracted > 0, just nothing new).
3. **`--cached-pages` reads html from disk**: write a `pages/<source>.html`,
   stub html fetch to raise if called, assert links come from the file and rss
   is still fetched live.
4. **Missing cache file → empty → halt** under `--cached-pages`.
5. **Resume completes**: simulate halt, drop a valid cache file, re-run with
   `--cached-pages`, assert step `COMPLETE` and the previously-empty source's
   URLs are now present.
6. **Pipeline plumbing**: `--cached-pages` reaches the gather step args and not
   other steps.

## Open questions

None outstanding. (RSS/REST manual-file recovery explicitly out of scope per
the non-goals.)
