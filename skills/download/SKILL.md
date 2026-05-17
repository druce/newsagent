---
name: news:download
description: Fetch full article HTML via Playwright for every kept URL, extract main text with trafilatura, store under download/. Skips URLs that already have a text_path. Phase 2 ships without near-duplicate dedup (Phase 3 adds it).
---

# news:download

Step 4 of `/news:run`.

## How to invoke

```bash
python -m lib.steps.download --db newsletter_agent.db --session SID [--max N]
```

## Behavior

For each headline in `state.headline_data` without a `text_path`:
1. Fetch full HTML via Playwright (chromium, headless).
2. Extract main article text via `trafilatura.extract`.
3. Write to `download/<sha256-of-url>.txt`.
4. Set `headline["text_path"]` to that path.

Failed fetches/extractions are logged to `runs/<SID>/download.json`; they don't abort the step.

## Phase 2 limits

- Serial execution (one browser at a time). Concurrent downloads come in Phase 2b.
- No embedding-based dedup. That ships in Phase 3 after the embedding API is wired up.
