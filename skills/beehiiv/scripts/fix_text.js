/* fix_text.js — copy-edit a beehiiv draft in place, from a list of find → replace fixes,
 * preserving each range's marks (link / italic / bold) so edited link text stays a hyperlink.
 * Used by newsagent:fix. Run in the logged-in /posts/<id>/edit tab via the Claude Chrome
 * extension's execute_javascript (paste this whole file, then call window.fx.*).
 *
 * Why the editor MODEL and not clicks/typing/clipboard: typing at the end of a link, or
 * pasting over a selection that crosses the link boundary, can leave new words outside the
 * link mark. A ProseMirror transaction that replaces [from,to) with a text node carrying the
 * OLD range's marks cannot. Same live TipTap instance newsagent:beehiiv writes the body with
 * (document.querySelector('.ProseMirror').editor — the first .ProseMirror is the post; the
 * second is beehiiv's chat box). Dispatched transactions sync through ydoc and persist,
 * which fx.verify() after a reload confirms.
 *
 * An edit is [old, new] or [old, new, near]: `near` is optional extra text that must ALSO be
 * in the same block (paragraph/heading/list item) — use it to pick one of several matches.
 *
 * All calls are synchronous and return small JSON-able objects (no URLs/tokens: the harness
 * redacts results that look like credentials).
 *
 *   fx.plan(edits)    dry run → per edit {i, n, uniform, marks, already}; changes nothing
 *   fx.apply(edits)   all-or-nothing: if any edit is not exactly-1/uniform, applies NONE and
 *                     returns the failures; else applies each (re-finding positions after
 *                     every dispatch) → [{i, ok, marks}]
 *   fx.straighten()   ‘ ’ “ ” → ' " in ONE transaction, each char keeping its own marks
 *                     → {converted, where:[snippets], left}
 *   fx.verify(strs)   after a reload: per string {found, inLink, links, tail} — links = number
 *                     of distinct link runs in that block (1 = headline not split), tail = the
 *                     block's non-link text (should be just " - Source")
 *   fx.count(re)      occurrences of a regex (string or RegExp) in the whole draft's text
 *   fx.show(s, w)     the block text around s (w chars each side) — for disambiguation
 */
(function () {
  function ed() {
    var pm = document.querySelector('.ProseMirror');
    if (!pm || !pm.editor) throw new Error('no editor on .ProseMirror (is /edit loaded?)');
    return pm.editor;
  }
  function sameMarks(a, b) {
    if (!a || !b || a.length !== b.length) return false;
    for (var i = 0; i < a.length; i++) if (!a[i].eq(b[i])) return false;
    return true;
  }
  function markNames(m) {
    return (m || []).map(function (x) { return x.type.name; }).join('+') || 'plain';
  }
  function linkOf(m) {
    return (m || []).find(function (x) { return x.type.name === 'link'; }) || null;
  }

  /* Every textblock as {t, map, marks, idx}: t = its text (non-text inline nodes such as
   * hardBreak become U+FFFC so offsets stay aligned), map[k] = doc position of char k,
   * marks[k] = that char's marks (null for a non-text inline node). */
  function blocks() {
    var out = [], idx = 0;
    ed().state.doc.descendants(function (n, pos) {
      if (!n.isTextblock) return true;
      var t = '', map = [], marks = [];
      n.forEach(function (c, off) {
        var p = pos + 1 + off;
        if (c.isText) {
          for (var k = 0; k < c.text.length; k++) { t += c.text[k]; map.push(p + k); marks.push(c.marks); }
        } else { t += '￼'; map.push(p); marks.push(null); }
      });
      out.push({ t: t, map: map, marks: marks, idx: idx++ });
      return false;
    });
    return out;
  }

  function find(s, near) {
    var r = [];
    blocks().forEach(function (b) {
      if (near && b.t.indexOf(near) < 0) return;
      var k = -1;
      while ((k = b.t.indexOf(s, k + 1)) >= 0) {
        var m0 = b.marks[k], uniform = true;
        for (var j = k; j < k + s.length; j++) if (!sameMarks(b.marks[j], m0)) { uniform = false; break; }
        r.push({ from: b.map[k], to: b.map[k + s.length - 1] + 1, marks: m0, uniform: uniform, block: b.idx });
      }
    });
    return r;
  }

  function check(e, i) {
    var f = find(e[0], e[2]);
    var res = { i: i, old: e[0].slice(0, 40), n: f.length };
    if (f.length === 1) { res.uniform = f[0].uniform; res.marks = markNames(f[0].marks); }
    if (f.length === 0) res.already = find(e[1], e[2]).length > 0;  // new text present → likely applied
    res.ok = f.length === 1 && f[0].uniform;
    return res;
  }

  function plan(edits) { return edits.map(check); }

  function apply(edits) {
    var pre = edits.map(check);
    var bad = pre.filter(function (r) { return !r.ok; });
    if (bad.length) return { applied: 0, refused: bad };
    var e0 = ed(), out = [];
    edits.forEach(function (e, i) {
      var f = find(e[0], e[2]);  // re-find: earlier dispatches shift positions
      if (f.length !== 1 || !f[0].uniform) { out.push({ i: i, ok: false, n: f.length }); return; }
      var node = e[1].length ? e0.schema.text(e[1], f[0].marks) : null;
      var tr = node ? e0.state.tr.replaceWith(f[0].from, f[0].to, node) : e0.state.tr.delete(f[0].from, f[0].to);
      e0.view.dispatch(tr);
      out.push({ i: i, ok: true, marks: markNames(f[0].marks) });
    });
    return { applied: out.filter(function (r) { return r.ok; }).length, results: out };
  }

  var STRAIGHT = { '‘': "'", '’': "'", '“': '"', '”': '"' };
  function straighten() {
    var e0 = ed(), hits = [];
    blocks().forEach(function (b) {
      for (var k = 0; k < b.t.length; k++) if (STRAIGHT[b.t[k]]) {
        hits.push({ pos: b.map[k], ch: b.t[k], marks: b.marks[k],
                    where: b.idx + ':' + b.t.slice(Math.max(0, k - 12), k + 12) });
      }
    });
    if (hits.length) {
      var tr = e0.state.tr;
      hits.slice().reverse().forEach(function (h) {  // back to front: positions stay valid
        tr.replaceWith(h.pos, h.pos + 1, e0.schema.text(STRAIGHT[h.ch], h.marks));
      });
      e0.view.dispatch(tr);
    }
    return { converted: hits.length, where: hits.map(function (h) { return h.where; }), left: count(/[‘’“”]/g) };
  }

  function verify(strs) {
    var bl = blocks();
    return strs.map(function (s) {
      var b = bl.find(function (x) { return x.t.indexOf(s) >= 0; });
      if (!b) return { s: s.slice(0, 40), found: false };
      var k = b.t.indexOf(s), inLink = true, runs = 0, prev = null, tail = '';
      for (var j = k; j < k + s.length; j++) if (!linkOf(b.marks[j])) { inLink = false; break; }
      for (var q = 0; q < b.t.length; q++) {
        var l = linkOf(b.marks[q]);
        if (l && (!prev || prev.attrs.href !== l.attrs.href)) runs++;
        if (!l) tail += b.t[q];
        prev = l;
      }
      return { s: s.slice(0, 40), found: true, inLink: inLink, links: runs, tail: tail.slice(0, 60) };
    });
  }

  function count(re) {
    var rx = typeof re === 'string' ? new RegExp(re.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'g') : re;
    if (!rx.global) rx = new RegExp(rx.source, rx.flags + 'g');
    return (ed().state.doc.textContent.match(rx) || []).length;
  }

  function show(s, w) {
    w = w || 60;
    return blocks().filter(function (b) { return b.t.indexOf(s) >= 0; }).map(function (b) {
      var k = b.t.indexOf(s);
      return b.idx + ': …' + b.t.slice(Math.max(0, k - w), k + s.length + w) + '…';
    });
  }

  window.fx = { blocks: blocks, find: find, plan: plan, apply: apply, straighten: straighten,
                verify: verify, count: count, show: show };
})();
JSON.stringify({ fx: 'ready', editor: !!(document.querySelector('.ProseMirror') || {}).editor });
