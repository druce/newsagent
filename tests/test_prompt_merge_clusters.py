import importlib
import pytest
import lib.prompts  # triggers registration of all prompts
from lib.llm import get_prompt, _registry
from lib.prompts.merge_clusters import ClusterPair, MergeClustersInput


@pytest.fixture(autouse=True)
def _ensure_registered():
    if "merge_clusters" not in _registry:
        mod = importlib.import_module("lib.prompts.merge_clusters")
        importlib.reload(mod)
    yield


def test_merge_clusters_registered():
    cfg = get_prompt("merge_clusters")
    assert cfg.default_engine == "subagent"
    assert cfg.reasoning_effort == 4


def test_merge_clusters_input_accepts_two_cluster_pairs():
    a = ClusterPair(id="c1", name="AI Regulation", top_headlines=["AI bill passes", "Senate AI vote"])
    b = ClusterPair(id="c2", name="AI Policy", top_headlines=["House debates AI rules", "AI governance proposed"])
    inp = MergeClustersInput(a=a, b=b)
    assert inp.a.id == "c1"
    assert inp.b.name == "AI Policy"


def test_merge_clusters_pair_json_computed_field():
    import json
    a = ClusterPair(id="c1", name="Climate", top_headlines=["Arctic melts"])
    b = ClusterPair(id="c2", name="Global Warming", top_headlines=["Temps rise", "CO2 levels high"])
    inp = MergeClustersInput(a=a, b=b)
    data = json.loads(inp.pair_json)
    assert "a" in data and "b" in data
    assert data["a"]["id"] == "c1"
    assert data["b"]["name"] == "Global Warming"


def test_merge_clusters_system_prompt_has_key_phrases():
    cfg = get_prompt("merge_clusters")
    assert "merge" in cfg.system_prompt.lower()
    assert "cluster" in cfg.system_prompt.lower()
