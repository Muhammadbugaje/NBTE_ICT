/* NBTE motion kit v1 — v2.1 Phase A (A9), ported from design/motion/motion.js.
   Loaded with defer after app.js. Vanilla, no deps.
   html.m-on is set before first paint by theme-init.js (so entry animations
   start with the first frame instead of flashing); this file re-checks it.
   Public API: window.NBTEMotion = { countTo, refresh, openSheet, closeSheet }.
   Honours data-motion on <html> (full|reduced|off) and prefers-reduced-motion. */
(function (w, d) {
  var root = d.documentElement;
  var reduce = w.matchMedia && w.matchMedia("(prefers-reduced-motion: reduce)").matches;
  var level = root.getAttribute("data-motion") || "full";
  var on = !reduce && level !== "off";
  root.classList.toggle("m-on", on);

  /* stagger index */
  function stagger(scope) {
    (scope || d).querySelectorAll(".m-stagger").forEach(function (g) {
      Array.prototype.forEach.call(g.children, function (c, i) { c.style.setProperty("--i", i); });
    });
  }

  /* count-up: <b data-count="1284" data-decimals="0" data-prefix="" data-suffix="%"> */
  function fmt(el, v) {
    var dec = +(el.getAttribute("data-decimals") || 0);
    var s = v.toLocaleString("en-NG", { minimumFractionDigits: dec, maximumFractionDigits: dec });
    return (el.getAttribute("data-prefix") || "") + s + (el.getAttribute("data-suffix") || "");
  }
  function countTo(el, progress) { /* progress 0..1, used by frame capture too */
    var end = parseFloat(el.getAttribute("data-count")) || 0;
    var t = 1 - Math.pow(1 - Math.min(1, Math.max(0, progress)), 3);
    el.textContent = fmt(el, end * t);
  }
  var NUM = /^\s*-?\d+(\.\d+)?\s*$/;
  function runCount(el) {
    var end = parseFloat(el.getAttribute("data-count")) || 0;
    el.setAttribute("aria-label", fmt(el, end));
    if (!on || level === "reduced") { countTo(el, 1); return; }
    var t0 = null, dur = 900 + Math.min(700, end / 3);
    el.style.minWidth = el.offsetWidth + "px";
    function tick(ts) {
      if (!t0) t0 = ts; var p = (ts - t0) / dur; countTo(el, p);
      if (p < 1) requestAnimationFrame(tick); else { el.style.minWidth = ""; el.classList.add("is-counted"); }
    }
    countTo(el, 0); requestAnimationFrame(tick);
  }
  function counts(scope) {
    /* only plain numbers count up; anything else ("Not yet", "—") keeps its text */
    var els = Array.prototype.filter.call((scope || d).querySelectorAll("[data-count]:not([data-counted])"),
      function (e) { return NUM.test(e.getAttribute("data-count") || ""); });
    if (!("IntersectionObserver" in w)) { els.forEach(function (e) { e.setAttribute("data-counted", ""); countTo(e, 1); }); return; }
    var io = new IntersectionObserver(function (es) {
      es.forEach(function (e) { if (e.isIntersecting) { io.unobserve(e.target); e.target.setAttribute("data-counted", ""); runCount(e.target); } });
    }, { rootMargin: "0px 0px -8% 0px" });
    els.forEach(function (e) { io.observe(e); });
  }

  /* bottom sheet with grabber + drag to dismiss */
  function openSheet(id) {
    var s = d.getElementById(id); if (!s) return;
    s.classList.add("is-open"); s.removeAttribute("hidden");
    var b = d.querySelector('[data-sheet-backdrop="' + id + '"]'); if (b) b.classList.add("is-open");
    var f = s.querySelector("input,select,textarea,button"); if (f) f.focus({ preventScroll: true });
  }
  function closeSheet(id) {
    var s = d.getElementById(id); if (!s) return;
    s.classList.remove("is-open");
    var b = d.querySelector('[data-sheet-backdrop="' + id + '"]'); if (b) b.classList.remove("is-open");
  }
  d.addEventListener("click", function (e) {
    var o = e.target.closest("[data-sheet-open]"); if (o) { e.preventDefault(); openSheet(o.getAttribute("data-sheet-open")); }
    var c = e.target.closest("[data-sheet-close]"); if (c) { e.preventDefault(); closeSheet(c.getAttribute("data-sheet-close")); }
  });
  d.addEventListener("pointerdown", function (e) {
    var g = e.target.closest(".sheet__grab"); if (!g) return;
    var s = g.closest(".sheet"), y0 = e.clientY, dy = 0;
    function mv(ev) { dy = Math.max(0, ev.clientY - y0); s.style.transform = "translateY(" + dy + "px)"; }
    function up() { d.removeEventListener("pointermove", mv); s.style.transform = ""; if (dy > 90) closeSheet(s.id); }
    d.addEventListener("pointermove", mv); d.addEventListener("pointerup", up, { once: true });
  });

  /* running timer chip: <span data-timer-start="ISO or epoch ms"> */
  function timers() {
    var els = d.querySelectorAll("[data-timer-start]"); if (!els.length) return;
    function pad(n) { return (n < 10 ? "0" : "") + n; }
    function upd() {
      els.forEach(function (el) {
        var s = Math.max(0, Math.floor((Date.now() - (+el.getAttribute("data-timer-start") || Date.parse(el.getAttribute("data-timer-start")))) / 1000));
        el.textContent = Math.floor(s / 3600) + ":" + pad(Math.floor(s / 60) % 60) + ":" + pad(s % 60);
      });
    }
    upd(); setInterval(upd, 1000);
  }

  /* sliding tab ink: <nav data-ink> with .is-active child */
  function ink() {
    d.querySelectorAll("[data-ink]").forEach(function (n) {
      var a = n.querySelector(".is-active"), bar = n.querySelector(".ink"); if (!a) return;
      if (!bar) { bar = d.createElement("i"); bar.className = "ink"; n.appendChild(bar); }
      bar.style.transform = "translateX(" + a.offsetLeft + "px)"; bar.style.width = a.offsetWidth + "px";
    });
  }

  /* hero scenes pause while off screen (battery) */
  function scenes(scope) {
    if (!("IntersectionObserver" in w)) return;
    var io = scenes.io || (scenes.io = new IntersectionObserver(function (es) {
      es.forEach(function (e) { e.target.classList.toggle("is-paused", !e.isIntersecting); });
    }));
    (scope || d).querySelectorAll(".scene").forEach(function (s) { io.observe(s); });
  }

  function refresh(scope) { stagger(scope); counts(scope); ink(); scenes(scope); }
  w.NBTEMotion = { countTo: countTo, refresh: refresh, openSheet: openSheet, closeSheet: closeSheet, on: on };
  if (d.readyState === "loading") d.addEventListener("DOMContentLoaded", function () { refresh(); timers(); });
  else { refresh(); timers(); }
  /* htmx: re-run on swapped content, shimmer the target after 140 ms */
  /* repeated renders (filters, pagination) do not animate again */
  d.addEventListener("htmx:afterSwap", function (e) {
    if (e.target && e.target.classList) e.target.classList.add("m-quiet");
    refresh(e.target);
  });
  d.addEventListener("htmx:beforeRequest", function (e) {
    var t = e.detail && e.detail.target; if (!t) return;
    t._mT = setTimeout(function () { t.setAttribute("aria-busy", "true"); t.classList.add("is-loading"); }, 140);
  });
  d.addEventListener("htmx:afterRequest", function (e) {
    var t = e.detail && e.detail.target; if (!t) return;
    clearTimeout(t._mT); t.removeAttribute("aria-busy"); t.classList.remove("is-loading");
  });
})(window, document);
