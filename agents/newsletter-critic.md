# Newsletter Critic

Persona embedded in `lib/prompts/critique_newsletter.py`. Used inside the critic-optimizer loop in `news:rewrite`.

## Role

A 15+ year newsletter veteran reviewing the whole assembled newsletter for structural, formatting, and editorial issues — not for new content. Returns `CritiqueResult{score, feedback, accept}`.

## Inputs

The full newsletter as markdown (all sections concatenated).

## Output

```json
{
  "score": 6.8,
  "feedback": "Sections 3 and 7 cover the same chip-export story — merge. 'Other News' has 12 items — split. Headline 4 of section 2 is title case — fix to sentence case. Section title 'AI Stuff' is generic — rename...",
  "accept": false
}
```

## Rubric dimensions

Distilled into one `score`, but the critic considers:

- **Title quality** — 6–12 words, factual, captures 2–3 major themes
- **Structure quality** — proper markdown nesting, 7–15 sections, "Other News" last
- **Section quality** — coherence, ≤7 stories per non-"Other" section, similar small sections merge
- **Headline quality** — ≤25 words, sentence case, neutral tone, no redundancy across sections

## Grading rubric

- 9.0–10.0: Excellent — ready to publish
- 8.0–8.9: Good — minor polish (short-circuit threshold)
- 7.0–7.9: Acceptable — needs targeted improvements
- <7.0: Significant rework needed

## Tuning knobs

- `default_engine="subagent"`, `reasoning_effort=8`.
- `news:rewrite --max-edits` controls the iteration cap (default 2).
