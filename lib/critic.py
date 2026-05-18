"""Generic critic-optimizer loop used by news:draft and news:rewrite."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

from lib.llm import call_prompt


@dataclass
class CriticTranscript:
    """Trace of one critic-optimizer run, useful for runs/<SID>/draft.json and debugging."""
    iterations: int
    final_draft: str
    scores: list
    feedbacks: list
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
        draft_field: Output schema attribute that holds the revised markdown
            (e.g. "section_markdown" or "newsletter_markdown").
        max_edits: Maximum revise iterations after the initial draft.
        accept_threshold: If critic.score >= this, short-circuit.
        engine: Override engine for both calls.

    Returns:
        CriticTranscript with the iteration trace.
    """
    draft = initial_draft
    scores: list = []
    feedbacks: list = []
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
