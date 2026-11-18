/* v2.3 Phase 1 (D8) — connection banner: tells the truth, then hides itself.
 *
 * States
 *   hidden    nothing to say (default)
 *   offline   the browser says it is offline AND a probe of /healthz fails
 *   slow      the browser is online but our requests are not being answered
 *             (reported by qa-live.js and the shared poller via NBTE.net.report)
 *   restored  "Back online" in green, hidden automatically after 3 seconds
 *
 * Forms marked data-needs-network (payments, uploads) still refuse to submit
 * while offline, with a clear message, so nobody is left on a spinner.
 * Works with and without HTMX. Other scripts report through NBTE.net.report().
 */
(function () {
  "use strict";
  if (window.NBTE && window.NBTE.net) return; // loaded twice (shell + page): run once
  var RESTORED_MS = 3000;
  var PROBE_URL = "/healthz";
  var banner, state = "hidden", hideTimer = null, probing = false;

  var CSS = '.net-banner{position:fixed;left:50%;bottom:calc(16px + env(safe-area-inset-bottom,0px));transform:translateX(-50%);z-index:9999;display:flex;gap:.6rem;align-items:center;max-width:min(560px,calc(100vw - 32px));padding:.65rem 1rem;border-radius:999px;font-size:.9rem;box-shadow:0 10px 30px rgba(0,0,0,.18);transition:opacity .2s ease}' +
    '.net-banner[hidden]{display:none}' +
    '.net-banner[data-state=offline]{background:#1f2a23;color:#fff}' +
    '.net-banner[data-state=slow]{background:var(--surface,#fff);color:var(--text,#1d2a24);border:1px solid var(--border,#e2e5e0)}' +
    '.net-banner[data-state=restored]{background:#e9f6ee;color:#145a33}' +
    '.net-banner__dot{width:9px;height:9px;border-radius:50%;flex:none}' +
    '.net-banner[data-state=offline] .net-banner__dot{background:#f5a524;animation:net-pulse 1.4s ease-in-out infinite}' +
    '.net-banner[data-state=slow] .net-banner__dot{background:#94a3b8}' +
    '.net-banner[data-state=restored] .net-banner__dot{background:#1F6B3E}' +
    '.net-banner__retry{margin-left:.25rem;border:0;background:transparent;color:inherit;font:inherit;font-weight:600;text-decoration:underline;cursor:pointer;padding:.25rem}' +
    '@keyframes net-pulse{50%{opacity:.35}}' +
    '@media (prefers-reduced-motion:reduce){.net-banner__dot{animation:none!important}.net-banner{transition:none}}' +
    /* keep clear of the phone tab bar / sticky action bar */
    '@media (max-width:767px){.net-banner{bottom:calc(84px + env(safe-area-inset-bottom,0px))}}';

  var TEXT = {
    offline: "<strong>You’re offline.</strong> Your changes are kept on this page until the connection is back.",
    slow: "The server is slow to answer. Still trying…",
    restored: "<strong>Back online.</strong>"
  };

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
    (document.body || document.documentElement).appendChild(banner);
    banner.addEventListener("click", function (e) {
      if (e.target && e.target.classList && e.target.classList.contains("net-banner__retry")) probe(true);
    });
    return banner;
  }

  function show(next) {
    var b = ensureBanner();
    if (hideTimer) { window.clearTimeout(hideTimer); hideTimer = null; }
    state = next;
    document.documentElement.classList.toggle("is-offline", next === "offline");
    if (next === "hidden") { b.hidden = true; b.removeAttribute("data-state"); return; }
    b.setAttribute("data-state", next);
    b.innerHTML = '<span class="net-banner__dot" aria-hidden="true"></span><span>' + TEXT[next] + '</span>' +
      (next === "slow" ? '<button type="button" class="net-banner__retry">Retry now</button>' : "");
    b.hidden = false;
    if (next === "restored") hideTimer = window.setTimeout(function () { show("hidden"); }, RESTORED_MS);
  }

  // Ask our own server whether it is reachable. /healthz never touches the DB.
  function probe(manual) {
    if (probing) return;
    probing = true;
    var ctrl = window.AbortController ? new AbortController() : null;
    var timer = window.setTimeout(function () { if (ctrl) ctrl.abort(); }, 6000);
    fetch(PROBE_URL + "?t=" + Date.now(), { cache: "no-store", credentials: "omit", signal: ctrl ? ctrl.signal : undefined })
      .then(function (r) { if (!r.ok) throw new Error(String(r.status)); report("ok"); })
      .catch(function () { report(navigator.onLine === false ? "offline" : "slow"); if (manual) say("error", "Still no answer", "We'll keep trying in the background."); })
      .finally(function () { window.clearTimeout(timer); probing = false; });
  }

  // Single entry point for every script: "ok" | "slow" | "offline".
  function report(kind) {
    if (kind === "ok") {
      if (state === "offline" || state === "slow") show("restored");
      return;
    }
    if (kind === "offline") { show("offline"); return; }
    // "slow" never hides a real outage, but replaces "offline" once the browser is back online.
    if (kind === "slow" && (state !== "offline" || navigator.onLine !== false)) show("slow");
  }

  function say(kind, title, body) {
    if (window.QA && typeof window.QA.toast === "function") { window.QA.toast(kind, title, body); return; }
    if (window.NBTE && NBTE.Toast && NBTE.Toast.push) { NBTE.Toast.push(kind === "error" ? "error" : "info", title, body); }
  }

  window.addEventListener("online", function () { probe(false); });
  window.addEventListener("offline", function () {
    // navigator.onLine can be wrong (captive portals, VPNs): confirm first.
    probe(false);
    if (navigator.onLine === false) show("offline");
  });

  document.addEventListener("submit", function (e) {
    var form = e.target;
    if (!form || !form.closest) return;
    if (state === "offline" && form.closest("[data-needs-network], form[data-needs-network]")) {
      e.preventDefault();
      e.stopImmediatePropagation();
      say("error", "You are offline", "Reconnect to the internet, then try again. Nothing was sent or charged.");
    }
  }, true);
  document.addEventListener("htmx:beforeRequest", function (e) {
    var elt = e.detail && e.detail.elt;
    if (state === "offline" && elt && elt.closest && elt.closest("[data-needs-network]")) {
      e.preventDefault();
      say("error", "You are offline", "Reconnect to the internet, then try again. Nothing was sent or charged.");
    }
  });
  // Any successful HTMX answer proves the connection is fine.
  document.addEventListener("htmx:afterRequest", function (e) {
    var xhr = e.detail && e.detail.xhr;
    if (xhr && xhr.status > 0 && xhr.status < 500 && (state === "offline" || state === "slow")) report("ok");
  });

  function start() {
    ensureBanner();
    if (navigator.onLine === false) { show("offline"); probe(false); }
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start); else start();

  window.NBTE = window.NBTE || {};
  window.NBTE.net = { report: report, probe: probe, state: function () { return state; } };
})();
