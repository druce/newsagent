"""newsagent:edition — house-style issue draft from the curated picks.

Pure functions over a run dir (runs/edition-<id>/) and the DB:

  build_merge_batch     → (agent writes edition-merge-results/)    → apply_merge
                                     (apply_merge calls build_section_batches)
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
import os
import re
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from pydantic import ValidationError

from lib.edition_images import DEFAULT_IMAGE_DIR, fetch_item_images

from lib.prompts._dispatch_schemas import EditionIssue, EditionMerge, EditionSection
from lib.prompts.edit_edition import EDIT_EDITION, EditEditionInput
from lib.prompts.edit_edition import render_user_prompt as _render_edit
from lib.prompts.merge_edition_cats import MERGE_EDITION_CATS, MergeEditionCatsInput
from lib.prompts.merge_edition_cats import render_user_prompt as _render_merge
from lib.prompts.write_edition_section import (
    WRITE_EDITION_SECTION,
    WriteEditionSectionInput,
)
from lib.prompts.write_edition_section import render_user_prompt as _render_section
from lib.utilities import strip_tracking_params

MERGE_BATCHES = "edition-merge-batches"
MERGE_RESULTS = "edition-merge-results"
MERGE_CLUSTERS_WARN = (12, 30)  # outside this range → warning (target ~15–25)
MERGE_OTHER_WARN = 3            # more "Other" stories than this → warning
SECTIONS_BATCHES = "edition-sections-batches"
SECTIONS_RESULTS = "edition-sections-results"
EDIT_BATCHES = "edition-edit-batches"
EDIT_RESULTS = "edition-edit-results"
SUBTITLE_ALTS = 5
MAX_KICKER_WORDS = 7
# Item summary (the headline): aim <= 20 words, hard cap 25 — same cap as the pipeline's
# write_section headlines. Over the cap fails check_batch so the agent rewrites it.
TARGET_SUMMARY_WORDS = 20
MAX_SUMMARY_WORDS = 25
FOOTER = "Follow the latest AI headlines via druce.ai on Bluesky"
SEPARATOR = "---"  # markdown horizontal rule (content break)


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


def build_merge_batch(rows: list[dict], run_dir: Path, kicker_count: int) -> Path:
    """Freeze the curated rows (run_dir/rows.json) and write the one merge batch."""
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "rows.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1))
    stories = [{k: r.get(k) for k in
                ("id", "cat", "session_id", "title", "short_summary", "rating")}
               for r in rows]
    meta = {
        "kind": "edition-merge", "kicker_count": kicker_count,
        "ids": [r["id"] for r in rows],
        "output_schema": EditionMerge.model_json_schema(),
    }
    d = _fresh_dir(run_dir / MERGE_BATCHES)
    return _write_batch(d, 0, meta, MERGE_EDITION_CATS.system_prompt,
                        _render_merge(MergeEditionCatsInput(stories=stories)))


# ── validation (also used by tools/check_batch.py) ───────────────────────────

def _id_errors(got: list[int], batch: dict) -> list[str]:
    """Every batch id exactly once: missing / extra / duplicate."""
    errors: list[str] = []
    expected = [int(i) for i in batch.get("ids", [])]
    missing = sorted(set(expected) - set(got))
    extra = sorted(set(got) - set(expected))
    dups = sorted({i for i in got if got.count(i) > 1})
    if missing:
        errors.append(f"missing ids ({len(missing)}): {missing}")
    if extra:
        errors.append(f"extra ids ({len(extra)}): {extra}")
    if dups:
        errors.append(f"duplicate ids ({len(dups)}): {dups}")
    return errors


def _check_items(items: list, batch: dict) -> tuple[list[str], list[str]]:
    errors = _id_errors([it.id for it in items], batch)
    warnings: list[str] = []
    n = int(batch.get("kicker_count", 0))
    for it in items:
        if not it.kicker.strip():
            errors.append(f"id {it.id}: empty kicker (every item gets one)")
        if not it.summary.strip():
            errors.append(f"id {it.id}: empty summary")
        if len(it.alt_kickers) != n:
            errors.append(f"id {it.id}: alt_kickers has {len(it.alt_kickers)}, need {n}")
        sw = len(it.summary.split())
        if sw > MAX_SUMMARY_WORDS:
            errors.append(f"id {it.id}: summary over {MAX_SUMMARY_WORDS} words ({sw}); "
                          "cut to one fact, drop the second clause")
        elif sw > TARGET_SUMMARY_WORDS:
            warnings.append(f"id {it.id}: summary over {TARGET_SUMMARY_WORDS} words ({sw})")
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


def validate_merge(result: dict, batch: dict) -> tuple[list[str], list[str]]:
    try:
        merge = EditionMerge.model_validate(result)
    except ValidationError as exc:
        return [f"schema: {exc.errors()[:3]}"], []
    errors = _id_errors([a.id for a in merge.assignments], batch)
    warnings: list[str] = []
    empty = [a.id for a in merge.assignments if not a.cat.strip()]
    if empty:
        errors.append(f"empty cat for ids: {empty}")
    known = {int(i) for i in batch.get("ids", [])}
    for n, d in enumerate(merge.duplicates):
        if len(set(d.ids)) < 2:
            errors.append(f"duplicates[{n}]: needs at least 2 distinct ids, got {d.ids}")
        unknown = sorted(set(d.ids) - known)
        if unknown:
            errors.append(f"duplicates[{n}]: unknown ids {unknown}")
    clusters = {a.cat.strip() for a in merge.assignments if a.cat.strip()}
    lo, hi = MERGE_CLUSTERS_WARN
    if not lo <= len(clusters) <= hi and len(known) > lo:
        warnings.append(f"{len(clusters)} clusters (target ~15–25)")
    other = sum(1 for a in merge.assignments if a.cat.strip().lower() == "other")
    if other > MERGE_OTHER_WARN:
        warnings.append(f"{other} stories left in Other")
    return errors, warnings


# ── apply ────────────────────────────────────────────────────────────────────

def apply_merge(run_dir: Path, docs: str) -> list[Path]:
    """Validate the merge result, write run_dir/merge.json, and build the section
    batches from the frozen rows re-grouped by merged cat: clusters by their best
    rating, stories by rating within. Raises ValueError if the result is invalid."""
    run_dir = Path(run_dir)
    batch = json.loads((run_dir / MERGE_BATCHES / "batch-000.json").read_text())
    result = json.loads((run_dir / MERGE_RESULTS / "batch-000.json").read_text())
    errors, _ = validate_merge(result, batch)
    if errors:
        raise ValueError("merge result failed validation: " + "; ".join(errors))
    merge = EditionMerge.model_validate(result).model_dump()
    (run_dir / "merge.json").write_text(json.dumps(merge, ensure_ascii=False, indent=1))

    cat_of = {a["id"]: a["cat"].strip() for a in merge["assignments"]}
    rows = [{**r, "cat": cat_of[r["id"]]}
            for r in json.loads((run_dir / "rows.json").read_text())]

    def score(r: dict) -> float:
        return r["rating"] if r.get("rating") is not None else float("-inf")

    best: dict[str, float] = {}
    for r in rows:
        best[r["cat"]] = max(best.get(r["cat"], float("-inf")), score(r))
    rows.sort(key=lambda r: (-best[r["cat"]], r["cat"], -score(r), r["id"]))
    return build_section_batches(rows, run_dir, int(batch["kicker_count"]), docs)


def merge_duplicates(run_dir: Path, issue: dict) -> tuple[dict[int, list[str]], list[str]]:
    """The merge step's duplicate groups, phrased with the final kickers.

    Returns ({item id: ['"<other kicker>" (reason)', ...]}, [one note per group]).
    Empty when the run had no merge step (--prepare-sections)."""
    path = Path(run_dir) / "merge.json"
    if not path.exists():
        return {}, []
    kicker = {int(it["id"]): (it.get("kicker") or f"id {it['id']}")
              for c in issue["clusters"] for it in c["items"]}
    per_item: dict[int, list[str]] = {}
    notes: list[str] = []
    for d in json.loads(path.read_text()).get("duplicates") or []:
        ids = [int(i) for i in d["ids"] if int(i) in kicker]
        if len(ids) < 2:
            continue
        for i in ids:
            per_item.setdefault(i, []).extend(
                f'"{kicker[o]}" ({d["reason"]})' for o in ids if o != i)
        notes.append("possible duplicates: "
                     + " / ".join(f'"{kicker[i]}"' for i in ids) + f' ({d["reason"]})')
    return per_item, notes


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
               now: datetime | None = None, images: bool = True,
               image_dir: Path = DEFAULT_IMAGE_DIR) -> tuple[Path, dict | None]:
    """Validate the final edit, render markdown to out/edition-<stamp>.md.

    With `images`, fetches a candidate og:image for every item into `image_dir`
    and returns stats {"with", "total"}; image paths in the md are relative to out_dir.
    """
    run_dir, out_dir = Path(run_dir), Path(out_dir)
    now = now or datetime.now()
    batch = json.loads((run_dir / EDIT_BATCHES / "batch-000.json").read_text())
    result = json.loads((run_dir / EDIT_RESULTS / "batch-000.json").read_text())
    errors, _ = validate_issue(result, batch)
    if errors:
        raise ValueError("final edit failed validation: " + "; ".join(errors))
    issue = EditionIssue.model_validate(result).model_dump()
    urls = _urls_by_id(db_path, [int(i) for i in batch["ids"]])
    rel_images, stats = None, None
    if images:
        fetched = fetch_item_images(urls, cache_dir=image_dir)
        rel_images = {i: (os.path.relpath(p, out_dir) if p else None, reason)
                      for i, (p, reason) in fetched.items()}
        stats = {"with": sum(1 for p, _ in fetched.values() if p), "total": len(urls)}
    dupes, _ = merge_duplicates(run_dir, issue)
    md = render_edition_md(issue, urls, now.strftime("AI Reading for %A %B %-d"),
                           images=rel_images, dupes=dupes)

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
    return path, stats


# ── rendering ────────────────────────────────────────────────────────────────

def _link_text(s: str) -> str:
    return s.replace("[", "\\[").replace("]", "\\]")


def _href(url: str) -> str:
    return url.replace(" ", "%20").replace("(", "%28").replace(")", "%29")


def _note(label: str, text: str) -> str:
    """One editor-note bullet, e.g. `- Alt kickers: a | b | c`. Visible in the md and
    imported into beehiiv as a bulleted list, so the editor reads them there and
    deletes them before sending."""
    return f"- {label}: " + " ".join(text.split())


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


def render_edition_md(issue: dict, urls: dict[int, str], title: str,
                      images: dict[int, tuple[str | None, str]] | None = None,
                      dupes: dict[int, list[str]] | None = None) -> str:
    """`images` maps item id → (path relative to the md file, "") or (None, reason).
    None (the default) emits no image lines at all. `dupes` maps item id → the
    merge step's possible-duplicate notes, one bullet each. Editor notes (alternatives,
    flags, missing images, merge notes) are `- Label: text` bullets, not comments."""
    out = [f"# {title}", issue["subtitle"].strip(),
           _note("Alt subtitles", " | ".join(issue["subtitle_alts"]))]
    for note in issue.get("notes") or []:
        out.append(_note("Note", note))
    blocks = []
    for cluster in issue["clusters"]:
        lines = []
        if (cluster.get("title") or "").strip():
            lines.append(f"## {cluster['title'].strip()}")
            lines.append(_note("Alt titles", " | ".join(cluster.get("title_alts") or [])))
            lines.append("")
        for it in cluster["items"]:
            url = strip_tracking_params(urls.get(int(it["id"])) or "#")
            if images is not None:
                img, reason = images.get(int(it["id"]), (None, "no og:image"))
                lines.append(f"![]({_href(img)})" if img
                             else _note("No image", reason))
            lines.append(_item_line(it, url))
            lines.append(_note("Alt kickers", " | ".join(it["alt_kickers"])))
            if it.get("flag"):
                lines.append(_note("Flag", it["flag"]))
            for d in (dupes or {}).get(int(it["id"]), []):
                lines.append(_note("Possible duplicate", d))
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
