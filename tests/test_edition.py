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


T = {"title": "Tim Cooked", "title_alts": ["A", "B", "C"]}


SECTION_BATCH = {"kind": "edition-section", "ids": [1, 2], "kicker_count": 3}


def test_validate_section_ok():
    errors, _ = validate_section({**T, "cat": "A", "items": [_item(1), _item(2)]}, SECTION_BATCH)
    assert errors == []


def test_validate_section_missing_extra_and_alt_count():
    errors, _ = validate_section(
        {**T, "cat": "A", "items": [_item(1, alt_kickers=["a", "b"]), _item(3)]}, SECTION_BATCH)
    text = " ".join(errors)
    assert "missing" in text and "2" in text
    assert "extra" in text and "3" in text
    assert "alt_kickers" in text


def test_validate_section_duplicate_and_long_kicker_warning():
    errors, warnings = validate_section(
        {**T, "cat": "A", "items": [_item(1), _item(1), _item(2, kicker="one two three four five six seven eight")]},
        SECTION_BATCH)
    assert any("duplicate" in e for e in errors)
    assert any("7 words" in w for w in warnings)


def test_validate_section_summary_length():
    long = " ".join(["word"] * 26)
    mid = " ".join(["word"] * 21)
    errors, warnings = validate_section(
        {**T, "cat": "A", "items": [_item(1, summary=long), _item(2, summary=mid)]}, SECTION_BATCH)
    assert any("id 1" in e and "summary over 25 words (26)" in e for e in errors)
    assert not any("id 2" in e for e in errors)
    assert any("id 2" in w and "summary over 20 words (21)" in w for w in warnings)


def test_validate_issue_flattens_clusters():
    batch = {"kind": "edition-issue", "ids": [1, 2], "kicker_count": 3}
    ok = {"subtitle": "Hacktoberfest", "subtitle_alts": list("abcde"),
          "clusters": [{**T, "cat": "A", "items": [_item(2)]}, {**T, "cat": "B", "items": [_item(1)]}],
          "notes": []}
    assert validate_issue(ok, batch)[0] == []
    bad = dict(ok, clusters=[{**T, "cat": "A", "items": [_item(2)]}], subtitle="")
    text = " ".join(validate_issue(bad, batch)[0])
    assert "missing" in text and "subtitle" in text


def test_validate_issue_requires_titles():
    batch = {"kind": "edition-issue", "ids": [1], "kicker_count": 3}
    issue = {"subtitle": "S", "subtitle_alts": list("abcde"), "notes": [],
             "clusters": [{"cat": "A", "title": "", "title_alts": ["x", "y"],
                           "items": [_item(1)]}]}
    errors = validate_issue(issue, batch)[0]
    assert "cluster 0: empty title" in errors
    assert "cluster 0: title_alts has 2, need 3" in errors


# ── renderer ─────────────────────────────────────────────────────────────────

ISSUE = {
    "subtitle": "Software eats itself",
    "subtitle_alts": ["s1", "s2", "s3", "s4", "s5"],
    "clusters": [
        {**T, "cat": "Lead", "items": [
            _item(1, kicker="Brin it on", summary="Sergey Brin says [Argon] helps",
                   source="Business_Insider"),
            _item(2, kicker="Sparking joy?", summary="Meta releases a thing",
                   flag="possible duplicate of 1 --> check"),
        ]},
        {**T, "cat": "Tail", "items": [_item(3, kicker="", summary="No kicker here")]},
    ],
    "notes": [],
}
URLS = {1: "https://a.com/x?utm_source=z", 2: "https://b.com/(y)", 3: "https://c.com/z"}


def test_render_cluster_title():
    md = render_edition_md(ISSUE, URLS, "AI Reading")
    block = "## Tim Cooked\n- Alt titles: A | B | C\n"
    assert block in md
    assert md.index(block) < md.index("[Brin it on")


def test_render_edition_md_format():
    md = render_edition_md(ISSUE, URLS, "AI Reading for Sunday November 15")
    lines = md.splitlines()
    assert lines[0] == "# AI Reading for Sunday November 15"
    assert "Software eats itself" in lines[1]
    assert "- Alt subtitles: s1 | s2 | s3 | s4 | s5" in md
    assert "<!--" not in md
    assert ("[Brin it on: Sergey Brin says \\[Argon\\] helps](https://a.com/x)"
            " - _Business\\_Insider_") in md
    assert "[Sparking joy? Meta releases a thing](https://b.com/%28y%29) - _Business Insider_" in md
    assert "[No kicker here](https://c.com/z)" in md
    assert "- Alt kickers: a | b | c" in md
    assert "- Flag: possible duplicate of 1 --> check" in md
    assert md.count("\n---\n") == 1
    assert lines[-1] == "Follow the latest AI headlines via druce.ai on Bluesky"


def test_render_image_line_and_no_image_bullet():
    imgs = {1: ("../download/edition-images/x.jpg", ""), 2: (None, "logo")}
    lines = render_edition_md(ISSUE, URLS, "T", images=imgs).splitlines()
    i1 = next(i for i, l in enumerate(lines) if l.startswith("[Brin it on"))
    assert lines[i1 - 1] == "![](../download/edition-images/x.jpg)"
    i2 = next(i for i, l in enumerate(lines) if l.startswith("[Sparking joy?"))
    assert lines[i2 - 1] == "- No image: logo"
    i3 = next(i for i, l in enumerate(lines) if l.startswith("[No kicker"))
    assert lines[i3 - 1] == "- No image: no og:image"


def test_render_no_images_kwarg_emits_nothing():
    md = render_edition_md(ISSUE, URLS, "T")
    assert "![" not in md and "No image" not in md


# ── recent_kickers ───────────────────────────────────────────────────────────

def test_recent_kickers_parses_recent_files_only(tmp_path):
    (tmp_path / "edition-2026-11-10-070000.md").write_text(
        "# AI Reading\nsub\n\n[Brin it on: Sergey says](u) - _X_\n"
        "<!-- alts: a | b -->\n[Sparking joy? Meta](u) - _Y_\n[No kicker](u) - _Z_\n")
    (tmp_path / "edition-2026-09-01-070000.md").write_text("[Old one: x](u) - _X_\n")
    got = recent_kickers(tmp_path, datetime(2026, 11, 15), days=30)
    assert got == ["Brin it on", "Sparking joy?"]


# ── batch build / apply round trip ───────────────────────────────────────────

def test_section_and_edit_round_trip_uses_db_urls(tmp_db, tmp_path, monkeypatch):
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
             "clusters": [{**T, "cat": "Beta", "items": [_item(b)]},
                          {**T, "cat": "Alpha", "items": [_item(a)]}], "notes": ["n"]}
    edit_res = run_dir / "edition-edit-results"
    edit_res.mkdir(parents=True)
    (edit_res / "batch-000.json").write_text(json.dumps(issue))
    out_dir = tmp_path / "out"
    img = tmp_path / "download" / "edition-images" / "abc.jpg"
    import lib.edition as edition_mod
    monkeypatch.setattr(edition_mod, "fetch_item_images",
                        lambda urls, cache_dir: {a: (img, ""), b: (None, "fetch failed")})
    path, stats = apply_edit(run_dir, tmp_db, out_dir, now=datetime(2026, 11, 15, 9, 0, 0),
                             image_dir=img.parent)
    md = path.read_text()
    assert stats == {"with": 1, "total": 2}
    assert "![](../download/edition-images/abc.jpg)" in md
    assert "- No image: fetch failed" in md
    path2, stats2 = apply_edit(run_dir, tmp_db, out_dir, now=datetime(2026, 11, 15, 9, 0, 0),
                               images=False)
    assert stats2 is None and "![" not in path2.read_text()
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
    (rd / "batch-000.json").write_text(json.dumps({**T, "cat": "A", "items": [_item(1), _item(2)]}))
    ok = _check(rd / "batch-000.json")
    assert ok.returncode == 0 and ok.stdout.startswith("OK")
    (rd / "batch-000.json").write_text(json.dumps({**T, "cat": "A", "items": [_item(1)]}))
    bad = _check(rd / "batch-000.json")
    assert bad.returncode == 1 and "missing" in bad.stdout


def test_check_batch_edition_issue(tmp_path):
    bd, rd = tmp_path / "edition-edit-batches", tmp_path / "edition-edit-results"
    bd.mkdir(); rd.mkdir()
    (bd / "batch-000.json").write_text(json.dumps(
        {"kind": "edition-issue", "ids": [1], "kicker_count": 3}))
    (rd / "batch-000.json").write_text(json.dumps(
        {"subtitle": "S", "subtitle_alts": list("abcde"),
         "clusters": [{**T, "cat": "A", "items": [_item(1)]}], "notes": []}))
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
        json.dumps({**T, "cat": "Alpha", "items": [_item(a)]}))
    res = r.invoke(step.cli, ["--apply-sections", run])
    assert res.exit_code == 0, res.output

    (run_dir / "edition-edit-results").mkdir()
    (run_dir / "edition-edit-results" / "batch-000.json").write_text(json.dumps(
        {"subtitle": "Sub", "subtitle_alts": list("abcde"),
         "clusters": [{**T, "cat": "Alpha", "items": [_item(a)]}], "notes": ["read X as Y"]}))
    res = r.invoke(step.cli, ["--apply-edit", run, "--db", tmp_db, "--no-images"])
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


def test_section_batch_includes_title_for_no_content_items(tmp_db, tmp_path):
    _insert(tmp_db, url="https://wsj.com/x", title="Amazon delivery drones take off",
            short_summary="no content", summary="- no content")
    run_dir = tmp_path / "runs" / "edition-x"
    build_section_batches(selected_rows(tmp_db, SINCE), run_dir, 3, "DOCS")
    prompt = (run_dir / "edition-sections-batches" / "batch-000.md").read_text()
    assert "Amazon delivery drones take off" in prompt


# ── cat merge (edition-merge) ────────────────────────────────────────────────

def _rows(*specs):
    """specs: (id, cat, rating) → curated-row dicts like selected_rows returns."""
    return [{"id": i, "session_id": IN_SID, "url": f"https://ex.com/{i}", "title": f"T{i}",
             "short_summary": f"S{i}", "summary": "- b", "site_name": "Ex", "cat": c,
             "rating": r, "selected": 1} for i, c, r in specs]


def _merge(assign, dupes=(), notes=()):
    return {"assignments": [{"id": i, "cat": c} for i, c in assign],
            "duplicates": [{"ids": list(ids), "reason": why} for ids, why in dupes],
            "notes": list(notes)}


def test_build_merge_batch_writes_rows_and_batch(tmp_path):
    from lib.edition import MERGE_BATCHES, build_merge_batch
    rows = _rows((1, "Huang Ouster", 9.0), (2, "Other", 1.0))
    p = build_merge_batch(rows, tmp_path, kicker_count=4)
    assert p == tmp_path / MERGE_BATCHES / "batch-000.json"
    meta = json.loads(p.read_text())
    assert meta["kind"] == "edition-merge" and meta["ids"] == [1, 2]
    assert meta["kicker_count"] == 4
    assert json.loads((tmp_path / "rows.json").read_text()) == rows
    prompt = p.with_suffix(".md").read_text()
    assert "Huang Ouster" in prompt and "S2" in prompt and IN_SID in prompt


def test_validate_merge_ok_and_id_errors():
    from lib.edition import validate_merge
    batch = {"kind": "edition-merge", "ids": [1, 2, 3]}
    assert validate_merge(_merge([(1, "A"), (2, "A"), (3, "B")]), batch)[0] == []
    errors = validate_merge(_merge([(1, "A"), (1, "A"), (4, " ")]), batch)[0]
    text = " ".join(errors)
    assert "missing" in text and "extra" in text and "duplicate" in text
    assert "empty cat" in text


def test_validate_merge_bad_duplicate_groups():
    from lib.edition import validate_merge
    batch = {"kind": "edition-merge", "ids": [1, 2]}
    bad = _merge([(1, "A"), (2, "A")], dupes=[((1,), "solo"), ((1, 9), "unknown id")])
    text = " ".join(validate_merge(bad, batch)[0])
    assert "at least 2" in text and "9" in text


def test_validate_merge_cluster_count_and_other_are_warnings():
    from lib.edition import validate_merge
    ids = list(range(1, 42))
    batch = {"kind": "edition-merge", "ids": ids}
    errors, warnings = validate_merge(_merge([(i, f"C{i}") for i in ids]), batch)
    assert errors == [] and any("41 clusters" in w for w in warnings)
    errors, warnings = validate_merge(_merge([(i, "Other") for i in ids]), batch)
    assert errors == [] and any("Other" in w for w in warnings)


def test_apply_merge_regroups_rows_into_section_batches(tmp_path, tmp_db):
    from lib.edition import MERGE_RESULTS, SECTIONS_BATCHES, apply_merge, build_merge_batch
    rows = _rows((1, "Jensen Huang Downfall", 5.0), (2, "Jensen Huang Ouster", 9.0),
                 (3, "Other", 2.0), (4, "Data Centers", 3.0))
    build_merge_batch(rows, tmp_path, kicker_count=3)
    (tmp_path / MERGE_RESULTS).mkdir()
    (tmp_path / MERGE_RESULTS / "batch-000.json").write_text(json.dumps(_merge(
        [(1, "Huang and Nvidia"), (2, "Huang and Nvidia"), (3, "Data Centers"),
         (4, "Data Centers")], dupes=[((1, 2), "same ouster news")])))
    paths = apply_merge(tmp_path, docs="DOCS")
    metas = [json.loads(p.read_text()) for p in paths]
    assert [m["cat"] for m in metas] == ["Huang and Nvidia", "Data Centers"]  # max rating first
    assert metas[0]["ids"] == [2, 1] and metas[1]["ids"] == [4, 3]           # rating within
    assert all(m["kicker_count"] == 3 for m in metas)
    assert paths[0].parent == tmp_path / SECTIONS_BATCHES
    merged = json.loads((tmp_path / "merge.json").read_text())
    assert merged["duplicates"] == [{"ids": [1, 2], "reason": "same ouster news"}]


def test_apply_merge_rejects_invalid_result(tmp_path):
    from lib.edition import MERGE_RESULTS, apply_merge, build_merge_batch
    build_merge_batch(_rows((1, "A", 1.0), (2, "B", 1.0)), tmp_path, kicker_count=3)
    (tmp_path / MERGE_RESULTS).mkdir()
    (tmp_path / MERGE_RESULTS / "batch-000.json").write_text(json.dumps(_merge([(1, "A")])))
    with pytest.raises(ValueError, match="missing"):
        apply_merge(tmp_path, docs="DOCS")


def test_render_dupes_bullet_under_each_item():
    out = render_edition_md(ISSUE, {1: "https://a.com", 2: "https://b.com", 3: "https://c.com"},
                            "AI Reading", dupes={1: ['"Sparking joy?" (same launch)'],
                                                 2: ['"Brin it on" (same launch)']})
    assert out.count("- Possible duplicate:") == 2
    assert '- Possible duplicate: "Sparking joy?" (same launch)' in out
    plain = render_edition_md(ISSUE, {1: "u", 2: "u", 3: "u"}, "AI Reading")
    assert "Possible duplicate:" not in plain


def test_check_batch_edition_merge(tmp_path):
    bd, rd = tmp_path / "edition-merge-batches", tmp_path / "edition-merge-results"
    bd.mkdir(); rd.mkdir()
    (bd / "batch-000.json").write_text(json.dumps({"kind": "edition-merge", "ids": [1, 2]}))
    (rd / "batch-000.json").write_text(json.dumps(_merge([(1, "A"), (2, "A")])))
    ok = _check(rd / "batch-000.json")
    assert ok.returncode == 0 and ok.stdout.startswith("OK (edition-merge)"), ok.stdout
    (rd / "batch-000.json").write_text(json.dumps(_merge([(1, "A")])))
    bad = _check(rd / "batch-000.json")
    assert bad.returncode == 1 and "missing" in bad.stdout


def test_cli_prepare_apply_merge_and_dupe_notes(tmp_db, tmp_path, monkeypatch):
    from click.testing import CliRunner
    import lib.steps.edition as step

    a = _insert(tmp_db, url="https://a.com/1", cat="Huang Downfall", rating=5.0)
    b = _insert(tmp_db, url="https://a.com/2", cat="Huang Ouster", rating=9.0)
    monkeypatch.setattr(step, "_RUNS", tmp_path / "runs")
    monkeypatch.setattr(step, "_OUT", tmp_path / "out")
    r = CliRunner()

    res = r.invoke(step.cli, ["--prepare", "--since", SINCE, "--db", tmp_db, "--kickers", "3"])
    assert res.exit_code == 0, res.output
    run = res.output.split()[1]
    run_dir = tmp_path / "runs" / run
    assert "edition-merge-batches/batch-000.json" in res.output and "2 categories" in res.output

    res = r.invoke(step.cli, ["--apply-merge", run])  # no result yet
    assert res.exit_code == 1 and "no merge result" in res.output

    (run_dir / "edition-merge-results").mkdir()
    (run_dir / "edition-merge-results" / "batch-000.json").write_text(json.dumps(
        _merge([(a, "Huang"), (b, "Huang")], dupes=[((a, b), "same news")])))
    res = r.invoke(step.cli, ["--apply-merge", run])
    assert res.exit_code == 0, res.output
    assert "2 categories → 1 sections" in res.output and "1 possible duplicate" in res.output
    assert "2 items  Huang" in res.output

    (run_dir / "edition-sections-results").mkdir()
    (run_dir / "edition-sections-results" / "batch-000.json").write_text(json.dumps(
        {**T, "cat": "Huang", "items": [_item(b, kicker="Board stiff"), _item(a, kicker="Jen and done")]}))
    assert r.invoke(step.cli, ["--apply-sections", run]).exit_code == 0
    (run_dir / "edition-edit-results").mkdir()
    (run_dir / "edition-edit-results" / "batch-000.json").write_text(json.dumps(
        {"subtitle": "Sub", "subtitle_alts": list("abcde"), "notes": [],
         "clusters": [{**T, "cat": "Huang", "items": [_item(b, kicker="Board stiff"),
                                                       _item(a, kicker="Jen and done")]}]}))
    res = r.invoke(step.cli, ["--apply-edit", run, "--db", tmp_db, "--no-images"])
    assert res.exit_code == 0, res.output
    assert "note: possible duplicates: " in res.output and "same news" in res.output
    md = (tmp_path / "out" / "latest-edition.md").read_text()
    assert '- Possible duplicate: "Jen and done" (same news)' in md
    with sqlite3.connect(tmp_db) as conn:  # DB cat untouched
        assert conn.execute("SELECT cat FROM published_articles WHERE id=?", (a,)).fetchone()[0] \
            == "Huang Downfall"


def test_render_notes_as_bullets_under_subtitle():
    md = render_edition_md({**ISSUE, "notes": ["kept both IPO clusters"]}, URLS, "T")
    lines = md.splitlines()
    assert lines[1] == ISSUE["subtitle"]
    assert lines[2].startswith("- Alt subtitles: ")
    assert lines[3] == "- Stories: 3"
    assert lines[4] == "- Note: kept both IPO clusters"
