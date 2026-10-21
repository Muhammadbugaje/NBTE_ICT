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
          el.hidden = !matches;
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
  });

  window.NBTEMemo = {
    initTargetPicker: initTargetPicker,
    initMemoUserPicker: initMemoUserPicker,
    initActionModals: initActionModals,
    initAckBar: initAckBar,
  };
})();