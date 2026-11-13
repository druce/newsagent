"""The feed excerpt a title+excerpt filter would see, backfilled for eval cases.

Three sources, all captured by gather or saved alongside it:
- rss_summary: stored by the RSS/REST fetchers in gather state (NewsAPI,
  Guardian, NYT, HackerNoon). Hacker News's is just a "Comments" link → empty.
- HTML landing pages (runs/<SID>/pages/<filename>.html): re-extracted with the
  current lib.fetch.extract, which picks the real headline (not the dek) and keeps
  the dek as rss_summary. The corrected headline replaces the archived title.
- Feedly AI landing page (runs/<SID>/pages/Feedly_AI.html): each entry has a
  snippet div after its metadata; gather currently keeps only the link text.

Writes <run>/excerpts.jsonl: {case_id, source, title, excerpt, excerpt_source}.
"""
from __future__ import annotations

import gzip
import json
import re
from collections import Counter
from pathlib import Path

import yaml
from bs4 import BeautifulSoup

from lib.fetch.extract import extract_article_links
from lib.utilities import clean_url

ARCHIVE = Path(__file__).resolve().parents[1] / "archive"
RUNS = Path(__file__).resolve().parents[2] / "runs"
MAX_CHARS = 600
_ROOT = Path(__file__).resolve().parents[2]
SOURCES: dict = {k: v for k, v in (yaml.safe_load((_ROOT / "sources.yaml").read_text()) or {}).items()
                 if isinstance(v, dict) and v.get("type") == "html"}


def clean_summary(s: str | None) -> str:
    if not s:
        return ""
    text = re.sub(r"\s+", " ", BeautifulSoup(s, "html.parser").get_text(" ")).strip()
    text = re.sub(r"\s+([.,;:!?])", r"\1", text)
    if text.lower() in ("comments", ""):
        return ""
    return text[:MAX_CHARS]


def feedly_excerpts(html: str) -> dict[str, str]:
    """{cleaned article url: snippet text} for every Feedly entry that has one."""
    soup = BeautifulSoup(html, "html.parser")
    out = {}
    for a in soup.select("a.EntryTitleLink[href]"):
        entry = a
        while entry is not None and not entry.select_one(".EntryMetadataWrapper"):
            entry = entry.parent
        if entry is None:
            continue
        title_block = a.find_parent("div")
        meta_block = entry.select_one(".EntryMetadataWrapper")
        snippet = ""
        for div in entry.find_all("div", recursive=False):
            if div is title_block or div.find(class_="EntryTitleLink") or div.select_one(".EntryMetadataWrapper") \
                    or (meta_block is not None and div is meta_block.parent):
                continue
            t = re.sub(r"\s+", " ", div.get_text(" ", strip=True))
            if len(t) > len(snippet):
                snippet = t
        if snippet:
            out[clean_url(str(a["href"]))] = snippet[:MAX_CHARS]
    return out


def pick(rss: str, page_dek: str, feedly: str) -> tuple[str, str]:
    if rss:
        return rss, "rss"
    if page_dek:
        return page_dek, "page"
    if feedly:
        return feedly, "feedly"
    return "", "none"


def build_excerpts(run_dir: Path) -> list[dict]:
    """Excerpts for every case in <run_dir>/cases.jsonl (case_id = filter_urls:SID:batch:id)."""
    cases = [json.loads(l) for l in (run_dir / "cases.jsonl").open()]
    want = {}
    for c in cases:
        _, sid, _b, lid = c["case_id"].split(":")
        want[(sid, lid)] = c["case_id"]
    sids = {k[0] for k in want}
    def case_title(c: dict) -> str:
        items = (c.get("input") or {}).get("items") or [{}]
        return items[0].get("title") or ""

    titles = {c["case_id"]: case_title(c) for c in cases}
    gather, by_title = {}, {}
    with gzip.open(ARCHIVE / "ckpt_gather.jsonl.gz", "rt") as fh:
        for line in fh:
            rec = json.loads(line)
            if rec["sid"] not in sids:
                continue
            for it in rec["items"]:
                by_title.setdefault((rec["sid"], it.get("title")), it)
                cid = want.get((rec["sid"], str(it["id"])))
                if cid:
                    gather[cid] = it
    # The (sid, id) join can point at another story when gather was re-run
    # after filter; verify by title and re-join by title within the session.
    for cid, t in titles.items():
        if t and cid in gather and gather[cid].get("title") != t:
            alt = by_title.get((cid.split(":")[1], t))
            if alt is not None:
                gather[cid] = alt
            else:
                del gather[cid]
    feedly_cache: dict[str, dict] = {}
    page_cache: dict[tuple[str, str], dict] = {}

    def page_item(sid: str, source: str, url: str):
        cfg = SOURCES.get(source)
        if not cfg:
            return None
        key = (sid, source)
        if key not in page_cache:
            p = RUNS / sid / "pages" / f"{cfg.get('filename', source.replace(' ', '_'))}.html"
            page_cache[key] = {}
            if p.exists():
                # url + minlength only: include/exclude may have changed since; we match by URL anyway
                arts = extract_article_links(p.read_text(errors="ignore"),
                                             {"url": cfg.get("url", ""), "minlength": cfg.get("minlength")}, source)
                page_cache[key] = {a.url: a for a in arts}
        return page_cache[key].get(clean_url(url))

    out = []
    for c in cases:
        g = gather.get(c["case_id"], {})
        sid = c["case_id"].split(":")[1]
        title = g.get("title") or titles.get(c["case_id"], "")
        page_dek = ""
        if g.get("url"):
            art = page_item(sid, g.get("source", ""), g["url"])
            if art is not None:
                title, page_dek = art.title, clean_summary(art.rss_summary)
        fx = ""
        if g.get("source") == "Feedly AI" and g.get("url"):
            if sid not in feedly_cache:
                p = RUNS / sid / "pages" / "Feedly_AI.html"
                feedly_cache[sid] = feedly_excerpts(p.read_text(errors="ignore")) if p.exists() else {}
            fx = feedly_cache[sid].get(clean_url(g["url"]), "")
        text, src = pick(clean_summary(g.get("rss_summary")), page_dek, fx)
        out.append({"case_id": c["case_id"], "source": g.get("source", c["strata"]["source"]),
                    "title": title, "excerpt": text, "excerpt_source": src})
    with (run_dir / "excerpts.jsonl").open("w") as f:
        f.writelines(json.dumps(r, ensure_ascii=False) + "\n" for r in out)
    return out


def coverage(rows: list[dict]) -> dict:
    tot, got = Counter(r["source"] for r in rows), Counter(r["source"] for r in rows if r["excerpt"])
    return {s: f"{got[s]}/{n}" for s, n in tot.most_common()}
