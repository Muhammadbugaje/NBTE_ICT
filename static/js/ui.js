/* ==========================================================================
   NBTE ICT — ui.js (v1, UI overhaul)

   Interaction primitives on window.NBTE. Loaded after app.js on every page.
   app.js keeps its existing objects (NBTE.Theme, NBTE.Toast, NBTE.Modal,
   NBTE.Dropdown, NBTE.Sidebar …); this module adds the lower-case
   primitives below and wires them to the markup automatically.

   Every primitive is:
     · idempotent  — calling it twice on the same element does nothing new
     · accessible  — keyboard operable, ARIA states kept in sync
     · motion-safe — honours prefers-reduced-motion (and html[data-motion])
   No inline handlers: everything is attached with addEventListener.
   ========================================================================== */
(function () {
  "use strict";

  var NBTE = (window.NBTE = window.NBTE || {});
  if (NBTE.__uiReady) return;
  NBTE.__uiReady = true;

  /* ------------------------------------------------------------------ utils */
  var FOCUSABLE =
    'a[href], button:not([disabled]), input:not([disabled]):not([type="hidden"]), ' +
    'select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

  function reducedMotion() {
    if (/^(reduce|reduced|off)$/.test(document.documentElement.getAttribute("data-motion") || "")) return true;
    try { return window.matchMedia("(prefers-reduced-motion: reduce)").matches; }
    catch (e) { return false; }
  }

  function once(el, key) {
    if (!el) return false;
    var flag = "nbte" + key;
    if (el.dataset[flag] === "1") return false;
    el.dataset[flag] = "1";
    return true;
  }

  function $all(scope, sel) {
    return Array.prototype.slice.call((scope || document).querySelectorAll(sel));
  }

  function store(key, value) {
    try {
      if (value === undefined) return localStorage.getItem(key);
      if (value === null) localStorage.removeItem(key);
      else localStorage.setItem(key, value);
    } catch (e) { return null; }
    return value;
  }

  function trapFocus(container, e) {
    if (e.key !== "Tab") return;
    var items = $all(container, FOCUSABLE).filter(function (n) { return n.offsetParent !== null; });
    if (!items.length) { e.preventDefault(); return; }
    var first = items[0];
    var last = items[items.length - 1];
    if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
  }

  var scrollLocks = 0;
  function lockScroll(on) {
    scrollLocks = Math.max(0, scrollLocks + (on ? 1 : -1));
    document.documentElement.classList.toggle("is-scroll-locked", scrollLocks > 0);
  }

  /* ============================================================ dropdown */
  /**
   * NBTE.dropdown(el) — make a trigger + menu pair keyboard friendly.
   * Markup: <button data-dropdown-trigger="menu-id"> … <div id="menu-id"
   * data-dropdown-menu aria-hidden="true">. Opening/closing and the click
   * outside behaviour stay in app.js (NBTE.Dropdown); this adds focus
   * management: ArrowDown/Enter on the trigger focuses the first item,
   * arrow keys move between items, Escape returns focus to the trigger.
   * Animation: scale + fade from the trigger corner, 180ms (CSS).
   * @param {HTMLElement} el the trigger element
   */
  NBTE.dropdown = function (el) {
    if (!el || !once(el, "Dropdown")) return;
    var menu = document.getElementById(el.getAttribute("data-dropdown-trigger"));
    if (!menu) return;
    el.setAttribute("aria-haspopup", "true");
    el.setAttribute("aria-controls", menu.id);
    function items() {
      return $all(menu, "a[href], button:not([disabled]), [role='menuitem']")
        .filter(function (n) { return n.offsetParent !== null; });
    }
    function focusItem(i) {
      var list = items();
      if (!list.length) return;
      list[(i + list.length) % list.length].focus();
    }
    el.addEventListener("keydown", function (e) {
      if (e.key === "ArrowDown" && menu.getAttribute("aria-hidden") !== "false") {
        e.preventDefault();
        el.click();
        setTimeout(function () { focusItem(0); }, 30);
      }
    });
    menu.addEventListener("keydown", function (e) {
      var list = items();
      var i = list.indexOf(document.activeElement);
      if (e.key === "ArrowDown") { e.preventDefault(); focusItem(i + 1); }
      else if (e.key === "ArrowUp") { e.preventDefault(); focusItem(i - 1); }
      else if (e.key === "Escape") { el.focus(); }
      else if (e.key === "Tab") {
        if ((!e.shiftKey && i === list.length - 1) || (e.shiftKey && i === 0)) {
          if (NBTE.Dropdown) NBTE.Dropdown.closeAll();
        }
      }
    });
  };

  /* =============================================================== modal */
  /**
   * NBTE.modal(el) — controller for a `.modal-root` dialog.
   * Returns {open, close, toggle}. Opening fades the backdrop and scales the
   * dialog up (a bottom sheet on phones), traps focus, locks page scroll and
   * closes on Escape or a backdrop click. Focus returns to the opener.
   * Declarative use: <button data-modal-open="id">, <button data-modal-close>.
   * @param {HTMLElement|string} el the .modal-root element or its id
   */
  NBTE.modal = function (el) {
    if (typeof el === "string") el = document.getElementById(el);
    if (!el) return null;
    if (el.__nbteModal) return el.__nbteModal;
    var lastFocus = null;
    var dialog = el.querySelector(".modal, [role='dialog']") || el;
    if (!dialog.hasAttribute("role")) dialog.setAttribute("role", "dialog");
    dialog.setAttribute("aria-modal", "true");
    function onKey(e) {
      if (e.key === "Escape") { e.stopPropagation(); api.close(); }
      else trapFocus(dialog, e);
    }
    var api = {
      open: function () {
        if (el.getAttribute("aria-hidden") === "false") return;
        lastFocus = document.activeElement;
        el.setAttribute("aria-hidden", "false");
        el.hidden = false;
        lockScroll(true);
        document.addEventListener("keydown", onKey, true);
        setTimeout(function () {
          var first = dialog.querySelector(
            "input:not([type=hidden]):not([disabled]), textarea, select, button:not(.modal-close)"
          ) || dialog.querySelector(FOCUSABLE);
          if (first) { try { first.focus(); } catch (e) {} }
        }, 60);
        el.dispatchEvent(new CustomEvent("nbte:modal-open", { bubbles: true }));
      },
      close: function () {
        if (el.getAttribute("aria-hidden") === "true") return;
        el.setAttribute("aria-hidden", "true");
        lockScroll(false);
        document.removeEventListener("keydown", onKey, true);
        if (lastFocus && lastFocus.focus) { try { lastFocus.focus(); } catch (e) {} }
        el.dispatchEvent(new CustomEvent("nbte:modal-close", { bubbles: true }));
      },
      toggle: function () { el.getAttribute("aria-hidden") === "false" ? api.close() : api.open(); }
    };
    var backdrop = el.querySelector(".modal-backdrop");
    if (backdrop) backdrop.addEventListener("click", api.close);
    $all(el, ".modal-close, [data-modal-close]").forEach(function (b) {
      b.addEventListener("click", function (e) { e.preventDefault(); api.close(); });
    });
    el.__nbteModal = api;
    return api;
  };

  /* ============================================================== drawer */
  /**
   * NBTE.drawer(el, side) — slide-in panel (the sidebar on phones/tablets).
   * Toggle with any [data-drawer-toggle="id"] button. Slides with
   * translateX, fades a backdrop, traps focus while open, closes on Escape,
   * backdrop click, a link click inside it, or a swipe back toward its edge.
   * @param {HTMLElement} el   the drawer panel (e.g. .app-sidebar)
   * @param {"start"|"end"} [side="start"] edge it slides in from
   */
  NBTE.drawer = function (el, side) {
    if (!el || el.__nbteDrawer) return el && el.__nbteDrawer;
    side = side || "start";
    var shell = el.closest(".app-shell") || document.body;
    var OPEN = "sidebar-open";
    var lastFocus = null;
    var mq = window.matchMedia ? window.matchMedia("(min-width: 1024px)") : null;
    function isOpen() { return shell.classList.contains(OPEN); }
    function triggers() { return $all(document, '[data-drawer-toggle="' + el.id + '"]'); }
    function sync(open) {
      triggers().forEach(function (t) { t.setAttribute("aria-expanded", open ? "true" : "false"); });
      // Off-screen drawer: hide it from screen readers and the tab order.
      var hide = !open && !(mq && mq.matches);
      el.inert = hide;
      if (hide) el.setAttribute("aria-hidden", "true");
      else el.removeAttribute("aria-hidden");
    }
    function onKey(e) {
      if (e.key === "Escape") api.close();
      else trapFocus(el, e);
    }
    var api = {
      open: function () {
        if (isOpen()) return;
        lastFocus = document.activeElement;
        shell.classList.add(OPEN);
        sync(true);
        lockScroll(true);
        document.addEventListener("keydown", onKey);
        var target = el.querySelector(".app-nav-item.active") || el.querySelector(FOCUSABLE);
        if (target) setTimeout(function () { target.focus({ preventScroll: true }); }, 60);
      },
      close: function () {
        if (!isOpen()) return;
        shell.classList.remove(OPEN);
        sync(false);
        lockScroll(false);
        document.removeEventListener("keydown", onKey);
        if (lastFocus && lastFocus.focus) { try { lastFocus.focus(); } catch (e) {} }
      },
      toggle: function () { isOpen() ? api.close() : api.open(); }
    };
    el.__nbteDrawer = api;
    sync(false);
    if (mq && mq.addEventListener) mq.addEventListener("change", function () { api.close(); sync(false); });
    el.addEventListener("click", function (e) {
      if (e.target.closest("a[href]") && !(mq && mq.matches)) api.close();
    });
    var backdrop = shell.querySelector(".app-sidebar-backdrop");
    if (backdrop) backdrop.addEventListener("click", api.close);
    // Swipe toward the edge to close (touch only, passive listeners).
    var startX = null;
    el.addEventListener("touchstart", function (e) { startX = e.touches[0].clientX; }, { passive: true });
    el.addEventListener("touchend", function (e) {
      if (startX === null) return;
      var dx = e.changedTouches[0].clientX - startX;
      startX = null;
      if ((side === "start" && dx < -60) || (side === "end" && dx > 60)) api.close();
    }, { passive: true });
    // Keep the old global API working (NBTE.Sidebar.toggleMobile()).
    if (NBTE.Sidebar) NBTE.Sidebar.toggleMobile = api.toggle;
    return api;
  };

  /* =============================================================== toast */
  /**
   * NBTE.toast(msg, kind, opts) — non-blocking notification.
   * Slides in at the top right, stacks, auto-dismisses after 5s (8s for
   * errors), pauses on hover, closable. kind: success | warning | error |
   * info (aliases: warn, danger). Delegates to app.js NBTE.Toast.
   * @param {string} msg
   * @param {string} [kind="info"]
   * @param {{title?: string, duration?: number}} [opts]
   * @returns {Function} dismiss
   */
  NBTE.toast = function (msg, kind, opts) {
    opts = opts || {};
    var map = { warning: "warn", danger: "error", debug: "info" };
    kind = map[kind] || kind || "info";
    if (!NBTE.Toast) return function () {};
    var title = opts.title || msg;
    var body = opts.title ? msg : "";
    return NBTE.Toast.push(kind, title, body, opts);
  };

  /* ============================================================== ripple */
  /**
   * NBTE.ripple(button) — material-style ripple from the pointer position.
   * A radial circle expands and fades (320ms). Applied automatically to every
   * <button> and a.btn through one delegated pointerdown listener; call it
   * directly only for custom elements. Skipped with reduced motion.
   * @param {HTMLElement} button
   * @param {PointerEvent} [evt]
   */
  NBTE.ripple = function (button, evt) {
    if (!button || reducedMotion() || button.hasAttribute("data-no-ripple")) return;
    if (button.disabled || button.getAttribute("aria-disabled") === "true") return;
    var cs = window.getComputedStyle(button);
    if (cs.display === "inline" || cs.display === "contents") return;
    if (cs.position === "static") button.classList.add("ripple-anchor");
    var host = button.querySelector(":scope > .ripple-host");
    if (!host) {
      host = document.createElement("span");
      host.className = "ripple-host";
      host.setAttribute("aria-hidden", "true");
      button.appendChild(host);
    }
    var rect = button.getBoundingClientRect();
    var size = Math.max(rect.width, rect.height);
    var x = evt && evt.clientX ? evt.clientX - rect.left : rect.width / 2;
    var y = evt && evt.clientY ? evt.clientY - rect.top : rect.height / 2;
    var dot = document.createElement("span");
    dot.className = "ripple";
    dot.style.width = dot.style.height = size + "px";
    dot.style.left = (x - size / 2) + "px";
    dot.style.top = (y - size / 2) + "px";
    host.appendChild(dot);
    dot.addEventListener("animationend", function () { dot.remove(); });
  };

  /* ============================================================ collapse */
  /**
   * NBTE.collapse(el) — accordion / collapsible section.
   * el is the trigger: <button data-collapse-trigger="panel-id"
   * aria-expanded="false">; the panel gets .collapse-body. Height animates
   * via max-height measured from scrollHeight; aria-expanded/aria-hidden kept
   * in sync. Returns {open, close, toggle}.
   * @param {HTMLElement} el
   */
  NBTE.collapse = function (el) {
    if (!el) return null;
    if (el.__nbteCollapse) return el.__nbteCollapse;
    var panel = document.getElementById(el.getAttribute("data-collapse-trigger") || el.getAttribute("aria-controls"));
    if (!panel) return null;
    panel.classList.add("collapse-body");
    el.setAttribute("aria-controls", panel.id);
    function set(open) {
      el.setAttribute("aria-expanded", open ? "true" : "false");
      panel.setAttribute("aria-hidden", open ? "false" : "true");
      panel.style.maxHeight = open ? panel.scrollHeight + "px" : "0px";
      if (open) {
        var done = function () {
          if (el.getAttribute("aria-expanded") === "true") panel.style.maxHeight = "none";
          panel.removeEventListener("transitionend", done);
        };
        panel.addEventListener("transitionend", done);
        if (reducedMotion()) done();
      }
    }
    var api = {
      open: function () { set(true); },
      close: function () {
        panel.style.maxHeight = panel.scrollHeight + "px";
        void panel.offsetHeight;   // start the transition from the real height
        set(false);
      },
      toggle: function () { el.getAttribute("aria-expanded") === "true" ? api.close() : api.open(); }
    };
    el.addEventListener("click", function (e) { e.preventDefault(); api.toggle(); });
    set(el.getAttribute("aria-expanded") === "true");
    el.__nbteCollapse = api;
    return api;
  };

  /* ================================================================ tabs */
  /**
   * NBTE.tabs(container) — tab switching with a sliding underline.
   * Markup: [role=tablist] containing [role=tab][aria-controls=panelId];
   * panels are [role=tabpanel]. Arrow keys / Home / End move between tabs
   * (roving tabindex). The .tabs-indicator translates to the active tab.
   * @param {HTMLElement} container the [role=tablist] element
   */
  NBTE.tabs = function (container) {
    if (!container || !once(container, "Tabs")) return;
    var tabs = $all(container, "[role='tab']");
    if (!tabs.length) return;
    var indicator = container.querySelector(".tabs-indicator");
    if (!indicator) {
      indicator = document.createElement("span");
      indicator.className = "tabs-indicator";
      indicator.setAttribute("aria-hidden", "true");
      container.appendChild(indicator);
    }
    function moveIndicator(tab) {
      indicator.style.width = tab.offsetWidth + "px";
      indicator.style.transform = "translateX(" + tab.offsetLeft + "px)";
    }
    function select(tab, focus) {
      tabs.forEach(function (t) {
        var on = t === tab;
        t.setAttribute("aria-selected", on ? "true" : "false");
        t.tabIndex = on ? 0 : -1;
        var p = document.getElementById(t.getAttribute("aria-controls"));
        if (p) p.hidden = !on;
      });
      moveIndicator(tab);
      if (focus) tab.focus();
    }
    tabs.forEach(function (t, i) {
      t.addEventListener("click", function () { select(t); });
      t.addEventListener("keydown", function (e) {
        var n = null;
        if (e.key === "ArrowRight") n = tabs[(i + 1) % tabs.length];
        else if (e.key === "ArrowLeft") n = tabs[(i - 1 + tabs.length) % tabs.length];
        else if (e.key === "Home") n = tabs[0];
        else if (e.key === "End") n = tabs[tabs.length - 1];
        if (n) { e.preventDefault(); select(n, true); }
      });
    });
    var current = tabs.filter(function (t) { return t.getAttribute("aria-selected") === "true"; })[0] || tabs[0];
    select(current);
    window.addEventListener("resize", function () {
      var cur = container.querySelector("[aria-selected='true']");
      if (cur) moveIndicator(cur);
    });
  };

  /* ========================================================= searchPanel */
  /**
   * NBTE.searchPanel(input) — the topbar search overlay.
   * The search behaviour itself lives in search.js (NBTE.Search). This
   * primitive adds the presentation: the overlay gets .is-open (input
   * widens, page dims, results fade in — see search.css) and the trigger's
   * aria-expanded follows. Safe to call twice.
   * @param {HTMLInputElement} input the overlay's search input
   */
  NBTE.searchPanel = function (input) {
    if (!input || !once(input, "Search")) return;
    var overlay = input.closest(".app-search-overlay");
    if (!overlay) return;
    var trigger = document.querySelector("[data-search-trigger]");
    if (trigger) trigger.setAttribute("aria-controls", overlay.id);
    var obs = new MutationObserver(function () {
      var open = overlay.getAttribute("aria-hidden") === "false";
      overlay.classList.toggle("is-open", open);
      if (trigger) trigger.setAttribute("aria-expanded", open ? "true" : "false");
    });
    obs.observe(overlay, { attributes: true, attributeFilter: ["aria-hidden"] });
    window.addEventListener("pagehide", function () { obs.disconnect(); }, { once: true });
  };

  /* ======================================================= notifications */
  /**
   * NBTE.notifications(btn) — bell feedback.
   * Watches the unread badge (#notif-badge, updated by notifications.js).
   * When the count goes up the badge pops and the bell wobbles once; the
   * panel itself slides down when opened (CSS). No polling of its own.
   * @param {HTMLElement} btn the bell button (#notif-bell)
   */
  NBTE.notifications = function (btn) {
    if (!btn || !once(btn, "Bell")) return;
    var badge = btn.querySelector(".notif-badge") || document.getElementById("notif-badge");
    if (!badge) return;
    var last = parseInt(badge.textContent, 10) || 0;
    var obs = new MutationObserver(function () {
      var n = parseInt(badge.textContent, 10) || 0;
      if (badge.hidden) n = 0;
      if (n > last) {
        badge.classList.remove("is-popping");
        btn.classList.remove("is-ringing");
        void badge.offsetWidth;
        badge.classList.add("is-popping");
        btn.classList.add("is-ringing");
        setTimeout(function () { btn.classList.remove("is-ringing"); badge.classList.remove("is-popping"); }, 700);
      }
      btn.setAttribute("aria-label", n ? "Notifications, " + n + " unread" : "Notifications");
      last = n;
    });
    obs.observe(badge, { childList: true, characterData: true, subtree: true, attributes: true, attributeFilter: ["hidden"] });
    window.addEventListener("pagehide", function () { obs.disconnect(); }, { once: true });
  };

  /* ============================================================ carousel */
  /**
   * NBTE.carousel(el) — hero / sign-in background carousel.
   * Markup: [data-carousel] > .carousel-track > .carousel-slide (one is
   * .is-active). Options as attributes: data-interval (ms, default 8000),
   * data-effect="fade|slide", data-pause-on-hover. Cross-fades with a slow
   * Ken Burns zoom; dots, ←/→ keys and touch swipe navigate. Pauses when the
   * tab is hidden. With reduced motion it stays on the first slide.
   * @param {HTMLElement} el
   * @returns {{go: Function, next: Function, prev: Function, pause: Function, play: Function}|null}
   */
  NBTE.carousel = function (el) {
    if (!el) return null;
    if (el.__nbteCarousel) return el.__nbteCarousel;
    var slides = $all(el, ".carousel-slide");
    if (slides.length < 2) { if (slides[0]) slides[0].classList.add("is-active"); return null; }
    var interval = parseInt(el.getAttribute("data-interval"), 10) || 8000;
    var index = Math.max(0, slides.findIndex(function (s) { return s.classList.contains("is-active"); }));
    var timer = null;
    var paused = false;
    var userPaused = false;
    var dotsWrap = el.querySelector(".carousel-dots");
    var dots = [];
    if (dotsWrap) {
      dotsWrap.setAttribute("role", "group");
      dotsWrap.setAttribute("aria-label", "Choose slide");
      slides.forEach(function (s, i) {
        var d = document.createElement("button");
        d.type = "button";
        d.className = "carousel-dot";
        d.setAttribute("aria-label", "Show image " + (i + 1) + " of " + slides.length);
        d.addEventListener("click", function () { go(i); restart(); });
        dotsWrap.appendChild(d);
        dots.push(d);
      });
      // Auto-rotating content needs a pause control (WCAG 2.2.2).
      var tog = document.createElement("button");
      tog.type = "button";
      tog.className = "carousel-toggle";
      tog.setAttribute("aria-label", "Pause slideshow");
      tog.innerHTML = '<svg aria-hidden="true" width="12" height="12" viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="5" width="4" height="14" rx="1"/><rect x="14" y="5" width="4" height="14" rx="1"/></svg>';
      tog.addEventListener("click", function () {
        userPaused = !userPaused;
        tog.setAttribute("aria-label", userPaused ? "Play slideshow" : "Pause slideshow");
        tog.classList.toggle("is-paused", userPaused);
        userPaused ? stop() : start();
      });
      dotsWrap.appendChild(tog);
    }
    function go(i) {
      var prev = index;
      index = (i + slides.length) % slides.length;
      slides.forEach(function (s, n) {
        s.classList.toggle("is-active", n === index);
        s.classList.toggle("is-prev", n === prev && n !== index);
        s.setAttribute("aria-hidden", n === index ? "false" : "true");
      });
      dots.forEach(function (d, n) { d.setAttribute("aria-current", n === index ? "true" : "false"); });
    }
    function next() { go(index + 1); }
    function prev() { go(index - 1); }
    function stop() { if (timer) { clearInterval(timer); timer = null; } }
    function start() { stop(); if (!paused && !userPaused && !reducedMotion() && !document.hidden) timer = setInterval(next, interval); }
    function restart() { start(); }
    go(index);
    if (el.hasAttribute("data-pause-on-hover")) {
      el.addEventListener("mouseenter", function () { paused = true; stop(); });
      el.addEventListener("mouseleave", function () { paused = false; start(); });
    }
    el.addEventListener("focusin", function () { paused = true; stop(); });
    el.addEventListener("focusout", function () { paused = false; start(); });
    if (!el.hasAttribute("tabindex") && el.hasAttribute("data-keyboard")) el.tabIndex = 0;
    el.addEventListener("keydown", function (e) {
      if (e.key === "ArrowRight") { next(); restart(); }
      else if (e.key === "ArrowLeft") { prev(); restart(); }
    });
    var sx = null;
    el.addEventListener("touchstart", function (e) { sx = e.touches[0].clientX; }, { passive: true });
    el.addEventListener("touchend", function (e) {
      if (sx === null) return;
      var dx = e.changedTouches[0].clientX - sx;
      sx = null;
      if (Math.abs(dx) > 45) { dx < 0 ? next() : prev(); restart(); }
    }, { passive: true });
    document.addEventListener("visibilitychange", function () { document.hidden ? stop() : start(); });
    window.addEventListener("pagehide", stop);
    start();
    var api = { go: go, next: next, prev: prev, pause: function () { paused = true; stop(); }, play: function () { paused = false; start(); } };
    el.__nbteCarousel = api;
    return api;
  };

  /* ========================================================== lazyImages */
  var lazyObserver = null;
  /**
   * NBTE.lazyImages(scope) — lazy-load and fade in images.
   * <img data-src="…"> loads when it nears the viewport; <img data-lazy>
   * (already with src and loading="lazy") just fades in once loaded.
   * Opacity 0 → 1 (CSS, 320ms).
   * @param {ParentNode} [scope=document]
   */
  NBTE.lazyImages = function (scope) {
    var imgs = $all(scope, "img[data-src], img[data-lazy]").filter(function (img) { return once(img, "Lazy"); });
    if (!imgs.length) return;
    function reveal(img) {
      if (img.dataset.src) { img.src = img.dataset.src; img.removeAttribute("data-src"); }
      if (img.complete && img.naturalWidth) img.classList.add("is-loaded");
      else {
        img.addEventListener("load", function () { img.classList.add("is-loaded"); }, { once: true });
        img.addEventListener("error", function () { img.classList.add("is-loaded"); }, { once: true });
      }
    }
    imgs.forEach(function (img) { img.setAttribute("data-lazy", ""); });
    if (!("IntersectionObserver" in window)) { imgs.forEach(reveal); return; }
    if (!lazyObserver) {
      lazyObserver = new IntersectionObserver(function (entries) {
        entries.forEach(function (en) {
          if (en.isIntersecting) { lazyObserver.unobserve(en.target); reveal(en.target); }
        });
      }, { rootMargin: "200px 0px" });
    }
    imgs.forEach(function (img) { lazyObserver.observe(img); });
  };

  /* ========================================================= scrollReveal */
  var revealSweep = null;
  var revealObserver = null;
  /**
   * NBTE.scrollReveal(scope) — reveal sections as they scroll into view.
   * Targets existing .reveal elements plus the children of card grids
   * ([data-reveal] containers, .grid, dashboard/home tile grids). Each
   * element rises 12px and fades in once (CSS .reveal → .in). Content is
   * only hidden after JS runs, so nothing disappears without JS.
   * @param {ParentNode} [scope=document]
   */
  NBTE.scrollReveal = function (scope) {
    var sel = ".reveal, [data-reveal] > *, .grid > .card, .grid-2 > *, .grid-3 > *, .grid-4 > *";
    var els = $all(scope, sel).filter(function (el) {
      return !el.classList.contains("in") && once(el, "Reveal");
    });
    if (!els.length) return;
    if (reducedMotion() || !("IntersectionObserver" in window)) {
      els.forEach(function (el) { el.classList.add("reveal", "in"); });
      return;
    }
    if (!revealObserver) {
      revealObserver = new IntersectionObserver(function (entries) {
        entries.forEach(function (en) {
          if (en.isIntersecting) { en.target.classList.add("in"); revealObserver.unobserve(en.target); }
        });
      }, { rootMargin: "0px 0px -6% 0px", threshold: 0.05 });
    }
    els.forEach(function (el) {
      var r = el.getBoundingClientRect();
      // Already on screen at load: show without animating (no flash).
      if (r.top < window.innerHeight && r.bottom > 0 && document.readyState !== "complete") {
        el.classList.add("reveal", "in");
        return;
      }
      el.classList.add("reveal");
      revealObserver.observe(el);
    });
    // IntersectionObserver only reports *changes*: an element jumped over by
    // an anchor link or a fast fling never intersects and would stay hidden.
    // One rAF-throttled scroll listener reveals anything already passed.
    if (!revealSweep) {
      var ticking = false;
      revealSweep = function () {
        ticking = false;
        var vh = window.innerHeight;
        $all(document, ".reveal:not(.in)").forEach(function (el) {
          if (el.getBoundingClientRect().top < vh) {
            el.classList.add("in");
            if (revealObserver) revealObserver.unobserve(el);
          }
        });
      };
      window.addEventListener("scroll", function () {
        if (!ticking) { ticking = true; window.requestAnimationFrame(revealSweep); }
      }, { passive: true });
      window.addEventListener("hashchange", function () { window.requestAnimationFrame(revealSweep); });
    }
  };

  /* ========================================================= themeToggle */
  /**
   * NBTE.themeToggle(btn) — light/dark switch, persisted to localStorage.
   * Uses NBTE.Theme (app.js) so the stored key stays "nbte-theme". The
   * sun/moon icons morph via CSS. aria-pressed reflects dark mode.
   * @param {HTMLElement} btn
   */
  NBTE.themeToggle = function (btn) {
    if (!btn || !once(btn, "Theme")) return;
    // The theme can change without a click (the device switched light/dark,
    // or another tab). theme:change carries the new theme: use it, because
    // an animated switch only sets data-theme on the next frame.
    function sync(e) {
      var theme = e && e.detail && e.detail.theme;
      var dark = (theme || document.documentElement.getAttribute("data-theme")) === "dark";
      btn.setAttribute("aria-pressed", dark ? "true" : "false");
      btn.setAttribute("aria-label", dark ? "Switch to light theme" : "Switch to dark theme");
    }
    btn.addEventListener("click", function (e) {
      e.preventDefault();
      if (NBTE.Theme) NBTE.Theme.toggle();   // fires theme:change → sync
      else sync();
    });
    window.addEventListener("theme:change", sync);
    sync();
  };

  /* ====================================================== formValidation */
  /**
   * NBTE.formValidation(form) — inline validation and submit feedback.
   * · Fields the browser rejects get aria-invalid, a short shake and the
   *   browser's message shown under the field.
   * · Required fields that become valid get a green border.
   * · On submit the submit button shows a spinner and is disabled until the
   *   page unloads (or the htmx request finishes); restored on back/forward.
   * Forms with `novalidate` keep server-side validation: only the busy
   * state is added. Opt out entirely with data-no-enhance.
   * @param {HTMLFormElement} form
   */
  NBTE.formValidation = function (form) {
    if (!form || form.hasAttribute("data-no-enhance") || !once(form, "Form")) return;
    function messageFor(field) {
      var id = field.id ? field.id + "-nbte-error" : null;
      var holder = id ? document.getElementById(id) : null;
      if (!holder) {
        holder = document.createElement("span");
        holder.className = "field-error";
        if (id) holder.id = id;
        holder.setAttribute("aria-live", "polite");
        field.insertAdjacentElement("afterend", holder);
        if (id) field.setAttribute("aria-describedby", ((field.getAttribute("aria-describedby") || "") + " " + id).trim());
      }
      return holder;
    }
    form.addEventListener("invalid", function (e) {
      var f = e.target;
      f.setAttribute("aria-invalid", "true");
      if (!reducedMotion()) {
        f.classList.remove("is-shaking");
        void f.offsetWidth;
        f.classList.add("is-shaking");
      }
      messageFor(f).textContent = f.validationMessage;
    }, true);
    form.addEventListener("input", function (e) {
      var f = e.target;
      if (!f.willValidate) return;
      if (f.getAttribute("aria-invalid") === "true" && f.checkValidity()) {
        f.removeAttribute("aria-invalid");
        var m = f.id && document.getElementById(f.id + "-nbte-error");
        if (m) m.textContent = "";
      }
    });
    form.addEventListener("change", function (e) {
      var f = e.target;
      if (f.required && f.classList.contains("input")) f.classList.toggle("is-valid", !!f.value && f.checkValidity());
    });
    form.addEventListener("animationend", function (e) { e.target.classList.remove("is-shaking"); });
    form.addEventListener("submit", function (e) {
      var method = (form.getAttribute("method") || "get").toLowerCase();
      if (method !== "post" || form.hasAttribute("data-no-busy") || form.target) return;
      var btn = e.submitter || form.querySelector("button[type=submit], button:not([type]), input[type=submit]");
      if (!btn || btn.classList.contains("is-busy")) return;
      // Deferred: (1) the clicked button's name/value must still be sent,
      // (2) another handler (e.g. a confirmation dialog) may cancel the
      // submit — defaultPrevented is only final after dispatch.
      setTimeout(function () {
        if (e.defaultPrevented) return;
        NBTE.setBusy(btn, true, btn.getAttribute("data-busy-text") || form.getAttribute("data-busy-text"));
        // File downloads never unload the page; give the button back.
        setTimeout(function () { clearBusy(form); }, 8000);
      }, 0);
    });
  };
  /**
   * NBTE.setBusy(button, on, text) — the one busy state for buttons: disabled,
   * aria-busy, .is-busy (an on-brand arc, nbte-loader.css). With `text`
   * (or data-busy-text) the label becomes spinner + words, e.g. "Signing you in…";
   * the original content comes back when the busy state ends.
   */
  NBTE.setBusy = function (btn, on, text) {
    if (!btn) return;
    if (on) {
      if (btn.classList.contains("is-busy")) return;
      text = text || btn.getAttribute("data-busy-text");
      if (text && btn.tagName === "BUTTON" && !btn.__nbteBusyHTML) {
        btn.__nbteBusyHTML = btn.innerHTML;
        btn.classList.add("has-busy-text");
        btn.innerHTML = '<span class="nbte-busy"><span class="spinner spinner-sm" aria-hidden="true"></span><span></span></span>';
        btn.querySelector(".nbte-busy > span:last-child").textContent = text;
      }
      btn.classList.add("is-busy");
      btn.setAttribute("aria-busy", "true");
      btn.disabled = true;
    } else {
      if (btn.__nbteBusyHTML != null) { btn.innerHTML = btn.__nbteBusyHTML; btn.__nbteBusyHTML = null; }
      btn.classList.remove("is-busy", "has-busy-text");
      btn.removeAttribute("aria-busy");
      btn.disabled = false;
    }
  };
  function clearBusy(scope) {
    var root = scope || document;
    var list = $all(root, ".is-busy");
    if (root.classList && root.classList.contains("is-busy")) list.push(root);
    list.forEach(function (b) { NBTE.setBusy(b, false); });
  }
  NBTE.clearBusy = clearBusy;

  /* ======================================================= loadingOverlay */
  var overlayEl = null;
  /**
   * NBTE.loadingOverlay(show) — global blocking loader (the NBTE crest loader
   * + dimmed backdrop that fades in). Use for whole-page operations only.
   * Pass a string instead of true to set the status line.
   * @param {boolean|string} show
   */
  NBTE.loadingOverlay = function (show) {
    if (show) {
      if (overlayEl) return;
      overlayEl = document.createElement("div");
      overlayEl.className = "global-loader";
      overlayEl.setAttribute("role", "status");
      overlayEl.setAttribute("aria-live", "polite");
      // The NBTE crest loader from the page (partials/page_loader.html);
      // a plain on-brand arc if this page has none.
      var tpl = document.querySelector("#page-loader [data-nbte-loader]");
      if (tpl) {
        var loader = tpl.cloneNode(true);
        loader.classList.remove("nbte-loader--lg");
        var lbl = loader.querySelector("[data-loader-label]");
        if (lbl) lbl.textContent = typeof show === "string" ? show : "Please wait…";
        overlayEl.appendChild(loader);
      } else {
        overlayEl.innerHTML = '<span class="spinner spinner-lg" aria-hidden="true"></span><span class="sr-only">Loading…</span>';
      }
      document.body.appendChild(overlayEl);
    } else if (overlayEl) {
      overlayEl.remove();
      overlayEl = null;
    }
  };

  /* ======================================================= pageTransition */
  /**
   * NBTE.pageTransition() — fade between in-app navigations.
   * Browsers with cross-document View Transitions cross-fade whole pages via
   * CSS (@view-transition in animations.css). Others get a short fade/slide-in
   * of the main content on arrival. htmx swaps use document.startViewTransition
   * when available.
   */
  NBTE.pageTransition = function () {
    if (!once(document.documentElement, "Page")) return;
    var crossDoc = "onpagereveal" in window;
    var main = document.getElementById("app-content") || document.querySelector(".public-body main");
    if (main && !crossDoc && !reducedMotion()) {
      main.classList.add("is-entering");
      main.addEventListener("animationend", function () { main.classList.remove("is-entering"); }, { once: true });
    }
    if (window.htmx && document.startViewTransition && !reducedMotion()) {
      try { window.htmx.config.globalViewTransitions = true; } catch (e) {}
    }
  };

  /* ================================================== responsive tables */
  /**
   * Wraps every content table in .table-wrap (horizontal scroll with edge
   * shadows and a "swipe" hint on phones). Tables already inside a scroll
   * wrapper, marked data-no-wrap, or using .table--stack are left alone.
   * @param {ParentNode} [scope=document]
   */
  NBTE.responsiveTables = function (scope) {
    $all(scope, "table").forEach(function (t) {
      if (!once(t, "Table") || t.hasAttribute("data-no-wrap") || t.classList.contains("table--stack")) return;
      if (t.closest(".table-wrap, [data-no-wrap], .fc, .email-body")) return;
      // Tables whose cells carry data-label are designed to stack as cards
      // on phones; a scroll wrapper would defeat that.
      if (t.querySelector("td[data-label]")) return;
      var p = t.parentElement;
      if (p) {
        var ox = window.getComputedStyle(p).overflowX;
        if (ox === "auto" || ox === "scroll") return;
      }
      var wrap = document.createElement("div");
      wrap.className = "table-wrap";
      wrap.setAttribute("data-scroll-hint", "");
      wrap.setAttribute("tabindex", "0");
      wrap.setAttribute("role", "region");
      var cap = t.querySelector("caption");
      wrap.setAttribute("aria-label", cap ? cap.textContent.trim() : "Scrollable table");
      t.parentNode.insertBefore(wrap, t);
      wrap.appendChild(t);
      var update = function () {
        if (wrap.scrollWidth <= wrap.clientWidth + 2) wrap.removeAttribute("data-scroll-hint");
        else wrap.setAttribute("data-scroll-hint", "");
      };
      update();
      window.addEventListener("resize", update);
    });
  };

  /* =================================================== view mode switch */
  /**
   * Desktop / mobile layout override. html[data-view] = auto | desktop |
   * mobile, persisted in localStorage ("nbte-view"). Buttons:
   * [data-view-set="auto|desktop|mobile"].
   */
  NBTE.viewMode = {
    KEY: "nbte-view",
    get: function () { return store(this.KEY) || "auto"; },
    set: function (mode) {
      if (["auto", "desktop", "mobile"].indexOf(mode) === -1) mode = "auto";
      document.documentElement.setAttribute("data-view", mode);
      store(this.KEY, mode === "auto" ? null : mode);
      $all(document, "[data-view-set]").forEach(function (b) {
        b.setAttribute("aria-pressed", b.getAttribute("data-view-set") === mode ? "true" : "false");
      });
    }
  };

  /* ============================================== declarative behaviours */
  /**
   * Small data-attribute behaviours that replace the old inline handlers:
   *   data-autosubmit           on a field: submit its form on change
   *   data-href="/url/"         on a row/card: navigate on click or Enter
   *   data-print                on a button: open the print dialog
   *   data-dismiss="id"         on a button/backdrop: hide element #id
   *   data-fallback-src="/url"  on an <img>: swap to this if the src fails
   */
  function declarative(scope) {
    $all(scope, "[data-href]").forEach(function (row) {
      if (!once(row, "Href")) return;
      if (!row.hasAttribute("tabindex")) row.tabIndex = 0;
      row.classList.add("is-clickable");
    });
    $all(scope, "img[data-fallback-src]").forEach(function (img) {
      if (!once(img, "Fallback")) return;
      function swap() {
        var alt = img.getAttribute("data-fallback-src");
        img.removeAttribute("data-fallback-src");
        if (alt && img.getAttribute("src") !== alt) img.src = alt;
      }
      if (img.complete && !img.naturalWidth && img.getAttribute("src")) swap();
      else img.addEventListener("error", swap, { once: true });
    });
  }
  document.addEventListener("change", function (e) {
    var f = e.target.closest && e.target.closest("[data-autosubmit]");
    if (f && f.form) { f.form.requestSubmit ? f.form.requestSubmit() : f.form.submit(); }
  });
  document.addEventListener("click", function (e) {
    var p = e.target.closest("[data-print]");
    if (p) { e.preventDefault(); window.print(); return; }
    var d = e.target.closest("[data-dismiss]");
    if (d) {
      var t = document.getElementById(d.getAttribute("data-dismiss"));
      if (t) { e.preventDefault(); t.hidden = true; }
      return;
    }
    var row = e.target.closest("[data-href]");
    if (row && !e.target.closest("a, button, input, select, textarea, label, summary")) {
      var url = row.getAttribute("data-href");
      if (e.metaKey || e.ctrlKey) window.open(url, "_blank");
      else window.location.href = url;
    }
  });
  document.addEventListener("keydown", function (e) {
    if (e.key !== "Enter") return;
    var row = e.target.closest && e.target.closest("[data-href]");
    if (row && e.target === row) window.location.href = row.getAttribute("data-href");
  });

  /* ================================================================ boot */
  function enhance(scope) {
    scope = scope || document;
    $all(scope, "[data-dropdown-trigger]").forEach(NBTE.dropdown);
    $all(scope, "[data-collapse-trigger]").forEach(NBTE.collapse);
    $all(scope, "[role='tablist'][data-tabs], .tabs[role='tablist']").forEach(NBTE.tabs);
    $all(scope, "[data-carousel]").forEach(NBTE.carousel);
    $all(scope, "form").forEach(NBTE.formValidation);
    $all(scope, ".theme-toggle, [data-theme-toggle]").forEach(NBTE.themeToggle);
    NBTE.responsiveTables(scope);
    declarative(scope);
    NBTE.lazyImages(scope);
    NBTE.scrollReveal(scope);
  }

  function boot() {
    var html = document.documentElement;
    html.classList.add("js");

    // Drawer: the app sidebar on phones and tablets.
    var sidebar = document.querySelector(".app-sidebar");
    if (sidebar) {
      if (!sidebar.id) sidebar.id = "app-sidebar";
      NBTE.drawer(sidebar, "start");
    }
    document.addEventListener("click", function (e) {
      var t = e.target.closest("[data-drawer-toggle]");
      if (t) {
        e.preventDefault();
        var d = document.getElementById(t.getAttribute("data-drawer-toggle"));
        if (d && d.__nbteDrawer) d.__nbteDrawer.toggle();
        return;
      }
      var mo = e.target.closest("[data-modal-open]");
      if (mo) {
        var m = NBTE.modal(mo.getAttribute("data-modal-open"));
        if (m) { e.preventDefault(); m.open(); }
        return;
      }
      var imp = e.target.closest("[data-impersonation-toggle]");
      if (imp && NBTE.Impersonation) { e.preventDefault(); NBTE.Impersonation.toggle(); return; }
      var vs = e.target.closest("[data-view-set]");
      if (vs) { e.preventDefault(); NBTE.viewMode.set(vs.getAttribute("data-view-set")); }
    });

    // Ripple on every button and .btn link (one delegated listener).
    document.addEventListener("pointerdown", function (e) {
      if (e.button !== 0) return;
      var b = e.target.closest("button, a.btn, .btn");
      if (b) NBTE.ripple(b, e);
    });

    var bell = document.getElementById("notif-bell");
    if (bell) NBTE.notifications(bell);
    var searchInput = document.querySelector(".app-search-input");
    if (searchInput) NBTE.searchPanel(searchInput);

    NBTE.viewMode.set(NBTE.viewMode.get());
    enhance(document);
    NBTE.pageTransition();

    // Busy buttons must not stay stuck after back/forward or htmx requests.
    window.addEventListener("pageshow", function (e) { if (e.persisted) clearBusy(); });
    document.body.addEventListener("htmx:afterRequest", function (e) {
      var elt = e.detail && e.detail.elt;
      var form = elt && elt.closest && elt.closest("form");
      clearBusy(form || undefined);
      if (elt && elt.__nbteHxBusy) { elt.__nbteHxBusy = false; NBTE.setBusy(elt, false); }
    });
    // A button that posts on its own (hx-post/put/patch/delete, no form)
    // gets the same busy state while the request runs; restored on success
    // and on error (afterRequest fires for both).
    document.body.addEventListener("htmx:beforeRequest", function (e) {
      var elt = e.detail && e.detail.elt;
      if (!elt || !elt.matches || !elt.matches("button, a.btn, input[type=submit]")) return;
      if (elt.hasAttribute("data-no-busy") || elt.hasAttribute("data-qa-no-busy") || elt.classList.contains("is-busy")) return;
      var verb = ((e.detail.requestConfig && e.detail.requestConfig.verb) || "get").toLowerCase();
      if (verb === "get" && !elt.hasAttribute("data-busy-text")) return;
      elt.__nbteHxBusy = true;
      NBTE.setBusy(elt, true);
    });
    // New content from htmx gets the same treatment.
    document.body.addEventListener("htmx:afterSwap", function (e) {
      if (e.detail && e.detail.target) enhance(e.detail.target);
    });
  }

  NBTE.enhance = enhance;

  // "Add to calendar" menus (partials/add_to_calendar.html): one open at a
  // time, closed by Escape or a click elsewhere; [data-nbte-copy] copies text.
  document.addEventListener("click", function (e) {
    var copy = e.target.closest && e.target.closest("[data-nbte-copy]");
    if (copy) {
      e.preventDefault();
      var text = copy.getAttribute("data-nbte-copy");
      var done = function () { if (NBTE.Toast) NBTE.Toast.push("success", "Link copied", "Paste it into your calendar app's \u201cSubscribe\u201d or \u201cAdd calendar from URL\u201d."); };
      if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(text).then(done, function () { window.prompt("Copy this link", text); });
      else window.prompt("Copy this link", text);
      return;
    }
    $all(document, "details[data-nbte-cal][open]").forEach(function (d) {
      if (!d.contains(e.target)) d.removeAttribute("open");
    });
  });
  document.addEventListener("keydown", function (e) {
    if (e.key !== "Escape") return;
    $all(document, "details[data-nbte-cal][open]").forEach(function (d) {
      d.removeAttribute("open");
      var s = d.querySelector("summary");
      if (s) s.focus();
    });
  });

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();
})();
