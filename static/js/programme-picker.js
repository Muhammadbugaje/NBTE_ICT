/* Programme picker (portal "Add a programme" and "Propose a new programme").
 *
 * - Type-to-filter: narrows the server-rendered list as you type (every word
 *   must match the row's name, aliases, discipline, category or level).
 * - ND/HND: an HND tick stays disabled until the ND of the same programme is
 *   ticked (or already on the institution's list — then the server renders it
 *   enabled). Unticking the ND unticks and disables the HND again.
 *
 * - Bulk clearance (staff queue): [data-select-all="<form id>"] ticks every
 *   [data-select-item="<form id>"]; the bulk buttons stay disabled until
 *   something is ticked and show the count.
 *
 * Event delegation on document, so it keeps working after HTMX swaps. No deps.
 */
(function () {
  "use strict";
  if (window.__nbteProgrammePicker) return;
  window.__nbteProgrammePicker = true;

  function norm(text) {
    return String(text || "").toLowerCase().replace(/[^a-z0-9]+/g, " ").trim();
  }

  function filter(input) {
    var root = input.closest("[data-programme-picker]");
    if (!root) return;
    var words = norm(input.value).split(" ").filter(Boolean);
    var shown = 0;
    root.querySelectorAll(".pp-picker__row").forEach(function (row) {
      var hay = row.getAttribute("data-search") || "";
      var ok = words.every(function (w) { return hay.indexOf(w) !== -1; });
      // keep ticked rows visible so a choice never disappears while filtering
      if (!ok && row.querySelector("input:checked")) ok = true;
      row.hidden = !ok;
      if (ok) shown += 1;
    });
    var count = root.querySelector("[data-picker-count]");
    if (count) count.textContent = String(shown);
    var empty = root.querySelector("[data-picker-empty]");
    if (empty) empty.hidden = shown !== 0;
  }

  function syncRow(row) {
    if (!row) return;
    var nd = row.querySelector('input[data-level="nd"]');
    var ndOn = !!(nd && nd.checked);
    row.querySelectorAll("input[data-needs-nd]").forEach(function (hnd) {
      hnd.disabled = !ndOn;
      if (!ndOn) hnd.checked = false;
      var label = hnd.closest(".pp-level");
      if (label) label.classList.toggle("is-disabled", !ndOn);
    });
    row.querySelectorAll("[data-nd-hint]").forEach(function (hint) { hint.hidden = ndOn; });
    row.classList.toggle("is-picked", !!row.querySelector("input:checked"));
  }

  // Capture phase: the filter box must not trigger the form's live fee estimate.
  document.addEventListener("input", function (e) {
    var t = e.target;
    if (t && t.matches && t.matches("[data-picker-filter]")) {
      e.stopPropagation();
      filter(t);
    }
  }, true);

  document.addEventListener("keydown", function (e) {
    var t = e.target;
    if (t && t.matches && t.matches("[data-picker-filter]") && e.key === "Enter") {
      e.preventDefault(); // Enter filters, it never submits the add form
    }
  });

  document.addEventListener("change", function (e) {
    var t = e.target;
    if (!t || !t.closest) return;
    var row = t.closest(".pp-picker__row");
    if (!row || !t.closest("[data-programme-picker]")) return;
    if (t.type === "radio") {
      t.closest("[data-programme-picker]").querySelectorAll(".pp-picker__row.is-picked")
        .forEach(function (r) { r.classList.remove("is-picked"); });
    }
    syncRow(row);
  });

  function bulkSync(id) {
    var items = document.querySelectorAll('input[data-select-item="' + id + '"]');
    var n = 0;
    items.forEach(function (i) { if (i.checked) n += 1; });
    var all = document.querySelector('input[data-select-all="' + id + '"]');
    if (all) {
      all.checked = items.length > 0 && n === items.length;
      all.indeterminate = n > 0 && n < items.length;
    }
    var form = document.getElementById(id);
    if (!form) return;
    form.querySelectorAll("[data-bulk-needs-selection]").forEach(function (b) { b.disabled = n === 0; });
    form.querySelectorAll("[data-bulk-count]").forEach(function (c) { c.textContent = n ? "(" + n + ")" : ""; });
  }

  document.addEventListener("change", function (e) {
    var t = e.target;
    if (!t || !t.getAttribute) return;
    var allId = t.getAttribute("data-select-all");
    if (allId) {
      document.querySelectorAll('input[data-select-item="' + allId + '"]').forEach(function (i) {
        if (!i.disabled) i.checked = t.checked;
      });
      bulkSync(allId);
      return;
    }
    var itemId = t.getAttribute("data-select-item");
    if (itemId) bulkSync(itemId);
  });

  function init(scope) {
    (scope || document).querySelectorAll("form[data-bulk-form]").forEach(function (f) { bulkSync(f.id); });
    if (scope && scope.matches && scope.matches("form[data-bulk-form]")) bulkSync(scope.id);
    (scope || document).querySelectorAll("[data-programme-picker] .pp-picker__row").forEach(function (row) {
      if (row.querySelector("input:checked")) row.classList.add("is-picked");
      var nd = row.querySelector('input[data-level="nd"]');
      if (nd && nd.checked) syncRow(row);
    });
    (scope || document).querySelectorAll("[data-picker-filter]").forEach(function (input) {
      if (input.value) filter(input);
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function () { init(document); });
  } else {
    init(document);
  }
  document.addEventListener("htmx:afterSettle", function () { init(document); });
})();
