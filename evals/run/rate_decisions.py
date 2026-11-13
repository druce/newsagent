"""rate_* challenger eval: gpt-6-luna on the OpenAI Decisions API (one call per
article, three predicates) vs. the production gpt-4o-mini rate_* prompts, both
scored against Opus boolean labels.

    evals/work/runs/rate_decisions/<run>/
        cases.jsonl        sampled articles {case_id, id, input_text, sid, bt_z}
        opus-batches/      Agent-dispatch input (25 articles per file)
        opus-results/      Agent output: {"results": [{"id", "quality_low", "importance", "on_topic"}]}
        luna.jsonl         {id, quality_low, importance, on_topic, secs, in_tok, error}
        mini.jsonl         {id, quality_low, importance, on_topic}  (gpt-4o-mini, pre-2026-11-13 prompts)
        report.json        per model x axis: AUC, Brier, log loss, accuracy@0.5

Usage:
    python -m evals.run.rate_decisions prepare --run gold300
    #   ... dispatch one Opus Agent per opus-batches/batch-NNN.json, then:
    python -m evals.run.rate_decisions luna --run gold300
    python -m evals.run.rate_decisions mini --run gold300
    python -m evals.run.rate_decisions report --run gold300
"""
from __future__ import annotations

import json
import math
import os
import random
import statistics
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import click

from evals.prompts.rate_decisions import AXES, NO_CONTENT, QUESTIONS  # noqa: F401

ROOT = Path("evals/work")
CANDIDATES = ROOT / "candidates"
DECISIONS_URL = "https://api.openai.com/v1/decisions"
LUNA = "gpt-6-luna"
PROD_PROMPT = {"quality_low": "rate_quality", "importance": "rate_importance", "on_topic": "rate_on_topic"}
PARALLELISM = 8
BATCH = 25

GOLD_SYSTEM = """\
You label news items for a daily AI newsletter read by AI practitioners, investors and policymakers.
Each item is a title followed by a bullet summary. For every item answer three yes/no questions,
each defined below. Judge each question on its own.

# quality_low
{quality_low}

# importance
{importance}

# on_topic
{on_topic}
"""

GOLD_USER = """\
Label every item in the `items` field of this batch file. Return only JSON of this shape:
{{"results": [{{"id": "<item id>", "quality_low": true|false, "importance": true|false, "on_topic": true|false}}]}}
with one entry per item, every id exactly once."""


def run_dir(run: str) -> Path:
    return ROOT / "runs" / "rate_decisions" / run


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.open()] if path.exists() else []


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))


# ------------------------------------------------------------------ sample
def sample_cases(per_axis: int = 100, seed: int = 0) -> list[dict]:
    """per_axis articles from each rate_* candidate file (they are hard-case
    weighted), deduped by normalized title across files."""
    rng = random.Random(seed)
    seen, out = set(), []
    for axis in AXES:
        pool = read_jsonl(CANDIDATES / f"{PROD_PROMPT[axis]}.jsonl")
        rng.shuffle(pool)
        took = 0
        for c in pool:
            if took >= per_axis:
                break
            if c["key"] in seen:
                continue
            seen.add(c["key"])
            inp = c["input"]  # {"input_text"}; candidate files from before 2026-11-13 hold {"items": [{id, input_text}]}
            text = inp["input_text"] if "input_text" in inp else inp["items"][0]["input_text"]
            out.append({"case_id": c["case_id"], "id": f"a{len(out)}", "input_text": text,
                        "sid": c["provenance"]["sid"], "from_axis": axis})
            took += 1
    return out


def write_gold_batches(cases: list[dict], rd: Path) -> list[Path]:
    bdir = rd / "opus-batches"
    bdir.mkdir(parents=True, exist_ok=True)
    system = GOLD_SYSTEM.format(**QUESTIONS)
    paths = []
    for b, start in enumerate(range(0, len(cases), BATCH)):
        chunk = cases[start:start + BATCH]
        p = bdir / f"batch-{b:03d}.json"
        p.write_text(json.dumps({
            "batch": f"batch-{b:03d}", "ids": [c["id"] for c in chunk],
            "items": [{"id": c["id"], "text": c["input_text"]} for c in chunk],
            "system_prompt": system, "user_prompt": GOLD_USER.format(),
        }, ensure_ascii=False))
        paths.append(p)
    return paths


def read_gold(rd: Path) -> tuple[dict[str, dict[str, bool]], list[str]]:
    gold, problems = {}, []
    for bp in sorted((rd / "opus-batches").glob("batch-*.json")):
        ids = json.loads(bp.read_text())["ids"]
        rp = rd / "opus-results" / bp.name
        if not rp.exists():
            problems.append(f"{bp.name}: no result")
            continue
        got = {str(r["id"]): r for r in json.loads(rp.read_text())["results"]}
        for i in ids:
            r = got.get(i)
            if r is None or any(not isinstance(r.get(a), bool) for a in AXES):
                problems.append(f"{bp.name} {i}: missing or non-boolean label")
            else:
                gold[i] = {a: r[a] for a in AXES}
    return gold, problems


# ----------------------------------------------------------------- models
def decisions_call(input_text: str, model: str = LUNA) -> dict:
    """One Decisions API call with all three predicates; never raises."""
    import httpx
    body = {"model": model, "input": input_text,
            "questions": [{"type": "predicate", "name": a, "instructions": QUESTIONS[a]} for a in AXES]}
    err = None
    for k in range(3):
        t = time.time()
        try:
            r = httpx.post(DECISIONS_URL, json=body, timeout=60,
                           headers={"Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}"})
            r.raise_for_status()
            j = r.json()
            probs = {a["name"]: a["probability"] for a in j["answers"]}
            return {**{a: probs[a] for a in AXES}, "secs": round(time.time() - t, 3),
                    "in_tok": j.get("usage", {}).get("input_tokens"), "error": None}
        except Exception as e:  # noqa: BLE001
            err = f"{type(e).__name__}: {e}"[:300]
            time.sleep(2 ** k)
    return {"error": err}


def run_luna(rd: Path) -> None:
    cases = read_jsonl(rd / "cases.jsonl")
    with ThreadPoolExecutor(PARALLELISM) as ex:
        rows = list(ex.map(lambda c: {"id": c["id"], **decisions_call(c["input_text"])}, cases))
    write_jsonl(rd / "luna.jsonl", rows)
    errs = [r for r in rows if r["error"]]
    click.echo(f"luna: {len(rows)} calls, {len(errs)} errors" + (f" (first: {errs[0]['error']})" if errs else ""))


def run_mini(rd: Path) -> None:
    """The gpt-4o-mini rate_* prompts were replaced by the Decisions predicates
    on 2026-11-13; their gold300 scores are kept in mini.jsonl. To re-run them,
    check out a commit before that switch."""
    raise click.ClickException("gpt-4o-mini rate_* prompts no longer exist; see run_mini docstring")


# ----------------------------------------------------------------- metrics
def auc(scores: list[float], labels: list[bool]) -> float:
    """Mann-Whitney AUC with tie handling."""
    pos = [s for s, y in zip(scores, labels) if y]
    neg = [s for s, y in zip(scores, labels) if not y]
    if not pos or not neg:
        return float("nan")
    wins = sum((p > n) + 0.5 * (p == n) for p in pos for n in neg)
    return wins / (len(pos) * len(neg))


def brier(scores, labels) -> float:
    return statistics.mean((s - y) ** 2 for s, y in zip(scores, labels))


def log_loss(scores, labels, eps: float = 1e-3) -> float:
    return statistics.mean(-math.log(min(max(s if y else 1 - s, eps), 1)) for s, y in zip(scores, labels))


def accuracy(scores, labels) -> float:
    return statistics.mean(float((s >= 0.5) == y) for s, y in zip(scores, labels))


def paired_auc_diff(a: list[float], b: list[float], labels: list[bool], n_boot: int = 2000, seed: int = 0):
    """AUC(a) - AUC(b) with a 95% bootstrap CI over cases."""
    rng = random.Random(seed)
    idx = list(range(len(labels)))
    diffs = []
    for _ in range(n_boot):
        s = [rng.choice(idx) for _ in idx]
        da = auc([a[i] for i in s], [labels[i] for i in s])
        db = auc([b[i] for i in s], [labels[i] for i in s])
        if not (math.isnan(da) or math.isnan(db)):
            diffs.append(da - db)
    diffs.sort()
    return auc(a, labels) - auc(b, labels), diffs[int(.025 * len(diffs))], diffs[int(.975 * len(diffs))]


def report(rd: Path) -> dict:
    gold, problems = read_gold(rd)
    models = {m: {r["id"]: r for r in read_jsonl(rd / f"{m}.jsonl") if not r.get("error")} for m in ("luna", "mini")}
    out = {"n_gold": len(gold), "gold_problems": problems[:10], "axes": {}}
    for a in AXES:
        ids = [i for i in gold if all(a in models[m].get(i, {}) for m in models)]
        y = [gold[i][a] for i in ids]
        ax = {"n": len(ids), "positive_rate": statistics.mean(map(float, y))}
        for m in models:
            s = [float(models[m][i][a]) for i in ids]
            ax[m] = {"auc": auc(s, y), "brier": brier(s, y), "log_loss": log_loss(s, y),
                     "acc@0.5": accuracy(s, y), "distinct": len({round(v, 4) for v in s})}
        d, lo, hi = paired_auc_diff([float(models["luna"][i][a]) for i in ids],
                                    [float(models["mini"][i][a]) for i in ids], y)
        ax["auc_luna_minus_mini"] = {"diff": d, "ci95": [lo, hi]}
        out["axes"][a] = ax
    (rd / "report.json").write_text(json.dumps(out, indent=1))
    return out


# --------------------------------------------------------------------- CLI
@click.group()
def cli():
    pass


@cli.command("prepare")
@click.option("--run", required=True)
@click.option("--per-axis", default=100, show_default=True)
@click.option("--seed", default=0, show_default=True)
def prepare_cmd(run, per_axis, seed):
    rd = run_dir(run)
    rd.mkdir(parents=True, exist_ok=True)
    cases = sample_cases(per_axis, seed)
    write_jsonl(rd / "cases.jsonl", cases)
    (rd / "opus-results").mkdir(exist_ok=True)
    for p in write_gold_batches(cases, rd):
        click.echo(p)


@cli.command("luna")
@click.option("--run", required=True)
def luna_cmd(run):
    import lib  # noqa: F401  (loads .env)
    run_luna(run_dir(run))


@cli.command("mini")
@click.option("--run", required=True)
def mini_cmd(run):
    import lib  # noqa: F401
    import lib.prompts  # noqa: F401
    run_mini(run_dir(run))


@cli.command("report")
@click.option("--run", required=True)
def report_cmd(run):
    out = report(run_dir(run))
    click.echo(f"gold labels: {out['n_gold']}" + (f"; problems: {out['gold_problems']}" if out["gold_problems"] else ""))
    for a, ax in out["axes"].items():
        click.echo(f"\n{a}  n={ax['n']}  positive rate {ax['positive_rate']:.2f}")
        for m in ("luna", "mini"):
            r = ax[m]
            click.echo(f"  {m:5s} AUC {r['auc']:.3f}  Brier {r['brier']:.3f}  logloss {r['log_loss']:.3f}  "
                       f"acc@0.5 {r['acc@0.5']:.3f}  distinct {r['distinct']}")
        d = ax["auc_luna_minus_mini"]
        click.echo(f"  AUC luna - mini: {d['diff']:+.3f} (95% CI {d['ci95'][0]:+.3f} .. {d['ci95'][1]:+.3f})")


if __name__ == "__main__":
    cli()
