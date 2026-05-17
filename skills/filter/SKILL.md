---
name: news:filter
description: Classify each gathered headline as AI-related using the filter_urls prompt. Default drops non-AI headlines from session state and updates urls.isAI in the DB so future runs dedup against this signal. Pass --keep-non-ai to retain them for inspection.
---

# news:filter

Step 3 of /news:run.

## How to invoke

python -m lib.steps.filter --db newsletter_agent.db --session SID [--keep-non-ai] [--engine ENGINE]

## Behavior

- Batches up to 50 headlines per LLM call (FILTER_URLS prompt).
- Default engine: subagent. Override via --engine or NEWS_PROMPT_FILTER_URLS_ENGINE.
- Writes runs/<SID>/filter.json with counts.
