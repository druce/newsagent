# Prompt inventory and eval candidates

Step 1 of the prompt-optimization effort (plan: `~/.claude/plans/i-would-like-to-snuggly-ripple.md`).
Status (2026-11-10): Phase A (archive) and Phase B (candidates) are done. Phases C–E come next:
challenger models mine disagreements, Opus adjudicates, then human review. **Do not truncate the DB,
`runs/`, or the `.bak` until Phase E is verified.**

## Regenerate

```bash
.venv/bin/python -m evals.extract.archive      # Phase A → evals/archive/*.jsonl.gz + report.json (~1.5 min, read-only)
.venv/bin/python -m evals.extract.candidates   # Phase B → evals/work/candidates/<prompt>.jsonl + report.json (~25 s)
```

Both output dirs are gitignored. Phase A reads `runs/` and the `agent_state` checkpoints, so it
**can only be re-run before truncation**. After that, `evals/archive/` is the only copy.

## Prompts

Every prompt is in `lib/prompts/`; `call_prompt` (`lib/llm.py`) is the single entry point.
"Live" means the prompt runs in the interactive pipeline (SKILL.md batch dispatch) or in-process.

| Prompt | Engine / effort | One call = | Eval kind | Archive source | History | Candidates |
|---|---|---|---|---|---|---|
| filter_urls | subagent (Haiku) / 4 | 25 titles | binary | filter-batches/results | 103.5K items | 1000 (500/500 by label, 70% hard) |
| extract_summaries | subagent (Opus) / 6 | ≤10 articles (text ≤8K chars) | code checks + judge (faithfulness) | summarize-batches; text parsed out of `user_prompt` | 51.5K items | 1000 (70% hard) |
| dedupe_vs_archive | gemini-3.1-flash-lite / 2 (live: Opus agents) | 25 pairs, today vs published | binary | crossdedupe-batches (since 09-16 only) | 5.3K pairs | 1000 (500/500) |
| coverage_boost | gemini-3.1-flash-lite / 2 (live: Opus agents) | 25 same-day pairs | binary | coverage-batches (since 07-08) | 7.6K pairs | 1000 (500/500) |
| rate_quality / rate_on_topic / rate_importance | openai:gpt-4o-mini (logprobs) | 25 items, `title\nsummary` | numeric confidence | `rate` checkpoint (scalars only) | 46.7K items each | 1000 each (70% hard) |
| battle_prompt | gemini-3.5-flash-lite / 2 (was 3.1, 6 items, until 2026-11-12) | 10 items `{id, title, summary}` → ranking | ranking | **not saved**; groups built from each session's rated set (= that run's `_short.html`), quartiles by composite `rating` | 314 sessions | 1000 = 200 each of q1_top/q2/q3/q4_bottom (6 random from one quartile) + 200 mixed (1 per quartile + 2 random); reference ranking = rating desc |
| name_topic_batch | subagent (Opus) / 3 | all clusters | code checks + judge | cluster-batches | 12.2K clusters | 1000 |
| extract_noise_clusters | subagent (Opus) / 4 | 50 noise headlines | partition checks + judge | select-repechage | 381 calls | all 381 |
| consolidate_cluster_names | subagent (Opus) / 5 | all cluster names | closed-set checks + judge | select-consolidate | 312 calls | all 312 |
| reassign_to_clusters (`assign_noise_batch.py`) | subagent (Haiku) / 3 | 25 headlines × all clusters | closed-set classification | select-reassign | 51.6K items | 1000 (70% hard) |
| write_section | subagent (Opus) / 8 | one section | code checks + judge | draft-batches; **only the final post-loop section survives** | 4.3K | 1000 random |
| critique_section | subagent / 8 | one section | judge (meta-eval) | draft-results: last score/feedback, kept only when `accepted` (otherwise the last critique judged an earlier draft) | 4.2K | 1000 random |
| improve_section | subagent / 6 | section + critique | — | intermediate drafts not saved | 0 | none. Start logging drafts if we want this |
| critique_newsletter | subagent / 8 | whole newsletter | judge | rewrite-work `draft-*` → `critique-i` | 587 | all |
| improve_newsletter | subagent / 6 | newsletter + critique | judge | rewrite-work `(draft, critique-i)` → `draft-i` | 549 | all |
| generate_newsletter_title | subagent / 8 | final newsletter | code check (6–12 words) + judge | rewrite-work `title.txt` (+ `newsletters.title`) | 295 | all |
| sitename | gemini-3.1-flash-lite / 4 | ≤25 domains | mostly exact match | sitename-batches (+ `sites.name`) | 900 unique domains | all 900 |
| bsky_reorder / bsky_headlines | subagent / 4 | whole digest | judge / order | only the latest run; snapshot in `evals/archive/bsky/<date>/` | 1 | none yet |
| name_topic | subagent / 3 | one cluster | — | classic fallback only, never logged | 0 | skip |

**Not archived:**
- The `rewrite-results/` of the 16 earliest runs (pre-`rewrite-work` format).
- One `select-assign-batches` dir (obsolete format).

## Case format (`evals/work/candidates/<prompt>.jsonl`)

```json
{"case_id": "filter_urls:<sid>:batch-007:13", "prompt": "filter_urls", "unit": "item|call",
 "key": "<dedupe key>", "input": {...validates against input_schema...}, "prod_output": {...},
 "provenance": {"sid": "...", "batch": "...", "id": "...", "system_prompt_hash": "..."},
 "strata": {"month": "2026-09", "source": "...", "label": true}, "tags": ["borderline_topic"],
 "hard_score": 1.5, "sample": "hard|random"}
```

Batched prompts are stored **one item per case**. The eval runner re-packs them into batches of
production size. The sampler:
1. dedupes by key (normalized title / pair / domain);
2. takes `hard_frac` (0.7) of picks from the hard pool, round-robin across months, with no
   single tag above 40% of the hard picks;
3. fills the rest at random.

The binary prompts are balanced 50/50 by label. `system_prompt_hash` records which prompt version
produced `prod_output`. A few summarize and consolidate cases predate the current prompt.

**Hard-case signals.** These come from prod output alone; Phase C replaces them with real
model disagreement.

| Prompt | Signals |
|---|---|
| filter_urls | same title labeled both ways on different days (`inconsistent_prod`); borderline topic words; minority label for a mixed source |
| dedupe_vs_archive / coverage_boost | "different" with high title overlap; "same" with near-zero overlap; overlap in between |
| extract_summaries | source attribution left in; not 3 bullets; >25 words; "no content"; short or truncated text |
| rate_* | mid-range confidence; importance disagreeing with the battle z-score; on-topic vs. importance conflict |
| reassign_to_clusters | "Other"; word overlap prefers a different cluster; off-vocabulary assignment |
| battle_prompt | none. Groups are stratified by quartile instead; the within-quartile groups (median rating spread ~1 for q2/q3) are the hard ones |

## Prompt drift to fix in step 5

- `agents/*.md` (newsletter-critic, section-critic, section-drafter) restate the prompts as
  documentation, but nothing loads them, so they can drift from `lib/prompts/*`.
- `write_section`'s system prompt describes story fields `{rating, summary, site_name, url}`, but
  `draft._section_input` sends `{title, url, summary, source, rating}`.
- `skills/rewrite/SKILL.md` overrides the H1 rules: the critic must not penalize a missing H1, and
  the improver must strip the H1. `critique_newsletter.py` still says "If an H1 IS present".
- `skills/draft/SKILL.md` and `skills/rewrite/SKILL.md` contain the critique-loop agent
  instructions. Those are prompt text outside `lib/prompts/` and part of what we're optimizing.
