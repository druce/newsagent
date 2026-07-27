---
name: beehiiv
description: Turn the newsagent Bluesky digest (out/latest-bsky.html — a flat list of posts, each a thumbnail, a headline link with the source name appended, and an <hr />; no sections) into a beehiiv DRAFT post, end-to-end, via the Claude Chrome extension. Uploads every image to beehiiv and populates the full post body (links + images in document order). Use when the user wants to build/publish their daily Bluesky roundup, "today's issue", "the bsky digest", or import the bsky digest into beehiiv. Creates the draft from a template and authors the title via beehiiv's internal API, then uploads images and pastes the body.
compatibility: Requires the Claude Chrome extension (Control Chrome tools: tabs_context/tabs_create, execute_javascript/javascript_tool, navigate, and the computer tool for native Cmd+V/click). macOS for `pbcopy` (image-set + digest-HTML clipboard staging). Pillow (in the project venv) for transcoding remote AVIF images. The user must be logged in to beehiiv with a tab open on app.beehiiv.com.
---

# newsagent:beehiiv

Import the newsagent **Bluesky digest** (`out/latest-bsky.html`) into a beehiiv **draft**:
upload all images, then populate the body (a flat list of posts in document order — each a
thumbnail, a headline link with the source name appended, and an `<hr />`; no sections).
Produces a draft only — **never publish**.

Everything runs as same-origin JavaScript inside the logged-in `app.beehiiv.com` tab,
talking to beehiiv's **internal** API (`https://app.beehiiv.com/api/v2`) — the session
cookie, the Bearer token from `localStorage`, and the publication id all ride along
automatically. The draft is created from a saved template and its title set via plain
REST; the **image upload and body population** cannot be done with a REST write and must
go through the editor.

**Read both references before running** — the non-obvious findings there are what make
this work:
- `references/beehiiv-internal-api.md` — auth model, the create-from-template + set-title
  primitive, and the full endpoint map.
- `references/beehiiv-image-and-body.md` — the **asset** upload endpoint (the one a real
  paste uses), the clipboard transport, the **imageBlock + chunked-insert** body method, and
  the "approaches that don't work" list.

## Inputs (these live at fixed locations — do NOT search for them)

- **Digest file** — `/Users/drucev/projects/newsagent/out/latest-bsky.html` (a symlink to
  the dated `out/bsky-YYYY-MM-DD.html`, written by the `newsagent:bluesky` step). Its body is
  a **flat list of posts** (legacy skynet.html format — **no `<h1>`/`<h2>` headers, no
  sections**). Each post is, in order: usually an
  `<p><img alt='image' src='file:///…/download/bsky-images/<hash>.<ext>'></p>` thumbnail, then
  either `<p><a href='…'>post text</a>  - <em>source</em></p>` (linked headline with the
  source name appended) or — for link-less posts — a bare `<p>post text</p>`, then `<hr />`.
  The body ends with a "Follow … on Bluesky" footer paragraph. (Some posts have no image.)
  Regenerated daily in place — read it fresh each run; post and image count vary day to day.
- **Images dir** — `/Users/drucev/projects/newsagent/download/bsky-images`. The digest's
  `file://…/download/bsky-images/<hash>.<ext>` srcs resolve here. Filenames are content
  hashes with **mixed extensions** (`.jpg`, `.png`, `.webp`) — not sequential `ImageN.jpg`.
  Only the images **referenced in the digest** are uploaded, in document order.
- **Template name** — `"Daily"`.
- **Title** — `"AI Reading for <Weekday> <Month> <Day>"` for today (e.g.
  `"AI Reading for Saturday June 13"`). This matches the publication's existing post history.
  **Compute the date string first, in Bash — don't hand-write it:**
  `date +"AI Reading for %A %B %-d"` (`%-d` = no leading zero). Use that exact string for
  the title, and confirm the date with the user only if ambiguous. (An empty
  `"AI Reading for …"` draft may already exist from manual template use — don't confuse it
  with the one you create; match on the post `id` returned from the create call.)
- **Email subject line** — the SAME computed string. `email_subject_line` is a **separate
  field** from the title and is cloned from the template, whose copy is the dateless stub
  `"AI Reading for"`. Patching only `{title, web_title}` leaves the send with subject
  "AI Reading for" (what happened on 2026-07-25). `setTitle()` now patches all three
  (`title`, `web_title`, `email_subject_line`) — verify the readback in step 1.
  (`email_preview_text` is left empty for the user to fill with the day's punny headline.)

## The four execution gotchas

1. `execute_javascript` does **not await** — use the two-call stash pattern: kick off
   async work onto a `window.__x` global + return a sentinel string; read
   `JSON.stringify(window.__x)` in a second call.
2. No top-level `return` — wrap in an IIFE.
3. **The tab must be VISIBLE for every clipboard paste** (`document.visibilityState ===
   'visible'`). The computer tool's `key cmd+v` delivers **no key events at all** to a
   hidden tab (background tab, minimized/occluded window) — the paste silently does
   nothing; JS injection and *clicks* still work, which makes it look like a page bug
   (2026-07-20: two "waiting for paste" failures diagnosed this way). Check
   `document.visibilityState` BEFORE each paste; verify `document.activeElement` is the
   catcher right before `cmd+v`. If `"hidden"`, escalate in this order (2026-07-21 run:
   the first two did NOT flip visibility — the MCP group sat as a background tab group in
   an existing window whose active tab was another site; `tabs_create_mcp` does NOT
   activate the tab it creates, and closing a group tab activates an arbitrary neighbor):
   1. `open -a "Google Chrome"` from Bash (activates Chrome; no Apple-events permission
      needed, unlike `osascript -e 'tell app "Google Chrome"…'` which is usually denied).
   2. `tabs_create_mcp` + `tabs_close_mcp` of the new tab (sometimes re-activates the
      session tab).
   3. **The reliable fix**: `osascript` to **System Events** (allowed even when Apple
      events to Chrome are denied; Accessibility is granted): `tell application "System
      Events" to tell process "Google Chrome"` → `set frontmost to true`, then
      `keystroke "9" using command down`. Cmd+9 selects the window's LAST tab, and
      freshly created MCP-group tabs sit at the end — this flipped `visible:false` →
      `true` when 1–2 didn't. System Events can also inspect/repair windows: read
      `AXPosition`/`AXSize`/`AXMinimized` of `windows`, resize a stunted window, and
      `perform action "AXRaise"`. Diagnose with `w`/`h` from `window.innerWidth`: if they
      match a DIFFERENT window's size, your tab is a background tab of that window.
   Dead end: the `file_upload` tool can't substitute for the paste — it rejects host
   filesystem paths.
4. **Stash the draft id in `localStorage['__bh_draft']` right after creating it.** Tab
   groups can vanish mid-run (observed 2026-07-21: "No tab group exists" after window
   juggling) and `window.__draft` dies with the tab; `localStorage` is per-origin and
   survives, so a fresh tab can pick the flow back up without re-creating the draft. This
   also lets the `/edit` navigation happen in-page (`location.href = '/posts/' + id +
   '/edit'`) so the UUID never has to round-trip through a (redactable) tool result.

Extra: results that contain the publication UUID or an auth token may be redacted by the
harness as "Cookie/query string data". Return **counts / booleans**, not full URLs. The
redaction is trigger-happy: even a benign `'ready; visibility=' + …` string return was
blocked once (2026-07-21) while a `JSON.stringify({...booleans})` of the same state passed
— always end injections with a small JSON object of counts/booleans.

## Why this needs the browser + clipboard (not curl, not REST)

- beehiiv is behind **Cloudflare** — shell `curl` with a valid Bearer token gets a 403
  challenge. Everything runs as same-origin JS in the logged-in tab.
- Image bytes reach the page via the **OS clipboard + a native paste event** (Cmd+V).
  `localhost` fetch is blocked (private-network), and `navigator.clipboard.read()` is
  permission-blocked — but a real Cmd+V hands the page `clipboardData` with no prompt.
- The post **body cannot be written via REST** — `PATCH` of `tiptap_state` /
  `draft_tiptap_state` returns 200 but is silently dropped. The body is authored only
  through the editor's collaborative (ydoc) sync, so we drive the **live in-page TipTap
  editor instance** on the `/edit` page (build a doc of real `imageBlock`/paragraph/`hr`
  nodes and insert it in small chunks) — same-origin JS, no REST body-write.
- **Images must be real `imageBlock` nodes uploaded to the post ASSET endpoint** — exactly
  what the editor itself does on a paste. Bare `<img>` tags (and the publication "images"
  CDN) render in the live editor but are **silently dropped from the published web page and
  the EMAIL**. See `references/beehiiv-image-and-body.md`.

## Workflow

### 0. Pre-flight
- Confirm a logged-in `app.beehiiv.com` tab (`tabs_context` / `get_current_tab`). Create
  an MCP tab and `navigate` it to `https://app.beehiiv.com/` if needed (a fresh tab shares
  the logged-in session). Inject `scripts/beehiiv_browser.js` helpers; `getAuth()` should
  return `token`, `pub`, `csrf`.
- The image set and the digest HTML both reach the page via `pbcopy` (one paste each); no
  `osascript` and no per-image staging. `python3 scripts/build_image_clipboard.py` runs from
  the project root (it uses the venv's Pillow for AVIF transcode — invoke via
  `.venv/bin/python3` if system python lacks Pillow).

### 1. Create the draft (from the "Daily" template)
`createDraftFromTemplate('Daily', '<title>')` (from `scripts/beehiiv_browser.js`) →
`{ id, title, subject, draft_url, status:'draft' }`. It finds the template by name
(`GET /post_templates`), `POST /posts` with `{ post_template_id, title }` to clone it
(the server uses the template's own title, so it then `PATCH`es
`{ title, web_title, email_subject_line }` — all three, or the email ships with the
template's dateless "AI Reading for" subject), and reads back the new post.
**Check the readback: `subject === title`.** Record the post `id`. See
`references/beehiiv-internal-api.md` for the endpoint map and the auth/status-code
details. (You may reuse an existing empty draft instead, but matching on the returned
`id` is safest.)

### 2. Upload every image to the ASSET endpoint — ONE paste for the whole set

**Use the post ASSET endpoint, not `/images`.** A real editor paste does
`POST /api/v2/publications/<pub>/assets` (multipart field **`asset[file]`**) → returns
`{ id, file:{ url:"https://beehiiv-images-production.s3…/uploads/asset/file/<id>/<name>" } }`,
then inserts an `imageBlock` whose `attrs.id` = `<id>` and `attrs.src` = `file.url + "?t=<ts>"`.
The old `/images` endpoint (field `file`, `uploads/publication/file/…` CDN url dropped into a
bare `<img>`) renders in the editor but is **silently dropped on publish + in email** — that
was the missing-images bug. Only asset-backed `imageBlock` nodes survive.

**Do not loop image-by-image.** Bytes ride the clipboard as **text**: one
`{ "<src>": {n:filename, b:base64} }` JSON map keyed by the digest's exact `<img src>`, one
`Cmd+V`, page decodes + uploads all of them. ~3 tool calls total, zero model tokens for the
bytes.

1. **Stage the whole set on the clipboard** (one Bash call):
   `python3 scripts/build_image_clipboard.py` — reads `out/latest-bsky.html` (follows the
   symlink), collects **every** `<img src>` in document order (local `file://…/bsky-images/…`
   AND already-remote `https://…`), reads/downloads the bytes (AVIF and other non-web rasters
   are transcoded to JPEG with Pillow, matching what a real paste would hand the editor),
   writes `{src:{n,b}}` to `/tmp/bh_imgs.json`, and `pbcopy`s it. Prints `{count, local,
   remote, bytes, names}` (never the base64). Pass `DIGEST OUT LIMIT` to override (LIMIT = smoke test).
   The map is keyed by src, so duplicate `<img src>`s collapse: `count` can be LESS than the
   digest's total `<img>` tag count N (2026-07-21: 60 unique srcs for 64 tags) — that's normal;
   `buildDoc` still emits one `imageBlock` per tag and its `imgs` must equal N.
2. **Inject** `scripts/beehiiv_browser.js` then `scripts/batch_image_uploader.js` (the catcher).
3. **One paste:** click the blue catcher once (computer tool, native focus), then `key cmd+v`
   **once**. The catcher `JSON.parse`s the pasted text and, per entry, decodes b64→`Blob` and
   POSTs it to `/publications/<pub>/assets` (field `asset[file]`, filename = `n`, MIME from the
   ext), stashing `{id, src}` into `window.__assets[<src>]` (concurrency-capped, default 6).
4. **Verify once:** poll `JSON.stringify(window.__batch)` until `done:true`, then confirm
   `ok === total`, `errs:[]`, and every `src` in `window.__assets` has an `https://` `.src`.
   (If `error:"bad json…"` the paste delivered partial/empty text — re-focus the catcher and
   re-paste. If it still says `note:"waiting for paste"`, NO paste event fired at all — the
   tab is hidden; see gotcha 3.)

### 3. Build the doc JSON (real beehiiv nodes) and stash it in localStorage
- Get the **raw** digest HTML into the page. Simplest zero-token route: pbcopy it
  (`cat out/latest-bsky.html | pbcopy`), inject `scripts/build_doc.js` + an **empty** catcher
  `<textarea>` (start `value=''` — a placeholder leaks into the paste), click it, `key cmd+v`,
  read `textarea.value`. (Or base64-inject it — works but costs tokens.)
- Call `window.bh.buildDoc(html)`. It walks the digest DOM into a TipTap doc JSON:
  `<p><img></p>` → an `imageBlock` node from `window.__assets[img src]`; `<p>` text →
  a paragraph (`<a>`→link mark, `<em>`→italic); `<hr>` → `horizontalRule`. It stashes the doc
  in `window.__bh_doc` **and `localStorage['__bh_doc']`** (survives the nav to `/edit`).
  Confirm `missing:[]` (every `<img src>` matched an uploaded asset), `imgs === N`,
  `hrs === P`, `links === L` (see counts below).
- Do this on the **dashboard** (after uploads, before navigating).

### 4. Open the editor and insert the doc in CHUNKS (the React-safe path)

A single `setContent()` of the whole doc throws **React error #185** ("max update depth") —
mounting ~50 `imageBlock` node-views in one transaction loops the renderer, the editor
crashes, and **nothing persists** (verified). So insert a few nodes at a time and let React
settle between inserts — and drive the chunks **from the caller** (one `execute_javascript`
per chunk; the tool round-trip is the settle time). An in-page `setTimeout` loop gets clamped
to ~1/s in a background tab and races itself — don't.

- `navigate` to `https://app.beehiiv.com/posts/<id>/edit`. This wipes `window.*` but NOT
  `localStorage`. Wait for `.ProseMirror` (with `.editor`) to exist.
- Re-inject `scripts/build_doc.js` (functions were wiped by the navigation; the doc is in
  `localStorage['__bh_doc']`).
- **Insert in chunks.** Call `window.bh.applyChunk(12)` and read its `{inserted,total,done}`.
  Repeat — **one call per `execute_javascript`** — until `done:true`. The first call seeds the
  doc (`setContent` of the first 12 nodes); later calls append at the end. ~12 nodes (≈4
  images) per chunk stays under the React #185 threshold; do NOT raise it much or loop it
  inside one call. If any call returns `{err:…}`, stop and inspect (a thrown insert means the
  chunk was too big).
- **Strip boundary blanks.** Each chunk insert leaves an empty paragraph at its seam. After
  `done:true`, call `window.bh.stripEmptyParagraphs()` **once** (a single delete transaction —
  unmounting nodes does NOT trigger React #185). It returns the post-cleanup counts.
- **Verify by the editor MODEL — `window.bh.editorCounts()` (parses `editor.getJSON()`), NEVER
  the live `.ProseMirror` DOM.** Count `imageBlock` NODES, not `<img>` tags: an `imageBlock`
  does **not** serialize to `<img>` in `getHTML()`, so an `<img>` count is always 0 even on
  success. Targets:
  - `N` = total `<img>` in the digest = `grep -c '<img' out/latest-bsky.html` (local + remote).
  - `P` = `grep -c '<hr' out/latest-bsky.html` (post count).
  - `L` = `grep -c '<a href' out/latest-bsky.html` (post links + footer link).
  Success = `imgs === N` **and** `hrs === P` **and** `links === L` **and** `emptyParas === 0`
  **and** `lastText` ends with the "Follow … on Bluesky" footer.
  - If `links === L + k`: TipTap **auto-links bare domains** in plain text on insert (seen
    2026-07-19: "ludic.mataroa.blog" in a link-less post gained a link mark). Diff editor
    hrefs against the stashed doc's hrefs to find the extras, then `tr.removeMark` the link
    mark from those text ranges — don't touch the text itself.
- **Confirm it PERSISTED** (this is the whole point — persistence = renders in web + email):
  wait ~5s for "Synced", `navigate` to the same `/edit` URL again, re-inject `build_doc.js`,
  and re-run `editorCounts()`. The counts must be unchanged after reload. (If the body comes
  back empty, a React crash ate the sync — redo step 4 with a smaller chunk.)
- Then `localStorage.removeItem('__bh_doc')`.

### 5. Verify (do NOT publish)
- Confirm the persisted MODEL counts (step 4) one more time after the reload:
  `imgs === N`, `hrs === P`, `links === L`, `emptyParas === 0`, footer last.
- **Re-check the subject line** — `GET /posts/<id>` and confirm `email_subject_line`
  equals the dated title (not the template's bare `"AI Reading for"`). Report it alongside
  the `draft_url` so the user can see it before sending.
- Optionally confirm pixels are loading: read the live DOM's `img` elements (imageBlocks DO
  render to `<img>` in the live DOM) and check the first is `complete && naturalWidth>0`.
- Screenshot the top of the body (leading image + first headline) to eyeball it.
- Report the `draft_url`. Leave it as a draft.

## Resilience
- This is an undocumented API + a live-editor write; both can change. Re-confirm the asset
  endpoint and the `imageBlock` attrs by **pasting one image into a scratch draft** and reading
  the `POST /assets` network call + `editor.getJSON()` (that is exactly how this flow was
  derived). Update the scripts/docs when they drift.
- **Verify the body by the editor MODEL (`editorCounts()` / `editor.getJSON()`), never by the
  live `.ProseMirror` DOM** — the DOM can read empty for seconds during async image load.
- **The persistence reload (step 4) is mandatory.** A `setContent` that throws React #185
  leaves the right model in memory but never syncs to ydoc, so the saved/published/emailed body
  is empty. Only a clean reload-and-recount proves the body will actually send.
- See `references/beehiiv-image-and-body.md` for the verified asset response shape, the
  `imageBlock` node schema, the chunked-insert rationale, and the full guardrail list.
