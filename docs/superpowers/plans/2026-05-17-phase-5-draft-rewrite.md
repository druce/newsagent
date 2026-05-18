# Phase 5 — `draft` + `rewrite` (Critic-Optimizer Loops)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Two final pipeline LLM steps. `news:draft` writes each newsletter section using a parallel **critic-optimizer** loop (draft → critique → improve, up to `--max-edits` iterations). `news:rewrite` assembles all section drafts into one newsletter, runs a whole-newsletter critic-optimizer pass, and generates the title. After Phase 5 the system produces a real newsletter end-to-end.

**Architecture:**
- **`news:draft`**: groups `state.newsletter_section_data` by `cat` (cluster name); for each cluster spawns one section-drafting task running in a thread pool (`parallelism=K`, default 4). Each task: `WRITE_SECTION → CRITIQUE_SECTION → IMPROVE_SECTION → critique → improve …` up to `--max-edits` iterations (default 2). Critic returns `{score, feedback, accept}` — early-exit when `accept=True`. Outputs section markdown for each cluster.
- **`news:rewrite`**: concatenates all section markdowns into a draft, runs `CRITIQUE_NEWSLETTER → IMPROVE_NEWSLETTER` up to `--max-edits`. Generates final title via `GENERATE_NEWSLETTER_TITLE`. Writes `state.final_newsletter` (markdown) and `state.newsletter_title`.
- Critic-optimizer is generic: extracted into `lib/critic.py` so both steps reuse the same loop pattern.

**Hard constraints:**
- No Anthropic SDK; allowed engines per memory: subagent, openrouter, openai, google.
- PromptConfig binds model + reasoning_effort per legacy style.

**Tech additions:** none — Phase 4's `numpy`/`scipy` + Python stdlib (`ThreadPoolExecutor`) is enough.

**Reference (read, don't import):**
- `~/projects/OpenAIAgentsSDK/prompts.py:824` — `WRITE_SECTION`
- `~/projects/OpenAIAgentsSDK/prompts.py:966` — `CRITIQUE_SECTION`
- `~/projects/OpenAIAgentsSDK/prompts.py:877` — `CRITIQUE_NEWSLETTER`
- `~/projects/OpenAIAgentsSDK/prompts.py:1118` — `IMPROVE_NEWSLETTER`
- `~/projects/OpenAIAgentsSDK/prompts.py:1174` — `GENERATE_NEWSLETTER_TITLE`
- `~/projects/OpenAIAgentsSDK/CRITIC_OPTIMIZER_LOOP_IMPLEMENTATION.md` — legacy loop design notes (background reading)

## File structure (new files)

| Path | Purpose |
|---|---|
| `lib/prompts/write_section.py` | Section drafter (port + simplified output schema) |
| `lib/prompts/critique_section.py` | Section critic |
| `lib/prompts/improve_section.py` | Section optimizer (NEW — takes draft+critique → revised) |
| `lib/prompts/critique_newsletter.py` | Newsletter critic |
| `lib/prompts/improve_newsletter.py` | Newsletter optimizer |
| `lib/prompts/generate_title.py` | Title generator |
| `lib/critic.py` | Generic `critic_optimizer_loop(initial, critique_prompt, improve_prompt, max_edits)` |
| `lib/steps/draft.py` | news:draft CLI — parallel section drafters |
| `lib/steps/rewrite.py` | news:rewrite CLI — newsletter critic loop + title |
| `skills/draft/SKILL.md`, `skills/rewrite/SKILL.md` | Agent-facing contracts |
| `agents/section-drafter.md`, `agents/section-critic.md`, `agents/newsletter-critic.md` | Reference persona templates (markdown only, no SKILL.md) |
| `tests/test_critic_loop.py` |  |
| `tests/test_prompt_write_section.py` |  |
| `tests/test_prompt_critique_section.py` |  |
| `tests/test_prompt_improve_section.py` |  |
| `tests/test_prompt_critique_newsletter.py` |  |
| `tests/test_prompt_improve_newsletter.py` |  |
| `tests/test_prompt_generate_title.py` |  |
| `tests/test_step_draft.py` |  |
| `tests/test_step_rewrite.py` |  |

## Shared output schema for sections

Each section produced by `news:draft` is a markdown string. We don't try to enforce structured output — the LLM's section markdown is the artifact. A simple Pydantic wrapper holds it:

```python
class SectionDraft(BaseModel):
    section_markdown: str
```

Critic outputs:
```python
class CritiqueResult(BaseModel):
    score: float           # 0-10
    feedback: str          # what to improve
    accept: bool           # short-circuit signal: score >= 8.0
```

This unified critic schema is shared by both `critique_section` and `critique_newsletter`.

---

## Task 1: Port `WRITE_SECTION` (simplified to markdown output)

**Files:**
- Create: `lib/prompts/write_section.py`
- Create: `tests/test_prompt_write_section.py`
- Modify: `lib/prompts/__init__.py`

Legacy `WRITE_SECTION` (prompts.py:824) outputs a structured JSON of headlines + links. For Phase 5 we **simplify**: output is a markdown section (one `## Section Title` heading + bullet list with embedded links). The model produces the rendered section text directly.

Input schema:
```python
class WriteSectionInput(BaseModel):
    section_title: str
    stories: List[Dict[str, Any]]  # {title, url, summary, source, rating}
    @computed_field
    @property
    def stories_json(self) -> str:
        return json.dumps(self.stories, ensure_ascii=False, indent=2)
```

Output: `SectionDraft` (single field `section_markdown: str`).

Prompt: adapt legacy `WRITE_SECTION` system_prompt — keep the workflow numbered steps but replace the JSON output instruction with "Output: a markdown section in this format:"
```
## <Section Title>
- <crisp headline> — [<Source>](url), [<Source>](url2)
- <next headline> — [<Source>](url)
...
```

`default_engine="subagent"`, `reasoning_effort=8` (matches legacy).

User prompt:
```
SECTION TITLE (proposed): {section_title}

STORIES (JSON, sorted by rating):
{stories_json}

Write the section as markdown.
```

Tests (3): registered, default engine + reasoning_effort, accepts valid input schema.

- [ ] Tests → fail → implement → pass → commit `feat(prompts): port WRITE_SECTION (markdown output)`

---

## Task 2: NEW `CRITIQUE_SECTION` prompt (unified critic schema)

**Files:**
- Create: `lib/prompts/critique_section.py`
- Create: `tests/test_prompt_critique_section.py`
- Modify: `lib/prompts/__init__.py`

Port legacy CRITIQUE_SECTION text but simplify output to the unified `CritiqueResult` schema. Drop legacy's structured-action JSON.

Input:
```python
class CritiqueSectionInput(BaseModel):
    section_markdown: str
```

Output: `CritiqueResult` (score 0-10, feedback str, accept bool).

`default_engine="subagent"`, `reasoning_effort=8`.

System prompt — paraphrase legacy CRITIQUE_SECTION but ask for:
```
Score the section 0-10 across coherence, headline quality, ordering.
Return: { "score": float, "feedback": "concrete edits to make", "accept": bool (true if score >= 8.0) }
```

Keep the quality guidelines block from legacy (headline length, sentence case, no clickbait, 2-7 stories, etc.).

User prompt:
```
Section markdown:
{section_markdown}

Critique this section.
```

Tests (3): registered, output schema validates, system prompt has key quality phrases.

- [ ] Tests → fail → implement → pass → commit `feat(prompts): NEW critique_section with unified critic schema`

---

## Task 3: NEW `IMPROVE_SECTION` prompt

**Files:**
- Create: `lib/prompts/improve_section.py`
- Create: `tests/test_prompt_improve_section.py`
- Modify: `lib/prompts/__init__.py`

No legacy equivalent. Takes a draft + critique feedback → revised draft.

Input:
```python
class ImproveSectionInput(BaseModel):
    section_markdown: str
    critique: str
```

Output: `SectionDraft` (revised markdown).

`default_engine="subagent"`, `reasoning_effort=6`.

System prompt:
```
You are a newsletter editor revising a section draft based on a critique. Apply the critique's recommendations precisely. Do NOT add new information, do NOT change source URLs, do NOT introduce new stories. Return ONLY the revised markdown section.
```

User prompt:
```
Original section:
{section_markdown}

Critique:
{critique}

Return the revised section as markdown.
```

Tests (3): registered, schemas, system prompt forbids new info.

- [ ] Tests → fail → implement → pass → commit `feat(prompts): NEW improve_section`

---

## Task 4: Port `CRITIQUE_NEWSLETTER` (unified critic schema)

**Files:**
- Create: `lib/prompts/critique_newsletter.py`
- Create: `tests/test_prompt_critique_newsletter.py`
- Modify: `lib/prompts/__init__.py`

Same unified `CritiqueResult` schema. Legacy `CRITIQUE_NEWSLETTER` (prompts.py:877) has a multi-dimensional rubric (title_quality, structure_quality, section_quality, headline_quality, overall_score, should_iterate, critique_text). For Phase 5 we collapse to the unified `{score, feedback, accept}` shape — `score` = overall_score, `accept` = should_iterate inverted, `feedback` = critique_text.

Input:
```python
class CritiqueNewsletterInput(BaseModel):
    newsletter_markdown: str
```

Output: `CritiqueResult`.

`default_engine="subagent"`, `reasoning_effort=8`.

System prompt: paraphrase legacy — keep the rubric (title, structure, section, headline) but ask for overall score 0-10 + feedback + accept bool.

User prompt:
```
Newsletter draft:
{newsletter_markdown}

Critique:
```

Tests (3): registered, schema, system prompt mentions rubric concepts.

- [ ] Tests → fail → implement → pass → commit `feat(prompts): port CRITIQUE_NEWSLETTER (unified critic schema)`

---

## Task 5: Port `IMPROVE_NEWSLETTER`

**Files:**
- Create: `lib/prompts/improve_newsletter.py`
- Create: `tests/test_prompt_improve_newsletter.py`
- Modify: `lib/prompts/__init__.py`

Legacy `IMPROVE_NEWSLETTER` (prompts.py:1118). Simplify output to a single `revised: str` field.

Input:
```python
class ImproveNewsletterInput(BaseModel):
    newsletter_markdown: str
    critique: str
```

Output:
```python
class NewsletterDraft(BaseModel):
    newsletter_markdown: str
```

`default_engine="subagent"`, `reasoning_effort=6` (matches legacy).

Port system prompt text mostly verbatim — keep "do not introduce new info, do not modify links" guards.

Tests (3): registered, schemas, system forbids new info.

- [ ] Tests → fail → implement → pass → commit `feat(prompts): port IMPROVE_NEWSLETTER`

---

## Task 6: Port `GENERATE_NEWSLETTER_TITLE`

**Files:**
- Create: `lib/prompts/generate_title.py`
- Create: `tests/test_prompt_generate_title.py`
- Modify: `lib/prompts/__init__.py`

Legacy `GENERATE_NEWSLETTER_TITLE` (prompts.py:1174).

Input:
```python
class GenerateTitleInput(BaseModel):
    newsletter_markdown: str
```

Output:
```python
class NewsletterTitle(BaseModel):
    title: str  # 6-12 words, factual, active voice
```

`default_engine="subagent"`, `reasoning_effort=8`.

Tests (2): registered, schema.

- [ ] Tests → fail → implement → pass → commit `feat(prompts): port GENERATE_NEWSLETTER_TITLE`

---

## Task 7: `lib/critic.py` — generic critic-optimizer loop

**Files:**
- Create: `lib/critic.py`
- Create: `tests/test_critic_loop.py`

```python
# lib/critic.py
"""Generic critic-optimizer loop used by news:draft and news:rewrite."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Optional

from lib.llm import call_prompt


@dataclass
class CriticTranscript:
    """Trace of one critic-optimizer run, useful for runs/<SID>/draft.json and debugging."""
    iterations: int
    final_draft: str
    scores: list[float]
    feedbacks: list[str]
    accepted: bool


def critic_optimizer_loop(
    initial_draft: str,
    critique_prompt_name: str,
    improve_prompt_name: str,
    critique_input_builder: Callable[[str], dict],
    improve_input_builder: Callable[[str, str], dict],
    draft_field: str,
    max_edits: int = 2,
    accept_threshold: float = 8.0,
    engine: Optional[str] = None,
) -> CriticTranscript:
    """Drive draft → critique → improve → ... loop until accept or max_edits.

    Args:
        initial_draft: Markdown of the first draft.
        critique_prompt_name: Registered prompt name for the critic.
        improve_prompt_name: Registered prompt name for the optimizer.
        critique_input_builder: Maps current_draft -> input dict for critique call.
        improve_input_builder: Maps (current_draft, critique_feedback) -> input dict for improve call.
        draft_field: Output schema attribute that holds the revised markdown (e.g. "section_markdown" or "newsletter_markdown").
        max_edits: Maximum revise iterations after the initial draft.
        accept_threshold: If critic.score >= this, short-circuit.
        engine: Override engine for both calls.

    Returns:
        CriticTranscript with the iteration trace.
    """
    draft = initial_draft
    scores: list[float] = []
    feedbacks: list[str] = []
    accepted = False
    iterations = 0

    for _i in range(max_edits):
        iterations += 1
        critique = call_prompt(critique_prompt_name,
                               critique_input_builder(draft),
                               engine=engine)
        scores.append(float(critique.score))
        feedbacks.append(critique.feedback)
        if critique.accept or critique.score >= accept_threshold:
            accepted = True
            break

        improved = call_prompt(improve_prompt_name,
                               improve_input_builder(draft, critique.feedback),
                               engine=engine)
        draft = getattr(improved, draft_field)

    return CriticTranscript(
        iterations=iterations,
        final_draft=draft,
        scores=scores,
        feedbacks=feedbacks,
        accepted=accepted,
    )
```

Tests (4):

```python
# tests/test_critic_loop.py
from unittest.mock import patch
from lib.critic import critic_optimizer_loop
from pydantic import BaseModel


class _Critique(BaseModel):
    score: float
    feedback: str
    accept: bool


class _Draft(BaseModel):
    section_markdown: str


def _fake_critic_and_improver(critiques, improvements):
    """Build a `call_prompt` mock that returns critiques and improvements in order."""
    calls = {"crit": 0, "impr": 0}

    def fake(name, inputs, *, engine=None):
        if "critique" in name:
            r = critiques[calls["crit"]]
            calls["crit"] += 1
            return r
        else:
            r = improvements[calls["impr"]]
            calls["impr"] += 1
            return r
    return fake


def test_critic_loop_short_circuits_on_accept(monkeypatch):
    critiques = [_Critique(score=9.0, feedback="great", accept=True)]
    improvements: list = []
    monkeypatch.setattr("lib.critic.call_prompt",
                        _fake_critic_and_improver(critiques, improvements))
    t = critic_optimizer_loop(
        "draft v0",
        critique_prompt_name="critique_section",
        improve_prompt_name="improve_section",
        critique_input_builder=lambda d: {"section_markdown": d},
        improve_input_builder=lambda d, c: {"section_markdown": d, "critique": c},
        draft_field="section_markdown",
        max_edits=2,
    )
    assert t.iterations == 1
    assert t.accepted is True
    assert t.final_draft == "draft v0"  # no improvement applied


def test_critic_loop_iterates_max_edits(monkeypatch):
    critiques = [
        _Critique(score=5.0, feedback="too short", accept=False),
        _Critique(score=6.0, feedback="still short", accept=False),
    ]
    improvements = [
        _Draft(section_markdown="draft v1"),
        _Draft(section_markdown="draft v2"),
    ]
    monkeypatch.setattr("lib.critic.call_prompt",
                        _fake_critic_and_improver(critiques, improvements))
    t = critic_optimizer_loop(
        "draft v0",
        critique_prompt_name="critique_section",
        improve_prompt_name="improve_section",
        critique_input_builder=lambda d: {"section_markdown": d},
        improve_input_builder=lambda d, c: {"section_markdown": d, "critique": c},
        draft_field="section_markdown",
        max_edits=2,
    )
    assert t.iterations == 2
    assert t.final_draft == "draft v2"
    assert t.scores == [5.0, 6.0]
    assert t.accepted is False


def test_critic_loop_max_edits_zero_returns_initial(monkeypatch):
    monkeypatch.setattr("lib.critic.call_prompt", lambda *_a, **_k: None)
    t = critic_optimizer_loop(
        "draft v0",
        critique_prompt_name="critique_section",
        improve_prompt_name="improve_section",
        critique_input_builder=lambda d: {"section_markdown": d},
        improve_input_builder=lambda d, c: {"section_markdown": d, "critique": c},
        draft_field="section_markdown",
        max_edits=0,
    )
    assert t.iterations == 0
    assert t.final_draft == "draft v0"


def test_critic_loop_stops_when_score_threshold_met(monkeypatch):
    """`accept=False` but `score >= accept_threshold` should still short-circuit."""
    critiques = [
        _Critique(score=8.5, feedback="good", accept=False),
    ]
    monkeypatch.setattr("lib.critic.call_prompt",
                        _fake_critic_and_improver(critiques, []))
    t = critic_optimizer_loop(
        "draft v0",
        critique_prompt_name="critique_section",
        improve_prompt_name="improve_section",
        critique_input_builder=lambda d: {"section_markdown": d},
        improve_input_builder=lambda d, c: {"section_markdown": d, "critique": c},
        draft_field="section_markdown",
        max_edits=3,
        accept_threshold=8.0,
    )
    assert t.iterations == 1
    assert t.accepted is True
```

- [ ] Tests → fail → implement → pass → commit `feat: critic-optimizer loop helper`

---

## Task 8: `news:draft` step + SKILL.md

**Files:**
- Create: `lib/steps/draft.py`
- Create: `skills/draft/SKILL.md`
- Create: `tests/test_step_draft.py`

Logic:
1. Load state. Group `state.newsletter_section_data` by `cat`.
2. For each cluster (cat), build the input: `{section_title: cat, stories: [{title, url, summary, source, rating}...]}`.
3. In a `ThreadPoolExecutor(max_workers=K)`, dispatch one task per section. Each task:
   - Calls `write_section` to get initial draft.
   - Runs `critic_optimizer_loop` with `critique_section` + `improve_section`, max_edits=`--max-edits`.
   - Returns the final markdown.
4. Assemble: `state.newsletter_section_data` becomes `[{cat: ..., section_markdown: ...}]` (one row per cluster now, replacing the per-headline rows).
5. Write `runs/<SID>/draft.json` with per-section transcript (iterations, final score, accepted bool).
6. Mark `draft` step complete.

`--max-edits` default 2, `--parallelism` default 4.

Tests (3-4):
- Drafts one markdown per cluster
- max_edits=0 produces drafts but no critic
- Writes transcript JSON
- Engine override via `--engine` propagates

Use `monkeypatch` to mock `call_prompt`. Mock can return different responses for different prompt names.

- [ ] Tests → fail → implement → pass → commit `feat(steps): news:draft parallel section drafting with critic loop`
- [ ] SKILL.md → commit `docs(skills): news:draft SKILL.md`

---

## Task 9: `news:rewrite` step + SKILL.md

**Files:**
- Create: `lib/steps/rewrite.py`
- Create: `skills/rewrite/SKILL.md`
- Create: `tests/test_step_rewrite.py`

Logic:
1. Load state. Concatenate all `section_markdown` from `newsletter_section_data` into a single draft string.
2. Run `critic_optimizer_loop` with `critique_newsletter` + `improve_newsletter`, max_edits=`--max-edits`.
3. Call `generate_title` on the final draft to get `newsletter_title`.
4. Write `state.final_newsletter = "# {title}\n\n{body}"` (prepend the H1).
5. Write `state.newsletter_title`.
6. Write `runs/<SID>/rewrite.json` with critic transcript + title.
7. Mark `rewrite` step complete.

Tests (3):
- Produces final_newsletter with title
- Concatenates section_markdown correctly
- Writes transcript JSON

- [ ] Tests → fail → implement → pass → commit `feat(steps): news:rewrite whole-newsletter critic loop + title`
- [ ] SKILL.md → commit `docs(skills): news:rewrite SKILL.md`

---

## Task 10: Agent persona reference docs

**Files:**
- Create: `agents/section-drafter.md`
- Create: `agents/section-critic.md`
- Create: `agents/newsletter-critic.md`

These are markdown reference files documenting the personas embedded in the prompts. They're NOT Claude Code agent definitions — just human-readable docs explaining "what the section-drafter is supposed to do" so future contributors can tune the prompts without diving into the code. Each file should be ~100-200 words.

- [ ] Write three markdown files, commit `docs(agents): persona reference docs for draft/rewrite prompts`

---

## Task 11: End-to-end Phase 5 verification

- [ ] Full pytest + coverage: `.venv/bin/pytest tests/ --cov=lib --cov-report=term`. All tests pass.
- [ ] Engine resolution sanity for the 6 new prompts.
- [ ] Tag `phase-5-complete`.
- [ ] Optionally: run `news:draft --max-edits 0 --session <test_session>` against a mocked state to verify the wiring without live LLM calls.

---

## Notes for the implementer

- **The critic loop is the heart of Phase 5.** Keep `lib/critic.py` small and well-tested.
- **The unified `CritiqueResult` schema** (score, feedback, accept) is shared by both section and newsletter critics. Don't replicate.
- **Section markdown is the artifact.** Don't try to structure section output as JSON — the model writes markdown directly, and the assembled newsletter is just concatenation.
- **`ThreadPoolExecutor` is enough.** Don't reach for asyncio.
- **`.venv/bin/pytest`** not system pytest.

## Out of scope for Phase 5

- `/news:run` orchestrator (Phase 6)
- Engine flexibility polishing (Phase 6)
- Bluesky (Phase 7)
- Live LLM smoke tests (mocked tests only; live verification can come in Phase 6 with the orchestrator)
