/* NBTE — attendance check-out (v2.1 Phase D, G1/G2).
   "Check out" tries GPS first; when you're not inside an office geofence it
   offers the current office code or a scan of the office QR (html5-qrcode). */
(function () {
  "use strict";

  function csrf() {
    var m = document.cookie.match(/csrftoken=([^;]+)/);
    if (m) return decodeURIComponent(m[1]);
    var b = document.body.getAttribute("hx-headers");
    try { return JSON.parse(b)["X-CSRFToken"]; } catch (e) { return ""; }
  }

  function boot(root) {
    var api = root.getAttribute("data-api");
    var msg = root.querySelector("[data-checkout-msg]");
    var alt = root.querySelector("[data-checkout-alt]");
    var gpsBtn = root.querySelector("[data-checkout-gps]");
    var scanner = null;

    function say(text, kind) {
      msg.hidden = false;
      msg.textContent = text;
      msg.className = "att-out-msg" + (kind ? " is-" + kind : "");
    }
    function showAlt() { alt.hidden = false; var i = alt.querySelector("input"); if (i) i.focus(); }

    function send(body) {
      return fetch(api, {
        method: "POST", credentials: "same-origin",
        headers: { "Content-Type": "application/json", "X-CSRFToken": csrf(), "Accept": "application/json" },
        body: JSON.stringify(body)
      }).then(function (r) { return r.json().then(function (d) { return d; }); })
        .then(function (d) {
          if (d.ok) {
            say(d.message, "ok");
            if (gpsBtn) gpsBtn.disabled = true;
            setTimeout(function () { window.location.reload(); }, 1200);
          } else {
            say(d.message || "Check-out was not recorded.", "error");
            if (d.error === "gps_outside") showAlt();
          }
          return d;
        })
        .catch(function () { say("No connection. Try again.", "error"); });
    }

    if (gpsBtn) gpsBtn.addEventListener("click", function () {
      if (!navigator.geolocation) { say("This device can't share its location. Use the office code or QR.", "error"); showAlt(); return; }
      say("Checking that you're at the office…");
      navigator.geolocation.getCurrentPosition(function (pos) {
        send({ method: "gps", lat: pos.coords.latitude, lng: pos.coords.longitude, accuracy: pos.coords.accuracy });
      }, function () {
        say("Location is off or was refused. Use the office code or QR instead.", "error");
        showAlt();
      }, { enableHighAccuracy: true, timeout: 15000, maximumAge: 0 });
    });

    var show = root.querySelector("[data-checkout-show]");
    if (show) show.addEventListener("click", showAlt);

    var form = root.querySelector("[data-checkout-code-form]");
    if (form) form.addEventListener("submit", function (e) {
      e.preventDefault();
      var code = (form.querySelector("input").value || "").trim();
      if (!code) { say("Enter the code shown on the office screen.", "error"); return; }
      send({ method: "code", code: code });
    });

    var scanBtn = root.querySelector("[data-checkout-scan]");
    if (scanBtn) scanBtn.addEventListener("click", function () {
      var box = document.getElementById("att-out-reader");
      if (typeof Html5Qrcode === "undefined" || !box) { say("The camera scanner could not load. Use the code instead.", "error"); return; }
      box.hidden = false;
      if (scanner) return;
      scanner = new Html5Qrcode("att-out-reader");
      scanner.start({ facingMode: "environment" }, { fps: 10, qrbox: 220 }, function (text) {
        scanner.stop().then(function () { scanner = null; box.hidden = true; });
        send({ method: "qr", token: text });
      }, function () {}).catch(function () { say("Camera not available. Use the office code instead.", "error"); });
    });
  }

  function init() { document.querySelectorAll("[data-checkout]").forEach(boot); }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init); else init();
})();
