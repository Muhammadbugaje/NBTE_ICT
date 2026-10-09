/* ==========================================================================
   NBTE ICT — Tasks module JS
   Self-contained. All helpers defined in this file. Safe to load alone.
   ========================================================================== */
(function () {
  "use strict";

  /* ============================================================
     SAFE CALL
     ============================================================ */
  function safeCall(label, fn) {
    try { fn(); }
    catch (e) { console.error("[NBTE-Tasks] " + label + " failed", e); }
  }

  /* ============================================================
     TIMER
     ============================================================ */
  function humanTimer(seconds) {
    var s = Math.max(0, Math.floor(seconds));
    var h = Math.floor(s / 3600);
    var m = Math.floor((s % 3600) / 60);
    var ss = s % 60;
    if (h > 0) {
      return h + ":" + String(m).padStart(2, "0") + ":" + String(ss).padStart(2, "0");
    }
    return String(m).padStart(2, "0") + ":" + String(ss).padStart(2, "0");
  }

  function tickTimers() {
    var els = document.querySelectorAll("[data-timer-started]");
    if (!els.length) return;
    var now = Date.now();
    els.forEach(function (el) {
      var started = new Date(el.getAttribute("data-timer-started")).getTime();
      if (isNaN(started)) return;
      el.textContent = humanTimer((now - started) / 1000);
    });
  }

  var timerInterval = null;
  function initTimers() {
    tickTimers();
    if (timerInterval) return;
    timerInterval = setInterval(tickTimers, 1000);
  }

  /* ============================================================
     AUTORESIZE TEXTAREAS
     ============================================================ */
  function autosize(el) {
    el.style.height = "auto";
    el.style.height = Math.min(el.scrollHeight, 300) + "px";
  }
  function initAutoresize() {
    document.querySelectorAll("textarea[data-autoresize]").forEach(function (el) {
      if (el.__autosize) return;
      el.__autosize = true;
      autosize(el);
      el.addEventListener("input", function () { autosize(el); });
    });
  }

  /* ============================================================
     CONFIRM MODAL
     ============================================================ */
  function ensureConfirmModal() {
    var modal = document.getElementById("tsk-confirm-modal");
    if (modal) return modal;
    modal = document.createElement("div");
    modal.id = "tsk-confirm-modal";
    modal.className = "tsk-modal-overlay";
    modal.setAttribute("hidden", "");
    modal.setAttribute("role", "dialog");
    modal.setAttribute("aria-modal", "true");
    modal.innerHTML = [
      '<div class="tsk-modal">',
      '  <div class="tsk-modal-head">',
      '    <div class="tsk-modal-icon">',
      '      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">',
      '        <path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/>',
      '        <line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/>',
      '      </svg>',
      '    </div>',
      '    <h3 class="tsk-modal-title" data-modal-title>Confirm</h3>',
      '  </div>',
      '  <div class="tsk-modal-body">',
      '    <p data-modal-message style="margin:0;">Are you sure?</p>',
      '    <p class="tsk-modal-warning">This action cannot be undone.</p>',
      '  </div>',
      '  <div class="tsk-modal-actions">',
      '    <button type="button" class="tsk-btn tsk-btn-ghost" data-modal-cancel>Cancel</button>',
      '    <button type="button" class="tsk-btn tsk-btn-danger" data-modal-confirm>Confirm</button>',
      '  </div>',
      '</div>'
    ].join("");
    document.body.appendChild(modal);
    return modal;
  }

  function initConfirmModal() {
    var modal = ensureConfirmModal();
    var titleEl = modal.querySelector("[data-modal-title]");
    var msgEl = modal.querySelector("[data-modal-message]");
    var confirmBtn = modal.querySelector("[data-modal-confirm]");
    var cancelBtn = modal.querySelector("[data-modal-cancel]");
    var pendingFormId = null;

    function open(title, message, formId, danger) {
      titleEl.textContent = title || "Confirm";
      msgEl.textContent = message || "Are you sure?";
      confirmBtn.textContent = danger ? "Delete" : "Confirm";
      confirmBtn.className = "tsk-btn " + (danger ? "tsk-btn-danger" : "tsk-btn-primary");
      pendingFormId = formId;
      modal.removeAttribute("hidden");
      document.body.style.overflow = "hidden";
      setTimeout(function () { cancelBtn.focus(); }, 20);
    }
    function close() {
      modal.setAttribute("hidden", "");
      document.body.style.overflow = "";
      pendingFormId = null;
    }

    document.addEventListener("click", function (e) {
      var btn = e.target.closest("[data-tsk-confirm]");
      if (!btn) return;
      e.preventDefault();
      open(
        btn.getAttribute("data-modal-title"),
        btn.getAttribute("data-modal-message"),
        btn.getAttribute("data-form-id"),
        btn.getAttribute("data-modal-danger") === "1"
      );
    });

    confirmBtn.addEventListener("click", function () {
      if (pendingFormId) {
        var f = document.getElementById(pendingFormId);
        if (f) f.submit();
      }
      close();
    });
    cancelBtn.addEventListener("click", close);
    modal.addEventListener("click", function (e) { if (e.target === modal) close(); });
    document.addEventListener("keydown", function (e) {
      if (!modal.hasAttribute("hidden") && e.key === "Escape") close();
    });
  }

  /* ============================================================
     CTRL/CMD + ENTER TO SUBMIT
     ============================================================ */
  function initSubmitShortcut() {
    document.addEventListener("keydown", function (e) {
      if (!(e.ctrlKey || e.metaKey) || e.key !== "Enter") return;
      var ta = e.target;
      if (!ta || ta.tagName !== "TEXTAREA") return;
      var form = ta.closest("form");
      if (!form) return;
      e.preventDefault();
      if (typeof form.requestSubmit === "function") form.requestSubmit();
      else form.submit();
    });
  }

  /* ============================================================
     SUBTASK FORMSET
     ============================================================ */

  function initSubtaskFormset() {
    var container = document.querySelector("[data-subtask-formset]");
    if (!container || container.__init) return;
    container.__init = true;

    var totalInput = container.querySelector("[name$='-TOTAL_FORMS']");
    var templateEl = container.querySelector("[data-empty-form]");
    var addBtn = document.querySelector("[data-add-subtask]");

    if (!addBtn) {
      console.warn("[tasks.subtask] add button missing");
      return;
    }
    if (!templateEl) {
      console.warn("[tasks.subtask] <template data-empty-form> missing");
      return;
    }
    if (!totalInput) {
      console.warn("[tasks.subtask] TOTAL_FORMS hidden input missing — formset not in context?");
      return;
    }

    addBtn.addEventListener("click", function (e) {
      e.preventDefault();
      var idx = parseInt(totalInput.value, 10) || 0;
      var html = templateEl.innerHTML.replace(/__prefix__/g, idx);
      var wrapper = document.createElement("div");
      wrapper.innerHTML = html;
      var row = wrapper.firstElementChild;
      if (!row) {
        console.warn("[tasks.subtask] empty-form template produced no row");
        return;
      }
      container.insertBefore(row, addBtn);
      totalInput.value = idx + 1;

      // Re-init pickers inside the new row (if any) and focus the first input.
      try { initAssigneePickers(); } catch (err) {}
      var firstInput = row.querySelector("input, textarea, select");
      if (firstInput) firstInput.focus();
    });

    container.addEventListener("click", function (e) {
      var removeBtn = e.target.closest("[data-remove-row]");
      if (!removeBtn) return;
      e.preventDefault();
      var row = removeBtn.closest(".tsk-formset-row");
      if (!row) return;
      row.style.transition = "opacity 0.15s ease, transform 0.15s ease";
      row.style.opacity = "0";
      row.style.transform = "translateX(-10px)";
      setTimeout(function () { row.remove(); }, 150);
    });
  }

    /* ============================================================
    PROGRESS BAR — animate fill from 0% to target on load
    ============================================================ */
    function initProgressBars() {
    document.querySelectorAll(".tsk-progress-fill:not([data-animated])").forEach(function (el) {
        el.setAttribute("data-animated", "1");
        var target = el.style.width || "0%";
        if (!target || target === "0%") return;
        // Start at 0, then animate to target on the next frame.
        el.style.width = "0%";
        requestAnimationFrame(function () {
        requestAnimationFrame(function () {
            el.style.width = target;
        });
        });
    });
    }

  /* ============================================================
     CAPACITY PREVIEW (no-op placeholder)
     ============================================================ */
  function initCapacityPreview() { /* reserved */ }

  /* ============================================================
     BULK SELECT
     ============================================================ */
  function initBulkSelect() {
    var container = document.querySelector("[data-bulk-scope]");
    if (!container || container.__init) return;
    container.__init = true;

    var bar = document.querySelector("[data-bulk-bar]");
    var countEl = bar ? bar.querySelector("[data-bulk-count]") : null;
    var input = bar ? bar.querySelector("[data-bulk-ids]") : null;

    function refresh() {
      var checked = container.querySelectorAll(".tsk-select-toggle.is-checked");
      var ids = [];
      checked.forEach(function (cb) { ids.push(cb.getAttribute("data-task-id")); });
      if (bar) {
        bar.classList.toggle("is-visible", ids.length > 0);
        if (countEl) countEl.textContent = ids.length + " selected";
        if (input) input.value = ids.join(",");
      }
    }

    container.addEventListener("click", function (e) {
      var toggle = e.target.closest(".tsk-select-toggle");
      if (!toggle) return;
      e.preventDefault();
      e.stopPropagation();
      toggle.classList.toggle("is-checked");
      refresh();
    });

    var clearBtn = document.querySelector("[data-bulk-clear]");
    if (clearBtn) {
      clearBtn.addEventListener("click", function (e) {
        e.preventDefault();
        container.querySelectorAll(".tsk-select-toggle").forEach(function (t) {
          t.classList.remove("is-checked");
        });
        refresh();
      });
    }
  }

  /* ============================================================
     KANBAN DRAG & DROP
     ============================================================ */
  function initKanbanDnD() {
    var board = document.querySelector("[data-kanban]");
    if (!board || board.__init) return;
    board.__init = true;

    var dragged = null;

    board.querySelectorAll(".tsk-kanban-card").forEach(function (card) {
      card.setAttribute("draggable", "true");
      card.addEventListener("dragstart", function (e) {
        dragged = card;
        card.classList.add("is-dragging");
        try { e.dataTransfer.setData("text/plain", card.getAttribute("data-task-id")); } catch (err) {}
        e.dataTransfer.effectAllowed = "move";
      });
      card.addEventListener("dragend", function () {
        card.classList.remove("is-dragging");
        dragged = null;
        board.querySelectorAll(".tsk-kanban-col").forEach(function (c) {
          c.classList.remove("is-drop-target");
        });
      });
    });

    board.querySelectorAll(".tsk-kanban-col").forEach(function (col) {
      col.addEventListener("dragover", function (e) {
        e.preventDefault();
        e.dataTransfer.dropEffect = "move";
        col.classList.add("is-drop-target");
      });
      col.addEventListener("dragleave", function (e) {
        if (!col.contains(e.relatedTarget)) col.classList.remove("is-drop-target");
      });
      col.addEventListener("drop", function (e) {
        e.preventDefault();
        col.classList.remove("is-drop-target");
        if (!dragged) return;
        var target = col.getAttribute("data-key");
        var taskId = dragged.getAttribute("data-task-id");
        if (!taskId || !target) return;

        var body = "target=" + encodeURIComponent(target);
        var csrf = (document.cookie.match(/csrftoken=([^;]+)/) || [])[1] || "";

        fetch("/tasks/" + taskId + "/change-state/", {
          method: "POST",
          credentials: "same-origin",
          headers: {
            "Content-Type": "application/x-www-form-urlencoded",
            "X-CSRFToken": decodeURIComponent(csrf),
            "X-Requested-With": "XMLHttpRequest",
          },
          body: body,
        })
          .then(function (r) { return r.json().then(function (j) { return { ok: r.ok, data: j }; }); })
          .then(function (res) {
            if (!res.ok || !res.data.ok) {
              if (window.NBTE && window.NBTE.Toast) {
                window.NBTE.Toast.error("Could not move task", res.data.error || "Try again.");
              }
              return;
            }
            var card = dragged;
            if (card) {
              card.classList.add("is-dropping");
              var cards = col.querySelector(".tsk-kanban-cards");
              if (cards) cards.appendChild(card);
              setTimeout(function () { card.classList.remove("is-dropping"); }, 400);
            }
            if (window.NBTE && window.NBTE.Toast) {
              window.NBTE.Toast.success("Moved", "Task state updated.");
            }
          })
          .catch(function () {
            if (window.NBTE && window.NBTE.Toast) {
              window.NBTE.Toast.error("Network error", "Could not move task.");
            }
          });
      });
    });
  }

  /* ============================================================
     SSE STREAMS
     ============================================================ */
  var sseHooksBound = false;
  function initSSE() {
    document.querySelectorAll("[data-sse-stream]").forEach(function (el) {
      if (el.__sse) return;
      var url = el.getAttribute("data-sse-stream");
      if (!url || typeof window.EventSource === "undefined") return;
      try {
        var src = new EventSource(url);
        el.__sse = src;
        src.addEventListener("activity", function (evt) {
          var detail = {};
          try { if (evt.data) detail = JSON.parse(evt.data); } catch (e) {}
          document.body.dispatchEvent(new CustomEvent("sse:activity", { detail: detail }));
        });
        src.addEventListener("error", function () {});
        window.addEventListener("beforeunload", function () { try { src.close(); } catch (e) {} });
      } catch (err) {
        console.error("[NBTE-Tasks] SSE init failed", url, err);
      }
    });

    if (!sseHooksBound) {
      sseHooksBound = true;
      document.body.addEventListener("htmx:afterSwap", function () { initSSE(); });
    }
  }

  /* ============================================================
     TIMELINE SCROLL
     ============================================================ */
  function initTimelineObservers() {
    document.body.addEventListener("htmx:afterSwap", function (evt) {
      var t = evt.detail && evt.detail.target;
      if (!t || !t.id) return;
      if (t.id !== "tsk-timeline-container") return;
      setTimeout(function () {
        t.scrollIntoView({ behavior: "smooth", block: "nearest" });
      }, 40);
    });
  }

  /* ============================================================
     ASSIGNEE PICKER
     ============================================================ */
  function pickerEscape(str) {
    return String(str == null ? "" : str).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function pickerInitials(name) {
    if (!name) return "?";
    var parts = String(name).trim().split(/\s+/);
    if (parts.length === 1) return parts[0].slice(0, 1).toUpperCase();
    return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
  }

  function initAssigneePickers() {
    document.querySelectorAll("[data-assignee-picker]").forEach(function (root) {
      if (root.__pickerInit) return;
      root.__pickerInit = true;

      var name         = root.getAttribute("data-name") || "assignees";
      var taskId       = root.getAttribute("data-task-id") || "";
      var categoryCode = root.getAttribute("data-category-code") || "";
      var initialIds   = (root.getAttribute("data-initial-ids") || "")
                           .split(",").map(function (s) { return s.trim(); })
                           .filter(function (s) { return /^\d+$/.test(s); });

      var trigger     = root.querySelector("[data-picker-trigger]");
      var label       = root.querySelector("[data-picker-label]");
      var panel       = root.querySelector("[data-picker-panel]");
      var searchInput = root.querySelector("[data-picker-search]");
      var list        = root.querySelector("[data-picker-list]");
      var stateEl     = root.querySelector("[data-picker-state]");
      var chipsWrap   = root.querySelector("[data-picker-chips]");
      var hiddenWrap  = root.querySelector("[data-picker-hidden-inputs]");
      var closeBtn    = root.querySelector("[data-picker-close]");
      var clearBtn    = root.querySelector("[data-picker-clear]");

      if (!trigger || !panel || !list || !chipsWrap || !hiddenWrap) {
        console.warn("[tasks.picker] picker markup incomplete", root);
        return;
      }

      var users    = [];
      var selected = {};
      var fetched  = false;

      function hiddenInputs() {
        hiddenWrap.innerHTML = "";
        Object.keys(selected).forEach(function (id) {
          var inp = document.createElement("input");
          inp.type = "hidden";
          inp.name = name;
          inp.value = id;
          hiddenWrap.appendChild(inp);
        });
      }

      function renderChips() {
        chipsWrap.innerHTML = "";
        Object.keys(selected).forEach(function (id) {
          var u = selected[id] || {};
          var chip = document.createElement("span");
          chip.className = "tsk-picker-chip";
          chip.innerHTML =
            '<span class="tsk-picker-chip-avatar">' + pickerEscape(u.initials || pickerInitials(u.name)) + '</span>' +
            '<span class="tsk-picker-chip-name">' + pickerEscape(u.name || "…") + '</span>' +
            (u.role ? '<span class="tsk-picker-chip-rank">' + pickerEscape(u.role) + '</span>' : '') +
            '<button type="button" class="tsk-picker-chip-x" data-remove-id="' + pickerEscape(id) + '" title="Remove">' +
              '<svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>' +
            '</button>';
          var xBtn = chip.querySelector("[data-remove-id]");
          if (xBtn) {
            xBtn.addEventListener("click", function (e) {
              e.preventDefault();
              e.stopPropagation();
              delete selected[id];
              hiddenInputs();
              renderChips();
              renderList();
              updateTriggerLabel();
            });
          }
          chipsWrap.appendChild(chip);
        });
      }

      function updateTriggerLabel() {
        if (!label) return;
        var n = Object.keys(selected).length;
        label.textContent = n ? (n + " selected") : "Add people…";
      }

      function renderList() {
        var q = (searchInput ? searchInput.value : "").toLowerCase().trim();
        list.innerHTML = "";
        if (!users.length) {
          if (stateEl) {
            stateEl.hidden = false;
            var loading = stateEl.querySelector(".tsk-picker-loading");
            if (loading) loading.textContent = "No eligible people for this task.";
          }
          return;
        }
        if (stateEl) stateEl.hidden = true;

        var visible = 0;
        users.forEach(function (u) {
          var hay = (u.name + " " + (u.email || "") + " " + (u.role || "") + " " + (u.department || "")).toLowerCase();
          var match = !q || hay.indexOf(q) !== -1;
          var id = String(u.id);
          var isSelected = !!selected[id];
          var disabled = u.at_capacity && !isSelected;

          var option = document.createElement("button");
          option.type = "button";
          option.className = "tsk-picker-option" + (disabled ? " is-disabled" : "");
          option.setAttribute("role", "option");
          option.setAttribute("aria-selected", isSelected ? "true" : "false");

          var capText, capClass;
          if (disabled) {
            capText = "Full (" + u.used + "/" + u.max + ")";
            capClass = "is-full";
          } else if (u.remaining <= 1) {
            capText = u.remaining + " slot" + (u.remaining === 1 ? "" : "s") + " left";
            capClass = "is-warn";
          } else {
            capText = u.used + "/" + u.max + " open";
            capClass = "";
          }

          option.innerHTML =
            '<span class="tsk-picker-check"></span>' +
            '<span class="tsk-picker-avatar">' + pickerEscape(u.initials || pickerInitials(u.name)) + '</span>' +
            '<span class="tsk-picker-body">' +
              '<span class="tsk-picker-name">' + pickerEscape(u.name) + '</span>' +
              '<span class="tsk-picker-sub">' +
                pickerEscape(u.role || "No role") +
                (u.department ? " · " + pickerEscape(u.department) : "") +
              '</span>' +
            '</span>' +
            '<span class="tsk-picker-meta">' +
              '<span class="tsk-picker-rank">rank ' + (u.rank || 0) + '</span>' +
              '<span class="tsk-picker-capacity ' + capClass + '">' + pickerEscape(capText) + '</span>' +
            '</span>';

          if (!match) option.classList.add("is-hidden");
          else visible++;

          if (!disabled) {
            option.addEventListener("click", function (e) {
              e.preventDefault();
              if (selected[id]) delete selected[id];
              else selected[id] = u;
              hiddenInputs();
              renderChips();
              renderList();
              updateTriggerLabel();
            });
          }
          list.appendChild(option);
        });

        if (visible === 0 && stateEl) {
          stateEl.hidden = false;
          var loading = stateEl.querySelector(".tsk-picker-loading");
          if (loading) loading.textContent = "No matches for “" + (searchInput ? searchInput.value : "") + "”.";
        }
      }

      function openPanel() {
        panel.hidden = false;
        trigger.setAttribute("aria-expanded", "true");
        if (searchInput) setTimeout(function () { searchInput.focus(); }, 30);
      }
      function closePanel() {
        panel.hidden = true;
        trigger.setAttribute("aria-expanded", "false");
      }

      function fetchUsers() {
        if (stateEl) {
          stateEl.hidden = false;
          var loading = stateEl.querySelector(".tsk-picker-loading");
          if (loading) loading.textContent = "Loading eligible people…";
        }
        list.innerHTML = "";

        var params = [];
        if (taskId) params.push("task=" + encodeURIComponent(taskId));
        if (categoryCode) params.push("category=" + encodeURIComponent(categoryCode));
        Object.keys(selected).forEach(function (id) {
          params.push("include=" + encodeURIComponent(id));
        });

        var url = "/tasks/api/eligible-assignees/?" + params.join("&");
        console.log("[tasks.picker] fetching", url);

        fetch(url, {
          credentials: "same-origin",
          headers: { "X-Requested-With": "XMLHttpRequest", "Accept": "application/json" },
        })
          .then(function (r) {
            if (!r.ok) throw new Error("HTTP " + r.status);
            return r.json();
          })
          .then(function (data) {
            users = data.results || [];
            fetched = true;
            Object.keys(selected).forEach(function (id) {
              var found = users.find(function (u) { return String(u.id) === id; });
              if (found) selected[id] = found;
            });
            renderList();
            renderChips();
            updateTriggerLabel();
          })
          .catch(function (err) {
            console.error("[tasks.picker] fetch failed", err);
            if (stateEl) {
              stateEl.hidden = false;
              var loading = stateEl.querySelector(".tsk-picker-loading");
              if (loading) loading.textContent = "Could not load eligible people (" + err.message + ").";
            }
          });
      }

      if (initialIds.length) {
        initialIds.forEach(function (id) {
          selected[id] = { id: id, name: "…", initials: "…", role: "", rank: "" };
        });
        hiddenInputs();
        renderChips();
        updateTriggerLabel();
      }

      trigger.addEventListener("click", function (e) {
        e.preventDefault();
        e.stopPropagation();
        if (panel.hidden) {
          if (!fetched) fetchUsers();
          openPanel();
        } else {
          closePanel();
        }
      });

      if (searchInput) searchInput.addEventListener("input", renderList);
      if (closeBtn) closeBtn.addEventListener("click", closePanel);
      if (clearBtn) {
        clearBtn.addEventListener("click", function () {
          selected = {};
          hiddenInputs();
          renderChips();
          renderList();
          updateTriggerLabel();
        });
      }
      document.addEventListener("click", function (e) {
        if (!root.contains(e.target)) closePanel();
      });
      document.addEventListener("keydown", function (e) {
        if (e.key === "Escape" && !panel.hidden) closePanel();
      });

      root.__refresh = function () { fetched = false; fetchUsers(); };
    });
  }

  document.addEventListener("change", function (e) {
    if (!e.target || !e.target.name || e.target.name !== "category") return;
    var val = e.target.value || "";
    document.querySelectorAll("[data-assignee-picker]").forEach(function (p) {
      p.setAttribute("data-category-code", val);
      if (typeof p.__refresh === "function") p.__refresh();
    });
  });

  /* ============================================================
     RESCAN AFTER HTMX SWAP
     ============================================================ */
  function rescanAfterSwap() {
    safeCall("timers",      initTimers);
    safeCall("autoresize",  initAutoresize);
    safeCall("subtask",     initSubtaskFormset);
    safeCall("bulk-select", initBulkSelect);
    safeCall("kanban-dnd",  initKanbanDnD);
    safeCall("sse",         initSSE);
    safeCall("pickers",     initAssigneePickers);
  }
  function initRescan() {
    document.body.addEventListener("htmx:afterSwap",   function () { rescanAfterSwap(); });
    document.body.addEventListener("htmx:afterSettle", function () { rescanAfterSwap(); });
  }

  /* ============================================================
     BOOT
     ============================================================ */
  function boot() {
    console.log("[tasks.js] booting");
    safeCall("timers",        initTimers);
    safeCall("autoresize",    initAutoresize);
    safeCall("confirm-modal", initConfirmModal);
    safeCall("submit-short",  initSubmitShortcut);
    safeCall("subtask",       initSubtaskFormset);
    safeCall("capacity",      initCapacityPreview);
    safeCall("bulk-select",   initBulkSelect);
    safeCall("kanban-dnd",    initKanbanDnD);
    safeCall("sse",           initSSE);
    safeCall("timeline",      initTimelineObservers);
    safeCall("pickers",       initAssigneePickers);
    safeCall("rescan",        initRescan);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }

  window.NBTETasks = {
    tickTimers:    tickTimers,
    initKanbanDnD: initKanbanDnD,
    initPickers:   initAssigneePickers,
    rescan:        rescanAfterSwap,
  };
})();