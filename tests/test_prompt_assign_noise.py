import importlib
import pytest
import lib.prompts  # triggers registration of all prompts
from lib.llm import get_prompt, _registry
from lib.prompts.assign_noise import AssignNoiseInput, ClusterDescriptor


@pytest.fixture(autouse=True)
def _ensure_registered():
    if "assign_noise" not in _registry:
        mod = importlib.import_module("lib.prompts.assign_noise")
        importlib.reload(mod)
    yield


def test_assign_noise_registered():
    cfg = get_prompt("assign_noise")
    assert cfg.default_engine == "subagent"
    assert cfg.reasoning_effort == 3


def test_assign_noise_input_accepts_valid_data():
    clusters = [
        ClusterDescriptor(id="c1", name="AI Policy", sample_headlines=["AI bill passes", "Senate debates AI"]),
        ClusterDescriptor(id="c2", name="Climate Change", sample_headlines=["Temperatures rise", "Arctic ice melts"]),
    ]
    inp = AssignNoiseInput(
        headline_title="New AI regulation proposed",
        headline_summary="A bill to regulate artificial intelligence was introduced.",
        clusters=clusters,
        allow_new=True,
    )
    assert inp.headline_title == "New AI regulation proposed"
    assert len(inp.clusters) == 2
    # clusters_json should be valid JSON with both clusters
    import json
    parsed = json.loads(inp.clusters_json)
    assert len(parsed) == 2
    assert parsed[0]["id"] == "c1"


def test_assign_noise_input_clusters_json_computed_field():
    import json
    clusters = [
        ClusterDescriptor(id="x1", name="Tech", sample_headlines=["headline 1"]),
    ]
    inp = AssignNoiseInput(
        headline_title="Test headline",
        headline_summary="A test summary",
        clusters=clusters,
        allow_new=False,
    )
    data = json.loads(inp.clusters_json)
    assert data[0]["name"] == "Tech"
    assert "sample_headlines" in data[0]


def test_assign_noise_system_prompt_has_key_phrases():
    cfg = get_prompt("assign_noise")
    assert "cluster" in cfg.system_prompt.lower()
    assert "assign" in cfg.system_prompt.lower() or "assigning" in cfg.system_prompt.lower()
