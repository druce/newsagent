---
name: legacy-reference
description: Map a newsagent topic (clustering, rating, dedup, prompts, scraping, email format, Bluesky digest) to the file in the legacy implementation at ~/projects/OpenAIAgentsSDK/ that defines its intended behavior. Use when porting or tweaking ported behavior and you need the original source of truth.
---

# Legacy reference map

The legacy implementation at `~/projects/OpenAIAgentsSDK/` is **read-only reference**. Do not import from that path; do not modify it. It is the source of truth on *intent* for anything ported into `lib/`.

| Topic | File in `~/projects/OpenAIAgentsSDK/` |
|---|---|
| Workflow orchestration & resume | `run_agent.py`, `news_agent.py` |
| State schema | `newsletter_state.py`, `db.py` |
| Prompts | `prompts.py` |
| Source fetching | `fetch.py`, `sources.yaml` |
| Playwright scraping | `scrape.py` |
| Clustering math | `do_cluster.py`, `umap_reducer.pkl` (now copied locally) |
| Bradley-Terry rating | `do_rating.py` |
| Dedup | `do_dedupe.py` |
| Email / HTML format | `utilities.py` |
| Bluesky digest | `Compose newsletter from BlueSky posts.ipynb` |
