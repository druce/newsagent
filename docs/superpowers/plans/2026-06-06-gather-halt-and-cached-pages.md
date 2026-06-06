# Gather halt-on-empty + cached-pages Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `gather` halt the pipeline when any source extracts 0 URLs, add a `--cached-pages` mode that reads html sources from `runs/<SID>/pages/` (rss/rest still live), and support resuming the halted session after dropping a manual landing page.

**Architecture:** All new behavior lives in `lib/steps/gather.py` (a `--cached-pages` flag + a post-fetch "empty source" check that calls `error_step` and exits non-zero). `lib/steps/pipeline.py` plumbs `--cached-pages` to the gather step only. Resume reuses the existing step-status machinery — a halted gather is left `ERROR`, so `get_current_step()` returns it as the resume point. Docs updated in two SKILL.md files.

**Tech Stack:** Python 3.11, Click, pytest + Click `CliRunner`, SQLite. Use `.venv/bin/pytest`, never system pytest.

---

## File Structure

- `lib/steps/gather.py` — **modify**. Add `extract_article_links` import; add `_read_cached_page` + `_fetch_html_cached` helpers; thread `cached_pages`/`session_id` into `_fetch_one`; add `--cached-pages` CLI flag; add post-fetch empty-source detection → `error_step` + `gather.json` `empty_sources` + recovery print + `sys.exit(1)`.
- `lib/steps/pipeline.py` — **modify**. Add `--cached-pages` flag; pass it through `_build_args` to the gather step only.
- `tests/test_step_gather.py` — **modify**. Add tests for cached-pages reading, missing-cache halt, halt-on-empty, no-halt-on-0-new, and resume-completes.
- `tests/test_step_pipeline.py` — **create or modify**. Unit-test `_build_args` cached-pages plumbing.
- `skills/gather/SKILL.md` — **modify**. Document `--cached-pages` + halt semantics.
- `skills/pipeline/SKILL.md` — **modify**. Document handling a gather halt + recovery.

---

## Task 1: Cached-pages reading in gather

Add the `--cached-pages` flag and the disk-reading path for html sources. RSS/REST are untouched (still fetched live).

**Files:**
- Modify: `lib/steps/gather.py`
- Test: `tests/test_step_gather.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_step_gather.py`:

```python
def test_gather_cached_pages_reads_html_from_disk(tmp_path, tmp_db, monkeypatch):
    monkeypatch.chdir(tmp_path)
    yaml_path = tmp_path / "sources.yaml"
    yaml_path.write_text(
        "sources:\n  Site1:\n    type: html\n    url: https://news.example.com/\n    enabled: true\n"
    )
    _make_session(tmp_db, str(yaml_path))

    page_dir = Path("runs/g1/pages")
    page_dir.mkdir(parents=True, exist_ok=True)
    (page_dir / "Site1.html").write_text(
        '<html><body>'
        '<a href="https://news.example.com/story-one">'
        'A sufficiently long story headline here</a>'
        '</body></html>'
    )

    def _boom(*a, **k):
        raise AssertionError("fetch_html must not be called with --cached-pages")

    with patch("lib.steps.gather.fetch_html", side_effect=_boom):
        runner = CliRunner()
        result = runner.invoke(
            gather_cli, ["--db", tmp_db, "--session", "g1", "--cached-pages"]
        )

    assert result.exit_code == 0, result.output
    with sqlite3.connect(tmp_db) as conn:
        rows = {r[0] for r in conn.execute("SELECT initial_url FROM urls").fetchall()}
    assert "https://news.example.com/story-one" in rows


def test_gather_cached_pages_still_fetches_rss_live(tmp_path, tmp_db, monkeypatch):
    monkeypatch.chdir(tmp_path)
    yaml_path = tmp_path / "sources.yaml"
    yaml_path.write_text(
        "sources:\n"
        "  Site1:\n    type: html\n    url: https://news.example.com/\n    enabled: true\n"
        "  Feed1:\n    type: rss\n    url: https://feed.example.com/rss\n    enabled: true\n"
    )
    _make_session(tmp_db, str(yaml_path))

    page_dir = Path("runs/g1/pages")
    page_dir.mkdir(parents=True, exist_ok=True)
    (page_dir / "Site1.html").write_text(
        '<html><body>'
        '<a href="https://news.example.com/story-one">'
        'A sufficiently long story headline here</a>'
        '</body></html>'
    )

    rss_result = FetchResult(source="Feed1", ok=True, articles=[
        Article(source="Feed1", title="A sufficiently long rss headline here",
                url="https://feed.example.com/a"),
    ])
    with patch("lib.steps.gather.fetch_html",
               side_effect=AssertionError("html must come from cache")), \
         patch("lib.steps.gather.fetch_rss", return_value=rss_result) as mock_rss:
        runner = CliRunner()
        result = runner.invoke(
            gather_cli, ["--db", tmp_db, "--session", "g1", "--cached-pages"]
        )

    assert result.exit_code == 0, result.output
    mock_rss.assert_called_once()
    with sqlite3.connect(tmp_db) as conn:
        rows = {r[0] for r in conn.execute("SELECT initial_url FROM urls").fetchall()}
    assert "https://feed.example.com/a" in rows
    assert "https://news.example.com/story-one" in rows
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_step_gather.py::test_gather_cached_pages_reads_html_from_disk tests/test_step_gather.py::test_gather_cached_pages_still_fetches_rss_live -v`
Expected: FAIL — `gather_cli` has no `--cached-pages` option (Click "no such option" error / non-zero exit).

- [ ] **Step 3: Add the import and helpers**

In `lib/steps/gather.py`, add to the imports (after the existing `from lib.fetch.html import fetch_html` line):

```python
from lib.fetch.extract import extract_article_links
```

Then add these helpers right after `_safe_filename` (before `_fetch_one`):

```python
def _read_cached_page(session_id: str, source_name: str) -> str | None:
    path = Path("runs") / session_id / "pages" / f"{_safe_filename(source_name)}.html"
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8")


def _fetch_html_cached(
    source_name: str, cfg: dict, session_id: str
) -> tuple[FetchResult, str | None, str | None]:
    """Build a FetchResult from a previously-saved landing page on disk.

    Missing cache file → ok=False (counts as an empty source at halt time).
    """
    html = _read_cached_page(session_id, source_name)
    if html is None:
        rel = f"runs/{session_id}/pages/{_safe_filename(source_name)}.html"
        return (
            FetchResult(source=source_name, ok=False,
                        error=f"cached page missing: {rel}"),
            "cached",
            None,
        )
    articles = extract_article_links(html, cfg, source_name)
    ok = len(articles) > 0
    err = None if ok else "no links extracted from cached page"
    return (
        FetchResult(source=source_name, articles=articles, ok=ok, error=err),
        "cached",
        html,
    )
```

- [ ] **Step 4: Thread cached_pages into `_fetch_one`**

Replace the `_fetch_one` signature and its `html` branch. Change the signature line:

```python
def _fetch_one(
    source_name: str, cfg: dict, db_path: str,
    *, cached_pages: bool = False, session_id: str = "",
) -> tuple[FetchResult, str | None, str | None]:
```

And replace the `if stype == "html":` block body so it short-circuits to the cache in cached mode:

```python
    if stype == "html":
        if cached_pages:
            return _fetch_html_cached(source_name, cfg, session_id)
        url = cfg.get("url", "")
        domain = _domain_of(url)
        prior_method: str | None = None
        if domain:
            with sqlite3.connect(db_path) as conn:
                site = Site.get_by_domain(conn, domain)
                if site:
                    prior_method = site.scrape_method
        result, used, raw_html = fetch_html(source_name, cfg, scrape_method=prior_method)
        return result, used, raw_html
```

- [ ] **Step 5: Add the `--cached-pages` flag and pass it through**

In `lib/steps/gather.py`, add the option to the `cli` command (after the `--session` option):

```python
@click.option("--cached-pages", "cached_pages", is_flag=True,
              help="Read html sources from runs/<SID>/pages/<source>.html "
                   "instead of fetching them; rss/rest are still fetched live.")
```

Update the `cli` function signature to accept it:

```python
def cli(db_path: str, session_id: str, cached_pages: bool) -> None:
```

And update the `_fetch_one` call inside the loop:

```python
        result, used_method, raw_html = _fetch_one(
            name, cfg, db_path, cached_pages=cached_pages, session_id=session_id,
        )
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_step_gather.py::test_gather_cached_pages_reads_html_from_disk tests/test_step_gather.py::test_gather_cached_pages_still_fetches_rss_live -v`
Expected: PASS (both).

- [ ] **Step 7: Commit**

```bash
git add lib/steps/gather.py tests/test_step_gather.py
git commit -m "feat(gather): --cached-pages reads html sources from runs/<SID>/pages"
```

---

## Task 2: Halt-on-empty in gather

After fetching all sources, halt (exit non-zero, mark step ERROR) if any enabled source extracted 0 URLs. Working sources' URLs are still inserted; `gather.json` records the empty sources.

**Files:**
- Modify: `lib/steps/gather.py`
- Test: `tests/test_step_gather.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_step_gather.py`:

```python
def test_gather_halts_when_source_extracts_zero(tmp_path, tmp_db, monkeypatch):
    monkeypatch.chdir(tmp_path)
    yaml_path = tmp_path / "sources.yaml"
    yaml_path.write_text(
        "sources:\n"
        "  Good:\n    type: rss\n    url: https://feed.example.com/rss\n    enabled: true\n"
        "  Bad:\n    type: rss\n    url: https://bad.example.com/rss\n    enabled: true\n"
    )
    _make_session(tmp_db, str(yaml_path))

    def _rss(source_name, url):
        if source_name == "Good":
            return FetchResult(source="Good", ok=True, articles=[
                Article(source="Good", title="A sufficiently long good headline here",
                        url="https://feed.example.com/a"),
            ])
        return FetchResult(source="Bad", ok=False, error="boom", articles=[])

    with patch("lib.steps.gather.fetch_rss", side_effect=_rss):
        runner = CliRunner()
        result = runner.invoke(gather_cli, ["--db", tmp_db, "--session", "g1"])

    # Halts with a non-zero exit code.
    assert result.exit_code != 0, result.output
    # Working source's URL is still persisted.
    with sqlite3.connect(tmp_db) as conn:
        rows = {r[0] for r in conn.execute("SELECT initial_url FROM urls").fetchall()}
    assert "https://feed.example.com/a" in rows
    # Step is left in ERROR so resume picks it up.
    state = NewsletterAgentState(session_id="g1", db_path=tmp_db).load_latest_from_db()
    assert state.get_current_step() == "gather"
    # gather.json lists the empty source.
    report = json.loads(Path("runs/g1/gather.json").read_text())
    assert [e["source"] for e in report["empty_sources"]] == ["Bad"]


def test_gather_does_not_halt_on_zero_new_after_dedup(tmp_path, tmp_db, monkeypatch):
    monkeypatch.chdir(tmp_path)
    yaml_path = tmp_path / "sources.yaml"
    yaml_path.write_text(
        "sources:\n  Feed1:\n    type: rss\n    url: https://feed.example.com/rss\n    enabled: true\n"
    )
    _make_session(tmp_db, str(yaml_path))

    # Pre-seed the only article so it dedups to 0 new — but extracted > 0.
    with sqlite3.connect(tmp_db) as conn:
        conn.execute(
            "INSERT INTO urls (initial_url, final_url, title, source) VALUES (?,?,?,?)",
            ("https://feed.example.com/a", "https://feed.example.com/a", "A", "Feed1"),
        )
        conn.commit()

    rss_result = FetchResult(source="Feed1", ok=True, articles=[
        Article(source="Feed1", title="A sufficiently long headline here",
                url="https://feed.example.com/a"),
    ])
    with patch("lib.steps.gather.fetch_rss", return_value=rss_result):
        runner = CliRunner()
        result = runner.invoke(gather_cli, ["--db", tmp_db, "--session", "g1"])

    assert result.exit_code == 0, result.output
    state = NewsletterAgentState(session_id="g1", db_path=tmp_db).load_latest_from_db()
    # gather completed, so the first non-complete step is the next one (filter).
    assert state.get_current_step() == "filter"
```

Note: `NewsletterAgentState` subclasses `WorkflowState`, so `get_current_step()`, `complete_step()`, and `error_step()` are called directly on `state` (there is no `state.workflow` attribute). `get_current_step()` returns the first non-`COMPLETE` step.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_step_gather.py::test_gather_halts_when_source_extracts_zero tests/test_step_gather.py::test_gather_does_not_halt_on_zero_new_after_dedup -v`
Expected: FAIL — first test gets exit_code 0 (no halt yet) and `KeyError: 'empty_sources'`.

- [ ] **Step 3: Implement the halt**

In `lib/steps/gather.py`, replace this existing block:

```python
    state.headline_data.extend(new_articles_for_state)
    state.complete_step(
        "gather",
        message=f"{len(enabled_sources)} sources, {new_count} new headlines",
    )
    state.save_checkpoint("gather")
```

with:

```python
    state.headline_data.extend(new_articles_for_state)

    # A source is "empty" if it failed to fetch OR extracted 0 links (pre-dedup).
    # "0 new after dedup" is NOT empty — those links were simply already known.
    empty_sources = [
        {
            "source": s["source"],
            "type": s["type"],
            "error": s["error"],
            "page_path": (
                f"runs/{session_id}/pages/{_safe_filename(s['source'])}.html"
                if s["type"] == "html" else None
            ),
        }
        for s in report_sources
        if not s["ok"] or s["count"] == 0
    ]

    if empty_sources:
        state.error_step(
            "gather",
            f"{len(empty_sources)} source(s) extracted 0 URLs: "
            + ", ".join(e["source"] for e in empty_sources),
        )
    else:
        state.complete_step(
            "gather",
            message=f"{len(enabled_sources)} sources, {new_count} new headlines",
        )
    state.save_checkpoint("gather")
```

- [ ] **Step 4: Record empty sources in gather.json and add the recovery exit**

In the `gather.json` write, add the `empty_sources` field:

```python
    (runs_dir / "gather.json").write_text(json.dumps({
        "session_id": session_id,
        "completed_at": datetime.now().isoformat(),
        "sources": report_sources,
        "new_headlines": new_count,
        "empty_sources": empty_sources,
    }, indent=2))
```

Then, at the very end of `cli` (after the existing per-source table print loop), add:

```python
    if empty_sources:
        click.echo("")
        click.echo(f"gather halted: {len(empty_sources)} source(s) returned 0 URLs.")
        for e in empty_sources:
            if e["page_path"]:
                click.echo(
                    f"  - {e['source']} (html): download the landing page to "
                    f"{e['page_path']}, then resume."
                )
            else:
                click.echo(
                    f"  - {e['source']} ({e['type']}): returned 0 "
                    f"({e['error'] or 'no links'}); fix upstream and resume."
                )
        click.echo(
            "Resume with: python -m lib.steps.pipeline "
            f"--resume {session_id} --cached-pages"
        )
        sys.exit(1)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_step_gather.py::test_gather_halts_when_source_extracts_zero tests/test_step_gather.py::test_gather_does_not_halt_on_zero_new_after_dedup -v`
Expected: PASS (both).

- [ ] **Step 6: Run the full gather test module (no regressions)**

Run: `.venv/bin/pytest tests/test_step_gather.py -v`
Expected: PASS for all tests, including the pre-existing `test_gather_writes_report_json` (it feeds a 0-article source but only asserts `gather.json` contents, which are written before the halt exit).

- [ ] **Step 7: Commit**

```bash
git add lib/steps/gather.py tests/test_step_gather.py
git commit -m "feat(gather): halt on any source that extracts 0 URLs"
```

---

## Task 3: Resume-completes integration test

Prove the end-to-end recovery: a halted gather, then a `--cached-pages` re-run after the manual file is present, completes the step.

**Files:**
- Test: `tests/test_step_gather.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_step_gather.py`:

```python
def test_gather_resume_with_cached_pages_completes(tmp_path, tmp_db, monkeypatch):
    monkeypatch.chdir(tmp_path)
    yaml_path = tmp_path / "sources.yaml"
    yaml_path.write_text(
        "sources:\n  Site1:\n    type: html\n    url: https://news.example.com/\n    enabled: true\n"
    )
    _make_session(tmp_db, str(yaml_path))

    # First pass: html fetch comes back empty → halt.
    empty = FetchResult(source="Site1", ok=False, error="blocked", articles=[])
    with patch("lib.steps.gather.fetch_html", return_value=(empty, "playwright", None)):
        runner = CliRunner()
        first = runner.invoke(gather_cli, ["--db", tmp_db, "--session", "g1"])
    assert first.exit_code != 0, first.output

    state = NewsletterAgentState(session_id="g1", db_path=tmp_db).load_latest_from_db()
    assert state.get_current_step() == "gather"

    # Operator drops the manual landing page where the halt message said.
    page_dir = Path("runs/g1/pages")
    page_dir.mkdir(parents=True, exist_ok=True)
    (page_dir / "Site1.html").write_text(
        '<html><body>'
        '<a href="https://news.example.com/story-one">'
        'A sufficiently long story headline here</a>'
        '</body></html>'
    )

    # Resume in cached mode: html read from disk, no network.
    with patch("lib.steps.gather.fetch_html",
               side_effect=AssertionError("must use cached page on resume")):
        runner = CliRunner()
        second = runner.invoke(
            gather_cli, ["--db", tmp_db, "--session", "g1", "--cached-pages"]
        )

    assert second.exit_code == 0, second.output
    with sqlite3.connect(tmp_db) as conn:
        rows = {r[0] for r in conn.execute("SELECT initial_url FROM urls").fetchall()}
    assert "https://news.example.com/story-one" in rows
    state = NewsletterAgentState(session_id="g1", db_path=tmp_db).load_latest_from_db()
    assert state.get_current_step() != "gather"
```

- [ ] **Step 2: Run the test**

Run: `.venv/bin/pytest tests/test_step_gather.py::test_gather_resume_with_cached_pages_completes -v`
Expected: PASS (behavior already implemented in Tasks 1–2; this test just locks in the integration). If it fails, fix the underlying behavior in `gather.py` rather than the test.

- [ ] **Step 3: Commit**

```bash
git add tests/test_step_gather.py
git commit -m "test(gather): resume with --cached-pages completes a halted gather"
```

---

## Task 4: Pipeline `--cached-pages` plumbing

Add `--cached-pages` to the orchestrator and pass it to the gather step only.

**Files:**
- Modify: `lib/steps/pipeline.py`
- Test: `tests/test_step_pipeline.py` (create if absent)

- [ ] **Step 1: Write the failing test**

Create `tests/test_step_pipeline.py` (or append if it exists):

```python
from lib.steps.pipeline import _build_args


def _args(step_id, **over):
    base = dict(
        step_id=step_id, session_id="s1", db_path="db.sqlite",
        sources_path="sources.yaml", max_edits=2, parallelism=4,
        engine=None, no_email=False, cached_pages=True,
    )
    base.update(over)
    return _build_args(**base)


def test_build_args_passes_cached_pages_to_gather():
    assert "--cached-pages" in _args("gather")


def test_build_args_omits_cached_pages_for_non_gather_steps():
    assert "--cached-pages" not in _args("filter")
    assert "--cached-pages" not in _args("download")


def test_build_args_no_cached_pages_when_flag_off():
    assert "--cached-pages" not in _args("gather", cached_pages=False)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/pytest tests/test_step_pipeline.py -v`
Expected: FAIL — `_build_args()` got an unexpected keyword argument `cached_pages`.

- [ ] **Step 3: Add the param to `_build_args`**

In `lib/steps/pipeline.py`, change the `_build_args` signature to add `cached_pages: bool` (place it after `no_email`):

```python
def _build_args(
    step_id: str,
    session_id: str,
    db_path: str,
    sources_path: str,
    max_edits: int,
    parallelism: int,
    engine: str | None,
    no_email: bool,
    cached_pages: bool = False,
) -> list[str]:
```

And inside `_build_args`, before `return args`, add:

```python
    if cached_pages and step_id == "gather":
        args.append("--cached-pages")
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `.venv/bin/pytest tests/test_step_pipeline.py -v`
Expected: PASS (all three).

- [ ] **Step 5: Add the CLI flag and pass it through**

In `lib/steps/pipeline.py`, add the option (after `--no-summary`):

```python
@click.option("--cached-pages", "cached_pages", is_flag=True,
              help="Read html sources from runs/<SID>/pages/ during gather "
                   "(rss/rest still fetched live). Use after dropping a manual "
                   "landing page to resume a halted gather.")
```

Add `cached_pages: bool` to the `cli` function signature (after `no_summary: bool`):

```python
    no_summary: bool,
    cached_pages: bool,
) -> None:
```

Update the `_build_args` call inside the run loop:

```python
        args = _build_args(
            step_id, session_id, db_path, sources_path,
            max_edits, parallelism, engine, no_email,
            cached_pages=cached_pages,
        )
```

- [ ] **Step 6: Run the pipeline test module again (sanity)**

Run: `.venv/bin/pytest tests/test_step_pipeline.py -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add lib/steps/pipeline.py tests/test_step_pipeline.py
git commit -m "feat(pipeline): --cached-pages plumbed to the gather step"
```

---

## Task 5: Documentation

Document the new flag and the halt/recovery flow in both SKILL.md files.

**Files:**
- Modify: `skills/gather/SKILL.md`
- Modify: `skills/pipeline/SKILL.md`

- [ ] **Step 1: Update `skills/gather/SKILL.md`**

Add a section near the top of the body (after the existing intro), verbatim:

```markdown
## Halt on empty sources

After fetching every enabled source, `gather` halts (exit code 1, step marked
ERROR) if ANY source extracted 0 URLs — i.e. it failed to fetch, or its landing
page yielded 0 matching links. "0 new after dedup" does NOT count as empty.

URLs from working sources are still inserted before the halt, and the empty
sources are recorded in `runs/<SID>/gather.json` under `empty_sources`. The step
is left ERROR so it is the resume point for `lib.steps.pipeline --resume`.

## --cached-pages

`python -m lib.steps.gather --session SID --cached-pages`

Reads each html source from `runs/<SID>/pages/<source>.html` instead of
fetching it; rss/rest are still fetched live. A missing cache file counts as an
empty source (→ halt). Use this to recover a halted gather: download the failed
source's landing page to the path named in the halt message, then re-run with
`--cached-pages` (typically via `lib.steps.pipeline --resume SID --cached-pages`).
```

- [ ] **Step 2: Update `skills/pipeline/SKILL.md`**

In the "Resuming / recovery" section, append verbatim:

```markdown
### Gather halt (0-URL source)

If `gather` reports `gather halted: N source(s) returned 0 URLs`, the run
stopped because a source extracted nothing (e.g. WSJ blocked). To recover:

1. For each html source named, download its landing page to the
   `runs/<SID>/pages/<source>.html` path printed in the halt message (e.g. via
   the headed browser `scripts/playwright_login.py` or Bright Data).
2. Resume in cached-pages mode (html from disk, rss/rest live):
   ```bash
   .venv/bin/python -m lib.steps.pipeline --resume SID --cached-pages
   ```
   Gather re-reads the cached html (including your manual file), finds the
   source non-empty, completes, and the pipeline continues.

For a halted rss/rest source there is no manual-file path — just resume
(`--resume SID`) once the feed/API is reachable again.
```

Also add a row to the Flags table in that file:

```markdown
| `--cached-pages` | off | Gather reads html sources from `runs/<SID>/pages/` (rss/rest still live). Use to resume a halted gather after dropping a manual landing page. |
```

- [ ] **Step 3: Commit**

```bash
git add skills/gather/SKILL.md skills/pipeline/SKILL.md
git commit -m "docs: gather halt-on-empty + --cached-pages recovery"
```

---

## Final verification

- [ ] **Run the full test suite**

Run: `.venv/bin/pytest tests/ -q`
Expected: all pass (the new gather/pipeline tests plus the existing suite, ~270+ tests).

---

## Self-Review notes (author)

- **Spec coverage:** Halt-on-empty → Task 2; "finish all then halt" (URLs inserted before exit) → Task 2 Step 3/4; any-source-type trigger → Task 2 (`empty_sources` derives from all `report_sources` regardless of type); `--cached-pages` html-from-disk + rss/rest-live → Task 1; missing-cache→empty → Task 1 helper + Task 2 detection; resume-from-last-step recovery → Task 3 (behavior) + existing `get_current_step`; pipeline plumbing → Task 4; docs (both SKILL.md, html-only manual recovery non-goal stated) → Task 5.
- **Placeholder scan:** none — every code step has full code.
- **Type consistency:** `_fetch_one(..., *, cached_pages, session_id)`, `_fetch_html_cached`, `_read_cached_page`, `empty_sources` dict shape (`source/type/error/page_path`), and `_build_args(..., cached_pages=False)` are consistent across tasks and tests.
