# Phase 1 — `call_prompt` LLM Layer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the unified `call_prompt(prompt_name, inputs, engine=..., schema=...)` LLM entry point with two engines (`"subagent"` via Claude Code CLI subprocess, `"openrouter:<model>"` via HTTP), batched parallel dispatch (K workers × N items), prompt registry, and one fully-ported `PromptConfig` (the AI-relevance classifier `filter_urls`) end-to-end.

**Architecture:**
- `lib/llm.py` is the public surface: `call_prompt`, `call_prompt_batch`, `register_prompt`, `get_prompt`.
- `lib/prompts/*.py` — one file per `PromptConfig`; each declares default engine + Pydantic input/output schemas. Module side-effect registers itself.
- `lib/engines/*.py` — each engine is a callable `(system: str, user: str, schema: type[BaseModel]) -> BaseModel`. `__init__.py` resolves engine identifier strings to callables.
- Batching uses `concurrent.futures.ThreadPoolExecutor` (both engines do blocking I/O: subprocess and `httpx`).
- Engine resolution: explicit arg → `NEWS_PROMPT_<NAME>_ENGINE` env var → `PromptConfig.default_engine`.

**No direct Anthropic SDK / API.** Per user constraint (2026-05-17): LLM calls go through Claude Code subagents (the `"subagent"` engine, which shells out to `claude -p`) or OpenRouter. Do not import `anthropic`. Do not read `ANTHROPIC_API_KEY`.

**Tech Stack:** Python 3.11, Pydantic v2, `httpx` (sync), stdlib `subprocess` + `concurrent.futures`, pytest with `respx` for HTTP mocks.

---

## Engine identifier format (locked)

| Identifier | Implementation | When used |
|---|---|---|
| `"subagent"` | Subprocess: `claude -p <prompt> --output-format json` | Default for all prompts; uses user's Claude Code subscription, no API key required |
| `"openrouter:<model>"` | HTTP POST `https://openrouter.ai/api/v1/chat/completions` via `httpx`; `OPENROUTER_API_KEY` from env. Examples: `openrouter:google/gemini-2.5-flash`, `openrouter:deepseek/deepseek-v3.1-chat` | Cost optimization, model A/B testing, non-Anthropic models |

`engine=None` resolves to (in order): `NEWS_PROMPT_<NAME>_ENGINE` env var → `PromptConfig.default_engine` → error if neither set.

## File structure

| Path | Purpose |
|---|---|
| `lib/llm.py` | Public API: `call_prompt`, `call_prompt_batch`, prompt registry, engine resolution |
| `lib/engines/__init__.py` | Engine factory: `get_engine(identifier: str) -> Callable` |
| `lib/engines/base.py` | `Engine` protocol + shared types (`EngineCall`, `EngineError`) |
| `lib/engines/openrouter.py` | OpenRouter HTTP client with structured-output (`response_format: json_schema`) |
| `lib/engines/subagent.py` | `claude -p` subprocess dispatcher with structured-output via embedded JSON-schema instruction |
| `lib/prompts/__init__.py` | Imports all prompt modules so they self-register |
| `lib/prompts/filter_urls.py` | `FILTER_URLS` `PromptConfig` ported from legacy `prompts.py:43` |
| `tests/test_llm.py` | `call_prompt` routing, env-var override, registry |
| `tests/test_prompt_filter_urls.py` | FILTER_URLS prompt schema + formatting |
| `tests/test_engine_openrouter.py` | OpenRouter request shape + response parsing (mocked via `respx`) |
| `tests/test_engine_subagent.py` | Subagent subprocess shape + response parsing (mocked via `monkeypatch`) |
| `tests/test_batching.py` | Parallelism, batch sizing, error handling |

Add `httpx>=0.27` and `respx>=0.21` to dependencies. `respx` to `[dev]`.

## Engine resolution & override semantics

```python
def resolve_engine(prompt_name: str, explicit: Optional[str], cfg: PromptConfig) -> str:
    if explicit is not None:
        return explicit
    env_var = f"NEWS_PROMPT_{prompt_name.upper()}_ENGINE"
    if env_var in os.environ:
        return os.environ[env_var]
    if cfg.default_engine is not None:
        return cfg.default_engine
    raise ValueError(f"No engine configured for {prompt_name}")
```

---

## Task 1: Add dependencies

**Files:**
- Modify: `pyproject.toml`
- Modify: `requirements.txt`

- [ ] **Step 1: Add httpx + respx to pyproject.toml**

Update `/Users/drucev/projects/news_agent/pyproject.toml`:
- In `[project] dependencies`, add `"httpx>=0.27"` after `"click>=8.1",`
- In `[project.optional-dependencies] dev`, add `"respx>=0.21"` after `"pytest-cov>=5.0",`

Final dependencies:
```toml
dependencies = [
  "pydantic>=2.7",
  "pyyaml>=6.0",
  "click>=8.1",
  "httpx>=0.27",
]

[project.optional-dependencies]
dev = [
  "pytest>=8.0",
  "pytest-cov>=5.0",
  "respx>=0.21",
]
```

- [ ] **Step 2: Update requirements.txt**

Update `/Users/drucev/projects/news_agent/requirements.txt` (replace contents):
```
pydantic>=2.7
pyyaml>=6.0
click>=8.1
httpx>=0.27
pytest>=8.0
pytest-cov>=5.0
respx>=0.21
```

- [ ] **Step 3: Reinstall and verify**

Run:
```bash
cd /Users/drucev/projects/news_agent
.venv/bin/pip install -e ".[dev]"
.venv/bin/python -c "import httpx, respx; print(httpx.__version__, respx.__version__)"
```
Expected: both versions print.

- [ ] **Step 4: Commit**

```bash
cd /Users/drucev/projects/news_agent
git add pyproject.toml requirements.txt
git commit -m "chore: add httpx + respx for call_prompt layer"
```

---

## Task 2: `lib/engines/base.py` — Engine protocol + shared types

**Files:**
- Create: `lib/engines/__init__.py`
- Create: `lib/engines/base.py`

- [ ] **Step 1: Create engines package**

Create `/Users/drucev/projects/news_agent/lib/engines/__init__.py` (empty for now; we'll fill in the factory in Task 5).

- [ ] **Step 2: Create `lib/engines/base.py`**

Create `/Users/drucev/projects/news_agent/lib/engines/base.py`:
```python
"""Shared types for engine implementations."""
from __future__ import annotations

from typing import Protocol, Type
from pydantic import BaseModel


class EngineError(Exception):
    """Raised when an engine fails to produce a valid response."""


class Engine(Protocol):
    """A callable that takes system+user prompts and returns a parsed Pydantic model."""

    def __call__(
        self,
        system: str,
        user: str,
        schema: Type[BaseModel],
    ) -> BaseModel: ...
```

- [ ] **Step 3: Commit**

```bash
cd /Users/drucev/projects/news_agent
git add lib/engines/__init__.py lib/engines/base.py
git commit -m "feat(engines): engine protocol + EngineError"
```

---

## Task 3: `lib/engines/openrouter.py` — HTTP engine with structured outputs

**Files:**
- Create: `lib/engines/openrouter.py`
- Create: `tests/test_engine_openrouter.py`

- [ ] **Step 1: Write failing tests**

Create `/Users/drucev/projects/news_agent/tests/test_engine_openrouter.py`:
```python
import json
import os
import pytest
import respx
import httpx
from pydantic import BaseModel
from lib.engines.openrouter import openrouter_engine
from lib.engines.base import EngineError


class _Out(BaseModel):
    answer: str
    confidence: float


@pytest.fixture
def or_key(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key-xyz")


def _ok_body(payload: dict) -> dict:
    return {
        "id": "gen-test",
        "choices": [{
            "message": {"role": "assistant", "content": json.dumps(payload)},
            "finish_reason": "stop",
        }],
    }


@respx.mock
def test_openrouter_engine_sends_correct_request(or_key):
    route = respx.post("https://openrouter.ai/api/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=_ok_body({"answer": "yes", "confidence": 0.9}))
    )
    engine = openrouter_engine("google/gemini-2.5-flash")
    result = engine(system="be helpful", user="is the sky blue?", schema=_Out)

    assert isinstance(result, _Out)
    assert result.answer == "yes"
    assert result.confidence == 0.9
    assert route.called
    sent = json.loads(route.calls.last.request.content)
    assert sent["model"] == "google/gemini-2.5-flash"
    assert sent["messages"] == [
        {"role": "system", "content": "be helpful"},
        {"role": "user", "content": "is the sky blue?"},
    ]
    # Structured output request
    assert sent["response_format"]["type"] == "json_schema"
    assert "schema" in sent["response_format"]["json_schema"]
    # Auth header
    auth = route.calls.last.request.headers["authorization"]
    assert auth == "Bearer test-key-xyz"


@respx.mock
def test_openrouter_engine_raises_on_http_error(or_key):
    respx.post("https://openrouter.ai/api/v1/chat/completions").mock(
        return_value=httpx.Response(500, text="server explode")
    )
    engine = openrouter_engine("google/gemini-2.5-flash")
    with pytest.raises(EngineError, match="500"):
        engine(system="s", user="u", schema=_Out)


@respx.mock
def test_openrouter_engine_raises_on_malformed_json(or_key):
    respx.post("https://openrouter.ai/api/v1/chat/completions").mock(
        return_value=httpx.Response(200, json={
            "choices": [{"message": {"role": "assistant", "content": "not json"}}],
        })
    )
    engine = openrouter_engine("google/gemini-2.5-flash")
    with pytest.raises(EngineError, match="parse"):
        engine(system="s", user="u", schema=_Out)


def test_openrouter_engine_requires_api_key(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with pytest.raises(EngineError, match="OPENROUTER_API_KEY"):
        openrouter_engine("google/gemini-2.5-flash")(system="s", user="u", schema=_Out)
```

- [ ] **Step 2: Run tests to verify they fail**

Run:
```bash
cd /Users/drucev/projects/news_agent
.venv/bin/pytest tests/test_engine_openrouter.py -v
```
Expected: ModuleNotFoundError for `lib.engines.openrouter`.

- [ ] **Step 3: Implement `lib/engines/openrouter.py`**

Create `/Users/drucev/projects/news_agent/lib/engines/openrouter.py`:
```python
"""OpenRouter HTTP engine for call_prompt.

Uses OpenRouter's OpenAI-compatible /chat/completions endpoint with
response_format: json_schema for structured outputs.
"""
from __future__ import annotations

import json
import os
from typing import Callable, Type

import httpx
from pydantic import BaseModel, ValidationError

from lib.engines.base import EngineError


_BASE_URL = "https://openrouter.ai/api/v1/chat/completions"
_TIMEOUT = httpx.Timeout(120.0, connect=10.0)


def openrouter_engine(model_id: str) -> Callable[..., BaseModel]:
    """Build an Engine callable bound to a specific OpenRouter model id."""

    def _call(system: str, user: str, schema: Type[BaseModel]) -> BaseModel:
        api_key = os.environ.get("OPENROUTER_API_KEY")
        if not api_key:
            raise EngineError("OPENROUTER_API_KEY not set in environment")

        body = {
            "model": model_id,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "structured_output",
                    "strict": True,
                    "schema": schema.model_json_schema(),
                },
            },
            "provider": {"sort": "latency", "require_parameters": True},
        }
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        try:
            with httpx.Client(timeout=_TIMEOUT) as client:
                resp = client.post(_BASE_URL, headers=headers, json=body)
        except httpx.HTTPError as exc:
            raise EngineError(f"OpenRouter HTTP error: {exc}") from exc

        if resp.status_code != 200:
            raise EngineError(
                f"OpenRouter returned {resp.status_code}: {resp.text[:500]}"
            )

        try:
            payload = resp.json()
            content = payload["choices"][0]["message"]["content"]
            parsed = json.loads(content)
        except (KeyError, IndexError, json.JSONDecodeError) as exc:
            raise EngineError(f"Failed to parse OpenRouter response: {exc}") from exc

        try:
            return schema.model_validate(parsed)
        except ValidationError as exc:
            raise EngineError(f"Response failed schema validation: {exc}") from exc

    return _call
```

- [ ] **Step 4: Run tests to verify they pass**

Run:
```bash
cd /Users/drucev/projects/news_agent
.venv/bin/pytest tests/test_engine_openrouter.py -v
```
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
cd /Users/drucev/projects/news_agent
git add lib/engines/openrouter.py tests/test_engine_openrouter.py
git commit -m "feat(engines): OpenRouter HTTP engine with structured outputs"
```

---

## Task 4: `lib/engines/subagent.py` — Claude CLI subprocess engine

**Files:**
- Create: `lib/engines/subagent.py`
- Create: `tests/test_engine_subagent.py`

The subagent engine shells out to `claude -p <prompt> --output-format json`. This runs under the user's Claude Code subscription — no API key needed. Structured output is enforced by embedding the JSON-schema in the prompt and parsing the response.

- [ ] **Step 1: Write failing tests**

Create `/Users/drucev/projects/news_agent/tests/test_engine_subagent.py`:
```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run:
```bash
cd /Users/drucev/projects/news_agent
.venv/bin/pytest tests/test_engine_subagent.py -v
```
Expected: ModuleNotFoundError for `lib.engines.subagent`.

- [ ] **Step 3: Implement `lib/engines/subagent.py`**

Create `/Users/drucev/projects/news_agent/lib/engines/subagent.py`:
```python
"""Claude Code CLI subprocess engine.

Shells out to `claude -p <prompt> --output-format json`. Uses the user's
Claude Code subscription; no API key required. Structured output is enforced
by embedding the JSON schema in the prompt and parsing the result.
"""
from __future__ import annotations

import json
import subprocess
from typing import Callable, Type

from pydantic import BaseModel, ValidationError

from lib.engines.base import EngineError


_TIMEOUT_SEC = 300


def _build_prompt(system: str, user: str, schema: Type[BaseModel]) -> str:
    schema_json = json.dumps(schema.model_json_schema(), indent=2)
    return (
        f"{system}\n\n"
        f"User input:\n{user}\n\n"
        f"You MUST respond with valid JSON matching this exact schema. "
        f"No prose, no markdown fences — just the JSON object.\n\n"
        f"JSON Schema:\n{schema_json}"
    )


def subagent_engine() -> Callable[..., BaseModel]:
    """Build an Engine callable that dispatches to `claude -p`."""

    def _call(system: str, user: str, schema: Type[BaseModel]) -> BaseModel:
        prompt = _build_prompt(system, user, schema)
        cmd = ["claude", "-p", prompt, "--output-format", "json"]

        try:
            completed = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=_TIMEOUT_SEC,
            )
        except FileNotFoundError as exc:
            raise EngineError("`claude` CLI not found on PATH") from exc
        except subprocess.TimeoutExpired as exc:
            raise EngineError(f"claude CLI timed out after {_TIMEOUT_SEC}s") from exc

        if completed.returncode != 0:
            raise EngineError(
                f"claude CLI exit {completed.returncode}: {completed.stderr[:500]}"
            )

        try:
            wrapper = json.loads(completed.stdout)
            assistant_text = wrapper.get("result", "")
            parsed = json.loads(assistant_text)
        except (json.JSONDecodeError, AttributeError) as exc:
            raise EngineError(f"Failed to parse claude CLI response: {exc}") from exc

        try:
            return schema.model_validate(parsed)
        except ValidationError as exc:
            raise EngineError(f"Subagent response failed schema validation: {exc}") from exc

    return _call
```

- [ ] **Step 4: Run tests to verify they pass**

Run:
```bash
cd /Users/drucev/projects/news_agent
.venv/bin/pytest tests/test_engine_subagent.py -v
```
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
cd /Users/drucev/projects/news_agent
git add lib/engines/subagent.py tests/test_engine_subagent.py
git commit -m "feat(engines): claude CLI subprocess engine"
```

---

## Task 5: `lib/engines/__init__.py` — engine factory

**Files:**
- Modify: `lib/engines/__init__.py`

- [ ] **Step 1: Implement factory**

Replace `/Users/drucev/projects/news_agent/lib/engines/__init__.py`:
```python
"""Engine factory: resolve string identifiers to Engine callables."""
from __future__ import annotations

from typing import Callable
from pydantic import BaseModel

from lib.engines.base import Engine, EngineError
from lib.engines.openrouter import openrouter_engine
from lib.engines.subagent import subagent_engine


def get_engine(identifier: str) -> Callable[..., BaseModel]:
    """Resolve an engine identifier string to a callable.

    Identifiers:
      - "subagent"               → Claude CLI subprocess
      - "openrouter:<model_id>"  → OpenRouter HTTP, e.g. "openrouter:google/gemini-2.5-flash"
    """
    if identifier == "subagent":
        return subagent_engine()
    if identifier.startswith("openrouter:"):
        model_id = identifier[len("openrouter:"):]
        if not model_id:
            raise EngineError("openrouter engine requires a model id, e.g. openrouter:google/gemini-2.5-flash")
        return openrouter_engine(model_id)
    raise EngineError(f"Unknown engine identifier: {identifier!r}")


__all__ = ["get_engine", "Engine", "EngineError"]
```

- [ ] **Step 2: Add factory tests inline**

Append to `/Users/drucev/projects/news_agent/tests/test_engine_subagent.py` (or create `tests/test_engine_factory.py`; choose factory):

Create `/Users/drucev/projects/news_agent/tests/test_engine_factory.py`:
```python
import pytest
from lib.engines import get_engine, EngineError


def test_get_engine_subagent():
    assert callable(get_engine("subagent"))


def test_get_engine_openrouter_requires_model():
    with pytest.raises(EngineError, match="model id"):
        get_engine("openrouter:")


def test_get_engine_openrouter_with_model():
    assert callable(get_engine("openrouter:google/gemini-2.5-flash"))


def test_get_engine_unknown():
    with pytest.raises(EngineError, match="Unknown"):
        get_engine("anthropic:claude-opus-4-7")
```

- [ ] **Step 3: Run tests**

Run:
```bash
cd /Users/drucev/projects/news_agent
.venv/bin/pytest tests/test_engine_factory.py -v
```
Expected: 4 passed.

- [ ] **Step 4: Commit**

```bash
cd /Users/drucev/projects/news_agent
git add lib/engines/__init__.py tests/test_engine_factory.py
git commit -m "feat(engines): factory resolves 'subagent' and 'openrouter:<m>'"
```

---

## Task 6: `lib/llm.py` — `call_prompt` + PromptConfig + registry

**Files:**
- Create: `lib/llm.py`
- Create: `tests/test_llm.py`

- [ ] **Step 1: Write failing tests**

Create `/Users/drucev/projects/news_agent/tests/test_llm.py`:
```python
import os
import pytest
from pydantic import BaseModel
from lib.llm import (
    PromptConfig, call_prompt, register_prompt, get_prompt, _registry,
)


class _In(BaseModel):
    title: str


class _Out(BaseModel):
    is_ai: bool


def _make_cfg() -> PromptConfig:
    return PromptConfig(
        name="test_prompt",
        system_prompt="be brief",
        user_prompt="Classify: {title}",
        input_schema=_In,
        output_schema=_Out,
        default_engine="subagent",
    )


@pytest.fixture(autouse=True)
def _clean_registry():
    _registry.clear()
    yield
    _registry.clear()


def test_register_and_get_prompt():
    cfg = _make_cfg()
    register_prompt(cfg)
    assert get_prompt("test_prompt") is cfg


def test_register_prompt_rejects_duplicate():
    register_prompt(_make_cfg())
    with pytest.raises(ValueError, match="already registered"):
        register_prompt(_make_cfg())


def test_get_prompt_missing():
    with pytest.raises(KeyError, match="test_prompt"):
        get_prompt("test_prompt")


def test_call_prompt_formats_user_prompt_and_routes(monkeypatch):
    register_prompt(_make_cfg())
    called = {}

    def fake_engine(system, user, schema):
        called["system"] = system
        called["user"] = user
        called["schema"] = schema
        return schema(is_ai=True)

    def fake_get_engine(identifier):
        called["engine"] = identifier
        return fake_engine

    monkeypatch.setattr("lib.llm.get_engine", fake_get_engine)

    result = call_prompt("test_prompt", {"title": "GPT-6 released"})
    assert result.is_ai is True
    assert called["system"] == "be brief"
    assert called["user"] == "Classify: GPT-6 released"
    assert called["schema"] is _Out
    assert called["engine"] == "subagent"


def test_call_prompt_explicit_engine_overrides_default(monkeypatch):
    register_prompt(_make_cfg())
    captured = {}

    def fake_get_engine(identifier):
        captured["id"] = identifier
        return lambda system, user, schema: schema(is_ai=False)

    monkeypatch.setattr("lib.llm.get_engine", fake_get_engine)

    call_prompt("test_prompt", {"title": "x"}, engine="openrouter:google/gemini-2.5-flash")
    assert captured["id"] == "openrouter:google/gemini-2.5-flash"


def test_call_prompt_env_var_overrides_default(monkeypatch):
    register_prompt(_make_cfg())
    monkeypatch.setenv("NEWS_PROMPT_TEST_PROMPT_ENGINE", "openrouter:foo/bar")
    captured = {}

    def fake_get_engine(identifier):
        captured["id"] = identifier
        return lambda system, user, schema: schema(is_ai=False)

    monkeypatch.setattr("lib.llm.get_engine", fake_get_engine)

    call_prompt("test_prompt", {"title": "x"})
    assert captured["id"] == "openrouter:foo/bar"


def test_call_prompt_explicit_beats_env_var(monkeypatch):
    register_prompt(_make_cfg())
    monkeypatch.setenv("NEWS_PROMPT_TEST_PROMPT_ENGINE", "openrouter:via-env")
    captured = {}

    def fake_get_engine(identifier):
        captured["id"] = identifier
        return lambda system, user, schema: schema(is_ai=False)

    monkeypatch.setattr("lib.llm.get_engine", fake_get_engine)

    call_prompt("test_prompt", {"title": "x"}, engine="openrouter:via-arg")
    assert captured["id"] == "openrouter:via-arg"


def test_call_prompt_rejects_missing_engine(monkeypatch):
    cfg = PromptConfig(
        name="no_default",
        system_prompt="s", user_prompt="u: {title}",
        input_schema=_In, output_schema=_Out,
        default_engine=None,
    )
    register_prompt(cfg)
    monkeypatch.delenv("NEWS_PROMPT_NO_DEFAULT_ENGINE", raising=False)
    with pytest.raises(ValueError, match="No engine"):
        call_prompt("no_default", {"title": "x"})


def test_call_prompt_validates_input(monkeypatch):
    register_prompt(_make_cfg())
    monkeypatch.setattr("lib.llm.get_engine",
                        lambda i: (lambda s, u, sc: sc(is_ai=True)))
    with pytest.raises(ValueError):
        call_prompt("test_prompt", {"wrong_field": "x"})
```

- [ ] **Step 2: Run tests to verify they fail**

Run:
```bash
cd /Users/drucev/projects/news_agent
.venv/bin/pytest tests/test_llm.py -v
```
Expected: ModuleNotFoundError.

- [ ] **Step 3: Implement `lib/llm.py`**

Create `/Users/drucev/projects/news_agent/lib/llm.py`:
```python
"""Unified LLM entry point: call_prompt.

Every LLM call in the pipeline goes through here. Engine routing is:
  explicit arg → NEWS_PROMPT_<NAME>_ENGINE env var → PromptConfig.default_engine
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Dict, Optional, Type

from pydantic import BaseModel, ValidationError

from lib.engines import get_engine


@dataclass(frozen=True)
class PromptConfig:
    name: str
    system_prompt: str
    user_prompt: str
    input_schema: Type[BaseModel]
    output_schema: Type[BaseModel]
    default_engine: Optional[str]


_registry: Dict[str, PromptConfig] = {}


def register_prompt(cfg: PromptConfig) -> None:
    if cfg.name in _registry:
        raise ValueError(f"Prompt {cfg.name!r} already registered")
    _registry[cfg.name] = cfg


def get_prompt(name: str) -> PromptConfig:
    if name not in _registry:
        raise KeyError(f"Prompt not registered: {name!r}")
    return _registry[name]


def _resolve_engine(prompt_name: str, explicit: Optional[str],
                    cfg: PromptConfig) -> str:
    if explicit is not None:
        return explicit
    env_var = f"NEWS_PROMPT_{prompt_name.upper()}_ENGINE"
    if env_var in os.environ:
        return os.environ[env_var]
    if cfg.default_engine is not None:
        return cfg.default_engine
    raise ValueError(
        f"No engine configured for {prompt_name!r} "
        f"(set {env_var} or pass engine=...)"
    )


def call_prompt(
    prompt_name: str,
    inputs: Dict[str, Any] | BaseModel,
    *,
    engine: Optional[str] = None,
) -> BaseModel:
    """Run a registered prompt against an engine and return the parsed result."""
    cfg = get_prompt(prompt_name)

    if isinstance(inputs, BaseModel):
        validated = inputs
    else:
        try:
            validated = cfg.input_schema.model_validate(inputs)
        except ValidationError as exc:
            raise ValueError(f"Input validation failed for {prompt_name}: {exc}") from exc

    user_str = cfg.user_prompt.format(**validated.model_dump())

    engine_id = _resolve_engine(prompt_name, engine, cfg)
    eng = get_engine(engine_id)
    return eng(system=cfg.system_prompt, user=user_str, schema=cfg.output_schema)
```

- [ ] **Step 4: Run tests to verify they pass**

Run:
```bash
cd /Users/drucev/projects/news_agent
.venv/bin/pytest tests/test_llm.py -v
```
Expected: 9 passed.

- [ ] **Step 5: Commit**

```bash
cd /Users/drucev/projects/news_agent
git add lib/llm.py tests/test_llm.py
git commit -m "feat(llm): call_prompt entry point + prompt registry"
```

---

## Task 7: `lib/llm.py` — `call_prompt_batch` parallel dispatch

**Files:**
- Modify: `lib/llm.py`
- Create: `tests/test_batching.py`

- [ ] **Step 1: Write failing tests**

Create `/Users/drucev/projects/news_agent/tests/test_batching.py`:
```python
import threading
import time
import pytest
from pydantic import BaseModel
from lib.llm import (
    PromptConfig, register_prompt, call_prompt_batch, _registry,
)


class _In(BaseModel):
    n: int


class _Out(BaseModel):
    doubled: int


@pytest.fixture(autouse=True)
def _clean_registry():
    _registry.clear()
    yield
    _registry.clear()


def _register():
    register_prompt(PromptConfig(
        name="double",
        system_prompt="double the number",
        user_prompt="n={n}",
        input_schema=_In,
        output_schema=_Out,
        default_engine="subagent",
    ))


def test_batch_returns_results_in_order(monkeypatch):
    _register()
    def fake_engine(system, user, schema):
        n = int(user.split("=")[1])
        return schema(doubled=n * 2)
    monkeypatch.setattr("lib.llm.get_engine", lambda i: fake_engine)

    results = call_prompt_batch("double", [{"n": i} for i in range(5)], parallelism=2)
    assert [r.doubled for r in results] == [0, 2, 4, 6, 8]


def test_batch_runs_in_parallel(monkeypatch):
    _register()
    active_lock = threading.Lock()
    active = {"now": 0, "peak": 0}

    def fake_engine(system, user, schema):
        with active_lock:
            active["now"] += 1
            active["peak"] = max(active["peak"], active["now"])
        time.sleep(0.05)
        with active_lock:
            active["now"] -= 1
        return schema(doubled=1)

    monkeypatch.setattr("lib.llm.get_engine", lambda i: fake_engine)
    call_prompt_batch("double", [{"n": i} for i in range(10)], parallelism=4)
    assert active["peak"] >= 3, f"expected ≥3 concurrent calls, got {active['peak']}"
    assert active["peak"] <= 4, f"expected ≤4 concurrent, got {active['peak']}"


def test_batch_propagates_errors(monkeypatch):
    _register()
    def fake_engine(system, user, schema):
        n = int(user.split("=")[1])
        if n == 3:
            raise RuntimeError("boom")
        return schema(doubled=n * 2)
    monkeypatch.setattr("lib.llm.get_engine", lambda i: fake_engine)

    with pytest.raises(RuntimeError, match="boom"):
        call_prompt_batch("double", [{"n": i} for i in range(5)], parallelism=2)


def test_batch_empty_inputs(monkeypatch):
    _register()
    monkeypatch.setattr("lib.llm.get_engine",
                        lambda i: (lambda s, u, sc: sc(doubled=0)))
    assert call_prompt_batch("double", [], parallelism=4) == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run:
```bash
cd /Users/drucev/projects/news_agent
.venv/bin/pytest tests/test_batching.py -v
```
Expected: `ImportError: cannot import name 'call_prompt_batch'`.

- [ ] **Step 3: Add `call_prompt_batch` to `lib/llm.py`**

Append to `/Users/drucev/projects/news_agent/lib/llm.py`:
```python
from concurrent.futures import ThreadPoolExecutor
from typing import List, Sequence


def call_prompt_batch(
    prompt_name: str,
    inputs: Sequence[Dict[str, Any] | BaseModel],
    *,
    engine: Optional[str] = None,
    parallelism: int = 8,
) -> List[BaseModel]:
    """Run a prompt over many inputs concurrently. Returns results in input order.

    Concurrency: up to `parallelism` engine calls in flight at once. Both engines
    block on I/O (subprocess / HTTP), so threading is sufficient — no asyncio.
    """
    if not inputs:
        return []
    if parallelism < 1:
        raise ValueError("parallelism must be >= 1")

    def _one(item):
        return call_prompt(prompt_name, item, engine=engine)

    with ThreadPoolExecutor(max_workers=parallelism) as pool:
        # executor.map preserves order and surfaces exceptions
        return list(pool.map(_one, inputs))
```

Also update the top-of-file imports to add `Sequence`, `List` if not already present.

- [ ] **Step 4: Run tests to verify they pass**

Run:
```bash
cd /Users/drucev/projects/news_agent
.venv/bin/pytest tests/test_batching.py -v
```
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
cd /Users/drucev/projects/news_agent
git add lib/llm.py tests/test_batching.py
git commit -m "feat(llm): call_prompt_batch with thread-pool parallelism"
```

---

## Task 8: Port `FILTER_URLS` PromptConfig

**Files:**
- Create: `lib/prompts/__init__.py`
- Create: `lib/prompts/filter_urls.py`
- Create: `tests/test_prompt_filter_urls.py`

- [ ] **Step 1: Write failing tests**

Create `/Users/drucev/projects/news_agent/tests/test_prompt_filter_urls.py`:
```python
import pytest
from lib.llm import get_prompt, _registry
import lib.prompts  # triggers registration


def test_filter_urls_registered():
    cfg = get_prompt("filter_urls")
    assert cfg is not None
    assert cfg.default_engine == "subagent"


def test_filter_urls_input_schema_accepts_list_of_headlines():
    cfg = get_prompt("filter_urls")
    valid = {"items": [{"id": "h1", "title": "GPT-6 released"}]}
    parsed = cfg.input_schema.model_validate(valid)
    assert parsed.items[0].title == "GPT-6 released"


def test_filter_urls_output_schema_round_trips():
    cfg = get_prompt("filter_urls")
    out = cfg.output_schema.model_validate({"classifications": [
        {"id": "h1", "is_ai": True},
        {"id": "h2", "is_ai": False},
    ]})
    assert len(out.classifications) == 2
    assert out.classifications[0].is_ai is True


def test_filter_urls_user_prompt_formats_items():
    cfg = get_prompt("filter_urls")
    user = cfg.user_prompt.format(input_text='[{"id":"h1","title":"x"}]')
    assert "h1" in user


def test_filter_urls_system_prompt_mentions_json():
    cfg = get_prompt("filter_urls")
    assert "JSON" in cfg.system_prompt or "json" in cfg.system_prompt.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run:
```bash
cd /Users/drucev/projects/news_agent
.venv/bin/pytest tests/test_prompt_filter_urls.py -v
```
Expected: ModuleNotFoundError for `lib.prompts`.

- [ ] **Step 3: Implement prompts package + filter_urls**

Create `/Users/drucev/projects/news_agent/lib/prompts/__init__.py`:
```python
"""Prompt registry — importing this package registers all prompts."""
from lib.prompts import filter_urls  # noqa: F401

__all__ = ["filter_urls"]
```

Create `/Users/drucev/projects/news_agent/lib/prompts/filter_urls.py`:
```python
"""filter_urls — classify headlines as AI-related.

Ported from ~/projects/OpenAIAgentsSDK/prompts.py:43 (FILTER_URLS).
"""
from __future__ import annotations

from typing import List

from pydantic import BaseModel, Field

from lib.llm import PromptConfig, register_prompt


class HeadlineItem(BaseModel):
    id: str
    title: str


class FilterUrlsInput(BaseModel):
    items: List[HeadlineItem] = Field(min_length=1)

    # Convenience for the user_prompt formatter
    @property
    def input_text(self) -> str:
        import json
        return json.dumps([item.model_dump() for item in self.items])


class HeadlineClassification(BaseModel):
    id: str
    is_ai: bool


class FilterUrlsOutput(BaseModel):
    classifications: List[HeadlineClassification]


_SYSTEM = """\
You are a content-classification assistant that labels news headlines as AI-related or not.
You will receive a list of JSON objects with fields "id" and "title".
Return **only** a JSON object that satisfies the provided schema.
For each headline provided, you MUST return one element with the same id, and a boolean value; do not skip any items.
Return elements in the same order they were provided.
No markdown, no markdown fences, no extra keys, no comments."""

_USER = """\
Classify every headline below.

AI-related if the title mentions (explicitly or implicitly):
- Core AI technologies: machine learning, neural / deep / transformer networks
- AI Applications: computer vision, NLP, robotics, autonomous driving, generative media
- AI hardware, GPU chip supply, AI data centers and infrastructure
- Companies or labs known for AI: OpenAI, DeepMind, Anthropic, xAI, NVIDIA, etc.
- AI models & products: ChatGPT, Gemini, Claude, Sora, Midjourney, DeepSeek, etc.
- New AI products and AI integration into existing products/services
- AI policy / ethics / safety / regulation / analysis
- Research results related to AI
- AI industry figures (Sam Altman, Demis Hassabis, etc.)
- AI market and business developments, funding rounds, partnerships centered on AI
- Any other news with a significant AI component

Non-AI examples: crypto, ordinary software, non-AI gadgets and medical devices, and anything else.
Input:
{input_text}"""


FILTER_URLS = PromptConfig(
    name="filter_urls",
    system_prompt=_SYSTEM,
    user_prompt=_USER,
    input_schema=FilterUrlsInput,
    output_schema=FilterUrlsOutput,
    default_engine="subagent",
)

register_prompt(FILTER_URLS)
```

Note: `FilterUrlsInput.input_text` is a `@property`, but `call_prompt` formats via `model_dump()` which doesn't include properties. We need to plumb computed fields through.

Update the file: replace the `input_text` `@property` with a `computed_field`:

```python
from pydantic import BaseModel, Field, computed_field


class FilterUrlsInput(BaseModel):
    items: List[HeadlineItem] = Field(min_length=1)

    @computed_field
    @property
    def input_text(self) -> str:
        import json
        return json.dumps([item.model_dump() for item in self.items])
```

`computed_field` makes the property appear in `.model_dump()` output, so `user_prompt.format(input_text=...)` works.

- [ ] **Step 4: Run tests to verify they pass**

Run:
```bash
cd /Users/drucev/projects/news_agent
.venv/bin/pytest tests/test_prompt_filter_urls.py -v
```
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
cd /Users/drucev/projects/news_agent
git add lib/prompts/__init__.py lib/prompts/filter_urls.py tests/test_prompt_filter_urls.py
git commit -m "feat(prompts): port FILTER_URLS as first PromptConfig"
```

---

## Task 9: End-to-end Phase 1 verification

**Files:** none modified.

- [ ] **Step 1: Full test suite passes**

Run:
```bash
cd /Users/drucev/projects/news_agent
.venv/bin/pytest tests/ -v --cov=lib --cov-report=term-missing
```
Expected: all tests pass (24 from Phase 0 + ~26 new = ~50 total). `lib/llm.py` coverage ≥ 85%; `lib/engines/openrouter.py` ≥ 80%; `lib/engines/subagent.py` ≥ 80%.

- [ ] **Step 2: Engine override smoke test (no network)**

Run:
```bash
cd /Users/drucev/projects/news_agent
.venv/bin/python -c "
import os, sys
os.environ.setdefault('OPENROUTER_API_KEY', 'fake')
# Verify env-var override path resolves cleanly
os.environ['NEWS_PROMPT_FILTER_URLS_ENGINE'] = 'openrouter:google/gemini-2.5-flash'
import lib.prompts
from lib.llm import get_prompt, _resolve_engine
cfg = get_prompt('filter_urls')
print('default:', cfg.default_engine)
print('resolved:', _resolve_engine('filter_urls', None, cfg))
assert _resolve_engine('filter_urls', None, cfg) == 'openrouter:google/gemini-2.5-flash'
print('OK')
"
```
Expected: prints `default: subagent`, `resolved: openrouter:google/gemini-2.5-flash`, `OK`.

- [ ] **Step 3: (Optional) live OpenRouter smoke test**

ONLY if `OPENROUTER_API_KEY` is set:

```bash
cd /Users/drucev/projects/news_agent
.venv/bin/python -c "
import lib.prompts
from lib.llm import call_prompt
result = call_prompt(
    'filter_urls',
    {'items': [
        {'id': 'a', 'title': 'OpenAI releases GPT-6'},
        {'id': 'b', 'title': 'Apple announces new MacBook'},
    ]},
    engine='openrouter:google/gemini-2.5-flash',
)
print(result.model_dump_json(indent=2))
"
```
Expected: `classifications[0].is_ai == True`, `classifications[1].is_ai == False`.

If `OPENROUTER_API_KEY` is not set, skip with `echo 'OPENROUTER_API_KEY not set — skipping live test'`.

- [ ] **Step 4: Tag**

```bash
cd /Users/drucev/projects/news_agent
git tag phase-1-complete
git log --oneline phase-0-complete..phase-1-complete
```

---

## Notes for the implementer

- **No Anthropic SDK / API.** This is a hard constraint. Do not import `anthropic`. Do not read `ANTHROPIC_API_KEY` anywhere. The `subagent` engine uses the user's Claude Code subscription via subprocess.
- **Threading vs async.** Both engines do blocking I/O. `ThreadPoolExecutor` is sufficient and simpler than asyncio. If later phases need true async (e.g. streaming), refactor then.
- **`computed_field` in Pydantic v2.** This is the right tool for "I want this property to appear in `model_dump()`." Don't manually add fields that duplicate computed state.
- **Test isolation.** `_registry` is module-level state. Every test that touches it must clean up via the autouse fixture pattern shown in `test_llm.py`.
- **`respx` for HTTP mocks.** It's well-supported with httpx. Always use `@respx.mock` decorator (or context manager) — don't reach for `unittest.mock` to fake HTTP responses.
- **Don't import from legacy `~/projects/OpenAIAgentsSDK/`.** Copy text where needed (we did this for `filter_urls`).

## Out of scope for Phase 1

- Async engines (deferred until a step actually demands it)
- Retry / rate-limit handling (deferred to Phase 2 when we hit real APIs in earnest)
- Engine-specific knobs like `temperature`, `reasoning_effort` (port when a prompt needs them)
- More than one ported PromptConfig (rest come in Phase 3 as their steps are built)
- The `cli:claude` Max-plan engine path mentioned in CLAUDE_REFACTOR (the `subagent` engine already covers this use case)
