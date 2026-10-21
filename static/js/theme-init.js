/* NBTE ICT — theme-init.js
   Loaded synchronously in <head> (tiny, no dependencies) so the saved theme,
   view mode and motion preference are applied before the first paint —
   no white flash in dark mode. app.js / ui.js take over after load. */
(function () {
  var d = document.documentElement;
  try {
    // Follow the device's light/dark setting, unless the toggle was pressed
    // while the device was on the setting it has now (see app.js Theme).
    var os = window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
    var t = localStorage.getItem("nbte-theme");
    if ((t !== "light" && t !== "dark") || localStorage.getItem("nbte-theme-os") !== os) t = os;
    d.setAttribute("data-theme", t);
    var v = localStorage.getItem("nbte-view");
    if (v === "desktop" || v === "mobile") d.setAttribute("data-view", v);
    if (localStorage.getItem("nbte-motion") === "reduce") d.setAttribute("data-motion", "reduce");
  } catch (e) { /* storage blocked: CSS falls back to prefers-color-scheme */ }
  d.className += " js";
})();
