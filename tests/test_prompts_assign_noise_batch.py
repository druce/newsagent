import lib.prompts  # register
from lib.llm import get_prompt
from lib.prompts.assign_noise_batch import (
    AssignNoiseBatchInput,
    AssignNoiseBatchOutput,
    NoiseHeadline,
    NoiseAssignment,
)
from lib.prompts.assign_noise import ClusterDescriptor


def test_assign_noise_batch_registered():
    cfg = get_prompt("assign_noise_batch")
    assert cfg.input_schema is AssignNoiseBatchInput
    assert cfg.output_schema is AssignNoiseBatchOutput


def test_assign_noise_batch_input_renders():
    inp = AssignNoiseBatchInput(
        headlines=[
            NoiseHeadline(id="0", title="GPT-6 launches", summary="OpenAI ships GPT-6"),
            NoiseHeadline(id="1", title="Stock roundup", summary="Markets mixed"),
        ],
        clusters=[ClusterDescriptor(id="3", name="OpenAI", sample_headlines=["GPT-6"])],
        allow_new=True,
    )
    cfg = get_prompt("assign_noise_batch")
    rendered = cfg.user_prompt.format(**inp.model_dump())
    assert "GPT-6 launches" in rendered
    assert "Stock roundup" in rendered
    assert "OpenAI" in rendered


def test_assign_noise_batch_output_shape():
    out = AssignNoiseBatchOutput(assignments=[
        NoiseAssignment(id="0", assignment="3"),
        NoiseAssignment(id="1", assignment="none"),
    ])
    assert out.assignments[0].assignment == "3"
    assert out.assignments[1].assignment == "none"
