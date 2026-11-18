/* =========================================================================
   ux-affordance.js — loaded (defer) from templates/base.html and
   templates/qa_portal/base_portal.html. Styles: static/css/ux-affordance.css

     1. Horizontal scroll affordance for tab / chip strips
     2. Reminder corner card (profile, signature, bank details)
     3. Tab strips whose active tab follows ?tab= (institution file)

   No inline handlers and no eval: the site's CSP forbids 'unsafe-eval'.
   ========================================================================= */
(function () {
  "use strict";
  if (window.NBTEAffordance) return;

  var reduceMotion = false;
  try { reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches; } catch (e) {}

  function debounce(fn, ms) {
    var t;
    return function () { window.clearTimeout(t); t = window.setTimeout(fn, ms); };
  }

  /* ====================================================================
     1. Horizontal scroll affordance
     ==================================================================== */
  var STRIPS = [
    ".qa-tabs", ".ix-tabs", ".qa-insp-nav", ".ssq-desk__tabs", ".qa-setnav", ".um-segments", ".sw-seg",
    ".c3-tabs", ".c3-steps", ".ad-tabs", ".qa-stepper", ".qa-board", ".c3-board", ".pb-tabs", ".portal-tabs",
    ".tk-tabs", ".tsk-tabs", ".wf-tabs", ".hr-tabs", ".org-tabs", ".notif-filters", ".ann-filters",
    ".npk__filters", ".ib-statustabs", ".pf-tabs", ".lj-track", "[data-hscroll]"
  ].join(",");
  var ACTIVE = '.is-active, .active, .is-current, [aria-current="page"], [aria-current="step"], [aria-selected="true"]';
  var EDGE = 2;          // px tolerance at either end
  var TALL = 160;        // taller than this = a board, not a tab strip
  var CHEV_L = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="15 18 9 12 15 6"/></svg>';
  var CHEV_R = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="9 18 15 12 9 6"/></svg>';
  var ro = "ResizeObserver" in window ? new ResizeObserver(function (entries) {
    entries.forEach(function (en) { update(en.target); });
  }) : null;

  function scrollsX(el) {
    var ox = window.getComputedStyle(el).overflowX;
    return ox === "auto" || ox === "scroll";
  }
  function overflows(el) { return el.scrollWidth - el.clientWidth > 4; }

  function makeBtn(dir) {
    var b = document.createElement("button");
    b.type = "button";
    b.className = "hs-btn hs-btn--" + (dir < 0 ? "l" : "r");
    b.setAttribute("data-hs-btn", dir < 0 ? "-1" : "1");
    b.setAttribute("aria-label", dir < 0 ? "Scroll back" : "Show more");
    b.setAttribute("tabindex", "-1");   // keyboard users reach every tab with Tab already
    b.innerHTML = dir < 0 ? CHEV_L : '<span class="hs-btn__label" aria-hidden="true">More</span>' + CHEV_R;
    return b;
  }

  /* Move the strip's outer box (margin, flex/grid placement, sticky) to the
     wrapper so wrapping never shifts the layout. Re-run on resize because
     media queries may change those values. */
  var BOX_PROPS = ["marginTop", "marginRight", "marginBottom", "marginLeft", "position", "top", "zIndex"];
  function syncBox(strip, wrap) {
    BOX_PROPS.forEach(function (p) { strip.style[p] = ""; });
    var cs = window.getComputedStyle(strip);
    var ws = wrap.style;
    ws.marginTop = cs.marginTop; ws.marginRight = cs.marginRight;
    ws.marginBottom = cs.marginBottom; ws.marginLeft = cs.marginLeft;
    ws.flex = cs.flexGrow + " " + cs.flexShrink + " " + cs.flexBasis;
    ws.alignSelf = cs.alignSelf;
    ws.order = cs.order;
    ws.gridColumn = cs.gridColumnStart === "auto" && cs.gridColumnEnd === "auto" ? "" : cs.gridColumnStart + " / " + cs.gridColumnEnd;
    wrap.classList.toggle("hs-wrap--inline", cs.display.indexOf("inline") === 0);
    if (cs.position === "sticky") {
      ws.position = "sticky"; ws.top = cs.top; ws.zIndex = cs.zIndex;
      strip.style.position = "relative"; strip.style.top = "auto";
    } else {
      ws.position = ""; ws.top = ""; ws.zIndex = "";
    }
    strip.style.marginTop = strip.style.marginRight = strip.style.marginBottom = strip.style.marginLeft = "0";
  }

  function wrapOf(strip) {
    var p = strip.parentElement;
    return p && p.classList.contains("hs-wrap") ? p : null;
  }

  function ensureWrap(strip) {
    var wrap = wrapOf(strip);
    if (!wrap) {
      wrap = document.createElement("div");
      wrap.className = "hs-wrap";
      // measure before moving: the computed values are the same either way
      strip.parentNode.insertBefore(wrap, strip);
      syncBox(strip, wrap);
      var left = strip.scrollLeft;
      wrap.appendChild(strip);
      strip.scrollLeft = left;
    }
    strip.classList.add("hs-strip");
    // idempotent: reuse buttons that survived a swap / history restore
    if (!wrap.querySelector(":scope > .hs-btn--l")) wrap.insertBefore(makeBtn(-1), strip);
    if (!wrap.querySelector(":scope > .hs-btn--r")) wrap.appendChild(makeBtn(1));
    return wrap;
  }

  function isTall(strip) { return strip.clientHeight > TALL; }

  function update(strip) {
    if (!strip.isConnected) return;
    var wrap = wrapOf(strip);
    var over = overflows(strip);
    if (!wrap) {
      if (!over || !scrollsX(strip)) return;        // fits: leave it exactly as it is
      wrap = ensureWrap(strip);
    }
    var canL = over && strip.scrollLeft > EDGE;
    var canR = over && strip.scrollLeft + strip.clientWidth < strip.scrollWidth - EDGE;
    strip.classList.toggle("hs-can-l", canL);
    strip.classList.toggle("hs-can-r", canR);
    wrap.classList.toggle("hs-wrap--tall", isTall(strip));
    var l = wrap.querySelector(":scope > .hs-btn--l"), r = wrap.querySelector(":scope > .hs-btn--r");
    if (l) l.classList.toggle("is-on", canL);
    if (r) r.classList.toggle("is-on", canR);
    if (canR && r) hintOnce(strip, r);
  }

  /* "More →" pulse: once per strip, per page, per browser session. */
  function hintOnce(strip, btn) {
    if (strip.__hsHinted || reduceMotion) return;
    strip.__hsHinted = true;
    var key = "nbte.hs.hint:" + window.location.pathname + ":" + (strip.className || "").replace(/\s*hs-[\w-]+/g, "").trim().split(/\s+/).slice(0, 2).join(".");
    try {
      if (window.sessionStorage.getItem(key)) return;
      window.sessionStorage.setItem(key, "1");
    } catch (e) {}
    window.setTimeout(function () {
      if (!btn.isConnected || !btn.classList.contains("is-on")) return;
      btn.classList.add("is-hint");
      window.setTimeout(function () { btn.classList.remove("is-hint"); }, 3000);
    }, 500);
  }

  function scrollStrip(strip, left, instant) {
    left = Math.max(0, Math.min(left, strip.scrollWidth - strip.clientWidth));
    if (instant || reduceMotion || !strip.scrollTo) strip.scrollLeft = left;
    else strip.scrollTo({ left: left, behavior: "smooth" });
  }

  /* Keep the active item in view (only scrolls when it is cut off). */
  function reveal(strip, item, instant) {
    if (!item || !overflows(strip)) return;
    var sr = strip.getBoundingClientRect(), ir = item.getBoundingClientRect();
    var pad = 44;   // room for the chevron + fade
    if (ir.left >= sr.left + pad - 1 && ir.right <= sr.right - pad + 1) return;
    var first = ir.left - sr.left + strip.scrollLeft;
    scrollStrip(strip, first - (strip.clientWidth - ir.width) / 2, instant);
  }
  function activeOf(strip) {
    var list = strip.querySelectorAll(ACTIVE);
    for (var i = 0; i < list.length; i++) if (list[i].offsetParent !== null) return list[i];
    return null;
  }

  function bind(strip) {
    if (strip.__hsBound) return;       // a JS property, so restored / swapped-in markup is bound afresh
    strip.__hsBound = true;
    strip.addEventListener("scroll", function () { update(strip); }, { passive: true });
    if (ro) ro.observe(strip);

    /* vertical wheel -> horizontal, only for strips (not boards) and only
       while there is somewhere left to go, so the page still scrolls. */
    strip.addEventListener("wheel", function (e) {
      if (e.ctrlKey || e.shiftKey || !overflows(strip) || isTall(strip)) return;
      if (Math.abs(e.deltaY) <= Math.abs(e.deltaX)) return;
      var max = strip.scrollWidth - strip.clientWidth;
      var d = e.deltaMode === 1 ? e.deltaY * 32 : e.deltaY;
      if ((d < 0 && strip.scrollLeft <= 0) || (d > 0 && strip.scrollLeft >= max - 1)) return;
      e.preventDefault();
      strip.scrollLeft += d;
    }, { passive: false });

    /* drag to scroll (mouse only; touch already swipes) */
    var drag = null;
    strip.addEventListener("pointerdown", function (e) {
      if (e.pointerType !== "mouse" || e.button !== 0 || !overflows(strip) || isTall(strip)) return;
      if (e.target.closest && e.target.closest('input, select, textarea, [contenteditable], [draggable="true"]')) return;
      drag = { x: e.clientX, left: strip.scrollLeft, moved: false, id: e.pointerId };
    });
    strip.addEventListener("pointermove", function (e) {
      if (!drag || e.pointerId !== drag.id) return;
      var dx = e.clientX - drag.x;
      if (!drag.moved) {
        if (Math.abs(dx) < 6) return;
        drag.moved = true;
        strip.classList.add("hs-dragging");
        try { strip.setPointerCapture(drag.id); } catch (err) {}
      }
      strip.scrollLeft = drag.left - dx;
    });
    function endDrag(e) {
      if (!drag || (e && e.pointerId !== drag.id)) return;
      var moved = drag.moved;
      try { strip.releasePointerCapture(drag.id); } catch (err) {}
      drag = null;
      strip.classList.remove("hs-dragging");
      if (moved) {   // swallow the click that ends a drag
        var stop = function (ev) { ev.preventDefault(); ev.stopPropagation(); };
        strip.addEventListener("click", stop, { capture: true, once: true });
        window.setTimeout(function () { strip.removeEventListener("click", stop, true); }, 80);
      }
    }
    strip.addEventListener("pointerup", endDrag);
    strip.addEventListener("pointercancel", endDrag);
    // links / images inside must not start a native drag while we scroll
    strip.addEventListener("dragstart", function (e) {
      if (drag && !(e.target.closest && e.target.closest('[draggable="true"]'))) e.preventDefault();
    });
  }

  function enhanceAll(revealActive) {
    var strips = document.querySelectorAll(STRIPS);
    for (var i = 0; i < strips.length; i++) {
      var s = strips[i];
      if (s.closest("[data-hscroll-off]")) continue;
      var wrap = wrapOf(s);
      if (wrap) { ensureWrap(s); syncBox(s, wrap); }
      bind(s);
      update(s);
      if (revealActive) reveal(s, activeOf(s), true);
    }
  }

  // chevrons: delegated, so they keep working after HTMX swaps / history restores
  document.addEventListener("click", function (e) {
    var btn = e.target.closest && e.target.closest("[data-hs-btn]");
    if (btn) {
      var strip = btn.parentElement && btn.parentElement.querySelector(":scope > .hs-strip");
      if (!strip) return;
      e.preventDefault();
      btn.classList.remove("is-hint");
      scrollStrip(strip, strip.scrollLeft + Number(btn.getAttribute("data-hs-btn")) * Math.max(120, strip.clientWidth * 0.8));
      return;
    }
    // a tab that was half-hidden slides fully into view when chosen
    var item = e.target.closest && e.target.closest("a, button, [role=tab]");
    var host = item && item.closest(".hs-strip");
    if (host && host.contains(item) && !isTall(host)) window.setTimeout(function () { reveal(host, item); }, 0);
  });

  /* ====================================================================
     3. Tab strips that follow ?tab=  (nav[data-tabsync="tab"])
     ==================================================================== */
  function tabKey(a, param) {
    var k = a.getAttribute("data-tab");
    if (k) return k;
    try { return new URL(a.getAttribute("href"), window.location.href).searchParams.get(param); } catch (e) { return null; }
  }
  function setActiveTab(nav, key) {
    var param = nav.getAttribute("data-tabsync") || "tab";
    var links = nav.querySelectorAll("a[data-tab], a[href]");
    var hit = null, i;
    for (i = 0; i < links.length; i++) if (tabKey(links[i], param) === key) { hit = links[i]; break; }
    if (!hit) return null;      // unknown key: keep what the server marked
    for (i = 0; i < links.length; i++) {
      var on = links[i] === hit;
      links[i].classList.toggle("is-active", on);
      if (on) links[i].setAttribute("aria-current", "page"); else links[i].removeAttribute("aria-current");
    }
    return hit;
  }
  function tabFromUrl(nav, url) {
    var param = nav.getAttribute("data-tabsync") || "tab";
    var key = null;
    try { key = new URL(url || window.location.href, window.location.href).searchParams.get(param); } catch (e) {}
    return key || nav.getAttribute("data-tabsync-default") || null;
  }
  function syncTabs(url) {
    var navs = document.querySelectorAll("nav[data-tabsync]");
    for (var i = 0; i < navs.length; i++) {
      var key = tabFromUrl(navs[i], url);
      var hit = key && setActiveTab(navs[i], key);
      if (hit && navs[i].classList.contains("hs-strip")) reveal(navs[i], hit);
    }
  }
  // 1. on click: move the highlight straight away
  document.addEventListener("click", function (e) {
    if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    var a = e.target.closest && e.target.closest("nav[data-tabsync] a");
    if (!a) return;
    var nav = a.closest("nav[data-tabsync]");
    var key = tabKey(a, nav.getAttribute("data-tabsync") || "tab");
    if (key) setActiveTab(nav, key);
  });
  // 2. after the section is swapped in: trust the URL that was actually fetched
  document.addEventListener("htmx:afterSwap", function (e) {
    var elt = e.detail && e.detail.elt, src = e.detail && e.detail.requestConfig && e.detail.requestConfig.elt;
    var a = (src && src.closest && src.closest("nav[data-tabsync] a")) || (elt && elt.closest && elt.closest("nav[data-tabsync] a"));
    if (!a) return;
    var path = e.detail.pathInfo && (e.detail.pathInfo.finalRequestPath || e.detail.pathInfo.requestPath);
    if (path) syncTabs(path);
  });
  // 3. URL changed (push), Back / Forward, restored snapshot: read ?tab from the address bar
  ["htmx:pushedIntoHistory", "htmx:replacedInHistory", "htmx:historyRestore"].forEach(function (name) {
    document.addEventListener(name, function () { syncTabs(); });
  });
  window.addEventListener("popstate", function () { window.setTimeout(syncTabs, 0); });
  window.addEventListener("pageshow", function () { syncTabs(); });
  // 4. the request failed: fall back to the tab the page is really on
  ["htmx:responseError", "htmx:sendError", "htmx:timeout", "htmx:swapError"].forEach(function (name) {
    document.addEventListener(name, function (e) {
      var elt = e.detail && e.detail.elt;
      if (elt && elt.closest && elt.closest("nav[data-tabsync]")) syncTabs();
    });
  });

  /* ====================================================================
     2. Reminder corner card
     ==================================================================== */
  var RC_KEY = "nbte.reminders.v1";
  var RC_FIRST = 120000;                 // first showing: 2 minutes
  var RC_AGAIN = 20000;                  // later showings: 20 seconds
  var RC_MIN = 6 * 60000, RC_MAX = 15 * 60000;   // comes back after a random 6–15 minutes
  var RC_STALE = 8 * 3600000;            // a new working session starts over
  var rc = { card: null, tick: null, wake: null, paused: false, left: 0, dur: 0, last: 0 };

  function rcRead() {
    try { return JSON.parse(window.localStorage.getItem(RC_KEY) || "null"); } catch (e) { return null; }
  }
  function rcWrite(s) {
    rc.mem = s;     // still works for this page when storage is blocked
    try { window.localStorage.setItem(RC_KEY, JSON.stringify(s)); } catch (e) {}
  }
  function rcState() { return rcRead() || rc.mem || null; }
  function rcRandom() { return RC_MIN + Math.floor(Math.random() * (RC_MAX - RC_MIN)); }

  function rcRing() {
    var ring = rc.card && rc.card.querySelector("[data-rc-ring]");
    if (ring) ring.style.strokeDashoffset = String(100 - Math.max(0, Math.min(100, rc.dur ? (rc.left / rc.dur) * 100 : 0)));
  }
  /* Tell the page how much room the card takes (--rc-clear: bottom space for
     the page, ux-affordance.css) and let other floating widgets move away. */
  function rcClear() {
    var card = rc.card, h = 0;
    if (card && card.isConnected && !card.hidden && !card.classList.contains("is-leaving")) h = Math.ceil(card.offsetHeight) + 16;
    var root = document.documentElement;
    if (h) root.style.setProperty("--rc-clear", h + "px"); else root.style.removeProperty("--rc-clear");
    root.classList.toggle("rc-on", !!h);
    try { document.dispatchEvent(new CustomEvent("nbte:floaters")); } catch (e) {}
  }
  function rcStopTimers() {
    window.clearInterval(rc.tick); rc.tick = null;
    window.clearTimeout(rc.wake); rc.wake = null;
  }
  function rcShow(left, dur) {
    var card = rc.card;
    rcStopTimers();
    rc.left = left; rc.dur = dur; rc.last = Date.now();
    card.classList.remove("is-leaving");
    if (card.hidden) card.hidden = false;
    rcRing();
    rcClear();
    rc.tick = window.setInterval(function () {
      var now = Date.now(), gone = now - rc.last;
      rc.last = now;
      if (rc.paused) return;
      rc.left -= gone;
      rcRing();
      if (rc.left <= 0) rcHide();
    }, 250);
  }
  function rcHide() {
    var card = rc.card;
    rcStopTimers();
    var s = rcState() || {};
    s.sig = card.getAttribute("data-rc-sig"); s.until = 0; s.dur = 0; s.next = Date.now() + rcRandom(); s.seen = Date.now();
    rcWrite(s);
    if (!card.hidden) {
      if (card.contains(document.activeElement)) { try { document.activeElement.blur(); } catch (e) {} }
      card.classList.add("is-leaving");
      window.setTimeout(function () { card.hidden = true; card.classList.remove("is-leaving"); rcClear(); }, reduceMotion ? 0 : 220);
    }
    rcClear();
    rcSchedule(s.next - Date.now());
  }
  function rcSchedule(ms) {
    window.clearTimeout(rc.wake);
    rc.wake = window.setTimeout(rcEvaluate, Math.max(250, Math.min(ms, 2147000000)));
  }
  function rcPause(on) {
    if (!rc.card || rc.paused === on) return;
    rc.paused = on;
    rc.card.classList.toggle("is-paused", on);
    if (!on && !rc.card.hidden) {       // resume: the deadline moves by however long it was held
      rc.last = Date.now();
      var s = rcState() || {};
      s.until = Date.now() + rc.left; s.seen = Date.now();
      rcWrite(s);
    }
  }
  /* Decide from the saved timestamps what this page should do now. */
  function rcEvaluate() {
    var card = rc.card;
    if (!card || !card.isConnected) return;
    var now = Date.now(), sig = card.getAttribute("data-rc-sig"), s = rcState();
    if (!s || s.sig !== sig || !s.seen || now - s.seen > RC_STALE) {
      // first page of a session, another user, or the list of items changed
      s = { sig: sig, until: now + RC_FIRST, dur: RC_FIRST, next: 0, seen: now };
      rcWrite(s);
    }
    if (s.until && now < s.until) {                       // still inside a showing (carried across pages)
      rcShow(s.until - now, s.dur || RC_FIRST);
    } else if (s.next && now < s.next) {                  // waiting to come back
      rcStopTimers();
      if (!card.hidden) card.hidden = true;
      rcClear();
      rcSchedule(s.next - now);
    } else if (s.next) {                                  // time to come back, briefly
      s.until = now + RC_AGAIN; s.dur = RC_AGAIN; s.next = 0; s.seen = now;
      rcWrite(s);
      rcShow(RC_AGAIN, RC_AGAIN);
    } else {                                              // a showing ran out while no page was open
      s.until = 0; s.next = now + rcRandom(); s.seen = now;
      rcWrite(s);
      if (!card.hidden) card.hidden = true;
      rcClear();
      rcSchedule(s.next - now);
    }
  }
  function rcInit() {
    var card = document.querySelector("[data-reminder-card]");
    if (!card) { rcStopTimers(); rc.card = null; rcClear(); return; }
    if (card.__rcBound) return;
    card.__rcBound = true;
    rcStopTimers();
    rc.card = card; rc.paused = false;
    card.addEventListener("mouseenter", function () { rcPause(true); });
    card.addEventListener("mouseleave", function () { if (!card.contains(document.activeElement)) rcPause(false); });
    card.addEventListener("focusin", function () { rcPause(true); });
    card.addEventListener("focusout", function (e) {
      if (!card.contains(e.relatedTarget) && !card.matches(":hover")) rcPause(false);
    });
    card.addEventListener("click", function (e) {
      if (e.target.closest && e.target.closest("[data-rc-close]")) { rc.paused = false; card.classList.remove("is-paused"); rcHide(); }
    });
    card.addEventListener("keydown", function (e) {
      if (e.key === "Escape") { rc.paused = false; card.classList.remove("is-paused"); rcHide(); }
    });
    rcEvaluate();
  }
  // another tab closed / re-opened the card: follow it
  window.addEventListener("storage", function (e) {
    if (e.key === RC_KEY && rc.card && !rc.paused) rcEvaluate();
  });

  /* ====================================================================
     boot
     ==================================================================== */
  function init() { enhanceAll(true); syncTabs(); rcInit(); }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
  window.addEventListener("load", function () { enhanceAll(false); });
  window.addEventListener("resize", debounce(function () { enhanceAll(false); rcClear(); }, 120));
  document.addEventListener("htmx:afterSettle", function () { enhanceAll(true); rcInit(); });
  document.addEventListener("htmx:historyRestore", function () { enhanceAll(true); rcInit(); });
  if (document.fonts && document.fonts.ready && document.fonts.ready.then) {
    document.fonts.ready.then(function () { enhanceAll(false); });
  }

  window.NBTEAffordance = { refresh: function () { enhanceAll(true); }, syncTabs: syncTabs };
})();
