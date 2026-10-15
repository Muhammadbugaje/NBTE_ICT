/* ==========================================================================
   NBTE ICT — Memos
   - Target-picker toggle on the compose form
   - Modal open/close for Return / Reject decisions
   - Ack progress bar on the detail page
   ========================================================================== */
(function () {
  "use strict";

  // -----------------------------------------------------------------
  // Target picker: show only the panel matching the chosen visibility
  // -----------------------------------------------------------------
  function initTargetPicker() {
    var forms = document.querySelectorAll("[data-memo-form]");
    forms.forEach(function (form) {
      var visibility = form.querySelector("[data-memo-visibility]");
      if (!visibility) return;

      var panels = form.querySelectorAll("[data-target-panel]");
      if (!panels.length) return;

      function sync() {
        var value = visibility.value;
        panels.forEach(function (el) {
          var matches = el.getAttribute("data-target-panel") === value;
          el.hidden = !matches;
        });
      }

      visibility.addEventListener("change", sync);
      sync();
    });
  }

  // -----------------------------------------------------------------
  // Action modals (Return / Reject)
  // -----------------------------------------------------------------
  function initActionModals() {
    document.querySelectorAll("[data-memo-action-open]").forEach(function (btn) {
      btn.addEventListener("click", function (e) {
        e.preventDefault();
        var id = btn.getAttribute("data-memo-action-open");
        var modal = document.getElementById(id);
        if (!modal) return;
        modal.hidden = false;
        // Focus the first textarea or button inside.
        var focusable = modal.querySelector("textarea, input:not([type=hidden]), button");
        if (focusable) setTimeout(function () { focusable.focus(); }, 40);
      });
    });

    document.querySelectorAll("[data-memo-action-close]").forEach(function (btn) {
      btn.addEventListener("click", function (e) {
        e.preventDefault();
        var modal = btn.closest("[data-memo-action-modal]");
        if (modal) modal.hidden = true;
      });
    });

    document.addEventListener("keydown", function (e) {
      if (e.key !== "Escape") return;
      document.querySelectorAll("[data-memo-action-modal]:not([hidden])").forEach(function (m) {
        m.hidden = true;
      });
    });
  }

  // -----------------------------------------------------------------
  // Ack progress bar
  // -----------------------------------------------------------------
  function initAckBar() {
    document.querySelectorAll("[data-ack-pct]").forEach(function (el) {
      var pct = parseInt(el.getAttribute("data-ack-pct"), 10);
      if (isNaN(pct)) pct = 0;
      if (pct < 0) pct = 0;
      if (pct > 100) pct = 100;
      el.style.width = pct + "%";
    });
  }

  // -----------------------------------------------------------------
  // Boot
  // -----------------------------------------------------------------
  function onReady(fn) {
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", fn);
    } else {
      fn();
    }
  }

  onReady(function () {
    try { initTargetPicker(); }   catch (e) { console.error("[NBTE memo] target picker:", e); }
    try { initActionModals(); }   catch (e) { console.error("[NBTE memo] modals:", e); }
    try { initAckBar(); }         catch (e) { console.error("[NBTE memo] ack bar:", e); }
  });

  window.NBTEMemo = {
    initTargetPicker: initTargetPicker,
    initActionModals: initActionModals,
    initAckBar: initAckBar,
  };
})();