"""Tests for lib/steps/cluster.py — UMAP+HDBSCAN+NAME_TOPIC."""
import json
from pathlib import Path
from unittest.mock import patch, MagicMock
from click.testing import CliRunner
import numpy as np
import lib.prompts  # noqa: F401 — register prompts
from lib.db import init_db
from lib.state import NewsletterAgentState
from lib.steps.cluster import cli as cluster_cli
from lib.prompts.name_topic_batch import NameTopicBatchOutput


def _seed(tmp_db, session_id="c1", n=6, with_embeddings=True, with_ratings=True):
    """Seed a DB with headlines that have summaries (and optionally embeddings/ratings)."""
    init_db(tmp_db)
    state = NewsletterAgentState(session_id=session_id, db_path=tmp_db)
    for s in ("init", "gather", "filter", "download", "summarize", "rate"):
        state.complete_step(s)
    headlines = []
    for i in range(n):
        h = {
            "id": i,
            "title": f"Headline {i}",
            "url": f"https://e.com/{i}",
            "summary": f"Summary {i}",
        }
        if with_embeddings:
            # Give each headline a 4-dim embedding
            h["embedding"] = [float(i % 2), float(i // 2), 0.0, 0.0]
        if with_ratings:
            h["rating"] = float(n - i)  # h0 highest rating
        headlines.append(h)
    state.headline_data = headlines
    state.save_checkpoint("rate")


def _fake_reducer():
    """Return a mock reducer whose transform returns the input unchanged."""
    reducer = MagicMock()
    reducer.transform.side_effect = lambda M: M  # pass-through
    return reducer


def test_cluster_assigns_cluster_id_and_name(tmp_db, monkeypatch, tmp_path):
    """Headlines get cluster_id and cluster_name; state.clusters is populated."""
    monkeypatch.chdir(tmp_path)
    _seed(tmp_db, n=6)

    # 3 blobs of 2 each
    fake_labels = np.array([0, 0, 1, 1, 2, 2])
    fake_metrics = {"n_clusters": 3, "noise_ratio": 0.0, "silhouette": 0.9, "best_params": {}}

    call_count = {"n": 0}

    def fake_call_prompt(name, inputs, *, engine=None):
        out = MagicMock()
        call_count["n"] += 1
        out.title = f"Cluster {call_count['n']}"  # unique name per call
        return out

    with patch("lib.steps.cluster.load_umap_reducer", return_value=_fake_reducer()), \
         patch("lib.steps.cluster.optimize_hdbscan", return_value=(fake_labels, fake_metrics)), \
         patch("lib.steps.cluster.call_prompt", side_effect=fake_call_prompt):
        runner = CliRunner()
        result = runner.invoke(cluster_cli, ["--db", tmp_db, "--session", "c1"])

    assert result.exit_code == 0, result.output

    state = NewsletterAgentState(session_id="c1", db_path=tmp_db).load_latest_from_db()
    # All headlines should have cluster_id
    for h in state.headline_data:
        assert "cluster_id" in h, f"Missing cluster_id on {h}"
    # state.clusters should have 3 entries (one per unique cluster name)
    assert len(state.clusters) == 3


def test_cluster_skips_when_no_summaries(tmp_db, monkeypatch, tmp_path):
    """If no headlines have summaries, step completes without calling UMAP or LLM."""
    monkeypatch.chdir(tmp_path)
    init_db(tmp_db)
    state = NewsletterAgentState(session_id="empty", db_path=tmp_db)
    for s in ("init", "gather", "filter", "download", "summarize", "rate"):
        state.complete_step(s)
    state.headline_data = [{"id": 0, "title": "T", "url": "https://e.com/0"}]  # no summary
    state.save_checkpoint("rate")

    with patch("lib.steps.cluster.load_umap_reducer") as mock_load, \
         patch("lib.steps.cluster.optimize_hdbscan") as mock_opt:
        runner = CliRunner()
        result = runner.invoke(cluster_cli, ["--db", tmp_db, "--session", "empty"])

    assert result.exit_code == 0, result.output
    mock_load.assert_not_called()
    mock_opt.assert_not_called()
    assert "nothing" in result.output.lower() or "no headlines" in result.output.lower()


def test_cluster_writes_cluster_json(tmp_db, monkeypatch, tmp_path):
    """cluster.json artifact is written to runs/<SID>/cluster.json."""
    monkeypatch.chdir(tmp_path)
    _seed(tmp_db, n=4)

    fake_labels = np.array([0, 0, 1, 1])
    fake_metrics = {"n_clusters": 2, "noise_ratio": 0.0, "silhouette": 0.8, "best_params": {}}

    def fake_call_prompt(name, inputs, *, engine=None):
        out = MagicMock()
        out.title = "Test Cluster Name"
        return out

    with patch("lib.steps.cluster.load_umap_reducer", return_value=_fake_reducer()), \
         patch("lib.steps.cluster.optimize_hdbscan", return_value=(fake_labels, fake_metrics)), \
         patch("lib.steps.cluster.call_prompt", side_effect=fake_call_prompt):
        runner = CliRunner()
        result = runner.invoke(cluster_cli, ["--db", tmp_db, "--session", "c1"])

    assert result.exit_code == 0, result.output
    import json
    cluster_json = tmp_path / "runs" / "c1" / "cluster.json"
    assert cluster_json.exists(), "cluster.json not written"
    data = json.loads(cluster_json.read_text())
    assert "clusters" in data
    assert data["n_clusters"] == 2


def test_cluster_handles_all_noise(tmp_db, monkeypatch, tmp_path):
    """When all points are noise (cluster_id=-1), step completes and clusters is empty."""
    monkeypatch.chdir(tmp_path)
    _seed(tmp_db, n=4)

    fake_labels = np.array([-1, -1, -1, -1])
    fake_metrics = {"n_clusters": 0, "noise_ratio": 1.0, "silhouette": float("nan"), "best_params": {}}

    with patch("lib.steps.cluster.load_umap_reducer", return_value=_fake_reducer()), \
         patch("lib.steps.cluster.optimize_hdbscan", return_value=(fake_labels, fake_metrics)), \
         patch("lib.steps.cluster.call_prompt") as mock_cp:
        runner = CliRunner()
        result = runner.invoke(cluster_cli, ["--db", tmp_db, "--session", "c1"])

    assert result.exit_code == 0, result.output
    # NAME_TOPIC should not have been called (no non-noise clusters)
    mock_cp.assert_not_called()

    state = NewsletterAgentState(session_id="c1", db_path=tmp_db).load_latest_from_db()
    assert len(state.clusters) == 0
    # All headlines should have cluster_id = -1
    for h in state.headline_data:
        assert h.get("cluster_id") == -1


def test_cluster_prepare_writes_single_batch(tmp_db, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    _seed(tmp_db, session_id="c2", n=6)

    # Stub UMAP reducer + HDBSCAN to force 2 clusters
    monkeypatch.setattr("lib.steps.cluster.load_umap_reducer", lambda _: _fake_reducer())
    monkeypatch.setattr(
        "lib.steps.cluster.apply_umap",
        lambda embeddings, reducer: [[float(i % 2), 0.0] for i in range(len(embeddings))],
    )
    monkeypatch.setattr(
        "lib.steps.cluster.optimize_hdbscan",
        lambda reduced, n_trials: ([0, 1, 0, 1, 0, 1], {"noise_ratio": 0.0, "best_params": {}}),
    )

    runner = CliRunner()
    result = runner.invoke(cluster_cli, [
        "--db", tmp_db, "--session", "c2", "--prepare-batches",
    ])
    assert result.exit_code == 0, result.output

    batches_dir = Path("runs/c2/cluster-batches")
    files = sorted(batches_dir.glob("batch-*.json"))
    assert len(files) == 1  # single batch for all clusters

    payload = json.loads(files[0].read_text())
    assert payload["batch_id"] == 0
    cluster_ids = sorted(c["cluster_id"] for c in payload["clusters"])
    assert cluster_ids == ["0", "1"]
    assert "system_prompt" in payload and payload["system_prompt"]
    assert "user_prompt" in payload and payload["user_prompt"]
    # Schema embedded
    assert payload["output_schema"]["properties"].get("names") is not None
