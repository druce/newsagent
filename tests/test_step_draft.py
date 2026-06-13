"""Tests for lib/steps/draft.py — parallel section drafting with critic-optimizer loop."""
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

class _SectionDraft(BaseModel):
    section_markdown: str


class _CritiqueResult(BaseModel):
    score: float
    feedback: str
    accept: bool


def _seed_state(tmp_db: str, session_id: str = "s1") -> NewsletterAgentState:
    """Create a state with two clusters × 2 stories each."""
    init_db(tmp_db)
    state = NewsletterAgentState(session_id=session_id, db_path=tmp_db)
    for s in ["start", "gather", "filter", "download", "summarize", "rate", "cluster", "select"]:
        state.complete_step(s)

    state.newsletter_section_data = [
        # Cluster A
        {
            "cat": "Artificial Intelligence",
            "headline": "AI makes a breakthrough",
            "link": "https://ai.com/1",
            "summary": "Researchers announced a new AI model.",
            "rating": 9.0,
            "id": 0,
        },
        {
            "cat": "Artificial Intelligence",
            "headline": "AI funding rounds surge",
            "link": "https://ai.com/2",
            "summary": "Startups raised record amounts.",
            "rating": 8.0,
            "id": 1,
        },
        # Cluster B
        {
            "cat": "Robotics",
            "headline": "Robot performs surgery",
            "link": "https://robotics.com/1",
            "summary": "A new surgical robot passed trials.",
            "rating": 7.0,
            "id": 2,
        },
        {
            "cat": "Robotics",
            "headline": "Robots deployed in warehouses",
            "link": "https://robotics.com/2",
            "summary": "Logistics companies scale up automation.",
            "rating": 6.0,
            "id": 3,
        },
    ]
    state.save_checkpoint("select")
    return state


def _make_fake_call_prompt(engine_log=None):
    """Return a fake call_prompt that serves per-prompt responses."""
    def fake(name, inputs, *, engine=None):
        if engine_log is not None:
            engine_log.append((name, engine))
        if name == "write_section":
            title = inputs.get("section_title", "Section") if isinstance(inputs, dict) else "Section"
            return _SectionDraft(section_markdown=f"## {title}\n- Story 1\n- Story 2\n")
        elif name == "critique_section":
            return _CritiqueResult(score=9.0, feedback="looks great", accept=True)
        elif name == "improve_section":
            md = inputs.get("section_markdown", "") if isinstance(inputs, dict) else ""
            return _SectionDraft(section_markdown=md + " (improved)")
        raise ValueError(f"Unexpected prompt: {name}")
    return fake


# ---------------------------------------------------------------------------
# Test 0: story payload prefers resolved publisher name over link hostname
# ---------------------------------------------------------------------------

def test_section_input_prefers_site_name_over_hostname():
    """source = site_name when present (set by download), else link hostname."""
    from lib.steps.draft import _section_input

    stories = [
        {
            "headline": "Redirected story",
            "link": "https://www.bloomberg.com/news/articles/real-story",
            "site_name": "Bloomberg",
            "summary": "s",
            "rating": 1.0,
        },
        {
            "headline": "Direct story",
            "link": "https://ai.com/2",
            "summary": "s",
            "rating": 2.0,
        },
    ]
    out = _section_input("Cat", stories)
    assert out["stories"][0]["source"] == "Bloomberg"
    assert out["stories"][1]["source"] == "ai.com"


# ---------------------------------------------------------------------------
# Test 1: drafts one markdown per cluster
# ---------------------------------------------------------------------------

def test_draft_produces_one_section_per_cluster(tmp_db, monkeypatch, tmp_path):
    """Each unique cat in newsletter_section_data gets exactly one markdown output."""
    monkeypatch.chdir(tmp_path)
    _seed_state(tmp_db, session_id="s1")

    from lib.steps.draft import cli as draft_cli

    fake = _make_fake_call_prompt()
    with patch("lib.steps.draft.call_prompt", side_effect=fake), \
         patch("lib.critic.call_prompt", side_effect=fake):
        runner = CliRunner()
        result = runner.invoke(draft_cli, ["--db", tmp_db, "--session", "s1",
                                           "--max-edits", "2", "--parallelism", "2"])

    assert result.exit_code == 0, result.output

    state = NewsletterAgentState(session_id="s1", db_path=tmp_db).load_latest_from_db()
    cats = [s["cat"] for s in state.newsletter_section_data]
    assert len(cats) == 2, f"Expected 2 sections, got {len(cats)}: {cats}"
    assert "Artificial Intelligence" in cats
    assert "Robotics" in cats
    # Each entry must have section_markdown
    for s in state.newsletter_section_data:
        assert "section_markdown" in s
        assert len(s["section_markdown"]) > 0


# ---------------------------------------------------------------------------
# Test 2: --max-edits 0 produces drafts but no critic calls
# ---------------------------------------------------------------------------

def test_draft_max_edits_zero_skips_critic(tmp_db, monkeypatch, tmp_path):
    """With --max-edits 0, write_section is called but critique_section is not."""
    monkeypatch.chdir(tmp_path)
    _seed_state(tmp_db, session_id="s2")

    from lib.steps.draft import cli as draft_cli

    call_log = []

    def tracking_fake(name, inputs, *, engine=None):
        call_log.append(name)
        if name == "write_section":
            title = inputs.get("section_title", "S") if isinstance(inputs, dict) else "S"
            return _SectionDraft(section_markdown=f"## {title}\n- story\n")
        raise ValueError(f"Unexpected prompt with max_edits=0: {name}")

    with patch("lib.steps.draft.call_prompt", side_effect=tracking_fake):
        runner = CliRunner()
        result = runner.invoke(draft_cli, ["--db", tmp_db, "--session", "s2",
                                           "--max-edits", "0"])

    assert result.exit_code == 0, result.output
    # Only write_section calls — no critique_section or improve_section
    assert all(n == "write_section" for n in call_log), (
        f"Expected only write_section calls, got: {call_log}"
    )
    # But we still get 2 sections
    state = NewsletterAgentState(session_id="s2", db_path=tmp_db).load_latest_from_db()
    assert len(state.newsletter_section_data) == 2


# ---------------------------------------------------------------------------
# Test 3: runs/<SID>/draft.json is written with per-section transcripts
# ---------------------------------------------------------------------------

def test_draft_writes_transcript_json(tmp_db, monkeypatch, tmp_path):
    """runs/<SID>/draft.json must exist and contain transcript data per section."""
    monkeypatch.chdir(tmp_path)
    _seed_state(tmp_db, session_id="s3")

    from lib.steps.draft import cli as draft_cli

    fake = _make_fake_call_prompt()
    with patch("lib.steps.draft.call_prompt", side_effect=fake), \
         patch("lib.critic.call_prompt", side_effect=fake):
        runner = CliRunner()
        result = runner.invoke(draft_cli, ["--db", tmp_db, "--session", "s3"])

    assert result.exit_code == 0, result.output

    artifact = tmp_path / "runs" / "s3" / "draft.json"
    assert artifact.exists(), f"draft.json not found at {artifact}"

    data = json.loads(artifact.read_text())
    assert "sections" in data, f"'sections' key missing from draft.json: {data.keys()}"
    sections = data["sections"]
    assert len(sections) == 2, f"Expected 2 sections in transcript, got {len(sections)}"
    for sec in sections:
        assert "cat" in sec
        assert "iterations" in sec
        assert "accepted" in sec
        assert "scores" in sec


# ---------------------------------------------------------------------------
# Test 4: --engine override propagates to all call_prompt calls
# ---------------------------------------------------------------------------

def test_draft_engine_override_propagates(tmp_db, monkeypatch, tmp_path):
    """All call_prompt calls should receive the --engine value."""
    monkeypatch.chdir(tmp_path)
    _seed_state(tmp_db, session_id="s4")

    from lib.steps.draft import cli as draft_cli

    engine_log = []
    fake = _make_fake_call_prompt(engine_log=engine_log)

    # Patch both the step module and the critic module (which calls call_prompt internally)
    with patch("lib.steps.draft.call_prompt", side_effect=fake), \
         patch("lib.critic.call_prompt", side_effect=fake):
        runner = CliRunner()
        result = runner.invoke(draft_cli, [
            "--db", tmp_db, "--session", "s4",
            "--max-edits", "1",
            "--engine", "openrouter:test-model",
        ])

    assert result.exit_code == 0, result.output
    # All calls should use the overridden engine
    engines_used = [eng for _, eng in engine_log]
    assert all(e == "openrouter:test-model" for e in engines_used), (
        f"Some calls used wrong engine: {engine_log}"
    )


# ---------------------------------------------------------------------------
# Test 5: --prepare-batches writes one batch file per section
# ---------------------------------------------------------------------------

def test_draft_prepare_writes_one_batch_per_section(tmp_db, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    _seed_state(tmp_db, session_id="d_prep")

    from lib.steps.draft import cli as draft_cli

    runner = CliRunner()
    result = runner.invoke(draft_cli, [
        "--db", tmp_db, "--session", "d_prep", "--prepare-batches",
        "--max-edits", "2",
    ])
    assert result.exit_code == 0, result.output

    batches_dir = Path("runs/d_prep/draft-batches")
    files = sorted(batches_dir.glob("batch-*.json"))
    # One batch per cat in the seeded state
    assert len(files) >= 1

    payload = json.loads(files[0].read_text())
    assert "cat" in payload
    assert "stories" in payload
    assert payload["max_edits"] == 2
    # All three prompts pre-rendered + present
    for key in ("write_system_prompt", "write_user_prompt",
                "critique_system_prompt", "critique_user_prompt",
                "improve_system_prompt", "improve_user_prompt"):
        assert key in payload and payload[key]
    # Output schema is DraftSectionResult
    schema = payload["output_schema"]
    assert "final_section_markdown" in schema["properties"]
    assert "iterations" in schema["properties"]


# ---------------------------------------------------------------------------
# Test 6: --apply-results reads section transcripts and writes markdowns
# ---------------------------------------------------------------------------

def test_draft_apply_reads_results_and_writes_sections(tmp_db, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    _seed_state(tmp_db, session_id="d_apply")

    from lib.steps.draft import cli as draft_cli

    runner = CliRunner()
    prep = runner.invoke(draft_cli, [
        "--db", tmp_db, "--session", "d_apply", "--prepare-batches",
    ])
    assert prep.exit_code == 0, prep.output

    # Figure out which cats were prepared
    batch_files = sorted(Path("runs/d_apply/draft-batches").glob("batch-*.json"))
    cats = [json.loads(f.read_text())["cat"] for f in batch_files]
    assert len(cats) >= 1

    results_dir = Path("runs/d_apply/draft-results")
    results_dir.mkdir(parents=True)
    for i, cat in enumerate(cats):
        (results_dir / f"batch-{i:03d}.json").write_text(json.dumps({
            "cat": cat,
            "final_section_markdown": f"## {cat}\n- fake headline",
            "iterations": 1,
            "scores": [7.5],
            "feedbacks": ["needs more"],
            "accepted": False,
        }))

    apply_res = runner.invoke(draft_cli, [
        "--db", tmp_db, "--session", "d_apply",
        "--apply-results", str(results_dir),
    ])
    assert apply_res.exit_code == 0, apply_res.output

    state = NewsletterAgentState(session_id="d_apply", db_path=tmp_db).load_latest_from_db()
    assert state is not None
    section_cats = {s["cat"] for s in state.newsletter_section_data}
    assert section_cats == set(cats)
    for s in state.newsletter_section_data:
        assert s["section_markdown"].startswith("## ")
