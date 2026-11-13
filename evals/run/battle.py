"""battle_prompt challenger eval: rank the same article groups with several
Gemini configs and score each against an Opus gold ranking.

    evals/work/runs/battle_prompt/<run>/
        cases.jsonl          the gold groups (stratified subset of the candidates)
        opus-batches/        Agent-dispatch input, ~12 groups per file
        opus-results/        Agent output: {"rankings": [{"group", "ranking"}]}
        <model>.jsonl        one row per call: ranking, secs, tokens, cost, error
        groups.csv           one row per group, every model's tau vs gold
        models.csv           per-model quality / cost / latency

Usage:
    python -m evals.run.battle prepare --run gold400
    #   ... dispatch one Opus Agent per opus-batches/batch-NNN.json, then:
    python -m evals.run.battle api --run gold400 --model hydrogen-low
    python -m evals.run.battle report --run gold400

Each API call is one production battle (6 items, the live battle_prompt).
"""
from __future__ import annotations

import csv
import json
import math
import os
import random
import statistics
import time
from concurrent.futures import ThreadPoolExecutor
from itertools import combinations
from pathlib import Path

import click

ROOT = Path("evals/work")
PROMPT = "battle_prompt"
CANDIDATES = ROOT / "candidates" / f"{PROMPT}.jsonl"
GROUP_TYPES = ("q1_top", "q2", "q3", "q4_bottom", "mixed")

# name -> (Gemini model id, thinking_level). flashlite31-low is production
# (battle_prompt: gemini-3.1-flash-lite, reasoning_effort=2 -> "low").
MODELS = {
    "flashlite31-low": ("gemini-3.1-flash-lite", "low"),
    "flashlite35-low": ("gemini-3.5-flash-lite", "low"),
    "hydrogen-low": ("gemini-4-hydrogen", "low"),
    "hydrogen-medium": ("gemini-4-hydrogen", "medium"),
}
# USD per 1M tokens (input, output incl. thinking). OpenRouter list prices 2026-11-12.
PRICES = {
    "gemini-3.1-flash-lite": (0.25, 1.50),
    "gemini-3.5-flash-lite": (0.30, 2.50),
    "gemini-4-hydrogen": (1.50, 7.50),
}
PARALLELISM = 8  # production _BATTLE_PARALLELISM

GOLD_SYSTEM_SUFFIX = """

# BATCH MODE
The user message holds several independent groups. Rank each group on its own, applying the
rules above only within that group. Every id of a group must appear exactly once in its ranking."""

GOLD_USER = """Rank each group below. Return only JSON of this shape:
{{"rankings": [{{"group": "<group id>", "ranking": ["<most important id>", ..., "<least important id>"]}}]}}
with one entry per group, in the same order as the input.

The groups to rank are in the `groups` field of this batch file."""


def cost(model: str, in_tok: int | None, out_tok: int | None) -> float | None:
    if in_tok is None:
        return None
    p_in, p_out = PRICES[MODELS[model][0]]
    return (in_tok * p_in + (out_tok or 0) * p_out) / 1e6


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.open()] if path.exists() else []


def run_dir(run: str) -> Path:
    return ROOT / "runs" / PROMPT / run


def _cfg():
    import lib.prompts  # noqa: F401
    from lib.llm import get_prompt
    return get_prompt(PROMPT)


# ------------------------------------------------------------------ metrics
def _pairs(ranking: list[str], gold: list[str]) -> tuple[int, int]:
    pos = {x: i for i, x in enumerate(ranking)}
    conc = disc = 0
    for a, b in combinations(gold, 2):  # gold says a > b
        if a in pos and b in pos:
            if pos[a] < pos[b]:
                conc += 1
            else:
                disc += 1
    return conc, disc


def pairwise_agreement(ranking: list[str], gold: list[str]) -> float:
    conc, disc = _pairs(ranking, gold)
    return conc / (conc + disc) if conc + disc else float("nan")


def kendall_tau(ranking: list[str], gold: list[str]) -> float:
    conc, disc = _pairs(ranking, gold)
    return (conc - disc) / (conc + disc) if conc + disc else float("nan")


def ranking_problem(ranking: list[str], ids: list[str]) -> str | None:
    probs = []
    if missing := sorted(set(ids) - set(ranking)):
        probs.append(f"missing {missing}")
    if extra := sorted(set(ranking) - set(ids)):
        probs.append(f"extra {extra}")
    if dups := sorted({x for x in ranking if ranking.count(x) > 1}):
        probs.append(f"duplicate {dups}")
    return "; ".join(probs) or None


# --------------------------------------------------------------- gold (Opus)
def select_gold_groups(cases: list[dict], per_type: int = 80, seed: int = 0) -> list[dict]:
    rng = random.Random(seed)
    out = []
    for t in GROUP_TYPES:
        pool = sorted((c for c in cases if c["strata"]["group_type"] == t), key=lambda c: c["case_id"])
        out += rng.sample(pool, min(per_type, len(pool)))
    return out


def write_gold_batches(cases: list[dict], rd: Path, max_groups: int = 12,
                       max_bytes: int = 55_000) -> list[Path]:
    """Pack groups into Agent batch files. Group ids g0..gN are global so
    check_batch.py can verify coverage; article ids stay as in the case."""
    system = _cfg().system_prompt + GOLD_SYSTEM_SUFFIX
    bdir = rd / "opus-batches"
    bdir.mkdir(parents=True, exist_ok=True)
    groups = [{"group": f"g{i}", "case_id": c["case_id"], "items": c["input"]["items"]}
              for i, c in enumerate(cases)]

    def render(chunk, b):
        body = [{"group": g["group"], "items": g["items"]} for g in chunk]
        return json.dumps({
            "batch": f"batch-{b:03d}", "ids": [g["group"] for g in chunk],
            "case_ids": [g["case_id"] for g in chunk], "groups": body,
            "system_prompt": system,
            "user_prompt": GOLD_USER.format(),
        }, ensure_ascii=False)

    paths, start = [], 0
    while start < len(groups):
        n = min(max_groups, len(groups) - start)
        while n > 1 and len(render(groups[start:start + n], len(paths)).encode()) > max_bytes:
            n -= 1
        p = bdir / f"batch-{len(paths):03d}.json"
        p.write_text(render(groups[start:start + n], len(paths)))
        paths.append(p)
        start += n
    return paths


def read_gold(rd: Path) -> tuple[dict[str, list[str]], list[str]]:
    """{case_id: gold ranking} from opus-results, plus a list of problems."""
    gold, problems = {}, []
    for bp in sorted((rd / "opus-batches").glob("batch-*.json")):
        b = json.loads(bp.read_text())
        rp = rd / "opus-results" / bp.name
        if not rp.exists():
            problems.append(f"{bp.name}: no result")
            continue
        got = {str(r["group"]): [str(x) for x in r["ranking"]] for r in json.loads(rp.read_text())["rankings"]}
        for g, cid in zip(b["groups"], b["case_ids"]):
            ids = [it["id"] for it in g["items"]]
            r = got.get(g["group"])
            prob = "no ranking" if r is None else ranking_problem(r, ids)
            if prob:
                problems.append(f"{bp.name} {g['group']}: {prob}")
            else:
                gold[cid] = r
    return gold, problems


# ---------------------------------------------------------------- API runs
def call_gemini(model_id: str, level: str, system: str, user: str, schema) -> dict:
    from google import genai
    from google.genai import types
    from lib.engines.google import _MAX_OUTPUT_TOKENS
    client = genai.Client(api_key=os.environ["GOOGLE_API_KEY"])
    resp = client.models.generate_content(
        model=model_id, contents=user,
        config=types.GenerateContentConfig(
            system_instruction=system, max_output_tokens=_MAX_OUTPUT_TOKENS,
            response_mime_type="application/json", response_schema=schema,
            thinking_config=types.ThinkingConfig(thinking_level=level)))
    out = schema.model_validate(json.loads(resp.text))
    m = resp.usage_metadata
    thoughts = m.thoughts_token_count or 0
    return {"ranking": [str(x) for x in out.ranking], "in_tok": m.prompt_token_count,
            "out_tok": (m.candidates_token_count or 0) + thoughts, "reasoning_tok": thoughts}


def battle_call(cfg, model: str, items: list[dict]) -> dict:
    """One production battle over ``items``; never raises (errors go in the row)."""
    model_id, level = MODELS[model]
    inp = cfg.input_schema.model_validate({"items": items})
    user = cfg.user_prompt.format(**inp.model_dump())
    ids = [it["id"] for it in items]
    t = time.time()
    res, err = None, None
    for k in range(3):
        try:
            res = call_gemini(model_id, level, cfg.system_prompt, user, cfg.output_schema)
            err = None
            break
        except Exception as e:  # noqa: BLE001
            err = f"{type(e).__name__}: {e}"[:300]
            time.sleep(2 ** k)
    res = res or {}
    return {"ranking": res.get("ranking"),
            "invalid": ranking_problem(res["ranking"], ids) if res else None,
            "secs": round(time.time() - t, 3), "in_tok": res.get("in_tok"), "out_tok": res.get("out_tok"),
            "reasoning_tok": res.get("reasoning_tok"),
            "cost": cost(model, res.get("in_tok"), res.get("out_tok")), "error": err}


def run_api(run: str, model: str, workers: int = PARALLELISM) -> None:
    import lib  # noqa: F401  (loads .env)
    cfg = _cfg()
    rd = run_dir(run)
    cases = read_jsonl(rd / "cases.jsonl")
    out_path = rd / f"{model}.jsonl"
    prev = [r for r in read_jsonl(out_path) if not r["error"]]  # rerun retries failures
    out_path.write_text("".join(json.dumps(r) + "\n" for r in prev))
    done = {r["case_id"] for r in prev}

    def one(c):
        return {"model": model, "case_id": c["case_id"], **battle_call(cfg, model, c["input"]["items"])}

    todo = [c for c in cases if c["case_id"] not in done]
    t0 = time.time()
    with ThreadPoolExecutor(workers) as ex:
        rows = list(ex.map(one, todo))
    wall = time.time() - t0
    with out_path.open("a") as f:
        f.write("".join(json.dumps(r) + "\n" for r in rows))
    (rd / f"{model}.wall.json").write_text(json.dumps({"calls": len(todo), "workers": workers, "wall_secs": wall}))
    errs = [r for r in rows if r["error"]]
    click.echo(f"{model}: {len(rows)} calls in {wall:.0f}s wall, {len(errs)} errors, "
               f"{sum(bool(r['invalid']) for r in rows)} invalid rankings"
               + (f" (first error: {errs[0]['error']})" if errs else ""))


# ------------------------------------------------------------------ report
def _mean(xs):
    xs = [x for x in xs if x is not None and not (isinstance(x, float) and math.isnan(x))]
    return statistics.mean(xs) if xs else None


def write_report(rd: Path, models: list[str]) -> list[dict]:
    gold, problems = read_gold(rd)
    cases = {c["case_id"]: c for c in read_jsonl(rd / "cases.jsonl")}
    rows = {m: {r["case_id"]: r for r in read_jsonl(rd / f"{m}.jsonl")} for m in models}
    # "reference" = the old composite-rating order the candidates shipped with
    rankers = ["reference"] + models

    def ranking_of(m, cid):
        if m == "reference":
            return cases[cid]["prod_output"]["ranking"]
        r = rows[m].get(cid)
        return r["ranking"] if r and not r["error"] and not r["invalid"] else None

    group_rows = []
    for cid, g in gold.items():
        row = {"case_id": cid, "group_type": cases[cid]["strata"]["group_type"], "gold": " ".join(g)}
        for m in rankers:
            r = ranking_of(m, cid)
            row[f"{m}_tau"] = None if r is None else round(kendall_tau(r, g), 4)
            row[f"{m}_top1"] = None if r is None else r[0] == g[0]
        group_rows.append(row)
    with (rd / "groups.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(group_rows[0]))
        w.writeheader()
        w.writerows(group_rows)

    summary = []
    for m in rankers:
        s = {"model": m}
        for t in ("all",) + GROUP_TYPES:
            sub = [r for r in group_rows if t == "all" or r["group_type"] == t]
            s[f"tau_{t}"] = _mean([r[f"{m}_tau"] for r in sub])
        sub = [r for r in group_rows if r[f"{m}_top1"] is not None]
        s["top1_match"] = _mean([float(r[f"{m}_top1"]) for r in sub])
        s["pairwise_agree"] = (s["tau_all"] + 1) / 2 if s["tau_all"] is not None else None
        if m != "reference":
            rs = [r for cid, r in rows[m].items() if cid in gold]
            ok = [r for r in rs if not r["error"]]
            secs = sorted(r["secs"] for r in ok)
            costs = [r["cost"] for r in ok if r["cost"] is not None]
            wall = rd / f"{m}.wall.json"
            s.update({
                "calls": len(rs), "errors": sum(bool(r["error"]) for r in rs),
                "invalid": sum(bool(r["invalid"]) for r in ok),
                "secs_mean": _mean(secs), "secs_p50": secs[len(secs) // 2] if secs else None,
                "secs_p90": secs[int(0.9 * (len(secs) - 1))] if secs else None,
                "wall_secs_8par": json.loads(wall.read_text())["wall_secs"] if wall.exists() else None,
                "in_tok_mean": _mean([r["in_tok"] for r in ok]),
                "out_tok_mean": _mean([r["out_tok"] for r in ok]),
                "reasoning_tok_mean": _mean([r["reasoning_tok"] for r in ok]),
                "cost_total_usd": sum(costs) if costs else None,
                "cost_per_1000_calls_usd": 1000 * statistics.mean(costs) if costs else None,
            })
        summary.append(s)
    keys = list(dict.fromkeys(k for s in summary for k in s))
    with (rd / "models.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        w.writerows(summary)
    if problems:
        click.echo(f"gold problems ({len(problems)}): {problems[:5]}")
    return summary


# -------------------------------------------------------- batch-size sweep
SIZE_TYPES = ("random", "band")
TYPICAL_N = 143  # median rated articles per rate run (318 sessions)


def big_groups(ckpts, n: int = 150, size: int = 20, seed: int = 0) -> list[dict]:
    """Groups of ``size`` articles from one session. Half "random" (any 20 of
    the session, like early Swiss rounds), half "band" (20 consecutive by
    composite rating, like late Swiss rounds where neighbours are close)."""
    rng = random.Random(seed)
    sessions = []
    for ck in ckpts:
        pool = [it for it in ck["items"] if it.get("rating") is not None and it.get("summary")]
        if len(pool) >= 2 * size:
            pool.sort(key=lambda it: -float(it["rating"]))
            sessions.append((ck["sid"], pool))
    out = []
    for ti, t in enumerate(SIZE_TYPES):
        count = n // len(SIZE_TYPES) + (1 if ti < n % len(SIZE_TYPES) else 0)
        order = sessions[:]
        rng.shuffle(order)
        for g in range(count):
            sid, pool = order[g % len(order)]
            if t == "band":
                start = rng.randrange(0, len(pool) - size + 1)
                grp = pool[start:start + size]
            else:
                grp = rng.sample(pool, size)
            grp = grp[:]
            rng.shuffle(grp)
            out.append({
                "case_id": f"{PROMPT}:{sid}:{t}-{g}", "prompt": PROMPT,
                "input": {"items": [{"id": str(it["id"]), "title": it.get("title") or "",
                                     "summary": it["summary"]} for it in grp]},
                "prod_output": {"ranking": [str(it["id"]) for it in
                                            sorted(grp, key=lambda it: -float(it["rating"]))]},
                "provenance": {"sid": sid}, "strata": {"group_type": t}})
    return out


def k_chunks(case: dict, k: int) -> list[list[dict]]:
    """Disjoint random chunks of k items (floor(size/k) of them), fixed per (case, k)."""
    items = list(case["input"]["items"])
    random.Random(f"{case['case_id']}:{k}").shuffle(items)
    return [items[i * k:(i + 1) * k] for i in range(len(items) // k)]


def pair_counts(ranking: list[str], gold: list[str]) -> tuple[int, int]:
    """(concordant, discordant) over the gold pairs whose both ids are ranked."""
    return _pairs(ranking, gold)


def run_k(run: str, model: str, k: int, workers: int = PARALLELISM) -> None:
    import lib  # noqa: F401
    cfg = _cfg()
    rd = run_dir(run)
    jobs = [(c, i, ch) for c in read_jsonl(rd / "cases.jsonl") for i, ch in enumerate(k_chunks(c, k))]
    out_path = rd / f"{model}-k{k}.jsonl"
    prev = [r for r in read_jsonl(out_path) if not r["error"]]
    done = {(r["case_id"], r["chunk"]) for r in prev}
    jobs = [j for j in jobs if (j[0]["case_id"], j[1]) not in done]

    def one(job):
        c, i, ch = job
        return {"model": model, "k": k, "case_id": c["case_id"], "chunk": i, **battle_call(cfg, model, ch)}

    t0 = time.time()
    with ThreadPoolExecutor(workers) as ex:
        rows = list(ex.map(one, jobs))
    wall = time.time() - t0
    out_path.write_text("".join(json.dumps(r) + "\n" for r in prev + rows))
    (rd / f"{model}-k{k}.wall.json").write_text(json.dumps({"calls": len(jobs), "wall_secs": wall}))
    click.echo(f"{model} k={k}: {len(rows)} calls in {wall:.0f}s, "
               f"{sum(bool(r['error']) for r in rows)} errors, {sum(bool(r['invalid']) for r in rows)} invalid")


def k_stats(rd: Path, model: str, k: int, gold: dict) -> dict:
    rows = read_jsonl(rd / f"{model}-k{k}.jsonl")
    cases = {c["case_id"]: c for c in read_jsonl(rd / "cases.jsonl")}
    per_group: dict[str, list[int]] = {}
    for r in rows:
        if r["error"] or r["invalid"] or r["case_id"] not in gold:
            continue
        conc, disc = pair_counts(r["ranking"], gold[r["case_id"]])
        acc = per_group.setdefault(r["case_id"], [0, 0])
        acc[0] += conc
        acc[1] += disc
    ok = [r for r in rows if not r["error"]]
    secs = sorted(r["secs"] for r in ok)
    cpc = statistics.mean(r["cost"] for r in ok) if ok else None
    calls_per_run = math.ceil((TYPICAL_N - 1) / (k - 1)) * math.ceil(TYPICAL_N / k)

    def acc_of(cids):
        c = sum(per_group[g][0] for g in cids)
        d = sum(per_group[g][1] for g in cids)
        return c / (c + d) if c + d else None
    wall = rd / f"{model}-k{k}.wall.json"
    return {
        "k": k, "calls": len(rows), "errors": len(rows) - len(ok),
        "invalid": sum(bool(r["invalid"]) for r in ok), "groups": len(per_group),
        "pair_acc": acc_of(list(per_group)),
        **{f"pair_acc_{t}": acc_of([g for g in per_group if cases[g]["strata"]["group_type"] == t])
           for t in SIZE_TYPES},
        "secs_p50": secs[len(secs) // 2] if secs else None,
        "secs_p90": secs[int(0.9 * (len(secs) - 1))] if secs else None,
        "wall_secs": json.loads(wall.read_text())["wall_secs"] if wall.exists() else None,
        "out_tok_mean": _mean([r["out_tok"] for r in ok]),
        "reasoning_tok_mean": _mean([r["reasoning_tok"] for r in ok]),
        "cost_per_call": cpc,
        "cost_per_1000_pairs": 1000 * cpc / (k * (k - 1) / 2) if cpc else None,
        "max_calls_per_run": calls_per_run,
        "max_cost_per_run": cpc * calls_per_run if cpc else None,
        "_per_group": {g: v[0] / (v[0] + v[1]) for g, v in per_group.items() if v[0] + v[1]},
    }


def paired_diff(a: dict, b: dict, n_boot: int = 5000, seed: int = 0) -> tuple[float, float, float]:
    """Mean per-group accuracy difference a - b with a 95% bootstrap CI over groups."""
    gs = sorted(set(a) & set(b))
    xs = [a[g] - b[g] for g in gs]
    rng = random.Random(seed)
    bs = sorted(statistics.mean(rng.choices(xs, k=len(xs))) for _ in range(n_boot))
    return statistics.mean(xs), bs[int(0.025 * n_boot)], bs[int(0.975 * n_boot)]


# --------------------------------------------------------------------- CLI
@click.group()
def cli():
    pass


@cli.command("prepare")
@click.option("--run", required=True)
@click.option("--per-type", default=80, show_default=True)
@click.option("--seed", default=0, show_default=True)
def prepare_cmd(run, per_type, seed):
    cases = select_gold_groups(read_jsonl(CANDIDATES), per_type=per_type, seed=seed)
    rd = run_dir(run)
    rd.mkdir(parents=True, exist_ok=True)
    (rd / "cases.jsonl").write_text("".join(json.dumps(c, ensure_ascii=False) + "\n" for c in cases))
    for p in write_gold_batches(cases, rd):
        click.echo(p)
    (rd / "opus-results").mkdir(exist_ok=True)


@cli.command("api")
@click.option("--run", required=True)
@click.option("--model", type=click.Choice(list(MODELS)), required=True)
def api_cmd(run, model):
    run_api(run, model)


@cli.command("report")
@click.option("--run", required=True)
@click.option("--models", default=",".join(MODELS), show_default=True)
def report_cmd(run, models):
    rd = run_dir(run)
    summary = write_report(rd, [m for m in models.split(",") if (rd / f"{m}.jsonl").exists()])
    for s in summary:
        click.echo(json.dumps({k: (round(v, 4) if isinstance(v, float) else v) for k, v in s.items()}))
    click.echo(f"wrote {rd}/groups.csv, models.csv")


@cli.command("prepare-sizes")
@click.option("--run", required=True)
@click.option("--n", default=150, show_default=True)
@click.option("--size", default=20, show_default=True)
@click.option("--seed", default=0, show_default=True)
def prepare_sizes_cmd(run, n, size, seed):
    import gzip
    with gzip.open("evals/archive/ckpt_rate.jsonl.gz", "rt") as f:
        ckpts = [json.loads(l) for l in f]
    cases = big_groups(ckpts, n=n, size=size, seed=seed)
    rd = run_dir(run)
    rd.mkdir(parents=True, exist_ok=True)
    (rd / "cases.jsonl").write_text("".join(json.dumps(c, ensure_ascii=False) + "\n" for c in cases))
    for p in write_gold_batches(cases, rd):
        click.echo(p)
    (rd / "opus-results").mkdir(exist_ok=True)


@cli.command("sweep")
@click.option("--run", required=True)
@click.option("--model", type=click.Choice(list(MODELS)), required=True)
@click.option("--ks", default="6,8,10,12,15,20", show_default=True)
@click.option("--min-drop", default=0.01, show_default=True,
              help="Stop once pair accuracy is this far below the first k AND the 95% CI excludes 0.")
def sweep_cmd(run, model, ks, min_drop):
    rd = run_dir(run)
    gold, problems = read_gold(rd)
    if problems:
        click.echo(f"gold problems ({len(problems)}): {problems[:5]}")
    ks = [int(k) for k in ks.split(",")]
    base, out = None, []
    for k in ks:
        run_k(run, model, k)
        st = k_stats(rd, model, k, gold)
        per = st.pop("_per_group")
        if base is None:
            base = per
            st.update(diff_vs_base=0.0, ci_lo=0.0, ci_hi=0.0)
        else:
            st["diff_vs_base"], st["ci_lo"], st["ci_hi"] = paired_diff(per, base)
        out.append(st)
        click.echo(json.dumps({k2: (round(v, 5) if isinstance(v, float) else v) for k2, v in st.items()}))
        if st["diff_vs_base"] <= -min_drop and st["ci_hi"] < 0:
            click.echo(f"stopping: k={k} is significantly worse than k={ks[0]}")
            break
    with (rd / f"{model}-sweep.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(out[0]))
        w.writeheader()
        w.writerows(out)
    click.echo(f"wrote {rd}/{model}-sweep.csv")


if __name__ == "__main__":
    cli()
