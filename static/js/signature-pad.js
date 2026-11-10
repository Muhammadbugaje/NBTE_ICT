/* Signature pad (My profile -> Signature), ported from EE Hub (v2.1 X23).
 *
 * <div data-sig-pad data-ink="#14214a">
 *   <canvas data-sig-canvas></canvas>
 *   <button data-sig-clear> <button data-sig-undo>
 *   <input type="hidden" name="sig_data" data-sig-output>
 *   <button type="submit" data-sig-save disabled>
 * </div>
 *
 * Pointer events (mouse, pen, finger); strokes are smoothed with quadratic
 * curves and get thinner as the pen moves faster, like ink. The drawing is
 * exported as a transparent PNG data URL into the hidden input on submit.
 * Also: upload tab switching and the live ink/sensitivity labels.
 */
(function () {
  "use strict";
  var INKS = { navy: "#14214a", blue: "#1d4ed8", black: "#111111" };

  function Pad(root) {
    var canvas = root.querySelector("[data-sig-canvas]");
    var out = root.querySelector("[data-sig-output]");
    var save = root.querySelector("[data-sig-save]");
    var hint = root.querySelector("[data-sig-hint]");
    var ctx = canvas.getContext("2d");
    var strokes = [], current = null, dpr = 1;
    var ink = INKS[root.getAttribute("data-ink")] || INKS.navy;

    function size() {
      var r = canvas.getBoundingClientRect();
      dpr = Math.min(window.devicePixelRatio || 1, 2);
      canvas.width = Math.max(1, Math.round(r.width * dpr));
      canvas.height = Math.max(1, Math.round(r.height * dpr));
      redraw();
    }
    function pos(e) {
      var r = canvas.getBoundingClientRect();
      return { x: (e.clientX - r.left) * dpr, y: (e.clientY - r.top) * dpr, t: e.timeStamp || Date.now(),
               p: e.pressure && e.pointerType === "pen" ? e.pressure : 0.5 };
    }
    function widthFor(a, b) {
      var d = Math.hypot(b.x - a.x, b.y - a.y), dt = Math.max(1, b.t - a.t);
      var v = d / dt / dpr;                                // px per ms
      var w = (3.4 - Math.min(2.4, v * 1.6)) * (0.6 + b.p * 0.8);
      return Math.max(1.1, w) * dpr;
    }
    function drawStroke(s) {
      var pts = s.pts;
      ctx.strokeStyle = s.ink; ctx.fillStyle = s.ink; ctx.lineCap = "round"; ctx.lineJoin = "round";
      if (pts.length === 1) {
        ctx.beginPath(); ctx.arc(pts[0].x, pts[0].y, 1.6 * dpr, 0, Math.PI * 2); ctx.fill(); return;
      }
      var lw = pts[0].w || 2 * dpr;
      for (var i = 1; i < pts.length; i++) {
        var a = pts[i - 1], b = pts[i];
        var mid1 = i > 1 ? { x: (pts[i - 2].x + a.x) / 2, y: (pts[i - 2].y + a.y) / 2 } : a;
        var mid2 = { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 };
        lw = lw * 0.65 + (b.w || lw) * 0.35;
        ctx.lineWidth = lw;
        ctx.beginPath(); ctx.moveTo(mid1.x, mid1.y); ctx.quadraticCurveTo(a.x, a.y, mid2.x, mid2.y); ctx.stroke();
      }
    }
    function redraw() {
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      strokes.forEach(drawStroke);
      if (current) drawStroke(current);
      var has = strokes.length > 0;
      if (save) save.disabled = !has;
      if (hint) hint.hidden = has;
      root.classList.toggle("has-ink", has);
    }
    canvas.addEventListener("pointerdown", function (e) {
      e.preventDefault();
      canvas.setPointerCapture && canvas.setPointerCapture(e.pointerId);
      var p = pos(e); p.w = 2.4 * dpr;
      current = { ink: ink, pts: [p] };
      redraw();
    });
    canvas.addEventListener("pointermove", function (e) {
      if (!current) return;
      e.preventDefault();
      var evs = e.getCoalescedEvents ? e.getCoalescedEvents() : [e];
      evs.forEach(function (ev) {
        var p = pos(ev), last = current.pts[current.pts.length - 1];
        if (Math.hypot(p.x - last.x, p.y - last.y) < 1.2 * dpr) return;
        p.w = widthFor(last, p);
        current.pts.push(p);
      });
      redraw();
    });
    function sync() { if (out) out.value = strokes.length ? canvas.toDataURL("image/png") : ""; }
    function end() {
      if (!current) return;
      strokes.push(current); current = null; redraw(); sync();
    }
    canvas.addEventListener("pointerup", end);
    canvas.addEventListener("pointercancel", end);
    canvas.addEventListener("pointerleave", function (e) { if (e.buttons === 0) end(); });

    var clear = root.querySelector("[data-sig-clear]");
    var undo = root.querySelector("[data-sig-undo]");
    if (clear) clear.addEventListener("click", function () { strokes = []; redraw(); sync(); });
    if (undo) undo.addEventListener("click", function () { strokes.pop(); redraw(); sync(); });
    root.querySelectorAll("[data-sig-ink]").forEach(function (r) {
      r.addEventListener("change", function () {
        ink = INKS[r.value] || INKS.navy;
        strokes.forEach(function (s) { s.ink = ink; });
        redraw(); sync();
      });
    });
    var form = root.closest("form");
    if (form) form.addEventListener("submit", function (e) {
      if (!strokes.length) { e.preventDefault(); return; }
      out.value = canvas.toDataURL("image/png");
    });
    if ("ResizeObserver" in window) new ResizeObserver(size).observe(canvas); else window.addEventListener("resize", size);
    size();
  }

  function tabs(root) {
    var btns = root.querySelectorAll("[data-sig-tab]");
    btns.forEach(function (b) {
      b.addEventListener("click", function () {
        btns.forEach(function (x) {
          var on = x === b;
          x.setAttribute("aria-selected", on ? "true" : "false");
          var panel = root.querySelector("#" + x.getAttribute("aria-controls"));
          if (panel) panel.hidden = !on;
        });
        try { localStorage.setItem("nbte.sig.tab", b.getAttribute("data-sig-tab")); } catch (e) { /* storage off */ }
        window.dispatchEvent(new Event("resize"));
      });
    });
    try {
      var saved = localStorage.getItem("nbte.sig.tab");
      var t = saved && root.querySelector('[data-sig-tab="' + saved + '"]');
      if (t) t.click();
    } catch (e) { /* storage off */ }
  }

  function ranges(root) {
    root.querySelectorAll("input[type=range][data-sig-range]").forEach(function (r) {
      var lab = root.querySelector("[data-sig-range-value='" + r.name + "']");
      function upd() { if (lab) lab.textContent = r.value; }
      r.addEventListener("input", upd); upd();
    });
    root.querySelectorAll("input[type=file][data-sig-file]").forEach(function (f) {
      var name = root.querySelector("[data-sig-file-name]");
      var go = root.querySelector("[data-sig-upload-go]");
      f.addEventListener("change", function () {
        if (name) name.textContent = f.files && f.files[0] ? f.files[0].name : "No file chosen";
        if (go) go.disabled = !(f.files && f.files[0]);
      });
    });
  }

  function init() {
    document.querySelectorAll("[data-sig-pad]").forEach(function (el) { if (!el._pad) { el._pad = new Pad(el); } });
    document.querySelectorAll("[data-sig-root]").forEach(function (el) {
      if (el._sig) return; el._sig = true; tabs(el); ranges(el);
      var rep = el.querySelector("[data-sig-replace]"), cap = el.querySelector("#sig-capture");
      if (rep && cap) rep.addEventListener("click", function (e) {
        e.preventDefault(); cap.open = true; cap.scrollIntoView({ behavior: "smooth", block: "nearest" });
        window.dispatchEvent(new Event("resize"));
      });
      if (cap) cap.addEventListener("toggle", function () { window.dispatchEvent(new Event("resize")); });
    });
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init); else init();
  document.addEventListener("htmx:afterSettle", init);
})();
