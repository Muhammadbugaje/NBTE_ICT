/* ==========================================================================
   Gallery bulk upload — drag-drop feedback, file list, form submission UX.
   ========================================================================== */
(function () {
  "use strict";

  var drop = document.querySelector("[data-dropzone]");
  if (!drop) return;
  var input   = drop.querySelector("[data-dropzone-input]");
  var fileList = document.querySelector("[data-file-list]");
  var fileItems = document.querySelector("[data-file-items]");
  var fileCount = document.querySelector("[data-file-count]");
  var fileClear = document.querySelector("[data-file-clear]");
  var submitBtn = document.querySelector("[data-upload-submit]");

  function humanSize(bytes) {
    if (!bytes) return "";
    var units = ["B", "KB", "MB", "GB"];
    var i = 0;
    while (bytes >= 1024 && i < units.length - 1) { bytes /= 1024; i++; }
    return bytes.toFixed(bytes < 10 ? 1 : 0) + " " + units[i];
  }

  function refresh() {
    var files = input.files;
    if (!files || !files.length) {
      fileList.hidden = true;
      fileItems.innerHTML = "";
      fileCount.textContent = "0 files selected";
      drop.classList.remove("has-files");
      return;
    }
    fileList.hidden = false;
    drop.classList.add("has-files");
    fileCount.textContent = files.length + " file" + (files.length === 1 ? "" : "s") + " selected";
    fileItems.innerHTML = "";
    Array.from(files).forEach(function (f) {
      var li = document.createElement("li");
      li.className = "gal-file-item";
      var name = document.createElement("span");
      name.className = "gal-file-name";
      name.textContent = f.name;
      var size = document.createElement("span");
      size.className = "gal-file-size";
      size.textContent = humanSize(f.size);
      li.appendChild(name);
      li.appendChild(size);
      fileItems.appendChild(li);
    });
  }

  input.addEventListener("change", refresh);

  ["dragenter", "dragover"].forEach(function (ev) {
    drop.addEventListener(ev, function (e) {
      e.preventDefault();
      drop.classList.add("is-dragover");
    });
  });
  ["dragleave", "drop"].forEach(function (ev) {
    drop.addEventListener(ev, function (e) {
      e.preventDefault();
      drop.classList.remove("is-dragover");
    });
  });
  drop.addEventListener("drop", function (e) {
    var files = e.dataTransfer && e.dataTransfer.files;
    if (files && files.length) {
      input.files = files;
      refresh();
    }
  });

  if (fileClear) {
    fileClear.addEventListener("click", function () {
      input.value = "";
      refresh();
    });
  }

  // Loading state on submit
  var form = document.getElementById("upload-form");
  form.addEventListener("submit", function () {
    if (submitBtn) {
      submitBtn.disabled = true;
      submitBtn.innerHTML =
        '<span class="spinner spinner-sm" style="border-top-color:#fff;border-color:rgba(255,255,255,0.35);"></span> Uploading…';
    }
  });
})();