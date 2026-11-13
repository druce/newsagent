"""Phase B: pick ~1000 eval candidates per prompt from the Phase A archive.

Classification/ranking/clustering prompts get a month-stratified sample that
oversamples "hard" cases, flagged with cheap signals from the production
output itself (e.g. the same title classified both ways on different days,
pairs whose word overlap contradicts the label, scores in the middle of the
range, broken format rules). Prose prompts (write_section onward) get a plain
random sample; their evaluation is designed later.

Every case is one JSON line:
  {case_id, prompt, unit, key, input, prod_output, provenance, strata, tags,
   hard_score, sample}
`input` validates against the prompt's input_schema. Batched prompts are
stored one item per case and re-packed into batches when the eval runs.

    .venv/bin/python -m evals.extract.candidates [--archive evals/archive] [--out evals/work/candidates]
"""
from __future__ import annotations

import json
import math
import random
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Callable, Iterable

import click

from evals.extract.archive import read_jsonl_gz
from lib.utilities import strip_source_attribution

_STOP = set("a an the of to in on for and or with by at from as is are was be its it this that "
            "after over into new says say s".split())

BORDERLINE = re.compile(
    r"\b(robot\w*|chip\w*|semiconductor\w*|gpu\w*|nvidia|data ?cent\w*|crypto\w*|bitcoin|"
    r"quantum|autonomous|self-driving|drone\w*|algorithm\w*|automat\w*|tesla|smart|"
    r"cloud|software|tech|startup\w*|deepfake\w*|surveillance|facial)\b", re.I)


def norm(s: str | None) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).split())


def toks(s: str | None) -> set[str]:
    return {w for w in norm(s).split() if w not in _STOP}


def jaccard(a: set, b: set) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


def month(sid: str) -> str:
    return sid[:7]


def _unit(prompt, rec, item_id, inp, out, *, key, strata=None, tags=None, hard=0.0, unit="item"):
    return {
        "case_id": f"{prompt}:{rec['sid']}:{rec['batch']}:{item_id}",
        "prompt": prompt, "unit": unit, "key": key, "input": inp, "prod_output": out,
        "provenance": {"sid": rec["sid"], "batch": rec["batch"], "id": item_id,
                       "system_prompt_hash": rec.get("system_prompt_hash")},
        "strata": {"month": month(rec["sid"]), **(strata or {})},
        "tags": list(tags or []), "hard_score": float(hard),
    }


def _by_id(rows: list | None, field: str = "id") -> dict:
    return {str(r.get(field)): r for r in rows or []}


def _flag_inconsistent(units: list[dict], label: Callable[[dict], object], weight: float = 2.0):
    """Same key, different production label on different calls → hard."""
    groups = defaultdict(list)
    for u in units:
        groups[u["key"]].append(u)
    for g in groups.values():
        if len({json.dumps(label(u), sort_keys=True) for u in g}) > 1:
            for u in g:
                u["tags"].append("inconsistent_prod")
                u["hard_score"] += weight


# ---------------------------------------------------------------- filter_urls
def filter_units(recs: Iterable[dict], gather: dict) -> list[dict]:
    units = []
    for rec in recs:
        if not rec.get("output"):
            continue
        labels = {str(c["id"]): c["is_ai"] for c in rec["output"].get("classifications", [])}
        for it in rec["input"]["items"]:
            i = str(it["id"])
            if i not in labels:
                continue
            meta = gather.get(rec["sid"], {}).get(i, {})
            title = it["title"]
            tags, hard = [], 0.0
            if BORDERLINE.search(title or ""):
                tags.append("borderline_topic")
                hard += 1.0
            units.append(_unit("filter_urls", rec, i, {"items": [{"id": "0", "title": title}]},
                               {"is_ai": labels[i]}, key=norm(title),
                               strata={"source": meta.get("source"), "label": labels[i]},
                               tags=tags, hard=hard))
    # sources whose production labels are mixed are where judgment is hard
    per_src = defaultdict(list)
    for u in units:
        per_src[u["strata"]["source"]].append(u["prod_output"]["is_ai"])
    for u in units:
        labs = per_src[u["strata"]["source"]]
        rate = sum(labs) / len(labs)
        if len(labs) >= 20 and 0.2 <= rate <= 0.8 and u["prod_output"]["is_ai"] == (rate < 0.5):
            u["tags"].append("minority_label_for_source")
            u["hard_score"] += 0.5
    _flag_inconsistent(units, lambda u: u["prod_output"]["is_ai"])
    # inputs are re-keyed to id "0"; restore the original id inside the unit for auditing
    for u in units:
        u["input"]["items"][0]["id"] = u["provenance"]["id"]
    return units


# ------------------------------------------------------ dedupe_vs_archive, coverage_boost
def pair_units(recs: Iterable[dict]) -> list[dict]:
    units = []
    for rec in recs:
        if not rec.get("output"):
            continue
        prompt = rec["prompt"]
        labels = {str(r["id"]): r["same"] for r in rec["output"].get("results", [])}
        for p in rec["input"]["pairs"]:
            i = str(p["id"])
            if i not in labels:
                continue
            same = labels[i]
            j = jaccard(toks(p["a_title"]), toks(p["b_title"]))
            tags, hard = [], 0.0
            # Pairs reach the judge only after an embedding shortlist, so most
            # are "same" with modest title overlap; flag only the tails.
            if not same and j >= 0.25:
                tags.append("different_but_similar")
                hard += 1.0 + j
            elif not same and j >= 0.15:
                tags.append("ambiguous_overlap")
                hard += 0.5
            elif same and j < 0.1:
                tags.append("same_but_dissimilar")
                hard += 1.0
            pair = {k: v for k, v in p.items() if k != "b_url"}
            key = " || ".join(sorted([norm(p["a_title"]), norm(p["b_title"])]))
            units.append(_unit(prompt, rec, i, {"pairs": [pair]}, {"same": same}, key=key,
                               strata={"label": same, "title_jaccard": round(j, 2)},
                               tags=tags, hard=hard))
    _flag_inconsistent(units, lambda u: u["prod_output"]["same"])
    return units


# ---------------------------------------------------------- extract_summaries
def summary_violations(out: dict, text: str) -> list[str]:
    v = []
    ss, summ = out.get("short_summary") or "", out.get("summary") or ""
    if norm(ss) == "no content" or "no content" in summ.lower():
        v.append("no_content")
    if len(ss.split()) > 25:
        v.append("short_summary_over_25")
    if sum(1 for ln in summ.splitlines() if ln.strip().startswith("-")) != 3 and "no_content" not in v:
        v.append("bullets_not_3")
    if ss and strip_source_attribution(ss) != ss:
        v.append("source_attribution")
    if len(text) >= 8000:
        v.append("text_truncated")
    elif len(text) < 1000:
        v.append("short_text")
    return v


# common, low-information conditions weigh less than real rule breaks
_SUMMARY_TAG_WEIGHT = {"no_content": 0.5, "short_text": 0.3, "text_truncated": 0.3}


def summarize_units(recs: Iterable[dict], catalog: dict) -> list[dict]:
    units = []
    for rec in recs:
        if not rec.get("output"):
            continue
        outs = _by_id(rec["output"].get("summaries"))
        for it in rec["input"]["items"]:
            i = str(it["id"])
            if i not in outs or "text" not in it:
                continue
            o = {k: outs[i].get(k) for k in ("short_summary", "summary")}
            v = summary_violations(o, it["text"])
            meta = catalog.get(rec["sid"], {}).get(norm(it["title"]), {})
            hard = sum(_SUMMARY_TAG_WEIGHT.get(t, 1.0) for t in v)
            units.append(_unit("extract_summaries", rec, i,
                               {"items": [{"id": i, "title": it["title"], "text": it["text"]}]}, o,
                               key=norm(it["title"]),
                               strata={"source": meta.get("site_name") or meta.get("source"),
                                       "text_chars": len(it["text"])},
                               tags=v, hard=hard))
    return units


# ------------------------------------------------------------------- rate_*
RATE_FIELD = {"rate_quality": "quality_low", "rate_on_topic": "on_topic",
              "rate_importance": "importance"}


def _phi(z: float) -> float:
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))


def rate_units(ckpts: Iterable[dict], prompt: str) -> list[dict]:
    field = RATE_FIELD[prompt]
    units = []
    for ck in ckpts:
        rec = {"sid": ck["sid"], "batch": "rate"}
        for it in ck["items"]:
            c = it.get(field)
            if c is None or not it.get("summary"):
                continue
            tags, hard = [], 0.0
            if 0.3 <= c <= 0.7:
                tags.append("midrange")
                hard += 1.0
            if prompt == "rate_importance" and it.get("bt_z") is not None:
                if abs(c - _phi(float(it["bt_z"]))) > 0.4:
                    tags.append("importance_vs_battle_mismatch")
                    hard += 1.0
            ot, imp = it.get("on_topic"), it.get("importance")
            if ot is not None and imp is not None and ot < 0.3 and imp > 0.6:
                tags.append("axis_conflict")
                hard += 0.5
            text = f"{it.get('title', '')}\n{it['summary']}"
            units.append(_unit(prompt, rec, it["id"], {"input_text": text},
                               {"confidence": c}, key=norm(it.get("title")),
                               strata={"source": it.get("site_name") or it.get("source"),
                                       "bucket": round(c, 1)},
                               tags=tags, hard=hard))
    return units


# -------------------------------------------------------------- battle_prompt
BATTLE_TYPES = ("q1_top", "q2", "q3", "q4_bottom", "mixed")


def battle_units(ckpts: Iterable[dict], n: int = 1000, size: int = 6, seed: int = 0) -> list[dict]:
    """No battle I/O was saved, so groups are built from each session's rated
    articles (the set shown in out/<stamp>_short.html), split into quartiles
    by composite `rating`. n is split evenly over five group types: 6 random
    stories from one quartile (q1_top .. q4_bottom), or "mixed" = one story
    per quartile + 2 more at random. Groups never mix sessions, matching
    production. Reference ranking = composite rating, descending."""
    rng = random.Random(seed)
    sessions = []
    for ck in ckpts:
        pool = [it for it in ck["items"] if it.get("rating") is not None and it.get("summary")]
        if len(pool) < 4 * size:
            continue
        pool.sort(key=lambda it: -float(it["rating"]))
        k = len(pool) // 4
        quart = [pool[:k], pool[k:2 * k], pool[2 * k:3 * k], pool[3 * k:]]
        sessions.append((ck["sid"], pool, quart))
    if not sessions:
        return []

    units = []
    per_type = [n // len(BATTLE_TYPES) + (1 if i < n % len(BATTLE_TYPES) else 0)
                for i in range(len(BATTLE_TYPES))]
    for t, count in zip(BATTLE_TYPES, per_type):
        order = sessions[:]
        rng.shuffle(order)  # spread each type over as many sessions as possible
        for g in range(count):
            sid, pool, quart = order[g % len(order)]
            if t == "mixed":
                grp = [rng.choice(q) for q in quart]
                taken = {id(it) for it in grp}
                grp += rng.sample([it for it in pool if id(it) not in taken], size - 4)
            else:
                grp = rng.sample(quart[BATTLE_TYPES.index(t)], size)
            rng.shuffle(grp)  # input order must not leak the reference ranking
            ranking = [it["id"] for it in sorted(grp, key=lambda it: -float(it["rating"]))]
            rec = {"sid": sid, "batch": "rate"}
            units.append(_unit(
                "battle_prompt", rec, f"{t}-{g}",
                {"items": [{"id": it["id"], "title": it.get("title", ""), "summary": it["summary"]}
                           for it in grp]},
                {"ranking": ranking, "rating": {it["id"]: it["rating"] for it in grp},
                 "bt_z": {it["id"]: it.get("bt_z") for it in grp}},
                key=f"{sid}:{t}:{','.join(sorted(ranking))}", unit="call",
                strata={"group_type": t}, tags=[t]))
    return units


# ------------------------------------------------------- reassign_to_clusters
def reassign_units(recs: Iterable[dict]) -> list[dict]:
    units = []
    for rec in recs:
        if not rec.get("output"):
            continue
        assigned = {str(a["id"]): a["assignment"] for a in rec["output"].get("assignments", [])}
        clusters = rec["input"]["clusters"] or []
        ctoks = {c["name"]: toks(c["name"] + " " + str(c.get("sample_headlines", ""))) for c in clusters}
        for h in rec["input"]["headlines"]:
            i = str(h["id"])
            if i not in assigned:
                continue
            a = assigned[i]
            ht = toks(h["title"] + " " + (h.get("short_summary") or ""))
            scores = {n: jaccard(ht, t) for n, t in ctoks.items()}
            best = max(scores, key=lambda n: scores[n]) if scores else None
            tags, hard = [], 0.0
            if a == "Other":
                tags.append("other")
                hard += 1.0
            elif a not in ctoks:
                tags.append("off_vocabulary")
                hard += 2.0
            elif best and best != a and scores[best] > 1.5 * scores.get(a, 0) + 0.02:
                tags.append("overlap_prefers_other_cluster")
                hard += 1.0
            units.append(_unit("reassign_to_clusters", rec, i,
                               {"headlines": [h], "clusters": clusters}, {"assignment": a},
                               key=norm(h["title"]), strata={"n_clusters": len(clusters)},
                               tags=tags, hard=hard))
    return units


# ----------------------------------------------------------- name_topic_batch
def _word_violation(name: str, lo: int, hi: int) -> bool:
    return not lo <= len((name or "").split()) <= hi


def name_topic_units(recs: Iterable[dict]) -> list[dict]:
    units = []
    for rec in recs:
        if not rec.get("output"):
            continue
        names = _by_id(rec["output"].get("names"), "cluster_id")
        for c in rec["input"]["clusters"]:
            cid = str(c["cluster_id"])
            if cid not in names:
                continue
            name = names[cid].get("name", "")
            tags, hard = [], 0.0
            if _word_violation(name, 4, 8):
                tags.append("name_words_out_of_range")
                hard += 1.0
            if len(c.get("headlines", "")) > 1500:
                tags.append("large_cluster")
                hard += 0.5
            units.append(_unit("name_topic_batch", rec, cid, {"clusters": [c]}, {"name": name},
                               key=f"{rec['sid']}:{cid}", tags=tags, hard=hard))
    return units


# ------------------------------------------------------------ whole-call units
def call_units(recs: Iterable[dict], prompt: str, out_fn: Callable[[dict], object] = lambda r: r["output"],
               hard_fn: Callable[[dict], tuple[list, float]] | None = None) -> list[dict]:
    units = []
    for rec in recs:
        if rec.get("output") is None:
            continue
        tags, hard = hard_fn(rec) if hard_fn else ([], 0.0)
        units.append(_unit(prompt, rec, rec["batch"], rec["input"], out_fn(rec),
                           key=f"{rec['sid']}:{rec['batch']}", unit="call", tags=tags, hard=hard))
    return units


def noise_hard(rec) -> tuple[list, float]:
    out, n = rec["output"], len(rec["input"]["headlines"] or [])
    tags, hard = [], 0.0
    un = len(out.get("unclustered_ids", []))
    if n and un / n > 0.8:
        tags.append("mostly_unclustered")
        hard += 1.0
    if any(_word_violation(c.get("name", ""), 4, 8) for c in out.get("proposed_clusters", [])):
        tags.append("name_words_out_of_range")
        hard += 1.0
    return tags, hard


def consolidate_hard(rec) -> tuple[list, float]:
    out = rec["output"]
    tags, hard = [], 0.0
    finals = out.get("final_names", [])
    if not 7 <= len(finals) <= 15:
        tags.append("section_count_out_of_range")
        hard += 1.0
    if "Other" in finals:
        tags.append("other_in_final_names")
        hard += 1.0
    if any(m.get("final_name") not in finals and m.get("final_name") != "Other"
           for m in out.get("mapping", [])):
        tags.append("mapping_off_vocabulary")
        hard += 2.0
    return tags, hard


def write_section_output(rec) -> dict:
    o = rec["output"]
    return {"section_markdown": o.get("final_section_markdown"), "loop": {
        k: o.get(k) for k in ("iterations", "scores", "feedbacks", "accepted")}}


def critique_section_units(recs: Iterable[dict]) -> list[dict]:
    """Only the final section survives; its last critique judged it only when
    the loop ended by accepting (otherwise the last critique judged the draft
    before the final improve pass)."""
    units = []
    for rec in recs:
        o = rec.get("output") or {}
        if not o.get("accepted") or not o.get("scores") or not o.get("final_section_markdown"):
            continue
        units.append(_unit("critique_section", rec, rec["batch"],
                           {"section_markdown": o["final_section_markdown"]},
                           {"score": o["scores"][-1], "feedback": (o.get("feedbacks") or [""])[-1],
                            "accept": True},
                           key=f"{rec['sid']}:{rec['batch']}", unit="call"))
    return units


def sitename_units(recs: Iterable[dict]) -> list[dict]:
    units = []
    for rec in recs:
        if not rec.get("output"):
            continue
        outs = _by_id(rec["output"].get("results"))
        for it in rec["input"]["items"]:
            i = str(it["id"])
            if i in outs:
                units.append(_unit("sitename", rec, i, {"items": [it]},
                                   {"site_name": outs[i].get("site_name")}, key=it["domain"]))
    return units


# ------------------------------------------------------------------ sampling
def _round_robin(buckets: dict[str, list], k: int) -> list:
    out, months = [], sorted(buckets)
    while len(out) < k and any(buckets[m] for m in months):
        for m in months:
            if buckets[m] and len(out) < k:
                out.append(buckets[m].pop(0))
    return out


def _cap_tags(ordered: list[dict], k: int, max_tag_share: float) -> list[dict]:
    """Reorder so no single tag fills more than max_tag_share of the first k;
    overflow is deferred to the end (still usable if nothing else is left)."""
    cap = max(1, int(k * max_tag_share))
    counts: Counter = Counter()
    ok, deferred = [], []
    for u in ordered:
        if any(counts[t] >= cap for t in u["tags"]):
            deferred.append(u)
        else:
            counts.update(u["tags"])
            ok.append(u)
    return ok + deferred


def _select_one(uniq: list[dict], n: int, hard_frac: float, rng: random.Random,
                max_tag_share: float) -> list[dict]:
    if len(uniq) <= n:
        for u in uniq:
            u["sample"] = "hard" if u["hard_score"] > 0 else "random"
        return list(uniq)

    hard = [u for u in uniq if u["hard_score"] > 0]  # ordered by -hard_score, random ties
    n_hard = min(round(n * hard_frac), len(hard))
    hb = defaultdict(list)
    for u in _cap_tags(hard, n_hard, max_tag_share):
        hb[u["strata"]["month"]].append(u)
    picked = _round_robin(hb, n_hard)
    for u in picked:
        u["sample"] = "hard"

    ids = {id(u) for u in picked}
    rest = [u for u in uniq if id(u) not in ids]
    rng.shuffle(rest)
    rb = defaultdict(list)
    for u in rest:
        rb[u["strata"]["month"]].append(u)
    more = _round_robin(rb, n - len(picked))
    for u in more:
        u["sample"] = "random"
    return picked + more


def select(units: list[dict], n: int, hard_frac: float, seed: int = 0,
           balance_label: bool = False, max_tag_share: float = 0.4) -> list[dict]:
    """Dedupe by key (keeping the hardest copy), then take round(n*hard_frac)
    hard cases (tag-capped) and fill the rest at random, both spread evenly
    across months. With balance_label, each strata["label"] value gets an
    equal share of n, or all of it when scarcer (the remainder goes to the
    other labels)."""
    rng = random.Random(seed)
    units = list(units)
    rng.shuffle(units)
    units.sort(key=lambda u: -u["hard_score"])
    seen, uniq = set(), []
    for u in units:
        if u["key"] not in seen:
            seen.add(u["key"])
            uniq.append(u)

    if not balance_label:
        return _select_one(uniq, n, hard_frac, rng, max_tag_share)

    groups = defaultdict(list)
    for u in uniq:
        groups[json.dumps(u["strata"].get("label"))].append(u)
    quotas, left = {}, n
    for i, (lab, g) in enumerate(sorted(groups.items(), key=lambda kv: len(kv[1]))):
        share = left // (len(groups) - i)
        quotas[lab] = min(len(g), share)
        left -= quotas[lab]
    out = []
    for lab, g in groups.items():
        out += _select_one(g, quotas[lab], hard_frac, rng, max_tag_share)
    return out


def recent_units(units: list[dict], n: int) -> list[dict]:
    """The n newest units by session id, one per `key` (the newest copy wins),
    marked sample="recent". Used to build eval sets from current production
    traffic rather than a stratified sample."""
    out, seen = [], set()
    for u in sorted(units, key=lambda u: u["provenance"]["sid"], reverse=True):
        if u["key"] in seen:
            continue
        seen.add(u["key"])
        out.append({**u, "sample": "recent"})
        if len(out) == n:
            break
    return out


RECENT_BUILDERS: dict[str, Callable[[list[dict]], list[dict]]] = {
    "coverage_boost": pair_units,
    "dedupe_vs_archive": pair_units,
}


# ---------------------------------------------------------------------- CLI
def _validate(prompt: str, units: list[dict]) -> int:
    import lib.prompts  # noqa: F401  (registers prompts)
    from lib.llm import get_prompt

    schema = get_prompt(prompt).input_schema
    bad = 0
    for u in units:
        try:
            schema.model_validate(u["input"])
        except Exception:
            bad += 1
    return bad


@click.group(invoke_without_command=True)
@click.option("--archive", "arch", default="evals/archive", type=click.Path(path_type=Path))
@click.option("--out", "out_dir", default="evals/work/candidates", type=click.Path(path_type=Path))
@click.option("--n", default=1000, show_default=True)
@click.option("--hard-frac", default=0.7, show_default=True)
@click.option("--seed", default=0, show_default=True)
@click.pass_context
def cli(ctx: click.Context, arch: Path, out_dir: Path, n: int, hard_frac: float, seed: int) -> None:
    """With no subcommand: the stratified ~n candidates per prompt (Phase B)."""
    ctx.obj = {"arch": arch}
    if ctx.invoked_subcommand is None:
        build_all(arch, out_dir, n, hard_frac, seed)


@cli.command("recent")
@click.option("--prompt", required=True, type=click.Choice(sorted(RECENT_BUILDERS)))
@click.option("--n", default=1000, show_default=True)
@click.option("--out", "out_path", required=True, type=click.Path(path_type=Path))
@click.pass_context
def recent_cmd(ctx: click.Context, prompt: str, n: int, out_path: Path) -> None:
    """The n most recent production cases for one prompt, deduped by key."""
    p = ctx.obj["arch"] / f"{prompt}.jsonl.gz"
    units = recent_units(RECENT_BUILDERS[prompt](list(read_jsonl_gz(p))), n)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w") as f:
        for u in units:
            f.write(json.dumps(u, ensure_ascii=False) + "\n")
    sids = [u["provenance"]["sid"] for u in units]
    labels = Counter(str(u["prod_output"].get("same")) for u in units)
    click.echo(f"{out_path}: {len(units)} cases, sids {min(sids)} .. {max(sids)}, "
               f"prod labels {dict(labels)}, invalid={_validate(prompt, units)}")


def build_all(arch: Path, out_dir: Path, n: int, hard_frac: float, seed: int) -> None:
    def load(name):
        p = arch / f"{name}.jsonl.gz"
        return list(read_jsonl_gz(p)) if p.exists() else []

    gather = {r["sid"]: {it["id"]: it for it in r["items"]} for r in load("ckpt_gather")}
    summ_cat = {r["sid"]: {norm(it.get("title")): it for it in r["items"]} for r in load("ckpt_summarize")}
    rate_ck = load("ckpt_rate")
    write_recs = load("write_section")

    plan: list[tuple[str, Callable[[], list[dict]], float]] = [  # (prompt, build, hard_frac)
        ("filter_urls", lambda: filter_units(load("filter_urls"), gather), hard_frac),
        ("extract_summaries", lambda: summarize_units(load("extract_summaries"), summ_cat), hard_frac),
        ("dedupe_vs_archive", lambda: pair_units(load("dedupe_vs_archive")), hard_frac),
        ("coverage_boost", lambda: pair_units(load("coverage_boost")), hard_frac),
        ("rate_quality", lambda: rate_units(rate_ck, "rate_quality"), hard_frac),
        ("rate_on_topic", lambda: rate_units(rate_ck, "rate_on_topic"), hard_frac),
        ("rate_importance", lambda: rate_units(rate_ck, "rate_importance"), hard_frac),
        ("battle_prompt", lambda: battle_units(rate_ck, n=n, seed=seed), 0.0),
        ("reassign_to_clusters", lambda: reassign_units(load("reassign_to_clusters")), hard_frac),
        ("name_topic_batch", lambda: name_topic_units(load("name_topic_batch")), hard_frac),
        ("extract_noise_clusters", lambda: call_units(load("extract_noise_clusters"),
                                                      "extract_noise_clusters", hard_fn=noise_hard), hard_frac),
        ("consolidate_cluster_names", lambda: call_units(load("consolidate_cluster_names"),
                                                         "consolidate_cluster_names",
                                                         hard_fn=consolidate_hard), hard_frac),
        # prose prompts: plain random sample, eval deferred
        ("write_section", lambda: call_units(write_recs, "write_section", write_section_output), 0.0),
        ("critique_section", lambda: critique_section_units(write_recs), 0.0),
        ("critique_newsletter", lambda: call_units(load("critique_newsletter"), "critique_newsletter",
                                                   lambda r: {"critique": r["output"]}), 0.0),
        ("improve_newsletter", lambda: call_units(load("improve_newsletter"), "improve_newsletter",
                                                  lambda r: {"newsletter_markdown": r["output"]}), 0.0),
        ("generate_newsletter_title", lambda: call_units(load("generate_newsletter_title"),
                                                         "generate_newsletter_title",
                                                         lambda r: {"title": r["output"]}), 0.0),
        ("sitename", lambda: sitename_units(load("sitename")), 0.0),
    ]

    out_dir.mkdir(parents=True, exist_ok=True)
    report = {}
    balanced = {"filter_urls", "dedupe_vs_archive", "coverage_boost"}
    for prompt, build, hf in plan:
        units = build()
        picked = select(units, n=n, hard_frac=hf, seed=seed, balance_label=prompt in balanced)
        with (out_dir / f"{prompt}.jsonl").open("w") as f:
            for u in picked:
                f.write(json.dumps(u, ensure_ascii=False) + "\n")
        tags = Counter(t for u in picked for t in u["tags"])
        labels = Counter(str(u["strata"].get("label")) for u in picked if "label" in u["strata"])
        report[prompt] = {
            "pool_units": len(units), "unique_keys": len({u["key"] for u in units}),
            "pool_hard": sum(u["hard_score"] > 0 for u in units),
            "picked": len(picked), "picked_hard": sum(u["sample"] == "hard" for u in picked),
            "months": dict(sorted(Counter(u["strata"]["month"] for u in picked).items())),
            "labels": dict(labels), "top_tags": dict(tags.most_common(8)),
            "invalid_inputs": _validate(prompt, picked),
        }
        r = report[prompt]
        click.echo(f"{prompt:27s} pool={r['pool_units']:6d} hard_pool={r['pool_hard']:6d} "
                   f"picked={r['picked']:4d} hard={r['picked_hard']:4d} invalid={r['invalid_inputs']} "
                   f"labels={r['labels'] or '-'} tags={r['top_tags']}")
    (out_dir / "report.json").write_text(json.dumps(report, indent=2))


if __name__ == "__main__":
    cli()
