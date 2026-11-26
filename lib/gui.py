"""Read-only data layer for the pipeline GUI (lib/steps/gui.py).

Pure functions over the SQLite agent_state checkpoints, runs/<SID>/ artifacts,
skills/<step>/SKILL.md and lib/steps/<step>.py. No HTTP here, so it is unit-
testable; the server just JSON-encodes what these return.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

import markdown as _markdown

from lib.state import WORKFLOW_STEPS
from lib.steplog import log_path, running_marker

STEP_IDS = [s[0] for s in WORKFLOW_STEPS]
REPO_ROOT = Path(__file__).resolve().parent.parent

_LOG_FIRST_READ_MAX = 200_000  # bytes returned when a log is first opened (tail)
_STATE_ROW_LIMIT = 500
_TABLE_COLUMNS = ["title", "headline", "site_name", "cat", "cluster_name", "rating",
                  "coverage_count", "is_ai", "short_summary", "url", "link"]
_SID_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})(?:-(\d{2})-(\d{2})-(\d{2}))?$")


class GuiError(ValueError):
    """Bad request (unknown step, missing session, ...)."""


def _check_step(step: str) -> None:
    if step not in STEP_IDS:
        raise GuiError(f"unknown step {step!r}")


def _check_sid(sid: str) -> None:
    # Session ids become path components under runs/; refuse traversal.
    if not sid or "/" in sid or "\\" in sid or sid.startswith("."):
        raise GuiError(f"bad session id {sid!r}")


def session_start(sid: str) -> str | None:
    m = _SID_RE.match(sid)
    if not m:
        return None
    y, mo, d, h, mi, s = m.groups()
    if h is None:
        return f"{y}-{mo}-{d}"
    return f"{y}-{mo}-{d}T{h}:{mi}:{s}"


# ---------------------------------------------------------------- sessions

def list_sessions(db_path: str, n: int = 30) -> list[dict[str, Any]]:
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT session_id, MAX(updated_at) AS u, COUNT(*) FROM agent_state "
            "GROUP BY session_id ORDER BY u DESC LIMIT ?", (n,)).fetchall()
    return [{"session_id": r[0], "updated_at": r[1], "checkpoints": r[2]} for r in rows]


_cache_lock = threading.Lock()
_steps_cache: dict[tuple[str, str], tuple[str, list[dict]]] = {}


def _latest_steps(db_path: str, sid: str) -> list[dict] | None:
    """`steps` list from the session's newest checkpoint, cached on updated_at
    (checkpoints are MBs of JSON; the page polls every 2 s)."""
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT updated_at FROM agent_state WHERE session_id=? "
            "ORDER BY updated_at DESC LIMIT 1", (sid,)).fetchone()
        if row is None:
            return None
        key = (db_path, sid)
        with _cache_lock:
            hit = _steps_cache.get(key)
        if hit and hit[0] == row[0]:
            return hit[1]
        data = conn.execute(
            "SELECT state_data FROM agent_state WHERE session_id=? "
            "ORDER BY updated_at DESC LIMIT 1", (sid,)).fetchone()[0]
    steps = json.loads(data).get("steps", [])
    with _cache_lock:
        _steps_cache[key] = (row[0], steps)
    return steps


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _running(runs_root: Path, sid: str, step: str) -> bool:
    marker = running_marker(runs_root, sid, step)
    try:
        pid = int(marker.read_text().strip())
    except (FileNotFoundError, ValueError):
        return False
    return _pid_alive(pid)


def _duration(start: str | None, end: str | None) -> float | None:
    if not start or not end:
        return None
    try:
        return (datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds()
    except ValueError:
        return None


_HEADER_RE = re.compile(rb"^=== \S+ (\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)", re.M)
_RUN_GAP_S = 3600.0  # invocations further apart than this belong to separate attempts
_runs_cache: dict[str, tuple[tuple[int, int], list[datetime]]] = {}


def _log_runs(path: Path) -> list[datetime]:
    """Start time of every invocation recorded in a step log (its `=== ` headers)."""
    try:
        st = path.stat()
    except FileNotFoundError:
        return []
    key = (st.st_mtime_ns, st.st_size)
    hit = _runs_cache.get(str(path))
    if hit and hit[0] == key:
        return hit[1]
    runs = [datetime.fromisoformat(m.decode()) for m in _HEADER_RE.findall(path.read_bytes())]
    _runs_cache[str(path)] = (key, runs)
    return runs


def _parse(ts: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat(ts) if ts else None
    except ValueError:
        return None


def step_span_start(runs: list[datetime], prev_end: datetime | None,
                    end: datetime | None) -> tuple[datetime | None, int]:
    """When the step's final attempt really began, and how many invocations it took.

    Agent-dispatched steps run as prepare → (agents) → apply invocations, and the
    checkpoint's started_at is overwritten by the last one, so it under-reports.
    Take the step's log invocations after the previous step finished (and not after
    this one completed), keep the last contiguous group (gaps < 1 h), and return
    its first time.
    """
    cand = sorted(t for t in runs
                  if (prev_end is None or (t - prev_end).total_seconds() >= -1)
                  and (end is None or t <= end))
    if not cand:
        return None, 0
    i = len(cand) - 1
    while i > 0 and (cand[i] - cand[i - 1]).total_seconds() < _RUN_GAP_S:
        i -= 1
    return cand[i], len(cand) - i


def session_overview(db_path: str, sid: str, runs_root: Path | str = "runs") -> dict[str, Any]:
    _check_sid(sid)
    runs_root = Path(runs_root)
    saved = _latest_steps(db_path, sid)
    if saved is None and not (runs_root / sid).is_dir():
        raise GuiError(f"no such session {sid!r}")
    by_id = {s.get("id"): s for s in (saved or [])}
    steps = []
    prev_end: datetime | None = None
    for step_id, name, desc in WORKFLOW_STEPS:
        s = by_id.get(step_id, {})
        status = s.get("status", "not_started")
        if status != "complete" and _running(runs_root, sid, step_id):
            status = "running"
        lp = log_path(runs_root, sid, step_id)
        started, completed = s.get("started_at"), s.get("completed_at")
        end = _parse(completed) if status == "complete" else None
        log_start, invocations = step_span_start(_log_runs(lp), prev_end, end)
        if log_start is not None and status in ("complete", "running", "error"):
            cp_start = _parse(started)
            if cp_start is None or log_start < cp_start:
                started = log_start.isoformat()
        steps.append({
            "id": step_id, "name": name, "description": desc,
            "status": status,
            "status_message": s.get("status_message", ""),
            "error_message": s.get("error_message", ""),
            "started_at": started,
            "completed_at": completed,
            "duration_s": _duration(started, completed) if status == "complete" else None,
            "invocations": invocations,
            "has_log": lp.exists(),
        })
        if end is not None:
            prev_end = end
    n_complete = sum(1 for s in steps if s["status"] == "complete")
    through = None
    for s in steps:
        if s["status"] != "complete":
            break
        through = s["id"]
    running = next((s["id"] for s in steps if s["status"] == "running"), None)
    errors = [s["id"] for s in steps if s["status"] == "error"]
    next_ready = next((s["id"] for s in steps if s["status"] != "complete"), None)
    if n_complete == len(steps):
        overall = "complete"
    elif errors:
        overall = "error"
    elif n_complete == 0 and running is None:
        overall = "not_started"
    else:
        overall = "in_progress"
    return {
        "session_id": sid,
        "started": session_start(sid),
        "status": overall,
        "n_complete": n_complete,
        "n_steps": len(steps),
        "through": through,
        "running": running,
        "next": next_ready,
        "errors": errors,
        "steps": steps,
        "loop": loop_detail(sid, runs_root),
    }


# ---------------------------------------------------------------- writing loop

def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def loop_detail(sid: str, runs_root: Path | str = "runs") -> dict[str, Any]:
    """Critic-loop iterations/scores from runs/<SID>/draft.json and rewrite.json."""
    runs_root = Path(runs_root)
    out: dict[str, Any] = {"draft": None, "rewrite": None}
    d = _read_json(runs_root / sid / "draft.json")
    if isinstance(d, dict):
        out["draft"] = [{
            "cat": s.get("cat", ""),
            "iterations": s.get("iterations"),
            "scores": s.get("scores") or [],
            "accepted": s.get("accepted"),
        } for s in d.get("sections", []) if isinstance(s, dict)]
    r = _read_json(runs_root / sid / "rewrite.json")
    if isinstance(r, dict):
        t = r["transcript"] if isinstance(r.get("transcript"), dict) else r
        out["rewrite"] = {
            "iterations": t.get("iterations"),
            "scores": t.get("scores") or [],
            "title": r.get("title", ""),
        }
    return out


# ---------------------------------------------------------------- pop-up tabs

def _split_frontmatter(text: str) -> tuple[dict[str, str], str]:
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text
    meta: dict[str, str] = {}
    for line in text[3:end].splitlines():
        if ":" in line and not line.startswith((" ", "\t")):
            k, v = line.split(":", 1)
            meta[k.strip()] = v.strip()
    return meta, text[end + 4:].lstrip("\n")


def step_doc(step: str, root: Path | str = REPO_ROOT) -> dict[str, Any]:
    _check_step(step)
    path = Path(root) / "skills" / step / "SKILL.md"
    if not path.exists():
        return {"path": str(path.relative_to(root)), "html": "", "description": "",
                "missing": True}
    meta, body = _split_frontmatter(path.read_text())
    html = _markdown.markdown(body, extensions=["fenced_code", "tables"])
    return {"path": f"skills/{step}/SKILL.md", "html": html,
            "description": meta.get("description", ""), "missing": False}


def step_code(step: str, root: Path | str = REPO_ROOT) -> dict[str, Any]:
    """The step module plus the lib/prompts files it references (by import or
    by prompt name in a string literal, e.g. call_prompt("filter_urls"))."""
    _check_step(step)
    root = Path(root)
    main = root / "lib" / "steps" / f"{step}.py"
    src = main.read_text()
    prompts_dir = root / "lib" / "prompts"
    stems = {p.stem for p in prompts_dir.glob("*.py") if p.stem != "__init__"}
    referenced = set(re.findall(r"lib\.prompts\.(\w+)", src))
    referenced |= {m for m in re.findall(r"[\"'](\w+)[\"']", src) if m in stems}
    files = [{"path": f"lib/steps/{step}.py", "text": src}]
    for stem in sorted(referenced & stems):
        files.append({"path": f"lib/prompts/{stem}.py",
                      "text": (prompts_dir / f"{stem}.py").read_text()})
    return {"files": files}


def step_log(sid: str, step: str, offset: int = 0,
             runs_root: Path | str = "runs") -> dict[str, Any]:
    """Log bytes from `offset`; offset<0 or a first read returns the tail."""
    _check_sid(sid)
    _check_step(step)
    path = log_path(runs_root, sid, step)
    if not path.exists():
        return {"text": "", "offset": 0, "size": 0, "exists": False}
    size = path.stat().st_size
    if offset > size:  # file was replaced/truncated
        offset = 0
    start = offset
    if offset <= 0 and size > _LOG_FIRST_READ_MAX:
        start = size - _LOG_FIRST_READ_MAX
    with open(path, "rb") as fh:
        fh.seek(start)
        data = fh.read(size - start)
    text = data.decode("utf-8", errors="replace")
    if start > 0 and offset <= 0:
        text = text.split("\n", 1)[-1]  # drop the partial first line
    return {"text": text, "offset": size, "size": size, "exists": True,
            "truncated": start > 0 and offset <= 0}


def _load_checkpoint(db_path: str, sid: str, step: str) -> dict | None:
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT state_data, updated_at FROM agent_state "
            "WHERE session_id=? AND step_name=?", (sid, step)).fetchone()
    if row is None:
        return None
    data = json.loads(row[0])
    data["_updated_at"] = row[1]
    return data


def _table(rows: list[dict]) -> dict[str, Any]:
    cols = [c for c in _TABLE_COLUMNS if any(c in r for r in rows)]
    if "rating" in cols:
        rows = sorted(rows, key=lambda r: -(r.get("rating") or 0))
    out = [[r.get(c) for c in cols] for r in rows[:_STATE_ROW_LIMIT]]
    return {"columns": cols, "rows": out, "total": len(rows)}


def step_state(db_path: str, sid: str, step: str,
               runs_root: Path | str = "runs") -> dict[str, Any]:
    _check_sid(sid)
    _check_step(step)
    runs_root = Path(runs_root)
    cp = _load_checkpoint(db_path, sid, step)
    artifacts = sorted(p.name + ("/" if p.is_dir() else "")
                       for p in (runs_root / sid).glob(f"{step}*")) \
        if (runs_root / sid).is_dir() else []
    summary = _read_json(runs_root / sid / f"{step}.json")
    if cp is None:
        return {"available": False, "artifacts": artifacts, "summary": summary}
    st = next((s for s in cp.get("steps", []) if s.get("id") == step), {})
    headlines = cp.get("headline_data") or []
    stats = {"headlines": len(headlines)}
    if cp.get("cluster_names"):
        stats["clusters"] = len(cp["cluster_names"])
    if cp.get("newsletter_section_data"):
        stats["selected"] = len(cp["newsletter_section_data"])
        stats["sections"] = len({r.get("cat", "") for r in cp["newsletter_section_data"]})
    if headlines and any("rating" in h for h in headlines):
        stats["top rating"] = round(max(h.get("rating") or 0 for h in headlines), 3)
    if any((h.get("coverage_count") or 1) > 1 for h in headlines):
        stats["coverage boosts"] = sum(1 for h in headlines if (h.get("coverage_count") or 1) > 1)
    if cp.get("final_newsletter"):
        stats["newsletter chars"] = len(cp["final_newsletter"])
    if step in ("select", "draft", "rewrite", "send") and cp.get("newsletter_section_data"):
        table = _table(cp["newsletter_section_data"])
    else:
        table = _table(headlines)
    extra: dict[str, Any] = {}
    if step in ("rewrite", "send"):
        extra["newsletter_title"] = cp.get("newsletter_title", "")
        extra["final_newsletter"] = cp.get("final_newsletter", "")
    if step == "draft":
        extra["section_drafts"] = cp.get("section_drafts", [])
    return {
        "available": True,
        "updated_at": cp["_updated_at"],
        "status_message": st.get("status_message", ""),
        "stats": stats,
        "table": table,
        "artifacts": artifacts,
        "summary": summary,
        **extra,
    }


# ---------------------------------------------------------------- output + artifacts tabs

_ARTIFACT_LIMIT = 1000
_PREVIEW_MAX = 1_000_000


def step_output(sid: str, step: str, runs_root: Path | str = "runs") -> dict[str, Any]:
    """The step's run summary, runs/<SID>/<step>.json (what the step reports it did)."""
    _check_sid(sid)
    _check_step(step)
    path = Path(runs_root) / sid / f"{step}.json"
    data = _read_json(path)
    return {"path": f"runs/{sid}/{step}.json", "exists": data is not None, "data": data}


def _out_stamp(sid: str) -> str | None:
    m = _SID_RE.match(sid)
    if not m or m.group(4) is None:
        return None
    y, mo, d, h, mi, s = m.groups()
    return f"{y}-{mo}-{d}-{h}{mi}{s}"


def _file_entry(p: Path, root: Path) -> dict[str, Any]:
    st = p.stat()
    try:
        rel = p.relative_to(root).as_posix()
    except ValueError:  # runs root outside the repo (tests, custom layouts)
        rel = p.as_posix()
    return {"path": rel, "size": st.st_size,
            "mtime": datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds")}


def _expand(paths: list[Path], root: Path) -> list[dict[str, Any]]:
    files: list[dict[str, Any]] = []
    for p in paths:
        if p.is_dir():
            for f in sorted(q for q in p.rglob("*") if q.is_file()):
                files.append(_file_entry(f, root))
                if len(files) >= _ARTIFACT_LIMIT:
                    return files
        elif p.is_file():
            files.append(_file_entry(p, root))
    return files[:_ARTIFACT_LIMIT]


def step_artifacts(db_path: str, sid: str, step: str, runs_root: Path | str = "runs",
                   root: Path | str = REPO_ROOT) -> dict[str, Any]:
    """Files the step wrote, grouped: runs/<SID>/<step>* (summary, log, batches,
    results), plus step-specific outputs (gather's cached landing pages, download's
    article cache files, rate's digest and send's newsletter in out/)."""
    _check_sid(sid)
    _check_step(step)
    root = Path(root)
    rdir = Path(runs_root) if Path(runs_root).is_absolute() else root / runs_root
    sdir = rdir / sid
    groups: list[dict[str, Any]] = []

    def add(label: str, paths: list[Path], base: Path = root) -> None:
        files = _expand(paths, base)
        if files:
            groups.append({"label": label, "files": files})

    own = sorted(sdir.glob(f"{step}.*")) + sorted(p for p in sdir.glob(f"{step}-*"))
    add(f"runs/{sid}/", own)
    if step == "gather":
        add("Cached landing pages", [sdir / "pages"])
    if step == "download":
        add("Site-name lookup", sorted(sdir.glob("sitename*")))
        cp = _load_checkpoint(db_path, sid, "download")
        cache = []
        for h in (cp or {}).get("headline_data") or []:
            for key in ("text_path", "html_path"):
                v = h.get(key)
                if v and (root / v).is_file():
                    cache.append(root / v)
        add("Article cache (download/; may be shared with earlier sessions)", cache)
    stamp = _out_stamp(sid)
    if stamp and step == "rate":
        add("Rated digest (out/)", [root / "out" / f"{stamp}_short.html"])
    if stamp and step == "send":
        add("Newsletter (out/)", [root / "out" / f"{stamp}.html"])
    return {"groups": groups}


def artifact_file(db_path: str, sid: str, step: str, rel: str,
                  runs_root: Path | str = "runs",
                  root: Path | str = REPO_ROOT) -> tuple[Path, int]:
    """Resolve a file from the step's artifact list (anything else is refused)."""
    listed = {f["path"] for g in step_artifacts(db_path, sid, step, runs_root, root)["groups"]
              for f in g["files"]}
    if rel not in listed:
        raise GuiError("not an artifact of this step")
    path = Path(rel) if Path(rel).is_absolute() else Path(root) / rel
    return path, path.stat().st_size
