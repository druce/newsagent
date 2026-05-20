---
name: dedupe
description: Remove near-duplicate articles by cosine similarity on OpenAI text-embedding-3-large embeddings of the full article body (trafilatura-extracted text). Catches syndicated reprints (e.g. Reuters wire stories republished by multiple sites). Keeps the longest body of each near-duplicate pair (threshold 0.95). Attaches embeddings to survivors for cluster/select reuse.
---

# newsagent:dedupe

Step 5 of `/newsagent:run` — runs between `download` and `summarize` so duplicate
articles never get summarized (saves LLM spend + avoids near-identical sections
downstream).

## How to invoke

```bash
python -m lib.steps.dedupe --db newsletter_agent.db --session SID [--threshold 0.95]
```

## Behavior

- Loads the latest session state.
- Collects every headline with a readable `text_path` (i.e. download succeeded).
- Reads the trafilatura-extracted body and truncates to 24,000 chars (≤ embedding model context).
- Embeds `title + body` via OpenAI `text-embedding-3-large` (batched, 256 per call).
- Attaches the vector to each headline as `headline["embedding"]` so cluster/select can skip re-embedding.
- Computes the full pairwise cosine similarity matrix (numpy).
- For each pair with similarity ≥ threshold (default 0.95) drops the headline with the shorter body; ties break on summary length, then insertion order.
- Marks the `dedupe` step COMPLETE in workflow state.
- Writes `runs/<SID>/dedupe.json` with counts.

## Why full text (not title+summary)

Wire stories (Reuters, AP, Bloomberg) get reprinted across many sites with
minor edits — different titles, different lead paragraphs, but the body
overlaps heavily. Embedding the body catches these; embedding `title+summary`
does not.

## Notes

- Requires `OPENAI_API_KEY` in environment.
- If no headlines have a downloaded body, marks the step complete with
  "nothing to dedupe" and exits.
