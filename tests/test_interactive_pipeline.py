"""End-to-end test of the interactive Agent-dispatch pipeline.

Drives cluster → select → draft → rewrite using --prepare-batches and
--apply-results with hand-written Agent result JSONs (no real LLM calls).
Asserts state shape after each step.
"""
import json
from collections import defaultdict
from pathlib import Path

import pytest
from click.testing import CliRunner

import lib.prompts  # register all prompts
from lib.db import init_db
from lib.state import NewsletterAgentState
from lib.steps.cluster import cli as cluster_cli
from lib.steps.select import cli as select_cli
from lib.steps.draft import cli as draft_cli
from lib.steps.rewrite import cli as rewrite_cli


@pytest.fixture
def tmp_db(tmp_path):
    db = str(tmp_path / "test.db")
    return db


def _seed_for_cluster(tmp_db, session_id):
    init_db(tmp_db)
    state = NewsletterAgentState(session_id=session_id, db_path=tmp_db)
    for step in ("start", "gather", "filter", "download", "dedupe", "summarize", "rate"):
        state.complete_step(step)
    headlines = []
    for i in range(6):
        headlines.append({
            "title": f"Headline {i}",
            "summary": f"Summary of headline {i}",
            "url": f"https://x.com/{i}",
            "rating": 0.5 + 0.1 * (i % 3),
            "embedding": [float(i % 2), float(i % 3), 0.0],
        })
    state.headline_data = headlines
    state.save_checkpoint("rate")
    return state


def test_interactive_pipeline_chains(tmp_db, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    _seed_for_cluster(tmp_db, session_id="e2e")

    # Stub cluster expensive paths
    monkeypatch.setattr("lib.steps.cluster.load_umap_reducer", lambda _: object())
    monkeypatch.setattr(
        "lib.steps.cluster.apply_umap",
        lambda embeddings, reducer: [[float(i % 2), 0.0] for i in range(len(embeddings))],
    )
    monkeypatch.setattr(
        "lib.steps.cluster.optimize_hdbscan",
        lambda reduced, n_trials: ([0, 1, 0, 1, -1, -1],
                                   {"noise_ratio": 0.33, "best_params": {}}),
    )

    # Stub select's embed_texts (used by _find_merge_candidates)
    monkeypatch.setattr(
        "lib.steps.select.embed_texts",
        lambda texts: [[0.0] * 8 for _ in texts],
    )

    runner = CliRunner()

    # --- cluster: prepare + apply ---
    r = runner.invoke(cluster_cli, ["--db", tmp_db, "--session", "e2e", "--prepare-batches"])
    assert r.exit_code == 0, r.output
    Path("runs/e2e/cluster-results").mkdir(parents=True)
    Path("runs/e2e/cluster-results/batch-000.json").write_text(json.dumps({
        "names": [
            {"cluster_id": "0", "name": "Cluster Zero"},
            {"cluster_id": "1", "name": "Cluster One"},
        ]
    }))
    r = runner.invoke(cluster_cli, [
        "--db", tmp_db, "--session", "e2e",
        "--apply-results", "runs/e2e/cluster-results",
    ])
    assert r.exit_code == 0, r.output

    state = NewsletterAgentState(session_id="e2e", db_path=tmp_db).load_latest_from_db()
    assert set(state.clusters.keys()) == {"Cluster Zero", "Cluster One"}

    # --- select: three-round prepare + apply ---
    # Round 1: repechage (mines the 2 noise headlines for new themes).
    r = runner.invoke(select_cli, [
        "--db", tmp_db, "--session", "e2e", "--prepare-repechage",
    ])
    assert r.exit_code == 0, r.output

    rep_batch = json.loads(Path("runs/e2e/select-repechage-batches/batch-000.json").read_text())
    noise_ids = rep_batch["ids"]
    assert len(noise_ids) == 2
    # Pretend repechage proposes no new clusters (both noise IDs left unclustered).
    Path("runs/e2e/select-repechage-results").mkdir(parents=True)
    Path("runs/e2e/select-repechage-results/batch-000.json").write_text(json.dumps({
        "proposed_clusters": [],
        "unclustered_ids": list(noise_ids),
    }))

    # Round 2: consolidate (just two HDBSCAN clusters → keep both).
    r = runner.invoke(select_cli, [
        "--db", tmp_db, "--session", "e2e", "--prepare-consolidate",
    ])
    assert r.exit_code == 0, r.output

    consolidate_batch = json.loads(
        Path("runs/e2e/select-consolidate-batches/batch-000.json").read_text()
    )
    input_cids = [c["cluster_id"] for c in consolidate_batch["clusters"]]
    Path("runs/e2e/select-consolidate-results").mkdir(parents=True)
    Path("runs/e2e/select-consolidate-results/batch-000.json").write_text(json.dumps({
        "final_names": ["Cluster Zero", "Cluster One"],
        "mapping": [
            {"cluster_id": cid,
             "final_name": "Cluster Zero" if cid.endswith("-0") else "Cluster One"}
            for cid in input_cids
        ],
    }))

    # Round 3: reassign — every headline gets a section.
    r = runner.invoke(select_cli, [
        "--db", tmp_db, "--session", "e2e", "--prepare-reassign",
    ])
    assert r.exit_code == 0, r.output

    reassign_batches = sorted(Path("runs/e2e/select-reassign-batches").glob("batch-*.json"))
    Path("runs/e2e/select-reassign-results").mkdir(parents=True)
    for i, bf in enumerate(reassign_batches):
        batch = json.loads(bf.read_text())
        assignments = []
        for hl_id in batch["ids"]:
            # Even ids → Cluster Zero, odd → Cluster One
            label = "Cluster Zero" if int(hl_id) % 2 == 0 else "Cluster One"
            assignments.append({"id": hl_id, "assignment": label})
        Path(f"runs/e2e/select-reassign-results/batch-{i:03d}.json").write_text(
            json.dumps({"assignments": assignments})
        )

    r = runner.invoke(select_cli, [
        "--db", tmp_db, "--session", "e2e",
        "--apply-results", "runs/e2e",
        "--k", "6",
    ])
    assert r.exit_code == 0, r.output

    state = NewsletterAgentState(session_id="e2e", db_path=tmp_db).load_latest_from_db()
    assert len(state.newsletter_section_data) > 0
    cats = {s["cat"] for s in state.newsletter_section_data}
    assert cats.issubset({"Cluster Zero", "Cluster One"})

    # --- draft: prepare + apply ---
    r = runner.invoke(draft_cli, ["--db", tmp_db, "--session", "e2e", "--prepare-batches"])
    assert r.exit_code == 0, r.output

    batch_files = sorted(Path("runs/e2e/draft-batches").glob("batch-*.json"))
    Path("runs/e2e/draft-results").mkdir(parents=True)
    for i, bf in enumerate(batch_files):
        cat = json.loads(bf.read_text())["cat"]
        Path(f"runs/e2e/draft-results/batch-{i:03d}.json").write_text(json.dumps({
            "cat": cat,
            "final_section_markdown": f"## {cat}\n- fake headline",
            "iterations": 1,
            "scores": [8.5],
            "feedbacks": ["good"],
            "accepted": True,
        }))

    r = runner.invoke(draft_cli, [
        "--db", tmp_db, "--session", "e2e",
        "--apply-results", "runs/e2e/draft-results",
    ])
    assert r.exit_code == 0, r.output

    state = NewsletterAgentState(session_id="e2e", db_path=tmp_db).load_latest_from_db()
    assert all("section_markdown" in s for s in state.newsletter_section_data)

    # --- rewrite: prepare + apply ---
    r = runner.invoke(rewrite_cli, ["--db", tmp_db, "--session", "e2e", "--prepare-batches"])
    assert r.exit_code == 0, r.output
    Path("runs/e2e/rewrite-results").mkdir(parents=True)
    Path("runs/e2e/rewrite-results/batch-000.json").write_text(json.dumps({
        "final_newsletter_markdown": "## Cluster Zero\n- fake\n\n## Cluster One\n- fake",
        "title": "End-to-end test newsletter",
        "iterations": 1,
        "scores": [9.0],
        "feedbacks": ["great"],
        "accepted": True,
    }))
    r = runner.invoke(rewrite_cli, [
        "--db", tmp_db, "--session", "e2e",
        "--apply-results", "runs/e2e/rewrite-results",
    ])
    assert r.exit_code == 0, r.output

    state = NewsletterAgentState(session_id="e2e", db_path=tmp_db).load_latest_from_db()
    assert state.newsletter_title == "End-to-end test newsletter"
    assert state.final_newsletter.startswith("# End-to-end test newsletter")
