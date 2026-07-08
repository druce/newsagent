# Design: `coverage` step — same-day coverage-count rating boost

**Date:** 2026-07-07
**Status:** Approved (pending spec review)

## Motivation

The legacy pipeline treated *how many outlets covered a story today* as an
importance signal: near-identical same-day stories were merged into one item
and the survivor's importance was weighted by coverage ("by count × rating").
The refactor split that behavior apart and kept only half of it:

- **Merge-into-one-item-with-links** still happens opportunistically at `draft`
  time (`lib/prompts/write_section.py:43-46`), within a single section.
- **Coverage-count → importance boost is gone.** The `rate` composite
  (`lib/config.py:RATING_COEFFS`) has no coverage term. Worse, `dedupe`
  (step 4) *drops* near-duplicates at 0.95 body cosine **before** `rate` runs,
  and `select`'s MMR actively *penalizes* the survivor of a near-dup cluster
  (diversity). A story eight outlets ran and a story one outlet ran arrive at
  `rate` on identical footing.

This design restores the **ranking boost** (not presentation): a story that
many independent outlets covered today should rank higher and be more likely to
survive `rate`/`select`.

## Goals

- Count, per story, how many of today's articles are the *same event*.
- Boost every member of a same-story group in the `rate` composite by
  `log₂(group_size)`.
- Rely on the existing `select` MMR diversity penalty to keep exactly one
  representative — the boosted one — with no drop/merge/representative-picking
  logic added.

## Non-goals

- **No change to presentation.** Multi-source link merging stays exactly as it
  is today (opportunistic, at `draft`).
- **No change to `dedupe`.** Byte-identical syndicated reprints are still
  dropped at 0.95 body cosine (step 4) and their multiplicity is discarded.
  Coverage deliberately rewards only *independent* editorial coverage (an outlet
  writing its own version of the event), which is a stronger importance signal
  than a wire-service pickup.
- **No change to `crossdedupe`** or its `same_story` prompt.
- **No change to `mmr.py` or `select`.**

## Same-story definition

Two of today's articles are "the same story" when a Haiku `same_story` judge,
shown their **full `summary`** (bullets), confirms they report the same
underlying news event. Candidate pairs are shortlisted first by embedding
cosine so the judge only sees plausible pairs. This is the same
cosine-shortlist + LLM-judge mechanism `crossdedupe` uses, pointed at today's
own article set instead of the `published_articles` store.

## Workflow placement

New step inserted after `summarize` and before `rate`:

```
… → summarize → crossdedupe → coverage → rate → cluster → …
```

- It **cannot** run "after dedupe" literally: `dedupe` (step 4) runs before
  `summarize` (step 5), but `coverage` needs `short_summary` (for the cosine
  matrix) and `summary` (for the judge), both produced by `summarize`.
- It runs **after `crossdedupe`** so counts reflect the set that actually
  survives — a story `crossdedupe` is about to drop as a prior-day republish
  shouldn't contribute to today's coverage counts.
- `lib/state.py:WORKFLOW_STEPS` grows 13 → 14; downstream step indices shift.

## Algorithm (the `coverage` step)

1. **Candidates.** All headlines with a non-empty `short_summary`
   (reuse the crossdedupe `_candidates` shape).
2. **Embed.** `title + "\n" + short_summary` per candidate via
   `lib.embeddings.embed_texts` (OpenAI `text-embedding-3-large`) — identical to
   crossdedupe's `_candidate_text`.
3. **Pairwise cosine.** Build the full upper-triangle cosine matrix over all
   candidates (length-normalized dot products, numpy).
4. **Shortlist.** Every pair with cosine ≥ `--shortlist-threshold`
   (default **0.70**, matching crossdedupe) becomes a candidate pair for the
   judge.
5. **Judge.** The Haiku `same_story_sameday` prompt confirms each shortlisted
   pair as same/different, shown both articles' **full `summary`**. 25 pairs per
   batch; prepare/dispatch/apply, same as crossdedupe.
6. **Group.** Build an undirected graph over candidates with an edge per
   confirmed-same pair; take **connected components** (union-find). Each
   component is one event.
7. **Stamp.** For every headline, set
   `coverage_count = size of its component` (a singleton — no confirmed same-story
   partner — has `coverage_count = 1`). Persisted on `state.headline_data[i]`.
8. **No drops, no merges.** All N members flow through to `rate` unchanged.

### Grouping notes

- **Transitivity is by construction:** if the judge confirms A~B and B~C but not
  A~C, connected components still place A, B, C in one event of size 3. This is
  intentional — coverage is a coarse "how many outlets ran this" count, and a
  chain of confirmed same-event links is reasonable evidence of one event.
- Component size is the count; there is no cap on component size (a genuinely
  huge event that many outlets ran *should* score high; `log₂` already
  compresses it).

## New prompt: `same_story_sameday`

New file `lib/prompts/same_story_sameday.py` (one-PromptConfig-per-file
convention). Near-identical rubric to `same_story`, with:

- **Symmetric same-day framing.** "A and B are two articles from today's batch;
  decide whether they report the same underlying news event" — not the cross-day
  "B was already published in a recent issue" framing.
- **Full-summary fields.** Input pairs carry `a_title`, `a_summary`, `b_title`,
  `b_summary` (full bullet summaries), not the one-line `short_summary`.
- Same "when uncertain, prefer different" guardrail.
- `default_engine = "google:gemini-3.1-flash-lite"`, `reasoning_effort = 2`
  (identical to `same_story`).

`crossdedupe` and `lib/prompts/same_story.py` are left untouched.

## Rating integration

Additive composite, consistent with `lib/config.py`'s documented model.

- **New coefficient** in `RATING_COEFFS`:
  ```python
  "coverage": 1.0,  # c_coverage * log2(coverage_count); singleton -> 0
  ```
  Default `1.0` matches the sibling unit-scale coefficients (a 2-outlet story
  → `log₂(2) = 1.0`, ~one on-topic point). It is the single tuning knob.
- **Composite term** added where the rating is assembled — the single
  `RATING_COEFFS` summation in `lib/steps/rate.py` (~line 330):
  ```python
  coverage_count = max(int(h.get("coverage_count", 1)), 1)
  rating += coeffs["coverage"] * math.log2(coverage_count)
  ```
- **Safe default:** a missing/absent `coverage_count` is treated as 1
  → `log₂(1) = 0` → no boost. So resume paths, the classic engine path, or a
  skipped coverage step never crash and never spuriously boost.
- **Why boosting all N members is correct:** every member of a same-story group
  gets the same boost, so whichever one MMR selects already carries it. MMR's
  diversity penalty suppresses the other N−1 near-duplicates, so exactly one
  boosted representative survives into the newsletter. "Boost the best one" is an
  emergent property; no representative is chosen explicitly and no item is
  dropped by this step.

## Step modes

Mirror `crossdedupe` exactly:

1. **classic** (default, `--engine`, CI/cron only): in-process
   `same_story_sameday` calls via `call_prompt`, then stamp counts.
2. **`--prepare-batches`**: write 25-pair batches to
   `runs/<SID>/coverage-batches/batch-NNN.json` for Haiku subagent dispatch.
3. **`--apply-results DIR`**: read verdict JSONs, build components, stamp
   `coverage_count`, complete the step.

Like crossdedupe, most runs shortlist **few or zero** pairs (most stories are
singletons); zero batches is a normal no-op and `--apply-results` completes the
step cleanly.

CLI flags (mirroring crossdedupe): `--db`, `--session`, `--engine`,
`--prepare-batches`, `--apply-results`, `--batch-size` (default 25),
`--shortlist-threshold` (default 0.70).

## Pipeline integration (interactive)

`skills/pipeline/SKILL.md` updated:

- 13-step plan → 14-step plan; `coverage` added to the ordered list between
  `crossdedupe` and `rate`.
- Per-step table row: **coverage | prepare/dispatch/apply | haiku | 25 pairs per
  batch (often 0 batches — no candidates)** — identical handling to
  `crossdedupe`.

## Observability

- `runs/<SID>/coverage.json`: candidate count, shortlisted-pair count,
  confirmed-pair count, number of multi-outlet groups, max coverage_count,
  number of headlines boosted.
- `rate` logs a short line naming the top few coverage-boosted stories and their
  `coverage_count`.

## Files touched

**New**
- `lib/steps/coverage.py`
- `lib/prompts/same_story_sameday.py`
- `skills/coverage/SKILL.md`
- `tests/test_step_coverage.py` (mirrors `tests/test_step_crossdedupe.py`)

**Modified**
- `lib/state.py` — `WORKFLOW_STEPS` +`coverage`
- `lib/config.py` — `RATING_COEFFS["coverage"]` + docstring
- `lib/steps/rate.py` — add the `log₂` coverage term to the `RATING_COEFFS`
  summation (~line 330) and its docstring formula (lines 4–17)
- `lib/prompts/__init__.py` — register the new prompt
- `skills/pipeline/SKILL.md` — 14-step plan + table row
- `tests/test_rating.py` — coverage-term cases
- `CLAUDE.md` — architecture/workflow blurb (step count, new step, new prompt)

## Edge cases

- **No candidates / no pairs above threshold** → zero batches, step completes as
  a no-op, all `coverage_count = 1`.
- **Resume / re-run** — step is idempotent: re-stamping `coverage_count`
  overwrites prior values; `rate` re-reads them.
- **Classic engine path** — same in-process judge loop as crossdedupe; no
  subagent dispatch needed for CI.
- **coverage_count absent at rate time** (step skipped) — defaults to 1, boost 0.
- **Bradley-Terry independence** — `bt_z` is computed from battles inside `rate`
  before the composite; the coverage term is added to the composite afterward,
  so the two do not interact.

## Testing plan

- `tests/test_step_coverage.py` (mirror crossdedupe tests): candidate selection,
  cosine shortlist threshold, batch prepare/apply round-trip, union-find grouping
  (including transitive A~B~C), `coverage_count` stamping, zero-pair no-op.
- `tests/test_rating.py`: `log₂` boost math, singleton → 0, missing-field
  default → 0, coefficient scaling.
- Full existing suite stays green (266 tests) with the new 14-step workflow.
