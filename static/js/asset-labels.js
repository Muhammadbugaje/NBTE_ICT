/* Asset label page: remembers the chosen style, applies option changes, prints.
 *
 * The style is a harmless UI preference, so it lives in localStorage. Every
 * style is rendered by the server (so the @page size is always right); picking
 * a style is a plain link, and this script only remembers it and re-applies it
 * the next time the page opens without an explicit ?style=.
 */
(function () {
  "use strict";

  var KEY = "nbte.assets.labelStyle";

  function store(value) {
    try { window.localStorage.setItem(KEY, value); } catch (e) { /* private mode */ }
  }
  function recall() {
    try { return window.localStorage.getItem(KEY) || ""; } catch (e) { return ""; }
  }

  var body = document.body;
  var current = body.getAttribute("data-style-key") || "";
  var params = new URLSearchParams(window.location.search);
  var picks = document.querySelectorAll(".lbl-pick[data-style-key]");

  // Re-apply the remembered style when none was asked for explicitly.
  if (!params.has("style") && picks.length) {
    var saved = recall();
    var known = Array.prototype.some.call(picks, function (el) {
      return el.getAttribute("data-style-key") === saved;
    });
    if (known && saved !== current) {
      params.set("style", saved);
      window.location.replace(window.location.pathname + "?" + params.toString());
      return;
    }
  }

  // Remember what is showing (explicit or default) and what gets clicked.
  if (current && picks.length) { store(current); }
  Array.prototype.forEach.call(picks, function (el) {
    el.addEventListener("click", function () {
      store(el.getAttribute("data-style-key"));
    });
  });

  // Copies / skip / guides: apply on change.
  var form = document.querySelector("[data-label-options]");
  if (form) {
    form.addEventListener("change", function () { form.submit(); });
    form.addEventListener("submit", function () { /* plain GET */ });
  }

  // Print.
  document.addEventListener("click", function (e) {
    var btn = e.target.closest("[data-label-print]");
    if (!btn || btn.disabled) { return; }
    e.preventDefault();
    window.print();
  });
})();
