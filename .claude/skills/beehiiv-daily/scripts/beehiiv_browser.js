/* beehiiv_browser.js — internal-API auth helpers, run via the Claude Chrome
 * extension's execute_javascript inside the logged-in app.beehiiv.com tab.
 * Auth model + endpoint map: references/beehiiv-internal-api.md.
 *
 * Two-call pattern (execute_javascript does NOT await): kick off async work onto a
 * window.__x global + end on a sentinel string; read JSON.stringify(window.__x) in a
 * second call. No top-level `return` — wrap logic in an IIFE.
 *
 * Paste this whole file at the top of a snippet, then use window.bh.*  (or inline).
 */
function getAuth() {
  const token = localStorage.getItem('token');
  const pub = localStorage.getItem('currentUserPrimaryPublicationId');
  const csrf = decodeURIComponent((document.cookie.match(/_csrf_token=([^;]+)/) || [])[1] || '');
  if (!token) throw new Error('no token in localStorage (logged out?)');
  if (!pub) throw new Error('no currentUserPrimaryPublicationId in localStorage');
  return { token, pub, csrf };
}
const BH_BASE = 'https://app.beehiiv.com/api/v2';
function _hdrs(a, write) {
  const h = { accept: 'application/json', authorization: 'Bearer ' + a.token };
  if (write) { h['content-type'] = 'application/json'; h['x-csrf-token'] = a.csrf; }
  return h;
}
function _withPub(path, a) {
  return BH_BASE + path + (path.includes('?') ? '&' : '?') + 'publication_id=' + a.pub;
}
async function _req(method, path, body) {
  const a = getAuth();
  const r = await fetch(_withPub(path, a), {
    method, headers: _hdrs(a, method !== 'GET'), credentials: 'include',
    body: body ? JSON.stringify(body) : undefined,
  });
  const t = await r.text(); let d; try { d = JSON.parse(t); } catch (e) { d = t; }
  if (!r.ok) throw new Error('HTTP ' + r.status + ' ' + method + ' ' + path + ': ' +
    (typeof d === 'string' ? d.slice(0, 150) : JSON.stringify(d).slice(0, 150)));
  return d;
}
const bhGet = (p) => _req('GET', p);
const bhPost = (p, b) => _req('POST', p, b);
const bhPatch = (p, b) => _req('PATCH', p, b);
const bhDelete = (p) => _req('DELETE', p);

async function findTemplate(name) {
  const data = await bhGet('/post_templates');
  const t = (data.post_templates || []).find(x => (x.name || '').toLowerCase() === name.toLowerCase());
  if (!t) throw new Error('template "' + name + '" not found. Available: ' +
    (data.post_templates || []).map(x => x.name).join(', '));
  return t;
}
async function setTitle(id, title) { return bhPatch('/posts/' + id, { title, web_title: title }); }

/* Create a draft from the named template, then set the title (the server uses the
 * template's own title and ignores the create-body title). Returns
 * { id, template, title, status, draft_url }. */
async function createDraftFromTemplate(templateName, title) {
  const tmpl = await findTemplate(templateName);
  const created = await bhPost('/posts', { post_template_id: tmpl.id, title });
  if (title) await setTitle(created.id, title);
  const d = await bhGet('/posts/' + created.id);
  return { id: created.id, template: tmpl.name, title: d.web_title || d.title, status: d.status, draft_url: d.draft_url };
}

window.bh = { getAuth, bhGet, bhPost, bhPatch, bhDelete, findTemplate, setTitle, createDraftFromTemplate };
'bh helpers ready';
