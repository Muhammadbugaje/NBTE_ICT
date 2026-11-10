/* ==========================================================================
   NBTE ICT — Global JS (v21)
   Changes in v21 (UI overhaul):
     - window.NBTE is merged, not replaced (ui.js adds primitives).
     - Toasts: close button styled by CSS (no inline style), countdown bar.
   ========================================================================== */
/* ==========================================================================
   (history)
   Changes in v20:
     - Added initSidebarActiveScroll(): scrolls .app-nav so the currently
       active item is visible on page load.
   Changes in v19:
     - Toast dedup window raised to 5 minutes (was 60 seconds).
   Changes in v18:
     - Added ThreadScroll: threads auto-scroll to newest message on load.
   Changes in v17:
     - Toast.push() dedups by signature within a window (sessionStorage).
   Changes in v16:
     - Added Impersonation module (collapsible banner, persisted state).
   Changes in v15:
     - Added syncTopbarOffset().
   Changes in v14:
     - Fixed stray document.body access; initSSEStreams registers once;
       boot() uses safeCall; initHtmxFilterHooks always binds.
   ========================================================================== */

(function () {
  "use strict";

  function safeCall(label, fn) {
    try { fn(); }
    catch (e) { console.error("[NBTE] " + label + " failed", e); }
  }

  function getCookie(name) {
    var cookies = document.cookie ? document.cookie.split(";") : [];
    for (var i = 0; i < cookies.length; i++) {
      var c = cookies[i].trim();
      if (c.indexOf(name + "=") === 0) {
        return decodeURIComponent(c.substring(name.length + 1));
      }
    }
    return null;
  }

  function onReady(fn) {
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", fn);
    } else {
      fn();
    }
  }

  /* ============================================================
     TOPBAR OFFSET
     ============================================================ */
  function syncTopbarOffset() {
    var tb = document.querySelector(".app-topbar");
    if (!tb) return;
    var rect = tb.getBoundingClientRect();
    document.documentElement.style.setProperty(
      "--topbar-offset-bottom",
      Math.round(rect.bottom) + "px"
    );
  }

  function initTopbarOffset() {
    syncTopbarOffset();
    window.addEventListener("load", syncTopbarOffset);
    window.addEventListener("resize", syncTopbarOffset);
    window.addEventListener("orientationchange", syncTopbarOffset);
    document.addEventListener("htmx:afterSwap", syncTopbarOffset);
  }

  /* ============================================================
     THEME
     ============================================================ */
  /* The page follows the device's light/dark setting. Pressing the toggle
     sets a manual choice, remembered together with the device setting at
     that moment (OS_KEY). As soon as the device setting changes — now or
     between visits — the device wins again and the toggle follows it. */
  var Theme = {
    STORAGE_KEY: "nbte-theme",
    OS_KEY: "nbte-theme-os",
    prefersDark: function () {
      try {
        return !!(window.matchMedia &&
               window.matchMedia("(prefers-color-scheme: dark)").matches);
      } catch (e) { return false; }
    },
    system: function () { return this.prefersDark() ? "dark" : "light"; },
    /* v2.1 A10: a light/dark choice saved on the profile (NBTE_PREFS.theme). */
    profile: function () {
      var t = (window.NBTE_PREFS || {}).theme;
      return (t === "light" || t === "dark") ? t : null;
    },
    get: function () {
      try {
        var stored = localStorage.getItem(this.STORAGE_KEY);
        var osAtChoice = localStorage.getItem(this.OS_KEY);
        if ((stored === "light" || stored === "dark") && osAtChoice === this.system()) return stored;
        this.forget();
      } catch (e) {}
      return this.profile() || this.system();
    },
    remember: function (theme) {
      try {
        localStorage.setItem(this.STORAGE_KEY, theme);
        localStorage.setItem(this.OS_KEY, this.system());
      } catch (e) {}
    },
    forget: function () {
      try {
        localStorage.removeItem(this.STORAGE_KEY);
        localStorage.removeItem(this.OS_KEY);
      } catch (e) {}
    },
    apply: function (theme, opts) {
      opts = opts || {};
      var html = document.documentElement;
      if (opts.animate && document.body) {
        document.body.classList.add("no-transition");
        requestAnimationFrame(function () {
          html.setAttribute("data-theme", theme);
          requestAnimationFrame(function () {
            document.body.classList.remove("no-transition");
          });
        });
      } else {
        html.setAttribute("data-theme", theme);
      }
      // Only a press of the toggle is remembered (apply() used to store every
      // theme it set, so the device setting was never followed again).
      if (opts.remember) this.remember(theme);
      try {
        window.dispatchEvent(new CustomEvent("theme:change", {
          detail: { theme: theme }
        }));
      } catch (e) {}
    },
    toggle: function () {
      var next = document.documentElement.getAttribute("data-theme") === "dark"
        ? "light" : "dark";
      if (next === this.system()) this.forget();   // back in step with the device
      this.apply(next, { animate: true, remember: next !== this.system() });
    },
    init: function () {
      this.apply(this.get());
      try {
        var mq = window.matchMedia &&
                 window.matchMedia("(prefers-color-scheme: dark)");
        var onChange = function (e) {
          // The device switched light/dark: follow it and drop any manual
          // choice (a light/dark saved on the profile still wins).
          Theme.forget();
          Theme.apply(Theme.profile() || (e.matches ? "dark" : "light"), { animate: true });
        };
        if (mq && mq.addEventListener) mq.addEventListener("change", onChange);
        else if (mq && mq.addListener) mq.addListener(onChange);   // older Safari
      } catch (e) {}
      // Another open tab pressed the toggle: keep this one in step.
      window.addEventListener("storage", function (e) {
        if (e.key === Theme.STORAGE_KEY || e.key === Theme.OS_KEY) {
          var stored = null;
          try { stored = localStorage.getItem(Theme.STORAGE_KEY); } catch (e) {}
          Theme.apply(
            stored === "light" || stored === "dark"
              ? stored : (Theme.profile() || Theme.system())
          );
        }
        if (e.key && /^nbte-(palette|motion|density)$/.test(e.key)) {
          var k = e.key.slice(5);
          document.documentElement.setAttribute("data-" + k, e.newValue || Theme.siteValue(k));
        }
      });
    },

    /* ---- v2.1 A9/A10: palette, motion, density (+ theme) ----------------
       Theme.set(k, v) applies one preference at once (one repaint: the
       theme-switching class turns transitions off for a frame), remembers it
       on this device and saves it on the profile (POST /me/preferences/), so
       it follows the person to every device. v = "" goes back to the site
       default. Fires "theme:change" {k, v}. */
    KEYS: { theme: 1, palette: 1, motion: 1, density: 1 },
    siteValue: function (k) {
      var s = (window.NBTE_PREFS || {}).site || {};
      return s[k] || { palette: "green", motion: "full", density: "comfortable" }[k] || "";
    },
    set: function (k, v, opts) {
      if (!this.KEYS[k]) return;
      opts = opts || {};
      var r = document.documentElement, stamp = Date.now();
      r.classList.add("theme-switching");
      try {
        if (k === "theme") {
          var t = (v === "light" || v === "dark") ? v : this.system();
          this.forget();
          r.setAttribute("data-theme", t);
          window.NBTE_PREFS = Object.assign(window.NBTE_PREFS || {}, { theme: v || "system" });
        } else {
          var val = v || this.siteValue(k);
          if (k === "motion" && v === "system") val = this.siteValue(k);
          if (k === "motion" && val === "full" && window.matchMedia &&
              window.matchMedia("(prefers-reduced-motion: reduce)").matches) val = "reduced";
          r.setAttribute("data-" + k, val);
          if (k === "motion") r.classList.toggle("m-on", val !== "off" && !(window.matchMedia &&
              window.matchMedia("(prefers-reduced-motion: reduce)").matches));
          if (v && v !== "system") localStorage.setItem("nbte-" + k, v); else localStorage.removeItem("nbte-" + k);
        }
        localStorage.setItem("nbte-stamp", String(stamp));
      } catch (e) {}
      requestAnimationFrame(function () {
        requestAnimationFrame(function () { r.classList.remove("theme-switching"); });
      });
      if (opts.save !== false) this.save(k, v, stamp);
      try {
        document.dispatchEvent(new CustomEvent("theme:change", { detail: { k: k, v: v } }));
      } catch (e) {}
    },
    setPalette: function (p) { this.set("palette", p); },
    save: function (k, v, stamp) {
      var body = {}; body[k] = v || ""; body.stamp = stamp;
      var token = getCookie("csrftoken");
      if (!token || !window.fetch) return;
      return fetch("/me/preferences/", {
        method: "POST", credentials: "same-origin",
        headers: { "X-CSRFToken": token, "Content-Type": "application/json", "Accept": "application/json" },
        body: JSON.stringify(body)
      }).then(function (res) {
        if (res.ok) return res.json().then(function (j) {
          try { if (j && j.preferences && j.preferences.stamp) localStorage.setItem("nbte-stamp", String(j.preferences.stamp)); } catch (e) {}
          return j;
        });
      }).catch(function () {});
    }
  };

  /* ============================================================
     PAGE LOADER
     ============================================================ */
  // Phase 6: the loader is hidden by default (content is never covered on
  // load) and only appears when a navigation is slow (over 400 ms).
  // The NBTE crest loader (partials/page_loader.html) also covers slow
  // full-page navigations and form posts (sign-in included): see
  // initNavigationLoader. show(text) sets the status line ("Signing you in…").
  var PageLoader = {
    timer: null,
    safety: null,
    hide: function () {
      var el = document.getElementById("page-loader");
      if (this.timer) { clearTimeout(this.timer); this.timer = null; }
      if (this.safety) { clearTimeout(this.safety); this.safety = null; }
      if (!el) return;
      el.classList.remove("is-shown");
      el.classList.add("hidden");
      el.setAttribute("aria-hidden", "true");
    },
    show: function (text, delay) {
      var el = document.getElementById("page-loader");
      if (!el) return;
      var self = this;
      if (this.timer) clearTimeout(this.timer);
      var label = el.querySelector("[data-loader-label]");
      if (label) {
        if (!label.hasAttribute("data-default")) label.setAttribute("data-default", label.textContent);
        label.textContent = text || label.getAttribute("data-default") || "Loading…";
      }
      this.timer = setTimeout(function () {
        el.classList.remove("hidden");
        el.classList.add("is-shown");
        el.setAttribute("aria-hidden", "false");
      }, typeof delay === "number" ? delay : 400);
      // A download or a cancelled navigation never unloads the page.
      if (this.safety) clearTimeout(this.safety);
      this.safety = setTimeout(function () { self.hide(); }, 10000);
    }
  };

  function initNavigationLoader() {
    function skipLink(a, e) {
      if (!a || e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return true;
      if (a.hasAttribute("download") || a.hasAttribute("data-no-loader") || (a.target && a.target !== "_self")) return true;
      if (a.hasAttribute("hx-get") || a.hasAttribute("hx-post") || a.closest("[hx-boost='true']")) return true;
      var href = a.getAttribute("href") || "";
      if (!href || href.charAt(0) === "#" || /^(mailto|tel|javascript|webcal|blob|data):/i.test(href)) return true;
      if (a.origin !== window.location.origin) return true;
      if (a.pathname === window.location.pathname && a.search === window.location.search && a.hash) return true;
      return /\.(ics|pdf|csv|xlsx?|docx?|zip|png|jpe?g)$/i.test(a.pathname);
    }
    document.addEventListener("click", function (e) {
      var a = e.target.closest && e.target.closest("a[href]");
      if (!a) return;
      // Decide after every other handler ran (htmx / dialogs may cancel it).
      setTimeout(function () { if (!skipLink(a, e)) PageLoader.show(a.getAttribute("data-loading-text"), 600); }, 0);
    });
    document.addEventListener("submit", function (e) {
      var form = e.target;
      if (!form || form.tagName !== "FORM") return;
      setTimeout(function () {
        if (e.defaultPrevented || form.hasAttribute("data-no-loader") || (form.target && form.target !== "_self")) return;
        if (form.hasAttribute("hx-post") || form.hasAttribute("hx-get") || form.closest("[hx-boost='true']")) return;
        // Opt-in (data-loading-text, e.g. sign-in): other posts may answer
        // with a file download, which never unloads the page, so they keep
        // the busy button (ui.js) instead of a full-screen loader.
        var text = form.getAttribute("data-loading-text");
        if (text) PageLoader.show(text, 250);
      }, 0);
    });
    window.addEventListener("pagehide", function () { PageLoader.hide(); });
  }

  /* ============================================================
     TOASTS
     ============================================================ */
  var TOAST_ICONS = {
    success: '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>',
    error:   '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/></svg>',
    info:    '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="12" y1="16" x2="12" y2="12"/><line x1="12" y1="8" x2="12.01" y2="8"/></svg>',
    warn:    '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>'
  };

  var Toast = {
    stack: null,
    DEDUP_MS: 5 * 60 * 1000,
    DEDUP_KEY: "nbte-toast-sigs",

    ensureStack: function () {
      if (this.stack && document.body.contains(this.stack)) return this.stack;
      this.stack = document.createElement("div");
      this.stack.className = "toast-stack";
      this.stack.setAttribute("aria-live", "polite");
      this.stack.setAttribute("aria-atomic", "true");
      document.body.appendChild(this.stack);
      return this.stack;
    },

    push: function (type, title, message, opts) {
      opts = opts || {};
      var duration = opts.duration === undefined ? 5000 : Number(opts.duration);
      if (isNaN(duration)) duration = 5000;

      var sig = type + "|" + (title || "") + "|" + (message || "");
      var sigs = {};
      try { sigs = JSON.parse(sessionStorage.getItem(this.DEDUP_KEY) || "{}") || {}; } catch (e) {}
      var now = Date.now();
      Object.keys(sigs).forEach(function (k) {
        if (now - sigs[k] > Toast.DEDUP_MS) delete sigs[k];
      });
      if (sigs[sig] && opts.dedupe !== false) return function () {};
      sigs[sig] = now;
      try { sessionStorage.setItem(this.DEDUP_KEY, JSON.stringify(sigs)); } catch (e) {}

      var stack = this.ensureStack();
      var el = document.createElement("div");
      el.className = "toast toast-" + type;
      el.setAttribute("role", type === "error" ? "alert" : "status");

      var icon = document.createElement("span");
      icon.className = "toast-icon";
      icon.innerHTML = TOAST_ICONS[type] || TOAST_ICONS.info;

      var body = document.createElement("div");
      body.className = "toast-body";
      var titleEl = document.createElement("div");
      titleEl.className = "toast-title";
      titleEl.textContent = String(title || "");
      body.appendChild(titleEl);
      if (message) {
        var msgEl = document.createElement("div");
        msgEl.className = "toast-msg";
        msgEl.textContent = String(message);
        body.appendChild(msgEl);
      }

      var closeBtn = document.createElement("button");
      closeBtn.type = "button";
      closeBtn.className = "toast-close";
      closeBtn.setAttribute("aria-label", "Dismiss notification");
      closeBtn.innerHTML = "&times;";

      el.appendChild(icon);
      el.appendChild(body);
      el.appendChild(closeBtn);
      // Countdown bar (UI overhaul): shows how long until auto-dismiss.
      var bar = null;
      if (duration > 0) {
        bar = document.createElement("span");
        bar.className = "toast-progress";
        bar.setAttribute("aria-hidden", "true");
        bar.style.animationDuration = duration + "ms";
        el.appendChild(bar);
      }
      stack.appendChild(el);

      var timer = null;
      var dismissed = false;

      function dismiss() {
        if (dismissed) return;
        dismissed = true;
        if (timer) { clearTimeout(timer); timer = null; }
        el.classList.add("leaving");
        setTimeout(function () {
          if (el.parentNode) el.parentNode.removeChild(el);
        }, 220);
      }

      if (duration > 0) timer = setTimeout(dismiss, duration);

      closeBtn.onclick = function (e) {
        if (e) { e.preventDefault(); e.stopPropagation(); }
        dismiss();
      };

      el.addEventListener("mouseenter", function () {
        if (timer) { clearTimeout(timer); timer = null; }
        if (bar) bar.style.animationPlayState = "paused";
      });
      el.addEventListener("mouseleave", function () {
        if (!dismissed && duration > 0) timer = setTimeout(dismiss, 1500);
      });

      return dismiss;
    },
    success: function (t, m, o) { return this.push("success", t, m, o); },
    error:   function (t, m, o) {
      o = o || {};
      if (o.duration === undefined) o.duration = 8000;
      return this.push("error", t, m, o);
    },
    info:  function (t, m, o) { return this.push("info", t, m, o); },
    warn:  function (t, m, o) { return this.push("warn", t, m, o); }
  };

  /* ============================================================
     MODAL
     ============================================================ */
  var Modal = {
    open: function (id) {
      var root = document.getElementById(id);
      if (!root) return false;
      root.setAttribute("aria-hidden", "false");
      document.body.style.overflow = "hidden";
      var first = root.querySelector(
        "input:not([type=hidden]), textarea, select, button:not(.modal-close)"
      );
      if (first) setTimeout(function () { try { first.focus(); } catch (e) {} }, 80);
      if (root._onKey) document.removeEventListener("keydown", root._onKey);
      root._onKey = function (e) { if (e.key === "Escape") Modal.close(id); };
      document.addEventListener("keydown", root._onKey);
      return true;
    },
    close: function (id) {
      var root = document.getElementById(id);
      if (!root) return false;
      root.setAttribute("aria-hidden", "true");
      document.body.style.overflow = "";
      if (root._onKey) {
        document.removeEventListener("keydown", root._onKey);
        delete root._onKey;
      }
      return true;
    },
    toggle: function (id) {
      var root = document.getElementById(id);
      if (!root) return false;
      var isOpen = root.getAttribute("aria-hidden") === "false";
      return isOpen ? this.close(id) : this.open(id);
    }
  };

  /* ============================================================
     DROPDOWN
     ============================================================ */
  function setDropdownTriggersExpanded(id, expanded) {
    document.querySelectorAll("[data-dropdown-trigger]").forEach(function (trigger) {
      if (trigger.getAttribute("data-dropdown-trigger") === id) {
        trigger.setAttribute("aria-expanded", expanded ? "true" : "false");
      }
    });
  }

  var Dropdown = {
    closeAll: function (except) {
      document.querySelectorAll("[data-dropdown-menu]").forEach(function (menu) {
        if (menu === except) return;
        menu.setAttribute("aria-hidden", "true");
        setDropdownTriggersExpanded(menu.id, false);
      });
    },
    toggle: function (id) {
      var menu = document.getElementById(id);
      if (!menu) return false;
      var isOpen = menu.getAttribute("aria-hidden") === "false";
      this.closeAll();
      if (!isOpen) {
        menu.setAttribute("aria-hidden", "false");
        setDropdownTriggersExpanded(id, true);
        syncTopbarOffset();
      }
      return true;
    },
    init: function () {
      document.addEventListener("click", function (e) {
        var trigger = e.target.closest("[data-dropdown-trigger]");
        if (trigger) {
          e.preventDefault();
          Dropdown.toggle(trigger.getAttribute("data-dropdown-trigger"));
          return;
        }
        if (!e.target.closest("[data-dropdown-menu]")) Dropdown.closeAll();
      });
      document.addEventListener("keydown", function (e) {
        if (e.key === "Escape") Dropdown.closeAll();
      });
    }
  };

  /* ============================================================
     IMPERSONATION BANNER
     ============================================================ */
  var Impersonation = {
    KEY: "nbte-imp-collapsed",

    isMobile: function () {
      try { return window.matchMedia("(max-width: 720px)").matches; }
      catch (e) { return false; }
    },

    set: function (collapsed, save) {
      var el = document.getElementById("impersonation-banner");
      if (!el) return;
      el.classList.toggle("is-collapsed", !!collapsed);

      var toggle = el.querySelector(".imp-toggle");
      if (toggle) toggle.setAttribute("aria-expanded", collapsed ? "false" : "true");

      if (save) {
        try { localStorage.setItem(this.KEY, collapsed ? "1" : "0"); } catch (e) {}
      }

      syncTopbarOffset();
    },

    toggle: function () {
      var el = document.getElementById("impersonation-banner");
      if (!el) return;
      this.set(!el.classList.contains("is-collapsed"), true);
    },

    restore: function () {
      var el = document.getElementById("impersonation-banner");
      if (!el) return;

      var stored = null;
      try { stored = localStorage.getItem(this.KEY); } catch (e) {}

      if (stored === "1") { this.set(true, false); return; }
      if (stored === "0") { this.set(false, false); return; }

      if (this.isMobile()) this.set(true, false);
    }
  };

  /* ============================================================
     THREAD SCROLL
     ============================================================ */
  var ThreadScroll = {
    scrollToBottom: function (opts) {
      opts = opts || {};
      var el = document.querySelector("[data-thread]");
      if (!el) return false;

      var doScroll = function () {
        if (el.scrollHeight > el.clientHeight &&
            (el.scrollTop !== undefined) &&
            getComputedStyle(el).overflowY !== "visible") {
          el.scrollTop = el.scrollHeight;
        }
        var rect = el.getBoundingClientRect();
        var absoluteBottom = rect.bottom + window.scrollY;
        var viewportH = window.innerHeight;
        var target = absoluteBottom - viewportH + 24;
        if (target < 0) target = 0;

        if (opts.instant) {
          window.scrollTo(0, target);
        } else {
          window.scrollTo({ top: target, behavior: "smooth" });
        }
      };

      var imgs = el.querySelectorAll("img");
      if (imgs.length) {
        var pending = 0;
        imgs.forEach(function (img) {
          if (!img.complete) {
            pending++;
            img.addEventListener("load", function () {
              pending--;
              if (pending === 0) doScroll();
            }, { once: true });
            img.addEventListener("error", function () {
              pending--;
              if (pending === 0) doScroll();
            }, { once: true });
          }
        });
        if (pending === 0) doScroll();
        setTimeout(doScroll, 300);
      } else {
        requestAnimationFrame(doScroll);
      }

      return true;
    },

    init: function () {
      if (!document.querySelector("[data-thread]")) return;

      setTimeout(function () {
        ThreadScroll.scrollToBottom({ instant: true });
      }, 60);

      document.body.addEventListener("htmx:afterSwap", function (evt) {
        var target = evt.detail && evt.detail.target;
        if (!target) return;
        if (target.closest && target.closest("[data-thread]")) {
          setTimeout(function () {
            ThreadScroll.scrollToBottom({ instant: false });
          }, 60);
        }
      });
    }
  };

  /* ============================================================
     SCROLL / NAV
     ============================================================ */
  function initScrollReveal() {
    var els = document.querySelectorAll(".reveal");
    if (!els.length) return;
    if (!("IntersectionObserver" in window)) {
      els.forEach(function (el) { el.classList.add("in"); });
      return;
    }
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (entry.isIntersecting) {
          entry.target.classList.add("in");
          io.unobserve(entry.target);
        }
      });
    }, { rootMargin: "0px 0px -8% 0px", threshold: 0.08 });
    els.forEach(function (el) { io.observe(el); });
  }

  function initStickyNav() {
    var nav = document.querySelector(".nav");
    if (!nav) return;
    var onScroll = function () {
      if (window.scrollY > 12) nav.classList.add("scrolled");
      else nav.classList.remove("scrolled");
    };
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
  }

  function initSmoothScroll() {
    document.addEventListener("click", function (e) {
      var a = e.target.closest('a[href^="#"]');
      if (!a) return;
      var href = a.getAttribute("href");
      if (!href || href === "#" || href.length < 2) return;
      var id = href.slice(1);
      var target = document.getElementById(id);
      if (!target) return;
      e.preventDefault();
      var top = target.getBoundingClientRect().top + window.scrollY - 80;
      window.scrollTo({ top: top, behavior: "smooth" });
      try { history.replaceState(null, "", href); } catch (err) {}
    });
  }

  /* ============================================================
     SIDEBAR — mobile drawer only (no desktop collapse)
     ============================================================ */
  var Sidebar = {
    toggleMobile: function () {
      var shell = document.querySelector(".app-shell");
      if (!shell) return;
      shell.classList.toggle("sidebar-open");
      var backdrop = document.querySelector(".app-sidebar-backdrop");
      if (backdrop) {
        var open = shell.classList.contains("sidebar-open");
        backdrop.style.display = open ? "block" : "";
      }
    }
  };

  /* ============================================================
     SIDEBAR GROUPS
     ============================================================ */
  var STORAGE_GROUPS_KEY = "nbte-nav-groups";

  function readGroupState() {
    try {
      return JSON.parse(localStorage.getItem(STORAGE_GROUPS_KEY) || "{}") || {};
    } catch (e) {
      return {};
    }
  }

  function writeGroupState(state) {
    try {
      localStorage.setItem(STORAGE_GROUPS_KEY, JSON.stringify(state));
    } catch (e) {}
  }

  function setGroupOpen(group, open, save) {
    if (!group) return;
    group.classList.toggle("is-open", !!open);
    var head = group.querySelector(".app-nav-group-head");
    if (head) head.setAttribute("aria-expanded", open ? "true" : "false");
    if (save) {
      var key = group.getAttribute("data-group");
      if (key) {
        var state = readGroupState();
        state[key] = !!open;
        writeGroupState(state);
      }
    }
  }

  function initSidebarGroups() {
    var groups = document.querySelectorAll(".app-nav-group");
    if (!groups.length) return;

    var saved = readGroupState();

    groups.forEach(function (group) {
      var key = group.getAttribute("data-group");
      var head = group.querySelector(".app-nav-group-head");
      if (!head || !key || group.hasAttribute("data-nav-bound")) return;
      group.setAttribute("data-nav-bound", "");

      if (Object.prototype.hasOwnProperty.call(saved, key)) {
        setGroupOpen(group, saved[key], false);
      }

      head.addEventListener("click", function () {
        var isOpen = group.classList.contains("is-open");
        setGroupOpen(group, !isOpen, true);
      });
    });
  }

  /* ============================================================
     SIDEBAR ACTIVE SCROLL
     Scroll .app-nav so the currently active item is visible on
     page load. Silent no-op if nothing is active, or if the item
     is already fully visible.
     ============================================================ */
  function initSidebarActiveScroll() {
    var nav = document.querySelector(".app-nav");
    if (!nav) return;

    var active = nav.querySelector(".app-nav-item.active");
    if (!active) return;

    function run() {
      var navRect    = nav.getBoundingClientRect();
      var activeRect = active.getBoundingClientRect();

      // Already fully visible? Do nothing.
      if (activeRect.top >= navRect.top && activeRect.bottom <= navRect.bottom) {
        return;
      }

      // Centre the active item in the nav viewport.
      var targetScroll =
        nav.scrollTop
        + (activeRect.top - navRect.top)
        - (navRect.height / 2 - activeRect.height / 2);

      // Clamp to valid scroll range.
      var maxScroll = nav.scrollHeight - nav.clientHeight;
      if (targetScroll < 0) targetScroll = 0;
      if (targetScroll > maxScroll) targetScroll = maxScroll;

      // Instant so the user never sees the sidebar animate on load.
      // (CSS scroll-behavior: smooth only affects explicit smooth requests.)
      nav.scrollTo({ top: targetScroll, behavior: "auto" });
    }

    // Two passes: once after initial layout, once a beat later in
    // case a late font or icon changed the row heights.
    requestAnimationFrame(run);
    setTimeout(run, 120);
  }

  /* ============================================================
     DJANGO MESSAGES → TOASTS
     ============================================================ */
  function initServerMessages() {
    document.querySelectorAll("[data-toast]").forEach(function (el) {
      var type = el.getAttribute("data-toast") || "info";
      var title = el.getAttribute("data-toast-title") || "";
      var msg = el.getAttribute("data-toast-msg") || "";
      Toast.push(type, title, msg);
      el.remove();
    });
  }

  /* ============================================================
     CSRF REFRESH
     ============================================================ */
  function initCSRFRefresh() {
    document.addEventListener("submit", function (e) {
      var form = e.target;
      if (!form || form.tagName !== "FORM") return;
      var method = (form.getAttribute("method") || "get").toLowerCase();
      if (method !== "post") return;
      var token = getCookie("csrftoken");
      if (!token) return;
      var input = form.querySelector("input[name=csrfmiddlewaretoken]");
      if (!input) {
        input = document.createElement("input");
        input.type = "hidden";
        input.name = "csrfmiddlewaretoken";
        form.appendChild(input);
      }
      input.value = token;
    }, true);
  }

  /* ============================================================
     PASSWORD REVEAL
     ============================================================ */
  function initPasswordReveals() {
    document.addEventListener("click", function (e) {
      var btn = e.target.closest("[data-toggle-password]");
      if (!btn) return;
      e.preventDefault();
      var targetId = btn.getAttribute("data-toggle-password");
      var input = targetId
        ? document.getElementById(targetId)
        : (btn.closest(".input-group")
            ? btn.closest(".input-group").querySelector("input")
            : null);
      if (!input) return;
      var reveal = input.type === "password";
      input.type = reveal ? "text" : "password";
      btn.classList.toggle("is-revealed", reveal);
      btn.classList.add("revealing");
      setTimeout(function () { btn.classList.remove("revealing"); }, 320);
    });
  }

  /* ============================================================
     HTMX — GLOBAL ERROR HANDLING
     ============================================================ */
  var progressBar = null;
  var progressTimer = null;

  function ensureProgressBar() {
    if (progressBar && document.body.contains(progressBar)) return progressBar;
    progressBar = document.createElement("div");
    progressBar.className = "htmx-progress";
    progressBar.setAttribute("aria-hidden", "true");
    document.body.appendChild(progressBar);
    return progressBar;
  }

  function showProgressBar() {
    var bar = ensureProgressBar();
    bar.classList.remove("active");
    bar.offsetHeight;
    bar.classList.add("active");
  }

  function hideProgressBar() {
    if (!progressBar) return;
    progressBar.classList.remove("active");
  }

  function handleHtmxRequestStart() {
    if (progressTimer) clearTimeout(progressTimer);
    progressTimer = setTimeout(showProgressBar, 180);
  }

  function handleHtmxRequestEnd() {
    if (progressTimer) { clearTimeout(progressTimer); progressTimer = null; }
    hideProgressBar();
  }

  function handleHtmxResponseError(evt) {
    var xhr = evt.detail.xhr;
    var status = xhr ? xhr.status : 0;
    var statusText = xhr ? (xhr.statusText || "") : "";

    if (evt.detail.shouldSwap === true) {
      evt.detail.shouldSwap = false;
    }

    var title = "Something went wrong";
    var msg = "";

    if (status === 403) {
      title = "Permission denied";
      msg = "Your session may have expired, or you don't have access. Refresh the page and try again.";
    } else if (status === 404) {
      title = "Not found";
      msg = "The requested resource could not be found.";
    } else if (status === 400) {
      title = "Invalid request";
      msg = "The server could not process your request. Check your input and try again.";
    } else if (status === 413) {
      title = "File too large";
      msg = "The uploaded file exceeds the size limit. Try a smaller file.";
    } else if (status === 500) {
      title = "Server error";
      msg = "The server hit an unexpected error. If this persists, contact ICT support.";
    } else if (status === 0) {
      title = "Network error";
      msg = "Could not reach the server. Check your connection and try again.";
    } else {
      msg = "Request failed (" + status + " " + statusText + ").";
    }

    Toast.error(title, msg, { duration: 9000 });
  }

  function handleHtmxSendError() {
    Toast.error("Network error", "Could not reach the server.");
  }

  function initHtmxHooks() {
    if (!window.htmx) {
      setTimeout(initHtmxHooks, 60);
      return;
    }
    // Phase 0 (N2, V6): one place for the history settings of every page.
    // A cache miss on Back reloads the URL as a full page (the server always
    // renders the full page for a normal or history-restore request).
    window.htmx.config.refreshOnHistoryMiss = true;
    window.htmx.config.historyCacheSize = 10;
    document.body.addEventListener("htmx:beforeRequest", handleHtmxRequestStart);
    document.body.addEventListener("htmx:afterRequest", handleHtmxRequestEnd);
    document.body.addEventListener("htmx:responseError", handleHtmxResponseError);
    document.body.addEventListener("htmx:sendError", handleHtmxSendError);
    // Django messages added during an HTMX request arrive as the HX-Trigger
    // event "nbte:messages" (core.middleware.DrainHtmxMessagesMiddleware).
    document.body.addEventListener("nbte:messages", function (evt) {
      var list = (evt.detail && evt.detail.value) || evt.detail || [];
      if (!Array.isArray(list)) return;
      var map = { error: "error", danger: "error", warning: "warn", warn: "warn", success: "success" };
      list.forEach(function (m) {
        if (m && m.title) Toast.push(map[m.kind] || "info", m.title, "", { dedupe: false });
      });
    });
    // QA pages (qa.js) show their own inline "taking too long" message.
    document.body.addEventListener("htmx:timeout", function () {
      if (window.NBTE && window.NBTE.QA) return;
      Toast.error("This is taking too long", "The server did not answer in time. Try again.");
    });
    document.body.addEventListener("htmx:beforeSwap", function (evt) {
      // 422 = a form with inline validation errors (QA §20): let it swap in.
      if (evt.detail.xhr && evt.detail.xhr.status >= 400 && evt.detail.xhr.status !== 422) {
        evt.detail.shouldSwap = false;
      }
    });
  }

  /* ============================================================
     HTMX — LIST FILTER/PAGINATION HINTS
     ============================================================ */
  function initHtmxFilterHooks() {
    document.body.addEventListener("htmx:beforeRequest", function (evt) {
      var elt = evt.detail && evt.detail.elt;
      if (!elt) return;
      var isPagination = elt.closest && elt.closest(".pagination");
      evt.detail.__isPagination = !!isPagination;
    });

    document.body.addEventListener("htmx:afterSwap", function (evt) {
      var target = evt.detail && evt.detail.target;
      if (!target || !target.id || target.id.indexOf("container") === -1) return;

      var wasPagination = evt.detail && evt.detail.requestConfig
        && evt.detail.requestConfig.elt
        && evt.detail.requestConfig.elt.closest
        && evt.detail.requestConfig.elt.closest(".pagination");

      if (wasPagination) {
        try {
          var top = target.getBoundingClientRect().top + window.scrollY - 100;
          window.scrollTo({ top: top, behavior: "smooth" });
        } catch (e) {}
      }
    });
  }

  /* ============================================================
     SSE STREAMS
     ============================================================ */
  var sseHooksBound = false;

  function initSSEStreams() {
    var els = document.querySelectorAll("[data-sse-stream]");
    els.forEach(function (el) {
      if (el.__sse) return;
      var url = el.getAttribute("data-sse-stream");
      if (!url) return;
      // Review H2: short-poll instead of a long-lived EventSource. The
      // endpoint answers at once with {"last_pk", "count"}; we keep the
      // highest pk seen so the same activity never fires twice.
      var last = 0;
      var m = /[?&]since=(\d+)/.exec(url);
      if (m) last = parseInt(m[1], 10) || 0;
      var base = url.split("?")[0];
      var tick = function () {
        if (document.hidden) return;
        fetch(base + "?since=" + last, {
          credentials: "same-origin",
          headers: { "Accept": "application/json" },
        })
          .then(function (r) { return r.ok ? r.json() : null; })
          .then(function (data) {
            if (!data) return;
            if (data.count > 0 && data.last_pk > last) {
              last = data.last_pk;
              document.body.dispatchEvent(new CustomEvent("sse:activity", { detail: data }));
            }
          })
          .catch(function () {});
      };
      el.__sse = setInterval(tick, 10000);
      window.addEventListener("beforeunload", function () { clearInterval(el.__sse); });
    });

    if (!sseHooksBound) {
      sseHooksBound = true;
      document.body.addEventListener("htmx:afterSwap", function () {
        initSSEStreams();
      });
    }
  }

  /* ============================================================
     NOTIFICATION BADGE
     ============================================================ */
  function _findNotificationBadge() {
    return (
      document.querySelector("[data-notif-badge]") ||
      document.querySelector("[data-notification-badge]") ||
      document.getElementById("notif-badge") ||
      document.querySelector(".notif-badge") ||
      null
    );
  }

  function _findNotificationPanel() {
    return (
      document.querySelector("[data-notif-panel]") ||
      document.querySelector("[data-notification-panel]") ||
      document.getElementById("notif-panel") ||
      document.querySelector(".notif-panel") ||
      null
    );
  }

  function _refreshBellIfOpen() {
    var panel = _findNotificationPanel();
    if (!panel) return;
    var isOpen = !panel.hasAttribute("hidden") &&
                 panel.getAttribute("aria-hidden") !== "true";
    if (!isOpen) return;
    if (typeof window.refreshBell === "function") {
      try { window.refreshBell(); } catch (e) {}
      return;
    }
    if (window.htmx && panel.hasAttribute("hx-get")) {
      try { window.htmx.trigger(panel, "refresh"); } catch (e) {}
    }
  }

  function initNotificationBadgeListener() {
    document.body.addEventListener("notificationCountChanged", function (e) {
      var detail = e.detail || {};
      var unread = (typeof detail.unread !== "undefined") ? detail.unread : null;

      var badge = _findNotificationBadge();
      if (badge && unread !== null) {
        badge.textContent = unread;
        badge.hidden = unread === 0;
        if (unread > 0) {
          badge.setAttribute(
            "aria-label",
            unread + " unread notification" + (unread === 1 ? "" : "s")
          );
        } else {
          badge.setAttribute("aria-label", "No unread notifications");
        }
      }

      _refreshBellIfOpen();
    });

    window.addEventListener("focus", function () {
      var panel = _findNotificationPanel();
      if (!panel) return;
      if (window.htmx && panel.hasAttribute("hx-get")) {
        try { window.htmx.trigger(panel, "refresh"); } catch (e) {}
      }
    });
  }

  /* ============================================================
     CONFIRMATION MODAL (data-confirm-open / data-confirm-close)
     ============================================================ */
  var Confirmation = {
    init: function () {
      document.addEventListener("click", function (e) {
        var opener = e.target.closest("[data-confirm-open]");
        if (opener) {
          var modal = document.getElementById(opener.getAttribute("data-confirm-open"));
          if (modal) {
            modal.hidden = false;
            document.body.style.overflow = "hidden";
            return;
          }
        }
        var closer = e.target.closest("[data-confirm-close]");
        if (closer) {
          var m = closer.closest("[data-confirm-modal]");
          if (m) {
            m.hidden = true;
            document.body.style.overflow = "";
          }
        }
      });
      document.addEventListener("keydown", function (e) {
        if (e.key !== "Escape") return;
        document.querySelectorAll("[data-confirm-modal]:not([hidden])").forEach(function (m) {
          m.hidden = true;
          document.body.style.overflow = "";
        });
      });
    }
  };

  /* ============================================================
     BOOT
     ============================================================ */
  function boot() {
    safeCall("topbar offset", initTopbarOffset);
    safeCall("theme init", function () { Theme.init(); });
    safeCall("page loader hide", function () { PageLoader.hide(); });
    window.addEventListener("pageshow", function () { PageLoader.hide(); });
    safeCall("navigation loader", initNavigationLoader);
    safeCall("impersonation restore", function () { Impersonation.restore(); });
    safeCall("scroll reveal", initScrollReveal);
    safeCall("sticky nav", initStickyNav);
    safeCall("smooth scroll", initSmoothScroll);
    safeCall("dropdown init", function () { Dropdown.init(); });
    safeCall("sidebar groups", initSidebarGroups);
    safeCall("sidebar active scroll", initSidebarActiveScroll);
    // Phase 6: the workspace switcher swaps the whole sidebar over HTMX;
    // bind the new groups and keep the active item in view.
    // initSidebarGroups skips groups it already bound, so this is cheap.
    document.body.addEventListener("htmx:afterSettle", function () {
      safeCall("sidebar groups (swap)", initSidebarGroups);
    });
    safeCall("server messages", initServerMessages);
    safeCall("csrf refresh", initCSRFRefresh);
    safeCall("password reveals", initPasswordReveals);

    safeCall("thread scroll", function () { ThreadScroll.init(); });

    safeCall("htmx hooks", initHtmxHooks);
    safeCall("htmx filter hooks", initHtmxFilterHooks);

    safeCall("notif badge listener", initNotificationBadgeListener);
    safeCall("sse streams", initSSEStreams);

    safeCall("confirmation modal", function () { Confirmation.init(); });
    // v2.1 A10: profile Appearance card — apply a pick at once; HTMX saves it.
    document.addEventListener("change", function (e) {
      var t = e.target;
      if (t && t.matches && t.matches("[data-pap-key]")) {
        Theme.set(t.getAttribute("data-pap-key"), t.value, { save: !window.htmx });
      }
    });
  }

  /* Expose public API */
  // Merge rather than replace: ui.js and the page scripts extend NBTE too.
  window.NBTE = Object.assign(window.NBTE || {}, {
    Theme: Theme,
    Toast: Toast,
    Modal: Modal,
    Dropdown: Dropdown,
    Sidebar: Sidebar,
    Impersonation: Impersonation,
    ThreadScroll: ThreadScroll,
    PageLoader: PageLoader,
    getCookie: getCookie,
    setGroupOpen: setGroupOpen,
    syncTopbarOffset: syncTopbarOffset,
  });
  window.NBTETheme = Theme;
  window.NBTEToast = Toast;
  window.NBTEModal = Modal;
  window.NBTELoader = PageLoader;

  onReady(boot);
})();