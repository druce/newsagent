import lib.prompts  # register all
from lib.llm import get_prompt
from lib.prompts.name_topic_batch import (
    NameTopicBatchInput,
    NameTopicBatchOutput,
    ClusterToName,
    ClusterName,
)


def test_name_topic_batch_registered():
    cfg = get_prompt("name_topic_batch")
    assert cfg.name == "name_topic_batch"
    assert cfg.input_schema is NameTopicBatchInput
    assert cfg.output_schema is NameTopicBatchOutput


def test_name_topic_batch_input_renders():
    inp = NameTopicBatchInput(clusters=[
        ClusterToName(cluster_id="0", entities="OpenAI, GPT-6", headlines="OpenAI ships GPT-6\nGPT-6 benchmarks"),
        ClusterToName(cluster_id="1", entities="EU, AI Act", headlines="EU passes AI Act"),
    ])
    cfg = get_prompt("name_topic_batch")
    rendered = cfg.user_prompt.format(**inp.model_dump())
    assert "OpenAI, GPT-6" in rendered
    assert "EU passes AI Act" in rendered


def test_name_topic_batch_output_shape():
    out = NameTopicBatchOutput(names=[
        ClusterName(cluster_id="0", name="OpenAI ships GPT-6"),
        ClusterName(cluster_id="1", name="EU passes the AI Act"),
    ])
    assert out.names[0].cluster_id == "0"
    assert out.names[1].name == "EU passes the AI Act"
