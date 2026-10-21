/* Asset list: tick assets (kept across HTMX page/filter swaps) and print labels. */
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

  function refresh() {
    var b = bar();
    if (!b) { return; }
    boxes().forEach(function (cb) { cb.checked = selected.has(cb.value); });
    var all = document.querySelector("[data-asset-select-page]");
    var list = Array.prototype.slice.call(boxes());
    if (all) { all.checked = list.length > 0 && list.every(function (cb) { return cb.checked; }); }
    b.querySelector("[data-asset-select-count]").textContent = selected.size;
    b.hidden = selected.size === 0 && list.length === 0 ? true : selected.size === 0;
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
    if (e.target.closest("[data-asset-select-clear]")) {
      selected.clear(); save(); refresh();
    } else if (e.target.closest("[data-asset-select-print]")) {
      var tags = Array.from(selected).slice(0, MAX);
      if (!tags.length) { return; }
      window.open(b.getAttribute("data-print-url") + "?tags=" + encodeURIComponent(tags.join(",")), "_blank");
    }
  });

  document.addEventListener("htmx:afterSwap", refresh);
  document.addEventListener("DOMContentLoaded", refresh);
  refresh();
})();
