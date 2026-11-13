"""Attach article context (summary bullets, or fetched article text) to gold cases.

filter_urls sees only the title, but the gold label should reflect what the
article is actually about. For each gold case we look for the production
summary bullets in the archive (matched by URL, then by normalized title); if
none exist (prod rejected it, so it was never downloaded), we fetch the article
now: httpx + trafilatura first, Bright Data Web Unlocker as fallback.

Writes gold/context.jsonl: {case_id, url, bullets_source, short_summary, bullets, article_text}.
"""
from __future__ import annotations

import gzip
import json
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ARCHIVE = Path(__file__).resolve().parents[1] / "archive"
_TEXT_CHARS = 4000


def norm_title(s: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def index_summaries(records) -> dict:
    """From extract_summaries archive records → {norm_title: output item}.
    Join on title only: summarize-batch ids are batch-local and do NOT match
    gather or checkpoint ids (a (sid, id) join paired 127/223 gold rows with
    the wrong article). Later records win."""
    by_title = {}
    for r in records:
        outs = {str(o.get("id")): o for o in (r.get("output") or {}).get("summaries", [])}
        for it in r["input"]["items"]:
            o = outs.get(str(it["id"]))
            if not o or not (o.get("summary") or "").strip():
                continue
            by_title[norm_title(it["title"])] = o
    return by_title


def lookup(case: dict, by_title: dict) -> dict | None:
    return by_title.get(norm_title(case["title"]))


_JUNK_PREFIXES = ("Top Items:", "Markets S&P", "TOP STORIES")  # memeorandum, biztoc, The Register sidebar


def looks_wrong(title: str, text: str) -> bool:
    """True when fetched text is an aggregator front page, a ticker or a sidebar,
    or shares no content word with the title (we got some other page)."""
    if text.lstrip().startswith(_JUNK_PREFIXES):
        return True
    words = lambda s: set(re.findall(r"[a-z]{4,}", s.lower()))
    t = words(title)
    return bool(t) and not (t & words(text[:2000]))


def _fetch_one(url: str) -> tuple[str | None, str]:
    from lib.fetch.extract import extract_article_text
    from lib.fetch.html import _http_fetch
    html, err = _http_fetch(url)
    if html:
        text = extract_article_text(html)
        if text and len(text) >= 400:
            return text, "http"
    try:
        from lib.fetch.brightdata import scrape_single_url_brightdata
        text, _html, _final, err = scrape_single_url_brightdata(url)
        if text and len(text) >= 400:
            return text, "brightdata"
    except Exception as exc:  # missing key, network, etc.
        err = str(exc)
    return None, f"fetch failed: {err}"


def build_context(gd: Path, gold_rows: list[dict], urls: dict, fetch=_fetch_one, workers: int = 8,
                  prior: dict | None = None) -> list[dict]:
    """prior: {case_id: context record} from an earlier run; fetched text is reused."""
    prior = prior or {}
    with gzip.open(ARCHIVE / "extract_summaries.jsonl.gz", "rt") as fh:
        by_title = index_summaries(json.loads(l) for l in fh)
    out, missing = [], []
    for c in gold_rows:
        hit = lookup(c, by_title)
        rec = {"case_id": c["case_id"], "url": urls.get(c["case_id"], ""), "bullets_source": "none",
               "short_summary": "", "bullets": "", "article_text": ""}
        if hit:
            rec.update(bullets_source="archive", short_summary=hit.get("short_summary") or "",
                       bullets=hit["summary"].strip())
        elif prior.get(c["case_id"], {}).get("article_text"):
            p = prior[c["case_id"]]
            rec.update(bullets_source=p["bullets_source"], article_text=p["article_text"])
        elif rec["url"]:
            missing.append(rec)
        out.append(rec)
    with ThreadPoolExecutor(workers) as ex:
        for rec, (text, how) in zip(missing, ex.map(lambda r: fetch(r["url"]), missing)):
            if text:
                rec.update(bullets_source=f"fetched:{how}", article_text=text[:_TEXT_CHARS])
            else:
                rec["bullets_source"] = how
    titles = {c["case_id"]: c["title"] for c in gold_rows}
    for rec in out:
        if rec["article_text"] and looks_wrong(titles[rec["case_id"]], rec["article_text"]):
            rec.update(bullets_source="wrong_page", article_text="")
    with (gd / "context.jsonl").open("w") as f:
        f.writelines(json.dumps(r, ensure_ascii=False) + "\n" for r in out)
    return out


# ------------------------------------------------- relabel with policy + context
# Editorial policy set by the user on 2026-11-10 (revised 2026-11-11, synced to lib/prompts/filter_urls.py: data centers, chips, wearables, roundups, stock pieces, surveillance, fake media).
POLICY = """Editorial policy for borderline categories (overrides the general rules above; kept in sync with lib/prompts/filter_urls.py):
- Data centers → AI when they are the main subject: construction, siting, power, water, emissions, local opposition, regulation, financing, data-center companies and REITs, and the data-center boom itself (its supply chains, firms built around AI data-center infrastructure). A single non-AI, non-data-center company's earnings or guidance that merely cite data-center demand (utilities, telecom gear, electrical equipment, real-estate brokers) → NOT AI.
- Chips, fabs, semiconductor and memory business (earnings, equipment, capacity, export controls, chip stocks) → NOT AI, unless there is an AI angle: AI chips/accelerators/GPUs, chips competing with Nvidia (e.g. Huawei's leading-edge AI chips), an AI-first company (e.g. NVIDIA), AI demand as the story (e.g. AI-driven memory shortages and prices, AI-driven fab expansion), or an AI application. A chip story with no stated AI link (e.g. a new fab with no AI mention) → NOT AI.
- Retrospective or historical tech stories → NOT AI unless they are about AI or automation.
- General Big Tech, market or stock pieces (indexes, earnings roundups, IPOs), crypto, ordinary software and SaaS, non-AI hardware and devices → NOT AI, unless AI is the subject or a main thread.
- Automation and robotics, including drones, military autonomy, and society, culture and history pieces about automation → AI.
- AI wearables and smart glasses → AI.
- AI surveillance and biometrics → AI: Flock and license-plate readers, facial recognition, face-based age verification.
- Fake or manipulated images and video, and fact-checks of them → AI (presumed deepfakes).
- News roundups / compilation stories (newsletters, "Plus, …" digests, multi-topic briefings) that are only partly about AI → NOT AI.
- Pure stock recommendations or forecasts not of interest to tech readers (price targets, "how to play X stock", buy/sell pieces, routine earnings commentary) → NOT AI, even for AI-first companies like NVIDIA: routine Nvidia earnings commentary, forecasts and stock moves → NOT AI. Significant AI product news (smart glasses, AI models) stays AI even when framed as a stock piece.
"Substantially about AI" means AI is the subject or a main thread of the article, not a passing mention such as "amid the AI boom"."""

RELABEL_SYSTEM = """You are the gold-standard labeler for an evaluation of an AI-news headline filter.
You will receive JSON objects with id, title, source, and (when available) the article's summary bullets or an excerpt of its text. Return **only** a JSON object that satisfies the schema given in the user message, with exactly one label per id. No markdown, no extra keys."""

RELABEL_USER = """Decide whether each article belongs in a daily AI newsletter. Judge what the ARTICLE is about: use the title, and the bullets or text excerpt when given (an excerpt may contain site boilerplate or a paywall; ignore that). When neither bullets nor text are given, judge from the title alone.

General rules:
{rules}

{policy}

For each item return:
- is_ai: the correct label under the rules and the policy.
- confidence: "high", "medium" or "low".
- ai_signal: where the deciding evidence is — "title" (the title alone is enough), "body" (only the bullets/text show it), "both", or "none" (not AI).
- not_a_headline: true if the title is not a real headline (e.g. "440 comments", a section name). Label those is_ai=false.
- context_matches_title: false if the bullets/excerpt are clearly about a different story than the title (then judge from the title alone); true otherwise, including when no context is given.
- rationale: one sentence, at most 25 words, naming the rule or policy line that decides it.

Schema: {{"labels": [{{"id": str, "is_ai": bool, "confidence": "high"|"medium"|"low", "ai_signal": "title"|"body"|"both"|"none", "not_a_headline": bool, "context_matches_title": bool, "rationale": str}}]}}

Input:
{items}"""

RELABEL_SCHEMA = {"type": "object", "properties": {"labels": {"type": "array", "items": {
    "type": "object", "properties": {
        "id": {"type": "string"}, "is_ai": {"type": "boolean"},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        "ai_signal": {"type": "string", "enum": ["title", "body", "both", "none"]},
        "not_a_headline": {"type": "boolean"}, "context_matches_title": {"type": "boolean"},
        "rationale": {"type": "string"}},
    "required": ["id", "is_ai", "confidence", "ai_signal", "not_a_headline", "context_matches_title",
                 "rationale"]}}},
    "required": ["labels"]}

_EXCERPT_CHARS = 2000

EXCERPT_SYSTEM = """You are the gold-standard labeler for an evaluation of an AI-news filter.
You will receive JSON objects with id, title, source and, when the feed provides one, "excerpt" (the feed's short summary of the article). Return **only** a JSON object that satisfies the schema given in the user message, with exactly one label per id. No markdown, no extra keys."""

EXCERPT_USER = """Decide whether each article belongs in a daily AI newsletter. The classifier being graded sees exactly what you see: the title and, when present, the feed excerpt. Judge from those alone, the way a careful human editor would; do not guess at article content beyond them.

General rules:
{rules}

{policy}

For each item return:
- is_ai: the correct label under the rules and the policy.
- confidence: "high", "medium" or "low". Use "low" when a reasonable editor could go either way from the title and excerpt.
- ai_signal: where the deciding evidence is — "title", "body" (only the excerpt shows it), "both", or "none" (not AI).
- not_a_headline: true if the title is not a real headline (e.g. "440 comments", a section name). Label those is_ai=false.
- context_matches_title: false if the excerpt is clearly about a different story than the title (then judge from the title alone); true otherwise, including when no excerpt is given.
- rationale: one sentence, at most 25 words, naming the rule or policy line that decides it.

Schema: {{"labels": [{{"id": str, "is_ai": bool, "confidence": "high"|"medium"|"low", "ai_signal": "title"|"body"|"both"|"none", "not_a_headline": bool, "context_matches_title": bool, "rationale": str}}]}}

Input:
{items}"""


def item_for(case: dict, ctx: dict, excerpt: str = "", mode: str = "article",
             title: str | None = None) -> dict:
    """mode="article": bullets or article text (v2 gold). mode="excerpt": only the
    feed excerpt the title+excerpt filter sees (v3 gold). ``title`` overrides the
    archived title (HTML headlines re-extracted from saved pages)."""
    it = {"title": title or case["title"], "source": case["source"]}
    if mode == "excerpt":
        if excerpt:
            it["excerpt"] = excerpt
        return it
    if ctx.get("bullets"):
        it["bullets"] = ctx["bullets"]
    elif ctx.get("article_text"):
        it["text_excerpt"] = ctx["article_text"][:_EXCERPT_CHARS]
    return it


def prepare_relabel(gd: Path, rules: str, batch_size: int = 12, seed: int = 0,
                    mode: str = "article", excerpts: dict | None = None,
                    titles: dict | None = None, sub: str | None = None) -> list[Path]:
    import csv
    import random
    cands = list(csv.DictReader((gd / "candidates.csv").open()))
    ctx = {r["case_id"]: r for r in (json.loads(l) for l in (gd / "context.jsonl").open())}
    excerpts, titles = excerpts or {}, titles or {}
    random.Random(seed).shuffle(cands)
    sub = sub or ("relabel" if mode == "article" else "relabel-excerpt")
    system, user = (RELABEL_SYSTEM, RELABEL_USER) if mode == "article" else (EXCERPT_SYSTEM, EXCERPT_USER)
    bd = gd / f"{sub}-batches"
    bd.mkdir(exist_ok=True)
    (gd / f"{sub}-results").mkdir(exist_ok=True)
    paths = []
    for b, start in enumerate(range(0, len(cands), batch_size)):
        chunk = cands[start:start + batch_size]
        its = [{"id": str(i), **item_for(c, ctx.get(c["case_id"], {}), excerpts.get(c["case_id"], ""), mode,
                                         titles.get(c["case_id"]))}
               for i, c in enumerate(chunk)]
        p = bd / f"batch-{b:03d}.json"
        p.write_text(json.dumps({
            "batch": f"batch-{b:03d}", "ids": [it["id"] for it in its], "items": its,
            "case_ids": [c["case_id"] for c in chunk], "system_prompt": system,
            "user_prompt": user.format(rules=rules, policy=POLICY,
                                               items=json.dumps(its, ensure_ascii=False, indent=1)),
            "output_schema": RELABEL_SCHEMA}, indent=1, ensure_ascii=False))
        paths.append(p)
    return paths


def read_labels(gd: Path, sub: str) -> dict:
    out = {}
    for bp in sorted((gd / f"{sub}-batches").glob("batch-*.json")):
        rp = gd / f"{sub}-results" / bp.name
        if not rp.exists():
            continue
        b = json.loads(bp.read_text())
        labels = {str(l["id"]): l for l in json.loads(rp.read_text())["labels"]}
        out.update({cid: labels[lid] for lid, cid in zip(b["ids"], b["case_ids"]) if lid in labels})
    return out


def labeler_context(gd: Path, sub: str = "relabel") -> dict:
    """{case_id: the exact bullets / text_excerpt string in the labeler's batch item}."""
    out = {}
    for bp in sorted((gd / f"{sub}-batches").glob("batch-*.json")):
        b = json.loads(bp.read_text())
        for it, cid in zip(b["items"], b["case_ids"]):
            out[cid] = it.get("bullets") or it.get("text_excerpt") or it.get("excerpt") or ""
    return out


def write_review_v2(gd: Path, models: list[str]) -> Path:
    """gold_review_v2.csv: candidates + context (url, short_summary, bullets or excerpt) +
    v1 (title-only) and v2 (policy + body) Opus labels, low-confidence / changed first."""
    import csv
    cands = list(csv.DictReader((gd / "candidates.csv").open()))
    ctx = {r["case_id"]: r for r in (json.loads(l) for l in (gd / "context.jsonl").open())}
    v1, v2 = read_labels(gd, "adjudicate"), read_labels(gd, "relabel")
    shown = labeler_context(gd)
    s = lambda v: "" if v is None else str(v)
    rows = []
    for c in cands:
        cx, a1, a2 = ctx.get(c["case_id"], {}), v1.get(c["case_id"], {}), v2.get(c["case_id"], {})
        gold = a2.get("is_ai")
        rows.append({
            "case_id": c["case_id"], "gold_reason": c["gold_reason"], "title": c["title"],
            "source": c["source"], "month": c["month"], "url": cx.get("url", ""),
            "context_source": cx.get("bullets_source", ""),
            "context_shown_to_labeler": shown.get(c["case_id"], ""),
            "prod": c["prod_is_ai"], **{m: c.get(f"{m}_is_ai", "") for m in models},
            "luna_prob": c.get("luna_prob", ""), "majority": c["majority"],
            "gold": s(gold), "confidence": s(a2.get("confidence")), "ai_signal": s(a2.get("ai_signal")),
            "not_a_headline": s(a2.get("not_a_headline")),
            "context_matches_title": s(a2.get("context_matches_title")), "rationale": s(a2.get("rationale")),
            "v1_title_only_gold": s(a1.get("is_ai")),
            "changed_from_v1": s(None if gold is None or "is_ai" not in a1 else gold != a1["is_ai"]),
            "gold_matches_majority": s(None if gold is None else str(gold) == c["majority"]),
            "gold_matches_prod": s(None if gold is None else str(gold) == c["prod_is_ai"]),
            "human_is_ai": "", "human_notes": ""})
    rank = {"low": 0, "medium": 1, "high": 2, "": 3}
    rows.sort(key=lambda r: (rank[r["confidence"]], r["changed_from_v1"] != "True", r["gold_reason"] != "split"))
    out = gd / "gold_review_v2.csv"
    with out.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    return out


def write_review_v4(gd: Path, src_csv: Path, out_csv: Path, sub: str = "relabel-v4",
                    flags: dict | None = None) -> Path:
    """New review CSV from a labeled one (src_csv is never modified).

    Keeps src rows, order and human labels; replaces the inputs with exactly what
    the ``sub`` labeler saw (corrected title + excerpt) and the rec_* columns with
    its labels. The previous title / Opus label are kept as v3_title / v3_rec_gold;
    ``flags`` ({case_id: reason}) fills info_flag so the harness shows it."""
    import csv
    flags = flags or {}
    labels = read_labels(gd, sub)
    shown = {}
    for bp in sorted((gd / f"{sub}-batches").glob("batch-*.json")):
        b = json.loads(bp.read_text())
        shown.update(zip(b["case_ids"], b["items"]))
    with src_csv.open(newline="", encoding="utf-8") as f:
        src = list(csv.DictReader(f))
    s = lambda v: "" if v is None else str(v)
    rows = []
    for r in src:
        cid = r["case_id"]
        it, lab = shown.get(cid, {}), labels.get(cid, {})
        rows.append({
            "case_id": cid, "gold_reason": r.get("gold_reason", ""), "month": r.get("month", ""),
            "input_title": it.get("title", r.get("input_title", "")),
            "input_source": it.get("source", r.get("input_source", "")),
            "input_excerpt": it.get("excerpt", ""),
            "rec_gold": s(lab.get("is_ai")), "rec_confidence": s(lab.get("confidence")),
            "rec_ai_signal": s(lab.get("ai_signal")), "rec_rationale": s(lab.get("rationale")),
            "info_flag": flags.get(cid, ""),
            **{k: v for k, v in r.items() if k.startswith("info_")},
            "v3_title": r.get("input_title", ""), "v3_excerpt": r.get("input_excerpt", ""),
            "v3_rec_gold": r.get("rec_gold", ""),
            "human_is_ai": r.get("human_is_ai", ""), "human_notes": r.get("human_notes", ""),
        })
    with out_csv.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    return out_csv


def _bool_label(s: str) -> bool | None:
    s = (s or "").strip().lower()
    return True if s == "true" else False if s == "false" else None


def write_eval_set(review_csv: Path, cases_jsonl: Path, out: Path, policy: str = POLICY) -> Path:
    """Final filter_urls eval set from the human-labeled review CSV: one record per row with
    the exact filter input (title + excerpt), the human label as expected output, and
    provenance from cases.jsonl. Writes the policy the labels follow next to it."""
    import csv
    with Path(review_csv).open(newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    bad = [r["case_id"] for r in rows if _bool_label(r.get("human_is_ai")) is None]
    if bad:
        raise ValueError(f"{len(bad)} unlabeled rows, e.g. {bad[:3]}")
    cases = {}
    for line in Path(cases_jsonl).read_text().splitlines():
        if line.strip():
            c = json.loads(line)
            cases[c["case_id"]] = c
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as f:
        for r in rows:
            item = {"id": "0", "title": r["input_title"]}
            if (r.get("input_excerpt") or "").strip():
                item["excerpt"] = r["input_excerpt"]
            c = cases.get(r["case_id"], {})
            f.write(json.dumps({
                "case_id": r["case_id"], "prompt": "filter_urls", "source": r["input_source"],
                "input": {"items": [item]},
                "expected": {"classifications": [{"id": "0", "is_ai": _bool_label(r["human_is_ai"])}]},
                "notes": r.get("human_notes") or "", "opus_is_ai": _bool_label(r.get("rec_gold")),
                "provenance": c.get("provenance", {}), "strata": c.get("strata", {}),
            }, ensure_ascii=False) + "\n")
    (out.parent / f"{out.stem}.policy.txt").write_text(policy)
    return out
