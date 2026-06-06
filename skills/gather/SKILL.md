---
name: gather
description: Fetch headlines from all configured sources (RSS/HTML/REST). HTML sources use adaptive scraping — httpx first, Playwright fallback, with per-site working method memoized in sites.scrape_method. Inserts new URLs into the `urls` table and updates session headline_data.
---

# newsagent:gather

Step 2 of `/newsagent:pipeline`. Fetches headlines and dedups against `urls`.

## How to invoke

```bash
python -m lib.steps.gather --db newsletter_agent.db --session SID
```

## Behavior

For each enabled source in `sources.yaml`:
- `type: rss` → feedparser via httpx
- `type: html` → httpx + BeautifulSoup first (parses `<a>` tags from the landing page); on failure or thin content (<3 links), fall back to Playwright. Persists the working method in `sites.scrape_method` for next run. Note: trafilatura is NOT used here — that's only for `download`, which extracts article body text.
- `type: rest` → JSON API (NewsAPI-style)

After all sources fetched:
- Dedups previously seen urls against `urls.initial_url`
- Inserts new URLs into `urls` table
- Appends new headlines to `headline_data`
- Writes per-source report to `runs/<SID>/gather.json`
- Marks `gather` step complete

## Halt on empty sources

After fetching every enabled source, `gather` halts (exit code 1, step marked
ERROR) if ANY source extracted 0 URLs — i.e. it failed to fetch, or its landing
page yielded 0 matching links. "0 new after dedup" does NOT count as empty.

URLs from working sources are still inserted before the halt, and the empty
sources are recorded in `runs/<SID>/gather.json` under `empty_sources`. The step
is left ERROR so it is the resume point for `lib.steps.pipeline --resume`.

## --cached-pages

`python -m lib.steps.gather --session SID --cached-pages`

Reads each html source from `runs/<SID>/pages/<source>.html` instead of
fetching it; rss/rest are still fetched live. A missing cache file counts as an
empty source (→ halt). Use this to recover a halted gather: download the failed
source's landing page to the path named in the halt message, then re-run with
`--cached-pages` (typically via `lib.steps.pipeline --resume SID --cached-pages`).

## Errors

- Missing session or session without `init` complete → exit 1
- Per-source failures are recorded but do not abort the step. Check `runs/<SID>/gather.json`.
