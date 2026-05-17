# Phase 3 — LLM Steps (`filter`, `summarize`, `dedupe`, `rate`) + OpenAI/Google Engines + Embeddings

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire the LLM-driven pipeline steps using `call_prompt`. Adds two new engines (`openai`, `google`), an embeddings helper (OpenAI `text-embedding-3-large`), four new pipeline steps (`filter`, `summarize`, `dedupe`, `rate`), and the Bradley-Terry Swiss-paired rating math.

**Architecture:**
- `PromptConfig` extended with `reasoning_effort: int` (0–10 scale, mirroring legacy). Engines accept it as a kwarg.
- New engines: `openai:<model>` (chat completions), `google:<model>` (Gemini). Engine factory updated.
- Embeddings live separately at `lib/embeddings.py` — they're a different API surface from chat (different endpoint, different schema, batched).
- `filter` wires the existing `FILTER_URLS` prompt; writes `is_ai` back to `headline_data` and `urls.isAI`.
- `summarize` ports `EXTRACT_SUMMARIES`; reads `download/<id>.txt`, writes bullet summary into `headline_data[i]["summary"]`.
- `dedupe` embeds summaries, computes cosine similarity, marks near-duplicates (≥0.95) for removal. Runs after summarize.
- `rate` is the big one: per-axis confidence ratings (quality, on-topic, importance) + Swiss-paired Bradley-Terry battles via `choix.opt_pairwise`. Combines signals into a composite `rating` column with per-signal columns preserved for debugging.

**Hard constraints (from memory):**
- No Anthropic SDK. Allowed engines: subagent, openrouter, openai, google.
- Embeddings use OpenAI `text-embedding-3-large` (matches legacy + the existing `umap_reducer.pkl`).
- PromptConfig binds to model + reasoning_effort per legacy style; subagent is the implicit fallback default.

**Tech Stack additions:** `openai>=1.50`, `google-genai>=0.4`, `choix>=0.3`, `numpy>=1.26`, `scipy>=1.13`.

**Reference (read, don't import):**
- `~/projects/OpenAIAgentsSDK/prompts.py` — all the prompt text
- `~/projects/OpenAIAgentsSDK/do_rating.py` — Bradley-Terry, Swiss pairing
- `~/projects/OpenAIAgentsSDK/do_dedupe.py` — cosine-similarity dedup
- `~/projects/OpenAIAgentsSDK/llm.py:840-925` — OpenRouter request shape (reference)

## File structure (new files)

| Path | Purpose |
|---|---|
| `lib/llm.py` (modify) | Add `reasoning_effort` field to PromptConfig, pass through to engines |
| `lib/engines/openrouter.py` (modify) | Accept `reasoning_effort` kwarg → `extra_body.reasoning.effort` |
| `lib/engines/subagent.py` (modify) | Accept `reasoning_effort` kwarg → embed in prompt |
| `lib/engines/openai_chat.py` | New: OpenAI chat completions engine |
| `lib/engines/google.py` | New: Google Gemini engine |
| `lib/engines/__init__.py` (modify) | Factory dispatch for `openai:` and `google:` |
| `lib/embeddings.py` | OpenAI embeddings batched helper |
| `lib/prompts/extract_summaries.py` | Port of `EXTRACT_SUMMARIES` from legacy |
| `lib/prompts/rate_quality.py` | Port of `RATE_QUALITY` |
| `lib/prompts/rate_on_topic.py` | Port of `RATE_ON_TOPIC` |
| `lib/prompts/rate_importance.py` | Port of `RATE_IMPORTANCE` |
| `lib/prompts/battle.py` | Port of `BATTLE_PROMPT` |
| `lib/prompts/__init__.py` (modify) | Import all new prompts |
| `lib/prompts/filter_urls.py` (modify) | Add `reasoning_effort=4` |
| `lib/rating.py` | Swiss pairing + Bradley-Terry orchestration (uses `choix`) |
| `lib/steps/filter.py` | news:filter — wires FILTER_URLS prompt |
| `lib/steps/summarize.py` | news:summarize — wires EXTRACT_SUMMARIES |
| `lib/steps/dedupe.py` | news:dedupe — cosine similarity |
| `lib/steps/rate.py` | news:rate — runs all signals + Bradley-Terry |
| `skills/{filter,summarize,dedupe,rate}/SKILL.md` | Agent-facing contracts |
| `tests/test_engine_openai_chat.py` |  |
| `tests/test_engine_google.py` |  |
| `tests/test_embeddings.py` |  |
| `tests/test_step_filter.py` |  |
| `tests/test_step_summarize.py` |  |
| `tests/test_step_dedupe.py` |  |
| `tests/test_rating.py` | Swiss pairing + BT math (no LLM, with synthetic battles) |
| `tests/test_step_rate.py` | Rate step end-to-end (LLM mocked) |

## Engine `reasoning_effort` mapping (locked)

| Engine | How `reasoning_effort` is applied |
|---|---|
| `subagent` | Embed `Reasoning effort: <int>/10` line in prompt; the model treats it as a hint |
| `openrouter:<m>` | Map to `extra_body.reasoning.effort`: 0→none, 1-3→"low", 4-6→"medium", 7-10→"high" |
| `openai:<m>` | Map to `reasoning_effort` API param: "minimal" (0), "low" (1-3), "medium" (4-6), "high" (7-10). Only sent if model supports it (o-series, gpt-5-mini, etc.). Ignored otherwise. |
| `google:<m>` | Map to `thinking_config.thinking_budget` (0 disables, otherwise scaled token budget). |

---

## Task 1: Extend PromptConfig + thread `reasoning_effort` through engines

**Files:**
- Modify: `lib/llm.py`
- Modify: `lib/engines/openrouter.py`
- Modify: `lib/engines/subagent.py`
- Modify: `lib/engines/__init__.py` (engine signatures)
- Modify: `lib/prompts/filter_urls.py` (add `reasoning_effort=4`)
- Modify: `tests/test_llm.py`, `tests/test_engine_openrouter.py`, `tests/test_engine_subagent.py`, `tests/test_prompt_filter_urls.py`

- [ ] **Step 1: Update tests to assert reasoning_effort plumbing**

Add to `tests/test_llm.py` (append, don't replace):
```python
def test_call_prompt_passes_reasoning_effort_to_engine(monkeypatch):
    register_prompt(PromptConfig(
        name="effort_test",
        system_prompt="s", user_prompt="u: {title}",
        input_schema=_In, output_schema=_Out,
        default_engine="subagent",
        reasoning_effort=8,
    ))
    captured = {}
    def fake_engine(system, user, schema, reasoning_effort=None):
        captured["effort"] = reasoning_effort
        return schema(is_ai=True)
    monkeypatch.setattr("lib.llm.get_engine", lambda i: fake_engine)
    call_prompt("effort_test", {"title": "x"})
    assert captured["effort"] == 8
```

Add to `tests/test_engine_openrouter.py`:
```python
@respx.mock
def test_openrouter_engine_sends_reasoning_effort(or_key):
    route = respx.post("https://openrouter.ai/api/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=_ok_body({"answer": "y", "confidence": 0.5}))
    )
    engine = openrouter_engine("google/gemini-2.5-flash")
    engine(system="s", user="u", schema=_Out, reasoning_effort=8)
    sent = json.loads(route.calls.last.request.content)
    assert sent["extra_body"]["reasoning"]["effort"] == "high"


@respx.mock
def test_openrouter_engine_omits_reasoning_when_zero(or_key):
    route = respx.post("https://openrouter.ai/api/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=_ok_body({"answer": "y", "confidence": 0.5}))
    )
    engine = openrouter_engine("google/gemini-2.5-flash")
    engine(system="s", user="u", schema=_Out, reasoning_effort=0)
    sent = json.loads(route.calls.last.request.content)
    # reasoning_effort=0 disables reasoning
    assert sent.get("extra_body", {}).get("reasoning", {}).get("enabled") is False \
        or "reasoning" not in sent.get("extra_body", {})
```

Add to `tests/test_engine_subagent.py`:
```python
def test_subagent_engine_embeds_reasoning_effort():
    with patch("lib.engines.subagent.subprocess.run") as mock_run:
        mock_run.return_value = _fake_run_ok({"label": "x", "score": 1})
        engine = subagent_engine()
        engine(system="s", user="u", schema=_Out, reasoning_effort=8)
    prompt = mock_run.call_args[0][0][2]  # third arg = prompt text
    assert "reasoning effort" in prompt.lower() or "8" in prompt
```

Add to `tests/test_prompt_filter_urls.py`:
```python
def test_filter_urls_has_reasoning_effort():
    cfg = get_prompt("filter_urls")
    assert cfg.reasoning_effort == 4
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd /Users/drucev/projects/news_agent
.venv/bin/pytest tests/test_llm.py tests/test_engine_openrouter.py tests/test_engine_subagent.py tests/test_prompt_filter_urls.py -v
```
Expected: new tests fail (AttributeError or assertion error).

- [ ] **Step 3: Update `lib/llm.py`**

Add field to `PromptConfig`:
```python
@dataclass(frozen=True)
class PromptConfig:
    name: str
    system_prompt: str
    user_prompt: str
    input_schema: Type[BaseModel]
    output_schema: Type[BaseModel]
    default_engine: Optional[str]
    reasoning_effort: int = 4  # 0-10 scale
```

Update `call_prompt` to pass `reasoning_effort` to the engine:
```python
def call_prompt(
    prompt_name: str,
    inputs: Union[Dict[str, Any], BaseModel],
    *,
    engine: Optional[str] = None,
) -> BaseModel:
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
    return eng(
        system=cfg.system_prompt,
        user=user_str,
        schema=cfg.output_schema,
        reasoning_effort=cfg.reasoning_effort,
    )
```

- [ ] **Step 4: Update `lib/engines/openrouter.py`**

Add `reasoning_effort: int = 4` to the inner `_call` signature. Map to `extra_body.reasoning`:
```python
def _call(system: str, user: str, schema: Type[BaseModel],
          reasoning_effort: int = 4) -> BaseModel:
    ...
    extra_body: dict = {"provider": {"sort": "latency", "require_parameters": True}}
    if reasoning_effort >= 1:
        if reasoning_effort <= 3:
            level = "low"
        elif reasoning_effort <= 6:
            level = "medium"
        else:
            level = "high"
        extra_body["reasoning"] = {"enabled": True, "effort": level}
    else:
        extra_body["reasoning"] = {"enabled": False}

    body = {
        "model": model_id,
        "messages": [...],
        "response_format": {...},
        "extra_body": extra_body,
    }
    ...
```

Note: legacy puts `provider` and `reasoning` inside `extra_body`. OpenRouter accepts both top-level and inside `extra_body`. Stick with `extra_body` for consistency with legacy.

- [ ] **Step 5: Update `lib/engines/subagent.py`**

Add `reasoning_effort: int = 4` to `_call`. Update `_build_prompt`:
```python
def _build_prompt(system: str, user: str, schema: Type[BaseModel],
                  reasoning_effort: int = 4) -> str:
    schema_json = json.dumps(schema.model_json_schema(), indent=2)
    return (
        f"{system}\n\n"
        f"Reasoning effort: {reasoning_effort}/10 "
        f"(0=trivial, 4=moderate, 8=heavy)\n\n"
        f"User input:\n{user}\n\n"
        f"You MUST respond with valid JSON matching this exact schema. "
        f"No prose, no markdown fences — just the JSON object.\n\n"
        f"JSON Schema:\n{schema_json}"
    )
```

- [ ] **Step 6: Update `lib/prompts/filter_urls.py`**

Add `reasoning_effort=4` to the `PromptConfig(...)` call:
```python
FILTER_URLS = PromptConfig(
    name="filter_urls",
    system_prompt=_SYSTEM,
    user_prompt=_USER,
    input_schema=FilterUrlsInput,
    output_schema=FilterUrlsOutput,
    default_engine="subagent",
    reasoning_effort=4,
)
```

- [ ] **Step 7: Run tests, commit**

```bash
.venv/bin/pytest tests/ -q
# Expect all prior tests still pass + new ones pass
git add lib/llm.py lib/engines/openrouter.py lib/engines/subagent.py lib/prompts/filter_urls.py tests/
git commit -m "feat(llm): PromptConfig.reasoning_effort threaded through engines"
```

---

## Task 2: Add openai + google + choix + numpy/scipy deps

**Files:** Modify `pyproject.toml`, `requirements.txt`.

- [ ] **Step 1: Update deps**

In `pyproject.toml` `dependencies`:
```toml
  "openai>=1.50",
  "google-genai>=0.4",
  "choix>=0.3",
  "numpy>=1.26",
  "scipy>=1.13",
```

Mirror into `requirements.txt`.

- [ ] **Step 2: Install + verify**

```bash
cd /Users/drucev/projects/news_agent
.venv/bin/pip install -e ".[dev]"
.venv/bin/python -c "import openai, google.genai, choix, numpy, scipy; print('OK')"
```

- [ ] **Step 3: Commit**

```bash
git add pyproject.toml requirements.txt
git commit -m "chore: add openai, google-genai, choix, numpy, scipy"
```

---

## Task 3: `lib/engines/openai_chat.py` — OpenAI direct engine

**Files:**
- Create: `lib/engines/openai_chat.py`
- Create: `tests/test_engine_openai_chat.py`

Uses `openai` SDK with `response_format={"type": "json_schema", "json_schema": {...}}`. Newer models (gpt-4o, gpt-5-mini) support structured outputs; if not, fall back to `{"type": "json_object"}`.

`reasoning_effort` is sent as a top-level `reasoning_effort` param (string: "minimal"/"low"/"medium"/"high"). The SDK silently ignores it for non-reasoning models.

- [ ] **Step 1: Write tests (mock the openai client)**

```python
# tests/test_engine_openai_chat.py
import json
from unittest.mock import patch, MagicMock
import pytest
from pydantic import BaseModel
from lib.engines.openai_chat import openai_chat_engine
from lib.engines.base import EngineError


class _Out(BaseModel):
    answer: str


def _fake_completion(content: str):
    msg = MagicMock()
    msg.content = content
    choice = MagicMock()
    choice.message = msg
    resp = MagicMock()
    resp.choices = [choice]
    return resp


def test_openai_chat_engine_sends_correct_body(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    fake_client = MagicMock()
    fake_client.chat.completions.create.return_value = _fake_completion('{"answer": "yes"}')
    with patch("lib.engines.openai_chat.OpenAI", return_value=fake_client):
        engine = openai_chat_engine("gpt-4o-mini")
        result = engine(system="be helpful", user="hi", schema=_Out, reasoning_effort=6)
    assert isinstance(result, _Out) and result.answer == "yes"
    kwargs = fake_client.chat.completions.create.call_args.kwargs
    assert kwargs["model"] == "gpt-4o-mini"
    assert kwargs["messages"] == [
        {"role": "system", "content": "be helpful"},
        {"role": "user", "content": "hi"},
    ]
    assert kwargs["response_format"]["type"] == "json_schema"
    assert kwargs.get("reasoning_effort") == "medium"


def test_openai_chat_engine_maps_effort_levels(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    fake_client = MagicMock()
    fake_client.chat.completions.create.return_value = _fake_completion('{"answer": "x"}')
    with patch("lib.engines.openai_chat.OpenAI", return_value=fake_client):
        engine = openai_chat_engine("gpt-5-mini")
        for effort, level in [(0, "minimal"), (2, "low"), (5, "medium"), (9, "high")]:
            fake_client.chat.completions.create.reset_mock()
            engine(system="s", user="u", schema=_Out, reasoning_effort=effort)
            kwargs = fake_client.chat.completions.create.call_args.kwargs
            assert kwargs.get("reasoning_effort") == level


def test_openai_chat_engine_requires_api_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(EngineError, match="OPENAI_API_KEY"):
        openai_chat_engine("gpt-4o-mini")(system="s", user="u", schema=_Out)


def test_openai_chat_engine_raises_on_unparseable(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    fake_client = MagicMock()
    fake_client.chat.completions.create.return_value = _fake_completion("not json")
    with patch("lib.engines.openai_chat.OpenAI", return_value=fake_client):
        engine = openai_chat_engine("gpt-4o-mini")
        with pytest.raises(EngineError, match="parse"):
            engine(system="s", user="u", schema=_Out)
```

- [ ] **Step 2: Implement `lib/engines/openai_chat.py`**

```python
"""OpenAI direct chat completions engine."""
from __future__ import annotations

import json
import os
from typing import Callable, Type

from openai import OpenAI
from pydantic import BaseModel, ValidationError

from lib.engines.base import EngineError


def _effort_level(effort: int) -> str:
    if effort <= 0:
        return "minimal"
    if effort <= 3:
        return "low"
    if effort <= 6:
        return "medium"
    return "high"


def openai_chat_engine(model_id: str) -> Callable[..., BaseModel]:
    def _call(system: str, user: str, schema: Type[BaseModel],
              reasoning_effort: int = 4) -> BaseModel:
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise EngineError("OPENAI_API_KEY not set in environment")

        client = OpenAI(api_key=api_key)

        kwargs = {
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
            "reasoning_effort": _effort_level(reasoning_effort),
        }

        try:
            resp = client.chat.completions.create(**kwargs)
        except Exception as exc:
            # SDK raises on unsupported params; retry once without reasoning_effort
            if "reasoning_effort" in str(exc):
                kwargs.pop("reasoning_effort", None)
                try:
                    resp = client.chat.completions.create(**kwargs)
                except Exception as inner:
                    raise EngineError(f"OpenAI API error: {inner}") from inner
            else:
                raise EngineError(f"OpenAI API error: {exc}") from exc

        try:
            content = resp.choices[0].message.content or ""
            parsed = json.loads(content)
        except (IndexError, AttributeError, json.JSONDecodeError) as exc:
            raise EngineError(f"Failed to parse OpenAI response: {exc}") from exc

        try:
            return schema.model_validate(parsed)
        except ValidationError as exc:
            raise EngineError(f"Response failed schema validation: {exc}") from exc

    return _call
```

- [ ] **Step 3: Run tests, commit**

```bash
.venv/bin/pytest tests/test_engine_openai_chat.py -v
# 4 passed
git add lib/engines/openai_chat.py tests/test_engine_openai_chat.py
git commit -m "feat(engines): OpenAI direct chat completions engine"
```

---

## Task 4: `lib/engines/google.py` — Google Gemini engine

**Files:**
- Create: `lib/engines/google.py`
- Create: `tests/test_engine_google.py`

Uses `google-genai` SDK. `response_mime_type="application/json"` + `response_schema=<pydantic_class>` for structured output. `reasoning_effort` → `thinking_config.thinking_budget`.

- [ ] **Step 1: Write tests**

```python
# tests/test_engine_google.py
from unittest.mock import patch, MagicMock
import pytest
from pydantic import BaseModel
from lib.engines.google import google_engine
from lib.engines.base import EngineError


class _Out(BaseModel):
    answer: str


def _fake_resp(text: str):
    r = MagicMock()
    r.text = text
    return r


def test_google_engine_sends_request(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "g-test")
    fake_client = MagicMock()
    fake_client.models.generate_content.return_value = _fake_resp('{"answer": "yes"}')
    with patch("lib.engines.google.genai.Client", return_value=fake_client):
        engine = google_engine("gemini-2.5-flash")
        result = engine(system="be helpful", user="hi", schema=_Out, reasoning_effort=6)
    assert result.answer == "yes"
    kwargs = fake_client.models.generate_content.call_args.kwargs
    assert kwargs["model"] == "gemini-2.5-flash"


def test_google_engine_requires_api_key(monkeypatch):
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    with pytest.raises(EngineError, match="GOOGLE_API_KEY"):
        google_engine("gemini-2.5-flash")(system="s", user="u", schema=_Out)


def test_google_engine_raises_on_unparseable(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "g-test")
    fake_client = MagicMock()
    fake_client.models.generate_content.return_value = _fake_resp("not json")
    with patch("lib.engines.google.genai.Client", return_value=fake_client):
        engine = google_engine("gemini-2.5-flash")
        with pytest.raises(EngineError, match="parse"):
            engine(system="s", user="u", schema=_Out)
```

- [ ] **Step 2: Implement `lib/engines/google.py`**

```python
"""Google Gemini engine via google-genai SDK."""
from __future__ import annotations

import json
import os
from typing import Callable, Type

from google import genai
from google.genai import types as genai_types
from pydantic import BaseModel, ValidationError

from lib.engines.base import EngineError


def _thinking_budget(effort: int) -> int:
    """Map 0-10 reasoning_effort to Gemini thinking_budget (-1 = dynamic, 0 = off)."""
    if effort <= 0:
        return 0
    if effort <= 3:
        return 2048
    if effort <= 6:
        return 8192
    return 24576


def google_engine(model_id: str) -> Callable[..., BaseModel]:
    def _call(system: str, user: str, schema: Type[BaseModel],
              reasoning_effort: int = 4) -> BaseModel:
        api_key = os.environ.get("GOOGLE_API_KEY")
        if not api_key:
            raise EngineError("GOOGLE_API_KEY not set in environment")

        client = genai.Client(api_key=api_key)

        config = genai_types.GenerateContentConfig(
            system_instruction=system,
            response_mime_type="application/json",
            response_schema=schema,
            thinking_config=genai_types.ThinkingConfig(
                thinking_budget=_thinking_budget(reasoning_effort)
            ),
        )

        try:
            resp = client.models.generate_content(
                model=model_id,
                contents=user,
                config=config,
            )
        except Exception as exc:
            raise EngineError(f"Google API error: {exc}") from exc

        try:
            text = resp.text or ""
            parsed = json.loads(text)
        except (json.JSONDecodeError, AttributeError) as exc:
            raise EngineError(f"Failed to parse Google response: {exc}") from exc

        try:
            return schema.model_validate(parsed)
        except ValidationError as exc:
            raise EngineError(f"Response failed schema validation: {exc}") from exc

    return _call
```

- [ ] **Step 3: Tests, commit**

```bash
.venv/bin/pytest tests/test_engine_google.py -v
# 3 passed
git add lib/engines/google.py tests/test_engine_google.py
git commit -m "feat(engines): Google Gemini engine"
```

---

## Task 5: Engine factory updates

**Files:**
- Modify: `lib/engines/__init__.py`
- Modify: `tests/test_engine_factory.py`

- [ ] **Step 1: Add factory cases**

In `lib/engines/__init__.py`:
```python
from lib.engines.openai_chat import openai_chat_engine
from lib.engines.google import google_engine


def get_engine(identifier: str):
    if identifier == "subagent":
        return subagent_engine()
    if identifier.startswith("openrouter:"):
        model_id = identifier[len("openrouter:"):]
        if not model_id:
            raise EngineError("openrouter engine requires a model id")
        return openrouter_engine(model_id)
    if identifier.startswith("openai:"):
        model_id = identifier[len("openai:"):]
        if not model_id:
            raise EngineError("openai engine requires a model id")
        return openai_chat_engine(model_id)
    if identifier.startswith("google:"):
        model_id = identifier[len("google:"):]
        if not model_id:
            raise EngineError("google engine requires a model id")
        return google_engine(model_id)
    raise EngineError(f"Unknown engine identifier: {identifier!r}")
```

- [ ] **Step 2: Add factory tests**

Append to `tests/test_engine_factory.py`:
```python
def test_get_engine_openai_with_model():
    assert callable(get_engine("openai:gpt-4o-mini"))


def test_get_engine_openai_requires_model():
    with pytest.raises(EngineError, match="model id"):
        get_engine("openai:")


def test_get_engine_google_with_model():
    assert callable(get_engine("google:gemini-2.5-flash"))


def test_get_engine_anthropic_still_unknown():
    """Anthropic direct is explicitly NOT supported — must error."""
    with pytest.raises(EngineError, match="Unknown"):
        get_engine("anthropic:claude-opus-4-7")
```

- [ ] **Step 3: Tests, commit**

```bash
.venv/bin/pytest tests/test_engine_factory.py -v
# 8 passed
git add lib/engines/__init__.py tests/test_engine_factory.py
git commit -m "feat(engines): factory supports openai: and google: identifiers"
```

---

## Task 6: `lib/embeddings.py` — OpenAI batched embeddings

**Files:**
- Create: `lib/embeddings.py`
- Create: `tests/test_embeddings.py`

API: `embed_texts(texts: list[str]) -> list[list[float]]`. Batched (OpenAI accepts up to 2048 inputs per call). Default model `text-embedding-3-large`. Uses `OPENAI_API_KEY`.

- [ ] **Step 1: Tests**

```python
# tests/test_embeddings.py
from unittest.mock import patch, MagicMock
import pytest
from lib.embeddings import embed_texts
from lib.engines.base import EngineError


def _fake_resp(vectors):
    return MagicMock(data=[MagicMock(embedding=v) for v in vectors])


def test_embed_texts_returns_vectors(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    fake = MagicMock()
    fake.embeddings.create.return_value = _fake_resp([[0.1, 0.2], [0.3, 0.4]])
    with patch("lib.embeddings.OpenAI", return_value=fake):
        result = embed_texts(["hello", "world"])
    assert result == [[0.1, 0.2], [0.3, 0.4]]
    kwargs = fake.embeddings.create.call_args.kwargs
    assert kwargs["model"] == "text-embedding-3-large"


def test_embed_texts_empty():
    assert embed_texts([]) == []


def test_embed_texts_batched(monkeypatch):
    """When inputs > BATCH_SIZE, multiple API calls are made and results stitched in order."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    fake = MagicMock()
    # Two calls, each returning 1 vector
    fake.embeddings.create.side_effect = [
        _fake_resp([[0.1]]),
        _fake_resp([[0.2]]),
    ]
    with patch("lib.embeddings.OpenAI", return_value=fake):
        with patch("lib.embeddings._BATCH_SIZE", 1):
            result = embed_texts(["a", "b"])
    assert result == [[0.1], [0.2]]
    assert fake.embeddings.create.call_count == 2


def test_embed_texts_requires_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(EngineError, match="OPENAI_API_KEY"):
        embed_texts(["x"])
```

- [ ] **Step 2: Implement**

```python
# lib/embeddings.py
"""OpenAI text-embedding-3-large helper for news_agent.

Used by dedup (cosine similarity), cluster (UMAP+HDBSCAN), and select (MMR).
Matches the legacy ~/projects/OpenAIAgentsSDK/ workflow so the existing
umap_reducer.pkl can be reused as-is.
"""
from __future__ import annotations

import os
from typing import List

from openai import OpenAI

from lib.engines.base import EngineError


_DEFAULT_MODEL = "text-embedding-3-large"
_BATCH_SIZE = 256


def embed_texts(
    texts: List[str],
    model: str = _DEFAULT_MODEL,
) -> List[List[float]]:
    """Return embeddings for each input text, preserving order."""
    if not texts:
        return []
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise EngineError("OPENAI_API_KEY not set in environment")

    client = OpenAI(api_key=api_key)
    out: List[List[float]] = []
    for i in range(0, len(texts), _BATCH_SIZE):
        chunk = texts[i:i + _BATCH_SIZE]
        try:
            resp = client.embeddings.create(model=model, input=chunk)
        except Exception as exc:
            raise EngineError(f"OpenAI embeddings error: {exc}") from exc
        out.extend([d.embedding for d in resp.data])
    return out
```

- [ ] **Step 3: Tests, commit**

```bash
.venv/bin/pytest tests/test_embeddings.py -v
# 4 passed
git add lib/embeddings.py tests/test_embeddings.py
git commit -m "feat(embeddings): OpenAI text-embedding-3-large batched helper"
```

---

## Task 7: `news:filter` step + SKILL.md

**Files:**
- Create: `lib/steps/filter.py`
- Create: `skills/filter/SKILL.md`
- Create: `tests/test_step_filter.py`

Logic:
1. Load state. For each headline in `headline_data` without `is_ai` set, batch and call `FILTER_URLS` via `call_prompt_batch` (chunks of `BATCH_N=50` headlines per call, `parallelism=2` to keep subagent latency reasonable).
2. Write `is_ai` to each headline dict AND to `urls.isAI` column (so cross-session dedup uses it).
3. Optionally drop non-AI headlines from state (controlled by `--keep-non-ai` flag; default drops them).
4. Write `runs/<SID>/filter.json`.

- [ ] **Step 1: Tests**

```python
# tests/test_step_filter.py
from unittest.mock import patch
from click.testing import CliRunner
import lib.prompts  # register FILTER_URLS
from lib.db import init_db
from lib.state import NewsletterAgentState
from lib.steps.filter import cli as filter_cli
from lib.prompts.filter_urls import FilterUrlsOutput, HeadlineClassification


def _seed(tmp_db):
    init_db(tmp_db)
    state = NewsletterAgentState(session_id="f1", db_path=tmp_db)
    state.complete_step("init")
    state.complete_step("gather")
    state.headline_data = [
        {"source": "S", "title": "OpenAI ships GPT-6", "url": "https://e.com/a"},
        {"source": "S", "title": "Stock market roundup", "url": "https://e.com/b"},
    ]
    state.save_checkpoint("gather")


def test_filter_marks_is_ai_via_prompt(tmp_db, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    _seed(tmp_db)

    def fake_call_prompt(name, inputs, *, engine=None):
        assert name == "filter_urls"
        # inputs is a dict that conforms to FilterUrlsInput
        ids = [it["id"] for it in inputs["items"]]
        return FilterUrlsOutput(classifications=[
            HeadlineClassification(id=ids[0], is_ai=True),
            HeadlineClassification(id=ids[1], is_ai=False),
        ])

    with patch("lib.steps.filter.call_prompt", side_effect=fake_call_prompt):
        runner = CliRunner()
        result = runner.invoke(filter_cli, ["--db", tmp_db, "--session", "f1", "--keep-non-ai"])
    assert result.exit_code == 0, result.output

    state = NewsletterAgentState(session_id="f1", db_path=tmp_db).load_latest_from_db()
    flags = [h["is_ai"] for h in state.headline_data]
    assert flags == [True, False]


def test_filter_drops_non_ai_by_default(tmp_db, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    _seed(tmp_db)

    def fake_call_prompt(name, inputs, *, engine=None):
        ids = [it["id"] for it in inputs["items"]]
        return FilterUrlsOutput(classifications=[
            HeadlineClassification(id=ids[0], is_ai=True),
            HeadlineClassification(id=ids[1], is_ai=False),
        ])

    with patch("lib.steps.filter.call_prompt", side_effect=fake_call_prompt):
        runner = CliRunner()
        runner.invoke(filter_cli, ["--db", tmp_db, "--session", "f1"])

    state = NewsletterAgentState(session_id="f1", db_path=tmp_db).load_latest_from_db()
    assert len(state.headline_data) == 1
    assert state.headline_data[0]["is_ai"] is True


def test_filter_writes_report(tmp_db, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    _seed(tmp_db)

    def fake_call_prompt(name, inputs, *, engine=None):
        ids = [it["id"] for it in inputs["items"]]
        return FilterUrlsOutput(classifications=[
            HeadlineClassification(id=i, is_ai=True) for i in ids
        ])

    with patch("lib.steps.filter.call_prompt", side_effect=fake_call_prompt):
        runner = CliRunner()
        runner.invoke(filter_cli, ["--db", tmp_db, "--session", "f1"])

    import json
    from pathlib import Path
    report = json.loads(Path("runs/f1/filter.json").read_text())
    assert report["total"] == 2
    assert report["ai"] == 2
```

- [ ] **Step 2: Implement**

```python
# lib/steps/filter.py
"""news:filter — classify headlines as AI-relevant."""
from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

import click

import lib.prompts  # noqa: F401 — register FILTER_URLS
from lib.llm import call_prompt
from lib.state import NewsletterAgentState


_BATCH = 50


@click.command()
@click.option("--db", "db_path", default="newsletter_agent.db")
@click.option("--session", "session_id", required=True)
@click.option("--keep-non-ai", is_flag=True, help="Keep non-AI headlines in state (default: drop)")
@click.option("--engine", default=None, help="Override engine (e.g. openrouter:google/gemini-2.5-flash)")
def cli(db_path: str, session_id: str, keep_non_ai: bool, engine: str | None) -> None:
    state = NewsletterAgentState(session_id=session_id, db_path=db_path).load_latest_from_db()
    if state is None:
        raise click.ClickException(f"No state found for session {session_id}")

    state.start_step("filter")
    state.save_checkpoint("filter")

    # Each headline gets a stable index id for prompt round-trip
    items = [{"id": str(i), "title": h["title"]}
             for i, h in enumerate(state.headline_data) if "is_ai" not in h]
    if not items:
        state.complete_step("filter", message="nothing to filter")
        state.save_checkpoint("filter")
        click.echo("Nothing to filter.")
        return

    classifications: dict[str, bool] = {}
    for i in range(0, len(items), _BATCH):
        batch = items[i:i + _BATCH]
        result = call_prompt("filter_urls", {"items": batch}, engine=engine)
        for c in result.classifications:
            classifications[c.id] = c.is_ai

    # Apply results
    kept: list[dict] = []
    ai_count = 0
    for i, h in enumerate(state.headline_data):
        key = str(i)
        if key in classifications:
            h["is_ai"] = classifications[key]
        is_ai = h.get("is_ai", False)
        if is_ai:
            ai_count += 1
        if is_ai or keep_non_ai or "is_ai" not in h:
            kept.append(h)
    state.headline_data = kept

    # Update urls.isAI for cross-session dedup
    with sqlite3.connect(db_path) as conn:
        for h in state.headline_data:
            if "is_ai" in h:
                conn.execute(
                    "UPDATE urls SET isAI=? WHERE initial_url=?",
                    (1 if h["is_ai"] else 0, h["url"]),
                )
        conn.commit()

    state.complete_step("filter", message=f"{ai_count}/{len(items)} AI-relevant")
    state.save_checkpoint("filter")

    runs_dir = Path("runs") / session_id
    runs_dir.mkdir(parents=True, exist_ok=True)
    (runs_dir / "filter.json").write_text(json.dumps({
        "session_id": session_id,
        "completed_at": datetime.now().isoformat(),
        "total": len(items),
        "ai": ai_count,
        "kept": len(state.headline_data),
    }, indent=2))

    click.echo(f"Filtered: {ai_count}/{len(items)} AI-relevant, {len(state.headline_data)} kept.")


if __name__ == "__main__":  # pragma: no cover
    sys.exit(cli())
```

- [ ] **Step 3: SKILL.md**

```markdown
---
name: news:filter
description: Classify each gathered headline as AI-related using the filter_urls prompt. Default drops non-AI headlines from session state and updates urls.isAI in the DB so future runs dedup against this signal. Pass --keep-non-ai to retain them for inspection.
---

# news:filter

Step 3 of /news:run.

## How to invoke

python -m lib.steps.filter --db newsletter_agent.db --session SID [--keep-non-ai] [--engine ENGINE]

## Behavior

- Batches up to 50 headlines per LLM call (FILTER_URLS prompt).
- Default engine: subagent. Override via --engine or NEWS_PROMPT_FILTER_URLS_ENGINE.
- Writes runs/<SID>/filter.json with counts.
```

- [ ] **Step 4: Tests, commit**

```bash
.venv/bin/pytest tests/test_step_filter.py -v
# 3 passed
git add lib/steps/filter.py skills/filter/SKILL.md tests/test_step_filter.py
git commit -m "feat(steps): news:filter classifies headlines via FILTER_URLS prompt"
```

---

## Task 8: Port `EXTRACT_SUMMARIES` PromptConfig

**Files:**
- Create: `lib/prompts/extract_summaries.py`
- Modify: `lib/prompts/__init__.py`
- Create: `tests/test_prompt_extract_summaries.py`

Schema: input is `{items: [{id, title, text}]}`, output is `{summaries: [{id, summary}]}`.

- [ ] **Step 1: Tests** (similar shape to `test_prompt_filter_urls.py` — registration, schemas, format).

```python
# tests/test_prompt_extract_summaries.py
import importlib
import pytest
from lib.llm import get_prompt, _registry


@pytest.fixture(autouse=True)
def _ensure_registered():
    if "extract_summaries" not in _registry:
        import lib.prompts.extract_summaries
        importlib.reload(lib.prompts.extract_summaries)
    yield


def test_extract_summaries_registered():
    cfg = get_prompt("extract_summaries")
    assert cfg.default_engine == "subagent"
    assert cfg.reasoning_effort == 6  # legacy value


def test_extract_summaries_input_accepts_articles():
    cfg = get_prompt("extract_summaries")
    parsed = cfg.input_schema.model_validate({
        "items": [{"id": "h1", "title": "T", "text": "Article body here..."}]
    })
    assert parsed.items[0].text == "Article body here..."


def test_extract_summaries_output_round_trips():
    cfg = get_prompt("extract_summaries")
    out = cfg.output_schema.model_validate({
        "summaries": [{"id": "h1", "summary": "- bullet one\n- bullet two\n- bullet three"}]
    })
    assert len(out.summaries) == 1
```

- [ ] **Step 2: Implement** (port prompt text verbatim from `~/projects/OpenAIAgentsSDK/prompts.py:284-324`):

```python
# lib/prompts/extract_summaries.py
"""extract_summaries — bullet-point article summaries.

Ported from ~/projects/OpenAIAgentsSDK/prompts.py:284 (EXTRACT_SUMMARIES).
"""
from __future__ import annotations

import json
from typing import List

from pydantic import BaseModel, Field, computed_field

from lib.llm import PromptConfig, register_prompt


class ArticleItem(BaseModel):
    id: str
    title: str
    text: str


class ExtractSummariesInput(BaseModel):
    items: List[ArticleItem] = Field(min_length=1)

    @computed_field
    @property
    def input_text(self) -> str:
        return json.dumps([item.model_dump() for item in self.items])


class ArticleSummary(BaseModel):
    id: str
    summary: str


class ExtractSummariesOutput(BaseModel):
    summaries: List[ArticleSummary]


# Verbatim from legacy prompts.py:292-324
_SYSTEM = """\
You are an expert AI news analyst. Your task is to create concise, informative bullet-point summaries of AI and technology articles for a professional newsletter audience.

You will receive a list of JSON objects with fields \"id\" and \"title\"
Return **only** a JSON object that satisfies the provided schema.
For each article provided, you MUST return one element with the same id, and the summary.
Return elements in the same order they were provided.
No markdown, no markdown fences, no extra keys, no comments.

Write a summary with 3 bullet points (-) that capture ONLY the newsworthy content.

Include
- Key facts & technological developments
- Business implications and market impact
- Future outlook and expert predictions
- Practical applications and use cases
- Key quotes
- Essential background tied directly to the story

Exclude
- Navigation/UI text, ads, paywalls, cookie banners, JS, legal/footer copy, \"About us\", social widgets

Rules
- Accurately summarize original meaning
- Contents only, no additional commentary or opinion, no \"the article discusses\", \"the author states\"
- Maintain factual & neutral tone
- If no substantive news, return one bullet: \"no content\"
- Output raw bullets (no code fences, no headings, no extra text--only the bullet strings)"""

_USER = """\
Summarize the articles below:

{input_text}"""


EXTRACT_SUMMARIES = PromptConfig(
    name="extract_summaries",
    system_prompt=_SYSTEM,
    user_prompt=_USER,
    input_schema=ExtractSummariesInput,
    output_schema=ExtractSummariesOutput,
    default_engine="subagent",
    reasoning_effort=6,
)

register_prompt(EXTRACT_SUMMARIES)
```

Update `lib/prompts/__init__.py`:
```python
from lib.prompts import filter_urls, extract_summaries  # noqa: F401
__all__ = ["filter_urls", "extract_summaries"]
```

- [ ] **Step 3: Tests, commit**

```bash
.venv/bin/pytest tests/test_prompt_extract_summaries.py tests/test_prompt_filter_urls.py -v
# 3 + 6 passed
git add lib/prompts/extract_summaries.py lib/prompts/__init__.py tests/test_prompt_extract_summaries.py
git commit -m "feat(prompts): port EXTRACT_SUMMARIES"
```

---

## Task 9: `news:summarize` step + SKILL.md

**Files:**
- Create: `lib/steps/summarize.py`
- Create: `skills/summarize/SKILL.md`
- Create: `tests/test_step_summarize.py`

Logic: for each headline with `text_path` set (downloaded) but no `summary`, read the text, batch into `EXTRACT_SUMMARIES`, write `summary` back to `headline_data[i]`. Skip headlines without `text_path` (they failed download).

Same pattern as filter step. `_BATCH = 10` (summaries are longer per item; smaller batch).

Test pattern: mock `call_prompt`, seed state with `text_path` pointing to a temp file, run, assert `summary` is set.

- [ ] **Step 1: Tests** — mirror `test_step_filter.py` structure. Three tests: writes summaries, skips no-text-path, writes report.

- [ ] **Step 2: Implement** — copy `lib/steps/filter.py` structure; swap `FILTER_URLS` → `EXTRACT_SUMMARIES`, swap batch size to 10, swap input field from `title` to `{id, title, text}` where text is read from disk.

- [ ] **Step 3: SKILL.md** — short.

- [ ] **Step 4: Tests, commit**

```bash
.venv/bin/pytest tests/test_step_summarize.py -v
# 3 passed
git add lib/steps/summarize.py skills/summarize/SKILL.md tests/test_step_summarize.py
git commit -m "feat(steps): news:summarize via EXTRACT_SUMMARIES"
```

---

## Task 10: `news:dedupe` step + SKILL.md

**Files:**
- Create: `lib/steps/dedupe.py`
- Create: `skills/dedupe/SKILL.md`
- Create: `tests/test_step_dedupe.py`

Logic:
1. Load state. For each headline with `summary` set, build the embedding input text (concat title + summary).
2. Embed via `embed_texts` (OpenAI text-embedding-3-large).
3. Compute pairwise cosine similarity (numpy).
4. For pairs with `sim >= 0.95`: keep the one with longer text, drop the other. (Legacy logic.)
5. Store the surviving embeddings on `state.headline_data[i]["embedding"]` so cluster/select can reuse.

Tests: mock `embed_texts` to return fixed vectors; verify dedup logic and embedding preservation.

- [ ] **Step 1: Tests**

```python
# tests/test_step_dedupe.py
from unittest.mock import patch
from click.testing import CliRunner
from lib.db import init_db
from lib.state import NewsletterAgentState
from lib.steps.dedupe import cli as dedupe_cli


def _seed(tmp_db):
    init_db(tmp_db)
    state = NewsletterAgentState(session_id="d1", db_path=tmp_db)
    state.complete_step("init")
    state.complete_step("gather")
    state.complete_step("filter")
    state.complete_step("download")
    state.complete_step("summarize")
    state.headline_data = [
        {"id": 0, "title": "T1", "url": "https://e.com/a", "summary": "alpha beta gamma"},
        {"id": 1, "title": "T1b", "url": "https://e.com/b", "summary": "alpha beta gamma"},  # near-dupe
        {"id": 2, "title": "T2", "url": "https://e.com/c", "summary": "completely different topic"},
    ]
    state.save_checkpoint("summarize")


def test_dedupe_drops_near_duplicates(tmp_db, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    _seed(tmp_db)
    # First two are identical vectors; third is orthogonal
    vectors = [[1.0, 0.0], [1.0, 0.0], [0.0, 1.0]]
    with patch("lib.steps.dedupe.embed_texts", return_value=vectors):
        runner = CliRunner()
        result = runner.invoke(dedupe_cli, ["--db", tmp_db, "--session", "d1"])
    assert result.exit_code == 0, result.output
    state = NewsletterAgentState(session_id="d1", db_path=tmp_db).load_latest_from_db()
    urls = [h["url"] for h in state.headline_data]
    assert len(urls) == 2
    assert "https://e.com/c" in urls


def test_dedupe_preserves_embeddings(tmp_db, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    _seed(tmp_db)
    vectors = [[1.0, 0.0], [0.5, 0.5], [0.0, 1.0]]  # none near-duplicate
    with patch("lib.steps.dedupe.embed_texts", return_value=vectors):
        runner = CliRunner()
        runner.invoke(dedupe_cli, ["--db", tmp_db, "--session", "d1"])
    state = NewsletterAgentState(session_id="d1", db_path=tmp_db).load_latest_from_db()
    assert all("embedding" in h for h in state.headline_data)
    assert len(state.headline_data) == 3


def test_dedupe_no_summaries_noop(tmp_db, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    init_db(tmp_db)
    state = NewsletterAgentState(session_id="empty", db_path=tmp_db)
    state.complete_step("init")
    state.save_checkpoint("init")
    runner = CliRunner()
    result = runner.invoke(dedupe_cli, ["--db", tmp_db, "--session", "empty"])
    assert result.exit_code == 0
    assert "nothing" in result.output.lower() or "0" in result.output
```

- [ ] **Step 2: Implement**

```python
# lib/steps/dedupe.py
"""news:dedupe — cosine-similarity near-duplicate removal."""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import List

import click
import numpy as np

from lib.embeddings import embed_texts
from lib.state import NewsletterAgentState


_SIM_THRESHOLD = 0.95


def _cosine_matrix(vectors: List[List[float]]) -> np.ndarray:
    """Pairwise cosine similarity matrix."""
    if not vectors:
        return np.zeros((0, 0))
    M = np.array(vectors)
    norms = np.linalg.norm(M, axis=1, keepdims=True)
    norms = np.where(norms == 0, 1.0, norms)
    N = M / norms
    return N @ N.T


def _drop_near_duplicates(headlines: List[dict], sim: np.ndarray, threshold: float) -> List[int]:
    """Return indices to drop. Keep the longer-text headline of each near-duplicate pair."""
    n = len(headlines)
    drop: set[int] = set()
    for i in range(n):
        if i in drop:
            continue
        for j in range(i + 1, n):
            if j in drop:
                continue
            if sim[i, j] >= threshold:
                li = len(headlines[i].get("summary", ""))
                lj = len(headlines[j].get("summary", ""))
                # Keep the longer; if tied keep i
                drop.add(j if li >= lj else i)
                if i in drop:
                    break
    return sorted(drop)


@click.command()
@click.option("--db", "db_path", default="newsletter_agent.db")
@click.option("--session", "session_id", required=True)
@click.option("--threshold", default=_SIM_THRESHOLD, type=float)
def cli(db_path: str, session_id: str, threshold: float) -> None:
    state = NewsletterAgentState(session_id=session_id, db_path=db_path).load_latest_from_db()
    if state is None:
        raise click.ClickException(f"No state found for session {session_id}")

    candidates = [h for h in state.headline_data if h.get("summary")]
    if not candidates:
        click.echo("Nothing to dedupe (no summaries yet).")
        # NB: dedupe is part of the summarize/cluster pipeline; we don't have a workflow step "dedupe"
        # in the 11-step pipeline. Skip step transitions.
        return

    texts = [(h.get("title", "") + " " + h.get("summary", "")) for h in candidates]
    vectors = embed_texts(texts)

    # Attach embeddings to headlines
    for h, v in zip(candidates, vectors):
        h["embedding"] = v

    sim = _cosine_matrix(vectors)
    drop_indices = _drop_near_duplicates(candidates, sim, threshold)

    drop_urls = {candidates[i]["url"] for i in drop_indices}
    state.headline_data = [h for h in state.headline_data if h["url"] not in drop_urls]

    # Persist via a custom step (use existing 'download' checkpoint slot or skip — we'll save under 'download')
    # Better: just save the latest state under a fresh pseudo-step.
    state.serialize_to_db("dedupe")

    runs_dir = Path("runs") / session_id
    runs_dir.mkdir(parents=True, exist_ok=True)
    (runs_dir / "dedupe.json").write_text(json.dumps({
        "session_id": session_id,
        "completed_at": datetime.now().isoformat(),
        "total_candidates": len(candidates),
        "dropped": len(drop_indices),
        "kept": len(state.headline_data),
        "threshold": threshold,
    }, indent=2))

    click.echo(f"Dedupe: dropped {len(drop_indices)}/{len(candidates)} near-duplicates "
               f"(threshold={threshold}). {len(state.headline_data)} headlines remain.")


if __name__ == "__main__":  # pragma: no cover
    sys.exit(cli())
```

**Note on workflow step:** `dedupe` is NOT one of the 11 canonical workflow steps registered in `lib/state.WORKFLOW_STEPS`. It's a maintenance step run between summarize and rate. Persist its output via `state.serialize_to_db("dedupe")` rather than `complete_step` — this keeps the step list clean while still creating a checkpoint row in `agent_state`.

- [ ] **Step 3: SKILL.md, tests, commit**

```bash
.venv/bin/pytest tests/test_step_dedupe.py -v
# 3 passed
git add lib/steps/dedupe.py skills/dedupe/SKILL.md tests/test_step_dedupe.py
git commit -m "feat(steps): news:dedupe via cosine similarity on OpenAI embeddings"
```

---

## Task 11: Port `RATE_QUALITY`, `RATE_ON_TOPIC`, `RATE_IMPORTANCE` prompts

**Files:**
- Create: `lib/prompts/rate_quality.py`
- Create: `lib/prompts/rate_on_topic.py`
- Create: `lib/prompts/rate_importance.py`
- Modify: `lib/prompts/__init__.py`
- Create: `tests/test_prompt_rates.py`

All three have the **same input/output schema** (only the prompt text differs):
- Input: `{items: [{id, input_text}]}` where `input_text` is `title + summary`
- Output: `{results_list: [{id, confidence}]}` where confidence is 0.0–1.0

Build a shared schema in `lib/prompts/_rating_schemas.py`:
```python
# lib/prompts/_rating_schemas.py
import json
from typing import List
from pydantic import BaseModel, Field, computed_field


class RatingItem(BaseModel):
    id: str
    input_text: str


class RatingInput(BaseModel):
    items: List[RatingItem] = Field(min_length=1)

    @computed_field
    @property
    def input_text(self) -> str:
        return json.dumps([item.model_dump() for item in self.items])


class StoryConfidence(BaseModel):
    id: str
    confidence: float


class RatingOutput(BaseModel):
    results_list: List[StoryConfidence]
```

Three prompt files import this shared schema + paste in the legacy prompt text verbatim from `~/projects/OpenAIAgentsSDK/prompts.py:439-560` (`RATE_QUALITY`, `RATE_ON_TOPIC`, `RATE_IMPORTANCE`).

Each:
```python
RATE_QUALITY = PromptConfig(
    name="rate_quality",
    system_prompt=_SYSTEM,
    user_prompt=_USER,
    input_schema=RatingInput,
    output_schema=RatingOutput,
    default_engine="openai:gpt-4o-mini",  # legacy used gpt-4.1-mini; closest available
    reasoning_effort=4,
)
register_prompt(RATE_QUALITY)
```

Tests: one per prompt — registered, default engine set, system prompt has key phrases (e.g., quality has "low quality", on_topic has "AI news topics", importance has "importance factors").

- [ ] **Step 1: Tests**
- [ ] **Step 2: Implement three prompt files + shared schema**
- [ ] **Step 3: Tests, commit `feat(prompts): port RATE_QUALITY/ON_TOPIC/IMPORTANCE`**

---

## Task 12: Port `BATTLE_PROMPT` + Bradley-Terry rating module

**Files:**
- Create: `lib/prompts/battle.py`
- Create: `lib/rating.py`
- Create: `tests/test_prompt_battle.py`
- Create: `tests/test_rating.py`

### Battle prompt

Input: `{items: [{id, title, summary}]}` (typically 5–8 items per battle).
Output: `{ranking: [<id>, <id>, ...]}` — ids in order most-relevant to least-relevant.

Schema:
```python
class BattleItem(BaseModel):
    id: str
    title: str
    summary: str


class BattleInput(BaseModel):
    items: List[BattleItem] = Field(min_length=2)
    @computed_field
    @property
    def input_text(self) -> str:
        return json.dumps([i.model_dump() for i in self.items])


class BattleOutput(BaseModel):
    ranking: List[str]  # ids in order most→least relevant
```

Default engine: `"google:gemini-2.5-flash-lite"` (legacy used `gemini-3.1-flash-lite`). `reasoning_effort=2`.

Port prompt text verbatim from `~/projects/OpenAIAgentsSDK/prompts.py:562-610`.

### Bradley-Terry rating

`lib/rating.py` exports:
```python
def swiss_pairing(items: list[dict], battle_history: set[tuple[str, str]],
                  current_scores: dict[str, float]) -> list[tuple[str, str]]:
    """Generate Swiss-style pairings: sort by current score, pair adjacent unused, skip already-battled."""

def run_battles(items: list[dict], pairs: list[tuple[str, str]],
                items_per_battle: int = 6) -> list[tuple[str, str]]:
    """Group pairs into battles of ~6 items, call BATTLE_PROMPT for each, return (winner_id, loser_id) outcomes."""

def bradley_terry_scores(items: list[dict],
                         max_rounds: int | None = None,
                         conv_threshold: float = 0.005) -> dict[str, float]:
    """Run iterative Swiss-paired battles + choix.opt_pairwise. Returns {id: bt_score}."""
```

Port from `~/projects/OpenAIAgentsSDK/do_rating.py:73-372` but simplify:
- Use `lib.llm.call_prompt_batch` instead of legacy `LLMagent`
- Drop pandas dependency — use plain dicts/lists
- Drop async; threading via `call_prompt_batch` is enough
- Keep `choix.opt_pairwise` core math

### Tests (`tests/test_rating.py`)

No LLM calls — feed synthetic battle outcomes:
```python
import numpy as np
import pytest
from lib.rating import swiss_pairing, bradley_terry_from_battles


def test_swiss_pairing_pairs_adjacent_by_score():
    items = [{"id": "a"}, {"id": "b"}, {"id": "c"}, {"id": "d"}]
    scores = {"a": 4.0, "b": 3.0, "c": 2.0, "d": 1.0}
    pairs = swiss_pairing(items, battle_history=set(), current_scores=scores)
    assert pairs == [("a", "b"), ("c", "d")]


def test_swiss_pairing_skips_battled_pairs():
    items = [{"id": "a"}, {"id": "b"}, {"id": "c"}]
    scores = {"a": 3, "b": 2, "c": 1}
    history = {("a", "b"), ("b", "a")}
    pairs = swiss_pairing(items, battle_history=history, current_scores=scores)
    assert ("a", "b") not in pairs and ("b", "a") not in pairs


def test_bradley_terry_from_battles_ranks_winners_higher():
    items = [{"id": "a"}, {"id": "b"}, {"id": "c"}]
    # a beats b 5 times, a beats c 3 times, b beats c 2 times
    battles = [("a", "b")] * 5 + [("a", "c")] * 3 + [("b", "c")] * 2
    scores = bradley_terry_from_battles([i["id"] for i in items], battles)
    assert scores["a"] > scores["b"] > scores["c"]
```

`bradley_terry_from_battles(ids, battles)` is a thin wrapper around `choix.opt_pairwise` returning a dict keyed by id. Decompose `bradley_terry_scores()` so the test can target the math without LLM mocking.

- [ ] **Step 1: Tests** (battle prompt + rating math)
- [ ] **Step 2: Implement battle prompt + rating module**
- [ ] **Step 3: Tests, commit `feat(rating): Bradley-Terry Swiss-paired battle scoring`**

---

## Task 13: `news:rate` step + SKILL.md

**Files:**
- Create: `lib/steps/rate.py`
- Create: `skills/rate/SKILL.md`
- Create: `tests/test_step_rate.py`

Logic:
1. For each headline with a summary, build `input_text = f"{title}\n{summary}"`.
2. Run three confidence prompts in parallel (each batched): `rate_quality`, `rate_on_topic`, `rate_importance`. Store per-axis floats on each headline.
3. Run Bradley-Terry battles using `lib.rating.bradley_terry_scores`. Store `bt_z` (z-scored) on each headline.
4. Compute composite `rating = w_quality*(1-quality) + w_topic*on_topic + w_importance*importance + w_bt*bt_z`. Default weights from `lib/config.py` (create it with defaults: `RATING_WEIGHTS = {"quality": 0.2, "on_topic": 0.3, "importance": 0.3, "bt": 0.2}`).
5. Add recency bonus: `recency = exp(-hours_since_published / 48)` if `published` field is set. Final composite: `rating + 0.1 * recency + 0.05 * site_reputation` (looked up from sites table).
6. Write all signals to `headline_data[i]` for debugging. Write `runs/<SID>/rate.json` with composite distribution.

Tests: mock `call_prompt` and `bradley_terry_scores`. Verify composites are computed correctly given synthetic per-axis signals.

- [ ] **Step 1: Create `lib/config.py`** with default weights:
```python
# lib/config.py
RATING_WEIGHTS = {
    "quality_low": 0.2,
    "on_topic": 0.3,
    "importance": 0.3,
    "bt_z": 0.2,
}
RECENCY_BONUS = 0.1
SITE_REPUTATION_BONUS = 0.05
```

- [ ] **Step 2: Tests**

```python
# tests/test_step_rate.py
from unittest.mock import patch
from click.testing import CliRunner
from lib.db import init_db
from lib.state import NewsletterAgentState
from lib.steps.rate import cli as rate_cli
from lib.prompts._rating_schemas import RatingOutput, StoryConfidence


def _seed(tmp_db, n=3):
    init_db(tmp_db)
    state = NewsletterAgentState(session_id="r1", db_path=tmp_db)
    for s in ("init", "gather", "filter", "download", "summarize"):
        state.complete_step(s)
    state.headline_data = [
        {"id": i, "title": f"T{i}", "url": f"https://e.com/{i}",
         "summary": f"S{i}", "is_ai": True}
        for i in range(n)
    ]
    state.save_checkpoint("summarize")


def test_rate_writes_per_signal_and_composite(tmp_db, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    _seed(tmp_db, n=3)

    def fake_call_prompt(name, inputs, *, engine=None):
        ids = [it["id"] for it in inputs["items"]]
        # Per-axis: quality=0.1 (low low-quality), on_topic=0.9, importance=0.8
        if name == "rate_quality":
            scores = [0.1, 0.1, 0.1]
        elif name == "rate_on_topic":
            scores = [0.9, 0.9, 0.9]
        elif name == "rate_importance":
            scores = [0.8, 0.5, 0.2]
        else:
            scores = [0.5] * len(ids)
        return RatingOutput(results_list=[
            StoryConfidence(id=i, confidence=s) for i, s in zip(ids, scores)
        ])

    def fake_bt(ids, battles=None, **_):
        return {i: float(j) for j, i in enumerate(ids)}

    with patch("lib.steps.rate.call_prompt", side_effect=fake_call_prompt):
        with patch("lib.steps.rate.bradley_terry_scores", side_effect=fake_bt):
            runner = CliRunner()
            result = runner.invoke(rate_cli, ["--db", tmp_db, "--session", "r1"])
    assert result.exit_code == 0, result.output

    state = NewsletterAgentState(session_id="r1", db_path=tmp_db).load_latest_from_db()
    for h in state.headline_data:
        for key in ["quality_low", "on_topic", "importance", "bt_z", "rating"]:
            assert key in h, f"missing {key} on {h}"

    # Headline 0 had higher importance — should have higher rating than 2
    ratings = {h["id"]: h["rating"] for h in state.headline_data}
    assert ratings[0] > ratings[2]
```

- [ ] **Step 3: Implement** (per the logic above; consult `~/projects/OpenAIAgentsSDK/do_rating.py:375-end` for `fn_rate_articles` patterns)

- [ ] **Step 4: SKILL.md, tests, commit**

```bash
.venv/bin/pytest tests/test_step_rate.py -v
git add lib/steps/rate.py skills/rate/SKILL.md tests/test_step_rate.py lib/config.py
git commit -m "feat(steps): news:rate combines per-axis confidence + Bradley-Terry composite"
```

---

## Task 14: End-to-end Phase 3 verification

**Files:** none modified.

- [ ] **Step 1: Full pytest + coverage**

```bash
cd /Users/drucev/projects/news_agent
.venv/bin/pytest tests/ -v --cov=lib --cov-report=term
```
Expected: all pass. New coverage targets: `lib/engines/openai_chat.py` ≥ 75%, `lib/engines/google.py` ≥ 75%, `lib/embeddings.py` ≥ 80%, `lib/rating.py` ≥ 70%, each new step ≥ 80%.

- [ ] **Step 2: Engine override sanity**

```bash
NEWS_PROMPT_FILTER_URLS_ENGINE='openai:gpt-4o-mini' \
.venv/bin/python -c "
import lib.prompts
from lib.llm import _resolve_engine, get_prompt
cfg = get_prompt('filter_urls')
print(_resolve_engine('filter_urls', None, cfg))
"
# Expect: openai:gpt-4o-mini
```

- [ ] **Step 3: Optional live test** (only if all four API keys are set):

```bash
cd /tmp && rm -rf p3-smoke && mkdir p3-smoke && cd p3-smoke
ln -s /Users/drucev/projects/news_agent/sources.yaml sources.yaml
/Users/drucev/projects/news_agent/.venv/bin/python -m lib.steps.init --db p3.db --sources sources.yaml --session p3
/Users/drucev/projects/news_agent/.venv/bin/python -m lib.steps.gather --db p3.db --session p3
# Only run filter on first 10 headlines to keep cost minimal
NEWS_PROMPT_FILTER_URLS_ENGINE='openrouter:google/gemini-2.5-flash' \
  /Users/drucev/projects/news_agent/.venv/bin/python -m lib.steps.filter --db p3.db --session p3
/Users/drucev/projects/news_agent/.venv/bin/python -m lib.steps.status --db p3.db --session p3
```

If you skip the live test (no keys), document that in the commit message of Step 4.

- [ ] **Step 4: Tag**

```bash
cd /Users/drucev/projects/news_agent
git tag phase-3-complete
git log --oneline phase-2-complete..phase-3-complete | head -25
```

---

## Notes for the implementer

- **No Anthropic SDK or API anywhere.** Hard constraint. Anywhere you'd be tempted to add an `anthropic:` engine path, the answer is `subagent`.
- **Embeddings = OpenAI `text-embedding-3-large`.** Don't substitute. The `umap_reducer.pkl` was fit on these dimensions.
- **PromptConfig binds model + reasoning_effort per prompt.** Keep ports close to legacy `prompts.py` so future ports are mechanical.
- **`.venv/bin/pytest`** not system `pytest`.
- **`monkeypatch.chdir(tmp_path)`** for any step test that writes to `runs/` or `out/`.
- **Pyright "could not be resolved"** = IDE noise, ignore.
- **Don't over-engineer rate.py.** Legacy `do_rating.py` is 527 LOC including pandas, async semaphores, display(), and convergence heuristics. Port the math + Swiss pairing logic, ignore everything else. ~150 LOC target.

## Out of scope for Phase 3

- `cluster` step (Phase 4 — UMAP + HDBSCAN)
- `select` step (Phase 4 — MMR diversity + LLM noise-point assignment)
- `draft`/`rewrite` steps (Phase 5)
- `/news:run` orchestrator (Phase 6)
- Bluesky pipeline (Phase 7)
