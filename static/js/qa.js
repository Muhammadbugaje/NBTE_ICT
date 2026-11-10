/* ==========================================================================
   NBTE QA — interaction contract runtime (Master Plan §20)

   Loaded on every QA page (internal Settings → QA / Pending confirmations,
   and the whole QA portal). Builds on what the codebase already has: HTMX,
   NBTE.Toast / NBTE.toast, NBTE.loadingOverlay, NBTEConfirm and the
   semantic tokens. Nothing here is an access control — the server decides.

     • Loading states    — busy + disabled submit buttons, skeleton shimmer on
                           the swapped region, a full-screen "Please wait"
                           overlay only once a request passes 400 ms.
     • Errors            — timeout from qa.ui.request_timeout_seconds, one
                           automatic retry for idempotent GETs, then an inline
                           "Something went wrong — Retry" that keeps the page
                           usable; specific messages for 403/404/409/429/session.
     • Accessibility     — every swap is announced in an aria-live region
                           (errors assertive); focus moves to the swapped
                           region's heading.
     • Optimistic UI     — [data-qa-optimistic] switches flip at once and roll
                           back with a toast if the server refuses.
     • Layout header     — X-NBTE-Layout: mobile|desktop from viewport width
                           and pointer type, for views that serve a separate
                           mobile partial.
   ========================================================================== */
(function () {
  "use strict";

  var NBTE = (window.NBTE = window.NBTE || {});
  var QA = (NBTE.QA = NBTE.QA || {});
  var OVERLAY_DELAY = 400;

  function onReady(fn) {
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", fn);
    else fn();
  }

  function timeoutMs() {
    var raw = document.body && document.body.getAttribute("data-qa-timeout");
    var n = parseInt(raw || "15000", 10);
    return isFinite(n) && n >= 3000 ? n : 15000;
  }

  /* ----------------------------------------------------------- live regions */
  function liveRegion(id, politeness) {
    var el = document.getElementById(id);
    if (!el) {
      el = document.createElement("div");
      el.id = id;
      el.className = "sr-only";
      el.setAttribute("aria-live", politeness);
      el.setAttribute("aria-atomic", "true");
      if (politeness === "assertive") el.setAttribute("role", "alert");
      document.body.appendChild(el);
    }
    return el;
  }

  QA.announce = function (message, assertive) {
    if (!message) return;
    var el = liveRegion(assertive ? "qa-live-assertive" : "qa-live", assertive ? "assertive" : "polite");
    el.textContent = "";
    window.setTimeout(function () { el.textContent = message; }, 40);
  };

  function toast(kind, title, body) {
    if (NBTE.Toast && typeof NBTE.Toast.push === "function") {
      // The toast stack is itself an aria-live region; don't announce twice.
      // components.css styles .toast-success / -error / -info / -warn.
      var map = { error: "error", danger: "error", warning: "warn", warn: "warn", success: "success" };
      NBTE.Toast.push(map[kind] || "info", title, body || "");
      return;
    }
    QA.announce(title + (body ? ". " + body : ""), kind === "error");
  }
  QA.toast = toast;

  /* ----------------------------------------------------------- busy state */
  var overlayTimer = null;
  var inflight = 0;

  function overlay(show) {
    if (typeof NBTE.loadingOverlay === "function") NBTE.loadingOverlay(show);
  }

  function busyButtons(elt) {
    if (!elt) return [];
    var buttons = [];
    if (elt.tagName === "FORM") {
      buttons = Array.prototype.slice.call(elt.querySelectorAll("button[type=submit], button:not([type])"));
    } else if (elt.tagName === "BUTTON") {
      buttons = [elt];
    }
    return buttons.filter(function (b) { return !b.hasAttribute("data-qa-no-busy"); });
  }

  function startBusy(evt) {
    var d = evt.detail || {};
    var elt = d.elt;
    inflight += 1;
    if (!overlayTimer && !(elt && elt.closest && elt.closest("[data-qa-no-overlay]"))) {
      overlayTimer = window.setTimeout(function () { overlay(true); }, OVERLAY_DELAY);
    }
    busyButtons(elt).forEach(function (b) {
      if (b.disabled) return;
      b.disabled = true;
      b.classList.add("is-busy");
      b.setAttribute("aria-busy", "true");
      b.setAttribute("data-qa-busy", "1");
    });
    var target = d.target;
    if (target && target.classList && !target.hasAttribute("data-qa-no-skeleton")) {
      target.classList.add("qa-is-loading");
      target.setAttribute("aria-busy", "true");
    }
  }

  function endBusy(evt) {
    var d = evt.detail || {};
    inflight = Math.max(0, inflight - 1);
    if (inflight === 0) {
      if (overlayTimer) { window.clearTimeout(overlayTimer); overlayTimer = null; }
      overlay(false);
    }
    var elt = d.elt;
    var scope = elt && elt.isConnected ? elt : document;
    Array.prototype.forEach.call(
      (scope.querySelectorAll ? scope.querySelectorAll("[data-qa-busy]") : []),
      function (b) { b.disabled = false; b.classList.remove("is-busy"); b.removeAttribute("aria-busy"); b.removeAttribute("data-qa-busy"); }
    );
    if (elt && elt.hasAttribute && elt.hasAttribute("data-qa-busy")) {
      elt.disabled = false; elt.classList.remove("is-busy"); elt.removeAttribute("aria-busy"); elt.removeAttribute("data-qa-busy");
    }
    var target = d.target;
    if (target && target.classList) {
      target.classList.remove("qa-is-loading");
      target.removeAttribute("aria-busy");
    }
  }

  /* ----------------------------------------------------------- errors */
  function describe(status, xhr) {
    if (status === 0) return ["You appear to be offline", "Check your connection, then retry."];
    if (status === 401) return ["Your session expired", "Sign in again to continue."];
    if (status === 403) return ["You can’t do that", "Your access does not include this action, or your session expired."];
    if (status === 404) return ["Not found", "This item no longer exists or is outside your scope."];
    if (status === 409) return ["This change was made by someone else", "Reload to see the latest version."];
    if (status === 429) return ["Too many attempts", "Please wait a moment and try again."];
    if (status >= 500) return ["Something went wrong", "The server could not finish this request."];
    return ["Something went wrong", ""];
  }

  function inlineError(target, title, body, retry) {
    if (!target || !target.isConnected) return;
    var host = target.closest("[data-qa-region]") || target;
    var existing = host.querySelector(":scope > .qa-inline-error");
    if (existing) existing.remove();
    var box = document.createElement("div");
    box.className = "qa-inline-error";
    box.setAttribute("role", "alert");
    var text = document.createElement("div");
    text.className = "qa-inline-error__text";
    var strong = document.createElement("strong");
    strong.textContent = title;
    text.appendChild(strong);
    if (body) {
      var span = document.createElement("span");
      span.textContent = body;
      text.appendChild(span);
    }
    box.appendChild(text);
    if (retry) {
      var btn = document.createElement("button");
      btn.type = "button";
      btn.className = "btn btn-sm btn-subtle";
      btn.textContent = "Retry";
      btn.addEventListener("click", function () { box.remove(); retry(); });
      box.appendChild(btn);
    }
    var dismiss = document.createElement("button");
    dismiss.type = "button";
    dismiss.className = "qa-inline-error__close";
    dismiss.setAttribute("aria-label", "Dismiss");
    dismiss.textContent = "×";
    dismiss.addEventListener("click", function () { box.remove(); });
    box.appendChild(dismiss);
    host.insertBefore(box, host.firstChild);
  }

  function retryFor(d) {
    var cfg = d.requestConfig || {};
    var verb = (cfg.verb || "get").toUpperCase();
    var path = cfg.path;
    var target = d.target;
    if (!path) return null;
    return function () {
      if (!window.htmx) return;
      window.htmx.ajax(verb, path, { source: d.elt, target: target, swap: (d.elt && d.elt.getAttribute("hx-swap")) || "innerHTML" });
    };
  }

  function handleFailure(evt, kind) {
    var d = evt.detail || {};
    var cfg = d.requestConfig || {};
    var verb = (cfg.verb || "get").toLowerCase();
    var elt = d.elt;
    // Optimistic controls roll themselves back.
    if (elt && elt.hasAttribute && elt.hasAttribute("data-qa-optimistic")) {
      rollback(elt);
      var info = describe(d.xhr ? d.xhr.status : 0, d.xhr);
      toast("error", "Not saved — " + info[0].toLowerCase(), info[1]);
      return;
    }
    // One automatic retry for idempotent GETs that timed out or never arrived.
    if ((kind === "timeout" || kind === "send") && verb === "get" && elt && !elt.__qaRetried) {
      elt.__qaRetried = true;
      var again = retryFor(d);
      if (again) { window.setTimeout(again, 300); return; }
    }
    if (elt) elt.__qaRetried = false;
    var status = kind === "timeout" ? 0 : (d.xhr ? d.xhr.status : 0);
    var desc = kind === "timeout" ? ["This is taking too long", "The server did not answer in time."] : describe(status, d.xhr);
    inlineError(d.target || elt, desc[0], desc[1], verb === "get" ? retryFor(d) : null);
    QA.announce(desc[0] + ". " + desc[1], true);
  }

  /* ----------------------------------------------------------- optimistic */
  function rollback(input) {
    if (input.type === "checkbox" && input.hasAttribute("data-qa-prev")) {
      input.checked = input.getAttribute("data-qa-prev") === "1";
      var label = input.closest(".qa-switch");
      if (label) label.classList.toggle("is-on", input.checked);
    }
  }

  function initOptimistic() {
    document.addEventListener("change", function (e) {
      var input = e.target;
      if (!(input instanceof HTMLInputElement) || !input.hasAttribute("data-qa-optimistic")) return;
      input.setAttribute("data-qa-prev", input.checked ? "0" : "1");
      var label = input.closest(".qa-switch");
      if (label) label.classList.toggle("is-on", input.checked);
    }, true);
  }

  /* ----------------------------------------------------------- focus + announce */
  function afterSwap(evt) {
    var target = evt.detail && evt.detail.target;
    // outerHTML swaps (row edits) replace the target; htmx 2 then fires
    // afterSwap on the new node, which is evt.target.
    if ((!target || !target.isConnected) && evt.target && evt.target.isConnected && evt.target !== document.body) {
      target = evt.target;
    }
    if (!target || !target.isConnected) return;
    var elt = evt.detail.elt;
    if (elt && elt.__qaRetried) elt.__qaRetried = false;
    var focusEl = target.querySelector("[data-qa-focus]") || (target.matches("[data-qa-focus-heading]") ? target.querySelector("h1, h2, h3") : null);
    if (!focusEl && target.id && /qa-(main|panel)/.test(target.id)) focusEl = target.querySelector("h1, h2");
    if (focusEl) {
      if (!focusEl.hasAttribute("tabindex")) focusEl.setAttribute("tabindex", "-1");
      try { focusEl.focus({ preventScroll: target.id !== "qa-main" }); } catch (e) { focusEl.focus(); }
    }
    var msg = target.querySelector("[data-qa-announce]");
    if (msg) QA.announce(msg.getAttribute("data-qa-announce") || msg.textContent.trim(), false);
    else if (focusEl) QA.announce(focusEl.textContent.trim(), false);
    if (typeof NBTE.responsiveTables === "function") { try { NBTE.responsiveTables(target); } catch (e) {} }
  }

  /* ----------------------------------------------------------- layout header */
  function layoutHint() {
    var narrow = window.matchMedia && window.matchMedia("(max-width: 767px)").matches;
    var coarse = window.matchMedia && window.matchMedia("(pointer: coarse)").matches;
    return narrow || (coarse && window.innerWidth < 1024) ? "mobile" : "desktop";
  }

  /* ----------------------------------------------------------- session expiry */
  function looksLikeLogin(xhr, requestedPath) {
    if (!xhr || !xhr.responseURL) return false;
    var url = xhr.responseURL;
    var isLogin = /\/(accounts\/)?login\/?(\?|$)/.test(url);
    return isLogin && !/login/.test(requestedPath || "");
  }

  /* ----------------------------------------------------------- wire up */
  function wireHtmx() {
    if (!window.htmx) { window.setTimeout(wireHtmx, 50); return; }
    var htmx = window.htmx;
    htmx.config.timeout = timeoutMs();
    htmx.config.defaultSettleDelay = 0;
    // History settings (refreshOnHistoryMiss, historyCacheSize) live in app.js.
    htmx.config.scrollBehavior = "instant";
    // §20: validation errors come back as 422 with the form partial — swap it
    // in place (field errors visible) instead of treating it as a failure.
    if (Array.isArray(htmx.config.responseHandling) &&
        !htmx.config.responseHandling.some(function (r) { return r.code === "422"; })) {
      htmx.config.responseHandling.unshift({ code: "422", swap: true, error: false });
    }

    var body = document.body;
    body.addEventListener("htmx:configRequest", function (evt) {
      evt.detail.headers["X-NBTE-Layout"] = layoutHint();
    });
    body.addEventListener("htmx:beforeRequest", startBusy);
    body.addEventListener("htmx:afterRequest", endBusy);
    body.addEventListener("htmx:beforeSwap", function (evt) {
      var d = evt.detail || {};
      if (looksLikeLogin(d.xhr, d.requestConfig && d.requestConfig.path)) {
        d.shouldSwap = false;
        inlineError(d.target, "Your session expired", "Sign in again to continue.", function () { window.location.reload(); });
        QA.announce("Your session expired. Sign in again to continue.", true);
      }
    });
    body.addEventListener("htmx:afterSwap", afterSwap);
    body.addEventListener("htmx:timeout", function (evt) { handleFailure(evt, "timeout"); });
    body.addEventListener("htmx:sendError", function (evt) { handleFailure(evt, "send"); });
    body.addEventListener("htmx:responseError", function (evt) { handleFailure(evt, "response"); });
    // Determinate progress for uploads/imports (> 1 s operations, §20).
    body.addEventListener("htmx:xhr:progress", function (evt) {
      var d = evt.detail || {};
      var form = d.elt && d.elt.closest && d.elt.closest("[data-qa-progress]");
      if (!form || !d.lengthComputable || !d.total) return;
      var bar = form.querySelector("[data-qa-progress-bar]");
      if (!bar) return;
      bar.hidden = false;
      var pct = Math.round((d.loaded / d.total) * 100);
      var fill = bar.querySelector("span");
      if (fill) fill.style.setProperty("--pct", pct + "%");
      bar.setAttribute("role", "progressbar");
      bar.setAttribute("aria-valuenow", String(pct));
    });
    body.addEventListener("qa:toast", function (evt) {
      var t = evt.detail || {};
      toast(t.kind || "info", t.title || "Done", t.body || "");
    });
  }

  /* ----------------------------------------------------------- small helpers */
  function initCopyButtons() {
    document.addEventListener("click", function (e) {
      var btn = e.target.closest && e.target.closest("[data-qa-copy]");
      if (!btn) return;
      var text = btn.getAttribute("data-qa-copy");
      if (navigator.clipboard) {
        navigator.clipboard.writeText(text).then(function () { toast("success", "Copied", text); });
      }
    });
  }

  /* ----------------------------------------------------------- decisions (NBTEConfirm) */
  // Forms that change state under the confirmation rules (PendingAction
  // confirm / reject) carry data-qa-confirm and hx-trigger="qa:confirmed".
  // The submit is stopped here (capture phase, before htmx sees it), the
  // shared NBTEConfirm dialog asks, and only a "yes" fires the request.
  // Never optimistic: the page waits for the server's answer (§20).
  function initDecisionForms() {
    document.addEventListener("submit", function (e) {
      var form = e.target;
      if (!(form instanceof HTMLFormElement) || !form.hasAttribute("data-qa-confirm")) return;
      e.preventDefault();
      e.stopImmediatePropagation();
      var need = form.getAttribute("data-qa-require");
      if (need) {
        var field = document.querySelector(need);
        if (field && field.value.trim().length < 3) {
          field.setAttribute("aria-invalid", "true");
          field.focus();
          toast("warning", form.getAttribute("data-qa-require-msg") || "A remark is required", "");
          return;
        }
        if (field) field.removeAttribute("aria-invalid");
      }
      var go = function () {
        if (window.htmx) window.htmx.trigger(form, "qa:confirmed");
        else form.submit();
      };
      if (!window.NBTEConfirm) { go(); return; }
      window.NBTEConfirm.open({
        title: form.getAttribute("data-qa-confirm-title") || "Are you sure?",
        body: form.getAttribute("data-qa-confirm"),
        confirmLabel: form.getAttribute("data-qa-confirm-label") || "Confirm",
        tone: form.getAttribute("data-qa-confirm-tone") || "default"
      }).then(function (ok) { if (ok && form.isConnected) go(); });
    }, true);
  }

  /* ----------------------------------------------------------- SOP-3 pass (optimistic) */
  // v2.6: stage-passing moves as fast as the click. On confirm (or submit for
  // assign/start), the panel shows the pass instantly and the POST runs in
  // the background; when the answer lands we follow it into the case AS IT
  // IS NOW with a partial swap (no full reload). If it fails, the panel
  // comes back with the server's own error — nothing is silently lost.
  function panel() {
    return document.getElementById("qa-panel");
  }

  function knockOutPassing() {
    Array.prototype.forEach.call(document.querySelectorAll(".c3-stage.is-passing, .c3-start.is-passing"),
      function (el) { el.classList.remove("is-passing"); el.classList.add("is-passed"); });
  }

  function swapPanel(html) {
    var target = panel();
    if (!target) { window.location.reload(); return; }
    var tpl = document.createElement("template");
    tpl.innerHTML = html;
    target.innerHTML = "";
    while (tpl.content.firstChild) target.appendChild(tpl.content.firstChild);
    afterSwap({ detail: { target: target }, target: target });
  }

  function loadPanelPath(url) {
    if (window.history && window.history.replaceState &&
        window.location.pathname + window.location.search !== url) {
      window.history.pushState({}, "", url);
    }
    if (window.htmx) {
      var target = panel();
      if (target) target.removeAttribute("aria-busy");
      Array.prototype.forEach.call(document.querySelectorAll(".c3-stage.is-passing, .c3-start.is-passing"),
        function (el) { el.classList.remove("is-passing"); });
      window.htmx.ajax("GET", url, { target: "#qa-panel", swap: "innerHTML" });
    } else {
      window.location.href = url;
    }
  }

  function passStage(form) {
    if (form.__qaPassing) return;
    form.__qaPassing = true;
    var section = form.closest(".c3-stage") || form.closest(".c3-start");
    if (section) section.classList.add("is-passing");
    var kind = form.getAttribute("data-qa-stage-pass") || "approve";
    var target = panel();
    var payload = new FormData(form);
    toast("info", kind === "approve" ? "Approving" : "Saving…", "Passing the stage — the page follows right away.");
    if (target) {
      target.setAttribute("aria-busy", "true");
      target.innerHTML = '<section class="c3-stage c3-stage--pending" role="status" aria-live="polite">' +
        '<div class="c3-stage__head"><div class="c3-stage__title"><span class="c3-stage__kicker">SOP-03 · Updating</span>' +
        '<h2>Moving to the next step…</h2><p>Your action is being saved. The next step will open automatically.</p></div>' +
        '</div></section>';
    }
    fetch(form.getAttribute("action"), {
      method: "POST", body: payload, credentials: "same-origin",
      headers: { "HX-Request": "true", "X-NBTE-Layout": layoutHint() }
    }).then(function (r) {
      form.__qaPassing = false;
      var redirect = r.headers.get("HX-Redirect");
      if (redirect) {
        toast("success", "Stage passed", "Opening the case at its next step…");
        loadPanelPath(redirect);
        return null;
      }
      var status = r.status;
      return r.text().then(function (html) {
        if (status >= 200 && status < 300 && html && html.length > 40) {
            if (panel()) panel().removeAttribute("aria-busy");
            knockOutPassing();
            swapPanel(html);
        } else {
          passFailed(section, status, html);
        }
      });
    }).catch(function (err) {
      form.__qaPassing = false;
      passFailed(section, 0, "");
      if (window.console && window.console.error) window.console.error("stage pass failed", err);
    });
  }

  function passFailed(section, status, html) {
    if (section) section.classList.remove("is-passing");
    var target = panel();
    if (target) target.removeAttribute("aria-busy");
    var info = describe(status, null);
    if (html && html.indexOf("qa-field") >= 0 && panel()) {
      // validation/scope errors come back as the panel partial — show it.
      toast("error", "Not passed", info[0] + ". " + info[1]);
      swapPanel(html);
      return;
    }
    toast("error", "Not passed — " + info[0].toLowerCase(), info[1] + " The case did not move; reload to retry.");
    QA.announce("Your action did not go through. The case was not moved.", true);
    if (window.htmx) {
      window.htmx.ajax("GET", window.location.pathname + window.location.search, { target: "#qa-panel" });
    } else {
      window.location.reload();
    }
  }

  function initStagePass() {
    document.addEventListener("qa:confirmed", function (e) {
      var form = e.target;
      if (!(form instanceof HTMLFormElement) || form.getAttribute("data-qa-stage-pass") !== "approve") return;
      e.preventDefault();
      e.stopImmediatePropagation();  // the request is ours; htmx must not double-fire
      passStage(form);
    }, true);
    document.addEventListener("submit", function (e) {
      var form = e.target;
      if (!(form instanceof HTMLFormElement)) return;
      var kind = form.getAttribute("data-qa-stage-pass");
      if (kind !== "assign" && kind !== "start") return;
      e.preventDefault();
      e.stopImmediatePropagation();  // the request is ours; htmx must not double-fire
      passStage(form);
    }, true);
  }

  // [data-qa-collapse="#id"] closes an expanded sub-row back to its empty slot.
  function initCollapse() {
    document.addEventListener("click", function (e) {
      var btn = e.target.closest && e.target.closest("[data-qa-collapse]");
      if (!btn) return;
      var el = document.querySelector(btn.getAttribute("data-qa-collapse"));
      if (!el) return;
      var owner = el.previousElementSibling && el.previousElementSibling.querySelector("[hx-target='#" + el.id + "']");
      el.className = "qa-slot";
      el.innerHTML = "";
      if (owner) owner.focus();
    });
  }

  function initDetailsToggles() {
    // <details data-qa-remember="key"> keeps its open state per viewer.
    Array.prototype.forEach.call(document.querySelectorAll("details[data-qa-remember]"), function (d) {
      var key = "qa:details:" + d.getAttribute("data-qa-remember");
      try { if (localStorage.getItem(key) === "1") d.open = true; } catch (e) {}
      d.addEventListener("toggle", function () {
        try { localStorage.setItem(key, d.open ? "1" : "0"); } catch (e) {}
      });
    });
  }

  function initBottomNavActive() {
    // Keep the portal's top tabs and bottom tab bar in sync after boosted swaps.
    document.body.addEventListener("htmx:pushedIntoHistory", syncNav);
    document.body.addEventListener("htmx:replacedInHistory", syncNav);
    window.addEventListener("popstate", function () { window.setTimeout(syncNav, 30); });
  }

  function syncNav() {
    var path = window.location.pathname;
    Array.prototype.forEach.call(document.querySelectorAll("[data-qa-nav]"), function (a) {
      var match = a.getAttribute("data-qa-nav");
      var active = match === "/" ? path === "/" : path.indexOf(match) === 0;
      a.classList.toggle("is-active", active);
      if (active) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
    });
  }

  onReady(function () {
    liveRegion("qa-live", "polite");
    liveRegion("qa-live-assertive", "assertive");
    wireHtmx();
    initOptimistic();
    initCopyButtons();
    initDecisionForms();
    initStagePass();
    initCollapse();
    initDetailsToggles();
    initBottomNavActive();
  });
})();
