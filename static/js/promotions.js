/* Promotions (staff/promotions/*, staff detail) — B.9.
   Selection (delegated, so it survives HTMX swaps of #promo-list), the
   promotion dialog (next-step default, ES override mode, cascade scope with a
   live count, fetch preview, Approve all) and the rollback dialog.
   Uses NBTE.modal / NBTE.toast from ui.js. */
(function () {
  "use strict";

  function $(sel, root) { return (root || document).querySelector(sel); }
  function $all(sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); }
  function toast(msg, kind) { if (window.NBTE && NBTE.toast) NBTE.toast(msg, kind); }

  function initPromotion(root) {
    var form = $("[data-promo-form]", root);
    if (!form) return;
    var single = root.hasAttribute("data-promo-single");
    var fixed = $("[data-promo-fixed]", form);
    var preview = $("[data-promo-preview]", form);
    var confirmBtn = $("[data-promo-confirm]", form);
    var previewBtn = $("[data-promo-preview-btn]", form);
    var cascade = $("[data-promo-cascade]", form);
    var cascadeText = $("[data-promo-cascade-text]", form);
    var cascadeOpts = $("[data-promo-cascade-options]", form);
    var notify = $("[data-promo-notify]", form);
    var override = $("[data-promo-override]", form);
    var target = $("[data-promo-target]", form);
    var titleCount = $("[data-promo-title-count]", form);
    var notifyTouched = false, countTimer = null;
    var DEFAULT_CASCADE = cascadeText ? cascadeText.textContent : "";

    function picks() { return $all("[data-promo-pick]"); }
    function selected() {
      if (fixed) return [fixed];
      return picks().filter(function (p) { return p.checked; });
    }
    function mode() { var r = $("[data-promo-mode-radio]:checked", form); return r ? r.value : "next"; }
    function scope() { var r = $("input[name=cascade_scope]:checked", form); return r ? r.value : "department"; }

    function stale() {
      if (confirmBtn) confirmBtn.disabled = true;
      if (preview && preview.getAttribute("data-has-preview")) {
        preview.innerHTML = '<p class="promo-preview-empty">Something changed — press <strong>Preview</strong> again.</p>';
        preview.removeAttribute("data-has-preview");
      }
    }

    function syncNotify() {
      if (!notify || notifyTouched) return;
      notify.checked = selected().length === 1 && !(cascade && cascade.checked);
    }

    function updateCount() {
      var sel = selected(), all = picks();
      var countEl = $("[data-promo-count]"), openBtn = $("[data-promo-open]"), allBox = $("[data-promo-all]");
      if (countEl) countEl.textContent = String(sel.length);
      if (openBtn) openBtn.disabled = sel.length === 0;
      if (titleCount) titleCount.textContent = sel.length === 1 ? "1 staff member" : sel.length + " staff";
      if (allBox) {
        allBox.checked = all.length > 0 && sel.length === all.length;
        allBox.indeterminate = sel.length > 0 && sel.length < all.length;
      }
      syncNotify();
      stale();
      if (cascade && cascade.checked) fetchCascade();
    }

    function fetchCascade() {
      clearTimeout(countTimer);
      countTimer = setTimeout(function () {
        var sel = selected();
        if (!sel.length || !cascadeText) return;
        var params = new URLSearchParams();
        sel.forEach(function (p) { params.append("ids", p.value); });
        params.set("cascade_scope", scope());
        cascadeText.textContent = "Counting…";
        fetch(root.getAttribute("data-count-url") + "?" + params.toString(), {
          credentials: "same-origin", headers: { "X-Requested-With": "fetch" }
        }).then(function (r) { return r.json(); }).then(function (data) {
          if (data.scope === "grade") {
            var g = data.grades && data.grades.length ? "grade level " + data.grades.join(" / ") : "the same grade level";
            cascadeText.textContent = data.count === 0
              ? "Nobody else on " + g + " — the cascade adds no one."
              : "Also promote " + data.count + " other staff on " + g + " in every department you can see, each one step up.";
          } else {
            var desig = data.tiers && data.tiers.length ? data.tiers.join(" / ") : "the same designation";
            cascadeText.textContent = data.count === 0
              ? "Nobody else on " + desig + " in the same department was appointed later — the cascade adds no one."
              : "Also promote " + data.count + " other staff on " + desig + " in the same department appointed later, each one step up.";
          }
        }).catch(function () { cascadeText.textContent = "Couldn't count the cascade — the preview will show it."; });
      }, 150);
    }

    function runPreview() {
      if (mode() === "target" && target && !target.value) {
        target.focus(); toast("Choose the designation first.", "warning"); return;
      }
      if (!selected().length) { toast("Select at least one staff member.", "warning"); return; }
      var data = new FormData(form);
      if (previewBtn) previewBtn.classList.add("is-busy");
      fetch(root.getAttribute("data-preview-url"), {
        method: "POST", body: data, credentials: "same-origin", headers: { "X-Requested-With": "fetch" }
      }).then(function (r) { if (!r.ok) throw new Error(r.status); return r.text(); })
        .then(function (html) {
          preview.innerHTML = html;
          preview.setAttribute("data-has-preview", "1");
          if (confirmBtn) confirmBtn.disabled = !preview.querySelector("[data-preview-ok='1']");
          if (window.matchMedia && window.matchMedia("(max-width: 767px)").matches) preview.scrollIntoView({ block: "start" });
        })
        .catch(function () { toast("The preview could not be loaded. Try again.", "error"); })
        .then(function () { if (previewBtn) previewBtn.classList.remove("is-busy"); });
    }

    // Delegated: rows are replaced by HTMX.
    document.addEventListener("change", function (e) {
      var t = e.target;
      if (t.matches && t.matches("[data-promo-pick]")) updateCount();
      else if (t.matches && t.matches("[data-promo-all]")) {
        picks().forEach(function (p) { p.checked = t.checked; });
        updateCount();
      }
    });
    document.body.addEventListener("htmx:afterSwap", function (e) {
      var region = e.detail && e.detail.target;
      var list = document.getElementById("promo-list");
      if (list) $all("[data-promo-tab]").forEach(function (h) { h.value = list.getAttribute("data-tab") || "eligible"; });
      updateCount();
    });

    $all("[data-promo-mode-radio]", form).forEach(function (r) {
      r.addEventListener("change", function () { if (override) override.hidden = mode() !== "target"; stale(); });
    });
    $all("input, textarea, select", form).forEach(function (el) {
      if (el === notify || el.type === "hidden") return;
      el.addEventListener("change", stale);
    });
    $all("input[name=cascade_scope]", form).forEach(function (r) { r.addEventListener("change", fetchCascade); });
    if (notify) notify.addEventListener("change", function () { notifyTouched = true; });
    if (cascade) {
      cascade.addEventListener("change", function () {
        if (cascadeOpts) cascadeOpts.hidden = !cascade.checked;
        syncNotify();
        if (cascade.checked) fetchCascade();
        else if (cascadeText) cascadeText.textContent = DEFAULT_CASCADE;
      });
    }
    if (previewBtn) previewBtn.addEventListener("click", function (e) { e.preventDefault(); runPreview(); });
    // D9: on the profile the dialog opens with the next step already previewed.
    if (single) {
      document.addEventListener("click", function (e) {
        var b = e.target.closest && e.target.closest("[data-modal-open='promo-modal']");
        if (b && !preview.getAttribute("data-has-preview")) setTimeout(runPreview, 60);
      });
    }
    form.addEventListener("submit", function (e) {
      if (e.submitter && e.submitter === confirmBtn) {
        confirmBtn.classList.add("is-busy");
        toast("Saving…", "info");
      }
    });
    updateCount();
  }

  function initRollback() {
    var modalEl = $("[data-promo-rollback-modal]");
    if (!modalEl || !window.NBTE || !NBTE.modal) return;
    var form = $("[data-rollback-form]", modalEl);
    document.addEventListener("click", function (e) {
      var btn = e.target.closest && e.target.closest("[data-promo-rollback]");
      if (!btn) return;
      e.preventDefault();
      form.setAttribute("action", btn.getAttribute("data-rollback-url"));
      $("[data-rollback-name]", modalEl).textContent = btn.getAttribute("data-rollback-name");
      $("[data-rollback-change]", modalEl).textContent = btn.getAttribute("data-rollback-change");
      NBTE.modal(modalEl).open();
    });
  }

  function boot() {
    $all("[data-promo-modal]").forEach(initPromotion);
    initRollback();
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();
})();
