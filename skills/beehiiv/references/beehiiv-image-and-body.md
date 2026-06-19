# beehiiv images + body — reference

How to upload images and populate a post body via beehiiv's internal API + editor,
discovered empirically against the live app (May 2026). Pairs with
`references/beehiiv-internal-api.md` (auth model, posts endpoints).

## Image upload endpoint — use ASSETS (what a real paste uses), NOT /images

`POST https://app.beehiiv.com/api/v2/publications/<pub>/assets`
(`<pub>` is in the **path**, not the `?publication_id=` query param the posts endpoints use).

- Multipart body: **`asset[file]`** = the image blob (Rails strong-param; a bare `file` field
  returns `400 {"error":"param is missing or the value is empty: asset"}`). No `url_type`.
- Headers: `Authorization: Bearer <token>`, `x-csrf-token: <csrf>`, `accept: application/json`.
  Do **not** set `Content-Type` — let the browser set the multipart boundary.
- Response: `{ "id":"<uuid>", "file":{ "url":"https://beehiiv-images-production.s3.amazonaws.com/uploads/asset/file/<id>/<filename>" }, "title":"<filename>", "file_type":"image/…", "width":…, "height":…, … }`
- The `imageBlock` you then insert uses `attrs.id = <id>` and `attrs.src = file.url + "?t=<ts>"`.
  The asset id is unique per upload, so filenames need **no** collision prefix.

This is the exact call the editor fires when you paste an image (discovered by pasting into a
scratch draft and reading `read_network_requests` + `editor.getJSON()`).

### Why NOT `POST /publications/<pub>/images`

That older endpoint (multipart field `file`, `url_type:"landscape"`) returns a
`media.beehiiv.com/cdn-cgi/image/.../uploads/**publication/file**/<pub>/landscape_<filename>`
CDN url — a **different namespace** (publication media library, not a post asset). Dropped into
the body as a bare `<img>`, it renders in the live editor but is **silently dropped from the
published web page and the EMAIL**. Only asset-backed `imageBlock` nodes survive. Do not use it.

## Getting image bytes into the page (the transport)

The page can't read local files, and several obvious routes are blocked (see guardrails).
Two clipboard-based routes work; **prefer the batch one**.

### Batch (preferred): all images in ONE paste, as base64 text

The clipboard transport doesn't have to carry *bitmaps* — it can carry **text**. Base64
every referenced image into one `{ "<src>": {n:filename, b:base64} }` JSON map keyed by the
digest's exact `<img src>`, `pbcopy` it, and deliver the whole map with a **single** `Cmd+V`
into a `<textarea>` catcher that `JSON.parse`s the pasted text and, per entry, decodes
b64→`Blob`→multipart POST to **`/assets`** (`asset[file]`), stashing `{id, src}` into
`window.__assets[<src>]`. See `scripts/build_image_clipboard.py` (builder + `pbcopy`; it also
downloads remote images and transcodes AVIF→JPEG with Pillow) and
`scripts/batch_image_uploader.js` (page-side decoder/uploader). Why this is the default:
- **~3 tool calls for the whole set** instead of ~3 *per image*.
- **No index-desync failure mode** — uploads are keyed by `src`, so completion order is irrelevant.
- **Zero model tokens for the bytes** — the base64 lives on the OS clipboard, never in the
  model context (unlike base64-injecting through `execute_javascript`).
- A single large text paste (tens of KB to a few MB) delivers fine via
  `clipboardData.getData('text/plain')`. Verified with full-set (54-image / 2.4 MB) runs.
- Keyed by the literal `src` so `build_doc.js` maps each digest `<img>` straight to its asset.

### Per-image (fallback): one bitmap per paste

Kept for when the batch catcher breaks (e.g. a paste-size limit). **OS clipboard → native
paste event**, one image at a time:

1. macOS: `osascript -e 'set the clipboard to (read (POSIX file "<abs>.jpg") as «class JPEG»)'`
   stages the image on the clipboard. (Pre-authorize `Bash(osascript:*)` to avoid a prompt
   per image. Run it with **no `cd` prefix** so the permission rule matches.)
2. A `contentEditable` catcher with a `paste` listener calls `preventDefault()` and reads
   `e.clipboardData.items` → the image `File` (Chrome usually hands over `image/png` even
   for a JPEG on the pasteboard — fine, beehiiv re-encodes; keep the `.jpg` filename).
3. Trigger the paste with the **Chrome extension computer tool `key cmd+v`** — it targets
   the tab via CDP, so it works even if the OS window isn't frontmost, and needs no
   clipboard-read permission. (AppleScript `System Events` keystrokes need macOS
   Accessibility permission and silently no-op without it.)
4. The listener POSTs the blob to the upload endpoint and stashes `{url}`. Because it
   `preventDefault`s, focus stays in the catcher — no re-click between images. Keep an
   ordered basename list + an index so each paste is named correctly; checkpoint the index
   periodically (a missed paste would desync the mapping).

## Populating the body: build a doc of real nodes + insert it in chunks (REST does NOT work)

`PATCH /posts/<id>` with `tiptap_state` **or** `draft_tiptap_state` returns `200 {timestamp}`
but the body is **silently unchanged**. Only whitelisted fields (`title`, `web_title`, …)
persist via PATCH. The body is authored exclusively through the editor's collaborative (ydoc)
sync.

So: build a TipTap **doc JSON of real beehiiv nodes** (`imageBlock` for images, `paragraph`
with `<a>`→link / `<em>`→italic marks for headlines, `horizontalRule` for `<hr>`) and write it
into the live editor. `scripts/build_doc.js` does the whole thing.

1. (dashboard, after uploads) `window.bh.buildDoc(digestHtml)` walks the digest DOM:
   `<p><img></p>` → `imageBlock` node from `window.__assets[img src]`; `<p>` text → paragraph;
   `<hr>` → `horizontalRule`. Stashes the doc in `window.__bh_doc` and
   `localStorage['__bh_doc']` (survives the nav to `/edit`; `window.*` does not). Returns
   `{nodes, imgs, hrs, links, missing}` — `missing:[]` means every `<img src>` matched an asset.
2. `navigate` to `/posts/<id>/edit`, wait for `.ProseMirror` (with `.editor`), re-inject
   `build_doc.js`.
3. **Insert in CHUNKS — do NOT `setContent` the whole doc.** A single `setContent` of ~50
   `imageBlock` node-views throws **React error #185** ("max update depth exceeded") — the
   renderer loops, the editor crashes, and the doc **never syncs to ydoc** (the in-memory model
   looks right but reloads/publishes EMPTY). Instead call `window.bh.applyChunk(12)` repeatedly,
   **one call per `execute_javascript`** (the tool round-trip is the React-settle time — an
   in-page `setTimeout` loop gets clamped to ~1/s in a background tab and races itself). The
   first call seeds the doc (`setContent` of the first chunk); later calls `insertContentAt` the
   end. Stop at `{done:true}`. ~12 nodes (≈4 images) per chunk is safe; bigger risks #185.
4. **Strip seam blanks.** Each chunk insert leaves an empty paragraph at its boundary. After
   `done:true`, call `window.bh.stripEmptyParagraphs()` **once** (a single delete transaction;
   unmounting nodes does NOT trigger #185).
5. **Verify by the editor MODEL — `window.bh.editorCounts()` (parses `editor.getJSON()`).**
   Count `imageBlock` NODES, NOT `<img>` tags: an `imageBlock` does **not** serialize to `<img>`
   in `getHTML()`, so an `<img>` count is 0 even on success. Success = `imgs === N` (total
   `<img>` in digest = local + already-remote), `hrs === P`, `links === L`, `emptyParas === 0`,
   `lastText` ends with the "Follow … on Bluesky" footer.
6. **Confirm PERSISTENCE (mandatory).** Wait ~5s for "Synced", `navigate` to the same `/edit`
   URL, re-inject `build_doc.js`, re-run `editorCounts()`. Counts must be unchanged. (If the
   body reloads empty, a #185 crash ate the sync — redo step 3 with a smaller chunk.) Then
   `localStorage.removeItem('__bh_doc')`.

`localStorage` is the **only** state that survives navigation — `window.*` (helpers,
`__assets`, `__bh_doc`) is wiped on the load into `/edit`. `buildDoc` saves the doc to
`localStorage['__bh_doc']` before you navigate; `applyChunk` reads it back on `/edit`.

## Verified TipTap `imageBlock` schema (reference)

This is the node shape beehiiv stores, read from `editor.getJSON()` right after a real paste.
`build_doc.js`:`imageBlockNode(asset)` builds exactly this (`attrs.id` = the `/assets` id,
`attrs.src` = `file.url + "?t=<ts>"`). Inserting it is what makes the image survive publish + email.

```json
{
  "type": "imageBlock",
  "attrs": {
    "id": "<uuid>", "alt": "", "src": "<hosted url>", "url": "", "align": "center",
    "title": "<filename>", "width": "100%", "captionUrl": "", "isUploaded": false,
    "borderStyle": "solid", "captionAlign": "center", "allowExternal": false,
    "borderWidthTop": 0, "borderWidthLeft": 0, "borderWidthRight": 0, "borderWidthBottom": 0,
    "borderTopLeftRadius": 0, "borderTopRightRadius": 0, "borderBottomLeftRadius": 0,
    "borderBottomRightRadius": 0, "useIndividualBorderWidth": false, "useIndividualBorderRadius": false
  },
  "content": [ { "type": "figcaption" } ]
}
```

Link paragraph: `paragraph`; content =
`[ {text, marks:[{type:"link", attrs:{rel:"noopener noreferrer nofollow", href, class:null, color:null, target:"_blank"}}]}, {text:"  - "}, {text:source, marks:[{type:"italic"}]} ]`.
Divider: `{ "type":"horizontalRule" }`. Root: `{ "type":"doc", "content":[…] }`.

## Approaches that do NOT work (guardrails)

- **Bare `<img>` in the body / `POST /publications/<pub>/images`** → renders in the live
  editor but is **silently dropped from the published web page AND the email** (different asset
  namespace, `uploads/publication/file/…`). Use `POST /publications/<pub>/assets` + `imageBlock`.
- **`setContent()` of the whole doc** (≥~dozens of imageBlocks) → **React #185** ("max update
  depth"), editor crash, NOTHING persists. Insert in small chunks, caller-driven. (2 imageBlocks
  in one `setContent` is fine; ~50 is not.)
- **In-page `setTimeout` loop to drive the chunks** → background-tab timer clamp (~1/s) +
  self-racing. Drive one chunk per `execute_javascript` call instead.
- **Verifying the body with an `<img>` count or the live `.ProseMirror` DOM** → `imageBlock`
  doesn't emit `<img>` in `getHTML()` (count always 0), and the live DOM reads empty for seconds
  during async load. Count `imageBlock` nodes via `editor.getJSON()`.
- **`asset[file]` field name** is required for `/assets`; a bare `file` field 400s
  ("param is missing or the value is empty: asset").
- **Shell `curl`** → Cloudflare 403 "Just a moment". Browser same-origin only.
- **`fetch('http://127.0.0.1:…')` from the https page** → silently hangs (Chrome
  private-network protection).
- **`navigator.clipboard.readText()/read()`** → rejects / permission-gated. Use a native
  **paste event** (computer tool `key cmd+v`) instead.
- **`PATCH tiptap_state` / `draft_tiptap_state`** → 200 but ignored. Body only via the live editor.
- **`data:` (base64) image `src` in the body** → rejected by the editor. Upload to `/assets` first.
- **Hooking `window.fetch`** to discover endpoints → the SPA captured `fetch` at load. Use
  `read_network_requests` (extension) or `performance.getEntriesByType('resource')`.
- **Base64-injecting image bytes through `execute_javascript`** → ~250k+ tokens. The
  clipboard-paste route avoids it.
