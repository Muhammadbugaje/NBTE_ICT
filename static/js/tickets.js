/* ==========================================================================
   NBTE ICT — Tickets module JS
   - Live SLA countdown + colour shift on every ticket element with
     [data-sla-due] and [data-sla-start].
   - Auto-resize for [data-autoresize] textareas.
   - Interactive star-rating widget for [data-star-rating].
   - Timeline: scroll to the newest entry on SSE-driven swap.
   - Comment composer: Ctrl/Cmd+Enter submits.
   No dependencies beyond what the rest of the app already loads.
   ========================================================================== */

(function () {
  "use strict";

  /* ----------------------------------------------------------------
     SLA countdown
     Any element with data-sla-due="<ISO timestamp>" and (optionally)
     data-sla-start="<ISO timestamp>" is kept up to date every second.
     The element should contain a child [data-sla-out] that receives the
     countdown text, and (optionally) a [data-sla-bar-fill] whose width
     reflects the elapsed fraction.
     ---------------------------------------------------------------- */
  function humanise(deltaSeconds) {
    var s = Math.abs(Math.floor(deltaSeconds));
    if (s < 60) return s + "s";
    var m = Math.floor(s / 60);
    if (m < 60) return m + "m";
    var h = Math.floor(m / 60);
    var mm = m % 60;
    if (h < 24) return mm ? h + "h " + mm + "m" : h + "h";
    var d = Math.floor(h / 24);
    var hh = h % 24;
    return hh ? d + "d " + hh + "h" : d + "d";
  }

  function updateSlaEl(el) {
    var dueStr = el.getAttribute("data-sla-due");
    if (!dueStr) return;
    var due = new Date(dueStr).getTime();
    if (isNaN(due)) return;
    var now = Date.now();
    var diff = (due - now) / 1000; // seconds; negative = overdue

    var out = el.querySelector("[data-sla-out]");
    if (out) {
      if (diff >= 0) {
        out.textContent = humanise(diff) + " left";
      } else {
        out.textContent = "overdue by " + humanise(diff);
      }
    }

    // Determine level
    var level = "ok";
    var startStr = el.getAttribute("data-sla-start");
    if (startStr) {
      var start = new Date(startStr).getTime();
      var total = (due - start) / 1000;
      var elapsed = (now - start) / 1000;
      var fraction = total > 0 ? Math.min(1, Math.max(0, elapsed / total)) : 1;

      var fill = el.querySelector("[data-sla-bar-fill]");
      if (fill) fill.style.width = (fraction * 100).toFixed(1) + "%";

      if (fraction >= 1 || diff < 0) level = "danger";
      else if (fraction >= 0.75) level = "warn";
    } else if (diff < 0) {
      level = "danger";
    }

    el.setAttribute("data-sla-level", level);
    var bar = el.querySelector("[data-sla-bar]");
    if (bar) bar.setAttribute("data-level", level);

    var chip = el.querySelector("[data-sla-chip]");
    if (chip) {
      chip.classList.remove("tk-chip-sla-ok", "tk-chip-sla-warn", "tk-chip-sla-danger");
      chip.classList.add("tk-chip-sla-" + level);
    }

    var row = el.querySelector("[data-sla-row]");
    if (row) {
      row.classList.remove("is-warn", "is-danger");
      if (level === "warn") row.classList.add("is-warn");
      if (level === "danger") row.classList.add("is-danger");
    }
  }

  function tickAllSla() {
    document.querySelectorAll("[data-sla-due]").forEach(updateSlaEl);
  }

  var slaInterval = null;
  function initSlaTicker() {
    tickAllSla();
    if (slaInterval) return;
    slaInterval = setInterval(tickAllSla, 1000);
  }

  /* ----------------------------------------------------------------
     Auto-resize textareas
     ---------------------------------------------------------------- */
  function autosize(el) {
    el.style.height = "auto";
    el.style.height = Math.min(el.scrollHeight, 260) + "px";
  }

  function initAutoresize() {
    document.querySelectorAll("textarea[data-autoresize]").forEach(function (el) {
      if (el.__autoresize) return;
      el.__autoresize = true;
      autosize(el);
      el.addEventListener("input", function () { autosize(el); });
    });
  }

  /* ----------------------------------------------------------------
     Star rating widget
     Usage:
       <div class="tk-stars" data-star-rating data-target="id_rating">
         <span class="tk-star" data-value="1">★</span> ... x5
       </div>
       <input type="hidden" id="id_rating" name="rating" value="5">
     ---------------------------------------------------------------- */
  function initStars() {
    document.querySelectorAll("[data-star-rating]").forEach(function (root) {
      if (root.__starsInit) return;
      root.__starsInit = true;
      var targetId = root.getAttribute("data-target");
      var input = targetId ? document.getElementById(targetId) : null;
      var stars = Array.from(root.querySelectorAll(".tk-star"));
      var current = input ? parseInt(input.value || "0", 10) : 0;

      function paint(n) {
        stars.forEach(function (s) {
          var v = parseInt(s.getAttribute("data-value"), 10);
          s.classList.toggle("is-filled", v <= n);
        });
      }

      paint(current);

      stars.forEach(function (s) {
        s.addEventListener("mouseenter", function () {
          paint(parseInt(s.getAttribute("data-value"), 10));
        });
        s.addEventListener("click", function () {
          current = parseInt(s.getAttribute("data-value"), 10);
          if (input) input.value = String(current);
          paint(current);
        });
      });

      root.addEventListener("mouseleave", function () { paint(current); });
    });
  }

  /* ----------------------------------------------------------------
     Timeline: on SSE-driven swap, scroll new entries into view and
     mark them with a soft pulse so the user notices.
     ---------------------------------------------------------------- */
  function initTimelineObservers() {
    document.body.addEventListener("htmx:afterSwap", function (evt) {
      var t = evt.detail && evt.detail.target;
      if (!t || !t.id) return;
      if (t.id !== "tkt-timeline-container" && t.id !== "wf-tl-container") return;
      // Slight delay so layout settles first.
      setTimeout(function () {
        t.scrollIntoView({ behavior: "smooth", block: "nearest" });
      }, 40);
    });
  }

  /* ----------------------------------------------------------------
     Ctrl/Cmd + Enter submits the nearest form for comment boxes.
     ---------------------------------------------------------------- */
  function initSubmitShortcut() {
    document.addEventListener("keydown", function (e) {
      if (!(e.ctrlKey || e.metaKey) || e.key !== "Enter") return;
      var ta = e.target;
      if (!ta || ta.tagName !== "TEXTAREA") return;
      var form = ta.closest("form");
      if (!form) return;
      e.preventDefault();
      if (typeof form.requestSubmit === "function") form.requestSubmit();
      else form.submit();
    });
  }

  /* ----------------------------------------------------------------
     On HTMX swaps, re-scan for new elements that need JS hooks.
     ---------------------------------------------------------------- */
  function initRescanOnSwap() {
    document.body.addEventListener("htmx:afterSwap", function () {
      initAutoresize();
      initStars();
      tickAllSla();
    });
  }

  /* ----------------------------------------------------------------
     Boot
     ---------------------------------------------------------------- */
  function boot() {
    initSlaTicker();
    initAutoresize();
    initStars();
    initTimelineObservers();
    initSubmitShortcut();
    initRescanOnSwap();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }

  // Public hooks
  window.NBTETickets = {
    tickAllSla: tickAllSla,
    initStars: initStars,
    initAutoresize: initAutoresize,
  };
})();