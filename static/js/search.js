/* ==========================================================================
   NBTE ICT — Global search
   Self-contained IIFE, same shape as dialog.js. Exposes window.NBTESearch
   (and window.NBTE.Search when app.js is loaded first, which it is).

   Behaviour:
     - "/" or Ctrl/Cmd+K anywhere → open and focus the input.
     - 220 ms debounce; in-flight requests are aborted when the query changes.
     - ↑/↓ moves the active row across group boundaries; Enter opens it.
     - Escape closes; clicking the backdrop closes; clicking a result opens it.
     - Empty query → hint; non-empty but no matches → empty state.
     - Respects light/dark via CSS variables.
   ========================================================================== */
(function () {
  "use strict";

  var DEBOUNCE_MS = 220;
  var MIN_QUERY = 2;

  // Simple icon map — keys match `BaseSearchProvider.icon`.
  var ICONS = {
    "check-square":   '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 11 12 14 22 4"/><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"/></svg>',
    "life-buoy":      '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><circle cx="12" cy="12" r="4"/><line x1="4.93" y1="4.93" x2="9.17" y2="9.17"/><line x1="14.83" y1="14.83" x2="19.07" y2="19.07"/><line x1="14.83" y1="9.17" x2="19.07" y2="4.93"/><line x1="14.83" y1="9.17" x2="18.36" y2="5.64"/><line x1="4.93" y1="19.07" x2="9.17" y2="14.83"/></svg>',
    "file-text":      '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/><polyline points="10 9 9 9 8 9"/></svg>',
    "users":          '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/></svg>',
    "box":            '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"/><polyline points="3.27 6.96 12 12.01 20.73 6.96"/><line x1="12" y1="22.08" x2="12" y2="12"/></svg>',
    "folder":         '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg>',
    "megaphone":      '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 11l18-5v12L3 14v-3z"/><path d="M11.6 16.8a3 3 0 1 1-5.8-1.6"/></svg>',
    "message-circle": '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z"/></svg>',
    "calendar":       '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="18" rx="2" ry="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/></svg>',
    "search":         '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>'
  };

  var Search = {
    overlay: null,
    panel: null,
    input: null,
    resultsEl: null,
    triggerBtn: null,
    isOpen: false,
    loading: false,
    query: "",
    groups: [],
    flat: [],          // flat list of { el, url } for keyboard nav
    activeIndex: -1,
    controller: null,
    timer: null,
    escBound: false,

    // -----------------------------------------------------------------
    // Bootstrap
    // -----------------------------------------------------------------
    init: function () {
      this.overlay = document.getElementById("app-search-overlay");
      if (!this.overlay) return;

      this.panel = this.overlay.querySelector(".app-search-panel");
      this.input = this.overlay.querySelector(".app-search-input");
      this.resultsEl = this.overlay.querySelector("#app-search-results");
      this.triggerBtn = document.querySelector("[data-search-trigger]");

      this.bind();
    },

    bind: function () {
      var self = this;

      // Trigger button
      if (this.triggerBtn) {
        this.triggerBtn.addEventListener("click", function (e) {
          e.preventDefault();
          self.open();
        });
      }

      // Close button + backdrop
      this.overlay.addEventListener("click", function (e) {
        if (e.target.closest("[data-search-close]")) {
          e.preventDefault();
          self.close();
        }
        if (e.target === self.overlay) {
          self.close();
        }
      });

      // Input
      this.input.addEventListener("input", function () {
        self.onInput();
      });

      // Keyboard inside the input
      this.input.addEventListener("keydown", function (e) {
        self.onKeyDown(e);
      });

      // Global hotkeys — "/" and Ctrl/Cmd+K
      document.addEventListener("keydown", function (e) {
        self.onGlobalKey(e);
      });

      // Clicking a result closes the overlay on the way out.
      this.resultsEl.addEventListener("click", function (e) {
        var a = e.target.closest("a.app-search-result");
        if (a) {
          self.close();
        }
      });
    },

    // -----------------------------------------------------------------
    // Open / close
    // -----------------------------------------------------------------
    open: function () {
      if (this.isOpen) return;
      this.isOpen = true;
      this.overlay.setAttribute("aria-hidden", "false");
      document.body.classList.add("app-search-open");

      var self = this;
      setTimeout(function () {
        try { self.input.focus(); self.input.select(); } catch (e) {}
      }, 20);
    },

    close: function () {
      if (!this.isOpen) return;
      this.isOpen = false;
      this.overlay.setAttribute("aria-hidden", "true");
      document.body.classList.remove("app-search-open");

      if (this.timer) { clearTimeout(this.timer); this.timer = null; }
      if (this.controller) {
        try { this.controller.abort(); } catch (e) {}
        this.controller = null;
      }
      this.loading = false;
      this.query = "";
      this.groups = [];
      this.flat = [];
      this.activeIndex = -1;
      if (this.input) this.input.value = "";
      this.renderHint();
    },

    // -----------------------------------------------------------------
    // Hotkeys
    // -----------------------------------------------------------------
    onGlobalKey: function (e) {
      // Ctrl/Cmd + K → open.
      if ((e.ctrlKey || e.metaKey) && (e.key === "k" || e.key === "K")) {
        e.preventDefault();
        this.open();
        return;
      }

      // "/" → open, only when not typing in another field and nothing
      // modal-ish is up.
      if (e.key === "/" && !e.ctrlKey && !e.metaKey && !e.altKey) {
        var t = e.target;
        var tag = (t && t.tagName) || "";
        var inField = (
          tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT"
          || (t && t.isContentEditable)
        );
        if (inField) return;
        if (document.querySelector("[data-confirm-modal]:not([hidden])")) return;
        e.preventDefault();
        this.open();
        return;
      }

      // Escape anywhere closes.
      if (e.key === "Escape" && this.isOpen) {
        e.preventDefault();
        this.close();
      }
    },

    // -----------------------------------------------------------------
    // Input flow
    // -----------------------------------------------------------------
    onInput: function () {
      var self = this;
      var q = (this.input.value || "").trim();
      this.query = q;

      if (this.timer) clearTimeout(this.timer);
      if (this.controller) {
        try { this.controller.abort(); } catch (e) {}
        this.controller = null;
      }

      if (q.length < MIN_QUERY) {
        this.groups = [];
        this.flat = [];
        this.activeIndex = -1;
        this.renderHint();
        return;
      }

      this.timer = setTimeout(function () {
        self.fetch(q);
      }, DEBOUNCE_MS);
    },

    fetch: function (q) {
      var self = this;
      var base = this.overlay.getAttribute("data-search-url") || "/search/";
      var url = base + (base.indexOf("?") === -1 ? "?" : "&")
                + "q=" + encodeURIComponent(q);

      this.loading = true;
      this.renderLoading();

      this.controller = new AbortController();
      var signal = this.controller.signal;

      fetch(url, {
        method: "GET",
        credentials: "same-origin",
        headers: { "Accept": "application/json" },
        signal: signal
      })
      .then(function (res) {
        if (!res.ok) throw new Error("HTTP " + res.status);
        return res.json();
      })
      .then(function (data) {
        // Stale response guard — a newer query already replaced this one.
        if (self.query !== q) return;
        self.loading = false;
        self.groups = (data && data.groups) || [];
        self.render();
      })
      .catch(function (err) {
        if (err && err.name === "AbortError") return;
        if (self.query !== q) return;
        self.loading = false;
        self.renderError(err);
      });
    },

    // -----------------------------------------------------------------
    // Keyboard nav inside the overlay
    // -----------------------------------------------------------------
    onKeyDown: function (e) {
      if (e.key === "ArrowDown") {
        e.preventDefault();
        this.moveActive(1);
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        this.moveActive(-1);
      } else if (e.key === "Enter") {
        var active = this.flat[this.activeIndex];
        if (active && active.url) {
          e.preventDefault();
          window.location.href = active.url;
        }
      } else if (e.key === "Escape") {
        e.preventDefault();
        this.close();
      } else if (e.key === "Home") {
        if (this.flat.length) {
          e.preventDefault();
          this.setActive(0);
        }
      } else if (e.key === "End") {
        if (this.flat.length) {
          e.preventDefault();
          this.setActive(this.flat.length - 1);
        }
      }
    },

    moveActive: function (delta) {
      if (!this.flat.length) return;
      var next = this.activeIndex + delta;
      if (next < 0) next = this.flat.length - 1;
      if (next >= this.flat.length) next = 0;
      this.setActive(next);
    },

    setActive: function (idx) {
      this.activeIndex = idx;
      for (var i = 0; i < this.flat.length; i++) {
        this.flat[i].el.classList.toggle("is-active", i === idx);
      }
      var a = this.flat[idx];
      if (a && a.el.scrollIntoView) {
        try {
          a.el.scrollIntoView({ block: "nearest" });
        } catch (e) {}
      }
    },

    // -----------------------------------------------------------------
    // Rendering
    // -----------------------------------------------------------------
    renderHint: function () {
      this.flat = [];
      this.activeIndex = -1;
      this.resultsEl.innerHTML =
        '<div class="app-search-hint">'
        + '<span class="app-search-hint-title">Type to search across the portal</span>'
        + '<span class="app-search-hint-sub">'
        + 'Tasks · Tickets · Requests · Staff · Assets · Documents · Announcements · Messages · Attendance'
        + '</span></div>';
      this.input.setAttribute("aria-expanded", "false");
    },

    renderLoading: function () {
      this.resultsEl.innerHTML =
        '<div class="app-search-state">'
        + '<span class="app-search-spinner" aria-hidden="true"></span>'
        + '<span>Searching…</span>'
        + '</div>';
    },

    renderError: function (err) {
      var msg = (err && err.message) ? err.message : "Network error.";
      this.resultsEl.innerHTML =
        '<div class="app-search-state app-search-state-error">'
        + 'Search failed — ' + escapeHtml(msg)
        + '</div>';
    },

    render: function () {
      var self = this;
      this.flat = [];
      this.activeIndex = -1;

      if (!this.groups.length) {
        this.resultsEl.innerHTML =
          '<div class="app-search-state">'
          + 'No matches for <strong>' + escapeHtml(this.query) + '</strong>.'
          + '</div>';
        this.input.setAttribute("aria-expanded", "false");
        return;
      }

      var frag = document.createDocumentFragment();

      this.groups.forEach(function (group) {
        var label = document.createElement("div");
        label.className = "app-search-group-label";
        label.innerHTML = (ICONS[group.icon] || ICONS.search)
          + '<span>' + escapeHtml(group.label) + '</span>'
          + '<span class="app-search-group-count">' + group.results.length + '</span>';
        frag.appendChild(label);

        group.results.forEach(function (r) {
          var a = document.createElement("a");
          a.className = "app-search-result";
          a.href = r.url || "#";
          a.setAttribute("role", "option");
          a.setAttribute("aria-selected", "false");

          var title = document.createElement("div");
          title.className = "app-search-result-title";
          title.textContent = r.title || "(untitled)";

          var meta = document.createElement("div");
          meta.className = "app-search-result-meta";
          meta.textContent = r.meta || "";

          var text = document.createElement("div");
          text.className = "app-search-result-text";
          text.appendChild(title);
          if (r.snippet) {
            var snippet = document.createElement("div");
            snippet.className = "app-search-result-snippet";
            snippet.textContent = r.snippet;
            text.appendChild(snippet);
          }
          if (r.meta) text.appendChild(meta);

          a.appendChild(text);

          if (r.badge) {
            var badge = document.createElement("span");
            badge.className = "app-search-result-badge";
            badge.textContent = r.badge;
            a.appendChild(badge);
          }

          frag.appendChild(a);
          self.flat.push({ el: a, url: r.url });
        });
      });

      this.resultsEl.innerHTML = "";
      this.resultsEl.appendChild(frag);

      // Auto-highlight the first row so Enter just works.
      if (this.flat.length) {
        this.setActive(0);
      }
      this.input.setAttribute("aria-expanded", "true");
    }
  };

  // -----------------------------------------------------------------
  // Util
  // -----------------------------------------------------------------
  function escapeHtml(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function onReady(fn) {
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", fn);
    } else {
      fn();
    }
  }

  // -----------------------------------------------------------------
  // Boot
  // -----------------------------------------------------------------
  onReady(function () {
    try {
      Search.init();
    } catch (e) {
      console.error("[NBTE] search init failed", e);
    }
  });

  window.NBTESearch = Search;
  if (window.NBTE) {
    window.NBTE.Search = Search;
  }
})();