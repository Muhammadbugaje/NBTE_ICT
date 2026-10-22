/* NBTE Admin theme behaviour (Phase 8). No dependencies.
   - Marks <html> with .nbte-js so CSS entrance motion only runs with JS.
   - Dashboard tiles count up from zero once (the real number is already in
     the page, so it reads correctly without JavaScript or with reduced motion).
   - "/" focuses the list search box; Esc leaves it.
   - Rows in admin lists get a pointer cursor and open on a click anywhere in
     the row (not on links, inputs or checkboxes), like a native table. */
(function () {
  "use strict";
  var root = document.documentElement;
  root.classList.add("nbte-js");
  var reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  function countUp(el) {
    var raw = el.getAttribute("data-nbte-count");
    var target = parseInt(raw, 10);
    if (!raw || isNaN(target) || target <= 0 || reduce) return;
    var dur = Math.min(1100, 450 + Math.log10(target + 1) * 220), start = null;
    function step(ts) {
      if (start === null) start = ts;
      var t = Math.min(1, (ts - start) / dur);
      var eased = 1 - Math.pow(1 - t, 3);
      el.textContent = Math.round(target * eased).toLocaleString();
      if (t < 1) window.requestAnimationFrame(step);
      else el.textContent = target.toLocaleString();
    }
    el.textContent = "0";
    window.requestAnimationFrame(step);
  }

  function brandLogin() {
    var form = document.getElementById("login-form");
    if (!form) return;
    root.classList.add("nbte-login");
    var icon = document.querySelector("link[rel='icon']");
    var src = icon ? icon.getAttribute("href") : "";
    var wrap = form.parentElement;
    if (src && wrap && !wrap.querySelector(".nbte-login__logo")) {
      var img = document.createElement("img");
      img.className = "nbte-login__logo";
      img.src = src;
      img.alt = "NBTE";
      wrap.insertBefore(img, wrap.firstChild);
    }
  }

  function init() {
    brandLogin();
    document.querySelectorAll("[data-nbte-count]").forEach(countUp);

    var search = document.getElementById("searchbar");
    document.addEventListener("keydown", function (e) {
      var tag = (e.target && e.target.tagName) || "";
      if (e.key === "/" && search && !/INPUT|TEXTAREA|SELECT/.test(tag) && !e.metaKey && !e.ctrlKey) {
        e.preventDefault();
        search.focus();
        search.select();
      } else if (e.key === "Escape" && e.target === search) {
        search.blur();
      }
    });

    var table = document.getElementById("result_list");
    if (table) {
      table.addEventListener("click", function (e) {
        if (e.target.closest("a, button, input, select, textarea, label, details, summary, [x-on\\:click], [role=button]")) return;
        // Never open a record from the selection column or an editable cell.
        var cell = e.target.closest("td, th");
        if (!cell || cell.classList.contains("action-checkbox") || cell.querySelector("input, select, textarea")) return;
        if (window.getSelection && String(window.getSelection())) return;
        var row = e.target.closest("tbody tr");
        if (!row) return;
        var link = row.querySelector("th a[href], td a[href]");
        if (link) window.location.href = link.href;
      });
      table.querySelectorAll("tbody tr").forEach(function (row) {
        if (row.querySelector("th a[href], td a[href]")) row.style.cursor = "pointer";
      });
    }
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
