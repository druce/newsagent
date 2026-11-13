---
name: rate
description: Multi-axis confidence rating + Bradley-Terry composite scoring for filtered, summarized articles. Runs three per-axis rating prompts (quality, on-topic, importance) and Swiss-paired Bradley-Terry battles, then combines all signals into a composite `rating` score on each headline.
---

# newsagent:rate

Step 7 of /newsagent:pipeline.

## How to invoke

```
python -m lib.steps.rate --db newsletter_agent.db --session SID \
  [--engine ENGINE] [--to ADDR] [--no-email]
```

- `--to ADDR`: email recipient for the rated digest. Defaults to `GMAIL_USER`.
- `--no-email`: write `out/<stamp>_short.html` and skip sending.

**Run this command in the background** (Bash tool with `run_in_background: true`).
Rating is long-running — it makes per-axis batched LLM calls plus Swiss-paired
Bradley-Terry battles, typically 5–15 minutes on a ~250-headline session. The
orchestrator should dispatch it backgrounded and proceed when the completion
notification arrives. Do not poll for status; the harness emits a notification
when the task finishes.

## Behavior

- Processes headlines that have a `summary` field; skips any without one.
- Runs three per-axis yes/no predicates concurrently, one article per call, each returning a probability (0..1) from the OpenAI Decisions API (`openai-decisions:gpt-6-luna`):
  - `rate_quality` — probability the article is low quality (subtracted in the composite)
  - `rate_on_topic` — probability the article is about AI
  - `rate_importance` — probability the article is important to AI-newsletter readers
  An article whose call fails (after the engine's retries and its chat-completions fallback) scores a neutral 0.5 on that axis; the step continues.
- Articles whose summary is the "no content" sentinel get `quality_low = NO_CONTENT_QUALITY_LOW` (`lib/config.py`, default 1.0 = full penalty; `None` uses the model's title-only score).
- Calls `lib.rating.bradley_terry_scores` to run Swiss-paired LLM battles; z-scores the result.
- Computes the additive composite documented in `lib/steps/rate.py` (coefficients in `lib/config.py:RATING_COEFFS`): reputation, length, on_topic, importance, −quality_low, bt_z, recency, log2(coverage).
- Writes six fields to each rated headline: `quality_low`, `on_topic`, `importance`, `bt_z`, `bt_score`, `rating`.
- Default engine: each prompt's own default (`openai-decisions:gpt-6-luna` for the axis predicates, `google:gemini-3.5-flash-lite` for battles). `--engine` overrides all of them; a chat engine then answers each predicate with a verbalized probability.
- Writes `runs/<SID>/rate.json` with rating distribution (min/max/mean).
- After ratings compute, **always** renders a rated digest and writes:
  - `out/<stamp>_short.html` — sorted-by-rating list of headline + short_summary + bullets
  - `out/latest_short.html` — symlink (or copy on fs without symlink support)
- By default, also emails the digest via Gmail SMTP (`lib.utilities.send_gmail`)
  using `GMAIL_USER` / `GMAIL_PASSWORD` from the environment. Pass `--no-email`
  to skip sending. Missing credentials log a warning but don't fail the step.

## Weights (lib/config.py)

```python
RATING_WEIGHTS = {"quality_low": 0.2, "on_topic": 0.3, "importance": 0.3, "bt_z": 0.2}
RECENCY_BONUS_WEIGHT = 0.1
```
