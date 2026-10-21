/* Nominal roll pages (staff/nominal_roll/*): file dropzone + import confirm.
   No inline handlers; progressive enhancement over plain forms. */
(function () {
  "use strict";

  function initDropzone(wrap) {
    var zone = wrap.querySelector(".sc-dropzone");
    var input = wrap.querySelector("input[type=file]");
    var label = wrap.querySelector("[data-nbr-file-name]");
    if (!zone || !input) return;

    function show() {
      var f = input.files && input.files[0];
      if (!label) return;
      if (f) {
        var kb = Math.max(1, Math.round(f.size / 1024));
        label.textContent = f.name + " · " + (kb > 1024 ? (kb / 1024).toFixed(1) + " MB" : kb + " KB");
        label.hidden = false;
        zone.classList.add("has-file");
      } else {
        label.hidden = true;
        zone.classList.remove("has-file");
      }
    }
    input.addEventListener("change", show);
    ["dragenter", "dragover"].forEach(function (t) {
      zone.addEventListener(t, function (e) { e.preventDefault(); zone.classList.add("is-dragging"); });
    });
    ["dragleave", "dragend", "drop"].forEach(function (t) {
      zone.addEventListener(t, function () { zone.classList.remove("is-dragging"); });
    });
    zone.addEventListener("drop", function (e) {
      e.preventDefault();
      var files = e.dataTransfer && e.dataTransfer.files;
      if (!files || !files.length) return;
      if (!/\.xls[xm]$/i.test(files[0].name)) {
        if (window.NBTE && NBTE.toast) NBTE.toast("Only .xlsx workbooks can be read.", "warning");
        return;
      }
      try { input.files = files; } catch (err) { return; }
      show();
    });
  }

  function initUploadForm(form) {
    form.addEventListener("submit", function () {
      var btn = form.querySelector("[data-nbr-upload-submit]");
      if (btn) btn.classList.add("is-busy");
      if (window.NBTE && NBTE.toast) NBTE.toast("Reading the workbook…", "info", { duration: 4000 });
    });
  }

  function initPreview(form) {
    var confirmBtn = form.querySelector("[data-nbr-confirm]");
    var boxes = form.querySelectorAll("input[name=sheet]");
    function sync() {
      var any = Array.prototype.some.call(boxes, function (b) { return b.checked; });
      if (confirmBtn && !confirmBtn.hasAttribute("data-locked")) confirmBtn.disabled = !any;
    }
    if (confirmBtn && confirmBtn.disabled) confirmBtn.setAttribute("data-locked", "");
    Array.prototype.forEach.call(boxes, function (b) {
      b.addEventListener("change", function () {
        // Options changed: the preview no longer matches — ask for a fresh dry run.
        if (confirmBtn) { confirmBtn.disabled = true; confirmBtn.setAttribute("data-locked", ""); }
        var note = form.querySelector("[data-nbr-stale]");
        if (note) note.hidden = false;
      });
    });
    Array.prototype.forEach.call(form.querySelectorAll("input[name=mode]"), function (r) {
      r.addEventListener("change", function () {
        if (confirmBtn) { confirmBtn.disabled = true; confirmBtn.setAttribute("data-locked", ""); }
        var note = form.querySelector("[data-nbr-stale]");
        if (note) note.hidden = false;
      });
    });
    // Sign-in accounts: show the password choices only when ticked; any
    // change means the preview count is stale.
    var acc = form.querySelector("[data-nbr-accounts]");
    var accOpts = form.querySelector("[data-nbr-accounts-options]");
    function markStale() {
      if (confirmBtn) { confirmBtn.disabled = true; confirmBtn.setAttribute("data-locked", ""); }
      var note = form.querySelector("[data-nbr-stale]");
      if (note) note.hidden = false;
    }
    if (acc) {
      acc.addEventListener("change", function () {
        if (accOpts) accOpts.hidden = !acc.checked;
        markStale();
      });
      Array.prototype.forEach.call(form.querySelectorAll("input[name=password_scheme], input[name=starter_password]"),
        function (el) { el.addEventListener("change", markStale); });
    }
    sync();
    form.addEventListener("submit", function (e) {
      var submitter = e.submitter;
      if (submitter && submitter.hasAttribute("data-nbr-confirm")) {
        submitter.classList.add("is-busy");
        if (window.NBTE && NBTE.toast) {
          NBTE.toast("Importing — this takes a few seconds. Please keep this page open.", "info", { duration: 8000 });
        }
      } else if (submitter && !submitter.name) {
        submitter.classList.add("is-busy");
      }
    });
  }

  function boot() {
    Array.prototype.forEach.call(document.querySelectorAll("[data-nbr-dropzone]"), initDropzone);
    Array.prototype.forEach.call(document.querySelectorAll("[data-nbr-upload]"), initUploadForm);
    Array.prototype.forEach.call(document.querySelectorAll("[data-nbr-preview]"), initPreview);
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();
})();
