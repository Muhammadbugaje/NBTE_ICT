/* ==========================================================================
   NBTE ICT — Attendance module JS
   Handles the GPS check-in flow on the check-in page and small UX touches.
   Pure vanilla — no framework. Only runs on pages that declare the
   [data-check-in-url] attribute.
   ========================================================================== */
(function () {
  "use strict";

  function safe(label, fn) {
    try { fn(); }
    catch (e) { console.error("[NBETT-Attendance] " + label + " failed", e); }
  }

  /* ============================================================
     CHECK-IN FLOW
     ============================================================ */
  function initCheckIn() {
    var page = document.querySelector("[data-check-in-url]");
    if (!page) return;
    if (page.__init) return;
    page.__init = true;

    var checkInUrl = page.getAttribute("data-check-in-url");
    var previewUrl = page.getAttribute("data-preview-url");
    var homeUrl = page.getAttribute("data-home-url");

    var iconEl = document.getElementById("att-checkin-icon");
    var titleEl = document.getElementById("att-checkin-title");
    var msgEl = document.getElementById("att-checkin-message");
    var ringWrap = document.getElementById("att-distance-ring");
    var ringProgress = document.getElementById("att-ring-progress");
    var distNumber = document.getElementById("att-distance-number");
    var nearestLocEl = document.getElementById("att-nearest-location");
    var effectiveRadiusEl = document.getElementById("att-effective-radius");
    var detailsEl = document.getElementById("att-checkin-details");
    var submitBtn = document.getElementById("att-submit");
    var retryBtn = document.getElementById("att-retry");
    var hintEl = document.getElementById("att-hint");

    var currentPosition = null;
    var inRange = false;
    var CIRCUMFERENCE = 2 * Math.PI * 54;

    function setIcon(state) {
      if (!iconEl) return;
      iconEl.classList.remove("is-locating", "is-in-range", "is-out-of-range", "is-error");
      if (state) iconEl.classList.add(state);
    }

    function updateRing(distance, radius) {
      if (!ringProgress || !distNumber) return;
      var pct = Math.max(0, Math.min(1, distance / Math.max(1, radius)));
      var offset = CIRCUMFERENCE * pct;
      ringProgress.style.strokeDasharray = String(CIRCUMFERENCE);
      ringProgress.style.strokeDashoffset = String(offset);
      // Colour the ring based on where we are
      var colour = pct <= 1 ? "#10b981" : "#ef4444";
      ringProgress.setAttribute("stroke", colour);
      distNumber.textContent = Math.round(distance);
    }

    function locate() {
      setIcon("is-locating");
      if (titleEl) titleEl.textContent = "Locating you…";
      if (msgEl) msgEl.textContent = "We're asking your device for its current position.";
      if (ringWrap) ringWrap.hidden = true;
      if (detailsEl) detailsEl.hidden = true;
      if (submitBtn) submitBtn.disabled = true;
      if (retryBtn) retryBtn.hidden = true;
      if (hintEl) hintEl.textContent = "";

      if (window.isSecureContext === false) {
        onError({
          code: -2,
          message: "Location access requires HTTPS or localhost. Open the secure site, then allow location access in your browser and device settings."
        });
        return;
      }

      if (!navigator.geolocation) {
        onError({
          code: -1,
          message: "This browser can't access location here. Use HTTPS or localhost and check your browser's site permissions."
        });
        return;
      }

      try {
        navigator.geolocation.getCurrentPosition(
          onSuccess,
          onError,
          { enableHighAccuracy: true, timeout: 30000, maximumAge: 5000 }
        );
      } catch (error) {
        console.warn("[attendance] geolocation request failed", error);
        onError({
          message: "Location access is blocked. Use HTTPS or localhost, then allow this site in your browser and device settings."
        });
      }
    }

    function onSuccess(pos) {
      currentPosition = { lat: pos.coords.latitude, lng: pos.coords.longitude };
      if (hintEl) hintEl.textContent = "Accuracy: ~" + Math.round(pos.coords.accuracy) + "m";

      // Ask the server what the nearest location is
      var url = previewUrl + "?lat=" + encodeURIComponent(currentPosition.lat)
                            + "&lng=" + encodeURIComponent(currentPosition.lng);

      fetch(url, { credentials: "same-origin", headers: { "Accept": "application/json" } })
        .then(function (r) {
          return r.json().catch(function () {
            throw new Error(r.ok ? "Could not verify your location." : "The attendance service returned an invalid response.");
          }).then(function (data) {
            if (!r.ok) throw new Error(data.error || data.message || "Could not verify your location.");
            return data;
          });
        })
        .then(function (data) {
          if (!data.ok) throw new Error(data.error || "Preview failed.");
          if (ringWrap) ringWrap.hidden = false;
          if (detailsEl) detailsEl.hidden = false;

          if (nearestLocEl) nearestLocEl.textContent = data.location || "—";
          if (effectiveRadiusEl) effectiveRadiusEl.textContent =
            data.effective_radius_meters != null ? data.effective_radius_meters + "m" : "—";

          if (data.in_range) {
            inRange = true;
            setIcon("is-in-range");
            if (titleEl) titleEl.textContent = "You're at the office";
            if (msgEl) msgEl.textContent = "Tap the button to check in for today.";
            if (submitBtn) submitBtn.disabled = false;
            if (ringProgress) updateRing(data.distance_meters, data.effective_radius_meters);
          } else {
            inRange = false;
            setIcon("is-out-of-range");
            if (titleEl) titleEl.textContent = "You're outside the office";
            if (msgEl) {
              if (data.message) msgEl.textContent = data.message;
              else msgEl.textContent = "Move closer to the office and try again.";
            }
            if (data.distance_meters != null && data.effective_radius_meters != null) {
              if (ringProgress) updateRing(data.distance_meters, data.effective_radius_meters);
            } else if (ringWrap) {
              ringWrap.hidden = true;
            }
            if (submitBtn) submitBtn.disabled = true;
            if (retryBtn) retryBtn.hidden = false;
          }
        })
        .catch(function (err) {
          console.error("[attendance] preview failed", err);
          onError({ message: err.message || "Could not verify your location." });
        });
    }

    function onError(err) {
      setIcon("is-error");
      var msg = "We couldn't get your location.";
      if (err && err.code === 1) {
        msg = "You denied location access. Enable it in your browser settings and try again. On macOS, also allow your browser in System Settings → Privacy & Security → Location Services.";
      }
      else if (err && err.code === 2) {
        msg = "Your device can't determine your position right now. Check that Location Services are enabled for this browser and your device.";
      }
      else if (err && err.code === 3) msg = "Locating you took too long. Check that Location Services are enabled for this browser and try again.";
      else if (err && err.message) msg = err.message;
      if (titleEl) titleEl.textContent = "Location unavailable";
      if (msgEl) msgEl.textContent = msg;
      if (ringWrap) ringWrap.hidden = true;
      if (detailsEl) detailsEl.hidden = true;
      if (submitBtn) submitBtn.disabled = true;
      if (retryBtn) retryBtn.hidden = false;
    }

    function submit() {
      if (!currentPosition || !inRange) return;
      if (submitBtn) {
        submitBtn.disabled = true;
        submitBtn.textContent = "Checking in…";
      }

      fetch(checkInUrl, {
        method: "POST",
        credentials: "same-origin",
        headers: {
          "Content-Type": "application/json",
          "X-CSRFToken": getCsrf(),
        },
        body: JSON.stringify(currentPosition),
      })
        .then(function (r) { return r.json().then(function (j) { return { ok: r.ok, data: j }; }); })
        .then(function (res) {
          if (res.ok && res.data.ok) {
            setIcon("is-in-range");
            if (titleEl) titleEl.textContent = "Checked in";
            if (msgEl) msgEl.textContent = res.data.message || "You're logged for today.";
            if (submitBtn) submitBtn.style.display = "none";
            if (retryBtn) retryBtn.hidden = true;
            window.setTimeout(function () { window.location.href = homeUrl; }, 1200);
          } else {
            setIcon("is-error");
            if (titleEl) titleEl.textContent = "Couldn't check you in";
            if (msgEl) msgEl.textContent =
              (res.data && (res.data.error || res.data.message)) || "Try again.";
            if (submitBtn) {
              submitBtn.disabled = false;
              submitBtn.textContent = "Check in";
            }
          }
        })
        .catch(function () {
          setIcon("is-error");
          if (titleEl) titleEl.textContent = "Network error";
          if (msgEl) msgEl.textContent = "We couldn't reach the server. Try again.";
          if (submitBtn) {
            submitBtn.disabled = false;
            submitBtn.textContent = "Check in";
          }
        });
    }

    if (submitBtn) submitBtn.addEventListener("click", submit);
    if (retryBtn) retryBtn.addEventListener("click", locate);

    locate();
  }

  function getCsrf() {
    var cookies = document.cookie ? document.cookie.split(";") : [];
    for (var i = 0; i < cookies.length; i++) {
      var c = cookies[i].trim();
      if (c.indexOf("csrftoken=") === 0) return decodeURIComponent(c.substring(10));
    }
    return "";
  }

  /* ============================================================
     UPLOAD DROP ZONE
     ============================================================ */
  function initUploadDrop() {
    var drop = document.querySelector("[data-upload-drop]");
    if (!drop || drop.__init) return;
    drop.__init = true;

    var input = drop.querySelector("[data-upload-input]") || drop.querySelector("input[type=file]");
    if (!input) return;

    var nameEl = drop.querySelector("[data-file-name]");

    function updateName() {
      if (!nameEl) return;
      if (input.files && input.files.length) {
        nameEl.textContent = input.files[0].name;
        drop.classList.add("has-file");
      } else {
        nameEl.textContent = "";
        drop.classList.remove("has-file");
      }
    }

    input.addEventListener("change", updateName);

    drop.addEventListener("click", function (e) {
      if (e.target.tagName === "INPUT") return;
      e.preventDefault();
      input.click();
    });

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
        updateName();
      }
    });

    updateName();
  }
  /* ============================================================
     BOOT
     ============================================================ */
  function boot() {
    safe("check-in", initCheckIn);
    safe("upload-drop", initUploadDrop);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();