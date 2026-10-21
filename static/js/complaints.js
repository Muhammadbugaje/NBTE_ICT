/* Complaints (QA Phase 7): live SLA clocks and knowledge-base suggestions.
   Vanilla JS, re-scanned after every HTMX swap. */
(function () {
  "use strict";

  function pad(n) { return (n < 10 ? "0" : "") + n; }

  function fmt(ms) {
    var s = Math.max(0, Math.floor(Math.abs(ms) / 1000));
    var d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
    if (d) return d + "d " + pad(h) + ":" + pad(m) + ":" + pad(sec);
    return pad(h) + ":" + pad(m) + ":" + pad(sec);
  }

  /* [data-cx-countdown="ISO"]: wall-clock time to the deadline, ticking. */
  function tick() {
    var now = Date.now();
    document.querySelectorAll("[data-cx-countdown]").forEach(function (el) {
      var due = Date.parse(el.getAttribute("data-cx-countdown"));
      if (isNaN(due)) return;
      var diff = due - now;
      el.textContent = (diff < 0 ? "−" : "") + fmt(diff);
      var clock = el.closest(".cx-clock");
      if (diff < 0 && clock && !clock.classList.contains("cx-clock--breached")) {
        clock.classList.add("cx-clock--breached");
        var label = clock.querySelector("[data-cx-countdown-label]");
        if (label) label.textContent = "Overdue by";
      }
    });
    /* Row clocks: flip to "overdue" the moment the deadline passes. */
    document.querySelectorAll(".cx-sla[data-cx-due]").forEach(function (el) {
      var due = Date.parse(el.getAttribute("data-cx-due"));
      if (!isNaN(due) && due < now && !el.classList.contains("cx-sla--breached")) {
        el.className = "cx-sla cx-sla--breached";
        var small = el.querySelector("small");
        if (small) small.textContent = "Overdue";
      }
    });
  }

  var timer = null;
  function startClocks() {
    if (timer) return;
    if (!document.querySelector("[data-cx-countdown], .cx-sla[data-cx-due]")) return;
    tick();
    timer = window.setInterval(function () {
      if (!document.querySelector("[data-cx-countdown], .cx-sla[data-cx-due]")) {
        window.clearInterval(timer); timer = null; return;
      }
      tick();
    }, 1000);
  }

  /* Raise form: choosing a layer shows its extra questions. */
  function bindLayers(root) {
    root.querySelectorAll("[data-cx-layers]").forEach(function (group) {
      if (group.dataset.cxBound) return;
      group.dataset.cxBound = "1";
      function sync() {
        var chosen = group.querySelector("input[name=layer]:checked");
        var code = chosen ? chosen.getAttribute("data-code") : "";
        document.querySelectorAll("[data-cx-fields-for]").forEach(function (box) {
          var on = box.getAttribute("data-cx-fields-for") === code;
          box.hidden = !on;
          box.querySelectorAll("input,select,textarea").forEach(function (f) { f.disabled = !on; });
        });
      }
      group.addEventListener("change", sync);
      sync();
    });
  }

  function init(root) {
    startClocks();
    bindLayers(root || document);
  }

  document.addEventListener("DOMContentLoaded", function () { init(document); });
  document.addEventListener("htmx:afterSwap", function (e) { init(e.target || document); });
  document.addEventListener("htmx:historyRestore", function () { init(document); });
})();
