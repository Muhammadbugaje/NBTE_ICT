/* Asset list: press "Select", tick assets (kept across HTMX page/filter swaps
   and pages in this tab) and print labels. The checkboxes and the bar are
   hidden until "Select" is pressed; "Done", Cancel or Esc hide them again and
   clear the selection. */
(function () {
  "use strict";
  var KEY = "nbte.assets.labelSelection";
  var MAX = 200;
  var selected = new Set();

  try {
    JSON.parse(window.sessionStorage.getItem(KEY) || "[]").forEach(function (t) { selected.add(t); });
  } catch (e) { /* ignore */ }

  function save() {
    try { window.sessionStorage.setItem(KEY, JSON.stringify(Array.from(selected))); } catch (e) { /* ignore */ }
  }
  function boxes() { return document.querySelectorAll("[data-asset-select]"); }
  function bar() { return document.querySelector("[data-asset-select-bar]"); }
  var html = document.documentElement;
  var mode = selected.size > 0;   // a selection carried over from another page: stay in select mode
  function setMode(on) {
    mode = !!on;
    if (!mode) { selected.clear(); save(); }
    refresh();
    if (mode) {
      var first = document.querySelector("[data-asset-select]");
      if (first) { try { first.focus({ preventScroll: true }); } catch (e) { first.focus(); } }
    }
  }

  function refresh() {
    var b = bar();
    if (!b) { return; }
    boxes().forEach(function (cb) { cb.checked = selected.has(cb.value); });
    var all = document.querySelector("[data-asset-select-page]");
    var list = Array.prototype.slice.call(boxes());
    if (all) { all.checked = list.length > 0 && list.every(function (cb) { return cb.checked; }); }
    b.querySelector("[data-asset-select-count]").textContent = selected.size;
    b.hidden = !mode;
    html.classList.toggle("asset-selecting", mode);
    document.querySelectorAll("[data-asset-select-mode]").forEach(function (btn) {
      btn.classList.toggle("is-on", mode);
      btn.setAttribute("aria-pressed", mode ? "true" : "false");
      var l = btn.querySelector("[data-asset-select-mode-label]");
      if (l) { l.textContent = mode ? "Done" : "Select"; }
    });
  }

  document.addEventListener("change", function (e) {
    var t = e.target;
    if (t.matches("[data-asset-select]")) {
      if (t.checked) { selected.add(t.value); } else { selected.delete(t.value); }
    } else if (t.matches("[data-asset-select-page]")) {
      boxes().forEach(function (cb) {
        if (t.checked) { selected.add(cb.value); } else { selected.delete(cb.value); }
      });
    } else { return; }
    save(); refresh();
  });

  document.addEventListener("click", function (e) {
    var b = bar();
    if (!b) { return; }
    if (e.target.closest("[data-asset-select-mode]")) {
      setMode(!mode);
    } else if (e.target.closest("[data-asset-select-clear]")) {
      setMode(false);
    } else if (e.target.closest("[data-asset-select-print]")) {
      var tags = Array.from(selected).slice(0, MAX);
      if (!tags.length) { return; }
      window.open(b.getAttribute("data-print-url") + "?tags=" + encodeURIComponent(tags.join(",")), "_blank");
    }
  });

  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape" && mode && !Array.prototype.some.call(document.querySelectorAll(".nbte-confirm, [role=dialog][aria-modal=true]:not([hidden])"), function (d) { return d.getClientRects().length > 0 && !d.closest("[aria-hidden=true], [hidden]"); })) { setMode(false); }
  });
  document.addEventListener("htmx:afterSwap", refresh);
  document.addEventListener("DOMContentLoaded", refresh);
  refresh();
})();
