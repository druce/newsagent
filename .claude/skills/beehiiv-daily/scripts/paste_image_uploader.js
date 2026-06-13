/* paste_image_uploader.js — run in the logged-in app.beehiiv.com tab AFTER
 * beehiiv_browser.js (needs window.bh.getAuth).
 *
 * Creates a contentEditable "catcher". For each image you stage on the OS clipboard
 * (osascript … set the clipboard … as «class JPEG») and paste into it (computer-tool
 * `key cmd+v`), it uploads the blob to beehiiv and stashes the hosted {url}.
 *
 * SET window.__order to the image basenames IN DOCUMENT ORDER before pasting. Each paste
 * is auto-named from __order[__pasteIdx] and __pasteIdx advances on success.
 *
 * Usage:
 *   call 1: <paste beehiiv_browser.js> <paste this file>   // sets up catcher
 *   computer: left_click the red box once (native focus)
 *   per image: Bash `osascript … <hash>.jpg`  →  computer `key cmd+v`  (JPEG staging only;
 *     for the digest's .png/.webp thumbnails use the batch path, which sends raw bytes)
 *   checkpoint: JSON.stringify({idx:window.__pasteIdx, errs:window.__pasteLog.filter(x=>!x.ok)})
 *   done: confirm window.__pasteIdx === window.__order.length, all window.__urls[*] are https
 */
(function () {
  var old = document.getElementById('__bhpaste'); if (old) old.remove();
  window.__urls = window.__urls || {};
  window.__pasteLog = [];
  window.__pasteIdx = 0;
  // window.__order = ["Image74.jpg", ...];  // <-- set this from the digest, in order
  var ed = document.createElement('div');
  ed.contentEditable = true; ed.id = '__bhpaste';
  ed.style.cssText = 'position:fixed;left:400px;top:300px;width:320px;height:130px;z-index:2147483647;background:#fff;border:3px solid #e11;color:#111;font:14px sans-serif;padding:8px;';
  ed.textContent = 'paste target';
  document.body.appendChild(ed);
  ed.addEventListener('paste', function (e) {
    e.preventDefault();
    var items = e.clipboardData && e.clipboardData.items;
    var idx = window.__pasteIdx;
    var name = (window.__order && window.__order[idx]) || ('extra-' + idx);
    var file = null;
    if (items) for (var i = 0; i < items.length; i++) if (items[i].kind === 'file') file = items[i].getAsFile();
    if (!file) { window.__pasteLog.push({ idx: idx, name: name, err: 'no file' }); return; }
    window.__pasteIdx = idx + 1;
    var auth = window.bh.getAuth();
    var fd = new FormData();
    fd.append('file', file, (window.__datePrefix || '') + name);   // set window.__datePrefix = 'YYYYMMDD-' per run to avoid cross-run filename collisions
    fd.append('url_type', 'landscape');
    fetch('https://app.beehiiv.com/api/v2/publications/' + auth.pub + '/images', {
      method: 'POST',
      headers: { authorization: 'Bearer ' + auth.token, 'x-csrf-token': auth.csrf, accept: 'application/json' },
      credentials: 'include', body: fd,
    })
      .then(function (r) { return r.text().then(function (t) { var d; try { d = JSON.parse(t); } catch (_) { d = t; } return { ok: r.ok, status: r.status, d: d }; }); })
      .then(function (res) { if (!res.ok) throw new Error('HTTP ' + res.status); window.__urls[name] = (res.d && res.d.url) || res.d; window.__pasteLog.push({ idx: idx, name: name, ok: true, size: file.size }); })
      .catch(function (err) { window.__urls[name] = { error: err.message }; window.__pasteLog.push({ idx: idx, name: name, err: err.message }); });
  });
  ed.focus();
})();
'paste catcher ready — set window.__order, click the red box, then osascript+Cmd+V per image';
