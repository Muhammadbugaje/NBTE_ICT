/* New QA request (portal): the HND "tick the ND too" note.
 *
 * An HND row carries a note ([data-nq-nd-note]) while the ND of the same
 * programme is not on the request. Once that ND is ticked in the same list the
 * note is hidden; unticking the ND shows it again. Display only: the server
 * still enforces the rule when the draft is created.
 *
 * Rows expose data-level ("nd" / "hnd") and data-programme (the name). Two rows
 * are the same programme when their names match once the level and the word
 * "Technology" are ignored (the same rule as institutions.programme_master.field_key).
 *
 * Event delegation on document, so it keeps working after HTMX swaps. No deps.
 */
(function () {
  "use strict";
  if (window.__nbteQaRequestNew) return;
  window.__nbteQaRequestNew = true;

  function key(name) {
    return String(name || "").toLowerCase()
      .replace(/^\s*h?nd\s+/, "")
      .replace(/\btechnology\b/g, "")
      .replace(/[^a-z0-9]+/g, " ").trim();
  }

  function sync(list) {
    if (!list) return;
    var ticked = {};
    list.querySelectorAll('[data-nq-row][data-level="nd"]').forEach(function (row) {
      var box = row.querySelector('input[type="checkbox"][name="programmes"]');
      if (box && box.checked && !box.disabled) ticked[key(row.getAttribute("data-programme"))] = true;
    });
    list.querySelectorAll('[data-nq-row][data-level="hnd"]').forEach(function (row) {
      var note = row.querySelector("[data-nq-nd-note]");
      if (note) note.hidden = !!ticked[key(row.getAttribute("data-programme"))];
    });
  }

  function syncAll(root) {
    (root || document).querySelectorAll("[data-nq-programmes]").forEach(sync);
  }

  document.addEventListener("change", function (e) {
    var t = e.target;
    if (t && t.matches && t.matches('[data-nq-programmes] input[name="programmes"]')) {
      sync(t.closest("[data-nq-programmes]"));
    }
  });
  document.addEventListener("htmx:afterSwap", function (e) { syncAll(e.target); });
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", function () { syncAll(); });
  else syncAll();
})();
