# Section Critic

Persona embedded in `lib/prompts/critique_section.py`. Used inside the critic-optimizer loop in `news:draft`.

## Role

A senior newsletter editor critiquing one section in isolation. Returns a structured `CritiqueResult{score, feedback, accept}` that drives the iterative improvement loop.

## Inputs

The current section as markdown.

## Output

```json
{
  "score": 7.5,
  "feedback": "Drop bullet 4 (off-topic). Tighten headline 2: drop 'massive' (hype). Reorder: 3-1-2-5.",
  "accept": false
}
```

`accept=true` short-circuits the loop. The orchestrator also short-circuits when `score >= 8.0` regardless of the `accept` flag.

## What it scores

- Thematic coherence of the section
- Headline quality: ≤25 words, sentence case, active voice, no clickbait
- Section title quality (≤7 words, creative but clear)
- Section size (2–7 stories preferred)
- Ordering (biggest first, lighter last)
- Drop candidates (low-rating, redundant, off-narrative)

## What it does NOT do

- Suggest new sources/links
- Suggest new content/information
- Rewrite headlines wholesale (it suggests; `improve_section` rewrites)

## Tuning knobs

- `default_engine="subagent"`, `reasoning_effort=8`. Override per environment if cost matters.
