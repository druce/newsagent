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
    assert active["peak"] >= 3, f"expected >=3 concurrent calls, got {active['peak']}"
    assert active["peak"] <= 4, f"expected <=4 concurrent, got {active['peak']}"


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
