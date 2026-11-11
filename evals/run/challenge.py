"""Phase C: run eval candidates through several models and record answers,
time, tokens and cost.

One run directory per (prompt, run name):
    evals/work/runs/<prompt>/<run>/
        cases.jsonl        the candidates used (subset of Phase B)
        batches.json       fixed batch composition, shared by every model
        <model>.jsonl      one row per call: labels, secs, tokens, cost, error
        <model>-batches/   Agent-dispatch input files (Claude models)
        <model>-results/   Agent-dispatch output files
        items.csv          one row per case, all models side by side
        models.csv         per-model totals and agreement
        calls.csv          one row per call (a batch of 25, or one item for luna)

Usage:
    python -m evals.run.challenge pack --prompt filter_urls --run smoke50 --limit 50
    python -m evals.run.challenge api --prompt filter_urls --run smoke50 --model terra
    python -m evals.run.challenge prepare-agents --prompt filter_urls --run smoke50 --model haiku
    #   ... dispatch one Agent per <model>-batches/batch-NNN.json, then:
    python -m evals.run.challenge apply-agents --prompt filter_urls --run smoke50 --model haiku --timings t.json
    python -m evals.run.challenge csv --prompt filter_urls --run smoke50

Models: haiku, opus (Agent dispatch, subscription → no $ cost);
terra (openai:gpt-6.1-terra, medium); gemini (google:gemini-3.5-flash-lite,
thinking_level=medium); luna (OpenAI Decisions API, gpt-6-luna,
one call per item, returns a probability).
"""
from __future__ import annotations

import csv
import json
import os
import random
import statistics
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import click

ROOT = Path("evals/work")

# USD per 1M tokens (input, output). OpenRouter catalog list prices on
# 2026-11-10, used as a proxy for direct-API pricing. Decisions API bills no
# output tokens. Agent-dispatched Claude runs on the subscription: None.
PRICES: dict[str, tuple[float, float] | None] = {
    "terra": (1.00, 5.00),
    "gemini": (0.30, 2.50),
    "luna": (0.10, 0.0),
    "haiku": None,
    "opus": None,
}
MODEL_IDS = {
    "terra": "openai:gpt-6.1-terra (reasoning_effort=medium)",
    "gemini": "google:gemini-3.5-flash-lite (thinking_level=medium)",
    "luna": "openai decisions api: gpt-6-luna (predicate)",
    "haiku": "Agent dispatch: haiku effort=medium",
    "opus": "Agent dispatch: opus effort=medium",
}
CALL_UNIT = {"luna": "item"}  # everything else is called once per batch
DEFAULT_MODELS = ["haiku", "opus", "terra", "gemini", "luna"]
EFFORT = 4  # filter_urls' own PromptConfig effort; maps to "medium" on every engine

# per-prompt adapters: output → {local_id: label dict}, and the label column
LABEL_FIELD = {"filter_urls": "is_ai"}


def cost(model: str, in_tok: int | None, out_tok: int | None) -> float | None:
    p = PRICES.get(model)
    if p is None or in_tok is None:
        return None
    return (in_tok * p[0] + (out_tok or 0) * p[1]) / 1e6


def parse_labels(prompt: str, out: dict) -> dict:
    if prompt == "filter_urls":
        return {str(c["id"]): {"is_ai": bool(c["is_ai"])} for c in out.get("classifications", [])}
    raise NotImplementedError(prompt)


def _cfg(prompt: str):
    import lib.prompts  # noqa: F401  (registers prompts)
    from lib.llm import get_prompt
    return get_prompt(prompt)


def run_dir(prompt: str, run: str) -> Path:
    return ROOT / "runs" / prompt / run


def append_jsonl(path: Path, rows) -> None:
    with path.open("a") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.open()] if path.exists() else []


# ------------------------------------------------------------------- packing
def pack(cases: list[dict], prompt: str, batch_size: int = 25, seed: int = 0,
         limit: int | None = None) -> list[dict]:
    """Shuffle once (fixed seed), pack into batches of production size, renumber
    ids 0..n-1 inside each batch. A --limit run is a prefix of the full run, so
    its batches are identical to the full run's first batches."""
    cfg = _cfg(prompt)
    order = sorted(cases, key=lambda c: c["case_id"])
    random.Random(seed).shuffle(order)
    if limit:
        order = order[:limit]
    batches = []
    for b, start in enumerate(range(0, len(order), batch_size)):
        chunk = order[start:start + batch_size]
        items = []
        for i, c in enumerate(chunk):
            (it,) = c["input"]["items"]
            items.append({**it, "id": str(i)})
        inp = cfg.input_schema.model_validate({"items": items})
        batches.append({
            "batch": f"batch-{b:03d}", "case_ids": [c["case_id"] for c in chunk],
            "ids": [it["id"] for it in items], "items": items,
            "system_prompt": cfg.system_prompt,
            "user_prompt": cfg.user_prompt.format(**inp.model_dump()),
            "output_schema": cfg.output_schema.model_json_schema(),
        })
    return batches


# ---------------------------------------------------------------- API models
def _retry(fn, tries: int = 3):
    for k in range(tries):
        try:
            return fn()
        except Exception:
            if k == tries - 1:
                raise
            time.sleep(2 ** k)


def call_terra(b: dict, prompt: str) -> dict:
    from openai import OpenAI
    from lib.engines.openai_chat import _effort_level, _strictify_schema
    cfg = _cfg(prompt)
    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    resp = client.chat.completions.create(
        model="gpt-6.1-terra",
        messages=[{"role": "system", "content": b["system_prompt"]},
                  {"role": "user", "content": b["user_prompt"]}],
        response_format={"type": "json_schema", "json_schema": {
            "name": "structured_output", "strict": True,
            "schema": _strictify_schema(cfg.output_schema.model_json_schema())}},
        reasoning_effort=_effort_level(EFFORT))
    out = cfg.output_schema.model_validate(json.loads(resp.choices[0].message.content)).model_dump()
    u = resp.usage
    details = getattr(u, "completion_tokens_details", None)
    pdetails = getattr(u, "prompt_tokens_details", None)
    return {"out": out, "in_tok": u.prompt_tokens, "out_tok": u.completion_tokens,
            "cached_tok": getattr(pdetails, "cached_tokens", None),
            "reasoning_tok": getattr(details, "reasoning_tokens", None)}


def call_gemini(b: dict, prompt: str) -> dict:
    from google import genai
    from google.genai import types
    from lib.engines.google import _MAX_OUTPUT_TOKENS, _thinking_level
    cfg = _cfg(prompt)
    client = genai.Client(api_key=os.environ["GOOGLE_API_KEY"])
    resp = client.models.generate_content(
        model="gemini-3.5-flash-lite", contents=b["user_prompt"],
        config=types.GenerateContentConfig(
            system_instruction=b["system_prompt"], max_output_tokens=_MAX_OUTPUT_TOKENS,
            response_mime_type="application/json", response_schema=cfg.output_schema,
            thinking_config=types.ThinkingConfig(thinking_level=_thinking_level(EFFORT))))
    out = cfg.output_schema.model_validate(json.loads(resp.text)).model_dump()
    m = resp.usage_metadata
    thoughts = m.thoughts_token_count or 0
    return {"out": out, "in_tok": m.prompt_token_count,
            "cached_tok": m.cached_content_token_count,
            "out_tok": (m.candidates_token_count or 0) + thoughts, "reasoning_tok": thoughts}


def decisions_instructions(prompt: str) -> str:
    """The Decisions API takes criteria, not a chat prompt: keep the rules from
    the user prompt, drop the JSON/IO boilerplate."""
    if prompt != "filter_urls":
        raise NotImplementedError(prompt)
    rules = _cfg(prompt).user_prompt.split("Input:")[0]
    rules = rules.replace("Classify every headline below.", "").strip()
    return "The input is a news headline. True if it is AI-related.\n\n" + rules


def call_luna(title: str, prompt: str) -> dict:
    import httpx
    r = httpx.post("https://api.openai.com/v1/decisions", timeout=60,
                   headers={"Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}"},
                   json={"model": "gpt-6-luna", "input": title,
                         "questions": [{"type": "predicate", "name": "ai_related",
                                        "instructions": decisions_instructions(prompt)}],
                         # identical instructions on every call → let OpenAI cache the prefix
                         "prompt_cache_key": "ai-news-classifier-v1"})
    r.raise_for_status()
    p = r.json()
    prob = float(p["answers"][0]["probability"])
    u = p.get("usage", {})
    return {"prob": prob, "in_tok": u.get("input_tokens"), "out_tok": u.get("output_tokens", 0),
            "cached_tok": (u.get("input_tokens_details") or {}).get("cached_tokens", 0),
            "reasoning_tok": (u.get("output_tokens_details") or {}).get("reasoning_tokens", 0)}


def _row(model, b, unit, labels, secs, res, err, item=None):
    in_tok, out_tok = (res or {}).get("in_tok"), (res or {}).get("out_tok")
    row = {"model": model, "batch": b["batch"], "unit": unit,
           "n_items": 1 if unit == "item" else len(b["ids"]), "labels": labels,
           "secs": round(secs, 3), "in_tok": in_tok, "out_tok": out_tok,
           "reasoning_tok": (res or {}).get("reasoning_tok"),
           "cached_tok": (res or {}).get("cached_tok"),
           "cost": cost(model, in_tok, out_tok), "error": err}
    if item is not None:
        row["item"] = item
    return row


def run_api(prompt: str, run: str, model: str, workers: int = 8) -> None:
    import lib  # noqa: F401  (loads .env)
    rd = run_dir(prompt, run)
    batches = json.loads((rd / "batches.json").read_text())
    out_path = rd / f"{model}.jsonl"
    done = {(r["batch"], r.get("item")) for r in read_jsonl(out_path) if not r["error"]}

    if model == "luna":
        jobs = [(b, it) for b in batches for it in b["items"] if (b["batch"], it["id"]) not in done]

        def one(job):
            b, it = job
            t = time.time()
            try:
                res = _retry(lambda: call_luna(it["title"], prompt))
                labels = {it["id"]: {"is_ai": res["prob"] >= 0.5, "prob": res["prob"]}}
                return _row(model, b, "item", labels, time.time() - t, res, None, item=it["id"])
            except Exception as e:
                return _row(model, b, "item", {}, time.time() - t, None, f"{type(e).__name__}: {e}"[:300], item=it["id"])
        workers = max(workers, 16)
    else:
        caller = {"terra": call_terra, "gemini": call_gemini}[model]
        jobs = [b for b in batches if (b["batch"], None) not in done]

        def one(b):
            t = time.time()
            try:
                res = _retry(lambda: caller(b, prompt))
                labels = parse_labels(prompt, res["out"])
                missing = set(b["ids"]) - set(labels)
                err = f"missing ids {sorted(missing)}" if missing else None
                return _row(model, b, "batch", labels, time.time() - t, res, err)
            except Exception as e:
                return _row(model, b, "batch", {}, time.time() - t, None, f"{type(e).__name__}: {e}"[:300])

    with ThreadPoolExecutor(workers) as ex:
        rows = list(ex.map(one, jobs))
    append_jsonl(out_path, rows)
    errs = [r for r in rows if r["error"]]
    click.echo(f"{model}: {len(rows)} calls, {len(errs)} errors"
               + (f" (first: {errs[0]['error']})" if errs else ""))


# ------------------------------------------------------------- Agent models
def prepare_agents(prompt: str, run: str, model: str) -> list[Path]:
    rd = run_dir(prompt, run)
    batches = json.loads((rd / "batches.json").read_text())
    bdir, rdir = rd / f"{model}-batches", rd / f"{model}-results"
    bdir.mkdir(exist_ok=True)
    rdir.mkdir(exist_ok=True)
    paths = []
    for b in batches:
        p = bdir / f"{b['batch']}.json"
        p.write_text(json.dumps({k: b[k] for k in
                                 ("batch", "ids", "items", "system_prompt", "user_prompt", "output_schema")},
                                indent=2))
        paths.append(p)
    return paths


def apply_agents(prompt: str, run: str, model: str, timings: dict) -> None:
    """timings: {batch: {"secs": float, "subagent_tokens": int}} from the Agent
    task notifications (wall-clock incl. tool calls; tokens incl. Claude Code
    overhead, so they are not comparable to API token counts)."""
    rd = run_dir(prompt, run)
    batches = json.loads((rd / "batches.json").read_text())
    rows = []
    for b in batches:
        rp = rd / f"{model}-results" / f"{b['batch']}.json"
        t = timings.get(b["batch"], {})
        res = {"in_tok": None, "out_tok": None, "reasoning_tok": None}
        try:
            labels = parse_labels(prompt, json.loads(rp.read_text()))
            missing = set(b["ids"]) - set(labels)
            err = f"missing ids {sorted(missing)}" if missing else None
        except (OSError, ValueError, KeyError) as e:
            labels, err = {}, f"{type(e).__name__}: {e}"[:300]
        row = _row(model, b, "batch", labels, t.get("secs", 0.0), res, err)
        row["subagent_tokens"] = t.get("subagent_tokens")
        rows.append(row)
    out = rd / f"{model}.jsonl"
    if out.exists():
        out.unlink()  # agent results are re-applied wholesale
    append_jsonl(out, rows)
    click.echo(f"{model}: applied {len(rows)} batches, {sum(bool(r['error']) for r in rows)} errors")


# ---------------------------------------------------------------------- CSV
def _fmt(x):
    if x is None:
        return ""
    if isinstance(x, float):
        return f"{x:.6g}"
    return x


def write_csvs(rd: Path, prompt: str, models: list[str]) -> None:
    field = LABEL_FIELD[prompt]
    batches = json.loads((rd / "batches.json").read_text())
    cases = {c["case_id"]: c for c in read_jsonl(rd / "cases.jsonl")}
    calls = {m: read_jsonl(rd / f"{m}.jsonl") for m in models}

    # (batch, local id) -> label per model; keep the latest successful call
    ans: dict[str, dict] = {m: {} for m in models}
    for m, rows in calls.items():
        for r in rows:
            for lid, lab in r["labels"].items():
                ans[m][(r["batch"], lid)] = lab

    items = []
    for b in batches:
        for lid, cid in zip(b["ids"], b["case_ids"]):
            c = cases[cid]
            row = {"case_id": cid, "batch": b["batch"], "local_id": lid,
                   "title": c["input"]["items"][0].get("title"),
                   "source": c["strata"].get("source"), "month": c["strata"].get("month"),
                   "tags": " ".join(c["tags"]), "sample": c.get("sample"),
                   f"prod_{field}": c["prod_output"].get(field)}
            votes = {}
            for m in models:
                lab = ans[m].get((b["batch"], lid))
                v = None if lab is None else lab.get(field)
                row[f"{m}_{field}"] = v
                if m == "luna":
                    row["luna_prob"] = None if lab is None else lab.get("prob")
                if v is not None:
                    votes[m] = v
            yes = sum(votes.values())
            maj = yes * 2 > len(votes) if votes else None
            row.update({"n_models": len(votes), f"n_{field.removeprefix('is_')}_votes": yes,
                        "unanimous": len(set(votes.values())) <= 1,
                        "majority": maj,
                        "prod_agrees_majority": None if maj is None else row[f"prod_{field}"] == maj,
                        "dissent": " ".join(m for m, v in votes.items() if v != maj)})
            items.append(row)
    items.sort(key=lambda r: (r["unanimous"], -min(r[f"n_{field.removeprefix('is_')}_votes"],
                                                    r["n_models"] - r[f"n_{field.removeprefix('is_')}_votes"])))
    with (rd / "items.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(items[0]))
        w.writeheader()
        w.writerows({k: _fmt(v) for k, v in r.items()} for r in items)

    # per-call rows
    call_rows = []
    for m in models:
        for r in calls[m]:
            call_rows.append({"model": m, "model_id": MODEL_IDS.get(m), "call_unit": r["unit"],
                              "batch": r["batch"], "item": r.get("item", ""), "n_items": r["n_items"],
                              "secs": r["secs"], "in_tok": r["in_tok"], "out_tok": r["out_tok"],
                              "reasoning_tok": r["reasoning_tok"],
                              "cached_tok": r.get("cached_tok"),
                              "subagent_tokens": r.get("subagent_tokens"),
                              "cost_usd": r["cost"], "error": r["error"]})
    with (rd / "calls.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(call_rows[0]))
        w.writeheader()
        w.writerows({k: _fmt(v) for k, v in r.items()} for r in call_rows)

    # per-model summary
    summary = []
    for m in models:
        rs = calls[m]
        secs = [r["secs"] for r in rs if not r["error"]]
        costs = [r["cost"] for r in rs if r["cost"] is not None]
        labelled = [r for r in items if r[f"{m}_{field}"] is not None]
        n_items = len(labelled)

        def tot(k):
            vals = [r.get(k) for r in rs if r.get(k) is not None]
            return sum(vals) if vals else None
        summary.append({
            "model": m, "model_id": MODEL_IDS.get(m), "call_unit": CALL_UNIT.get(m, "batch"),
            "calls": len(rs), "errors": sum(bool(r["error"]) for r in rs), "items_labelled": n_items,
            "secs_per_call_mean": statistics.mean(secs) if secs else None,
            "secs_per_call_p90": sorted(secs)[int(0.9 * (len(secs) - 1))] if secs else None,
            "secs_total": sum(secs) if secs else None,
            "in_tok_total": tot("in_tok"), "out_tok_total": tot("out_tok"),
            "reasoning_tok_total": tot("reasoning_tok"), "cached_tok_total": tot("cached_tok"), "subagent_tokens_total": tot("subagent_tokens"),
            "total_cost_usd": sum(costs) if costs else None,
            "cost_per_item_usd": (sum(costs) / n_items) if costs and n_items else None,
            "cost_per_1000_items_usd": (1000 * sum(costs) / n_items) if costs and n_items else None,
            f"{field}_rate": (sum(bool(r[f"{m}_{field}"]) for r in labelled) / n_items) if n_items else None,
            "agree_with_prod": (sum(r[f"{m}_{field}"] == r[f"prod_{field}"] for r in labelled) / n_items)
            if n_items else None,
            "agree_with_majority": (sum(r[f"{m}_{field}"] == r["majority"] for r in labelled) / n_items)
            if n_items else None,
        })
    with (rd / "models.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(summary[0]))
        w.writeheader()
        w.writerows({k: _fmt(v) for k, v in r.items()} for r in summary)


# ---------------------------------------------------------------------- CLI
@click.group()
def cli():
    pass


_prompt = click.option("--prompt", default="filter_urls", show_default=True)
_run = click.option("--run", "run", required=True)


@cli.command("pack")
@_prompt
@_run
@click.option("--limit", type=int, default=None)
@click.option("--batch-size", default=25, show_default=True)
@click.option("--seed", default=0, show_default=True)
def pack_cmd(prompt, run, limit, batch_size, seed):
    cases = read_jsonl(ROOT / "candidates" / f"{prompt}.jsonl")
    batches = pack(cases, prompt, batch_size=batch_size, seed=seed, limit=limit)
    rd = run_dir(prompt, run)
    rd.mkdir(parents=True, exist_ok=True)
    used = {cid for b in batches for cid in b["case_ids"]}
    (rd / "cases.jsonl").write_text("".join(json.dumps(c) + "\n" for c in cases if c["case_id"] in used))
    (rd / "batches.json").write_text(json.dumps(batches, indent=1))
    click.echo(f"{rd}: {len(used)} cases in {len(batches)} batches")


@cli.command("api")
@_prompt
@_run
@click.option("--model", type=click.Choice(["terra", "gemini", "luna"]), required=True)
def api_cmd(prompt, run, model):
    run_api(prompt, run, model)


@cli.command("prepare-agents")
@_prompt
@_run
@click.option("--model", type=click.Choice(["haiku", "opus"]), required=True)
def prepare_cmd(prompt, run, model):
    for p in prepare_agents(prompt, run, model):
        click.echo(p)


@cli.command("apply-agents")
@_prompt
@_run
@click.option("--model", type=click.Choice(["haiku", "opus"]), required=True)
@click.option("--timings", type=click.Path(exists=True, path_type=Path), default=None)
def apply_cmd(prompt, run, model, timings):
    apply_agents(prompt, run, model, json.loads(timings.read_text()) if timings else {})


@cli.command("csv")
@_prompt
@_run
@click.option("--models", default=",".join(DEFAULT_MODELS), show_default=True)
def csv_cmd(prompt, run, models):
    rd = run_dir(prompt, run)
    write_csvs(rd, prompt, [m for m in models.split(",") if (rd / f"{m}.jsonl").exists()])
    click.echo(f"wrote {rd}/items.csv, models.csv, calls.csv")


if __name__ == "__main__":
    cli()
