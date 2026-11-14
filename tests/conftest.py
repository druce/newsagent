import importlib
import sys
import pytest
from pathlib import Path


@pytest.fixture(autouse=True)
def _isolate_brightdata_env(monkeypatch):
    """Tests should never accidentally hit the live Bright Data proxy because
    BRIGHTDATA_API_KEY happens to be set in the dev shell. Individual tests
    that exercise BD wiring should set it themselves (and mock the call)."""
    monkeypatch.delenv("BRIGHTDATA_API_KEY", raising=False)
    monkeypatch.delenv("BRIGHTDATA_ZONE", raising=False)


@pytest.fixture(autouse=True)
def _clear_sites_map_cache():
    """`lib.sources._load_db_map` is lru_cached on the DB path, and the default
    path ("newsletter_agent.db") is RELATIVE. A test running from the repo root
    warms the cache with the real production sites table; a later test that
    chdirs to tmp_path then gets a cache hit on the same key and sees
    production names instead of its own empty DB. Clear it around every test."""
    from lib.sources import _load_db_map
    _load_db_map.cache_clear()
    yield
    _load_db_map.cache_clear()


@pytest.fixture(autouse=True)
def _ensure_prompts_registered():
    """Re-register all prompts before each test if the registry was cleared.

    `tests/test_batching.py` has an autouse `_clean_registry` fixture that
    clears `lib.llm._registry` for its own tests. Without this fixture, any
    test running after a test_batching test would see an empty registry and
    fail with `KeyError: 'Prompt not registered'`.

    Re-register the EXISTING PromptConfig instances (don't reload modules)
    so class identity is preserved — tests that use `cfg.input_schema is X`
    keep working.
    """
    from lib.llm import _registry, register_prompt, PromptConfig
    if not _registry:
        importlib.import_module("lib.prompts")  # ensure top-level prompt package loaded
        for modname, mod in list(sys.modules.items()):
            # eval-only variants (evals.prompts.*) register the same way
            if not modname.startswith(("lib.prompts.", "evals.prompts.")) or mod is None:
                continue
            for attr in dir(mod):
                obj = getattr(mod, attr, None)
                if isinstance(obj, PromptConfig) and obj.name not in _registry:
                    register_prompt(obj)
    yield


@pytest.fixture
def tmp_db(tmp_path: Path) -> str:
    """Return a path to a fresh SQLite DB file in a temp dir."""
    return str(tmp_path / "test_newsletter.db")


@pytest.fixture
def sample_sources_yaml(tmp_path: Path) -> str:
    """Write a minimal sources.yaml and return its path."""
    content = """
sources:
  example_rss:
    type: rss
    url: https://example.com/feed.xml
    enabled: true
  example_html:
    type: html
    url: https://example.com/news
    enabled: false
"""
    p = tmp_path / "sources.yaml"
    p.write_text(content)
    return str(p)


@pytest.fixture(autouse=True)
def _stub_published_embeddings(monkeypatch):
    """`select` (and send's fallback) record into published_articles via
    lib.published, which embeds the picks. Never hit OpenAI from tests; tests
    that care patch lib.published.embed_texts themselves (overrides this)."""
    monkeypatch.setattr(
        "lib.published.embed_texts", lambda texts: [[0.5, 0.5] for _ in texts])
