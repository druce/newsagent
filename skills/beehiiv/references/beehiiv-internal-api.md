# beehiiv internal API — reference

Undocumented API behind the beehiiv dashboard. Base:
`https://app.beehiiv.com/api/v2`. Distinct from the public, documented API at
`https://api.beehiiv.com/v2` (which uses a personal API key and `pub_…` ids, and
restricts post creation to Enterprise plans). For non-Enterprise accounts this
internal-API route is the only way to create drafts programmatically.

## Auth model (the important part)

Same-origin requests from the logged-in dashboard tab work because:

| Piece | Where it lives | How it's sent |
|---|---|---|
| Session | cookies (`_bhp`, session cookie) | automatic on same-origin fetch (`credentials:'include'`) |
| Bearer token | `localStorage.getItem('token')` (a JWT, ~236 chars) | `Authorization: Bearer <token>` header |
| Publication scope | `localStorage.getItem('currentUserPrimaryPublicationId')` (UUID) | **`publication_id=<uuid>` query-string param** |
| CSRF (writes only) | `_csrf_token` cookie (URL-encoded) | `x-csrf-token: <decodeURIComponent(value)>` header |

Observed status codes during discovery:

- No `Authorization` header → **401** ("you are not authorized" with cookies
  alone — the app relies on the Bearer token, not just the cookie).
- `Authorization` present but **no `publication_id` query param** → **403**.
- `Authorization` + `publication_id` query param → **200**.

So the minimum for reads is: `Authorization: Bearer <token>` and
`?publication_id=<uuid>`. For writes, add the `x-csrf-token` header and
`Content-Type: application/json`.

`scripts/beehiiv_browser.js` wraps all of this (`getAuth`, `bhGet/Post/Patch/Delete`,
`findTemplate`, `setTitle`, `createDraftFromTemplate`).

### Failure handling

- **401** → token expired or missing. Re-read `localStorage.getItem('token')`;
  if still failing, ask the user to reload the beehiiv tab (which refreshes the
  in-memory token) and retry.
- **403** → the `publication_id` query param was dropped, or it's wrong. Re-read
  `currentUserPrimaryPublicationId`.

## Endpoints used by this skill

All take `?publication_id=<uuid>` (plus the auth header).

### List post templates
`GET /post_templates?publication_id=PUB`
→ `{ pagination, post_templates: [ { id, name, description, thumbnail, ... } ] }`

### Post template detail
`GET /post_templates/<templateId>?publication_id=PUB`
→ includes `tiptap_state` (editor content), `post_theme_id` (the style/theme),
`web_title`, `email_subject_line`, `html`, and many post settings. This is what
gets cloned into a new post.

### Create post (from template)
`POST /posts?publication_id=PUB`
Body: `{ "post_template_id": "<templateId>", "title": "<stub>" }`
→ `{ "id": "<newPostId>" }`
Notes: server clones the template; new `status` is `"draft"`; the server uses the
template's own title and ignores the `title` field in the body (set it
afterwards via PATCH).

### Update post (e.g. title)
`PATCH /posts/<id>?publication_id=PUB`
Body: `{ "title": "...", "web_title": "..." }`
→ `{ "timestamp": ... }` (200 ack). `web_title` drives the public/web title; the
posts-list `title` also reflects it.

**Body fields are NOT writable via PATCH.** `tiptap_state` / `draft_tiptap_state`
return `200` but are silently dropped — only whitelisted fields (`title`,
`web_title`, …) persist. The body is authored only through the editor's
collaborative (ydoc) sync; see `references/beehiiv-image-and-body.md`.

### Get post detail
`GET /posts/<id>?publication_id=PUB`
→ full post; useful fields: `id`, `title`, `web_title`, `status`,
`post_theme_id`, `tiptap_state`, `draft_url`, `created_at`.

### List posts
`GET /posts?page=1&per_page=10&publication_id=PUB&order=desc&sort=newest_first`
(other params the app sends: `time_zone=America%2FNew_York`,
`return_all_ids=true`, `include_stats=false`)
→ `{ pagination, posts: [ { id, title, status, audience, ... } ] }`

### Delete post (cleanup)
`DELETE /posts/<id>?publication_id=PUB`

Drafts are reversible (`deletable: true`). If you create a malformed draft while
iterating, delete it rather than leaving junk.

### Upload a post image asset (for the body)
`POST /publications/<pub>/assets` — `<pub>` in the **path**. Multipart, field **`asset[file]`**
(NOT `file`; NOT the `?publication_id=` query param). → `{ id, file:{ url }, title, file_type,
width, height }` at `…s3…/uploads/asset/file/<id>/<name>`. This is the call the editor makes on
an image paste; the inserted `imageBlock` references `id` + `file.url`. **Do not** use the
older `POST /publications/<pub>/images` (field `file`, `uploads/publication/file/…`) — that
namespace renders only as a bare `<img>` and is dropped on publish/email. Full detail +
the body-insertion method in `beehiiv-image-and-body.md`.

## "Default style"

The post's style is its `post_theme_id`, inherited from the template when you
create from it. Creating from a template = that template's theme, which is the
intended/default style. Only override the theme if the user explicitly asks for a
*different* one (then look up the publication's themes and PATCH `post_theme_id`).

## Discovery method (for when things change)

This is an undocumented API; treat it as liable to change and **update this skill
(and the script) when you discover differences.** The dashboard is a SPA that
captured `fetch`/`XHR` at load, so re-wrapping `window.fetch` will NOT capture its
traffic, and React-Query caching means re-navigating often fires no new request.
The reliable discovery technique:

```js
// returns full URLs (with query strings) of every real network request the page made
performance.getEntriesByType('resource')
  .map(e => e.name)
  .filter(u => /\/api\/v2\//.test(u));
```

To surface a specific call, click the relevant in-app `<a>` link first (SPA
navigation doesn't reload the page, so any installed state and the auth in
localStorage survive), then re-read the resource list.

This is exactly how the `publication_id` query-param requirement and the precise
`/posts` parameter set were found. Do **not** drive the task by clicking editor
buttons for things the API can do — replicate the internal API call; it's faster
and far less fragile.
