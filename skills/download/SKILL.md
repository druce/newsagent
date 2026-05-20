---
name: download
description: Fetch full article text with adaptive http→playwright strategy + Bright Data routing for paywalled sites. Captures post-redirect final URL, memoizes per-domain working method in sites.scrape_method.
---

# newsagent:download

Step 4 of `/newsagent:run`.

## How to invoke

```bash
python -m lib.steps.download --db newsletter_agent.db --session SID [--max N] [--parallel 8]
```

## Behavior

Every invocation starts fresh:

- `download/txt/` and `download/html/` are wiped of all `*.txt` / `*.html` files.
- `text_path` and `html_path` are cleared on every headline in state.
- Every headline is re-fetched. No skip-already-downloaded shortcut.

For every headline in `state.headline_data`:

1. **Partition** URLs by domain (3-way, in precedence order):
   - Domain with `sites.bright_data_enabled=1` → phase 0 (Bright Data).
   - Domain pinned to `playwright` in `sites.scrape_method` → phase 2.
   - All others → phase 1 first.

2. **Phase 0 — Bright Data Web Unlocker (sequential):**
   - Used for paywalled or aggressively anti-bot domains (Bloomberg, CNN,
     WSJ, Fast Company, Forbes are seeded by default; flip the flag for
     others via `UPDATE sites SET bright_data_enabled=1 WHERE domain=?`).
   - Requires `BRIGHTDATA_API_KEY` in the environment.
   - Routes through `brightdata-sdk`'s `SyncBrightDataClient` (one persistent
     client per invocation; not thread-safe, hence sequential).
   - On success: save text/html, tag the domain as bright_data-routed in the
     run report. **`sites.scrape_method` is left alone** — BD routing is
     governed by `bright_data_enabled`, not by `scrape_method`.

3. **Phase 1 — httpx (concurrent, default 8 workers):**
   - GET with `follow_redirects=True` and a desktop UA.
   - Extract main text via `trafilatura.extract`.
   - Success = 200 + ≥500 chars extracted → save text, capture `resp.url` as `final_url`,
     mark domain `sites.scrape_method='http'`.
   - Failure (HTTP error, non-200, thin/empty extract) → URL deferred to phase 2.

4. **Phase 2 — Playwright batch (concurrent, default 8 workers):**
   - One shared stealth Firefox context (`lib/fetch/browser.py`), N concurrent pages.
   - Blocks images/media/fonts. Captures `page.url` after navigation as `final_url`.
   - Extract via trafilatura. Save text, mark domain `sites.scrape_method='playwright'`
     (playwright wins over `http` if any URL in the domain needed it).

5. **Phase 3 — Bright Data fallback (sequential):**
   - Any URL that failed in phase 1 + 2, plus any phase-0 URL whose initial
     BD attempt failed, gets one more try via Bright Data.
   - Without `BRIGHTDATA_API_KEY` this is a no-op; failures get recorded with
     the prior error message included.

6. **Persist** for each successful download:
   - Raw response body written to `download/html/<domain>.<sha256[:24]>.html`.
   - Extracted text written to `download/txt/<domain>.<sha256[:24]>.txt`.
     The two files share the same stem so you can pair them.
   - Hash is computed on the **post-redirect** `final_url`, so two feed URLs
     redirecting to the same canonical article share one pair of files.
   - `headline['final_url']`, `headline['text_path']`, `headline['html_path']` set.
   - `urls.final_url` updated where `initial_url` matches.

7. **Report** to `runs/<SID>/download.json`: counts, http-vs-playwright breakdown,
   per-URL failure list (failures from the BD fallback are labeled
   `phase=bright_data_fallback`).

## Errors

Per-URL failures are recorded but do not abort the step. Inspect
`runs/<SID>/download.json` for diagnosis.

## Why redirects matter

Many sources are aggregators (Feedly, Google News, Reddit, t.co). Their feed
URLs redirect to the publisher's canonical URL. Storing the resolved URL lets
later steps:
- group syndicated copies under one canonical (full-text dedup uses this implicitly).
- cite the publisher rather than the aggregator in the rendered newsletter.
