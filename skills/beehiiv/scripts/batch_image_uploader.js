/* batch_image_uploader.js — upload ALL of a digest's images in ONE paste, exactly the
 * way the editor itself does when you paste an image: via the post ASSET endpoint.
 * Run in the logged-in app.beehiiv.com tab AFTER beehiiv_browser.js (needs
 * window.bh.getAuth).
 *
 * THE ENDPOINT MATTERS. A real editor paste does:
 *     POST /api/v2/publications/<pub>/assets    (multipart, field name `asset[file]`)
 *       -> { id, file:{ url:"https://beehiiv-images-production.s3…/uploads/asset/file/<id>/<name>" }, … }
 * and then inserts an `imageBlock` node whose attrs.id === <id> and
 * attrs.src === file.url + "?t=<ts>". imageBlock nodes (NOT bare <img>) are the only
 * images beehiiv renders on the published web page and in the EMAIL.
 *
 * The OLD flow used `POST /publications/<pub>/images` (field `file`) which returns a
 * `media.beehiiv.com/.../uploads/publication/file/…` CDN url and was dropped into the body
 * as a bare <img>. Those render in the live editor but are SILENTLY DROPPED on publish and
 * in email. Do not use /images here.
 *
 * Transport: scripts/build_image_clipboard.py base64s every referenced image into a single
 * { "<src>": {n:filename, b:base64} } JSON map (keyed by the digest's exact <img src>) and
 * pbcopies it as TEXT. One native Cmd+V (computer tool) delivers the whole map to this
 * catcher, which decodes each entry to a Blob and POSTs it to /assets, stashing the result
 * into window.__assets[<src>] = { id, src } (src = file.url + "?t=<ts>", ready for an
 * imageBlock). Keyed by <src> (not basename) so the builder maps each <img> directly.
 *
 * Usage:
 *   Bash:    python3 scripts/build_image_clipboard.py    # builds + pbcopies the JSON
 *   call:    <paste beehiiv_browser.js> <paste this file>
 *   computer: left_click the catcher once (native focus), then key cmd+v   (ONE paste)
 *   verify:  poll JSON.stringify(window.__batch) until done:true, then confirm
 *            ok === total, errs:[], and every src in window.__assets has an https url.
 *
 * CONCURRENCY is capped (default 6); raise/lower via window.__batchConc before pasting.
 */
(function () {
  var old = document.getElementById('__bhbatch'); if (old) old.remove();
  window.__assets = window.__assets || {};
  window.__batch = { done: false, note: 'waiting for paste' };
  var ed = document.createElement('textarea');
  ed.id = '__bhbatch';
  ed.style.cssText = 'position:fixed;left:400px;top:300px;width:320px;height:120px;z-index:2147483647;background:#fff;border:3px solid #07c;color:#111;font:12px monospace;padding:8px;';
  ed.value = '';  // start EMPTY — a placeholder can leak into a paste target
  document.body.appendChild(ed);

  ed.addEventListener('paste', function (e) {
    e.preventDefault();
    var txt = (e.clipboardData && e.clipboardData.getData('text/plain')) || '';
    var map;
    try { map = JSON.parse(txt); } catch (err) { window.__batch = { done: true, error: 'bad json, len ' + txt.length }; return; }
    var srcs = Object.keys(map);
    var auth = window.bh.getAuth();
    var conc = window.__batchConc || 6;
    window.__batch = { done: false, total: srcs.length, ok: 0, errs: [] };

    function mimeOf(n) {
      var x = (n.split('.').pop() || '').toLowerCase();
      return x === 'png' ? 'image/png' : x === 'webp' ? 'image/webp'
           : x === 'gif' ? 'image/gif' : 'image/jpeg';
    }

    function uploadOne(src) {
      var entry = map[src], name = entry.n, b64 = entry.b;
      var bin = atob(b64), u8 = new Uint8Array(bin.length);
      for (var i = 0; i < bin.length; i++) u8[i] = bin.charCodeAt(i);
      var fd = new FormData();
      // field name MUST be asset[file] (Rails strong-param); a bare `file` 400s with
      // "param is missing or the value is empty: asset".
      fd.append('asset[file]', new Blob([u8], { type: mimeOf(name) }), name);
      return fetch('https://app.beehiiv.com/api/v2/publications/' + auth.pub + '/assets', {
        method: 'POST',
        headers: { authorization: 'Bearer ' + auth.token, 'x-csrf-token': auth.csrf, accept: 'application/json' },
        credentials: 'include', body: fd,
      }).then(function (r) { return r.text(); }).then(function (t) {
        var d; try { d = JSON.parse(t); } catch (_) { d = t; }
        var url = d && d.file && d.file.url;
        if (url) {
          window.__assets[src] = { id: d.id, src: url + '?t=' + Date.now(), title: d.title || name };
          window.__batch.ok++;
        } else {
          window.__batch.errs.push({ src: src, d: String(t).slice(0, 100) });
        }
      }).catch(function (err) { window.__batch.errs.push({ src: src, err: err.message }); });
    }

    var idx = 0;
    function pump() {
      if (idx >= srcs.length) return Promise.resolve();
      var s = srcs[idx++];
      return uploadOne(s).then(pump);
    }
    var workers = [];
    for (var w = 0; w < Math.min(conc, srcs.length); w++) workers.push(pump());
    Promise.all(workers).then(function () { window.__batch.done = true; });
  });

  ed.focus();
})();
'batch asset uploader ready — pbcopy the JSON (build_image_clipboard.py), click the blue box, one Cmd+V';
