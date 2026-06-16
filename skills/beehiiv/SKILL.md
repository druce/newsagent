---
name: beehiiv
description: Turn the newsagent Bluesky digest (out/latest-bsky.html — a flat list of posts, each a thumbnail, a headline link with the source name appended, and an <hr />; no sections) into a beehiiv DRAFT post, end-to-end, via the Claude Chrome extension. Uploads every image to beehiiv and populates the full post body (links + images in document order). Use when the user wants to build/publish their daily Bluesky roundup, "today's issue", "the bsky digest", or import the bsky digest into beehiiv. Creates the draft from a template and authors the title via beehiiv's internal API, then uploads images and pastes the body.
compatibility: Requires the Claude Chrome extension (Control Chrome tools: get_current_tab/tabs_context, execute_javascript/javascript_tool, navigate, and the computer tool for native Cmd+C/Cmd+V/click). macOS for the `osascript … set the clipboard` image staging. The user must be logged in to beehiiv with a tab open on app.beehiiv.com.
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
- `references/beehiiv-image-and-body.md` — image upload, the clipboard/paste transport,
  the editor-paste body method, and the "approaches that don't work" list.

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
  `"AI Reading for Saturday June 13"`). This matches the publication's existing post history;
  confirm the date with the user only if ambiguous. (An empty `"AI Reading for …"` draft may
  already exist from manual template use — don't confuse it with the one you create; match
  on the post `id` returned from the create call.)

## The two execution gotchas

1. `execute_javascript` does **not await** — use the two-call stash pattern: kick off
   async work onto a `window.__x` global + return a sentinel string; read
   `JSON.stringify(window.__x)` in a second call.
2. No top-level `return` — wrap in an IIFE.

Extra: results that contain the publication UUID or an auth token may be redacted by the
harness as "Cookie/query string data". Return **counts / booleans**, not full URLs.

## Why this needs the browser + clipboard (not curl, not REST)

- beehiiv is behind **Cloudflare** — shell `curl` with a valid Bearer token gets a 403
  challenge. Everything runs as same-origin JS in the logged-in tab.
- Image bytes reach the page via the **OS clipboard + a native paste event** (Cmd+V).
  `localhost` fetch is blocked (private-network), and `navigator.clipboard.read()` is
  permission-blocked — but a real Cmd+V hands the page `clipboardData` with no prompt.
- The post **body cannot be written via REST** — `PATCH` of `tiptap_state` /
  `draft_tiptap_state` returns 200 but is silently dropped. The body is authored only
  through the editor's collaborative (ydoc) sync, so we **paste** it into the editor.

## Workflow

### 0. Pre-flight
- Confirm a logged-in `app.beehiiv.com` tab (`tabs_context` / `get_current_tab`). Create
  an MCP tab and `navigate` it to `https://app.beehiiv.com/` if needed (a fresh tab shares
  the logged-in session). Inject `scripts/beehiiv_browser.js` helpers; `getAuth()` should
  return `token`, `pub`, `csrf`.
- **Pre-authorize the clipboard command** so you don't get one prompt per image: add
  `"Bash(osascript:*)"` to `.claude/settings.local.json` `permissions.allow` (use the
  update-config skill). Run the `osascript` clipboard commands **without** a `cd` prefix
  (absolute paths) so the rule matches the whole command.

### 1. Create the draft (from the "Daily" template)
`createDraftFromTemplate('Daily', '<title>')` (from `scripts/beehiiv_browser.js`) →
`{ id, draft_url, status:'draft' }`. It finds the template by name
(`GET /post_templates`), `POST /posts` with `{ post_template_id, title }` to clone it
(the server uses the template's own title, so it then `PATCH`es `{ title, web_title }`),
and reads back the new post. Record the post `id`. See
`references/beehiiv-internal-api.md` for the endpoint map and the auth/status-code
details. (You may reuse an existing empty draft instead, but matching on the returned
`id` is safest.)

### 2. Upload every image — ONE paste for the whole set (the fast path)

**Do not loop image-by-image.** The bytes don't need to ride the clipboard as bitmaps —
they ride as **text**. Base64 every referenced image into one `{basename: b64}` JSON map,
`pbcopy` it, do a **single** `Cmd+V`, and let the page decode + upload all of them in one
in-page loop. This is ~3 tool calls for the whole set instead of ~3 *per image*, and it
**has no index-desync failure mode** (each upload is keyed by filename, so completion order
is irrelevant). It costs **zero model tokens** for the image bytes (they never enter the
model context — unlike injecting base64 through `execute_javascript`).

1. **Stage the whole set on the clipboard** (one Bash call):
   `python3 scripts/build_image_clipboard.py` — reads `out/latest-bsky.html` (following the
   symlink), pulls the referenced `bsky-images/<hash>.<ext>` basenames **in document order**,
   base64s each file, writes `{basename: b64}` to `/tmp/bh_imgs.json`, and `pbcopy`s it. It
   prints a one-line `{count, names, bytes}` summary (never the base64). Defaults match the
   fixed inputs; pass `DIGEST IMAGES_DIR OUT LIMIT` to override (LIMIT is for a smoke test).
2. **Inject** `scripts/beehiiv_browser.js` then `scripts/batch_image_uploader.js` (the
   catcher). Set `window.__datePrefix = 'YYYYMMDD-'` for this run (avoids cross-run
   filename collisions in the image library).
3. **One paste:** click the blue catcher once (computer tool, native focus), then
   `key cmd+v` **once**. The catcher reads the pasted `text/plain`, `JSON.parse`s it,
   and for each entry decodes b64→`Blob` and POSTs to
   `POST /api/v2/publications/<pub>/images` (multipart `file` + `url_type:"landscape"`),
   stashing `{url}` into `window.__urls[basename]` (concurrency-capped, default 6). The
   uploader sets each Blob's MIME from its extension (`image/jpeg|png|webp`) — beehiiv's
   image CDN serves webp, so mixed formats upload fine; any per-image failure (whatever the
   cause) surfaces in `window.__batch.errs` rather than failing silently.
4. **Verify once:** poll `JSON.stringify(window.__batch)` until `done:true`, then confirm
   `ok === total`, `errs:[]`, and every referenced basename maps to an `https://` url in
   `window.__urls`. (If `error:"bad json…"` the paste didn't deliver the text — re-focus the
   catcher and re-paste; the clipboard still holds it.)

**Fallback** (only if the batch catcher breaks — e.g. a paste-size limit): the old
one-image-at-a-time loop in `scripts/paste_image_uploader.js`, which stages each file via
`osascript … as «class JPEG»` and pastes+verifies per image (watch `window.__pasteIdx` —
a missed paste desyncs every later slot). It works but is ~20× more tool calls. Note: the
`«class JPEG»` clipboard coercion is for JPEGs; for the digest's `.png`/`.webp` thumbnails
the batch path (which sends raw bytes with the right MIME) is the reliable route.

### 3. Build the hosted-URL HTML and stash it in localStorage
- Get the digest HTML into the page **without the clipboard**: read the digest file, base64
  it (`base64 < digest.html | tr -d '\n'`), and in-page decode with
  `new TextDecoder().decode(Uint8Array.from(atob(b64), c=>c.charCodeAt(0)))` (handles the
  digest's unicode — 🔥, smart quotes — which `atob` alone mangles).
- Inject `scripts/build_hosted_html.js` and call `buildHostedHtml(html)`: for each uploaded
  basename it regex-replaces the **whole** `file://…/download/bsky-images/<hash>.<ext>` src
  with `window.__urls[<hash>.<ext>]` (a basename-only swap would leave a dangling `file://`
  prefix), applies `\$`→`$`, stashes `window.__hostedHtml`, **and saves it to
  `localStorage['__bh_hosted_html']`**. Confirm `remainingLocal:0` (no `bsky-images/` paths
  left) and `hostedImgRefs == image count`.
- **Do NOT copy or navigate yet.** `window.*` is wiped on navigation to `/edit`, and the
  clipboard can be clobbered in the gap between a copy and a later paste. `localStorage` is
  per-origin and survives navigation — that's what carries the HTML across. (Today's run
  failed exactly here: copied on the dashboard, navigated, and the clipboard no longer held
  the HTML.)

### 4. Open the editor and paste the body via a synthetic paste event (the robust path)
- `navigate` to `https://app.beehiiv.com/posts/<id>/edit`. (Opening the editor flips
  `has_ydoc:true` — expected and required for this method.) This wipes `window.*` but NOT
  `localStorage`. Wait for `.ProseMirror` to exist before continuing.
- Re-inject `scripts/build_hosted_html.js` (its functions were wiped by the navigation).
- **Clear the template stub with real keystrokes:** computer-tool `left_click` the
  `.ProseMirror` body once (native focus — the title is a separate input), then `Cmd+A`.
  ProseMirror tracks the selection from the *real* keystroke; a programmatic DOM `Range`
  does **not** reliably update PM's internal selection, so the synthetic paste below would
  insert in the wrong place without it. (You may also `Backspace` after `Cmd+A` to empty the
  body first — either way the paste replaces the stub.)
- **Dispatch the synthetic paste:** call `window.bh.pasteHostedHtml()`. It reads the rich
  HTML from `window.__hostedHtml || localStorage['__bh_hosted_html']`, builds a
  `DataTransfer` with `text/html`, and dispatches a `ClipboardEvent('paste')` at the
  `.ProseMirror` node — handing TipTap exactly the HTML, with **no OS clipboard involved**.
  Confirm the return shows `defaultPrevented:true` (ProseMirror handled the paste).
- **Why not native `Cmd+C`/`Cmd+V`?** That OS-clipboard route is fragile: a native `Cmd+C`
  on a programmatic selection often fails to overwrite the clipboard, so the editor pastes
  whatever was copied earlier — e.g. the digest base64 from `build_image_clipboard.py` (this
  broke a run: the body filled with base64 text). The synthetic paste sidesteps the clipboard
  entirely. The Cmd+C/Cmd+V dance (`stageCopyDiv()` + green box) is kept only as a documented
  fallback in `scripts/build_hosted_html.js`.
- The editor imports the `<a>` links, `<p>` headline/text paragraphs (including the
  ` - <em>source</em>` suffix), and images — and **re-hosts the pasted `media.beehiiv.com`
  images onto its own S3** (`beehiiv-images-production.s3…`). It auto-syncs (status shows
  "Synced"). Then `localStorage.removeItem('__bh_hosted_html')` to clean up. (Allow a few
  seconds for the image re-hosting before verifying.) The digest is flat — there are no
  `<h2>` sections to import.

### 5. Verify (do NOT publish)
- Query the `.ProseMirror` DOM: counts of `img` (== image count) and `a` (== headline /
  linked-post count); first image `complete && naturalWidth>0`; last node is your
  final paragraph (the "Follow … on Bluesky" footer).
- Screenshot the top of the body (leading image + first headline) to eyeball it.
- Report the `draft_url`. Leave it as a draft.

## Resilience
- This is an undocumented API + a UI-paste step; both can change. If the editor stops
  importing images from pasted `<img>`, fall back to placing the cursor and pasting each
  image blob at its position (much slower). If REST body-write ever starts working,
  prefer it — but as of this writing it does not.
- See `references/beehiiv-image-and-body.md` for the verified TipTap `imageBlock` schema,
  the upload response shape, and the full guardrail list.
