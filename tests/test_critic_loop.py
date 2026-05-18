"""Tests for lib/critic.py — generic critic-optimizer loop."""
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
