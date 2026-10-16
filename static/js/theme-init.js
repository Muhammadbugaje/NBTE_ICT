/* NBTE ICT — theme-init.js
   Loaded synchronously in <head> (tiny, no dependencies) so the saved theme,
   view mode and motion preference are applied before the first paint —
   no white flash in dark mode. app.js / ui.js take over after load. */
(function () {
  var d = document.documentElement;
  try {
    var t = localStorage.getItem("nbte-theme");
    if (t !== "light" && t !== "dark") {
      t = window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
    }
    d.setAttribute("data-theme", t);
    var v = localStorage.getItem("nbte-view");
    if (v === "desktop" || v === "mobile") d.setAttribute("data-view", v);
    if (localStorage.getItem("nbte-motion") === "reduce") d.setAttribute("data-motion", "reduce");
  } catch (e) { /* storage blocked: CSS falls back to prefers-color-scheme */ }
  d.className += " js";
})();
