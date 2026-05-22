"""Tests for the redesigned lib/steps/select.py.

Three prepare phases + apply + classic mode:
  Phase A: --prepare-repechage  (LLM mines HDBSCAN noise for new themes)
  Phase B: --prepare-consolidate (LLM unifies HDBSCAN + repechage names)
  Phase C: --prepare-reassign   (LLM assigns every headline to a final name)
  Apply:   --apply-results       (load reassign results → global MMR → sections)
  Classic: in-process pipeline through call_prompt
"""
import json
from collections import defaultdict
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

import lib.prompts  # noqa: F401 — register prompts
from lib.db import init_db
from lib.state import NewsletterAgentState


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _emb(x: float, y: float) -> list:
    return [x, y]


def _seed_state(
    tmp_db: str, session_id: str = "s1",
    headlines: list | None = None,
) -> NewsletterAgentState:
    init_db(tmp_db)
    state = NewsletterAgentState(session_id=session_id, db_path=tmp_db)
    for s in ["init", "gather", "filter", "download", "dedupe",
              "summarize", "rate", "cluster"]:
        state.complete_step(s)

    if headlines is None:
        headlines = []
        # Cluster 0: 4 AI headlines
        for i in range(4):
            headlines.append({
                "id": i,
                "title": f"AI headline {i}",
                "url": f"https://ai.com/{i}",
                "summary": f"AI summary {i}",
                "short_summary": f"AI s{i}",
                "cluster_id": 0,
                "cluster_name": "Artificial Intelligence",
                "embedding": _emb(1.0 + i * 0.01, 0.0),
                "rating": float(4 - i),
            })
        # Cluster 1: 4 robotics
        for i in range(4):
            headlines.append({
                "id": 4 + i,
                "title": f"Robotics headline {i}",
                "url": f"https://robotics.com/{i}",
                "summary": f"Robotics summary {i}",
                "short_summary": f"Rob s{i}",
                "cluster_id": 1,
                "cluster_name": "Robotics",
                "embedding": _emb(0.0, 1.0 + i * 0.01),
                "rating": float(4 - i),
            })
        # Two noise headlines (cluster_id=-1)
        headlines.append({
            "id": 8,
            "title": "Noise headline about AI chips",
            "url": "https://noise.com/0",
            "summary": "About AI chip developments",
            "short_summary": "AI chip story",
            "cluster_id": -1,
            "cluster_name": "",
            "embedding": _emb(1.0, 0.01),
            "rating": 3.0,
        })
        headlines.append({
            "id": 9,
            "title": "Completely unrelated noise",
            "url": "https://noise.com/1",
            "summary": "Off-topic content",
            "short_summary": "off topic",
            "cluster_id": -1,
            "cluster_name": "",
            "embedding": _emb(0.5, 0.5),
            "rating": 1.0,
        })
    state.headline_data = headlines
    by_name: dict = defaultdict(list)
    for h in headlines:
        cid = h.get("cluster_id", -1)
        if cid >= 0:
            by_name[h.get("cluster_name", "")].append(h.get("url", ""))
    state.clusters = dict(by_name)
    state.save_checkpoint("cluster")
    return state


# ---------------------------------------------------------------------------
# Phase A: --prepare-repechage
# ---------------------------------------------------------------------------

def test_prepare_repechage_writes_batches_for_noise_only(tmp_db, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    _seed_state(tmp_db, session_id="s_rep")

    from lib.steps.select import cli as select_cli
    runner = CliRunner()
    result = runner.invoke(select_cli, [
        "--db", tmp_db, "--session", "s_rep",
        "--prepare-repechage", "--repechage-batch-size", "10",
    ])
    assert result.exit_code == 0, result.output

    batches_dir = Path("runs/s_rep/select-repechage-batches")
    files = sorted(batches_dir.glob("batch-*.json"))
    assert len(files) == 1
    payload = json.loads(files[0].read_text())
    assert len(payload["headlines"]) == 2  # only the two noise headlines
    # ids should be the state.headline_data indices of the noise points
    assert set(payload["ids"]) == {"8", "9"}
    assert "system_prompt" in payload
    assert "user_prompt" in payload
    assert "output_schema" in payload


def test_prepare_repechage_no_noise_writes_nothing(tmp_db, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    headlines = [
        {"title": "h", "url": "u", "summary": "s", "short_summary": "ss",
         "cluster_id": 0, "cluster_name": "X", "embedding": [1.0, 0.0],
         "rating": 1.0},
    ]
    _seed_state(tmp_db, session_id="s_rep0", headlines=headlines)
    from lib.steps.select import cli as select_cli
    runner = CliRunner()
    result = runner.invoke(select_cli, [
        "--db", tmp_db, "--session", "s_rep0", "--prepare-repechage",
    ])
    assert result.exit_code == 0, result.output
    batches_dir = Path("runs/s_rep0/select-repechage-batches")
    assert not list(batches_dir.glob("batch-*.json"))


# ---------------------------------------------------------------------------
# Phase B: --prepare-consolidate
# ---------------------------------------------------------------------------

def test_prepare_consolidate_combines_hdbscan_plus_repechage(tmp_db, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    _seed_state(tmp_db, session_id="s_con")

    # Pretend Phase A ran: write a single repechage result that proposes one new cluster.
    rep_dir = Path("runs/s_con/select-repechage-results")
    rep_dir.mkdir(parents=True)
    (rep_dir / "batch-000.json").write_text(json.dumps({
        "proposed_clusters": [
            {"name": "Noise-Mined Theme", "member_ids": ["8", "9"]}
        ],
        "unclustered_ids": [],
    }))

    from lib.steps.select import cli as select_cli
    runner = CliRunner()
    result = runner.invoke(select_cli, [
        "--db", tmp_db, "--session", "s_con", "--prepare-consolidate",
    ])
    assert result.exit_code == 0, result.output

    cbatch = Path("runs/s_con/select-consolidate-batches/batch-000.json")
    assert cbatch.exists()
    payload = json.loads(cbatch.read_text())
    cluster_names = [c["name"] for c in payload["clusters"]]
    cluster_ids = [c["cluster_id"] for c in payload["clusters"]]
    # 2 HDBSCAN + 1 repechage = 3 entries
    assert "Artificial Intelligence" in cluster_names
    assert "Robotics" in cluster_names
    assert "Noise-Mined Theme" in cluster_names
    assert any(cid.startswith("hdbscan-") for cid in cluster_ids)
    assert any(cid.startswith("repechage-") for cid in cluster_ids)


# ---------------------------------------------------------------------------
# Phase C: --prepare-reassign
# ---------------------------------------------------------------------------

def test_prepare_reassign_writes_per_headline_batches(tmp_db, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    _seed_state(tmp_db, session_id="s_rea")

    # Seed repechage + consolidate results.
    Path("runs/s_rea/select-repechage-results").mkdir(parents=True)
    Path("runs/s_rea/select-repechage-results/batch-000.json").write_text(json.dumps({
        "proposed_clusters": [],
        "unclustered_ids": ["8", "9"],
    }))
    # Run prepare-consolidate so the consolidate input batch is on disk.
    from lib.steps.select import cli as select_cli
    runner = CliRunner()
    pc = runner.invoke(select_cli, [
        "--db", tmp_db, "--session", "s_rea", "--prepare-consolidate",
    ])
    assert pc.exit_code == 0, pc.output
    # Now write a consolidate result mapping both HDBSCAN clusters to one final name.
    cres_dir = Path("runs/s_rea/select-consolidate-results")
    cres_dir.mkdir(parents=True)
    (cres_dir / "batch-000.json").write_text(json.dumps({
        "final_names": ["AI & Robotics"],
        "mapping": [
            {"cluster_id": "hdbscan-0", "final_name": "AI & Robotics"},
            {"cluster_id": "hdbscan-1", "final_name": "AI & Robotics"},
        ],
    }))

    result = runner.invoke(select_cli, [
        "--db", tmp_db, "--session", "s_rea",
        "--prepare-reassign", "--reassign-batch-size", "4",
    ])
    assert result.exit_code == 0, result.output

    batches = sorted(Path("runs/s_rea/select-reassign-batches").glob("batch-*.json"))
    # 10 headlines / batch-size 4 = 3 batches (4+4+2)
    assert len(batches) == 3
    payload0 = json.loads(batches[0].read_text())
    assert len(payload0["headlines"]) == 4
    # Every batch carries the same cluster_choices list.
    cluster_names = [c["name"] for c in payload0["clusters"]]
    assert cluster_names == ["AI & Robotics"]


# ---------------------------------------------------------------------------
# Apply: --apply-results runs/<SID>
# ---------------------------------------------------------------------------

def test_apply_results_runs_global_mmr_and_builds_sections(tmp_db, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    _seed_state(tmp_db, session_id="s_app")

    # Write a reassign result that puts everything into one section.
    rdir = Path("runs/s_app/select-reassign-results")
    rdir.mkdir(parents=True)
    assignments = [{"id": str(i), "assignment": "AI & Robotics"} for i in range(10)]
    (rdir / "batch-000.json").write_text(json.dumps({"assignments": assignments}))

    # Stub embed_texts so any missing-embedding path is harmless.
    monkeypatch.setattr(
        "lib.steps.select.embed_texts",
        lambda texts: [[0.0] * 8 for _ in texts],
    )

    from lib.steps.select import cli as select_cli
    runner = CliRunner()
    result = runner.invoke(select_cli, [
        "--db", tmp_db, "--session", "s_app",
        "--apply-results", "runs/s_app",
        "--k", "5",  # global top-K
    ])
    assert result.exit_code == 0, result.output

    state = NewsletterAgentState(session_id="s_app", db_path=tmp_db).load_latest_from_db()
    assert len(state.newsletter_section_data) == 5
    # All sections share the assigned label.
    cats = {s["cat"] for s in state.newsletter_section_data}
    assert cats == {"AI & Robotics"}
    # state.clusters reflects survivors only.
    assert set(state.clusters.keys()) == {"AI & Robotics"}


def test_apply_routes_unmapped_to_other(tmp_db, monkeypatch, tmp_path):
    """Headlines whose ids aren't in the reassign results land in 'Other'."""
    monkeypatch.chdir(tmp_path)
    _seed_state(tmp_db, session_id="s_other")
    # Only assign half the headlines explicitly.
    rdir = Path("runs/s_other/select-reassign-results")
    rdir.mkdir(parents=True)
    (rdir / "batch-000.json").write_text(json.dumps({
        "assignments": [
            {"id": "0", "assignment": "Cluster A"},
            {"id": "1", "assignment": "Other"},
        ]
    }))
    monkeypatch.setattr(
        "lib.steps.select.embed_texts",
        lambda texts: [[0.0] * 8 for _ in texts],
    )

    from lib.steps.select import cli as select_cli
    runner = CliRunner()
    result = runner.invoke(select_cli, [
        "--db", tmp_db, "--session", "s_other",
        "--apply-results", "runs/s_other",
        "--k", "10",
    ])
    assert result.exit_code == 0, result.output

    state = NewsletterAgentState(session_id="s_other", db_path=tmp_db).load_latest_from_db()
    cats = {s["cat"] for s in state.newsletter_section_data}
    # Unassigned items default to "Other".
    assert "Other" in cats
    assert "Cluster A" in cats


# ---------------------------------------------------------------------------
# Classic mode (in-process)
# ---------------------------------------------------------------------------

def test_classic_runs_three_phases_and_finalizes(tmp_db, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    _seed_state(tmp_db, session_id="s_classic")

    def fake_call_prompt(name, inputs, *, engine=None):
        out = MagicMock()
        if name == "extract_noise_clusters":
            # Pretend we found one new cluster covering both noise headlines.
            out.proposed_clusters = [
                MagicMock(name="AI Chips", member_ids=["8", "9"])
            ]
            # MagicMock(name=...) sets the mock's repr name, not an attribute.
            # Use a tiny adapter:
            out.proposed_clusters = [
                type("PC", (), {"name": "AI Chips", "member_ids": ["8", "9"]})()
            ]
            out.unclustered_ids = []
            return out
        if name == "consolidate_cluster_names":
            out.final_names = ["AI & Robotics"]
            out.mapping = [
                type("CM", (), {"cluster_id": "hdbscan-0", "final_name": "AI & Robotics"})(),
                type("CM", (), {"cluster_id": "hdbscan-1", "final_name": "AI & Robotics"})(),
                type("CM", (), {"cluster_id": "repechage-0", "final_name": "AI & Robotics"})(),
            ]
            return out
        if name == "reassign_to_clusters":
            ids = [h["id"] for h in inputs["headlines"]]
            out.assignments = [
                type("HA", (), {"id": i, "assignment": "AI & Robotics"})() for i in ids
            ]
            return out
        raise AssertionError(f"unexpected prompt {name!r}")

    monkeypatch.setattr(
        "lib.steps.select.embed_texts",
        lambda texts: [[0.0] * 8 for _ in texts],
    )

    from lib.steps.select import cli as select_cli
    runner = CliRunner()
    with patch("lib.steps.select.call_prompt", side_effect=fake_call_prompt):
        result = runner.invoke(select_cli, [
            "--db", tmp_db, "--session", "s_classic",
            "--engine", "subagent",
            "--k", "4",
            "--repechage-batch-size", "10",
            "--reassign-batch-size", "5",
        ])
    assert result.exit_code == 0, result.output

    state = NewsletterAgentState(session_id="s_classic", db_path=tmp_db).load_latest_from_db()
    assert len(state.newsletter_section_data) == 4
    assert {s["cat"] for s in state.newsletter_section_data} == {"AI & Robotics"}


# ---------------------------------------------------------------------------
# CLI guardrails
# ---------------------------------------------------------------------------

def test_mutually_exclusive_prepare_flags(tmp_db, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    _seed_state(tmp_db, session_id="s_excl")
    from lib.steps.select import cli as select_cli
    runner = CliRunner()
    result = runner.invoke(select_cli, [
        "--db", tmp_db, "--session", "s_excl",
        "--prepare-repechage", "--prepare-consolidate",
    ])
    assert result.exit_code != 0
    assert "mutually exclusive" in result.output


def test_prepare_consolidate_errors_without_repechage_results(tmp_db, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    _seed_state(tmp_db, session_id="s_missing")
    from lib.steps.select import cli as select_cli
    runner = CliRunner()
    result = runner.invoke(select_cli, [
        "--db", tmp_db, "--session", "s_missing", "--prepare-consolidate",
    ])
    assert result.exit_code != 0
