"""Tests for the edition draft: selected_rows, strip_tracking_params, lib/edition.py."""
import json
import sqlite3
from datetime import datetime
from pathlib import Path

import pytest

from lib.curate import selected_rows
from lib.db import init_db
from lib.edition import (
    apply_edit,
    apply_sections,
    build_edit_batch,
    build_section_batches,
    recent_kickers,
    render_edition_md,
    validate_issue,
    validate_section,
)
from lib.utilities import strip_tracking_params

SINCE = "2026-11-14-12-00-00"
IN_SID = "2026-11-15-07-00-14"


def _insert(db, **cols) -> int:
    init_db(db)
    row = {"session_id": IN_SID, "url": f"https://ex.com/{len(cols)}-{id(cols)}",
           "title": "T", "short_summary": "Short", "summary": "- a\n- b",
           "site_name": "Example", "cat": "Alpha", "rating": 1.0, "selected": 1}
    row.update(cols)
    with sqlite3.connect(db) as conn:
        return conn.execute(
            f"INSERT INTO published_articles ({', '.join(row)}) "
            f"VALUES ({', '.join('?' for _ in row)})", tuple(row.values()),
        ).lastrowid


# ── selected_rows ────────────────────────────────────────────────────────────

def test_selected_rows_only_selected_in_window(tmp_db):
    keep = _insert(tmp_db, url="https://a.com/1")
    _insert(tmp_db, url="https://a.com/2", selected=0)
    _insert(tmp_db, url="https://a.com/3", session_id="2026-11-14-07-00-13")
    _insert(tmp_db, url="https://a.com/4", session_id="test-session")
    assert [r["id"] for r in selected_rows(tmp_db, SINCE)] == [keep]


# ── strip_tracking_params ────────────────────────────────────────────────────

@pytest.mark.parametrize("url,expected", [
    ("https://x.com/a?utm_source=n&utm_medium=e", "https://x.com/a"),
    ("https://x.com/a?id=5&utm_campaign=z", "https://x.com/a?id=5"),
    ("https://x.com/a?_bhlid=abc&guccounter=1&guce_referrer=q", "https://x.com/a"),
    ("https://x.com/a?twclid=1&smid=nytcore&ck_subscriber_id=9", "https://x.com/a"),
    ("https://x.com/a?syn-25a6b1a6=1&st=abc", "https://x.com/a"),
    ("https://x.com/a?accessToken=t&gift=g&unlocked_article_code=u&utm_x=1",
     "https://x.com/a?accessToken=t&gift=g&unlocked_article_code=u"),
    ("https://x.com/a?utm_source=n#frag", "https://x.com/a#frag"),
    ("https://x.com/a", "https://x.com/a"),
    ("https://x.com/a?q=a%20b&utm_x=1", "https://x.com/a?q=a%20b"),
])
def test_strip_tracking_params(url, expected):
    assert strip_tracking_params(url) == expected


# ── validators ───────────────────────────────────────────────────────────────

def _item(i, **kw):
    base = {"id": i, "kicker": "Brin it on", "summary": "Sergey Brin says things",
            "source": "Business Insider", "alt_kickers": ["a", "b", "c"]}
    base.update(kw)
    return base


SECTION_BATCH = {"kind": "edition-section", "ids": [1, 2], "kicker_count": 3}


def test_validate_section_ok():
    errors, _ = validate_section({"cat": "A", "items": [_item(1), _item(2)]}, SECTION_BATCH)
    assert errors == []


def test_validate_section_missing_extra_and_alt_count():
    errors, _ = validate_section(
        {"cat": "A", "items": [_item(1, alt_kickers=["a", "b"]), _item(3)]}, SECTION_BATCH)
    text = " ".join(errors)
    assert "missing" in text and "2" in text
    assert "extra" in text and "3" in text
    assert "alt_kickers" in text


def test_validate_section_duplicate_and_long_kicker_warning():
    errors, warnings = validate_section(
        {"cat": "A", "items": [_item(1), _item(1), _item(2, kicker="one two three four five six seven eight")]},
        SECTION_BATCH)
    assert any("duplicate" in e for e in errors)
    assert any("7 words" in w for w in warnings)


def test_validate_issue_flattens_clusters():
    batch = {"kind": "edition-issue", "ids": [1, 2], "kicker_count": 3}
    ok = {"subtitle": "Hacktoberfest", "subtitle_alts": list("abcde"),
          "clusters": [{"cat": "A", "items": [_item(2)]}, {"cat": "B", "items": [_item(1)]}],
          "notes": []}
    assert validate_issue(ok, batch)[0] == []
    bad = dict(ok, clusters=[{"cat": "A", "items": [_item(2)]}], subtitle="")
    text = " ".join(validate_issue(bad, batch)[0])
    assert "missing" in text and "subtitle" in text


# ── renderer ─────────────────────────────────────────────────────────────────

ISSUE = {
    "subtitle": "Software eats itself",
    "subtitle_alts": ["s1", "s2", "s3", "s4", "s5"],
    "clusters": [
        {"cat": "Lead", "items": [
            _item(1, kicker="Brin it on", summary="Sergey Brin says [Argon] helps",
                   source="Business_Insider"),
            _item(2, kicker="Sparking joy?", summary="Meta releases a thing",
                   flag="possible duplicate of 1 --> check"),
        ]},
        {"cat": "Tail", "items": [_item(3, kicker="", summary="No kicker here")]},
    ],
    "notes": [],
}
URLS = {1: "https://a.com/x?utm_source=z", 2: "https://b.com/(y)", 3: "https://c.com/z"}


def test_render_edition_md_format():
    md = render_edition_md(ISSUE, URLS, "AI Reading for Sunday November 15")
    lines = md.splitlines()
    assert lines[0] == "# AI Reading for Sunday November 15"
    assert "Software eats itself" in lines[1]
    assert "<!-- subtitle alts: s1 | s2 | s3 | s4 | s5 -->" in md
    assert ("[Brin it on: Sergey Brin says \\[Argon\\] helps](https://a.com/x)"
            " - _Business\\_Insider_") in md
    assert "[Sparking joy? Meta releases a thing](https://b.com/%28y%29) - _Business Insider_" in md
    assert "[No kicker here](https://c.com/z)" in md
    assert "<!-- alts: a | b | c -->" in md
    assert "<!-- flag: possible duplicate of 1 - -> check -->" in md
    assert md.count("\n———\n") == 1
    assert lines[-1] == "Follow the latest AI headlines via druce.ai on Bluesky"


# ── recent_kickers ───────────────────────────────────────────────────────────

def test_recent_kickers_parses_recent_files_only(tmp_path):
    (tmp_path / "edition-2026-11-10-070000.md").write_text(
        "# AI Reading\nsub\n\n[Brin it on: Sergey says](u) - _X_\n"
        "<!-- alts: a | b -->\n[Sparking joy? Meta](u) - _Y_\n[No kicker](u) - _Z_\n")
    (tmp_path / "edition-2026-09-01-070000.md").write_text("[Old one: x](u) - _X_\n")
    got = recent_kickers(tmp_path, datetime(2026, 11, 15), days=30)
    assert got == ["Brin it on", "Sparking joy?"]


# ── batch build / apply round trip ───────────────────────────────────────────

def test_section_and_edit_round_trip_uses_db_urls(tmp_db, tmp_path):
    a = _insert(tmp_db, url="https://a.com/1?utm_source=n", cat="Alpha")
    b = _insert(tmp_db, url="https://b.com/2", cat="Beta")
    rows = selected_rows(tmp_db, SINCE)
    run_dir = tmp_path / "runs" / "edition-x"
    paths = build_section_batches(rows, run_dir, kicker_count=3, docs="DOCS")
    assert [p.name for p in paths] == ["batch-000.json", "batch-001.json"]
    meta = json.loads(paths[0].read_text())
    assert meta["ids"] == [a] and meta["kicker_count"] == 3
    prompt = (run_dir / "edition-sections-batches" / "batch-000.md").read_text()
    assert "DOCS" in prompt and "https://a.com/1" in prompt

    res_dir = run_dir / "edition-sections-results"
    res_dir.mkdir(parents=True)
    for p, i in zip(paths, (a, b)):
        cat = json.loads(p.read_text())["cat"]
        (res_dir / p.name).write_text(json.dumps({"cat": cat, "items": [_item(i)]}))
    sections, problems = apply_sections(run_dir)
    assert problems == [] and len(sections) == 2

    edit_meta = build_edit_batch(run_dir, docs="DOCS", recent=["Old frame"])
    meta = json.loads(edit_meta.read_text())
    assert sorted(meta["ids"]) == sorted([a, b])
    assert "Old frame" in (edit_meta.with_suffix(".md")).read_text()

    issue = {"subtitle": "Sub", "subtitle_alts": list("abcde"),
             "clusters": [{"cat": "Beta", "items": [_item(b)]},
                          {"cat": "Alpha", "items": [_item(a)]}], "notes": ["n"]}
    edit_res = run_dir / "edition-edit-results"
    edit_res.mkdir(parents=True)
    (edit_res / "batch-000.json").write_text(json.dumps(issue))
    out_dir = tmp_path / "out"
    path = apply_edit(run_dir, tmp_db, out_dir, now=datetime(2026, 11, 15, 9, 0, 0))
    md = path.read_text()
    assert path.name == "edition-2026-11-15-090000.md"
    assert (out_dir / "latest-edition.md").resolve() == path.resolve()
    assert md.index("https://b.com/2") < md.index("https://a.com/1)")
    assert "utm_source" not in md
    assert "# AI Reading for Sunday November 15" in md


def test_apply_sections_reports_missing_result(tmp_db, tmp_path):
    _insert(tmp_db, url="https://a.com/1")
    run_dir = tmp_path / "runs" / "edition-x"
    build_section_batches(selected_rows(tmp_db, SINCE), run_dir, 3, "DOCS")
    _, problems = apply_sections(run_dir)
    assert any("batch-000" in p for p in problems)


# ── tools/check_batch.py understands edition batches ─────────────────────────

def _check(result_path):
    import subprocess, sys
    return subprocess.run([sys.executable, "tools/check_batch.py", str(result_path)],
                          capture_output=True, text=True)


def test_check_batch_edition_section(tmp_path):
    bd, rd = tmp_path / "edition-sections-batches", tmp_path / "edition-sections-results"
    bd.mkdir(); rd.mkdir()
    (bd / "batch-000.json").write_text(json.dumps(SECTION_BATCH))
    (rd / "batch-000.json").write_text(json.dumps({"cat": "A", "items": [_item(1), _item(2)]}))
    ok = _check(rd / "batch-000.json")
    assert ok.returncode == 0 and ok.stdout.startswith("OK")
    (rd / "batch-000.json").write_text(json.dumps({"cat": "A", "items": [_item(1)]}))
    bad = _check(rd / "batch-000.json")
    assert bad.returncode == 1 and "missing" in bad.stdout


def test_check_batch_edition_issue(tmp_path):
    bd, rd = tmp_path / "edition-edit-batches", tmp_path / "edition-edit-results"
    bd.mkdir(); rd.mkdir()
    (bd / "batch-000.json").write_text(json.dumps(
        {"kind": "edition-issue", "ids": [1], "kicker_count": 3}))
    (rd / "batch-000.json").write_text(json.dumps(
        {"subtitle": "S", "subtitle_alts": list("abcde"),
         "clusters": [{"cat": "A", "items": [_item(1)]}], "notes": []}))
    assert _check(rd / "batch-000.json").returncode == 0


# ── CLI ──────────────────────────────────────────────────────────────────────

def test_cli_end_to_end(tmp_db, tmp_path, monkeypatch):
    from click.testing import CliRunner
    import lib.steps.edition as step

    a = _insert(tmp_db, url="https://a.com/1", cat="Alpha")
    monkeypatch.setattr(step, "_RUNS", tmp_path / "runs")
    monkeypatch.setattr(step, "_OUT", tmp_path / "out")
    r = CliRunner()

    res = r.invoke(step.cli, ["--prepare-sections", "--since", SINCE, "--db", tmp_db])
    assert res.exit_code == 0, res.output
    run = res.output.split()[1]
    assert run.startswith("edition-") and "1 sections" in res.output

    # not ready yet → exit 1 with the batch named
    res = r.invoke(step.cli, ["--apply-sections", run])
    assert res.exit_code == 1 and "batch-000" in res.output

    run_dir = tmp_path / "runs" / run
    (run_dir / "edition-sections-results").mkdir()
    (run_dir / "edition-sections-results" / "batch-000.json").write_text(
        json.dumps({"cat": "Alpha", "items": [_item(a)]}))
    res = r.invoke(step.cli, ["--apply-sections", run])
    assert res.exit_code == 0, res.output

    (run_dir / "edition-edit-results").mkdir()
    (run_dir / "edition-edit-results" / "batch-000.json").write_text(json.dumps(
        {"subtitle": "Sub", "subtitle_alts": list("abcde"),
         "clusters": [{"cat": "Alpha", "items": [_item(a)]}], "notes": ["read X as Y"]}))
    res = r.invoke(step.cli, ["--apply-edit", run, "--db", tmp_db])
    assert res.exit_code == 0, res.output
    assert "1 items" in res.output and "note: read X as Y" in res.output
    assert "https://a.com/1" in (tmp_path / "out" / "latest-edition.md").read_text()


def test_cli_requires_one_mode():
    from click.testing import CliRunner
    import lib.steps.edition as step
    assert CliRunner().invoke(step.cli, []).exit_code == 2


def test_cli_prepare_with_no_picks(tmp_db):
    from click.testing import CliRunner
    import lib.steps.edition as step
    init_db(tmp_db)
    res = CliRunner().invoke(step.cli, ["--prepare-sections", "--since", SINCE, "--db", tmp_db])
    assert res.exit_code == 1 and "no selected articles" in res.output
