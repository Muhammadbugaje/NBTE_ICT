/* ==========================================================================
   NBTE QA — live page kit (v2.8)

   Makes the QA portal and the SOP-03 case panel feel instant and keep
   themselves current without a full page reload:

     • A thin top progress bar and busy state on every HTMX request.
     • A small "Live" pill that polls a cheap JSON version stamp
       (qa_portal:live / qa_case:live) and refreshes the region only when
       the server says something moved.
     • Safe refresh guards: never while the viewer is typing, has unsaved
       form changes, has a dialog open, or while an HTMX request is in
       flight. In that case the pill becomes "Update available".
     • Polling pauses while the tab is hidden and backs off on failures.
     • Hover/focus prefetch warms the next page (best effort; skipped on
       save-data and 2G connections).
     • A short swap-in animation, disabled for prefers-reduced-motion.

   Loaded on the internal QA shell (qa_core/_shell.html) and the portal
   shell (qa_portal/base_portal.html), after qa.js. Everything is opt-in:
   a region only polls when it carries data-qa-live-url.
   ========================================================================== */
(function () {
  "use strict";

  var NBTE = (window.NBTE = window.NBTE || {});
  var QA = (NBTE.QA = NBTE.QA || {});
  if (QA.live) return;
  QA.live = true;

  var POLL_MS = 9000;
  var HIDDEN_RESUME_MS = 250;
  var FIRST_POLL_MIN = 2500;
  var FIRST_POLL_JITTER = 3500;
  var MAX_BACKOFF = 60000;

  var current = null;
  var pill = null;
  var bar = null;
  var inflightHtmx = 0;
  var barTimer = null;
  var prefetched = {};
  var prefetchQueue = [];
  var prefetching = false;

  function onReady(fn) {
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", fn);
    else fn();
  }

  function reduceMotion() {
    return !!(window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches);
  }

  function layoutHint() {
    var narrow = window.matchMedia && window.matchMedia("(max-width: 767px)").matches;
    var coarse = window.matchMedia && window.matchMedia("(pointer: coarse)").matches;
    return narrow || (coarse && window.innerWidth < 1024) ? "mobile" : "desktop";
  }

  function announce(message) {
    if (QA.announce) QA.announce(message, false);
  }

  /* ------------------------------------------------------------- progress */
  function ensureBar() {
    if (bar) return bar;
    bar = document.createElement("div");
    bar.className = "qa-progress";
    bar.setAttribute("aria-hidden", "true");
    var fill = document.createElement("span");
    bar.appendChild(fill);
    document.body.appendChild(bar);
    return bar;
  }

  function showBar() {
    ensureBar();
    if (barTimer) { window.clearTimeout(barTimer); barTimer = null; }
    bar.classList.add("is-active");
    bar.firstChild.style.width = "12%";
    window.requestAnimationFrame(function () {
      if (bar && bar.classList.contains("is-active")) bar.firstChild.style.width = "72%";
    });
  }

  function finishBar() {
    if (!bar) return;
    bar.firstChild.style.width = "100%";
    if (barTimer) window.clearTimeout(barTimer);
    barTimer = window.setTimeout(function () {
      if (!bar) return;
      bar.classList.remove("is-active");
      bar.firstChild.style.width = "0";
    }, 240);
  }

  /* ---------------------------------------------------------------- pill */
  function ensurePill() {
    if (pill) return pill;
    pill = document.createElement("button");
    pill.type = "button";
    pill.className = "qa-live-pill";
    pill.setAttribute("data-state", "idle");
    pill.setAttribute("aria-live", "polite");
    pill.innerHTML = '<span class="qa-live-pill__dot" aria-hidden="true"></span>' +
                     '<span class="qa-live-pill__text">Live</span>';
    pill.addEventListener("click", function () {
      if (!current) return;
      if (pill.getAttribute("data-state") === "available") refreshNow(true);
      else if (pill.getAttribute("data-state") === "offline") pollNow();
    });
    document.body.appendChild(pill);
    return pill;
  }

  function setPill(state, text) {
    if (!current) return;
    ensurePill();
    pill.setAttribute("data-state", state);
    pill.querySelector(".qa-live-pill__text").textContent = text;
    var label = current.label ? " · " + current.label : "";
    pill.title = text + label;
    pill.setAttribute("aria-label", text + label);
    pill.disabled = state !== "available" && state !== "offline";
  }

  function removePill() {
    if (pill && pill.parentNode) pill.parentNode.removeChild(pill);
    pill = null;
  }

  /* --------------------------------------------------------------- guards */
  function activeField() {
    var el = document.activeElement;
    if (!el || el === document.body) return false;
    var tag = (el.tagName || "").toLowerCase();
    return tag === "input" || tag === "textarea" || tag === "select" || el.isContentEditable;
  }

  function hasDialog() {
    return !!document.querySelector(
      'dialog[open], [role="dialog"]:not([hidden]), [aria-modal="true"]:not([hidden]), .modal.is-open'
    );
  }

  function targetBusy() {
    if (!current || !current.target) return false;
    return current.target.getAttribute("aria-busy") === "true" ||
      current.target.classList.contains("qa-is-loading");
  }

  function hasDirtyForm() {
    if (!current || !current.target) return false;
    return !!current.target.querySelector('form[data-qa-dirty="1"]');
  }

  function canRefresh() {
    return !document.hidden && !activeField() && !hasDialog() && !targetBusy() &&
      !hasDirtyForm() && inflightHtmx === 0 && !current.refreshing;
  }

  /* ------------------------------------------------------------- polling */
  function backoff() {
    if (!current) return POLL_MS;
    var n = Math.min(current.failures || 0, 4);
    return Math.min(MAX_BACKOFF, POLL_MS * Math.pow(2, n));
  }

  function schedule(ms) {
    if (!current) return;
    if (current.timer) window.clearTimeout(current.timer);
    current.timer = null;
    if (document.hidden) return;
    current.timer = window.setTimeout(pollNow, ms);
  }

  function abortPoll() {
    if (current && current.controller) {
      try { current.controller.abort(); } catch (e) { /* already done */ }
      current.controller = null;
    }
  }

  function pollNow() {
    if (!current || document.hidden || current.inflight || current.refreshing) return;
    var gen = current.gen;
    var url = current.url;
    if (!url) return;
    var req = url;
    if (current.token) {
      req += (url.indexOf("?") >= 0 ? "&" : "?") + "v=" + encodeURIComponent(current.token);
    }
    var controller = window.AbortController ? new AbortController() : null;
    current.controller = controller;
    current.inflight = true;
    fetch(req, {
      credentials: "same-origin",
      cache: "no-store",
      headers: { "Accept": "application/json", "X-Requested-With": "XMLHttpRequest" },
      signal: controller ? controller.signal : undefined
    }).then(function (r) {
      if (!r.ok) throw new Error("HTTP " + r.status);
      return r.json();
    }).then(function (data) {
      if (!current || gen !== current.gen) return;
      current.failures = 0;
      var first = !current.token;
      if (data && data.v) current.token = data.v;
      if (data && typeof data.unread === "number") updateUnread(data.unread);
      if (!data || data.unchanged) {
        if (current.pending) setPill("available", "Update available");
        return;
      }
      if (first) return; // the first answer is the baseline, not a change
      if (canRefresh()) {
        // Hold the poll loop until the refresh runs, then let the refreshed
        // panel set a fresh baseline.
        current.pending = true;
        setPill("updating", "Updating…");
        window.setTimeout(function () {
          if (!current) return;
          current.pending = false;
          refreshNow(false);
        }, 450);
      } else {
        var wasPending = current.pending;
        current.pending = true;
        setPill("available", "Update available");
        if (!wasPending) announce("New information is available. Refresh when you are ready.");
      }
    }).catch(function (err) {
      if (!current || gen !== current.gen) return;
      if (err && err.name === "AbortError") return;
      current.failures = (current.failures || 0) + 1;
      if (current.failures >= 2) setPill("offline", "Reconnecting…");
    }).finally(function () {
      if (!current || gen !== current.gen) return;
      current.inflight = false;
      current.controller = null;
      // Keep a gentle poll running even while an update is pending: the
      // guards may clear (field blurred, dialog closed) without another
      // event, and the next poll will refresh as soon as it is safe.
      schedule(backoff());
    });
  }

  /* ------------------------------------------------------------ refresh */
  function confirmDiscard() {
    var message = "You have unsaved changes. Refresh and lose them?";
    if (window.NBTEConfirm && typeof window.NBTEConfirm.open === "function") {
      return window.NBTEConfirm.open({
        title: "Refresh this page?",
        body: message,
        confirmLabel: "Refresh",
        tone: "default"
      }).then(function (ok) { return !!ok; });
    }
    return Promise.resolve(window.confirm(message));
  }

  function refreshNow(manual) {
    if (!current || current.refreshing) return;
    if (!manual && !canRefresh()) {
      current.pending = true;
      setPill("available", "Update available");
      schedule(5000);
      return;
    }
    var go = hasDirtyForm() ? confirmDiscard() : Promise.resolve(true);
    go.then(function (ok) {
      if (!ok || !current || current.refreshing) return;
      doRefresh();
    });
  }

  function doRefresh() {
    var gen = current.gen;
    var target = current.target;
    if (!target || !target.isConnected) { window.location.reload(); return; }
    current.refreshing = true;
    current.pending = false;
    setPill("updating", "Updating…");
    var url = window.location.pathname + window.location.search;
    var targetId = target.id || (current.targetSel || "").replace(/^#/, "");
    fetch(url, {
      credentials: "same-origin",
      cache: "no-store",
      headers: {
        "Accept": "text/html",
        "HX-Request": "true",
        "HX-Target": targetId,
        "X-NBTE-Layout": layoutHint()
      }
    }).then(function (r) {
      if (!r.ok) throw new Error("HTTP " + r.status);
      return r.text();
    }).then(function (html) {
      if (!current || gen !== current.gen) return;
      if (!html) throw new Error("Empty response");
      // A view that does not answer HTMX with a partial would inject a
      // whole document into the region. Reload instead.
      if (/^\s*<!doctype/i.test(html) || /<html[\s>]/i.test(html.slice(0, 600))) {
        window.location.reload();
        return;
      }
      var scrollY = window.scrollY;
      if (window.htmx && typeof window.htmx.swap === "function") {
        try {
          window.htmx.swap(target, html, { swapStyle: "innerHTML" });
        } catch (e) {
          target.innerHTML = html;
        }
      } else {
        target.innerHTML = html;
      }
      var strayTitle = target.querySelector("title");
      if (strayTitle) strayTitle.remove();
      if (window.htmx) {
        try { window.htmx.process(target); } catch (e) { /* old htmx */ }
      }
      window.scrollTo(0, scrollY);
      current.refreshing = false;
      setPill("idle", "Live");
      animateSwap(target);
      notifySwap(target);
      refreshState(true);
    }).catch(function () {
      if (!current || gen !== current.gen) return;
      current.refreshing = false;
      current.failures = (current.failures || 0) + 1;
      setPill("offline", "Reconnecting…");
      schedule(backoff());
    });
  }

  function notifySwap(target) {
    var detail = { target: target, elt: target, xhr: null };
    if (window.htmx && typeof window.htmx.trigger === "function") {
      try { window.htmx.trigger(target, "htmx:afterSwap", detail); return; } catch (e) { /* fall through */ }
    }
    try {
      target.dispatchEvent(new CustomEvent("htmx:afterSwap", { bubbles: true, detail: detail }));
    } catch (e) { /* very old browser */ }
  }

  /* ------------------------------------------------------------- unread */
  function updateUnread(n) {
    var count = Math.max(0, parseInt(n, 10) || 0);
    var hosts = document.querySelectorAll("[data-qa-unread-host]");
    Array.prototype.forEach.call(hosts, function (host) {
      var badge = host.querySelector("[data-qa-unread-badge]");
      if (count > 0) {
        if (!badge) {
          badge = document.createElement("span");
          badge.className = "qa-icon-btn__badge";
          badge.setAttribute("data-qa-unread-badge", "");
          badge.setAttribute("aria-hidden", "true");
          host.appendChild(badge);
        }
        badge.textContent = count > 99 ? "99+" : String(count);
      } else if (badge) {
        badge.parentNode.removeChild(badge);
      }
      if (host.tagName === "BUTTON") {
        host.setAttribute("aria-label", count ? "Notifications, " + count + " unread" : "Notifications");
      }
    });
  }

  /* ------------------------------------------------------------- swap-in */
  function animateSwap(target) {
    if (!target || reduceMotion()) return;
    target.classList.remove("qa-swap-in");
    // Force a reflow so the animation restarts when the same node is reused.
    void target.offsetWidth;
    target.classList.add("qa-swap-in");
    window.setTimeout(function () { target.classList.remove("qa-swap-in"); }, 520);
  }

  /* ------------------------------------------------------------ lifecycle */
  function stop() {
    abortPoll();
    if (current && current.timer) window.clearTimeout(current.timer);
    current = null;
    removePill();
  }

  function refreshState(force) {
    var root = document.querySelector("[data-qa-live-url]");
    if (!root) { stop(); return; }
    var url = root.getAttribute("data-qa-live-url");
    var targetSel = root.getAttribute("data-qa-live-target") || "#qa-main";
    var target = document.querySelector(targetSel) || root.closest("[data-qa-region]") || root;
    if (!url || !target) { stop(); return; }
    var label = root.getAttribute("data-qa-live-label") || "";
    var changed = !current || current.url !== url || current.targetSel !== targetSel;
    if (changed) {
      abortPoll();
      if (current && current.timer) window.clearTimeout(current.timer);
      current = {
        url: url, targetSel: targetSel, target: target, label: label,
        token: "", timer: null, inflight: false, refreshing: false,
        failures: 0, pending: false, gen: (current ? current.gen + 1 : 1), controller: null
      };
      ensurePill();
      setPill("idle", "Live");
      schedule(FIRST_POLL_MIN + Math.random() * FIRST_POLL_JITTER);
    } else {
      current.target = target;
      current.label = label || current.label;
      if (force) {
        if (current.timer) { window.clearTimeout(current.timer); current.timer = null; }
        schedule(300);
      }
    }
  }

  /* ------------------------------------------------------------- prefetch */
  function canPrefetch() {
    if (document.hidden) return false;
    var c = navigator.connection;
    if (c && (c.saveData || /(^|-)2g$/.test(c.effectiveType || ""))) return false;
    return true;
  }

  function prefetchLink(a) {
    if (!a || !canPrefetch()) return;
    if (a.hasAttribute("download") || a.target) return;
    if (a.getAttribute("hx-boost") === "false") return;
    if (!a.closest("[hx-boost], [hx-get]")) return;
    var href = a.getAttribute("href");
    if (!href || href.charAt(0) === "#" || /^(mailto:|tel:|javascript:)/i.test(href)) return;
    var url;
    try { url = new URL(href, window.location.href); } catch (e) { return; }
    if (url.origin !== window.location.origin) return;
    if (url.pathname === window.location.pathname && url.search === window.location.search) return;
    if (prefetched[url.href]) return;
    prefetched[url.href] = true;
    var targetSel = (current && current.targetSel) || "#qa-main";
    var targetId = targetSel.replace(/^#/, "");
    prefetchQueue.push({ url: url.href, target: targetId });
    drainPrefetch();
  }

  function drainPrefetch() {
    if (prefetching || !prefetchQueue.length || !canPrefetch()) return;
    prefetching = true;
    var item = prefetchQueue.shift();
    fetch(item.url, {
      credentials: "same-origin",
      // Warm the route without leaving a stale partial in the HTTP cache;
      // the real HTMX request must always see the current page.
      cache: "no-store",
      headers: {
        "Accept": "text/html",
        "HX-Request": "true",
        "HX-Target": item.target,
        "X-NBTE-Layout": layoutHint()
      }
    }).catch(function () { /* best effort */ }).finally(function () {
      prefetching = false;
      window.setTimeout(drainPrefetch, 120);
    });
  }

  function hoverIntent(a) {
    if (!a || !a.href) return;
    window.setTimeout(function () { prefetchLink(a); }, 120);
  }

  /* ---------------------------------------------------------------- wire */
  function wireHtmx() {
    if (!window.htmx) { window.setTimeout(wireHtmx, 60); return; }
    var body = document.body;
    body.addEventListener("htmx:beforeRequest", function () {
      inflightHtmx += 1;
      showBar();
    });
    body.addEventListener("htmx:afterRequest", function () {
      inflightHtmx = Math.max(0, inflightHtmx - 1);
      if (inflightHtmx === 0) finishBar();
    });
    body.addEventListener("htmx:afterSwap", function (evt) {
      var target = (evt.detail && evt.detail.target) || evt.target;
      animateSwap(target);
      refreshState(false);
    });
    body.addEventListener("htmx:afterSettle", function () { refreshState(false); });
    body.addEventListener("htmx:historyRestore", function () { refreshState(true); });
  }

  function wireInteractions() {
    document.addEventListener("click", function (e) {
      if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
      var a = e.target.closest && e.target.closest("a[href]");
      if (!a || a.target || a.hasAttribute("download")) return;
      var href = a.getAttribute("href");
      if (!href || href.charAt(0) === "#") return;
      var url;
      try { url = new URL(a.href, window.location.href); } catch (err) { return; }
      if (url.origin !== window.location.origin) return;
      if (url.pathname === window.location.pathname && url.hash) return;
      showBar();
      window.setTimeout(function () { if (inflightHtmx === 0) finishBar(); }, 4000);
    }, true);
    document.addEventListener("submit", function (e) {
      if (!e.defaultPrevented) {
        showBar();
        window.setTimeout(function () { if (inflightHtmx === 0) finishBar(); }, 4000);
      }
      var form = e.target.closest && e.target.closest("form");
      if (form) form.removeAttribute("data-qa-dirty");
    }, true);
    document.addEventListener("mouseover", function (e) {
      var a = e.target.closest && e.target.closest("a[href]");
      if (a) hoverIntent(a);
    }, true);
    document.addEventListener("focusin", function (e) {
      var a = e.target.closest && e.target.closest("a[href]");
      if (a) prefetchLink(a);
    });
    document.addEventListener("focusout", function () {
      if (current && current.pending) schedule(900);
    }, true);
    // Mark forms dirty so an auto-refresh never throws away typing.
    document.addEventListener("input", function (e) {
      var form = e.target.closest && e.target.closest("form");
      if (form && !form.hasAttribute("data-qa-no-dirty")) form.setAttribute("data-qa-dirty", "1");
    }, true);
    document.addEventListener("change", function (e) {
      var form = e.target.closest && e.target.closest("form");
      if (form && !form.hasAttribute("data-qa-no-dirty")) form.setAttribute("data-qa-dirty", "1");
    }, true);
    document.addEventListener("visibilitychange", function () {
      if (document.hidden) {
        abortPoll();
        if (current && current.timer) { window.clearTimeout(current.timer); current.timer = null; }
        return;
      }
      refreshState(false);
      window.setTimeout(function () { if (current) pollNow(); }, HIDDEN_RESUME_MS);
    });
    window.addEventListener("online", function () { if (current) { current.failures = 0; pollNow(); } });
    window.addEventListener("offline", function () { if (current) setPill("offline", "Offline"); });
    window.addEventListener("pagehide", stop);
    window.addEventListener("pageshow", function (e) {
      if (e && e.persisted) refreshState(true);
    });
    window.addEventListener("focus", function () {
      if (!current || document.hidden) return;
      if (!current.timer && !current.inflight && !current.refreshing) schedule(300);
    });
  }

  onReady(function () {
    ensureBar();
    wireInteractions();
    wireHtmx();
    refreshState(true);
  });
})();
