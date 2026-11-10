/* NBTE — theme-init.js (v2.1 Phase A, A9/A10; kit: design/theme/theme-init.js)
   Loaded synchronously in <head> (tiny, no dependencies) so the theme,
   palette, scene, motion level, density and view mode are on <html> before
   the first paint — no white flash, no flash of the wrong palette.

   Precedence, highest first (per axis):
     1. URL ?theme=&palette=&scene=&motion=&density= (previews, screenshots)
     2. this device's storage: "nbte-theme" (+ "nbte-theme-os", see below),
        "nbte-palette", "nbte-motion", "nbte-density"
     3. window.NBTE_PREFS (printed by the server: the user's saved
        preferences merged over the site's appearance.* settings)
     4. the device: prefers-color-scheme, prefers-reduced-motion
   The server wins over storage when the user changed a preference on
   another device (NBTE_PREFS.stamp newer than "nbte-stamp"), and always when
   the site does not let staff pick (NBTE_PREFS.lock).

   Light/dark keeps the v2 rule: a press of the toggle is remembered together
   with the device setting at that moment ("nbte-theme-os"); when the device
   setting changes the device wins again. A theme saved on the profile
   (light/dark) is followed on every device.

   It also carries the v2 safety net for the light/dark toggle: one delegated
   click listener on the document. If, shortly after a click on a
   [data-theme-toggle] / .theme-toggle button, the theme has not changed
   (ui.js not loaded on this page, a script error earlier in boot, or an old
   cached app.js without NBTE.Theme), it switches the theme itself, with the
   same storage keys app.js uses. ES5 only, so every Safari version parses it. */
(function (w) {
  var d = document.documentElement;
  var KEY = "nbte-theme", OS_KEY = "nbte-theme-os";
  var P = w.NBTE_PREFS, q = {};
  if (!P) {   // printed by the server with json_script (templates/partials/head.html)
    try { var el = document.getElementById("nbte-prefs"); P = el ? JSON.parse(el.textContent) : {}; } catch (e) { P = {}; }
  }
  P = P || {};
  w.NBTE_PREFS = P;
  var PAL = /^(green|blue|gold|teal|plum|graphite|custom)$/;
  var SCENE = /^(laminar|komorebi|quicksilver|blinds|none)$/;
  var MOTION = /^(full|reduced|off)$/;
  var DENSITY = /^(comfortable|compact)$/;
  var HEX = /^#?[0-9a-f]{6}$/i;

  function osTheme() {
    try {
      return w.matchMedia && w.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
    } catch (e) { return "light"; }
  }
  function osReduce() {
    try { return !!(w.matchMedia && w.matchMedia("(prefers-reduced-motion: reduce)").matches); } catch (e) { return false; }
  }
  function store(k) { try { return localStorage.getItem("nbte-" + k); } catch (e) { return null; } }
  try {
    location.search.replace(/[?&]([^=&#]+)=([^&#]*)/g, function (_, k, v) { q[k] = decodeURIComponent(v); });
  } catch (e) {}

  // Server newer than this device: drop the device's choices once.
  var stale = !!P.lock;
  try {
    if ((+P.stamp || 0) > (+localStorage.getItem("nbte-stamp") || 0)) {
      var ks = ["palette", "motion", "density"];
      for (var i = 0; i < ks.length; i++) localStorage.removeItem("nbte-" + ks[i]);
      if (P.theme === "light" || P.theme === "dark" || P.theme === "system") {
        localStorage.removeItem(KEY); localStorage.removeItem(OS_KEY);
      }
      localStorage.setItem("nbte-stamp", String(+P.stamp));
    }
  } catch (e) {}
  function pick(k, re, fallback) {
    var v = q[k];
    if (v && re.test(v)) return v;
    v = stale ? null : store(k);
    if (k === "motion" && v === "reduce") v = "reduced";   // v2 stored value
    if (v && re.test(v)) return v;
    v = P[k];
    if (v && re.test(v)) return v;
    return fallback;
  }

  try {
    // Theme: URL, then a toggle press made under the current device setting,
    // then the profile (light/dark), then the device.
    var os = osTheme(), t = q.theme;
    if (t !== "light" && t !== "dark") {
      t = localStorage.getItem(KEY);
      if ((t !== "light" && t !== "dark") || localStorage.getItem(OS_KEY) !== os) {
        t = (P.theme === "light" || P.theme === "dark") ? P.theme : os;
      }
    }
    d.setAttribute("data-theme", t);
    var v = localStorage.getItem("nbte-view");
    if (v === "desktop" || v === "mobile") d.setAttribute("data-view", v);
  } catch (e) {
    // Storage blocked (private mode on old Safari): still paint a theme.
    d.setAttribute("data-theme", (P.theme === "light" || P.theme === "dark") ? P.theme : osTheme());
  }

  var palette = pick("palette", PAL, "green");
  var motion = pick("motion", MOTION, "full");
  if (motion === "full" && osReduce()) motion = "reduced";
  d.setAttribute("data-palette", palette);
  d.setAttribute("data-scene", pick("scene", SCENE, "laminar"));
  d.setAttribute("data-motion", motion);
  d.setAttribute("data-density", pick("density", DENSITY, "comfortable"));
  // Custom pair for a preview (?cp=&ca=); the saved pair is a server <style>.
  if (palette === "custom" && q.cp && HEX.test(q.cp)) {
    d.style.setProperty("--custom-primary", q.cp.charAt(0) === "#" ? q.cp : "#" + q.cp);
    if (q.ca && HEX.test(q.ca)) d.style.setProperty("--custom-accent", q.ca.charAt(0) === "#" ? q.ca : "#" + q.ca);
  }
  // Motion kit gate (static/js/motion.js re-checks): entry animations start
  // with the first frame instead of flashing in after a deferred script.
  d.className += (motion !== "off" && !osReduce()) ? " js m-on" : " js";

  /* ---- toggle safety net ---- */
  function findToggle(node) {
    while (node && node !== document) {
      if (node.nodeType === 1 && (node.hasAttribute("data-theme-toggle") ||
          (" " + node.className + " ").indexOf(" theme-toggle ") !== -1)) return node;
      node = node.parentNode;
    }
    return null;
  }
  function syncButtons(theme) {
    var btns = document.querySelectorAll("[data-theme-toggle], .theme-toggle");
    for (var i = 0; i < btns.length; i++) {
      btns[i].setAttribute("aria-pressed", theme === "dark" ? "true" : "false");
    }
  }
  function fallbackToggle(before) {
    var next = before === "dark" ? "light" : "dark";
    d.setAttribute("data-theme", next);
    try {
      if (next === osTheme()) { localStorage.removeItem(KEY); localStorage.removeItem(OS_KEY); }
      else { localStorage.setItem(KEY, next); localStorage.setItem(OS_KEY, osTheme()); }
    } catch (e) {}
    syncButtons(next);
    try {
      var ev;
      if (typeof w.CustomEvent === "function") ev = new CustomEvent("theme:change", { detail: { theme: next } });
      else { ev = document.createEvent("CustomEvent"); ev.initCustomEvent("theme:change", false, false, { theme: next }); }
      w.dispatchEvent(ev);
    } catch (e) {}
  }
  var pending = null;
  var pendingChangeHandler = null;
  // Capture phase: runs before any page handler, so we see the theme the
  // click started from.
  document.addEventListener("click", function (e) {
    var btn = findToggle(e.target);
    if (!btn) return;
    var before = d.getAttribute("data-theme") || osTheme();
    if (btn.tagName === "A") e.preventDefault();
    // app.js switches on the next animation frame; give it a moment.
    // A second click before the check replaces the first check.
    if (pending) w.clearTimeout(pending);
    if (pendingChangeHandler) w.removeEventListener("theme:change", pendingChangeHandler);
    var changed = false;
    pendingChangeHandler = function (event) {
      if (event.detail && event.detail.theme && event.detail.theme !== before) changed = true;
    };
    w.addEventListener("theme:change", pendingChangeHandler);
    pending = w.setTimeout(function () {
      pending = null;
      w.removeEventListener("theme:change", pendingChangeHandler);
      pendingChangeHandler = null;
      if (!changed && (d.getAttribute("data-theme") || osTheme()) === before) fallbackToggle(before);
    }, 180);
  }, true);
})(window);
