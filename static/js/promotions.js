/* Promotions (staff/promotions/*, staff detail): selection, the promotion
   dialog (target filtering, live cascade count, fetch preview) and the
   rollback dialog. Uses NBTE.modal / NBTE.toast from ui.js. */
(function () {
  "use strict";

  function $(sel, root) { return (root || document).querySelector(sel); }
  function $all(sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); }

  /* ------------------------------------------------------------ dialog */
  function initPromotion(root) {
    var form = $("[data-promo-form]", root);
    if (!form) return;
    var picks = $all("[data-promo-pick]");               // table checkboxes (form="promo-form")
    var fixed = $("[data-promo-fixed]", form);            // single-staff hidden input
    var target = $("[data-promo-target]", form);
    var preview = $("[data-promo-preview]", form);
    var confirmBtn = $("[data-promo-confirm]", form);
    var previewBtn = $("[data-promo-preview-btn]", form);
    var cascade = $("[data-promo-cascade]", form);
    var cascadeText = $("[data-promo-cascade-text]", form);
    var cascadeOpts = $("[data-promo-cascade-options]", form);
    var notify = $("[data-promo-notify]", form);
    var grade = $("[data-promo-grade]", form);
    var tiers = $("[data-promo-tiers]", form);
    var tierList = $("[data-promo-tier-list]", form);
    var countEl = $("[data-promo-count]");
    var openBtn = $("[data-promo-open]");
    var allBox = $("[data-promo-all]");
    var titleCount = $("[data-promo-title-count]", form);
    var notifyTouched = false;
    var countTimer = null;

    function selected() {
      if (fixed) return [fixed];
      return picks.filter(function (p) { return p.checked; });
    }

    function stale() {
      if (confirmBtn) confirmBtn.disabled = true;
      if (preview && preview.getAttribute("data-has-preview")) {
        preview.innerHTML = '<p class="promo-preview-empty">Something changed — press <strong>Preview</strong> again.</p>';
        preview.removeAttribute("data-has-preview");
      }
    }

    function syncTargets() {
      var sel = selected();
      var ranks = sel.map(function (p) { return parseInt(p.getAttribute("data-rank") || "0", 10); });
      var low = ranks.length ? Math.min.apply(null, ranks) : 0;
      $all("option[data-rank]", target).forEach(function (o) {
        var ok = parseInt(o.getAttribute("data-rank"), 10) > low;
        o.hidden = !ok;
        o.disabled = !ok;
      });
      if (target.selectedOptions[0] && target.selectedOptions[0].disabled) target.value = "";
      var names = {};
      sel.forEach(function (p) { names[p.getAttribute("data-designation")] = true; });
      var list = Object.keys(names);
      if (tiers) {
        tiers.hidden = list.length < 2;
        if (tierList) tierList.textContent = list.join(" · ") + ". Only designations above the lowest one are offered.";
      }
    }

    function syncGrade() {
      var o = target.selectedOptions[0];
      if (!grade) return;
      if (!o || !o.value) { grade.textContent = "—"; grade.classList.remove("is-warn"); return; }
      var g = o.getAttribute("data-grade");
      grade.textContent = g ? "CONTEDISS " + g : "Unchanged — no range set";
      grade.classList.toggle("is-warn", !g);
    }

    function syncNotify() {
      if (!notify || notifyTouched) return;
      var single = selected().length === 1 && !(cascade && cascade.checked);
      notify.checked = single;
    }

    function updateCount() {
      var sel = selected();
      if (countEl) countEl.textContent = String(sel.length);
      if (openBtn) openBtn.disabled = sel.length === 0;
      if (titleCount) titleCount.textContent = sel.length === 1 ? "1 staff member" : sel.length + " staff";
      if (allBox) {
        allBox.checked = picks.length > 0 && sel.length === picks.length;
        allBox.indeterminate = sel.length > 0 && sel.length < picks.length;
      }
      syncTargets();
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
        cascadeText.textContent = "Counting…";
        fetch(root.getAttribute("data-count-url") + "?" + params.toString(), {
          credentials: "same-origin", headers: { "X-Requested-With": "fetch" }
        }).then(function (r) { return r.json(); }).then(function (data) {
          var desig = data.tiers && data.tiers.length ? data.tiers.join(" / ") : "the same designation";
          cascadeText.textContent = data.count === 0
            ? "Nobody else on " + desig + " in the same department was appointed later — the cascade adds no one."
            : "Also promote " + data.count + " other staff currently on " + desig +
              " whose date of present appointment is later than the selected staff.";
        }).catch(function () {
          cascadeText.textContent = "Couldn't count the cascade — the preview will show it.";
        });
      }, 150);
    }

    picks.forEach(function (p) { p.addEventListener("change", updateCount); });
    if (allBox) {
      allBox.addEventListener("change", function () {
        picks.forEach(function (p) { p.checked = allBox.checked; });
        updateCount();
      });
    }
    target.addEventListener("change", function () { syncGrade(); stale(); });
    $all("input, textarea, select", form).forEach(function (el) {
      if (el === target || el === notify || el.type === "hidden") return;
      el.addEventListener("change", stale);
    });
    if (notify) notify.addEventListener("change", function () { notifyTouched = true; });
    if (cascade) {
      cascade.addEventListener("change", function () {
        if (cascadeOpts) cascadeOpts.hidden = !cascade.checked;
        syncNotify();
        if (cascade.checked) fetchCascade();
        else if (cascadeText) cascadeText.textContent =
          "Also promote other staff on the same designation in the same department whose present appointment is later.";
      });
    }

    // Preview through fetch; the button's formaction is the no-JS fallback.
    if (previewBtn) {
      previewBtn.addEventListener("click", function (e) {
        e.preventDefault();
        if (!target.value) {
          target.focus();
          if (window.NBTE && NBTE.toast) NBTE.toast("Choose the new designation first.", "warning");
          return;
        }
        var data = new FormData(form);
        previewBtn.classList.add("is-busy");
        fetch(root.getAttribute("data-preview-url"), {
          method: "POST", body: data, credentials: "same-origin",
          headers: { "X-Requested-With": "fetch" }
        }).then(function (r) {
          if (!r.ok) throw new Error(r.status);
          return r.text();
        }).then(function (html) {
          preview.innerHTML = html;
          preview.setAttribute("data-has-preview", "1");
          var ok = preview.querySelector("[data-preview-ok='1']");
          if (confirmBtn) confirmBtn.disabled = !ok;
          if (window.matchMedia && window.matchMedia("(max-width: 767px)").matches) {
            preview.scrollIntoView({ block: "start" });
          }
        }).catch(function () {
          if (window.NBTE && NBTE.toast) NBTE.toast("The preview could not be loaded. Try again.", "error");
        }).then(function () { previewBtn.classList.remove("is-busy"); });
      });
    }
    form.addEventListener("submit", function (e) {
      if (e.submitter && e.submitter === confirmBtn) {
        confirmBtn.classList.add("is-busy");
        if (window.NBTE && NBTE.toast) NBTE.toast("Saving the promotion…", "info", { duration: 4000 });
      }
    });

    updateCount();
    syncGrade();
  }

  /* ---------------------------------------------------------- rollback */
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
