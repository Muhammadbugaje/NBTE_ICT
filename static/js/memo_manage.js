/* ==========================================================================
   Memo management — /manage/messages/
   - Conditional form fields (step approver kind, rule target kind)
   - Modal confirmations for destructive actions
   ========================================================================== */
(function () {
  "use strict";

  // -----------------------------------------------------------------
  // Step form: show approver_role only for role_in_department,
  //            show approver_user only for specific_user.
  // -----------------------------------------------------------------
  function initStepForm() {
    var kindSelect = document.querySelector("[data-mm-step-kind]");
    if (!kindSelect) return;

    var roleField = document.getElementById("mm-field-approver-role");
    var userField = document.getElementById("mm-field-approver-user");

    function sync() {
      var value = kindSelect.value;
      if (roleField) roleField.hidden = (value !== "role_in_department");
      if (userField) userField.hidden = (value !== "specific_user");
    }

    kindSelect.addEventListener("change", sync);
    sync();
  }

  // -----------------------------------------------------------------
  // Rule form: show target_role_codes only when
  //            target_kind === "specific_user".
  // -----------------------------------------------------------------
  function initRuleForm() {
    var kindSelect = document.querySelector("[data-mm-target-kind]");
    if (!kindSelect) return;

    var targetRoles = document.getElementById("mm-field-target-roles");
    var targetRolesHint = document.getElementById("mm-target-roles-hint");

    function sync() {
      var value = kindSelect.value;
      var showTargetRoles = (value === "specific_user");
      if (targetRoles) targetRoles.hidden = !showTargetRoles;
      if (targetRolesHint) targetRolesHint.hidden = !showTargetRoles;
    }

    kindSelect.addEventListener("change", sync);
    sync();
  }

  // -----------------------------------------------------------------
  // Confirm modals — data-mm-confirm attributes on forms.
  // -----------------------------------------------------------------
  function initConfirmations() {
    document.addEventListener("submit", function (e) {
      var form = e.target;
      if (!form || form.tagName !== "FORM") return;
      if (!form.hasAttribute("data-mm-confirm")) return;
      if (form.dataset.mmConfirmed === "1") {
        delete form.dataset.mmConfirmed;
        return;
      }

      e.preventDefault();

      var title = form.getAttribute("data-mm-confirm-title") || "Are you sure?";
      var body = form.getAttribute("data-mm-confirm-body") || "";
      var label = form.getAttribute("data-mm-confirm-label") || "Confirm";
      var tone = form.getAttribute("data-mm-confirm-tone") || "danger";

      openConfirmModal({
        title: title,
        body: body,
        confirmLabel: label,
        tone: tone,
      }).then(function (ok) {
        if (!ok) return;
        form.dataset.mmConfirmed = "1";
        form.submit();
      });
    });
  }

  var confirmResolve = null;

  function ensureConfirmModal() {
    var modal = document.getElementById("mm-confirm-modal");
    if (modal) return modal;

    modal = document.createElement("div");
    modal.className = "mm-modal";
    modal.id = "mm-confirm-modal";
    modal.setAttribute("data-mm-confirm-modal", "");
    modal.hidden = true;
    modal.innerHTML = ''
      + '<div class="mm-modal-backdrop" data-mm-confirm-close></div>'
      + '<div class="mm-modal-panel" role="dialog" aria-modal="true">'
      +   '<header class="mm-modal-head">'
      +     '<h3 data-mm-confirm-title>Are you sure?</h3>'
      +     '<p data-mm-confirm-body></p>'
      +   '</header>'
      +   '<footer class="mm-modal-actions">'
      +     '<button type="button" class="btn btn-ghost" data-mm-confirm-close>Cancel</button>'
      +     '<button type="button" class="btn btn-primary" data-mm-confirm-yes>Confirm</button>'
      +   '</footer>'
      + '</div>';

    document.body.appendChild(modal);

    modal.addEventListener("click", function (e) {
      if (e.target.closest("[data-mm-confirm-close]")) {
        closeConfirmModal(false);
      } else if (e.target.closest("[data-mm-confirm-yes]")) {
        closeConfirmModal(true);
      }
    });

    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && !modal.hidden) closeConfirmModal(false);
    });

    return modal;
  }

  function openConfirmModal(opts) {
    var modal = ensureConfirmModal();
    modal.querySelector("[data-mm-confirm-title]").textContent = opts.title;
    modal.querySelector("[data-mm-confirm-body]").textContent = opts.body;

    var yes = modal.querySelector("[data-mm-confirm-yes]");
    yes.textContent = opts.confirmLabel;
    yes.className = "btn " + (opts.tone === "danger" ? "btn-danger" : "btn-primary");

    modal.hidden = false;

    setTimeout(function () {
      try { yes.focus(); } catch (e) {}
    }, 40);

    return new Promise(function (resolve) {
      confirmResolve = resolve;
    });
  }

  function closeConfirmModal(result) {
    var modal = document.getElementById("mm-confirm-modal");
    if (modal) modal.hidden = true;
    if (confirmResolve) {
      confirmResolve(result);
      confirmResolve = null;
    }
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
    try { initStepForm(); }       catch (e) { console.error("[mm] step form:", e); }
    try { initRuleForm(); }       catch (e) { console.error("[mm] rule form:", e); }
    try { initConfirmations(); }  catch (e) { console.error("[mm] confirmations:", e); }
  });
})();