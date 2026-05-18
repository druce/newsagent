# Phase 7 — Bluesky Digest Pipeline (`news:bluesky`)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Standalone single-skill pipeline that fetches recent posts from a Bluesky account, enriches them with OG metadata + resized images, reorders them via LLM by importance, generates punny section titles, and renders an HTML digest. Independent from the main 11-step pipeline — uses the same `call_prompt` layer but has its own state file flow.

**Architecture:**
- `lib/bluesky/api.py` — direct HTTP (`httpx`) auth + `getAuthorFeed` (no `atproto` SDK; mirrors legacy notebook approach).
- `lib/bluesky/og_tags.py` — fetch OG metadata for embedded URLs.
- `lib/bluesky/images.py` — download + Pillow-resize embedded images to 240px height.
- 2 new prompts: `BSKY_REORDER` (sorts post indexes by importance), `BSKY_SECTION_TITLES` (generates punny titles for groups).
- `lib/steps/bluesky.py` — one-shot CLI that orchestrates fetch → enrich → reorder → titles → render.
- `skills/bluesky/SKILL.md` — agent-facing contract.

**Inputs:** Bluesky handle (CLI arg), env vars `BSKY_USERNAME` + `BSKY_SECRET` for auth.

**Outputs:**
- `out/bsky-YYYY-MM-DD.html` + symlink `out/latest-bsky.html`
- Cached images under `download/bsky-images/`

**Hard constraints:**
- No Anthropic SDK.
- Direct HTTP for Bluesky (no atproto SDK to keep deps minimal).

**Tech additions:** `Pillow>=10` for image resize.

**Reference (read, don't import):** `~/projects/OpenAIAgentsSDK/Compose newsletter from BlueSky posts.ipynb` (cells 4-22). Key endpoints:
- Login: POST `https://bsky.social/xrpc/com.atproto.server.createSession` with `{identifier, password}` → `{accessJwt}`
- Feed: GET `https://bsky.social/xrpc/app.bsky.feed.getAuthorFeed?actor=<handle>&filter=posts_and_author_threads&limit=80` with `Authorization: Bearer <jwt>`

## File structure

| Path | Purpose |
|---|---|
| `lib/bluesky/__init__.py` | package marker |
| `lib/bluesky/api.py` | login + getAuthorFeed |
| `lib/bluesky/og_tags.py` | OG metadata via httpx + BS4 |
| `lib/bluesky/images.py` | download + Pillow resize |
| `lib/prompts/bsky_reorder.py` | LLM reorder (post indexes) |
| `lib/prompts/bsky_section_titles.py` | LLM punny section titles |
| `lib/steps/bluesky.py` | news:bluesky CLI |
| `skills/bluesky/SKILL.md` | contract |
| `tests/test_bluesky_api.py`, `test_bluesky_og.py`, `test_bluesky_images.py` |  |
| `tests/test_prompt_bsky_*.py` |  |
| `tests/test_step_bluesky.py` |  |

---

## Task 1: Add Pillow + create bluesky package

**Files:**
- Modify: `pyproject.toml`, `requirements.txt`
- Create: `lib/bluesky/__init__.py` (empty)

- [ ] Add `"Pillow>=10"` to `[project] dependencies`. Mirror to `requirements.txt`.
- [ ] `mkdir -p lib/bluesky` (and `tests` already exists).
- [ ] Create empty `lib/bluesky/__init__.py`.
- [ ] `.venv/bin/pip install -e ".[dev]"` and verify `import PIL` works.
- [ ] Commit `chore: add Pillow for Bluesky image resize`.

---

## Task 2: `lib/bluesky/api.py` — auth + feed

**Files:**
- Create: `lib/bluesky/api.py`
- Create: `tests/test_bluesky_api.py`

API:
```python
def bsky_login(identifier: str, password: str) -> dict:
    """POST createSession; returns {accessJwt, refreshJwt, did, handle}."""

def bsky_get_author_feed(session: dict, actor: str,
                         filter: str = "posts_and_author_threads",
                         limit: int = 80) -> list[dict]:
    """GET getAuthorFeed; returns list of feed items (each {post, reply?, reason?})."""
```

Tests (4): mock httpx via respx.
- login posts the right body + returns session
- feed includes auth header + returns items
- HTTP error in login raises
- HTTP error in feed raises

Implementation: ~40 LOC. Use `httpx.Client` with `timeout=15s`.

- [ ] Tests → fail → implement → pass → commit `feat(bluesky): atproto auth + getAuthorFeed`.

---

## Task 3: `lib/bluesky/og_tags.py` — OG metadata

**Files:**
- Create: `lib/bluesky/og_tags.py`
- Create: `tests/test_bluesky_og.py`

API:
```python
def get_og_tags(url: str, timeout: float = 10.0) -> dict:
    """Fetch URL, parse <meta property="og:..."> tags. Returns {title, description, image, url} dict.
    Best-effort — missing tags are absent keys. Returns {} on HTTP error.
    """
```

Tests (3): mock httpx.
- Parses og:title, og:description, og:image from HTML
- Returns `{}` on HTTP error
- Handles HTML with no OG tags (returns `{}`)

Use `httpx` + `BeautifulSoup` (both already installed).

- [ ] Tests → fail → implement → pass → commit `feat(bluesky): OG-tag fetcher`.

---

## Task 4: `lib/bluesky/images.py` — download + resize

**Files:**
- Create: `lib/bluesky/images.py`
- Create: `tests/test_bluesky_images.py`

API:
```python
def download_image(url: str, dest_dir: Path, max_bytes: int = 5_000_000) -> Path | None:
    """Download image to dest_dir using URL hash as filename. Returns saved path or None on error."""

def resize_image(input_path: Path, desired_height: int = 240) -> Path:
    """In-place resize maintaining aspect ratio. Returns the path."""
```

Tests (3): with httpx mock + a tiny inline PNG fixture.
- Downloads and saves an image
- Resizes correctly
- Rejects images over max_bytes

Implementation: ~50 LOC.

- [ ] Tests → fail → implement → pass → commit `feat(bluesky): image download + Pillow resize`.

---

## Task 5: `BSKY_REORDER` prompt

**Files:**
- Create: `lib/prompts/bsky_reorder.py`
- Modify: `lib/prompts/__init__.py`
- Create: `tests/test_prompt_bsky_reorder.py`

Schema:
```python
class BskyPost(BaseModel):
    index: int
    text: str
    og_title: Optional[str] = None
    og_description: Optional[str] = None

class BskyReorderInput(BaseModel):
    posts: List[BskyPost]
    @computed_field
    @property
    def posts_json(self) -> str:
        return json.dumps([p.model_dump() for p in self.posts])

class BskyReorderOutput(BaseModel):
    indexes: List[int]  # in order most→least important
```

System prompt (paraphrase notebook cell 21):
```
You are an editor curating a daily digest of Bluesky posts. Given a list of posts with text and optional linked-article metadata, return the post indexes in order of importance for an AI/tech newsletter audience. Consider: significance of news, authoritative sources, novelty, clarity. Return ALL indexes — do not drop any.
```

`default_engine="subagent"`, `reasoning_effort=4`.

Tests (3): registered, schema, system prompt mentions "important" / "order".

- [ ] Tests → fail → implement → pass → commit `feat(prompts): BSKY_REORDER for Bluesky digest`.

---

## Task 6: `BSKY_SECTION_TITLES` prompt

**Files:**
- Create: `lib/prompts/bsky_section_titles.py`
- Modify: `lib/prompts/__init__.py`
- Create: `tests/test_prompt_bsky_section_titles.py`

Schema:
```python
class BskySectionGroup(BaseModel):
    index_range: str  # e.g. "1-5" or "6-10"
    sample_texts: List[str]  # first text of each post in the group

class BskySectionTitlesInput(BaseModel):
    groups: List[BskySectionGroup]
    @computed_field
    @property
    def groups_json(self) -> str:
        return json.dumps([g.model_dump() for g in self.groups])

class BskySectionTitlesOutput(BaseModel):
    titles: List[str]  # one title per group, same order
```

System prompt:
```
You are a newsletter editor generating punny, descriptive section titles for groups of Bluesky posts. Each title should be 4-8 words, capture the theme of the group, and use light wordplay where appropriate. Return one title per group in input order.
```

`default_engine="subagent"`, `reasoning_effort=4`.

Tests (3).

- [ ] Tests → fail → implement → pass → commit `feat(prompts): BSKY_SECTION_TITLES for Bluesky digest`.

---

## Task 7: `news:bluesky` step + SKILL.md

**Files:**
- Create: `lib/steps/bluesky.py`
- Create: `skills/bluesky/SKILL.md`
- Create: `tests/test_step_bluesky.py`

CLI:
```bash
python -m lib.steps.bluesky --user <handle> [--limit 80] [--group-size 5] [--engine ENGINE]
```

Logic:
1. Read `BSKY_USERNAME` + `BSKY_SECRET` from env. Error if missing.
2. `bsky_login` → session.
3. `bsky_get_author_feed(session, user, limit=limit)` → posts.
4. For each post: extract text + any embedded URL. Skip replies/reposts (filter is `posts_and_author_threads` by default).
5. For each unique URL: `get_og_tags(url)`. Cache results in memory.
6. For posts with og:image: `download_image(url, "download/bsky-images")` + `resize_image(path, 240)`.
7. Build `BskyReorderInput` with all posts → `call_prompt("bsky_reorder", ...)` → ordered indexes.
8. Group ordered posts into chunks of `--group-size` (default 5).
9. Build `BskySectionTitlesInput` from grouped sample texts → `call_prompt("bsky_section_titles", ...)` → titles.
10. Render HTML: title H1 ("Bluesky Digest — YYYY-MM-DD"), then per section: H2 title, list of posts (text + image + OG link if present).
11. Write `out/bsky-YYYY-MM-DD.html` + symlink `out/latest-bsky.html`.

Test pattern: mock `bsky_login`, `bsky_get_author_feed`, `get_og_tags`, image helpers, and `call_prompt`. Verify HTML is written and contains key elements.

Tests (3):
- Renders HTML with title + posts
- Requires BSKY env vars (errors clean)
- Uses LLM reorder + titles correctly

Implementation: ~150 LOC.

- [ ] Tests → fail → implement → pass → commits:
  - `feat(steps): news:bluesky digest pipeline`
  - `docs(skills): news:bluesky SKILL.md`

---

## Task 8: End-to-end Phase 7 verification

- [ ] Full `.venv/bin/pytest tests/ --cov=lib --cov-report=term`.
- [ ] Engine resolution check for two new prompts.
- [ ] Tag `phase-7-complete`.

---

## Notes for the implementer

- **No atproto SDK.** Direct httpx is enough; legacy notebook does the same.
- **Image cache** under `download/bsky-images/`. Re-runs same day reuse cached files.
- **Skip reply/repost** filtering — use Bluesky's `filter=posts_and_author_threads` and accept what it returns. Don't try to be clever.
- **Pillow** for image resize, no other image libs.

## Out of scope

- Multi-account aggregation (one handle per run for now)
- Caching OG tags across runs (in-memory only)
- HTML email sending (preview only, like main pipeline's `news:send`)
