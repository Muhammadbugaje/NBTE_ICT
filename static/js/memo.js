/* ==========================================================================
   NBTE ICT — Memos
   Handles audience show/hide on the compose form.
   ========================================================================== */
(function () {
  "use strict";

  function initAudienceToggles() {
    var forms = document.querySelectorAll("[data-memo-form]");
    forms.forEach(function (form) {
      var visibility = form.querySelector("select[name=visibility]");
      if (!visibility) return;

      var audiences = form.querySelectorAll("[data-audience]");
      if (!audiences.length) return;

      function sync() {
        var value = visibility.value;
        audiences.forEach(function (el) {
          var matches = el.getAttribute("data-audience") === value;
          el.hidden = !matches;
        });
      }

      visibility.addEventListener("change", sync);
      sync();
    });
  }

  function onReady(fn) {
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", fn);
    } else {
      fn();
    }
  }

  onReady(function () {
    try {
      initAudienceToggles();
    } catch (e) {
      console.error("[NBTE] memo.js init failed", e);
    }
  });

  window.NBTEMemo = { initAudienceToggles: initAudienceToggles };
})();