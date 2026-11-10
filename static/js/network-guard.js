/* v2.2 — network guard.
 * - Shows a calm banner while the browser is offline and hides it when the
 *   connection comes back.
 * - Forms marked data-needs-network (payments, uploads) refuse to submit
 *   while offline, with a clear message, so nobody is left on a spinner.
 * Works with and without HTMX. */
(function () {
  "use strict";
  var banner;
  var CSS = '.net-banner{position:fixed;left:50%;bottom:16px;transform:translateX(-50%);z-index:9999;display:flex;gap:.6rem;align-items:center;max-width:min(560px,calc(100vw - 32px));padding:.7rem 1rem;border-radius:12px;background:#1f2937;color:#fff;font-size:.9rem;box-shadow:0 10px 30px rgba(0,0,0,.25)}.net-banner[hidden]{display:none}.net-banner__dot{width:10px;height:10px;border-radius:50%;background:#f59e0b;flex:none;animation:net-pulse 1.4s ease-in-out infinite}@keyframes net-pulse{50%{opacity:.35}}@media (prefers-reduced-motion:reduce){.net-banner__dot{animation:none}}';
  function ensureBanner() {
    if (banner) return banner;
    var st = document.createElement("style");
    st.textContent = CSS;
    document.head.appendChild(st);
    banner = document.createElement("div");
    banner.className = "net-banner";
    banner.setAttribute("role", "status");
    banner.setAttribute("aria-live", "polite");
    banner.hidden = true;
    banner.innerHTML = '<span class="net-banner__dot" aria-hidden="true"></span>' +
      '<span><strong>You are offline.</strong> Changes and payments will not go through until your connection is back.</span>';
    document.body.appendChild(banner);
    return banner;
  }
  function say(kind, title, body) {
    if (window.QA && typeof window.QA.toast === "function") { window.QA.toast(kind, title, body); return; }
    if (window.NBTE && NBTE.Toast && NBTE.Toast.push) { NBTE.Toast.push(kind === "error" ? "error" : "info", title, body); }
  }
  function update() {
    var b = ensureBanner();
    var off = navigator.onLine === false;
    b.hidden = !off;
    document.documentElement.classList.toggle("is-offline", off);
  }
  window.addEventListener("online", function () { update(); say("success", "Back online", "You can continue."); });
  window.addEventListener("offline", update);
  document.addEventListener("submit", function (e) {
    var form = e.target;
    if (!form || !form.closest) return;
    if (navigator.onLine === false && form.closest("[data-needs-network], form[data-needs-network]")) {
      e.preventDefault();
      e.stopImmediatePropagation();
      say("error", "You are offline", "Reconnect to the internet, then try again. Nothing was sent or charged.");
    }
  }, true);
  document.addEventListener("htmx:beforeRequest", function (e) {
    var elt = e.detail && e.detail.elt;
    if (navigator.onLine === false && elt && elt.closest && elt.closest("[data-needs-network]")) {
      e.preventDefault();
      say("error", "You are offline", "Reconnect to the internet, then try again. Nothing was sent or charged.");
    }
  });
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", update); else update();
})();
