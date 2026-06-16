# CLAUDE.md — newsagent:beehiiv

Working notes for Claude in this directory. This is a self-contained **skill** that imports
an HTML digest into a beehiiv **draft**. Read `SKILL.md` for the workflow,
`references/beehiiv-internal-api.md` for the auth model + create-from-template primitive,
and `references/beehiiv-image-and-body.md` for the image-upload/body-paste detail before
executing.

## Do not waste time on these (verified dead ends)

- **Do NOT `PATCH` the post body.** `tiptap_state` and `draft_tiptap_state` writes return
  `200` but are silently ignored. The body is authored only via the editor's ydoc sync —
  build hosted-URL HTML, stash it in `localStorage`, open `/posts/<id>/edit`, and feed it to
  the editor with a **synthetic paste** (`window.bh.pasteHostedHtml()` — a
  `ClipboardEvent('paste')` carrying `text/html`).
- **Do NOT rely on native Cmd+C/Cmd+V for the body.** A native Cmd+C on a programmatic
  selection often fails to overwrite the OS clipboard, so the editor pastes whatever was
  copied earlier (e.g. the image base64 from `build_image_clipboard.py` — observed: body
  filled with base64 text). Use the synthetic paste; the Cmd+C/Cmd+V + green-box route is a
  documented fallback only.
- **Do NOT use shell `curl`** against beehiiv — Cloudflare 403s it. Same-origin JS in the
  logged-in tab only.
- **Do NOT base64-inject image bytes** through `execute_javascript` (works but ~250k+
  tokens for a digest's worth). Base64 the whole set into ONE `{basename:b64}` JSON map,
  `pbcopy` it (`scripts/build_image_clipboard.py`), and deliver it with a **single** paste
  into the `scripts/batch_image_uploader.js` catcher, which decodes + uploads all of them
  in-page. The base64 rides the clipboard as text, so it costs no model tokens.
- **Do NOT loop image-by-image** (stage→paste→verify per image). That was ~3 tool calls
  per image (~57 for a 19-image digest) and dominated wall-clock; the batch paste above is
  ~3 calls total and has no index-desync failure mode (uploads are keyed by filename).
- **Do NOT rely on AppleScript `System Events` keystrokes** (need Accessibility perm; they
  silently no-op) or `document.execCommand('copy')` (returns false without a gesture) or
  `navigator.clipboard.read()` (permission-gated). Use the extension **computer tool**
  `key cmd+v` / `cmd+c` — native, targets the tab, no prompt.

## Execution rules

- `execute_javascript` does **not await** → two-call stash pattern (`window.__x` +
  sentinel string; read `JSON.stringify(window.__x)` next call). No top-level `return` —
  use an IIFE.
- Return values containing the publication UUID or a token get redacted by the harness
  ("Cookie/query string data"). Return **counts/booleans**, keep hosted URLs in
  `window.__urls` and use them in-page.
- Navigating to `/edit` wipes `window.*` but NOT `localStorage` (per-origin). Carry the
  rich HTML across in `localStorage['__bh_hosted_html']` (done by `buildHostedHtml`), then on
  the `/edit` page re-inject `build_hosted_html.js`, click the `.ProseMirror` body + real
  `Cmd+A` (PM tracks the selection from real keystrokes; a programmatic DOM `Range` does not),
  and call `window.bh.pasteHostedHtml()` — confirm it returns `defaultPrevented:true`. Clean
  up with `localStorage.removeItem('__bh_hosted_html')` after. The synthetic paste touches no
  OS clipboard, so the copy-then-navigate-then-paste clobber problem doesn't apply.
- Pre-authorize `Bash(osascript:*)` (already in `.claude/settings.local.json`) and run the
  `osascript` clipboard command with **no `cd` prefix** so the rule matches.
- **Computer-tool coordinates are SCREENSHOT pixels, not CSS pixels.** `getBoundingClientRect()`
  returns CSS px; the screenshot is wider (observed 1092 px vs `window.innerWidth` 697 —
  scale ≈ 1.57, and it is NOT `devicePixelRatio`). Multiply JS-computed coords by
  `screenshotWidth / window.innerWidth` before clicking, or clicks land at ~2/3 of the
  intended position. A mis-scaled click once dropped a Backspace into a headline and
  silently deleted a character. After any selection-dependent click, verify
  `window.getSelection().toString()` in JS before acting. To repair a damaged paragraph,
  triple-click it (real gesture → PM selection) and dispatch a synthetic
  `ClipboardEvent('paste')` with the corrected `<p>…</p>` fragment.
- After the body paste, run an integrity diff before calling it done: pbcopy the digest,
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
- This is an undocumented API + a UI-paste step; both can drift. Rediscover endpoints via
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

The flow has produced a complete draft end-to-end (images + links + sources + dividers, in
order). **Use the batch image upload** (`build_image_clipboard.py` → one paste →
`batch_image_uploader.js`); the old per-image loop still exists as a fallback but is ~20×
slower. For the body, **use the synthetic paste** (`window.bh.pasteHostedHtml()`) after a
real click + `Cmd+A` in the `.ProseMirror` body — it feeds TipTap the `text/html` directly
with no OS clipboard, which is what makes it reliable (the native Cmd+C/Cmd+V route pasted
stale clipboard contents). Never publish — leave the result as a draft and report the
`draft_url`.
