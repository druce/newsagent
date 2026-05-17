---
name: news:dedupe
description: Remove near-duplicate headlines using cosine similarity on OpenAI text-embedding-3-large embeddings. Runs between summarize and rate. Keeps the longer-summary article of each near-duplicate pair (similarity >= 0.95). Attaches embeddings to surviving headlines so cluster/select can reuse them.
---

# news:dedupe

Maintenance step between summarize and rate in /news:run.

## How to invoke

python -m lib.steps.dedupe --db newsletter_agent.db --session SID [--threshold 0.95]

## Behavior

- Loads the latest session state from the database.
- Collects all headlines that have a `summary` field.
- Embeds `title + summary` text via OpenAI text-embedding-3-large (batched, 256 per call).
- Attaches the embedding vector to each headline as `headline["embedding"]` for reuse by cluster/select.
- Computes the full pairwise cosine similarity matrix (numpy).
- For each pair with similarity >= threshold (default 0.95): drops the headline with the shorter summary; ties keep the first.
- Persists updated state via `state.serialize_to_db("dedupe")` (not a registered workflow step — no start_step/complete_step).
- Writes runs/<SID>/dedupe.json with counts: total_candidates, dropped, kept, threshold.
- Prints a summary line to stdout.

## Notes

- `dedupe` is NOT in WORKFLOW_STEPS — it is a maintenance checkpoint between summarize and rate.
- Requires OPENAI_API_KEY in environment (used by embed_texts).
- Threshold can be tuned via --threshold; 0.95 matches legacy behavior.
- If no headlines have summaries, exits immediately with "Nothing to dedupe" message.
