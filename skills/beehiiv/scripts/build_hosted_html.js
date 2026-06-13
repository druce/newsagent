/* build_hosted_html.js — run in the app.beehiiv.com tab AFTER all images are uploaded
 * (window.__urls populated). Produces the rich HTML, stashes it in localStorage (which
 * survives navigation to /edit), and renders it for a native Cmd+C copy.
 *
 * Get the digest HTML into the page WITHOUT the clipboard (robust): base64 the file
 *   (`base64 < digest.html | tr -d '\n'`) and decode in-page —
 *   new TextDecoder().decode(Uint8Array.from(atob(b64), c=>c.charCodeAt(0)))
 *   (atob alone mangles the digest's unicode: 🔥, smart quotes).
 *
 * STEP A (on the dashboard, after uploads) — build window.__hostedHtml AND
 * localStorage['__bh_hosted_html'] (swap local img srcs for hosted URLs, clean \$):
 */
window.bh = window.bh || {};
window.bh.HOSTED_KEY = '__bh_hosted_html';
window.bh.buildHostedHtml = function (html) {
  var replaced = 0, missing = [];
  Object.keys(window.__urls).forEach(function (base) {
    var url = window.__urls[base];
    if (typeof url !== 'string') { missing.push(base); return; }
    // bsky srcs are absolute file:// URLs, e.g.
    //   file:///Users/.../download/bsky-images/<hash>.<ext>
    // Replace the WHOLE file:// URL (prefix + basename) with the hosted URL — a
    // basename-only swap would leave a dangling `file:///.../download/bsky-images/`.
    var esc = base.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    var re = new RegExp("file://[^'\"]*?bsky-images/" + esc, 'g');
    var before = html;
    html = html.replace(re, url);
    if (html !== before) replaced++;
  });
  html = html.replace(/\\\$/g, '$');                 // \$  ->  $
  window.__hostedHtml = html;
  try { localStorage.setItem(window.bh.HOSTED_KEY, html); } catch (e) {}  // survives nav to /edit
  return {
    replaced: replaced, missing: missing,
    remainingLocal: (html.match(/bsky-images\//g) || []).length,
    hostedImgRefs: (html.match(/media\.beehiiv\.com/g) || []).length,
    htmlLen: html.length,
  };
};
// usage: JSON.stringify(window.bh.buildHostedHtml(decodedDigestHtml))

/* STEP B (ON the /posts/<id>/edit page — re-inject this file first; window.* was wiped by
 * the navigation but localStorage was not). Render the hosted HTML into a contentEditable
 * and select it, then issue a NATIVE Cmd+C with the computer tool (execCommand('copy')
 * returns false without a gesture). Do the Cmd+C and the editor Cmd+V back-to-back on this
 * page — no navigation in between — so the clipboard can't be clobbered by a page load. */
window.bh.stageCopyDiv = function () {
  var html = window.__hostedHtml || localStorage.getItem(window.bh.HOSTED_KEY);
  if (!html) return { error: 'no hosted html in window or localStorage' };
  window.__hostedHtml = html;
  var old = document.getElementById('__bhcopy'); if (old) old.remove();
  var div = document.createElement('div');
  div.id = '__bhcopy'; div.contentEditable = true;
  div.style.cssText = 'position:fixed;left:380px;top:200px;width:480px;height:320px;z-index:2147483647;background:#fff;color:#111;border:3px solid #2a2;overflow:auto;font:11px sans-serif;padding:6px;';
  div.innerHTML = html;
  document.body.appendChild(div);
  return { imgs: div.querySelectorAll('img').length, links: div.querySelectorAll('a').length, hrs: div.querySelectorAll('hr').length };
};

/* STEP B (PREFERRED) — synthetic paste straight into the editor, NO OS clipboard.
 * The OS-clipboard Cmd+C/Cmd+V route is fragile: a native Cmd+C on a programmatic
 * selection often fails to overwrite the clipboard, so the editor receives whatever
 * was copied earlier (e.g. the digest base64 from build_image_clipboard.py) instead
 * of the rich HTML. Building the text/html into a DataTransfer and dispatching a
 * ClipboardEvent('paste') at the .ProseMirror node hands TipTap exactly the HTML we
 * want — verified one-shot (defaultPrevented:true, full body imported, images
 * re-hosted to S3).
 *
 * Sequence on /posts/<id>/edit (re-inject this file first; window.* was wiped, but
 * localStorage was not):
 *   1. computer left_click the .ProseMirror body (native focus), key cmd+a  (selects
 *      the template stub so the paste REPLACES it — ProseMirror tracks the selection
 *      from the real keystroke; a programmatic DOM Range does NOT update PM's
 *      internal selection reliably). Optionally key Backspace to empty it first.
 *   2. call window.bh.pasteHostedHtml()  (this function) — it reads the hosted HTML
 *      from window.__hostedHtml || localStorage and dispatches the paste.
 *   3. confirm the return { defaultPrevented:true }, then verify the .ProseMirror DOM.
 *   4. localStorage.removeItem('__bh_hosted_html') to clean up. */
window.bh.pasteHostedHtml = function () {
  var html = window.__hostedHtml || localStorage.getItem(window.bh.HOSTED_KEY);
  if (!html) return { error: 'no hosted html in window or localStorage' };
  window.__hostedHtml = html;
  var pm = document.querySelector('.ProseMirror');
  if (!pm) return { error: 'no .ProseMirror editor found (is the editor loaded?)' };
  pm.focus();
  var dt = new DataTransfer();
  dt.setData('text/html', html);
  dt.setData('text/plain', '');
  var ev = new ClipboardEvent('paste', { clipboardData: dt, bubbles: true, cancelable: true });
  try { Object.defineProperty(ev, 'clipboardData', { value: dt }); } catch (e) {}  // some builds read ev.clipboardData
  pm.dispatchEvent(ev);
  return { dispatched: true, defaultPrevented: ev.defaultPrevented, htmlLen: html.length };
};

/* STEP B (FALLBACK) — OS-clipboard copy of the staged green box, then native paste.
 * Only if pasteHostedHtml() stops working. Render the hosted HTML into a
 * contentEditable (stageCopyDiv), select it with a Range, NATIVE Cmd+C (execCommand
 * returns false without a gesture), then click .ProseMirror -> Cmd+A -> Cmd+V, all on
 * this page with no navigation in between. KNOWN FRAGILITY: the Cmd+C may silently
 * fail to overwrite the OS clipboard, and a page-level Cmd+A can escape the
 * contentEditable and copy only the first image — which is why the synthetic paste
 * above is preferred. */
// then: localStorage.removeItem('__bh_hosted_html')
'build_hosted_html helpers ready';
