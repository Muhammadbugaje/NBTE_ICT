/* ==========================================================================
   Workflow Management — drag-drop chain reordering
   ==========================================================================
   Auto-enabled on any element matching:
     .wf-pipeline[data-reorder-url][data-chain-pk]

   Each step must be:
     <article class="wf-step" data-step-pk="123" draggable="true">

   On drop, POSTs JSON {"order": [pk, pk, pk, ...]} to data-reorder-url.

   Uses the shared NBTE namespace for toasts. Silent fallback if fetch
   or the URL is unavailable — the ▲▼ arrow buttons still work.
   ========================================================================== */
(function () {
  "use strict";

  var DRAG_CLASS = "is-dragging";
  var TARGET_CLASS = "is-drop-target";
  var BEFORE_CLASS = "is-drop-before";
  var AFTER_CLASS = "is-drop-after";

  function post(url, payload) {
    // Pull the CSRF token out of the DOM — safer than relying on cookie
    // since SameSite may hide it in some setups.
    var csrf =
      (document.querySelector('input[name="csrfmiddlewaretoken"]') || {}).value ||
      (window.NBTE && NBTE.getCookie && NBTE.getCookie("csrftoken")) ||
      "";

    return fetch(url, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-CSRFToken": csrf,
        "X-Requested-With": "XMLHttpRequest",
      },
      credentials: "same-origin",
      body: JSON.stringify(payload),
    }).then(function (r) {
      if (!r.ok) {
        return r.text().then(function (txt) {
          throw new Error("HTTP " + r.status + ": " + (txt || r.statusText));
        });
      }
      return r.json();
    });
  }

  function clearDropIndicators(root) {
    root.querySelectorAll("." + TARGET_CLASS + ", ." + BEFORE_CLASS + ", ." + AFTER_CLASS).forEach(function (el) {
      el.classList.remove(TARGET_CLASS, BEFORE_CLASS, AFTER_CLASS);
    });
  }

  function updatePositionNumbers(root) {
    var steps = root.querySelectorAll(".wf-step");
    Array.prototype.forEach.call(steps, function (step, idx) {
      var dot = step.querySelector(".wf-step-dot");
      if (dot) dot.textContent = String(idx + 1);
      // Also update the disabled state of the up/down arrows
      var upBtn = step.querySelector('form[action*="/move/"] input[value="up"] + button');
      var downBtn = step.querySelector('form[action*="/move/"] input[value="down"] + button');
      if (upBtn) upBtn.disabled = (idx === 0);
      if (downBtn) downBtn.disabled = (idx === steps.length - 1);
    });
  }

  function currentOrder(root) {
    return Array.prototype.map.call(
      root.querySelectorAll(".wf-step"),
      function (step) {
        return String(step.getAttribute("data-step-pk") || "");
      }
    ).filter(Boolean);
  }

  function persist(root, originalOrder) {
    var url = root.getAttribute("data-reorder-url");
    if (!url) return;

    var newOrder = currentOrder(root);
    if (newOrder.join(",") === originalOrder.join(",")) return; // nothing moved

    post(url, { order: newOrder })
      .then(function (data) {
        if (!data || data.ok !== true) {
          throw new Error((data && data.error) || "Server rejected the new order.");
        }
        if (window.NBTE && NBTE.Toast) {
          NBTE.Toast.success("Chain order saved", "The new step order is live for new requests.");
        }
      })
      .catch(function (err) {
        if (window.NBTE && NBTE.Toast) {
          NBTE.Toast.error("Could not save order", String(err.message || err));
        }
        // Best-effort revert — reload is the only fully safe option.
        // But we only revert if the user hasn't already started another drag.
        setTimeout(function () {
          if (!root.querySelector("." + DRAG_CLASS)) {
            window.location.reload();
          }
        }, 800);
      });
  }

  function makePipeline(root) {
    if (!root || root.__workflowInit) return;
    root.__workflowInit = true;

    // Only proceed if HTML5 drag-drop is supported
    if (!("draggable" in document.createElement("div"))) return;

    var draggedStep = null;
    var originalOrder = currentOrder(root);

    root.addEventListener("dragstart", function (e) {
      var step = e.target.closest(".wf-step");
      if (!step) return;
      // Don't start a drag from an interactive element
      if (e.target.closest("a, button, input, select, textarea, form")) {
        // Allow the drag handle to work though
        if (!e.target.closest(".wf-drag-handle")) {
          e.preventDefault();
          return;
        }
      }
      draggedStep = step;
      // Defer the class toggle so Firefox doesn't cancel the drag
      setTimeout(function () {
        step.classList.add(DRAG_CLASS);
      }, 0);
      try {
        e.dataTransfer.effectAllowed = "move";
        e.dataTransfer.setData("text/plain", step.getAttribute("data-step-pk") || "");
      } catch (err) { /* Safari quirks */ }
    });

    root.addEventListener("dragover", function (e) {
      if (!draggedStep) return;
      e.preventDefault();
      e.dataTransfer.dropEffect = "move";

      var target = e.target.closest(".wf-step");
      if (!target || target === draggedStep) {
        clearDropIndicators(root);
        return;
      }

      var rect = target.getBoundingClientRect();
      var isBefore = (e.clientY - rect.top) < (rect.height / 2);

      clearDropIndicators(root);
      target.classList.add(TARGET_CLASS);
      target.classList.add(isBefore ? BEFORE_CLASS : AFTER_CLASS);
    });

    root.addEventListener("drop", function (e) {
      if (!draggedStep) return;
      e.preventDefault();

      var target = e.target.closest(".wf-step");
      if (!target || target === draggedStep) {
        clearDropIndicators(root);
        return;
      }

      var rect = target.getBoundingClientRect();
      var isBefore = (e.clientY - rect.top) < (rect.height / 2);

      if (isBefore) {
        root.insertBefore(draggedStep, target);
      } else {
        // Insert after target — use nextElementSibling if it exists
        var next = target.nextElementSibling;
        if (next) {
          root.insertBefore(draggedStep, next);
        } else {
          root.appendChild(draggedStep);
        }
      }

      clearDropIndicators(root);
      updatePositionNumbers(root);
      persist(root, originalOrder);
      originalOrder = currentOrder(root);
    });

    root.addEventListener("dragend", function () {
      if (draggedStep) draggedStep.classList.remove(DRAG_CLASS);
      draggedStep = null;
      clearDropIndicators(root);
    });

    // Cancel a stray drag if the user drops outside the pipeline
    root.addEventListener("dragleave", function (e) {
      // Only clear if truly leaving the pipeline area
      if (!root.contains(e.relatedTarget)) {
        clearDropIndicators(root);
      }
    });

    // Prevent dragover from bubbling to the page and enabling browser DnD
    root.addEventListener("drop", function (e) {
      if (e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files.length) return;
      e.preventDefault();
    });
  }

  function boot() {
    document.querySelectorAll(".wf-pipeline[data-reorder-url]").forEach(makePipeline);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }

  // Re-init after HTMX swaps (harmless if the page has no HTMX)
  document.addEventListener("htmx:afterSwap", boot);

  // Expose for manual use / debugging
  window.NBTE = window.NBTE || {};
  window.NBTE.ManageWorkflow = { boot: boot };
})();