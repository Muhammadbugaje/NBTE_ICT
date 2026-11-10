/* =========================================================================
   Scroll memory + scroll-into-view helpers (Phase 8 UX)

   A. Remember where you left off
      - The window scroll position is saved per URL (path + query) in
        sessionStorage while the user scrolls, and again before leaving
        (link click, form submit, HTMX navigation, pagehide).
      - It is restored when the user comes BACK to that URL: browser
        Back/Forward and reload (full page), HTMX history restore (cache hit
        or miss). A normal forward visit starts at the top as usual.
      - The last clicked row/card (by href) is remembered too and briefly
        highlighted on return, so the user sees where they were.
      - Never fights in-page anchors (#hash), pages that scroll themselves
        (message threads: [data-thread]) or any [data-scroll-memory="off"].
      - history.scrollRestoration = "manual" so the browser and this script
        don't both move the page; bfcache (pageshow.persisted) keeps the
        browser's own position and only adds the highlight.

   B. Reveal swapped forms (data-scroll-into-view)
      Any element carrying [data-scroll-into-view] that HTMX swaps in (or
      that is present on load with the value "load") is smoothly scrolled
      into view and its first field is focused. Put it on edit-form partials:
        <form data-scroll-into-view ...>
      Value "nofocus" scrolls without moving focus. NBTE.revealForm(el) does
      the same from other scripts.
   ========================================================================= */
(function () {
  "use strict";
  if (window.NBTEScrollMemory) return;

  var PREFIX = "nbte-scroll:";
  var CLICK_PREFIX = "nbte-lastclick:";
  var MAX_WAIT = 2500;          // ms to wait for content tall enough
  var suspended = 0;            // while restoring, don't overwrite saved values
  var userMoved = false;

  function key(loc) {
    loc = loc || window.location;
    return loc.pathname + loc.search;
  }
  function ss(fn) { try { return fn(window.sessionStorage); } catch (e) { return null; } }
  function save(k) {
    if (Date.now() < suspended) return;
    k = k || key();
    var y = Math.round(window.scrollY || window.pageYOffset || 0);
    ss(function (s) { s.setItem(PREFIX + k, String(y)); });
  }
  function read(k) {
    var v = ss(function (s) { return s.getItem(PREFIX + k); });
    if (v == null) return null;
    var n = parseInt(v, 10);
    return isNaN(n) ? null : n;
  }
  function optedOut() {
    if (window.location.hash && window.location.hash.length > 1) return true;
    return !!document.querySelector('[data-scroll-memory="off"], [data-thread], [data-scroll-into-view="load"]');
  }

  if ("scrollRestoration" in window.history) {
    try { window.history.scrollRestoration = "manual"; } catch (e) {}
  }

  /* ---- saving ---------------------------------------------------------- */
  var ticking = false;
  window.addEventListener("scroll", function () {
    if (ticking) return;
    ticking = true;
    window.requestAnimationFrame(function () { ticking = false; save(); });
  }, { passive: true });

  ["wheel", "touchstart", "keydown", "mousedown"].forEach(function (t) {
    window.addEventListener(t, function () { userMoved = true; }, { passive: true, capture: true });
  });

  function rememberClick(a) {
    var href = a.getAttribute("href");
    if (!href || href.charAt(0) === "#" || /^(javascript|mailto|tel):/i.test(href)) return;
    if (a.closest(".app-sidebar, .app-topbar, .topbar, .qa-topbar, .qa-tabbar, .mobile-tabbar, nav[aria-label='Breadcrumb'], .breadcrumb, .fpv")) return;
    ss(function (s) { s.setItem(CLICK_PREFIX + key(), href); });
  }

  document.addEventListener("click", function (e) {
    var a = e.target.closest && e.target.closest("a[href]");
    if (!a) return;
    save();
    rememberClick(a);
  }, true);
  document.addEventListener("submit", function () { save(); }, true);
  window.addEventListener("pagehide", function () { save(); });
  document.addEventListener("visibilitychange", function () { if (document.visibilityState === "hidden") save(); });

  // HTMX: snapshot the page we are leaving before a history push/replace.
  document.addEventListener("htmx:beforeHistorySave", function () { save(); });
  // While a pushed HTMX navigation swaps content the browser clamps the
  // scroll position; don't let that overwrite what we saved for the old URL.
  var navPending = false;
  document.addEventListener("htmx:beforeRequest", function (e) {
    var elt = e.detail && e.detail.elt;
    if (elt && (elt.hasAttribute("hx-push-url") || elt.hasAttribute("data-hx-push-url") || e.detail.boosted)) {
      save();
      navPending = true;
    }
  });
  document.addEventListener("htmx:beforeSwap", function () {
    if (navPending) suspended = Date.now() + 5000;
  });
  function endNav() {
    if (!navPending) return;
    navPending = false;
    window.setTimeout(function () { suspended = 0; save(); }, 120);
  }
  document.addEventListener("htmx:afterSettle", endNav);
  document.addEventListener("htmx:afterRequest", function (e) {
    if (e.detail && e.detail.failed) endNav();
  });
  // Back/Forward inside the HTMX shell: the URL has changed but the old
  // content is still there for a moment — stop saving until restored.
  window.addEventListener("popstate", function () { suspended = Date.now() + MAX_WAIT; });

  /* ---- restoring ------------------------------------------------------- */
  function highlightLastClicked(k, scrollIfHidden) {
    var href = ss(function (s) { return s.getItem(CLICK_PREFIX + k); });
    if (!href) return;
    var esc = window.CSS && CSS.escape ? CSS.escape(href) : href.replace(/"/g, '\\"');
    var main = document.querySelector("#qa-main, #app-content, main") || document.body;
    var link = main.querySelector('a[href="' + esc + '"]');
    if (!link) return;
    var row = link.closest("tr, li, .qa-rowlink, [data-row], .card, article, .list-row") || link;
    if (scrollIfHidden) {
      var r = row.getBoundingClientRect();
      if (r.bottom < 0 || r.top > window.innerHeight) {
        row.scrollIntoView({ block: "center" });
      }
    }
    row.classList.remove("is-return-highlight");
    void row.offsetWidth; // restart the animation
    row.classList.add("is-return-highlight");
    window.setTimeout(function () { row.classList.remove("is-return-highlight"); }, 2600);
  }

  // html { scroll-behavior: smooth } would animate a plain scrollTo.
  function jump(y) {
    try { window.scrollTo({ top: y, left: 0, behavior: "instant" }); }
    catch (e) { window.scrollTo(0, y); }
  }

  function restore(k, opts) {
    opts = opts || {};
    if (optedOut()) return;
    var y = read(k);
    var started = Date.now();
    suspended = started + MAX_WAIT;
    userMoved = false;
    function attempt() {
      if (userMoved) { suspended = 0; return; }
      if (y == null || y <= 0) {
        suspended = 0;
        highlightLastClicked(k, true);
        return;
      }
      var maxY = document.documentElement.scrollHeight - window.innerHeight;
      if (maxY >= y - 2 || Date.now() - started > MAX_WAIT) {
        jump(Math.min(y, Math.max(0, maxY)));
        suspended = 0;
        highlightLastClicked(k, false);
        return;
      }
      jump(Math.max(0, maxY));
      window.setTimeout(attempt, 60);
    }
    window.setTimeout(attempt, opts.delay || 0);
  }

  function navType() {
    try {
      var nav = performance.getEntriesByType("navigation")[0];
      if (nav && nav.type) return nav.type;
    } catch (e) {}
    if (performance.navigation) {
      return { 1: "reload", 2: "back_forward" }[performance.navigation.type] || "navigate";
    }
    return "navigate";
  }

  function onReady(fn) {
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", fn);
    else fn();
  }

  onReady(function () {
    var t = navType();
    if (t === "back_forward" || t === "reload") restore(key());
  });

  window.addEventListener("pageshow", function (e) {
    if (e.persisted) highlightLastClicked(key(), false);
  });

  // HTMX history restore (Back/Forward inside an HTMX app shell). htmx puts
  // its own cached position back with setTimeout(0); ours runs after it.
  document.addEventListener("htmx:historyRestore", function (e) {
    var path = (e.detail && e.detail.path) || key();
    restore(path, { delay: 20 });
  });

  /* ---- B. data-scroll-into-view ---------------------------------------- */
  var FIELD = 'input:not([type=hidden]):not([disabled]):not([readonly]), select:not([disabled]), textarea:not([disabled]):not([readonly]), [contenteditable="true"]';

  function topOffset() {
    var bar = document.querySelector(".app-topbar, .topbar, .qa-topbar");
    var h = 0;
    if (bar) {
      var cs = getComputedStyle(bar);
      if (cs.position === "fixed" || cs.position === "sticky") h = bar.getBoundingClientRect().height;
    }
    return h + 16;
  }

  function reveal(el, opts) {
    if (!el) return;
    opts = opts || {};
    var reduce = (window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches) || /^(reduce|reduced|off)$/.test(document.documentElement.getAttribute("data-motion") || "");
    var r = el.getBoundingClientRect();
    var off = topOffset();
    var fullyVisible = r.top >= off && r.bottom <= window.innerHeight;
    if (!fullyVisible) {
      var top = r.top + window.scrollY - off;
      window.scrollTo({ top: Math.max(0, top), behavior: reduce ? "auto" : "smooth" });
    }
    el.classList.remove("is-revealed");
    void el.offsetWidth;
    el.classList.add("is-revealed");
    window.setTimeout(function () { el.classList.remove("is-revealed"); }, 1600);
    if (opts.focus !== false) {
      var field = el.matches && el.matches(FIELD) ? el : el.querySelector(FIELD);
      if (field && typeof field.focus === "function") {
        // preventScroll: the smooth scroll above owns the motion
        window.setTimeout(function () {
          try { field.focus({ preventScroll: true }); } catch (e) { field.focus(); }
          if (field.select && /^(text|search|email|url|tel|number)$/.test(field.type || "")) {
            try { field.select(); } catch (e) {}
          }
        }, reduce ? 0 : 280);
      }
    }
  }

  var seen = typeof WeakSet === "function" ? new WeakSet() : { has: function () { return false; }, add: function () {} };
  function scanFor(root) {
    if (!root || !root.querySelectorAll) return;
    var list = [];
    if (root.hasAttribute && root.hasAttribute("data-scroll-into-view")) list.push(root);
    Array.prototype.push.apply(list, root.querySelectorAll("[data-scroll-into-view]"));
    var el = null;
    for (var i = 0; i < list.length; i++) {
      if (!seen.has(list[i]) && list[i].getClientRects().length) { el = list[i]; break; }
    }
    if (!el) return;
    seen.add(el);
    reveal(el, { focus: el.getAttribute("data-scroll-into-view") !== "nofocus" });
  }

  // Only an explicit Edit/View/Change click (a link or button outside the
  // swapped region) reveals the form; a form re-rendered by its own submit or
  // by a field change keeps the user's focus where it is.
  var lastTrigger = null;
  document.addEventListener("htmx:beforeRequest", function (e) {
    lastTrigger = e.detail && e.detail.elt;
  });
  document.addEventListener("htmx:afterSettle", function (e) {
    if (e.detail && e.detail.historyRequest) return;
    var trigger = (e.detail && e.detail.requestConfig && e.detail.requestConfig.elt) || lastTrigger;
    if (!trigger || !trigger.matches || !trigger.matches("a, button, [role=button], summary")) return;
    var t = e.detail && e.detail.target;
    if (t && !t.isConnected) t = e.target;
    if (!t || !t.isConnected) return;
    if (t.contains(trigger) && trigger.isConnected) return;
    window.requestAnimationFrame(function () { scanFor(t); });
  });

  onReady(function () {
    var el = document.querySelector('[data-scroll-into-view="load"]');
    if (el && !(window.location.hash && window.location.hash.length > 1)) reveal(el);
  });

  window.NBTEScrollMemory = { save: save, restore: restore, key: key };
  window.NBTE = window.NBTE || {};
  window.NBTE.revealForm = reveal;
})();
