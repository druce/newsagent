"""Tests for lib/steps/rewrite.py — whole-newsletter critic loop + title generation."""
import json
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner
from pydantic import BaseModel

import lib.prompts  # noqa: F401
from lib.db import init_db
from lib.state import NewsletterAgentState


# ---------------------------------------------------------------------------
# Shared fakes
# ---------------------------------------------------------------------------

class _NewsletterDraft(BaseModel):
    newsletter_markdown: str


class _CritiqueResult(BaseModel):
    score: float
    feedback: str
    accept: bool


class _NewsletterTitle(BaseModel):
    title: str


def _seed_state(tmp_db: str, session_id: str = "s1") -> NewsletterAgentState:
    """Create state with two sections already drafted."""
    init_db(tmp_db)
    state = NewsletterAgentState(session_id=session_id, db_path=tmp_db)
    for s in ["init", "gather", "filter", "download", "summarize",
              "rate", "cluster", "select", "draft"]:
        state.complete_step(s)

    state.newsletter_section_data = [
        {
            "cat": "Artificial Intelligence",
            "section_markdown": "## Artificial Intelligence\n- AI story 1 — [Source](https://ai.com/1)\n",
        },
        {
            "cat": "Robotics",
            "section_markdown": "## Robotics\n- Robotics story 1 — [Source](https://robotics.com/1)\n",
        },
    ]
    state.save_checkpoint("draft")
    return state


def _make_fake_call_prompt(engine_log=None):
    """Fake call_prompt serving per-prompt responses."""
    def fake(name, inputs, *, engine=None):
        if engine_log is not None:
            engine_log.append((name, engine))
        if name == "critique_newsletter":
            return _CritiqueResult(score=9.0, feedback="excellent", accept=True)
        elif name == "improve_newsletter":
            md = inputs.get("newsletter_markdown", "") if isinstance(inputs, dict) else ""
            return _NewsletterDraft(newsletter_markdown=md + " (improved)")
        elif name == "generate_newsletter_title":
            return _NewsletterTitle(title="AI and Robotics Weekly Digest")
        raise ValueError(f"Unexpected prompt: {name}")
    return fake


# ---------------------------------------------------------------------------
# Test 1: produces final_newsletter with H1 title prepended
# ---------------------------------------------------------------------------

def test_rewrite_produces_final_newsletter_with_title(tmp_db, monkeypatch, tmp_path):
    """final_newsletter must start with '# <title>' and contain section content."""
    monkeypatch.chdir(tmp_path)
    _seed_state(tmp_db, session_id="s1")

    from lib.steps.rewrite import cli as rewrite_cli

    fake = _make_fake_call_prompt()
    with patch("lib.critic.call_prompt", side_effect=fake), \
         patch("lib.steps.rewrite.call_prompt", side_effect=fake):
        runner = CliRunner()
        result = runner.invoke(rewrite_cli, ["--db", tmp_db, "--session", "s1",
                                              "--max-edits", "2"])

    assert result.exit_code == 0, result.output

    state = NewsletterAgentState(session_id="s1", db_path=tmp_db).load_latest_from_db()
    assert state.newsletter_title == "AI and Robotics Weekly Digest"
    assert state.final_newsletter.startswith("# AI and Robotics Weekly Digest"), (
        f"final_newsletter does not start with H1 title: {state.final_newsletter[:80]}"
    )
    assert len(state.final_newsletter) > 50


# ---------------------------------------------------------------------------
# Test 2: concatenates section_markdown from both sections
# ---------------------------------------------------------------------------

def test_rewrite_concatenates_sections(tmp_db, monkeypatch, tmp_path):
    """The newsletter body must contain content from both sections."""
    monkeypatch.chdir(tmp_path)
    _seed_state(tmp_db, session_id="s2")

    from lib.steps.rewrite import cli as rewrite_cli

    # Use a fake that returns the newsletter_markdown unchanged (no improvement)
    # so we can verify the concatenation
    def passthrough_fake(name, inputs, *, engine=None):
        if name == "critique_newsletter":
            return _CritiqueResult(score=9.0, feedback="great", accept=True)
        elif name == "improve_newsletter":
            md = inputs.get("newsletter_markdown", "") if isinstance(inputs, dict) else ""
            return _NewsletterDraft(newsletter_markdown=md)
        elif name == "generate_newsletter_title":
            return _NewsletterTitle(title="Test Title")
        raise ValueError(f"Unexpected prompt: {name}")

    with patch("lib.critic.call_prompt", side_effect=passthrough_fake), \
         patch("lib.steps.rewrite.call_prompt", side_effect=passthrough_fake):
        runner = CliRunner()
        result = runner.invoke(rewrite_cli, ["--db", tmp_db, "--session", "s2"])

    assert result.exit_code == 0, result.output

    state = NewsletterAgentState(session_id="s2", db_path=tmp_db).load_latest_from_db()
    # Both section markdowns should appear in final_newsletter
    assert "## Artificial Intelligence" in state.final_newsletter, (
        "AI section not found in final_newsletter"
    )
    assert "## Robotics" in state.final_newsletter, (
        "Robotics section not found in final_newsletter"
    )


# ---------------------------------------------------------------------------
# Test 3: writes runs/<SID>/rewrite.json with transcript + title
# ---------------------------------------------------------------------------

def test_rewrite_writes_artifact_json(tmp_db, monkeypatch, tmp_path):
    """runs/<SID>/rewrite.json must exist and contain transcript + title fields."""
    monkeypatch.chdir(tmp_path)
    _seed_state(tmp_db, session_id="s3")

    from lib.steps.rewrite import cli as rewrite_cli

    fake = _make_fake_call_prompt()
    with patch("lib.critic.call_prompt", side_effect=fake), \
         patch("lib.steps.rewrite.call_prompt", side_effect=fake):
        runner = CliRunner()
        result = runner.invoke(rewrite_cli, ["--db", tmp_db, "--session", "s3"])

    assert result.exit_code == 0, result.output

    artifact = tmp_path / "runs" / "s3" / "rewrite.json"
    assert artifact.exists(), f"rewrite.json not found at {artifact}"

    data = json.loads(artifact.read_text())
    assert "title" in data, f"'title' key missing from rewrite.json: {data.keys()}"
    assert "transcript" in data, f"'transcript' key missing from rewrite.json: {data.keys()}"
    transcript = data["transcript"]
    assert "iterations" in transcript
    assert "accepted" in transcript
    assert "scores" in transcript
    assert data["title"] == "AI and Robotics Weekly Digest"
