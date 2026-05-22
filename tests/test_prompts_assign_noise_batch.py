"""Schema/registration tests for the reassign_to_clusters prompt.

The PromptConfig.name is `reassign_to_clusters` (replaced the prior noise-only
assign_noise_batch prompt). The module filename is kept as
lib/prompts/assign_noise_batch.py for git continuity.
"""
import lib.prompts  # register
from lib.llm import get_prompt
from lib.prompts.assign_noise_batch import (
    ClusterChoice,
    HeadlineAssignment,
    HeadlineToReassign,
    ReassignToClustersInput,
    ReassignToClustersOutput,
)


def test_reassign_to_clusters_registered():
    cfg = get_prompt("reassign_to_clusters")
    assert cfg.input_schema is ReassignToClustersInput
    assert cfg.output_schema is ReassignToClustersOutput


def test_reassign_to_clusters_input_renders():
    inp = ReassignToClustersInput(
        headlines=[
            HeadlineToReassign(id="0", title="GPT-6 launches", short_summary="OpenAI ships GPT-6"),
            HeadlineToReassign(id="1", title="Stock roundup", short_summary="Markets mixed"),
        ],
        clusters=[
            ClusterChoice(name="OpenAI Releases", sample_headlines=["GPT-6"]),
        ],
    )
    cfg = get_prompt("reassign_to_clusters")
    rendered = cfg.user_prompt.format(**inp.model_dump())
    assert "GPT-6 launches" in rendered
    assert "Stock roundup" in rendered
    assert "OpenAI Releases" in rendered


def test_reassign_to_clusters_output_shape():
    out = ReassignToClustersOutput(assignments=[
        HeadlineAssignment(id="0", assignment="OpenAI Releases"),
        HeadlineAssignment(id="1", assignment="Other"),
    ])
    assert out.assignments[0].assignment == "OpenAI Releases"
    assert out.assignments[1].assignment == "Other"
