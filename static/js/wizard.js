/* ==========================================================================
   NBTE ICT — Import/Export wizard JS (v2)
   - Drag-and-drop drop zone with type validation and clear feedback
   - Column-help toggle
   - Cancel button clears the preview panel
   - Commit button shows a loading state (belt-and-suspenders on top of HTMX)
   ========================================================================== */

(function () {
  "use strict";

  function ready(fn) {
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", fn);
    else fn();
  }

  function toast(type, title, msg) {
    if (window.NBTE && window.NBTE.Toast) {
      window.NBTE.Toast[type](title, msg);
    } else {
      console.log("[" + type + "]", title, msg);
    }
  }

  /* ---------- Drop zone ---------- */
  function bindDropZones(root) {
    (root || document).querySelectorAll("[data-drop-zone]").forEach(function (zone) {
      if (zone.dataset.bound === "1") return;
      zone.dataset.bound = "1";

      var input = zone.querySelector("[data-file-input]");
      var label = zone.querySelector("[data-drop-label]");
      var originalLabel = label ? label.textContent : "";

      function acceptFile(f) {
        if (!f) return false;
        if (!/\.(xlsx|xlsm)$/i.test(f.name)) {
          toast("error", "Wrong file type", "Please upload an .xlsx file (Excel workbook).");
          return false;
        }
        if (f.size > 25 * 1024 * 1024) {
          toast("error", "File too large", "The file exceeds 25 MB. Please reduce it and try again.");
          return false;
        }
        return true;
      }

      ["dragenter", "dragover"].forEach(function (evt) {
        zone.addEventListener(evt, function (e) {
          e.preventDefault();
          e.stopPropagation();
          zone.classList.add("is-dragover");
        });
      });
      ["dragleave", "drop"].forEach(function (evt) {
        zone.addEventListener(evt, function (e) {
          e.preventDefault();
          e.stopPropagation();
          zone.classList.remove("is-dragover");
        });
      });

      zone.addEventListener("drop", function (e) {
        if (!input) return;
        var files = e.dataTransfer && e.dataTransfer.files;
        if (!files || !files.length) return;
        var f = files[0];
        if (!acceptFile(f)) return;
        try {
          var dt = new DataTransfer();
          dt.items.add(f);
          input.files = dt.files;
        } catch (err) { /* older browser: fall through */ }
        if (label) label.textContent = f.name;
        zone.classList.add("has-file");
        toast("info", "File ready", f.name + " — click Analyze file to continue.");
      });

      if (input) {
        input.addEventListener("change", function () {
          var f = input.files && input.files[0];
          if (f && !acceptFile(f)) {
            input.value = "";
            return;
          }
          if (label) label.textContent = f ? f.name : originalLabel;
          zone.classList.toggle("has-file", !!f);
        });
      }
    });
  }

  /* ---------- Column-help toggle ---------- */
  function bindColumnToggles(root) {
    (root || document).querySelectorAll("[data-toggle-columns]").forEach(function (btn) {
      if (btn.dataset.bound === "1") return;
      btn.dataset.bound = "1";
      btn.addEventListener("click", function () {
        var wizard = btn.closest(".wizard");
        if (!wizard) return;
        var panel = wizard.querySelector(".wizard-columns");
        if (!panel) return;
        var open = !panel.hasAttribute("hidden");
        if (open) panel.setAttribute("hidden", "");
        else panel.removeAttribute("hidden");
        btn.textContent = open ? "Show columns" : "Hide columns";
      });
    });
  }

  /* ---------- Cancel button ---------- */
  function bindCancel(root) {
    (root || document).querySelectorAll("[data-wizard-cancel]").forEach(function (btn) {
      if (btn.dataset.bound === "1") return;
      btn.dataset.bound = "1";
      btn.addEventListener("click", function () {
        var out = document.getElementById("wizard-result");
        if (out) out.innerHTML = "";
        // Reset the file input so the user can pick the same file again if they want
        var fi = document.querySelector("[data-file-input]");
        if (fi) { fi.value = ""; }
        var zone = document.querySelector("[data-drop-zone]");
        if (zone) zone.classList.remove("has-file");
        var lbl = document.querySelector("[data-drop-label]");
        if (lbl) lbl.textContent = "Drop your file here or click to browse";
      });
    });
  }

  /* ---------- Rebind after every HTMX swap ---------- */
  function rebind() {
    bindDropZones(document);
    bindColumnToggles(document);
    bindCancel(document);
  }

  ready(rebind);
  document.body.addEventListener("htmx:afterSwap", rebind);
  document.body.addEventListener("htmx:afterSettle", rebind);
})();