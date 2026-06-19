/* build_doc.js — turn the bsky digest HTML + the uploaded asset map into a TipTap
 * document of REAL beehiiv nodes (imageBlock / paragraph / horizontalRule) and write it
 * into the live editor, EXACTLY reproducing what the editor does when you paste images.
 *
 * Why this replaces the old build_hosted_html.js (which built bare-<img> HTML):
 *   beehiiv only renders first-class `imageBlock` nodes on the published web page and in
 *   the EMAIL. Bare <img> tags (what setContent(html) produced) show in the live editor but
 *   are SILENTLY DROPPED on publish/send — that was the "images missing in the email" bug.
 *   A real paste uploads bytes to POST /publications/<pub>/assets and inserts an imageBlock;
 *   build_image_clipboard.py + batch_image_uploader.js do the upload, this builds the nodes.
 *
 * Pipeline (functions below):
 *   1. (dashboard) batch_image_uploader.js filled window.__assets[<digest src>] = {id,src,title}.
 *   2. (dashboard) buildDoc(digestHtml) walks the digest DOM -> a doc JSON
 *      (<p><img></p> -> imageBlock from the asset map; <p> text -> paragraph with <a>/<em>
 *      marks; <hr> -> horizontalRule) and stashes it in localStorage['__bh_doc'] (survives
 *      the nav to /edit; window.* does not).
 *   3. (/edit) drive applyChunk(N) REPEATEDLY (one call per execute_javascript) until
 *      {done:true}, then stripEmptyParagraphs(), then verify with editorCounts().
 *
 * !! Why chunked, and why caller-driven (NOT a setTimeout loop):
 *   setContent() of the whole doc at once throws React error #185 ("max update depth") —
 *   mounting ~50 imageBlock node-views in one transaction loops the renderer, the editor
 *   crashes, and NOTHING persists. So insert a few nodes at a time and let React settle
 *   BETWEEN inserts. An in-page setTimeout loop gets clamped to ~1/s in a background tab
 *   (and races itself) — instead the CALLER invokes applyChunk once per round-trip; the
 *   tool-call latency is the settle time. ~12 nodes/chunk (≈4 images) is safe.
 */
window.bh = window.bh || {};
window.bh.DOC_KEY = '__bh_doc';

/* imageBlock node identical to what the editor builds on a real paste (attrs.id = asset id,
 * attrs.src = asset file.url + ?t cache-buster). */
window.bh.imageBlockNode = function (asset) {
  return {
    type: 'imageBlock',
    attrs: {
      id: asset.id, url: '', isUploaded: false, allowExternal: false, target: null,
      captionUrl: '', captionTarget: null, src: asset.src, title: asset.title || '', alt: '',
      source: null, width: '100%', align: 'center', captionAlign: 'center',
      borderWidthTop: 0, borderWidthRight: 0, borderWidthBottom: 0, borderWidthLeft: 0,
      useIndividualBorderWidth: false, borderTopLeftRadius: 0, borderTopRightRadius: 0,
      borderBottomRightRadius: 0, borderBottomLeftRadius: 0, useIndividualBorderRadius: false,
      borderColor: null, borderStyle: 'solid',
    },
    content: [{ type: 'figcaption' }],
  };
};

/* Convert a <p>'s inline HTML into TipTap text nodes (<a>->link, <em>->italic, <strong>->bold). */
window.bh._inline = function (el) {
  var out = [];
  function walk(node, marks) {
    if (node.nodeType === 3) {
      if (node.nodeValue && node.nodeValue.length)
        out.push(marks.length ? { type: 'text', text: node.nodeValue, marks: marks.slice() }
                              : { type: 'text', text: node.nodeValue });
      return;
    }
    if (node.nodeType !== 1) return;
    var t = node.tagName, m = marks.slice();
    if (t === 'A') m.push({ type: 'link', attrs: { rel: 'noopener noreferrer nofollow', href: node.getAttribute('href'), class: null, color: null, target: '_blank' } });
    else if (t === 'EM' || t === 'I') m.push({ type: 'italic' });
    else if (t === 'STRONG' || t === 'B') m.push({ type: 'bold' });
    else if (t === 'BR') { out.push({ type: 'hardBreak' }); return; }
    for (var i = 0; i < node.childNodes.length; i++) walk(node.childNodes[i], m);
  }
  for (var i = 0; i < el.childNodes.length; i++) walk(el.childNodes[i], []);
  return out;
};

/* Build the doc JSON from the digest HTML + window.__assets, stash it, return a report.
 * Run on the dashboard (after uploads) BEFORE navigating to /edit. */
window.bh.buildDoc = function (html) {
  var doc = new DOMParser().parseFromString(html, 'text/html');
  var content = [], missing = [], imgs = 0, hrs = 0, links = 0;
  var kids = doc.body.children;
  for (var i = 0; i < kids.length; i++) {
    var el = kids[i], tag = el.tagName;
    if (tag === 'HR') { content.push({ type: 'horizontalRule' }); hrs++; continue; }
    if (tag === 'P') {
      var img = el.querySelector('img');
      if (img) {
        var src = img.getAttribute('src');
        var asset = (window.__assets || {})[src];
        if (asset && asset.src) { content.push(window.bh.imageBlockNode(asset)); imgs++; }
        else { missing.push(src); }
        continue;
      }
      var inline = window.bh._inline(el);
      links += inline.filter(function (n) { return n.marks && n.marks.some(function (m) { return m.type === 'link'; }); }).length;
      // drop empties at build time — insertion adds its own separators anyway
      if (inline.length) content.push({ type: 'paragraph', content: inline });
      continue;
    }
  }
  var docJson = { type: 'doc', content: content };
  window.__bh_doc = docJson;
  try { localStorage.setItem(window.bh.DOC_KEY, JSON.stringify(docJson)); } catch (e) {}
  return { nodes: content.length, imgs: imgs, hrs: hrs, links: links, missing: missing };
};

/* (/edit) Insert the NEXT chunk of the stashed doc into the live editor. Caller invokes this
 * repeatedly (once per execute_javascript) until it returns {done:true}. First call seeds the
 * doc with setContent(first chunk); later calls append at the end. Returns {inserted,total,done}.
 * Default chunk 12 (≈4 images) stays under the React #185 threshold. */
window.bh.__ai = null;
window.bh.applyChunk = function (chunk) {
  chunk = chunk || 12;
  var pm = document.querySelector('.ProseMirror'); var ed = pm && pm.editor;
  if (!ed) return { err: 'no editor (is /edit loaded?)' };
  if (!window.bh.__ai) {
    var doc = window.__bh_doc; if (!doc) { try { doc = JSON.parse(localStorage.getItem(window.bh.DOC_KEY)); } catch (e) {} }
    if (!doc) return { err: 'no doc in window or localStorage' };
    var first = doc.content.slice(0, chunk);
    try { ed.commands.setContent({ type: 'doc', content: first }, true); } catch (e) { return { err: 'init setContent: ' + e.message }; }
    window.bh.__ai = { nodes: doc.content.slice(), i: first.length };
    return { inserted: first.length, total: doc.content.length, done: first.length >= doc.content.length };
  }
  var st = window.bh.__ai;
  if (st.i >= st.nodes.length) return { inserted: st.i, total: st.nodes.length, done: true };
  var batch = st.nodes.slice(st.i, st.i + chunk);
  try { ed.chain().insertContentAt(ed.state.doc.content.size, batch).run(); st.i += batch.length; }
  catch (e) { return { err: 'insert@' + st.i + ': ' + e.message, inserted: st.i, total: st.nodes.length }; }
  return { inserted: st.i, total: st.nodes.length, done: st.i >= st.nodes.length };
};

/* Remove empty paragraphs the chunked insert left at boundaries (one delete transaction —
 * unmounting nodes does NOT trigger React #185). Run once after applyChunk reports done. */
window.bh.stripEmptyParagraphs = function () {
  var pm = document.querySelector('.ProseMirror'); var ed = pm && pm.editor;
  if (!ed) return { err: 'no editor' };
  var dels = [];
  ed.state.doc.forEach(function (node, offset) {
    if (node.type.name === 'paragraph' && node.content.size === 0) dels.push({ from: offset, to: offset + node.nodeSize });
  });
  var tr = ed.state.tr;
  dels.slice().reverse().forEach(function (d) { tr.delete(d.from, d.to); });
  try { ed.view.dispatch(tr); } catch (e) { return { err: String(e) }; }
  return Object.assign({ deleted: dels.length }, window.bh.editorCounts());
};

/* Read the TRUE body state from the editor MODEL (getJSON). MUST count imageBlock nodes,
 * NOT <img> tags — imageBlock does NOT serialize to <img> in getHTML(), so an <img> count is
 * always 0 even on success. getJSON is correct immediately (the live DOM can read empty for
 * seconds during async image load). */
window.bh.editorCounts = function () {
  var pm = document.querySelector('.ProseMirror');
  if (!pm || !pm.editor) return { error: 'no editor on .ProseMirror' };
  var j = pm.editor.getJSON();
  var imgs = 0, hrs = 0, links = 0, emptyParas = 0, lastText = '';
  (function walk(n) {
    if (!n) return;
    if (n.type === 'imageBlock') imgs++;
    if (n.type === 'horizontalRule') hrs++;
    if (n.type === 'paragraph' && (!n.content || !n.content.length)) emptyParas++;
    if (n.marks && n.marks.some(function (m) { return m.type === 'link'; })) links++;
    if (n.type === 'text' && n.text) lastText = n.text;
    if (n.content) n.content.forEach(walk);
  })(j);
  return { top: (j.content || []).length, imgs: imgs, hrs: hrs, links: links, emptyParas: emptyParas, lastText: lastText.slice(-90) };
};

'build_doc helpers ready';
