/* ============================================================
   Profile page — file preview, dirty tracking, save bar
   ============================================================
   Strategy: never intercept the submit. Let the browser POST
   the form natively so Django handles validation and the file
   upload the same way on every browser. This file's only jobs:
     1. Show the chosen filename next to "Upload photo"
     2. Preview the photo in both avatars
     3. Show / hide the save bar based on dirty state
     4. Disable the save button on submit to prevent double-fire
   ============================================================ */
(function () {
  "use strict";

  // ---------------------------------------------------------
  //  Completeness ring — count the number up and draw the arc.
  //  The server already renders the real percentage into the markup
  //  (--pct on .side-ring-large and the number's text), so this is
  //  purely the animation. Runs independently of the profile form.
  // ---------------------------------------------------------
  function initRing() {
    var rings = document.querySelectorAll(".side-ring-large");
    Array.prototype.forEach.call(rings, function (ring) {
      var num  = ring.querySelector("[data-count-to]");
      var fill = ring.querySelector(".ring-fill");
      var target = num ? parseFloat(num.getAttribute("data-count-to")) : NaN;
      if (isNaN(target)) {
        target = parseFloat(ring.style.getPropertyValue("--pct"));
      }
      if (isNaN(target)) return;
      target = Math.max(0, Math.min(100, target));

      var CIRC = 276.46;
      var finalOffset = CIRC * (1 - target / 100);

      if (fill) {
        // Start empty, then let the CSS transition sweep to the real value.
        fill.style.strokeDasharray = String(CIRC);
        fill.style.strokeDashoffset = String(CIRC);
        window.requestAnimationFrame(function () {
          window.requestAnimationFrame(function () {
            fill.style.strokeDashoffset = String(finalOffset);
          });
        });
      }

      if (num) {
        var reduce = window.matchMedia &&
          window.matchMedia("(prefers-reduced-motion: reduce)").matches;
        if (reduce || target === 0) {
          num.textContent = String(Math.round(target));
          return;
        }
        var duration = 1200, start = null;
        function step(ts) {
          if (start === null) start = ts;
          var t = Math.min(1, (ts - start) / duration);
          var eased = 1 - Math.pow(1 - t, 3);
          num.textContent = String(Math.round(target * eased));
          if (t < 1) window.requestAnimationFrame(step);
          else num.textContent = String(Math.round(target));
        }
        window.requestAnimationFrame(step);
      }
    });
  }

  function init() {
    initRing();
    var form = document.getElementById("profile-form");
    if (!form) return;

    var fileInput   = form.querySelector("[data-photo-input]");
    var fileNameEl  = form.querySelector("[data-photo-name]");
    var removeBtn   = form.querySelector("[data-remove-photo]");
    var saveBar     = document.querySelector("[data-save-bar]");
    var saveText    = document.querySelector("[data-save-text]");
    var discardBtn  = document.querySelector("[data-discard-btn]");
    var saveBtn     = document.querySelector("[data-save-btn]");
    var saveLabel   = document.querySelector("[data-save-label]");

    var dirty = false;

    // ---------------------------------------------------------
    //  Dirty state → save bar visibility
    // ---------------------------------------------------------
    function setDirty(isDirty) {
      dirty = !!isDirty;
      if (!saveBar) return;
      if (dirty) {
        saveBar.setAttribute("aria-hidden", "false");
        saveBar.classList.add("is-visible");
        if (saveText) saveText.textContent = "Unsaved changes";
      } else {
        saveBar.setAttribute("aria-hidden", "true");
        saveBar.classList.remove("is-visible");
        if (saveText) saveText.textContent = "All changes saved";
      }
    }

    // ---------------------------------------------------------
    //  Avatar preview via FileReader
    // ---------------------------------------------------------
    function setAvatarImg(container, src) {
      if (!container) return;
      var img = container.querySelector("img");
      if (!img) {
        img = document.createElement("img");
        img.alt = "";
        // Remove the initials span if present
        var initials = container.querySelector("span");
        if (initials) initials.remove();
        container.appendChild(img);
      }
      img.src = src;
    }

    function previewAvatar(file) {
      if (!file) return;
      var reader = new FileReader();
      reader.onload = function (e) {
        setAvatarImg(document.querySelector("[data-avatar]"),       e.target.result);
        setAvatarImg(document.querySelector("[data-avatar-small]"), e.target.result);
      };
      reader.readAsDataURL(file);
    }

    // ---------------------------------------------------------
    //  File input
    // ---------------------------------------------------------
    if (fileInput) {
      fileInput.addEventListener("change", function () {
        var file = fileInput.files && fileInput.files[0];
        if (file) {
          if (fileNameEl) fileNameEl.textContent = file.name;
          if (removeBtn) removeBtn.hidden = false;
          previewAvatar(file);
          setDirty(true);
        } else {
          if (fileNameEl) fileNameEl.textContent = "No file chosen";
          if (removeBtn) removeBtn.hidden = true;
        }
      });
    }

    // ---------------------------------------------------------
    //  Remove-photo button — clears the selection
    //  (A real server-side remove would need its own endpoint;
    //  this just resets the file input so the user can pick again.)
    // ---------------------------------------------------------
    if (removeBtn) {
      removeBtn.addEventListener("click", function () {
        if (fileInput) fileInput.value = "";
        if (fileNameEl) fileNameEl.textContent = "No file chosen";
        removeBtn.hidden = true;
        setDirty(true);
      });
    }

    // ---------------------------------------------------------
    //  Watch other inputs for dirtiness
    // ---------------------------------------------------------
    var watchable = form.querySelectorAll(
      "input:not([type=file]):not([type=hidden]), textarea, select"
    );
    Array.prototype.forEach.call(watchable, function (el) {
      el.addEventListener("input",  function () { setDirty(true); });
      el.addEventListener("change", function () { setDirty(true); });
    });

    // ---------------------------------------------------------
    //  Discard
    // ---------------------------------------------------------
    if (discardBtn) {
      discardBtn.addEventListener("click", function () {
        form.reset();
        if (fileNameEl) fileNameEl.textContent = "No file chosen";
        if (removeBtn) removeBtn.hidden = true;
        // A reset does not restore a saved photo preview until reload,
        // but this is a rare path — a page reload is the user's escape hatch.
        setDirty(false);
      });
    }

    // ---------------------------------------------------------
    //  Character counter on duties textarea
    // ---------------------------------------------------------
    var counters = document.querySelectorAll("[data-counter]");
    Array.prototype.forEach.call(counters, function (el) {
      var label = document.querySelector('[data-counter-for="' + el.id + '"]');
      if (!label) return;
      var max = parseInt(el.getAttribute("maxlength"), 10) || 500;
      function update() {
        label.textContent = el.value.length + " / " + max;
      }
      el.addEventListener("input", update);
      update();
    });

    // ---------------------------------------------------------
    //  Submit — do NOT preventDefault.
    //  Let the browser POST the form normally. Django handles the
    //  file upload, validation, and re-render-on-error.
    //  We only disable the button so it can't be double-clicked.
    // ---------------------------------------------------------
    form.addEventListener("submit", function () {
      if (saveBtn) {
        saveBtn.disabled = true;
        if (saveLabel) saveLabel.textContent = "Saving…";
      }
      if (saveText) saveText.textContent = "Saving…";
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();