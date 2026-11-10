/* ==========================================================================
   NBTE ICT — Memos
   - Target-picker toggle on the compose form
   - Searchable single-select for "Send to" (specific_user)
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
          var wasHidden = el.hidden;
          el.hidden = !matches;
          // v2.1 Phase E: refresh a live count when its panel appears.
          if (matches && wasHidden && el.hasAttribute("hx-get") && window.htmx) {
            window.htmx.trigger(el, "change");
          }
        });
      }

      visibility.addEventListener("change", sync);
      sync();
    });
  }

  // -----------------------------------------------------------------
  // Searchable single-select for MemoForm.target_user
  // -----------------------------------------------------------------
  function initMemoUserPicker() {
    var pickers = document.querySelectorAll("[data-memo-user-picker]");
    pickers.forEach(function (picker) {
      var control = picker.querySelector(".memo-user-picker-control");
      var search  = picker.querySelector("[data-memo-user-search]");
      var panel   = picker.querySelector("[data-memo-user-panel]");
      var list    = picker.querySelector("[data-memo-user-list]");
      var chip    = picker.querySelector("[data-memo-user-chip]");
      var chipName  = picker.querySelector("[data-memo-user-chip-name]");
      var chipClear = picker.querySelector("[data-memo-user-chip-clear]");
      var hidden    = picker.querySelector('input[name="target_user"]');

      if (!search || !panel || !list || !hidden) return;

      // pk → display name, built once from the rendered list.
      var nameMap = {};
      list.querySelectorAll("li[data-pk]").forEach(function (li) {
        var strong = li.querySelector("strong");
        nameMap[li.getAttribute("data-pk")] =
          strong ? strong.textContent.trim() : ("User #" + li.getAttribute("data-pk"));
      });

      function setChip(pk) {
        if (!pk) {
          chip.hidden = true;
          if (chipName) chipName.textContent = "";
          return;
        }
        var name = nameMap[pk] || ("User #" + pk);
        if (chipName) chipName.textContent = name;
        chip.hidden = false;
      }

      function filter() {
        var q = search.value.trim().toLowerCase();
        var visible = 0;
        list.querySelectorAll("li[data-pk]").forEach(function (li) {
          var hay = (li.getAttribute("data-search-text") || "").toLowerCase();
          var match = !q || hay.indexOf(q) !== -1;
          li.style.display = match ? "" : "none";
          if (match) visible++;
        });
        var empty = list.querySelector(".memo-user-picker-empty");
        if (empty) empty.style.display = visible ? "none" : "";
      }

      function openPanel() {
        panel.hidden = false;
        setTimeout(function () { search.focus(); }, 0);
      }
      function closePanel() { panel.hidden = true; }

      function selectPk(pk) {
        hidden.value = pk;
        setChip(pk);
        search.value = "";
        filter();
        closePanel();
      }

      search.addEventListener("focus", openPanel);
      search.addEventListener("input", function () {
        if (search.value && panel.hidden) openPanel();
        filter();
      });

      list.addEventListener("click", function (e) {
        var li = e.target.closest("li[data-pk]");
        if (!li) return;
        e.preventDefault();
        selectPk(li.getAttribute("data-pk"));
      });

      // Keyboard nav: ↑/↓ to move, Enter to select, Esc to close.
      search.addEventListener("keydown", function (e) {
        if (e.key === "Escape") { closePanel(); return; }
        if (e.key === "ArrowDown" || e.key === "ArrowUp") {
          e.preventDefault();
          var visible = Array.prototype.slice.call(
            list.querySelectorAll("li[data-pk]")
          ).filter(function (li) { return li.style.display !== "none"; });
          if (!visible.length) return;
          var current = list.querySelector("li.is-focused");
          var idx = current ? visible.indexOf(current) : -1;
          var next = e.key === "ArrowDown"
            ? Math.min(visible.length - 1, idx + 1)
            : (idx === -1 ? visible.length - 1 : Math.max(0, idx - 1));
          if (current) current.classList.remove("is-focused");
          visible[next].classList.add("is-focused");
          visible[next].scrollIntoView({ block: "nearest" });
        } else if (e.key === "Enter") {
          var focused = list.querySelector("li.is-focused");
          if (focused) {
            e.preventDefault();
            selectPk(focused.getAttribute("data-pk"));
          }
        }
      });

      if (chipClear) {
        chipClear.addEventListener("click", function (e) {
          e.preventDefault();
          e.stopPropagation();
          hidden.value = "";
          setChip("");
          search.focus();
        });
      }

      // Click anywhere on the control → focus the search input.
      if (control) {
        control.addEventListener("click", function (e) {
          if (e.target.closest("[data-memo-user-chip-clear]")) return;
          search.focus();
        });
      }

      // Click outside closes.
      document.addEventListener("click", function (e) {
        if (!picker.contains(e.target)) closePanel();
      });

      // Initial chip for edit mode (or a failed POST round-trip).
      if (hidden.value) setChip(hidden.value);
      filter();
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
  // v2.1 Phase E: memo type (internal / external / general) and the
  // institution scope (all / selected / filter builder)
  // -----------------------------------------------------------------
  function initMemoKind() {
    document.querySelectorAll("[data-memo-form]").forEach(function (form) {
      var kinds = form.querySelectorAll('input[name="kind"]');
      var staff = form.querySelector("[data-staff-audience]");
      var inst = form.querySelector("[data-inst-section]");
      var label = form.querySelector("[data-send-label]");
      var btn = label ? label.closest("button") : null;

      function currentKind() {
        var k = "internal";
        kinds.forEach(function (r) { if (r.checked) k = r.value; });
        return k;
      }
      function syncKind() {
        var k = currentKind();
        form.setAttribute("data-kind", k);
        form.querySelectorAll("[data-kind-card]").forEach(function (c) {
          c.classList.toggle("is-selected", c.getAttribute("data-kind-card") === k);
        });
        if (staff) staff.hidden = (k === "external");
        if (inst) {
          var wasHidden = inst.hidden;
          inst.hidden = (k === "internal");
          if (!inst.hidden && wasHidden && window.htmx) window.htmx.trigger(inst, "change");
        }
        if (label && btn) {
          label.textContent = btn.getAttribute(k === "internal" ? "data-send-label-internal" : "data-send-label-outbound");
        }
      }
      if (kinds.length) {
        if (!currentKind() || !Array.prototype.some.call(kinds, function (r) { return r.checked; })) {
          kinds[0].checked = true;
        }
        kinds.forEach(function (r) { r.addEventListener("change", syncKind); });
      }
      syncKind();

      if (!inst) return;
      var scopes = inst.querySelectorAll('input[name="institution_scope"]');
      var panels = inst.querySelectorAll("[data-inst-panel]");
      function syncScope() {
        var v = "";
        scopes.forEach(function (r) { if (r.checked) v = r.value; });
        panels.forEach(function (p) { p.hidden = p.getAttribute("data-inst-panel") !== v; });
        inst.querySelectorAll(".memo-scope-pill").forEach(function (pill) {
          var input = pill.querySelector("input");
          pill.classList.toggle("is-selected", !!(input && input.checked));
        });
      }
      scopes.forEach(function (r) { r.addEventListener("change", syncScope); });
      syncScope();

      var search = inst.querySelector("[data-inst-search]");
      var list = inst.querySelector("[data-inst-list]");
      if (search && list) {
        search.addEventListener("input", function () {
          var q = search.value.trim().toLowerCase();
          list.querySelectorAll("label[data-search-text]").forEach(function (row) {
            row.hidden = q && row.getAttribute("data-search-text").indexOf(q) === -1;
          });
        });
      }
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
    try { initTargetPicker(); }    catch (e) { console.error("[NBTE memo] target picker:", e); }
    try { initMemoUserPicker(); }  catch (e) { console.error("[NBTE memo] user picker:", e); }
    try { initActionModals(); }    catch (e) { console.error("[NBTE memo] modals:", e); }
    try { initAckBar(); }          catch (e) { console.error("[NBTE memo] ack bar:", e); }
    try { initMemoKind(); }        catch (e) { console.error("[NBTE memo] kind:", e); }
  });

  window.NBTEMemo = {
    initTargetPicker: initTargetPicker,
    initMemoUserPicker: initMemoUserPicker,
    initActionModals: initActionModals,
    initAckBar: initAckBar,
  };
})();