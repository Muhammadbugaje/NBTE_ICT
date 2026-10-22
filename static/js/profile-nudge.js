/* =========================================================================
   Profile completeness nudges (Phase 8) — see accounts/profile_nudges.py

   Reads #profile-nudge-data (only rendered for staff below the threshold):
     - shows the banner unless hidden for this browser session;
     - when the user starts a new request (a link/form whose path starts with
       one of `gated`, or any element marked [data-requires-profile]) a dialog
       offers "Complete my profile" or "Continue anyway". Continuing is
       logged on the server (POST continue_url) and not asked again for that
       kind of request in this session. Esc / backdrop = stay on the page.
   ========================================================================= */
(function () {
  "use strict";
  if (window.NBTEProfileNudge) return;

  function data() {
    var el = document.getElementById("profile-nudge-data");
    if (!el) return null;
    try { return JSON.parse(el.textContent); } catch (e) { return null; }
  }
  function ss(fn) { try { return fn(window.sessionStorage); } catch (e) { return null; } }
  function csrf() {
    try {
      var h = JSON.parse(document.body.getAttribute("hx-headers") || "{}");
      if (h["X-CSRFToken"]) return h["X-CSRFToken"];
    } catch (e) {}
    var m = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
    return m ? decodeURIComponent(m[1]) : "";
  }

  /* ---- banner ---------------------------------------------------------- */
  function initBanner() {
    var banner = document.querySelector("[data-profile-banner]");
    if (!banner) return;
    if (ss(function (s) { return s.getItem("pn-banner-hidden"); }) === "1") return;
    banner.hidden = false;
    var btn = banner.querySelector("[data-profile-banner-dismiss]");
    if (btn) btn.addEventListener("click", function () {
      ss(function (s) { s.setItem("pn-banner-hidden", "1"); });
      banner.classList.add("is-leaving");
      window.setTimeout(function () { banner.hidden = true; }, 220);
    });
  }

  /* ---- interrupt -------------------------------------------------------- */
  var bypass = false;

  function matchGate(cfg, el) {
    var marked = el.closest("[data-requires-profile]");
    if (marked) return "marked:" + (marked.getAttribute("data-requires-profile") || "request");
    var url = el.getAttribute("href") || el.getAttribute("hx-get") || el.getAttribute("action") || "";
    if (!url || url.charAt(0) === "#") return null;
    var path;
    try { path = new URL(url, window.location.href).pathname; } catch (e) { return null; }
    for (var i = 0; i < (cfg.gated || []).length; i++) {
      if (path.indexOf(cfg.gated[i]) === 0) return cfg.gated[i];
    }
    return null;
  }

  function dialog(cfg) {
    return new Promise(function (resolve) {
      var root = document.createElement("div");
      root.className = "nbte-confirm pn-dialog";
      root.setAttribute("role", "dialog");
      root.setAttribute("aria-modal", "true");
      root.setAttribute("aria-labelledby", "pn-dialog-title");
      root.setAttribute("aria-describedby", "pn-dialog-body");
      var missing = (cfg.missing || []).slice(0, 5).map(function (m) {
        var li = document.createElement("li"); li.textContent = m; return li.outerHTML;
      }).join("");
      root.innerHTML =
        '<button type="button" class="nbte-confirm__backdrop" aria-label="Close"></button>' +
        '<div class="nbte-confirm__panel pn-dialog__panel" role="document" tabindex="-1">' +
        '  <div class="pn-dialog__head"><div class="pn-banner__ring pn-banner__ring--lg" style="--pct:' + Number(cfg.percent || 0) + '" aria-hidden="true"><span>' + Number(cfg.percent || 0) + '%</span></div>' +
        '  <h2 class="nbte-confirm__title" id="pn-dialog-title">Complete your profile first?</h2></div>' +
        '  <p class="nbte-confirm__body" id="pn-dialog-body">Your profile is ' + Number(cfg.percent || 0) + '% complete; ' + Number(cfg.threshold || 0) +
        '% is needed. A complete profile lets approvers and colleagues see who is asking and reach you.</p>' +
        (missing ? '  <ul class="pn-dialog__missing" aria-label="Still missing">' + missing + "</ul>" : "") +
        '  <div class="nbte-confirm__actions pn-dialog__actions">' +
        '    <button type="button" class="btn btn-ghost" data-pn="continue">Continue anyway</button>' +
        '    <button type="button" class="btn btn-primary" data-pn="complete">Complete my profile</button>' +
        "  </div></div>";
      var prevFocus = document.activeElement;
      var prevOverflow = document.body.style.overflow;
      function close(result) {
        document.removeEventListener("keydown", onKey, true);
        root.remove();
        document.body.style.overflow = prevOverflow;
        if (result === "stay" && prevFocus && prevFocus.focus) prevFocus.focus();
        resolve(result);
      }
      function onKey(e) {
        if (e.key === "Escape") { e.preventDefault(); close("stay"); return; }
        if (e.key !== "Tab") return;
        var f = root.querySelectorAll("button[data-pn]");
        var first = f[0], last = f[f.length - 1];
        if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
        else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
      }
      root.querySelector(".nbte-confirm__backdrop").addEventListener("click", function () { close("stay"); });
      root.querySelector('[data-pn="continue"]').addEventListener("click", function () { close("continue"); });
      root.querySelector('[data-pn="complete"]').addEventListener("click", function () { close("complete"); });
      document.addEventListener("keydown", onKey, true);
      document.body.style.overflow = "hidden";
      document.body.appendChild(root);
      root.querySelector('[data-pn="complete"]').focus();
    });
  }

  function logContinue(cfg, target) {
    var body = new FormData();
    body.append("target", target);
    body.append("csrfmiddlewaretoken", csrf());
    return fetch(cfg.continue_url, {
      method: "POST", body: body, credentials: "same-origin",
      headers: { "X-CSRFToken": csrf() }, keepalive: true
    }).catch(function () {});
  }

  function intercept(cfg, e, el, proceed) {
    if (bypass || !cfg.interrupt) return;
    var gate = matchGate(cfg, el);
    if (!gate) return;
    // Already on that request's page (e.g. its own form posting back): don't ask twice.
    if (gate.charAt(0) === "/" && window.location.pathname.indexOf(gate) === 0) return;
    if (ss(function (s) { return s.getItem("pn-continue:" + gate); }) === "1") return;
    e.preventDefault();
    e.stopImmediatePropagation();
    dialog(cfg).then(function (choice) {
      if (choice === "complete") {
        window.location.href = cfg.profile_url;
      } else if (choice === "continue") {
        ss(function (s) { s.setItem("pn-continue:" + gate, "1"); });
        var target = el.getAttribute("href") || el.getAttribute("hx-get") || el.getAttribute("action") || gate;
        logContinue(cfg, target).then(function () {
          bypass = true;
          try { proceed(); } finally { window.setTimeout(function () { bypass = false; }, 0); }
        });
      }
    });
  }

  function init() {
    initBanner();
    var cfg = data();
    if (!cfg) return;
    document.addEventListener("click", function (e) {
      if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
      var el = e.target.closest && e.target.closest("a[href], [hx-get], button[data-requires-profile], [data-requires-profile] a");
      if (!el || el.closest(".pn-dialog, .pn-banner, [data-uxb-bar]")) return;
      if (el.tagName === "BUTTON" && el.type === "submit" && el.form) return; // handled on submit
      intercept(cfg, e, el, function () { el.click(); });
    }, true);
    document.addEventListener("submit", function (e) {
      var form = e.target;
      if (!(form instanceof HTMLFormElement)) return;
      var submitter = e.submitter;
      intercept(cfg, e, form, function () {
        if (form.requestSubmit) { submitter && submitter.form === form ? form.requestSubmit(submitter) : form.requestSubmit(); }
        else form.submit();
      });
    }, true);
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();

  window.NBTEProfileNudge = { data: data };
})();
