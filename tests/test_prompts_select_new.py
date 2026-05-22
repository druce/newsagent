"""Schema/registration tests for the new select-phase prompts."""
import lib.prompts  # noqa: F401  — register
from lib.llm import get_prompt
from lib.prompts.consolidate_cluster_names import (
    ClusterMapping,
    ClusterToConsolidate,
    ConsolidateClusterNamesInput,
    ConsolidateClusterNamesOutput,
)
from lib.prompts.extract_noise_clusters import (
    ExtractNoiseClustersInput,
    ExtractNoiseClustersOutput,
    NoiseHeadlineToCluster,
    ProposedCluster,
)


# ── extract_noise_clusters ─────────────────────────────────────────────

def test_extract_noise_clusters_registered():
    cfg = get_prompt("extract_noise_clusters")
    assert cfg.input_schema is ExtractNoiseClustersInput
    assert cfg.output_schema is ExtractNoiseClustersOutput
    assert cfg.default_engine == "subagent"


def test_extract_noise_clusters_input_renders():
    inp = ExtractNoiseClustersInput(headlines=[
        NoiseHeadlineToCluster(id="42", title="Anthropic raises $5B", short_summary="big round"),
        NoiseHeadlineToCluster(id="43", title="OpenAI's new datacenter", short_summary="Texas"),
    ])
    cfg = get_prompt("extract_noise_clusters")
    rendered = cfg.user_prompt.format(**inp.model_dump())
    assert "Anthropic raises" in rendered
    assert "OpenAI's new datacenter" in rendered


def test_extract_noise_clusters_output_shape():
    out = ExtractNoiseClustersOutput(
        proposed_clusters=[
            ProposedCluster(name="AI Capex Race", member_ids=["42", "43"]),
        ],
        unclustered_ids=["99"],
    )
    assert out.proposed_clusters[0].member_ids == ["42", "43"]
    assert out.unclustered_ids == ["99"]


# ── consolidate_cluster_names ──────────────────────────────────────────

def test_consolidate_cluster_names_registered():
    cfg = get_prompt("consolidate_cluster_names")
    assert cfg.input_schema is ConsolidateClusterNamesInput
    assert cfg.output_schema is ConsolidateClusterNamesOutput
    assert cfg.default_engine == "subagent"


def test_consolidate_cluster_names_input_renders():
    inp = ConsolidateClusterNamesInput(clusters=[
        ClusterToConsolidate(
            cluster_id="hdbscan-0", name="AI Chips Race",
            sample_headlines=["Nvidia tops", "AMD chases"],
        ),
        ClusterToConsolidate(
            cluster_id="repechage-0", name="Semiconductor Boom",
            sample_headlines=["TSMC builds"],
        ),
    ])
    cfg = get_prompt("consolidate_cluster_names")
    rendered = cfg.user_prompt.format(**inp.model_dump())
    assert "AI Chips Race" in rendered
    assert "Semiconductor Boom" in rendered


def test_consolidate_cluster_names_output_shape():
    out = ConsolidateClusterNamesOutput(
        final_names=["Chip Race"],
        mapping=[
            ClusterMapping(cluster_id="hdbscan-0", final_name="Chip Race"),
            ClusterMapping(cluster_id="repechage-0", final_name="Chip Race"),
            ClusterMapping(cluster_id="hdbscan-5", final_name="Other"),
        ],
    )
    assert out.final_names == ["Chip Race"]
    assert out.mapping[-1].final_name == "Other"
