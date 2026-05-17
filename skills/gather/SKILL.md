---
name: news:gather
description: Fetch headlines from all configured sources (RSS/HTML/REST). HTML sources use adaptive scraping — trafilatura+httpx first, Playwright fallback, with per-site working method memoized in sites.scrape_method. Inserts new URLs into the `urls` table and updates session headline_data.
---

# news:gather

Step 2 of `/news:run`. Fetches headlines and dedups against `urls`.

## How to invoke

```bash
python -m lib.steps.gather --db newsletter_agent.db --session SID
```

## Behavior

For each enabled source in `sources.yaml`:
- `type: rss` → feedparser via httpx
- `type: html` → trafilatura+httpx first; on failure or thin content (<3 links), fall back to Playwright. Persists the working method in `sites.scrape_method` for next run.
- `type: rest` → JSON API (NewsAPI-style)

After all sources fetched:
- Dedups against `urls.initial_url`
- Inserts new URLs into `urls` table
- Appends new headlines to `headline_data`
- Writes per-source report to `runs/<SID>/gather.json`
- Marks `gather` step complete

## Errors

- Missing session or session without `init` complete → exit 1
- Per-source failures are recorded but do not abort the step. Check `runs/<SID>/gather.json`.
