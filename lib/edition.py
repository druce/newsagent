"""newsagent:edition — house-style issue draft from the curated picks.

Pure functions over a run dir (runs/edition-<id>/) and the DB:

  build_section_batches → (agents write edition-sections-results/) → apply_sections
  build_edit_batch      → (agent writes edition-edit-results/)     → apply_edit

Each batch is two files: batch-NNN.md (the full rendered prompt, plain text with
real newlines so an agent can Read it in chunks) and batch-NNN.json (metadata:
ids, kicker_count — what tools/check_batch.py validates against).

The model never emits links: URLs are looked up from published_articles by id
and tracking-stripped here.
"""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from pydantic import ValidationError

from lib.prompts._dispatch_schemas import EditionIssue, EditionSection
from lib.prompts.edit_edition import EDIT_EDITION, EditEditionInput
from lib.prompts.edit_edition import render_user_prompt as _render_edit
from lib.prompts.write_edition_section import (
    WRITE_EDITION_SECTION,
    WriteEditionSectionInput,
)
from lib.prompts.write_edition_section import render_user_prompt as _render_section
from lib.utilities import strip_tracking_params

SECTIONS_BATCHES = "edition-sections-batches"
SECTIONS_RESULTS = "edition-sections-results"
EDIT_BATCHES = "edition-edit-batches"
EDIT_RESULTS = "edition-edit-results"
SUBTITLE_ALTS = 5
MAX_KICKER_WORDS = 7
FOOTER = "Follow the latest AI headlines via druce.ai on Bluesky"
SEPARATOR = "———"


# ── batch files ──────────────────────────────────────────────────────────────

def _fresh_dir(d: Path) -> Path:
    if d.exists():
        for p in d.glob("batch-*"):
            p.unlink()
    d.mkdir(parents=True, exist_ok=True)
    return d


def _write_batch(d: Path, idx: int, meta: dict, system: str, user: str) -> Path:
    stem = f"batch-{idx:03d}"
    md_path = d / f"{stem}.md"
    md_path.write_text(f"# SYSTEM\n\n{system}\n\n# USER\n\n{user}\n")
    path = d / f"{stem}.json"
    path.write_text(json.dumps({**meta, "batch_id": idx, "prompt_path": str(md_path)},
                               indent=1))
    return path


def build_section_batches(rows: list[dict], run_dir: Path, kicker_count: int,
                          docs: str) -> list[Path]:
    """One batch per cat (curate order), returns the batch-NNN.json paths."""
    by_cat: dict[str, list[dict]] = {}
    for r in rows:
        by_cat.setdefault(r.get("cat") or "Other", []).append(r)
    d = _fresh_dir(Path(run_dir) / SECTIONS_BATCHES)
    paths = []
    for idx, (cat, members) in enumerate(by_cat.items()):
        stories = [{k: r.get(k) for k in
                    ("id", "url", "title", "site_name", "short_summary", "summary", "rating")}
                   for r in members]
        inp = WriteEditionSectionInput(cat=cat, stories=stories,
                                       kicker_count=kicker_count, docs=docs)
        meta = {
            "kind": "edition-section", "cat": cat, "kicker_count": kicker_count,
            "ids": [s["id"] for s in stories],
            "facts": {str(s["id"]): s.get("short_summary") or "" for s in stories},
            "output_schema": EditionSection.model_json_schema(),
        }
        paths.append(_write_batch(d, idx, meta, WRITE_EDITION_SECTION.system_prompt,
                                  _render_section(inp)))
    return paths


# ── validation (also used by tools/check_batch.py) ───────────────────────────

def _check_items(items: list, batch: dict) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    expected = [int(i) for i in batch.get("ids", [])]
    got = [it.id for it in items]
    missing = sorted(set(expected) - set(got))
    extra = sorted(set(got) - set(expected))
    dups = sorted({i for i in got if got.count(i) > 1})
    if missing:
        errors.append(f"missing ids ({len(missing)}): {missing}")
    if extra:
        errors.append(f"extra ids ({len(extra)}): {extra}")
    if dups:
        errors.append(f"duplicate ids ({len(dups)}): {dups}")
    n = int(batch.get("kicker_count", 0))
    for it in items:
        if not it.kicker.strip():
            errors.append(f"id {it.id}: empty kicker (every item gets one)")
        if not it.summary.strip():
            errors.append(f"id {it.id}: empty summary")
        if len(it.alt_kickers) != n:
            errors.append(f"id {it.id}: alt_kickers has {len(it.alt_kickers)}, need {n}")
        words = len(it.kicker.split())
        if words > MAX_KICKER_WORDS:
            warnings.append(f"id {it.id}: kicker over {MAX_KICKER_WORDS} words ({words})")
    return errors, warnings


def validate_section(result: dict, batch: dict) -> tuple[list[str], list[str]]:
    try:
        sec = EditionSection.model_validate(result)
    except ValidationError as exc:
        return [f"schema: {exc.errors()[:3]}"], []
    return _check_items(sec.items, batch)


def validate_issue(result: dict, batch: dict) -> tuple[list[str], list[str]]:
    try:
        issue = EditionIssue.model_validate(result)
    except ValidationError as exc:
        return [f"schema: {exc.errors()[:3]}"], []
    errors, warnings = _check_items([it for c in issue.clusters for it in c.items], batch)
    if not issue.subtitle.strip():
        errors.append("empty subtitle")
    if len(issue.subtitle_alts) != SUBTITLE_ALTS:
        errors.append(f"subtitle_alts has {len(issue.subtitle_alts)}, need {SUBTITLE_ALTS}")
    n = batch.get("kicker_count")
    for i, c in enumerate(issue.clusters):
        if not c.title.strip():
            errors.append(f"cluster {i}: empty title")
        if n is not None and len(c.title_alts) != n:
            errors.append(f"cluster {i}: title_alts has {len(c.title_alts)}, need {n}")
    return errors, warnings


# ── apply ────────────────────────────────────────────────────────────────────

def apply_sections(run_dir: Path) -> tuple[list[dict], list[str]]:
    """Validate every section result; on success write run_dir/sections.json."""
    run_dir = Path(run_dir)
    sections: list[dict] = []
    problems: list[str] = []
    for meta_path in sorted((run_dir / SECTIONS_BATCHES).glob("batch-*.json")):
        batch = json.loads(meta_path.read_text())
        res_path = run_dir / SECTIONS_RESULTS / meta_path.name
        if not res_path.exists():
            problems.append(f"{meta_path.stem}: no result ({batch['cat']})")
            continue
        try:
            result = json.loads(res_path.read_text())
        except json.JSONDecodeError as exc:
            problems.append(f"{meta_path.stem}: invalid JSON: {exc}")
            continue
        errors, _ = validate_section(result, batch)
        if errors:
            problems.append(f"{meta_path.stem}: " + "; ".join(errors))
            continue
        sec = EditionSection.model_validate(result).model_dump()
        for it in sec["items"]:
            it["facts"] = batch["facts"].get(str(it["id"]), "")
        sections.append(sec)
    if not problems:
        (run_dir / "sections.json").write_text(json.dumps(sections, indent=1))
    return sections, problems


def build_edit_batch(run_dir: Path, docs: str, recent: list[str]) -> Path:
    run_dir = Path(run_dir)
    sections = json.loads((run_dir / "sections.json").read_text())
    kicker_count = json.loads(
        next((run_dir / SECTIONS_BATCHES).glob("batch-*.json")).read_text()
    )["kicker_count"]
    inp = EditEditionInput(sections=sections, kicker_count=kicker_count,
                           recent_kickers=recent, docs=docs)
    meta = {
        "kind": "edition-issue", "kicker_count": kicker_count,
        "ids": [it["id"] for s in sections for it in s["items"]],
        "output_schema": EditionIssue.model_json_schema(),
    }
    d = _fresh_dir(run_dir / EDIT_BATCHES)
    return _write_batch(d, 0, meta, EDIT_EDITION.system_prompt, _render_edit(inp))


def _urls_by_id(db_path: str, ids: list[int]) -> dict[int, str]:
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            f"SELECT id, url FROM published_articles WHERE id IN "
            f"({','.join('?' for _ in ids)})", ids,
        ).fetchall()
    return {i: u for i, u in rows}


def apply_edit(run_dir: Path, db_path: str, out_dir: Path,
               now: datetime | None = None) -> Path:
    """Validate the final edit, render markdown to out/edition-<stamp>.md."""
    run_dir, out_dir = Path(run_dir), Path(out_dir)
    now = now or datetime.now()
    batch = json.loads((run_dir / EDIT_BATCHES / "batch-000.json").read_text())
    result = json.loads((run_dir / EDIT_RESULTS / "batch-000.json").read_text())
    errors, _ = validate_issue(result, batch)
    if errors:
        raise ValueError("final edit failed validation: " + "; ".join(errors))
    issue = EditionIssue.model_validate(result).model_dump()
    urls = _urls_by_id(db_path, [int(i) for i in batch["ids"]])
    md = render_edition_md(issue, urls, now.strftime("AI Reading for %A %B %-d"))

    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"edition-{now:%Y-%m-%d-%H%M%S}.md"
    path.write_text(md)
    latest = out_dir / "latest-edition.md"
    if latest.is_symlink() or latest.exists():
        latest.unlink()
    try:
        latest.symlink_to(path.name)
    except OSError:
        latest.write_text(md)
    return path


# ── rendering ────────────────────────────────────────────────────────────────

def _link_text(s: str) -> str:
    return s.replace("[", "\\[").replace("]", "\\]")


def _href(url: str) -> str:
    return url.replace(" ", "%20").replace("(", "%28").replace(")", "%29")


def _comment(s: str) -> str:
    return s.replace("--", "- -")


def _item_line(it: dict, url: str) -> str:
    kicker = it["kicker"].strip()
    summary = it["summary"].strip().rstrip(".")
    if not kicker:
        text = summary
    elif kicker.endswith("?"):
        text = f"{kicker} {summary}"
    else:
        text = f"{kicker.rstrip(':')}: {summary}"
    source = it["source"].strip().replace("_", "\\_")
    return f"[{_link_text(text)}]({_href(url)}) - _{source}_"


def render_edition_md(issue: dict, urls: dict[int, str], title: str) -> str:
    out = [f"# {title}", issue["subtitle"].strip(),
           f"<!-- subtitle alts: {_comment(' | '.join(issue['subtitle_alts']))} -->"]
    for note in issue.get("notes") or []:
        out.append(f"<!-- note: {_comment(note)} -->")
    blocks = []
    for cluster in issue["clusters"]:
        lines = []
        if (cluster.get("title") or "").strip():
            lines.append(f"## {cluster['title'].strip()}")
            lines.append(f"<!-- title alts: {_comment(' | '.join(cluster.get('title_alts') or []))} -->")
            lines.append("")
        for it in cluster["items"]:
            url = strip_tracking_params(urls.get(int(it["id"])) or "#")
            lines.append(_item_line(it, url))
            lines.append(f"<!-- alts: {_comment(' | '.join(it['alt_kickers']))} -->")
            if it.get("flag"):
                lines.append(f"<!-- flag: {_comment(it['flag'])} -->")
            lines.append("")
        blocks.append("\n".join(lines).rstrip("\n"))
    out.append("")
    out.append(f"\n\n{SEPARATOR}\n\n".join(blocks))
    out.append("")
    out.append(FOOTER)
    return "\n".join(out)


# ── recent kickers ───────────────────────────────────────────────────────────

_STAMP_RE = re.compile(r"edition-(\d{4}-\d{2}-\d{2})-\d{6}\.md$")
_KICKER_RE = re.compile(r"^\[([^\]]+?)(: |\? )")


def recent_kickers(out_dir: Path, now: datetime, days: int = 30) -> list[str]:
    """Kickers from out/edition-*.md files of the last `days` days, oldest first."""
    seen: list[str] = []
    for path in sorted(Path(out_dir).glob("edition-*.md")):
        m = _STAMP_RE.search(path.name)
        if not m or now - datetime.strptime(m.group(1), "%Y-%m-%d") > timedelta(days=days):
            continue
        for line in path.read_text().splitlines():
            km = _KICKER_RE.match(line)
            if km:
                k = km.group(1) + ("?" if km.group(2).startswith("?") else "")
                if k not in seen:
                    seen.append(k)
    return seen
