/* ==========================================================================
   NBTE ICT — Portal JS (v2)
   - Conditional scope forms (visibility / reminder-kind)
   - Recipient / role picker (messaging-style checkboxes with search)
   - Image preview on file inputs
   - Filter debounce (HTMX-friendly)
   - Modals + confirm-submit
   - Reminder dismiss
   - Copy-to-clipboard
   ========================================================================== */
(function () {
  "use strict";

  /* ============================================================
     1. FILTER DEBOUNCE
     ============================================================ */
  function initFilterDebounce() {
    var DEBOUNCE_MS = 320;
    document.querySelectorAll(".portal-filters").forEach(function (form) {
      if (form.__debounce) return;
      form.__debounce = true;
      var timer = null;
      form.addEventListener("input", function (e) {
        var t = e.target;
        if (!(t.tagName === "INPUT" || t.tagName === "SELECT")) return;
        if (timer) clearTimeout(timer);
        timer = setTimeout(function () {
          if (window.htmx && form.hasAttribute("hx-get")) window.htmx.trigger(form, "submit");
          else form.submit();
        }, DEBOUNCE_MS);
      });
      form.addEventListener("change", function (e) {
        if (e.target.tagName !== "SELECT") return;
        if (window.htmx && form.hasAttribute("hx-get")) window.htmx.trigger(form, "submit");
        else form.submit();
      });
    });
  }

  function initClearFilters() {
    document.addEventListener("click", function (e) {
      var btn = e.target.closest("[data-clear-filters]");
      if (!btn) return;
      e.preventDefault();
      var form = document.querySelector(".portal-filters");
      if (!form) return;
      form.querySelectorAll("input, select").forEach(function (el) {
        if (el.type === "checkbox" || el.type === "radio") el.checked = false;
        else if (el.tagName === "SELECT") el.selectedIndex = 0;
        else el.value = "";
      });
      if (window.htmx && form.hasAttribute("hx-get")) window.htmx.trigger(form, "submit");
      else form.submit();
    });
  }

  /* ============================================================
     2. SCOPE WATCHER — show/hide form slots per select value
     ============================================================ */
  function initScopeWatcher() {
    document.querySelectorAll("[data-scope-form]").forEach(function (form) {
      var controller = form.querySelector("[data-scope-controller]");
      if (!controller) return;
      var scopeName = form.getAttribute("data-scope-form");

      function refresh() {
        var current = controller.value;
        form.querySelectorAll('.portal-scope-slot[data-scope-for="' + scopeName + '"]').forEach(function (slot) {
          var whenList = (slot.getAttribute("data-scope-when") || "").split(",").map(function (s) { return s.trim(); });
          var visible = whenList.indexOf(current) !== -1;
          slot.classList.toggle("is-visible", visible);
        });
      }

      controller.addEventListener("change", refresh);
      refresh();
    });
  }

  /* ============================================================
     3. PICKER — replacement UI for <select multiple> with checkboxes
     ============================================================ */
  function initPickers() {
    document.querySelectorAll("[data-portal-picker]").forEach(function (root) {
      if (root.__pickerReady) return;
      root.__pickerReady = true;

      var select = root.querySelector("select.js-picker-source");
      if (!select) return;

      // ---- collect options
      var options = Array.from(select.options).map(function (o) {
        return { value: o.value, label: o.textContent.trim(), selected: o.selected };
      });

      // ---- build control UI
      var control = document.createElement("div");
      control.className = "portal-picker-control";
      control.innerHTML =
        '<div class="portal-picker-chips">' +
          '<input type="text" class="portal-picker-search" placeholder="Type to search…" autocomplete="off">' +
        '</div>' +
        '<button type="button" class="portal-picker-caret" aria-label="Toggle list">' +
          '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"/></svg>' +
        '</button>';
      var chipsBox = control.querySelector(".portal-picker-chips");
      var search   = control.querySelector(".portal-picker-search");

      // ---- build panel
      var panel = document.createElement("div");
      panel.className = "portal-picker-panel";
      panel.style.display = "none";
      var head = document.createElement("div");
      head.className = "portal-picker-head";
      head.innerHTML = '<span data-count>0 selected</span><button type="button" class="portal-picker-clear" data-clear>Clear all</button>';
      var list = document.createElement("ul");
      list.className = "portal-picker-list";

      options.forEach(function (opt, idx) {
        var li = document.createElement("li");
        li.className = "portal-picker-item";
        li.setAttribute("data-search", opt.label.toLowerCase());
        li.innerHTML =
          '<label>' +
            '<input type="checkbox" class="portal-picker-checkbox" value="' + opt.value + '"' + (opt.selected ? ' checked' : '') + '>' +
            '<span class="portal-avatar"></span>' +
            '<span class="portal-user-info">' +
              '<strong></strong>' +
              '<small></small>' +
            '</span>' +
            '<svg class="portal-picker-check" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>' +
          '</label>';
        var strong = li.querySelector("strong");
        var small  = li.querySelector("small");
        var avatar = li.querySelector(".portal-avatar");
        // Split label on " — " if present, otherwise treat whole as name
        var parts = opt.label.split(" — ");
        strong.textContent = parts[0] || opt.label;
        small.textContent  = parts[1] || "";
        var initials = (parts[0] || opt.label).replace(/[^A-Za-z ]/g, "").split(/\s+/).slice(0, 2).map(function (w) { return w[0] || ""; }).join("").toUpperCase() || "•";
        avatar.textContent = initials;
        list.appendChild(li);
      });

      panel.appendChild(head);
      panel.appendChild(list);

      // Replace the select with a hidden version + control + panel
      var wrap = document.createElement("div");
      wrap.style.position = "relative";
      select.parentNode.insertBefore(wrap, select);
      wrap.appendChild(control);
      wrap.appendChild(panel);
      wrap.appendChild(select);

      // ---- functions
      function syncFromSelect() {
        Array.from(select.options).forEach(function (o) {
          var cb = list.querySelector('.portal-picker-checkbox[value="' + CSS.escape(o.value) + '"]');
          if (cb) cb.checked = o.selected;
        });
        renderChips();
      }

      function syncToSelect() {
        Array.from(select.options).forEach(function (o) {
          var cb = list.querySelector('.portal-picker-checkbox[value="' + CSS.escape(o.value) + '"]');
          if (cb) o.selected = cb.checked;
        });
      }

      function renderChips() {
        chipsBox.querySelectorAll(".portal-chip").forEach(function (c) { c.remove(); });
        var checked = list.querySelectorAll(".portal-picker-checkbox:checked");
        checked.forEach(function (cb) {
          var li = cb.closest(".portal-picker-item");
          var name = li.querySelector("strong").textContent;
          var initials = li.querySelector(".portal-avatar").textContent;
          var chip = document.createElement("span");
          chip.className = "portal-chip";
          chip.setAttribute("data-value", cb.value);
          chip.innerHTML =
            '<span class="portal-chip-avatar"></span>' +
            '<span class="portal-chip-name"></span>' +
            '<button type="button" class="portal-chip-x" aria-label="Remove">×</button>';
          chip.querySelector(".portal-chip-avatar").textContent = initials;
          chip.querySelector(".portal-chip-name").textContent = name;
          chipsBox.insertBefore(chip, search);
        });
        head.querySelector("[data-count]").textContent = checked.length + " selected";
      }

      function openPanel() { panel.style.display = ""; setTimeout(function () { search.focus(); }, 0); }
      function closePanel() { panel.style.display = "none"; }
      function togglePanel() { panel.style.display === "none" || !panel.style.display ? openPanel() : closePanel(); }

      // ---- events
      list.addEventListener("change", function (e) {
        if (!e.target.classList.contains("portal-picker-checkbox")) return;
        syncToSelect();
        renderChips();
      });
      chipsBox.addEventListener("click", function (e) {
        var x = e.target.closest(".portal-chip-x");
        if (!x) return;
        e.stopPropagation();
        var chip = x.closest(".portal-chip");
        var val = chip.getAttribute("data-value");
        var cb = list.querySelector('.portal-picker-checkbox[value="' + CSS.escape(val) + '"]');
        if (cb) { cb.checked = false; syncToSelect(); renderChips(); }
      });
      search.addEventListener("input", function () {
        var q = search.value.trim().toLowerCase();
        list.querySelectorAll(".portal-picker-item").forEach(function (li) {
          var hay = li.getAttribute("data-search") || "";
          li.style.display = (!q || hay.indexOf(q) !== -1) ? "" : "none";
        });
      });
      control.addEventListener("click", function (e) {
        if (e.target.classList.contains("portal-chip-x")) return;
        openPanel();
      });
      search.addEventListener("focus", openPanel);
      control.querySelector(".portal-picker-caret").addEventListener("click", function (e) {
        e.stopPropagation();
        togglePanel();
      });
      head.querySelector("[data-clear]").addEventListener("click", function () {
        list.querySelectorAll(".portal-picker-checkbox").forEach(function (cb) { cb.checked = false; });
        syncToSelect();
        renderChips();
      });
      document.addEventListener("click", function (e) {
        if (!wrap.contains(e.target)) closePanel();
      });
      document.addEventListener("keydown", function (e) {
        if (e.key === "Escape") closePanel();
      });

      // Initial render
      renderChips();
    });
  }

  /* ============================================================
     4. IMAGE PREVIEW
     ============================================================ */
  function initImagePreview() {
    document.querySelectorAll("input[type=file][data-image-preview]").forEach(function (input) {
      // If no explicit target, look for the next .portal-image-preview sibling
      var explicit = input.getAttribute("data-image-preview");
      var target = null;
      if (explicit && explicit !== "this") {
        target = document.querySelector(explicit);
      } else {
        // Walk up to the field, then find the preview inside it
        var field = input.closest(".portal-field");
        if (field) target = field.querySelector(".portal-image-preview");
      }
      if (!target) return;
      if (input.__previewWired) return;
      input.__previewWired = true;

      input.addEventListener("change", function () {
        var file = input.files && input.files[0];
        if (!file) return;
        if (!/^image\//.test(file.type)) return;
        var url = URL.createObjectURL(file);
        target.innerHTML = "";
        var img = document.createElement("img");
        img.src = url;
        img.alt = "";
        target.appendChild(img);
        target.classList.add("has-image");
      });
    });
  }

  /* ============================================================
     5. MODALS + CONFIRM-SUBMIT
     ============================================================ */
  function openModal(id) {
    var m = document.getElementById(id);
    if (!m) return;
    m.removeAttribute("hidden");
    document.body.style.overflow = "hidden";
    var focusTarget = m.querySelector("input:not([type=hidden]),textarea,select,button");
    if (focusTarget) setTimeout(function () { try { focusTarget.focus(); } catch (e) {} }, 60);
  }
  function closeModal(el) {
    var m = el && el.closest ? el.closest("[data-portal-modal]") : null;
    if (!m) {
      var open = document.querySelector("[data-portal-modal]:not([hidden])");
      if (!open) return;
      m = open;
    }
    m.setAttribute("hidden", "");
    document.body.style.overflow = "";
  }
  function initModals() {
    document.addEventListener("click", function (e) {
      var opener = e.target.closest("[data-open-portal-modal]");
      if (opener) {
        e.preventDefault();
        openModal(opener.getAttribute("data-open-portal-modal"));
        return;
      }
      if (e.target.closest("[data-portal-close]")) {
        e.preventDefault(); closeModal(e.target); return;
      }
      if (e.target.matches("[data-portal-modal-backdrop]")) {
        closeModal(e.target); return;
      }
    });
    document.addEventListener("keydown", function (e) {
      if (e.key !== "Escape") return;
      document.querySelectorAll("[data-portal-modal]:not([hidden])").forEach(function (m) {
        m.setAttribute("hidden", ""); document.body.style.overflow = "";
      });
    });
  }
  function initConfirmSubmit() {
    document.addEventListener("submit", function (e) {
      var form = e.target;
      if (!form || !form.hasAttribute) return;
      if (!form.hasAttribute("data-confirm-submit")) return;
      if (form.__confirmed) { delete form.__confirmed; return; }
      e.preventDefault();
      var modalId = form.getAttribute("data-confirm-submit");
      var modal = document.getElementById(modalId);
      if (!modal) { form.__confirmed = true; form.submit(); return; }
      var goBtn = modal.querySelector("[data-confirm-go]");
      if (goBtn) {
        if (goBtn.__wired) goBtn.removeEventListener("click", goBtn.__wired);
        goBtn.__wired = function () {
          form.__confirmed = true; closeModal(goBtn); form.submit();
        };
        goBtn.addEventListener("click", goBtn.__wired);
      }
      openModal(modalId);
    });
  }

  /* ============================================================
     6. REMINDER DISMISS
     ============================================================ */
  function initReminderDismiss() {
    document.addEventListener("click", function (e) {
      var btn = e.target.closest("[data-dismiss-reminder]");
      if (!btn) return;
      e.preventDefault();
      var url = btn.getAttribute("data-dismiss-reminder");
      var card = btn.closest(".reminder-card") || btn.closest(".portal-card");
      fetch(url, {
        method: "POST",
        headers: {
          "X-CSRFToken": (document.cookie.match(/csrftoken=([^;]+)/) || [])[1] || "",
          "HX-Request": "true",
        },
        credentials: "same-origin",
      }).then(function () {
        if (card) {
          card.style.transition = "opacity .16s ease, transform .16s ease";
          card.style.opacity = "0";
          card.style.transform = "translateX(8px)";
          setTimeout(function () { card.style.display = "none"; }, 160);
        }
      }).catch(function () {});
    });
  }

  /* ============================================================
     7. COPY BUTTON
     ============================================================ */
  function initCopyButtons() {
    document.addEventListener("click", function (e) {
      var btn = e.target.closest("[data-copy]");
      if (!btn) return;
      e.preventDefault();
      var text = btn.getAttribute("data-copy");
      if (!text) return;
      var done = function () {
        var old = btn.textContent;
        btn.textContent = "Copied!";
        setTimeout(function () { btn.textContent = old; }, 1400);
      };
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(done).catch(done);
      } else {
        var tmp = document.createElement("textarea");
        tmp.value = text; document.body.appendChild(tmp);
        tmp.select(); try { document.execCommand("copy"); } catch (err) {}
        document.body.removeChild(tmp); done();
      }
    });
  }

  /* ============================================================
     8. HTMX loading hint on list containers
     ============================================================ */
  function initHtmxLoadingStates() {
    document.addEventListener("htmx:beforeRequest", function (evt) {
      var el = evt.detail.elt;
      if (!el || !el.closest) return;
      var target = el.closest("[data-portal-list]");
      if (target) target.classList.add("is-loading");
    });
    document.addEventListener("htmx:afterRequest", function (evt) {
      var el = evt.detail.elt;
      if (!el || !el.closest) return;
      var target = el.closest("[data-portal-list]");
      if (target) target.classList.remove("is-loading");
    });
  }

  /* ============================================================
     BOOT
     ============================================================ */
  function boot() {
    try { initFilterDebounce(); } catch (e) { console.error("[portal] filters", e); }
    try { initClearFilters(); } catch (e) { console.error("[portal] clear", e); }
    try { initScopeWatcher(); } catch (e) { console.error("[portal] scope", e); }
    try { initPickers(); } catch (e) { console.error("[portal] pickers", e); }
    try { initImagePreview(); } catch (e) { console.error("[portal] preview", e); }
    try { initModals(); } catch (e) { console.error("[portal] modals", e); }
    try { initConfirmSubmit(); } catch (e) { console.error("[portal] confirm", e); }
    try { initReminderDismiss(); } catch (e) { console.error("[portal] dismiss", e); }
    try { initCopyButtons(); } catch (e) { console.error("[portal] copy", e); }
    try { initHtmxLoadingStates(); } catch (e) { console.error("[portal] htmx", e); }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }

  window.PortalJS = { openModal: openModal, closeModal: closeModal };
})();