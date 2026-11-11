"""Phase A: archive every historical prompt input/output pair.

Walks runs/<SID>/<step>-batches + -results, runs/<SID>/rewrite-work, and the
rate/gather/summarize checkpoints in agent_state (scalar fields only — never
embeddings), and writes evals/archive/<name>.jsonl.gz plus report.json.

Read-only with respect to runs/ and the DB. This is the insurance copy that
must exist (and be verified) before any truncation.

    .venv/bin/python -m evals.extract.archive [--runs runs] [--db newsletter_agent.db] [--out evals/archive]
"""
from __future__ import annotations

import gzip
import hashlib
import json
import shutil
import sqlite3
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path
from typing import Iterable, Iterator

import click

# step dir prefix -> (prompt name, input keys copied from the batch file)
STEP_PROMPTS: dict[str, tuple[str, tuple[str, ...]]] = {
    "filter": ("filter_urls", ("items",)),
    "summarize": ("extract_summaries", ("items",)),
    "crossdedupe": ("same_story", ("pairs",)),
    "coverage": ("same_story_sameday", ("pairs",)),
    "cluster": ("name_topic_batch", ("clusters",)),
    "select-repechage": ("extract_noise_clusters", ("headlines",)),
    "select-consolidate": ("consolidate_cluster_names", ("clusters",)),
    "select-reassign": ("reassign_to_clusters", ("headlines", "clusters")),
    "sitename": ("sitename", ("items",)),
    "draft": ("write_section", ()),  # special-cased: section_title + stories
}

RATE_FIELDS = (
    "title", "summary", "short_summary", "source", "site_name", "url",
    "final_url", "published", "quality_low", "on_topic", "importance",
    "bt_z", "bt_score", "reputation", "coverage_count", "recency_score",
    "rating",
)
GATHER_FIELDS = ("title", "source", "url", "published")
SUMMARIZE_FIELDS = ("title", "source", "site_name", "url", "final_url", "short_summary")


def sha(s: str) -> str:
    return hashlib.sha256((s or "").encode()).hexdigest()[:12]


def parse_items_from_user_prompt(user_prompt: str) -> list:
    """The rendered user_prompt is a header followed by a JSON array of items."""
    start = user_prompt.index("\n[") + 1
    obj, _ = json.JSONDecoder().raw_decode(user_prompt[start:])
    return obj


def _load(path: Path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def unknown_batch_dirs(run_dir: Path) -> list[str]:
    known = {f"{s}-batches" for s in STEP_PROMPTS} | {"rewrite-batches"}
    return sorted(p.name for p in run_dir.glob("*-batches") if p.is_dir() and p.name not in known)


def batch_records(run_dir: Path) -> Iterator[dict]:
    sid = run_dir.name
    for step, (prompt, keys) in STEP_PROMPTS.items():
        bdir = run_dir / f"{step}-batches"
        if not bdir.is_dir():
            continue
        for bpath in sorted(bdir.glob("batch-*.json")):
            b = _load(bpath)
            if b is None:
                continue
            if step == "draft":
                inp = {"section_title": b.get("cat"), "stories": b.get("stories", [])}
                sys_p, user_p = b.get("write_system_prompt", ""), b.get("write_user_prompt", "")
                extra = {k: b.get(k) for k in ("critique_system_prompt", "improve_system_prompt",
                                                "max_edits", "accept_threshold")}
            else:
                inp = {k: b.get(k) for k in keys}
                sys_p, user_p = b.get("system_prompt", ""), b.get("user_prompt", "")
                extra = {}
                if step == "summarize" and inp["items"] and "text" not in inp["items"][0]:
                    try:
                        inp["items"] = parse_items_from_user_prompt(user_p)
                    except (ValueError, json.JSONDecodeError):
                        pass
            rpath = run_dir / f"{step}-results" / bpath.name
            yield {
                "prompt": prompt, "step": step, "sid": sid, "batch": bpath.stem,
                "input": inp, "output": _load(rpath) if rpath.exists() else None,
                "system_prompt_hash": sha(sys_p), "system_prompt": sys_p,
                "user_prompt_hash": sha(user_p), **extra,
            }


def rewrite_records(run_dir: Path) -> list[dict]:
    """critique-i judges the draft before it (draft-init, then draft-(i-1));
    improve pass i turns (that draft, critique-i) into draft-i; the title is
    generated from the last draft."""
    w = run_dir / "rewrite-work"
    if not w.is_dir():
        return []
    sid = run_dir.name

    def read(name):
        p = w / name
        return p.read_text() if p.exists() else None

    base = {"sid": sid, "step": "rewrite"}
    recs: list[dict] = []
    cur = read("draft-init.md")
    if cur is None:
        return []
    i = 0
    while (crit := read(f"critique-{i}.md")) is not None:
        recs.append({**base, "prompt": "critique_newsletter", "batch": f"critique-{i}",
                     "input": {"newsletter_markdown": cur}, "output": crit})
        nxt = read(f"draft-{i}.md")
        if nxt is None:
            break
        recs.append({**base, "prompt": "improve_newsletter", "batch": f"improve-{i}",
                     "input": {"newsletter_markdown": cur, "critique": crit}, "output": nxt})
        cur = nxt
        i += 1
    title = read("title.txt")
    if title is not None:
        recs.append({**base, "prompt": "generate_newsletter_title", "batch": "title",
                     "input": {"newsletter_markdown": cur}, "output": title.strip()})
    return recs


def checkpoint_items(db: Path, step: str, fields: Iterable[str]) -> list[dict]:
    """One record per session: scalar fields of each headline_data item at
    `step`, extracted in SQL so embeddings are never loaded into Python."""
    fields = list(fields)
    cols = ", ".join(f"json_extract(j.value, '$.{f}')" for f in fields)
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        rows = con.execute(
            f"select s.session_id, j.key, {cols} from agent_state s, "
            f"json_each(s.state_data, '$.headline_data') j "
            f"where s.step_name = ? order by s.session_id, j.key", (step,))
        by_sid: dict[str, list] = defaultdict(list)
        for sid, key, *vals in rows:
            item = {"id": str(key)}
            item.update(zip(fields, vals))
            by_sid[sid].append(item)
    finally:
        con.close()
    return [{"sid": sid, "step": step, "items": items} for sid, items in by_sid.items()]


def _table(db: Path, sql: str) -> list[dict]:
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in con.execute(sql)]
    finally:
        con.close()


def write_jsonl_gz(path: Path, records: Iterable[dict]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with gzip.open(path, "wt", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
            n += 1
    return n


def read_jsonl_gz(path: Path) -> Iterator[dict]:
    with gzip.open(path, "rt", encoding="utf-8") as f:
        for line in f:
            yield json.loads(line)


@click.command()
@click.option("--runs", "runs_dir", default="runs", type=click.Path(path_type=Path))
@click.option("--db", default="newsletter_agent.db", type=click.Path(path_type=Path))
@click.option("--out", "out_dir", default="evals/archive", type=click.Path(path_type=Path))
def cli(runs_dir: Path, db: Path, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    run_dirs = sorted(p for p in runs_dir.iterdir() if p.is_dir() and p.name[:4].isdigit())

    by_prompt: dict[str, list[dict]] = defaultdict(list)
    unknown: Counter = Counter()
    for rd in run_dirs:
        for rec in batch_records(rd):
            by_prompt[rec["prompt"]].append(rec)
        for rec in rewrite_records(rd):
            by_prompt[rec["prompt"]].append(rec)
        unknown.update(unknown_batch_dirs(rd))

    report: dict = {"prompts": {}, "unknown_batch_dirs": dict(unknown)}
    for prompt, recs in sorted(by_prompt.items()):
        write_jsonl_gz(out_dir / f"{prompt}.jsonl.gz", recs)
        report["prompts"][prompt] = {
            "calls": len(recs),
            "with_output": sum(r["output"] is not None for r in recs),
            "sessions": len({r["sid"] for r in recs}),
            "system_prompt_versions": len({r.get("system_prompt_hash") for r in recs}),
            "first": min(r["sid"] for r in recs), "last": max(r["sid"] for r in recs),
        }
        click.echo(f"{prompt:28s} {report['prompts'][prompt]}")

    # on-disk cross-check: every batch file under a known step must be archived
    disk = Counter()
    for step, (prompt, _) in STEP_PROMPTS.items():
        disk[prompt] += sum(1 for _ in runs_dir.glob(f"[0-9]*/{step}-batches/batch-*.json"))
    report["disk_batch_files"] = dict(disk)
    mismatch = {p: (n, report["prompts"].get(p, {}).get("calls", 0))
                for p, n in disk.items() if n != report["prompts"].get(p, {}).get("calls", 0)}
    report["disk_mismatch"] = mismatch

    for step, fields, name in [("rate", RATE_FIELDS, "ckpt_rate"),
                               ("gather", GATHER_FIELDS, "ckpt_gather"),
                               ("summarize", SUMMARIZE_FIELDS, "ckpt_summarize")]:
        recs = checkpoint_items(db, step, fields)
        n = write_jsonl_gz(out_dir / f"{name}.jsonl.gz", recs)
        report[name] = {"sessions": n, "items": sum(len(r["items"]) for r in recs)}
        click.echo(f"{name:28s} {report[name]}")

    report["newsletters"] = write_jsonl_gz(
        out_dir / "newsletters.jsonl.gz",
        _table(db, "select session_id, title from newsletters"))
    report["sites"] = write_jsonl_gz(
        out_dir / "sites.jsonl.gz", _table(db, "select domain, name from sites"))

    bsky = runs_dir / "bsky-druce.ai"
    if bsky.is_dir():
        dest = out_dir / "bsky" / date.today().isoformat()
        dest.mkdir(parents=True, exist_ok=True)
        for p in bsky.glob("*.json"):
            shutil.copy2(p, dest / p.name)
        report["bsky_snapshot"] = str(dest)

    (out_dir / "report.json").write_text(json.dumps(report, indent=2))
    click.echo(f"disk mismatch: {mismatch or 'none'}")
    click.echo(f"unknown batch dirs: {dict(unknown) or 'none'}")


if __name__ == "__main__":
    cli()
