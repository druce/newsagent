# news:select

Select a diverse top-K set of headlines per topic cluster for newsletter sections.

## What it does

Runs three substeps against the clustered headline data produced by `news:cluster`:

1. **LLM noise assignment** — For each headline that HDBSCAN marked as noise
   (`cluster_id=-1`), calls `assign_noise` to decide whether it belongs to an
   existing cluster, deserves its own new cluster, or should be dropped entirely.

2. **LLM cluster merge** — Embeds cluster names, finds pairs whose name embeddings
   have cosine similarity >= 0.85, then calls `merge_clusters` for each candidate
   pair. If confirmed, all headlines from cluster B are reassigned to cluster A
   under the merged name.

3. **MMR selection** — For each surviving cluster, runs Maximal Marginal Relevance
   (`mmr_select`) to pick the top-K headlines that balance rating (relevance) with
   embedding diversity. Default K=5, lambda=0.7.

Writes:
- `state.newsletter_section_data` — list of `{cat, headline, link, rating, summary, id}` dicts
- `state.clusters` — updated `{cluster_name: [idx, ...]}` map
- `runs/<SID>/select.json` — summary artifact

## Design vs legacy

The legacy `do_cluster.py` / `newsletter_state.py` pipeline used plain top-K-by-rating
to pick headlines per cluster. The new step adds:
- LLM-assisted noise classification (no legacy equivalent)
- LLM-assisted cluster deduplication (no legacy equivalent)
- MMR diversity selection instead of greedy top-K

## Invocation

```bash
# Standard invocation (after news:cluster)
python -m lib.steps.select --session SID

# Override defaults
python -m lib.steps.select --session SID --k 8 --lambda 0.5

# Skip LLM substeps (faster, for debugging)
python -m lib.steps.select --session SID --no-noise-assign --no-merge

# Force a specific LLM engine
python -m lib.steps.select --session SID --engine openrouter:anthropic/claude-3-haiku
```

## Options

| Flag | Default | Description |
|---|---|---|
| `--session` | required | Session ID |
| `--db` | `newsletter_agent.db` | SQLite DB path |
| `--k` | `5` | Max headlines per cluster |
| `--lambda` | `0.7` | MMR trade-off (1.0=relevance, 0.0=diversity) |
| `--engine` | from PromptConfig | Override LLM engine |
| `--no-noise-assign` | off | Skip LLM noise assignment |
| `--no-merge` | off | Skip LLM cluster merge |

## Prerequisites

- `news:cluster` must be complete for the session (headlines must have `cluster_id`
  and `embedding` fields)
- `OPENAI_API_KEY` — used by `embed_texts` for cluster-name embeddings (merge step)
- `ANTHROPIC_API_KEY` or configured engine — for assign_noise + merge_clusters prompts

## Outputs

`state.newsletter_section_data` is a list of section-headline records, one per
selected headline:

```python
{
    "cat": "AI Safety",          # cluster/section name
    "headline": "OpenAI...",     # article title
    "link": "https://...",       # article URL
    "rating": 4.2,               # composite Bradley-Terry rating
    "summary": "Short summary",  # from summarize step
    "id": 7,                     # index into state.headline_data
}
```

The `news:draft` step consumes `newsletter_section_data` to produce per-section drafts.
