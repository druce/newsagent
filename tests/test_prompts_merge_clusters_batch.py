import lib.prompts  # noqa: F401
from lib.llm import get_prompt
from lib.prompts.merge_clusters_batch import (
    MergeClustersBatchInput,
    MergeClustersBatchOutput,
    PairToDecide,
    MergeDecision,
)
from lib.prompts.merge_clusters import ClusterPair


def test_merge_clusters_batch_registered():
    cfg = get_prompt("merge_clusters_batch")
    assert cfg.input_schema is MergeClustersBatchInput
    assert cfg.output_schema is MergeClustersBatchOutput


def test_merge_clusters_batch_input_renders():
    inp = MergeClustersBatchInput(pairs=[
        PairToDecide(
            pair_id="0",
            a=ClusterPair(id="1", name="GPT-6 launch", top_headlines=["GPT-6 ships"]),
            b=ClusterPair(id="2", name="OpenAI ships GPT-6", top_headlines=["GPT-6 benchmarks"]),
        ),
    ])
    cfg = get_prompt("merge_clusters_batch")
    rendered = cfg.user_prompt.format(**inp.model_dump())
    assert "GPT-6 launch" in rendered
    assert "OpenAI ships GPT-6" in rendered


def test_merge_clusters_batch_output_shape():
    out = MergeClustersBatchOutput(decisions=[
        MergeDecision(pair_id="0", merge=True, merged_name="OpenAI ships GPT-6"),
        MergeDecision(pair_id="1", merge=False, merged_name=None),
    ])
    assert out.decisions[0].merge is True
    assert out.decisions[1].merged_name is None
