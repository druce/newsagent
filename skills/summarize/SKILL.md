---
name: news:summarize
description: Extract bullet-point article summaries for each downloaded headline using the extract_summaries prompt. Reads article text from text_path, skips headlines without a downloaded text file, and writes summaries back to session state.
---

# news:summarize

Step 5 of /news:run.

## How to invoke

python -m lib.steps.summarize --db newsletter_agent.db --session SID [--engine ENGINE]

## Behavior

- Processes headlines that have `text_path` set (downloaded) but no `summary` yet.
- Batches up to 10 articles per LLM call (EXTRACT_SUMMARIES prompt, reasoning_effort=6).
- Caps article text at 20,000 characters to avoid oversized prompts.
- Default engine: subagent. Override via --engine.
- Writes runs/<SID>/summarize.json with counts.
