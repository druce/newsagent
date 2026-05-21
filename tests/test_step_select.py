"""Tests for lib/steps/select.py — MMR + LLM noise-assign + cluster-merge."""
import json
from collections import defaultdict
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
from click.testing import CliRunner

import lib.prompts  # noqa: F401 — register prompts
from lib.db import init_db
from lib.state import NewsletterAgentState


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_embedding(x: float, y: float) -> list:
    """Return a simple 2-D embedding as a list."""
    return [x, y]


def _seed_state(
    tmp_db: str,
    session_id: str = "s1",
    prior_steps: list | None = None,
    headlines: list | None = None,
) -> NewsletterAgentState:
    """Return a state with two clusters + two noise headlines, saved to DB.

    If `headlines` is provided, use it verbatim as state.headline_data and
    auto-derive state.clusters from it (grouping by cluster_name for non-noise).
    If `headlines` is None, use the default 10-headline seed (4+4 clustered + 2 noise).
    """
    init_db(tmp_db)
    if prior_steps is None:
        prior_steps = ["init", "gather", "filter", "download", "dedupe", "summarize", "rate", "cluster"]

    state = NewsletterAgentState(session_id=session_id, db_path=tmp_db)
    for s in prior_steps:
        state.complete_step(s)

    if headlines is None:
        headlines = []
        # Cluster 0 — 4 headlines (embedding: near [1,0])
        for i in range(4):
            headlines.append({
                "id": i,
                "title": f"AI headline {i}",
                "url": f"https://ai.com/{i}",
                "summary": f"AI summary {i}",
                "cluster_id": 0,
                "cluster_name": "Artificial Intelligence",
                "embedding": _make_embedding(1.0 + i * 0.01, 0.0),
                "rating": float(4 - i),  # 4,3,2,1
            })
        # Cluster 1 — 4 headlines (embedding: near [0,1])
        for i in range(4):
            headlines.append({
                "id": 4 + i,
                "title": f"Robotics headline {i}",
                "url": f"https://robotics.com/{i}",
                "summary": f"Robotics summary {i}",
                "cluster_id": 1,
                "cluster_name": "Robotics",
                "embedding": _make_embedding(0.0, 1.0 + i * 0.01),
                "rating": float(4 - i),
            })
        # Two noise headlines (cluster_id=-1)
        headlines.append({
            "id": 8,
            "title": "Noise headline about AI chips",
            "url": "https://noise.com/0",
            "summary": "About AI chip developments",
            "cluster_id": -1,
            "cluster_name": "",
            "embedding": _make_embedding(1.0, 0.01),
            "rating": 3.0,
        })
        headlines.append({
            "id": 9,
            "title": "Completely unrelated noise",
            "url": "https://noise.com/1",
            "summary": "Off-topic content",
            "cluster_id": -1,
            "cluster_name": "",
            "embedding": _make_embedding(0.5, 0.5),
            "rating": 1.0,
        })
        state.headline_data = headlines
        state.clusters = {
            "Artificial Intelligence": [str(i) for i in range(4)],
            "Robotics": [str(4 + i) for i in range(4)],
        }
    else:
        state.headline_data = headlines
        # Auto-derive clusters from the provided headlines
        by_name: dict = defaultdict(list)
        for i, h in enumerate(headlines):
            cid = h.get("cluster_id", -1)
            if cid >= 0:
                cname = h.get("cluster_name", f"Cluster {cid}")
                by_name[cname].append(h.get("url", str(i)))
        state.clusters = {name: urls for name, urls in by_name.items()}

    state.save_checkpoint("cluster")
    return state


# ---------------------------------------------------------------------------
# Test 1: noise points are assigned to existing clusters
# ---------------------------------------------------------------------------

def test_noise_assigned_to_existing_cluster(tmp_db, monkeypatch, tmp_path):
    """When assign_noise returns a valid cluster id, the noise headline is
    reassigned to that cluster and appears in newsletter_section_data."""
    monkeypatch.chdir(tmp_path)
    _seed_state(tmp_db, session_id="s1")

    from lib.steps.select import cli as select_cli

    def fake_call_prompt(name, inputs, *, engine=None):
        out = MagicMock()
        if name == "assign_noise":
            # Always assign noise to cluster 0
            out.assignment = "0"
        elif name == "merge_clusters":
            out.merge = False
            out.merged_name = None
        return out

    with patch("lib.steps.select.call_prompt", side_effect=fake_call_prompt), \
         patch("lib.steps.select.embed_texts", return_value=[
             [1.0, 0.0], [0.0, 1.0]  # two cluster-name embeddings (AI + Robotics)
         ]):
        runner = CliRunner()
        result = runner.invoke(select_cli, ["--db", tmp_db, "--session", "s1"])

    assert result.exit_code == 0, result.output

    state = NewsletterAgentState(session_id="s1", db_path=tmp_db).load_latest_from_db()
    # The formerly-noise headline (id=8 assigned to cluster 0) should appear in sections
    section_headlines = [s["headline"] for s in state.newsletter_section_data]
    # "Noise headline about AI chips" was assigned to cluster 0; with k=5 and only
    # 4 original + 1 assigned = 5, it should be picked by MMR
    ai_section_urls = [s["link"] for s in state.newsletter_section_data
                       if s["cat"] == "Artificial Intelligence"]
    assert "https://noise.com/0" in ai_section_urls, (
        f"Assigned noise URL not found in AI section. All section links: "
        f"{[s['link'] for s in state.newsletter_section_data]}"
    )


# ---------------------------------------------------------------------------
# Test 2: "none" assignment drops the noise headline
# ---------------------------------------------------------------------------

def test_none_assignment_drops_headline(tmp_db, monkeypatch, tmp_path):
    """When assign_noise returns 'none', the headline is removed from headline_data."""
    monkeypatch.chdir(tmp_path)
    _seed_state(tmp_db, session_id="s2")

    from lib.steps.select import cli as select_cli

    def fake_call_prompt(name, inputs, *, engine=None):
        out = MagicMock()
        if name == "assign_noise":
            out.assignment = "none"
        elif name == "merge_clusters":
            out.merge = False
            out.merged_name = None
        return out

    with patch("lib.steps.select.call_prompt", side_effect=fake_call_prompt), \
         patch("lib.steps.select.embed_texts", return_value=[
             [1.0, 0.0], [0.0, 1.0]
         ]):
        runner = CliRunner()
        result = runner.invoke(select_cli, ["--db", tmp_db, "--session", "s2"])

    assert result.exit_code == 0, result.output

    state = NewsletterAgentState(session_id="s2", db_path=tmp_db).load_latest_from_db()
    # Noise headline URLs must NOT appear anywhere
    all_links = [s["link"] for s in state.newsletter_section_data]
    assert "https://noise.com/0" not in all_links, "Dropped noise URL found in sections"
    assert "https://noise.com/1" not in all_links, "Dropped noise URL found in sections"
    # All remaining headlines should have non-negative cluster_ids
    for h in state.headline_data:
        assert h.get("cluster_id", -2) >= 0, f"Headline with negative cluster_id survived: {h}"


# ---------------------------------------------------------------------------
# Test 3: MMR selects top-K per cluster (with --no-noise-assign)
# ---------------------------------------------------------------------------

def test_mmr_selects_top_k_per_cluster(tmp_db, monkeypatch, tmp_path):
    """With k=3 and each cluster having 4 headlines, MMR picks exactly 3 per cluster."""
    monkeypatch.chdir(tmp_path)

    # Seed with 6 headlines per cluster (so there's something to trim)
    init_db(tmp_db)
    state = NewsletterAgentState(session_id="s3", db_path=tmp_db)
    for s in ["init", "gather", "filter", "download", "summarize", "rate", "cluster"]:
        state.complete_step(s)

    headlines = []
    for i in range(6):
        headlines.append({
            "id": i,
            "title": f"AI h{i}",
            "url": f"https://ai.com/{i}",
            "summary": f"AI s{i}",
            "cluster_id": 0,
            "cluster_name": "Artificial Intelligence",
            "embedding": _make_embedding(1.0 + i * 0.05, float(i) * 0.01),
            "rating": float(6 - i),
        })
    for i in range(6):
        headlines.append({
            "id": 6 + i,
            "title": f"Robotics h{i}",
            "url": f"https://robotics.com/{i}",
            "summary": f"Robotics s{i}",
            "cluster_id": 1,
            "cluster_name": "Robotics",
            "embedding": _make_embedding(float(i) * 0.01, 1.0 + i * 0.05),
            "rating": float(6 - i),
        })
    state.headline_data = headlines
    state.clusters = {
        "Artificial Intelligence": [str(i) for i in range(6)],
        "Robotics": [str(6 + i) for i in range(6)],
    }
    state.save_checkpoint("cluster")

    from lib.steps.select import cli as select_cli

    with patch("lib.steps.select.embed_texts", return_value=[
        [1.0, 0.0], [0.0, 1.0]
    ]):
        runner = CliRunner()
        result = runner.invoke(
            select_cli,
            ["--db", tmp_db, "--session", "s3",
             "--k", "3",
             "--no-noise-assign",
             "--no-merge"],
        )

    assert result.exit_code == 0, result.output

    state = NewsletterAgentState(session_id="s3", db_path=tmp_db).load_latest_from_db()
    # Count per cluster
    ai_count = sum(1 for s in state.newsletter_section_data if s["cat"] == "Artificial Intelligence")
    robotics_count = sum(1 for s in state.newsletter_section_data if s["cat"] == "Robotics")
    assert ai_count == 3, f"Expected 3 AI headlines, got {ai_count}"
    assert robotics_count == 3, f"Expected 3 Robotics headlines, got {robotics_count}"
    assert len(state.newsletter_section_data) == 6


# ---------------------------------------------------------------------------
# Test 4: cluster merge combines two clusters (with --no-noise-assign)
# ---------------------------------------------------------------------------

def test_cluster_merge_combines_clusters(tmp_db, monkeypatch, tmp_path):
    """When merge_clusters returns merge=True, both clusters become one section."""
    monkeypatch.chdir(tmp_path)

    init_db(tmp_db)
    state = NewsletterAgentState(session_id="s4", db_path=tmp_db)
    for s in ["init", "gather", "filter", "download", "summarize", "rate", "cluster"]:
        state.complete_step(s)

    # Two clusters with very similar names (so embedding sim >= threshold)
    headlines = []
    for i in range(3):
        headlines.append({
            "id": i,
            "title": f"AI chips headline {i}",
            "url": f"https://chips.com/{i}",
            "summary": f"Chips s{i}",
            "cluster_id": 0,
            "cluster_name": "AI Chips",
            "embedding": _make_embedding(1.0, float(i) * 0.01),
            "rating": float(3 - i),
        })
    for i in range(3):
        headlines.append({
            "id": 3 + i,
            "title": f"Artificial intelligence chips {i}",
            "url": f"https://aichips.com/{i}",
            "summary": f"AI chips s{i}",
            "cluster_id": 1,
            "cluster_name": "Artificial Intelligence Chips",
            "embedding": _make_embedding(1.0, 0.1 + float(i) * 0.01),
            "rating": float(3 - i),
        })
    state.headline_data = headlines
    state.clusters = {
        "AI Chips": ["0", "1", "2"],
        "Artificial Intelligence Chips": ["3", "4", "5"],
    }
    state.save_checkpoint("cluster")

    from lib.steps.select import cli as select_cli

    def fake_call_prompt(name, inputs, *, engine=None):
        out = MagicMock()
        if name == "merge_clusters":
            out.merge = True
            out.merged_name = "AI Chips"
        return out

    # Return embeddings that are very similar (cosine sim >= 0.85) for both cluster names
    # Two nearly-identical unit vectors -> sim ~1.0
    similar_embs = [[1.0, 0.01], [1.0, 0.02]]

    with patch("lib.steps.select.call_prompt", side_effect=fake_call_prompt), \
         patch("lib.steps.select.embed_texts", return_value=similar_embs):
        runner = CliRunner()
        result = runner.invoke(
            select_cli,
            ["--db", tmp_db, "--session", "s4",
             "--no-noise-assign",
             "--k", "10"],  # k large enough to get all
        )

    assert result.exit_code == 0, result.output

    state = NewsletterAgentState(session_id="s4", db_path=tmp_db).load_latest_from_db()

    # After merge, there should be only one section name
    section_names = {s["cat"] for s in state.newsletter_section_data}
    assert len(section_names) == 1, (
        f"Expected 1 merged section, got {len(section_names)}: {section_names}"
    )
    assert "AI Chips" in section_names

    # All 6 headlines should appear (k=10, only 6 available)
    assert len(state.newsletter_section_data) == 6

    # state.clusters should reflect the merge
    assert len(state.clusters) == 1


# ---------------------------------------------------------------------------
# Test 5: --prepare-batches writes both assign and merge subdirs
# ---------------------------------------------------------------------------

def test_select_prepare_writes_both_subdirs(tmp_db, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    # Seed with: 2 named clusters + 3 noise headlines
    _seed_state(
        tmp_db,
        session_id="s_prep",
        headlines=[
            {"title": "GPT-6 ships", "summary": "OpenAI", "cluster_id": 0,
             "cluster_name": "OpenAI", "url": "https://x.com/1", "rating": 0.9},
            {"title": "AI Act passes", "summary": "EU", "cluster_id": 1,
             "cluster_name": "AI Act", "url": "https://x.com/2", "rating": 0.8},
            {"title": "Lone story 1", "summary": "X", "cluster_id": -1,
             "url": "https://x.com/3", "rating": 0.5},
            {"title": "Lone story 2", "summary": "Y", "cluster_id": -1,
             "url": "https://x.com/4", "rating": 0.4},
            {"title": "Lone story 3", "summary": "Z", "cluster_id": -1,
             "url": "https://x.com/5", "rating": 0.3},
        ],
    )
    monkeypatch.setattr(
        "lib.steps.select.embed_texts",
        lambda texts: [[0.0] * 8 for _ in texts],
    )

    from lib.steps.select import cli as select_cli

    runner = CliRunner()
    result = runner.invoke(select_cli, [
        "--db", tmp_db, "--session", "s_prep", "--prepare-batches",
    ])
    assert result.exit_code == 0, result.output

    # Noise-assign subdir: one batch with all 3 noise headlines (batch-size default 25)
    assign_dir = Path("runs/s_prep/select-assign-batches")
    assign_files = sorted(assign_dir.glob("batch-*.json"))
    assert len(assign_files) == 1
    assign_payload = json.loads(assign_files[0].read_text())
    assert len(assign_payload["headlines"]) == 3
    assert assign_payload["clusters"][0]["name"] in ("OpenAI", "AI Act")

    # Merge-pairs subdir: written only if cosine cluster-name similarities yield candidates;
    # for this seed the two cluster names differ, so 0 pairs → no merge batch file written.
    merge_dir = Path("runs/s_prep/select-merge-batches")
    if merge_dir.exists():
        # If anything was written it must be a single batch
        merge_files = sorted(merge_dir.glob("batch-*.json"))
        assert len(merge_files) <= 1
