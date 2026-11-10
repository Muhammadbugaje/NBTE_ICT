/* v2.3 — decide only after opening the file.
   Inside [data-preview-scope], every [data-needs-preview] control stays
   disabled until the person opens the document (Preview button, a Download
   link, or any link marked data-preview-trigger). The server checks it too. */
(function () {
  "use strict";
  function unlock(scope) {
    if (!scope || scope.dataset.opened === "1") return;
    scope.dataset.opened = "1";
    scope.querySelectorAll("[data-needs-preview]").forEach(function (el) {
      el.disabled = false; el.classList.remove("is-locked"); el.removeAttribute("aria-disabled");
    });
    var note = scope.querySelector("[data-preview-note]");
    if (note) {
      note.classList.add("is-open");
      var icon = note.querySelector(".ux-gate__icon"); if (icon) icon.textContent = "✓";
      var text = note.querySelector("[data-preview-text]");
      if (text) text.textContent = "You have opened this document. You can now decide.";
    }
  }
  document.addEventListener("click", function (e) {
    var opener = e.target.closest("[data-preview], [data-preview-trigger], a[href*='/files/'], a[download]");
    if (!opener) return;
    var scope = opener.closest("[data-preview-scope]");
    if (scope) window.setTimeout(function () { unlock(scope); }, 400);
  }, true);
  // Locked controls explain themselves instead of doing nothing.
  document.addEventListener("click", function (e) {
    var locked = e.target.closest("[data-preview-scope] [data-needs-preview][disabled]");
    if (!locked) return;
    var note = locked.closest("[data-preview-scope]").querySelector("[data-preview-note]");
    if (note && note.animate) note.animate([{ transform: "translateX(0)" }, { transform: "translateX(-6px)" },
      { transform: "translateX(6px)" }, { transform: "translateX(0)" }], { duration: 260 });
  });
})();
