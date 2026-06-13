/* batch_image_uploader.js — upload ALL of a digest's images in ONE paste.
 * Run in the logged-in app.beehiiv.com tab AFTER beehiiv_browser.js (needs
 * window.bh.getAuth). Replaces the per-image paste loop in paste_image_uploader.js:
 * ~3 tool calls for the whole set instead of ~3 PER image, and no index-desync
 * failure mode (each upload is keyed by filename, so completion order is irrelevant).
 *
 * Transport: scripts/build_image_clipboard.py base64s every referenced image into a
 * single {basename: b64} JSON map and pbcopies it as TEXT. One native Cmd+V (computer
 * tool) delivers the whole map to this catcher, which decodes each entry to a Blob and
 * POSTs it to POST /api/v2/publications/<pub>/images (multipart file + url_type:landscape),
 * stashing the hosted {url} into window.__urls[basename].
 *
 * Usage:
 *   Bash:    python3 scripts/build_image_clipboard.py    # builds + pbcopies the JSON
 *   call:    <paste beehiiv_browser.js> <paste this file>; set window.__datePrefix
 *   computer: left_click the catcher once (native focus), then key cmd+v   (ONE paste)
 *   verify:  poll JSON.stringify(window.__batch) until done:true, then confirm
 *            ok === total, errs:[], and every name in window.__urls is an https URL.
 *
 * CONCURRENCY is capped (default 6) to avoid hammering the upload endpoint; raise/lower
 * via window.__batchConc before pasting.
 */
(function () {
  var old = document.getElementById('__bhbatch'); if (old) old.remove();
  window.__urls = window.__urls || {};
  window.__batch = { done: false, note: 'waiting for paste' };
  var ed = document.createElement('textarea');
  ed.id = '__bhbatch';
  ed.style.cssText = 'position:fixed;left:400px;top:300px;width:320px;height:120px;z-index:2147483647;background:#fff;border:3px solid #07c;color:#111;font:12px monospace;padding:8px;';
  ed.value = 'batch paste target';
  document.body.appendChild(ed);

  ed.addEventListener('paste', function (e) {
    e.preventDefault();
    var txt = (e.clipboardData && e.clipboardData.getData('text/plain')) || '';
    var map;
    try { map = JSON.parse(txt); } catch (err) { window.__batch = { done: true, error: 'bad json, len ' + txt.length }; return; }
    var names = Object.keys(map);
    var auth = window.bh.getAuth();
    var conc = window.__batchConc || 6;
    window.__batch = { done: false, total: names.length, ok: 0, errs: [] };

    // bsky images come in mixed formats (jpg/png/webp) — set the Blob MIME from
    // the basename extension rather than hardcoding image/jpeg.
    function mimeOf(n) {
      var ext = (n.split('.').pop() || '').toLowerCase();
      return ext === 'png' ? 'image/png' : ext === 'webp' ? 'image/webp' : 'image/jpeg';
    }

    function uploadOne(name) {
      var bin = atob(map[name]);
      var u8 = new Uint8Array(bin.length);
      for (var i = 0; i < bin.length; i++) u8[i] = bin.charCodeAt(i);
      var fd = new FormData();
      fd.append('file', new Blob([u8], { type: mimeOf(name) }), (window.__datePrefix || '') + name);
      fd.append('url_type', 'landscape');
      return fetch('https://app.beehiiv.com/api/v2/publications/' + auth.pub + '/images', {
        method: 'POST',
        headers: { authorization: 'Bearer ' + auth.token, 'x-csrf-token': auth.csrf, accept: 'application/json' },
        credentials: 'include', body: fd,
      }).then(function (r) { return r.text(); }).then(function (t) {
        var d; try { d = JSON.parse(t); } catch (_) { d = t; }
        if (d && d.url) { window.__urls[name] = d.url; window.__batch.ok++; }
        else { window.__batch.errs.push({ name: name, d: String(d).slice(0, 80) }); }
      }).catch(function (err) { window.__batch.errs.push({ name: name, err: err.message }); });
    }

    // simple concurrency-limited pump over the name list
    var idx = 0;
    function pump() {
      if (idx >= names.length) return Promise.resolve();
      var name = names[idx++];
      return uploadOne(name).then(pump);
    }
    var workers = [];
    for (var w = 0; w < Math.min(conc, names.length); w++) workers.push(pump());
    Promise.all(workers).then(function () { window.__batch.done = true; });
  });

  ed.focus();
})();
'batch catcher ready — pbcopy the JSON (build_image_clipboard.py), click the blue box, one Cmd+V';
