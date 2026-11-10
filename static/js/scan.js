/* v2.1 Phase F (X26) — Scan and verify.
   Camera: BarcodeDetector where the browser has it, else jsQR 1.4.0 (vendored,
   loaded only when needed). Upload: the image is decoded in the browser.
   Typed code: a normal form (HTMX). Every lookup is a POST to /scan/lookup/
   with {raw, via}; the server returns the result card. Safe to load twice. */
(function () {
  "use strict";
  if (window.__nbteScan) { window.__nbteScan.init(document); return; }

  var jsqrPromise = null;

  function loadJsQR(src) {
    if (window.jsQR) return Promise.resolve(window.jsQR);
    if (!jsqrPromise) {
      jsqrPromise = new Promise(function (resolve, reject) {
        var s = document.createElement("script");
        s.src = src; s.async = true;
        s.onload = function () { window.jsQR ? resolve(window.jsQR) : reject(new Error("jsQR missing")); };
        s.onerror = function () { reject(new Error("jsQR failed to load")); };
        document.head.appendChild(s);
      });
    }
    return jsqrPromise;
  }

  function detector() {
    try {
      if ("BarcodeDetector" in window) return new window.BarcodeDetector({ formats: ["qr_code"] });
    } catch (e) { /* unsupported format */ }
    return null;
  }

  function csrf(root) {
    var el = root.querySelector("input[name=csrfmiddlewaretoken]");
    return el ? el.value : "";
  }

  function setup(root) {
    if (root.__scanReady) return;
    root.__scanReady = true;
    var view = root.querySelector("[data-scan-view]");
    var video = root.querySelector("[data-scan-video]");
    var canvas = root.querySelector("[data-scan-canvas]");
    var toast = root.querySelector("[data-scan-toast]");
    var msg = root.querySelector("[data-scan-msg]");
    var result = document.getElementById("scan-result");
    var lookupUrl = root.getAttribute("data-lookup") || "/scan/lookup/";
    var jsqrSrc = root.getAttribute("data-jsqr-src");
    var stream = null, timer = null, busy = false, bd = detector();

    function say(text, tone) { if (msg) { msg.textContent = text || ""; msg.setAttribute("data-tone", tone || ""); } }

    function stop() {
      if (timer) { clearTimeout(timer); timer = null; }
      if (stream) { stream.getTracks().forEach(function (t) { t.stop(); }); stream = null; }
      video.removeAttribute("data-live");
      view.setAttribute("data-state", "idle");
    }

    function send(raw, via) {
      if (busy) return;
      busy = true;
      if (toast) toast.hidden = false;
      var body = new URLSearchParams();
      body.set("raw", raw); body.set("via", via);
      fetch(lookupUrl, { method: "POST", credentials: "same-origin",
        headers: { "X-CSRFToken": csrf(root), "HX-Request": "true", "Content-Type": "application/x-www-form-urlencoded" },
        body: body.toString() })
        .then(function (r) { return r.text(); })
        .then(function (html) {
          result.innerHTML = html;
          if (window.htmx) window.htmx.process(result);
          say("");
          if (window.matchMedia("(max-width: 980px)").matches) result.scrollIntoView({ behavior: "smooth", block: "start" });
        })
        .catch(function () { say("Could not reach NBTE. Check your connection and try again.", "error"); })
        .finally(function () { busy = false; setTimeout(function () { if (toast) toast.hidden = true; }, 900); });
    }

    function decodeFrom(source, w, h) {
      if (bd) {
        return bd.detect(source).then(function (codes) { return codes && codes.length ? codes[0].rawValue : null; });
      }
      return loadJsQR(jsqrSrc).then(function (jsQR) {
        var scale = Math.min(1, 1024 / Math.max(w, h));
        canvas.width = Math.max(1, Math.round(w * scale));
        canvas.height = Math.max(1, Math.round(h * scale));
        var ctx = canvas.getContext("2d", { willReadFrequently: true });
        ctx.drawImage(source, 0, 0, canvas.width, canvas.height);
        var img = ctx.getImageData(0, 0, canvas.width, canvas.height);
        var hit = jsQR(img.data, img.width, img.height, { inversionAttempts: "attemptBoth" });
        return hit ? hit.data : null;
      });
    }

    function tick() {
      if (!stream) return;
      if (video.readyState < 2) { timer = setTimeout(tick, 200); return; }
      decodeFrom(video, video.videoWidth, video.videoHeight).then(function (value) {
        if (value) { stop(); send(value, "qr_camera"); return; }
        timer = setTimeout(tick, 250);
      }).catch(function () { timer = setTimeout(tick, 500); });
    }

    function start() {
      if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
        say("This browser cannot use the camera here. Upload an image or enter the code instead.", "error");
        return;
      }
      say("Starting the camera…");
      navigator.mediaDevices.getUserMedia({ video: { facingMode: { ideal: "environment" } }, audio: false })
        .then(function (s) {
          stream = s;
          video.srcObject = s;
          video.setAttribute("data-live", "");
          view.setAttribute("data-state", "live");
          return video.play();
        })
        .then(function () { say("Hold the QR code inside the frame."); tick(); })
        .catch(function () {
          stop();
          say("The camera is blocked or not available. Upload an image or enter the code instead.", "error");
        });
    }

    root.querySelectorAll("[data-scan-start]").forEach(function (b) { b.addEventListener("click", start); });

    root.querySelectorAll("[data-scan-mode]").forEach(function (tab) {
      tab.addEventListener("click", function () {
        var mode = tab.getAttribute("data-scan-mode");
        root.querySelectorAll("[data-scan-mode]").forEach(function (t) {
          t.setAttribute("aria-selected", t === tab ? "true" : "false");
        });
        root.querySelectorAll("[data-scan-pane]").forEach(function (p) { p.hidden = p.getAttribute("data-scan-pane") !== mode; });
        if (mode !== "camera") stop();
        if (mode === "code") { var input = root.querySelector("#scan-code"); if (input) input.focus(); }
        say("");
      });
    });

    var file = root.querySelector("[data-scan-file]");
    if (file) {
      file.addEventListener("change", function () {
        var f = file.files && file.files[0];
        if (!f) return;
        say("Reading the image…");
        var url = URL.createObjectURL(f);
        var img = new Image();
        img.onload = function () {
          decodeFrom(img, img.naturalWidth, img.naturalHeight).then(function (value) {
            URL.revokeObjectURL(url);
            if (value) { send(value, "image_upload"); }
            else { say("No QR code found in that image. Try a sharper photo, or enter the code.", "error"); }
          }).catch(function () { URL.revokeObjectURL(url); say("That image could not be read.", "error"); });
        };
        img.onerror = function () { URL.revokeObjectURL(url); say("That file is not an image.", "error"); };
        img.src = url;
        file.value = "";
      });
    }

    document.addEventListener("visibilitychange", function () { if (document.hidden) stop(); });
    window.addEventListener("pagehide", stop);
    document.body.addEventListener("htmx:beforeSwap", function (e) {
      if (e.detail && e.detail.target && e.detail.target.contains && e.detail.target.contains(root)) stop();
    });
  }

  function init(scope) {
    (scope || document).querySelectorAll("[data-scan]").forEach(setup);
  }

  window.__nbteScan = { init: init };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", function () { init(document); });
  else init(document);
  document.addEventListener("htmx:load", function (e) { init(e.target || document); });
})();
