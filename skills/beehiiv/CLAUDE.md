# CLAUDE.md — newsagent:beehiiv

Working notes for Claude in this directory. This is a self-contained **skill** that imports
an HTML digest into a beehiiv **draft**. Read `SKILL.md` for the workflow,
`references/beehiiv-internal-api.md` for the auth model + create-from-template primitive,
and `references/beehiiv-image-and-body.md` for the image-upload/body-paste detail before
executing.

## Do not waste time on these (verified dead ends)

- **Do NOT insert images as bare `<img>` / via `setContent(html)`.** They render in the live
  editor but are **silently dropped from the published web page and the EMAIL** (the
  missing-images bug, 2026-06-19). beehiiv only renders first-class `imageBlock` nodes.
  Upload each image to the post **asset** endpoint and insert an `imageBlock` — exactly what a
  real paste does (see next bullet). The old `build_hosted_html.js` (hosted-URL bare-`<img>`)
  is DELETED; the body builder is `scripts/build_doc.js`.
- **Use the ASSET endpoint, not `/images`.** A real paste does
  `POST /api/v2/publications/<pub>/assets`, multipart field **`asset[file]`** (a bare `file`
  400s: "param is missing or the value is empty: asset") → `{id, file:{url}}` at
  `…s3…/uploads/asset/file/<id>/<name>`. Then an `imageBlock` with `attrs.id`=`<id>`,
  `attrs.src`=`file.url+"?t=<ts>"`. The OLD `/images` endpoint returns a
  `media.beehiiv.com/.../uploads/**publication/file**/…` CDN url — a different namespace that
  only works as a bare `<img>`, i.e. the dropped-on-send case. `batch_image_uploader.js` now
  POSTs to `/assets` and stashes `window.__assets[<digest src>] = {id, src, title}`.
- **Do NOT `setContent()` the whole doc at once.** ~50 `imageBlock` node-views mounting in one
  transaction throws **React #185** ("max update depth"), the editor crashes, and NOTHING
  persists (the model looks right in memory but never syncs to ydoc → empty on reload/publish).
  Insert in small chunks (`window.bh.applyChunk(12)`), one chunk per `execute_javascript`
  call (the round-trip lets React settle), until `done:true`; then `stripEmptyParagraphs()`.
- **Do NOT drive the chunks with an in-page `setTimeout` loop.** Background tabs clamp timers
  to ~1/s and the loop races itself (observed: stalls + self-conflict). Drive from the caller.
- **Do NOT `PATCH` the post body.** `tiptap_state` / `draft_tiptap_state` return `200` but are
  silently ignored. Body is authored only via the editor's ydoc sync.
- **Do NOT verify the body by the live `.ProseMirror` DOM**, and **do NOT count `<img>` tags.**
  An `imageBlock` does NOT serialize to `<img>` in `getHTML()` — an `<img>` count is 0 even on
  success. Verify by the editor MODEL: `window.bh.editorCounts()` (parses `editor.getJSON()`),
  counting `imageBlock` NODES. The live DOM can also read empty for seconds during async load.
- **The persistence reload is mandatory.** After chunks + cleanup, wait ~5s, reload `/edit`,
  re-count. If a React crash ate the sync the body comes back empty — only a clean
  reload-and-recount proves it will publish/email.
- **Do NOT use shell `curl`** against beehiiv — Cloudflare 403s it. Same-origin JS only.
- **Do NOT base64-inject image bytes** through `execute_javascript` (~250k+ tokens). Base64
  the whole set into ONE `{src:{n,b}}` JSON map, `pbcopy` it (`build_image_clipboard.py`),
  deliver with a **single** paste into the `batch_image_uploader.js` catcher. Zero model tokens.
- **Do NOT loop image-by-image.** The batch paste is ~3 calls total and keyed by `src`, so
  upload completion order is irrelevant.

## Execution rules

- `execute_javascript` does **not await** → two-call stash pattern (`window.__x` +
  sentinel string; read `JSON.stringify(window.__x)` next call). No top-level `return` —
  use an IIFE.
- Return values containing the publication UUID or a token get redacted by the harness
  ("Cookie/query string data"). Return **counts/booleans**, keep asset URLs in
  `window.__assets` and use them in-page.
- Navigating to `/edit` wipes `window.*` but NOT `localStorage` (per-origin). On the
  dashboard, after uploads, `window.bh.buildDoc(digestHtml)` builds the TipTap doc (real
  `imageBlock`/paragraph/`hr` nodes) and stashes it in `localStorage['__bh_doc']`. On `/edit`,
  re-inject `build_doc.js`, then drive `window.bh.applyChunk(12)` ONE call per
  `execute_javascript` until `{done:true}` (first call seeds via `setContent` of the first
  chunk; later calls `insertContentAt` the end). Then `window.bh.stripEmptyParagraphs()` once.
  Then `localStorage.removeItem('__bh_doc')`.
- **Verify the body by the editor MODEL, not the live DOM.** `window.bh.editorCounts()` parses
  `editor.getJSON()` and counts `imageBlock` NODES (NOT `<img>` — imageBlock doesn't emit one).
  Success = `imgs === N` (TOTAL `<img>` in digest, `grep -c '<img'`), `hrs === P`
  (`grep -c '<hr'`), `links === L` (`grep -c '<a href'`), `emptyParas === 0`, `lastText` ends
  with the "Follow … on Bluesky" footer. **Then RELOAD `/edit` and re-count** — a React-#185
  crash leaves the right model in memory but unsynced, so only a clean reload proves the body
  will persist/publish/email.
- **Computer-tool coordinates are SCREENSHOT pixels, not CSS pixels.** `getBoundingClientRect()`
  returns CSS px; the screenshot is wider (observed 1092 px vs `window.innerWidth` 697 —
  scale ≈ 1.57, and it is NOT `devicePixelRatio`). Multiply JS-computed coords by
  `screenshotWidth / window.innerWidth` before clicking, or clicks land at ~2/3 of the
  intended position. A mis-scaled click once dropped a Backspace into a headline and
  silently deleted a character. After any selection-dependent click, verify
  `window.getSelection().toString()` in JS before acting. To repair a damaged paragraph,
  triple-click it (real gesture → PM selection) and dispatch a synthetic
  `ClipboardEvent('paste')` with the corrected `<p>…</p>` fragment.
- After the body write, run an integrity diff before calling it done: pbcopy the digest,
  paste it into a temporary `<textarea>` catcher on the /edit page, `DOMParser` it in-page,
  and check every digest `<a>` headline and `<p>` text (the post text plus its `- source`
  suffix) appears in `.ProseMirror.textContent` (the digest is flat — no `<h2>` sections).
  This caught both a deleted character and a stray trailing "x" after the footer link.

## Requirements

- Needs the Claude Chrome extension + a logged-in `app.beehiiv.com` tab (macOS for the
  clipboard staging). Playwright is not a substitute.
- `scripts/beehiiv_browser.js` owns the internal-API auth model + the create-from-template
  primitive (`getAuth`, `bhGet/Post/Patch/Delete`, `findTemplate`, `setTitle`,
  `createDraftFromTemplate`); `references/beehiiv-internal-api.md` documents it.
- This is an undocumented API + a live-editor write; both can drift. Rediscover endpoints via
  `performance.getEntriesByType('resource')`, not by hooking `window.fetch`. Update these
  docs when you find differences.

## Inputs are fixed (don't search)

- Digest: `/Users/drucev/projects/newsagent/out/latest-bsky.html` (symlink to the dated
  `out/bsky-YYYY-MM-DD.html` from the `newsagent:bluesky` step; regenerated daily — image &
  post count vary, read it fresh each run). Body shape: a **flat list of posts** (no `<h2>`
  sections) — each post is an optional `<p><img></p>` thumbnail, then either
  `<p><a href>post text</a>  - <em>source</em></p>` or a bare `<p>post text</p>`, then `<hr />`,
  ending with a "Follow … on Bluesky" footer paragraph.
- Images dir: `/Users/drucev/projects/newsagent/download/bsky-images`. Filenames are content
  hashes with **mixed extensions** (`.jpg`/`.png`/`.webp`), referenced as absolute `file://`
  srcs — not relative `download/images/ImageN.jpg`.
- Template: `Daily`. Title: `AI Reading for <Weekday> <Month> <Day>` for today.

## Status

Rebuilt 2026-06-19 to fix images vanishing from the published page + email. Flow:
1. `build_image_clipboard.py` → all imgs (local + remote, AVIF→JPEG via Pillow) as one
   `{src:{n,b}}` clipboard map.
2. `batch_image_uploader.js` → one paste → uploads each to `POST /publications/<pub>/assets`
   (`asset[file]`), stashes `window.__assets[src] = {id, src}`.
3. `build_doc.js`:`buildDoc(html)` → TipTap doc of real `imageBlock` + paragraph + `hr` nodes
   → `localStorage['__bh_doc']`.
4. On `/edit`: `applyChunk(12)` driven once-per-call to `done:true`, then
   `stripEmptyParagraphs()`, then verify `editorCounts()` AND reload-and-recount.

Verified end-to-end 2026-06-19 on a scratch draft: 54 imageBlocks / 58 hr / 55 links,
persisted clean across reload (so it renders in web + email — imageBlock is the same node a
manual paste creates). The mechanism was derived by pasting an image into a scratch draft and
reading the `POST /assets` call + `editor.getJSON()` (see references). Never publish — leave a
draft and report the `draft_url`.
