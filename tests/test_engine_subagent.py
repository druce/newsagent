import json
from unittest.mock import patch, MagicMock
import pytest
from pydantic import BaseModel
from lib.engines.subagent import subagent_engine
from lib.engines.base import EngineError


class _Out(BaseModel):
    label: str
    score: int


def _fake_run_ok(payload: dict, returncode: int = 0):
    completed = MagicMock()
    completed.returncode = returncode
    # claude --output-format json wraps response in a top-level structure;
    # we expect our engine to extract assistant text and parse it as JSON.
    completed.stdout = json.dumps({
        "type": "result",
        "subtype": "success",
        "result": json.dumps(payload),  # the assistant's final text
    })
    completed.stderr = ""
    return completed


def test_subagent_engine_calls_claude_cli():
    with patch("lib.engines.subagent.subprocess.run") as mock_run:
        mock_run.return_value = _fake_run_ok({"label": "AI", "score": 9})
        engine = subagent_engine()
        result = engine(system="classify it", user="headline X", schema=_Out)

    assert isinstance(result, _Out)
    assert result.label == "AI"
    assert result.score == 9

    cmd = mock_run.call_args[0][0]
    assert cmd[0] == "claude"
    # Prompt is passed as the last positional or via -p flag
    assert "-p" in cmd or "--print" in cmd
    # Output format must be json
    assert "--output-format" in cmd
    fmt_idx = cmd.index("--output-format")
    assert cmd[fmt_idx + 1] == "json"


def test_subagent_engine_passes_schema_via_flag():
    with patch("lib.engines.subagent.subprocess.run") as mock_run:
        mock_run.return_value = _fake_run_ok({"label": "x", "score": 1})
        engine = subagent_engine()
        engine(system="be brief", user="classify this", schema=_Out)

    cmd = mock_run.call_args[0][0]
    assert "--json-schema" in cmd
    schema = json.loads(cmd[cmd.index("--json-schema") + 1])
    assert set(schema["properties"]) == {"label", "score"}
    # Schema and JSON-forcing text no longer ride in the prompt itself.
    prompt = cmd[cmd.index("-p") + 1]
    assert "be brief" in prompt and "classify this" in prompt
    assert "JSON Schema" not in prompt
    assert "MUST" not in prompt


def test_subagent_engine_reads_structured_output_field():
    completed = MagicMock(returncode=0, stderr="")
    completed.stdout = json.dumps({
        "type": "result", "subtype": "success", "result": "",
        "structured_output": {"label": "AI", "score": 7},
    })
    with patch("lib.engines.subagent.subprocess.run", return_value=completed):
        result = subagent_engine()(system="s", user="u", schema=_Out)
    assert result == _Out(label="AI", score=7)


def test_subagent_engine_raises_on_nonzero_exit():
    with patch("lib.engines.subagent.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="boom")
        engine = subagent_engine()
        with pytest.raises(EngineError, match="exit"):
            engine(system="s", user="u", schema=_Out)


def test_subagent_engine_raises_on_unparseable_response():
    with patch("lib.engines.subagent.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps({"type": "result", "subtype": "success",
                               "result": "not json at all"}),
            stderr="",
        )
        engine = subagent_engine()
        with pytest.raises(EngineError, match="parse"):
            engine(system="s", user="u", schema=_Out)


def test_subagent_engine_raises_when_claude_not_found():
    with patch("lib.engines.subagent.subprocess.run",
               side_effect=FileNotFoundError("no claude on PATH")):
        engine = subagent_engine()
        with pytest.raises(EngineError, match="claude"):
            engine(system="s", user="u", schema=_Out)


@pytest.mark.parametrize("effort,level", [
    (0, "low"), (3, "low"), (4, "medium"), (6, "medium"),
    (7, "high"), (8, "high"), (9, "xhigh"), (10, "xhigh"),
])
def test_subagent_engine_maps_reasoning_effort_to_flag(effort, level):
    with patch("lib.engines.subagent.subprocess.run") as mock_run:
        mock_run.return_value = _fake_run_ok({"label": "x", "score": 1})
        engine = subagent_engine()
        engine(system="s", user="u", schema=_Out, reasoning_effort=effort)
    cmd = mock_run.call_args[0][0]
    assert cmd[cmd.index("--effort") + 1] == level
    assert "reasoning effort" not in cmd[cmd.index("-p") + 1].lower()
