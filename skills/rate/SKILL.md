---
name: rate
description: Multi-axis confidence rating + Bradley-Terry composite scoring for filtered, summarized articles. Runs three per-axis rating prompts (quality, on-topic, importance) and Swiss-paired Bradley-Terry battles, then combines all signals into a composite `rating` score on each headline.
---

# newsagent:rate

Step 7 of /newsagent:run.

## How to invoke

```
python -m lib.steps.rate --db newsletter_agent.db --session SID [--engine ENGINE]
```

## Behavior

- Processes headlines that have a `summary` field; skips any without one.
- Calls three per-axis prompts in sequence, each batched at 25 items:
  - `rate_quality` — confidence the article is low quality (0..1, inverted in composite)
  - `rate_on_topic` — confidence the article is on-topic for AI news (0..1)
  - `rate_importance` — confidence the article is important (0..1)
- Calls `lib.rating.bradley_terry_scores` to run Swiss-paired LLM battles; z-scores the result.
- Computes composite: `rating = 0.2*(1-quality_low) + 0.3*on_topic + 0.3*importance + 0.2*bt_z`
- Adds recency bonus `+0.1 * exp(-hours_since/48)` if `published` date is set and parseable.
- Writes six fields to each rated headline: `quality_low`, `on_topic`, `importance`, `bt_z`, `bt_score`, `rating`.
- Default engine: each prompt's own default (e.g. `openai:gpt-4o-mini` for axis prompts). Override all with `--engine`.
- Writes `runs/<SID>/rate.json` with rating distribution (min/max/mean).

## Weights (lib/config.py)

```python
RATING_WEIGHTS = {"quality_low": 0.2, "on_topic": 0.3, "importance": 0.3, "bt_z": 0.2}
RECENCY_BONUS_WEIGHT = 0.1
```
