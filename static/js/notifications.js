/* ==========================================================================
   NBTE ICT — Notifications
   - Fetches the bell payload (unread count + latest items)
   - SSE stream for live unread count (falls back to polling on error)
   - Clicking a bell item navigates via /open/ (marks read first)
   - "Mark all read" button
   ========================================================================== */

(function () {
  "use strict";

  var BELL_URL = "/notifications/bell/";
  var STREAM_URL = "/notifications/stream/";
  var MARK_ALL_URL = "/notifications/mark-all-read/";

  var state = {
    eventSource: null,
    pollTimer: null,
    loaded: false,
  };

  function ready(fn) {
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", fn);
    else fn();
  }

  function getCSRF() {
    var m = document.cookie.match(/csrftoken=([^;]+)/);
    return m ? decodeURIComponent(m[1]) : "";
  }

  function escapeHtml(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  // ------------------------------------------------------------------------
  // Bell payload
  // ------------------------------------------------------------------------
  function fetchBell() {
    return fetch(BELL_URL, {
      credentials: "same-origin",
      headers: { "X-Requested-With": "XMLHttpRequest" },
    }).then(function (r) { return r.json(); });
  }

  function updateBadge(count) {
    var badge = document.getElementById("notif-badge");
    if (!badge) return;
    if (count > 0) {
      badge.textContent = count > 99 ? "99+" : String(count);
      badge.hidden = false;
    } else {
      badge.hidden = true;
    }
  }

  function iconFor(level) {
    if (level === "success") return '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>';
    if (level === "warning") return '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>';
    if (level === "error")   return '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/></svg>';
    return '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="12" y1="16" x2="12" y2="12"/><line x1="12" y1="8" x2="12.01" y2="8"/></svg>';
  }

  function renderBell(data) {
    var list = document.getElementById("notif-list");
    var markAll = document.getElementById("notif-mark-all");
    if (!list) return;

    updateBadge(data.unread || 0);
    list.setAttribute("aria-busy", "false");

    if (markAll) markAll.hidden = !data.unread;

    if (!data.items || !data.items.length) {
      list.innerHTML = '<div class="notif-dropdown-empty">You\'re all caught up.</div>';
      return;
    }

    var html = "";
    data.items.forEach(function (n) {
      var cls = "notif-item notif-level-" + (n.level || "info") + (n.is_read ? "" : " is-unread");
      var href = "/notifications/" + n.id + "/open/";
      html += '<a class="' + cls + '" href="' + href + '">';
      html += '<span class="notif-item-icon">' + iconFor(n.level) + "</span>";
      html += '<span class="notif-item-body">';
      html += '<span class="notif-item-title">' + escapeHtml(n.title) + "</span>";
      if (n.body) html += '<span class="notif-item-preview">' + escapeHtml(n.body) + "</span>";
      html += '<span class="notif-item-when">' + escapeHtml(n.when) + "</span>";
      html += "</span></a>";
    });
    list.innerHTML = html;
    state.loaded = true;
  }

  function refreshBell() {
    fetchBell().then(renderBell).catch(function () {});
  }

  // ------------------------------------------------------------------------
  // SSE stream (with polling fallback)
  // ------------------------------------------------------------------------
  // Review H2: the endless SSE stream held one server worker per open tab.
  // The badge now short-polls STREAM_URL, which answers immediately with
  // {"unread": N}. Polling pauses while the tab is hidden.
  var POLL_MS = 20000;

  function pollUnread() {
    if (document.hidden) return;
    fetch(STREAM_URL, {
      credentials: "same-origin",
      headers: { "Accept": "application/json" },
    })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (data) { if (data) updateBadge(data.unread || 0); })
      .catch(function () {});
  }

  function startStream() {
    startPolling();
  }

  function startPolling() {
    if (state.pollTimer) return;
    pollUnread();
    state.pollTimer = setInterval(pollUnread, POLL_MS);
    document.addEventListener("visibilitychange", function () {
      if (!document.hidden) pollUnread();
    });
  }

  // ------------------------------------------------------------------------
  // Mark all read
  // ------------------------------------------------------------------------
  function bindMarkAll() {
    var btn = document.getElementById("notif-mark-all");
    if (!btn || btn.dataset.bound === "1") return;
    btn.dataset.bound = "1";
    btn.addEventListener("click", function (e) {
      e.preventDefault();
      e.stopPropagation();
      fetch(MARK_ALL_URL, {
        method: "POST",
        credentials: "same-origin",
        headers: { "X-CSRFToken": getCSRF(), "X-Requested-With": "XMLHttpRequest" },
      }).then(function (r) { return r.json(); }).then(function () {
        updateBadge(0);
        btn.hidden = true;
        refreshBell();
      }).catch(function () {});
    });
  }

  // ------------------------------------------------------------------------
  // Refresh the dropdown list when the bell menu opens
  // ------------------------------------------------------------------------
  function watchBellOpen() {
    var bell = document.getElementById("notif-bell");
    if (!bell || bell.dataset.bound === "1") return;
    bell.dataset.bound = "1";
    bell.addEventListener("click", function () {
      // Defer slightly so the dropdown transition starts first.
      setTimeout(refreshBell, 40);
    });
  }

  // ------------------------------------------------------------------------
  // Boot
  // ------------------------------------------------------------------------
  function boot() {
    if (!document.getElementById("notif-bell")) return;
    bindMarkAll();
    watchBellOpen();
    refreshBell();
    startStream();
  }

  ready(boot);
})();