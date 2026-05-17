import json
import subprocess
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

    args, kwargs = mock_run.call_args
    cmd = args[0]
    assert cmd[0] == "claude"
    # Prompt is passed as the last positional or via -p flag
    assert "-p" in cmd or "--print" in cmd
    # Output format must be json
    assert "--output-format" in cmd
    fmt_idx = cmd.index("--output-format")
    assert cmd[fmt_idx + 1] == "json"


def test_subagent_engine_embeds_schema_in_prompt():
    with patch("lib.engines.subagent.subprocess.run") as mock_run:
        mock_run.return_value = _fake_run_ok({"label": "x", "score": 1})
        engine = subagent_engine()
        engine(system="be brief", user="classify this", schema=_Out)

    cmd = mock_run.call_args[0][0]
    # The prompt is one of the args; find the long arg
    prompt_text = next(a for a in cmd if "classify this" in a or "be brief" in a)
    assert "schema" in prompt_text.lower() or "json" in prompt_text.lower()
    assert "label" in prompt_text
    assert "score" in prompt_text


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
