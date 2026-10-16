/* ==========================================================================
   NBTE ICT — groups.js (Stage 3: groups & permissions pages)

   Page behaviour only. Uses the Stage 2 primitives and adds none:
     NBTE.modal     delete / discard / matrix dialogs
     NBTE.collapse  per-app accordions (wired by ui.js via data-collapse-trigger)
     NBTE.toast     success messages (Django messages -> toasts in base.html)
     NBTE.formValidation, NBTE.ripple, NBTE.scrollReveal  applied by ui.js
   Everything is server-enforced; this file only improves the experience.
   ========================================================================== */
(function () {
  "use strict";

  function $all(scope, sel) { return Array.prototype.slice.call((scope || document).querySelectorAll(sel)); }
  function openModal(id) {
    var m = window.NBTE && NBTE.modal && NBTE.modal(id);
    if (m) { m.open(); return true; }
    return false;
  }

  /* ------------------------------------------------------------ picker */
  function initPicker(picker) {
    var form = picker.closest("form");
    var summary = form && form.querySelector("[data-perm-summary]");
    var apps = $all(picker, "[data-perm-app]");

    function boxes(app) { return $all(app, "[data-perm-row] input[type=checkbox]"); }

    function syncApp(app) {
      var all = boxes(app);
      var on = all.filter(function (b) { return b.checked; }).length;
      var editable = all.filter(function (b) { return !b.disabled && !b.closest("[data-perm-row]").hidden; });
      var editableOn = editable.filter(function (b) { return b.checked; }).length;
      var master = app.querySelector("[data-select-all]");
      if (master) {
        master.disabled = editable.length === 0;
        master.checked = editable.length > 0 && editableOn === editable.length;
        master.indeterminate = editableOn > 0 && editableOn < editable.length;
      }
      var pill = app.querySelector("[data-app-pill]");
      var count = app.querySelector("[data-app-selected]");
      if (count) count.textContent = String(on);
      if (pill) pill.hidden = on === 0;
      app.classList.toggle("has-selection", on > 0);
      return on;
    }

    function syncSummary() {
      var total = 0, appCount = 0, list = [];
      apps.forEach(function (app) {
        var on = syncApp(app);
        if (on) { total += on; appCount += 1; list.push([app.getAttribute("data-app-name"), on]); }
      });
      if (!summary) return;
      var t = summary.querySelector("[data-summary-total]");
      var a = summary.querySelector("[data-summary-apps]");
      var ul = summary.querySelector("[data-summary-list]");
      var empty = summary.querySelector("[data-summary-empty]");
      if (t) t.textContent = String(total);
      if (a) a.textContent = String(appCount);
      if (empty) empty.hidden = total > 0;
      if (ul) {
        ul.textContent = "";
        list.forEach(function (row) {
          var li = document.createElement("li");
          var n = document.createElement("span"); n.textContent = row[0];
          var c = document.createElement("span"); c.textContent = String(row[1]);
          li.appendChild(n); li.appendChild(c); ul.appendChild(li);
        });
      }
    }

    apps.forEach(function (app) {
      var master = app.querySelector("[data-select-all]");
      if (master) {
        master.addEventListener("change", function () {
          boxes(app).forEach(function (b) {
            if (!b.disabled && !b.closest("[data-perm-row]").hidden) b.checked = master.checked;
          });
          syncSummary();
          if (form) form.dispatchEvent(new Event("change", { bubbles: true }));
        });
      }
      var search = app.querySelector("[data-app-search]");
      if (search) {
        search.addEventListener("input", function () {
          var q = search.value.trim().toLowerCase();
          var shown = 0;
          $all(app, "[data-perm-row]").forEach(function (row) {
            var hit = !q || row.getAttribute("data-search").indexOf(q) !== -1;
            row.hidden = !hit;
            if (hit) shown += 1;
          });
          var none = app.querySelector("[data-no-match]");
          if (none) none.hidden = shown > 0;
          syncApp(app);
        });
      }
      boxes(app).forEach(function (b) { b.addEventListener("change", syncSummary); });
    });

    $all(picker, "[data-picker-expand]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var open = btn.getAttribute("data-picker-expand") === "open";
        apps.forEach(function (app) {
          var trig = app.querySelector("[data-collapse-trigger]");
          var api = trig && (trig.__nbteCollapse || (window.NBTE && NBTE.collapse && NBTE.collapse(trig)));
          if (api) { open ? api.open() : api.close(); }
        });
      });
    });

    syncSummary();
  }

  /* ------------------------------------------------------- dirty guard */
  function initDirtyGuard(form) {
    var dirty = false;
    form.addEventListener("change", function () { dirty = true; });
    form.addEventListener("input", function (e) {
      if (e.target && e.target.matches && e.target.matches("[data-app-search]")) return;
      dirty = true;
    });
    form.addEventListener("submit", function () { dirty = false; });
    $all(document, "[data-cancel-guard]").forEach(function (link) {
      link.addEventListener("click", function (e) {
        if (!dirty) return;
        var leave = document.querySelector("[data-discard-leave]");
        if (!leave) return;
        e.preventDefault();
        leave.setAttribute("href", link.getAttribute("href"));
        openModal("discard-modal");
      });
    });
  }

  /* ------------------------------------------------------------ delete */
  function initDelete() {
    var modal = document.getElementById("group-delete-modal");
    if (!modal) return;
    $all(document, "[data-group-delete]").forEach(function (link) {
      link.addEventListener("click", function (e) {
        e.preventDefault();
        var name = modal.querySelector("[data-delete-name]");
        var formEl = modal.querySelector("[data-delete-form]");
        if (name) name.textContent = link.getAttribute("data-delete-name");
        if (formEl) formEl.setAttribute("action", link.getAttribute("data-delete-url"));
        if (!openModal("group-delete-modal")) window.location.href = link.getAttribute("href");
      });
    });
  }

  /* ------------------------------------------------- group checklists */
  function initGroupRows() {
    $all(document, "[data-group-row] input[type=checkbox]").forEach(function (box) {
      var row = box.closest("[data-group-row]");
      box.addEventListener("change", function () { row.classList.toggle("is-checked", box.checked); });
    });
  }

  /* ------------------------------------------------ role grant preview */
  function initRolePreview(form) {
    var panel = document.querySelector("[data-role-preview-panel]");
    var baselineEl = document.getElementById("rg-baseline");
    if (!panel || !baselineEl) return;
    var baseline = [];
    try { baseline = JSON.parse(baselineEl.textContent) || []; } catch (e) { baseline = []; }

    function update() {
      var set = {};
      baseline.forEach(function (p) { set[p] = true; });
      $all(form, "input[name=groups]").forEach(function (b) {
        if (!b.checked) return;
        (b.getAttribute("data-perms") || "").split(" ").forEach(function (p) { if (p) set[p] = true; });
      });
      var byApp = {};
      Object.keys(set).sort().forEach(function (label) {
        var i = label.indexOf(".");
        var app = label.slice(0, i);
        (byApp[app] = byApp[app] || []).push(label.slice(i + 1));
      });
      var apps = Object.keys(byApp);
      var total = Object.keys(set).length;
      var t = panel.querySelector("[data-preview-total]");
      var a = panel.querySelector("[data-preview-apps]");
      if (t) t.textContent = String(total);
      if (a) a.textContent = String(apps.length);
      var list = panel.querySelector("[data-preview-list]");
      if (!list) return;
      list.textContent = "";
      apps.forEach(function (app) {
        var li = document.createElement("li");
        var h = document.createElement("h4");
        h.textContent = app + " (" + byApp[app].length + ")";
        var ul = document.createElement("ul");
        ul.className = "gp-code-list";
        byApp[app].forEach(function (code) {
          var c = document.createElement("li");
          var el = document.createElement("code");
          el.textContent = app + "." + code;
          c.appendChild(el); ul.appendChild(c);
        });
        li.appendChild(h); li.appendChild(ul); list.appendChild(li);
      });
    }
    form.addEventListener("change", update);
  }

  /* ------------------------------------------------------------ matrix */
  function initMatrix() {
    var dataEl = document.getElementById("matrix-data");
    if (!dataEl) return;
    var data = {};
    try { data = JSON.parse(dataEl.textContent) || {}; } catch (e) { data = {}; }
    var modal = document.getElementById("matrix-modal");
    document.addEventListener("click", function (e) {
      var cell = e.target.closest && e.target.closest("[data-matrix-cell]");
      if (!cell || !modal) return;
      var d = data[cell.getAttribute("data-matrix-cell")];
      if (!d) return;
      modal.querySelector("[data-matrix-title]").textContent = d.role + " · " + d.app;
      var list = modal.querySelector("[data-matrix-list]");
      list.textContent = "";
      d.perms.forEach(function (p) {
        var li = document.createElement("li");
        var label = document.createElement("span"); label.textContent = p.label;
        var code = document.createElement("code"); code.textContent = p.code;
        li.appendChild(label); li.appendChild(code); list.appendChild(li);
      });
      openModal("matrix-modal");
    });
  }

  function boot() {
    $all(document, "[data-perm-picker]").forEach(initPicker);
    $all(document, "form[data-dirty-guard]").forEach(initDirtyGuard);
    $all(document, "form[data-role-preview]").forEach(initRolePreview);
    initDelete();
    initGroupRows();
    initMatrix();
    // Phones: the role list scrolls sideways; bring the selected role into view.
    var current = document.querySelector(".gp-role-link[aria-current='page']");
    if (current && current.parentElement && current.closest(".gp-role-nav").scrollWidth > current.closest(".gp-role-nav").clientWidth) {
      var nav = current.closest(".gp-role-nav");
      nav.scrollLeft = current.parentElement.offsetLeft - nav.clientWidth / 2 + current.parentElement.offsetWidth / 2;
    }
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();
})();
