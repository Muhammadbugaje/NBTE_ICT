/* NBTE Admin theme behaviour (Phase 8). No dependencies.
   - Marks <html> with .nbte-js so CSS entrance motion only runs with JS.
   - Dashboard tiles count up from zero once (the real number is already in
     the page, so it reads correctly without JavaScript or with reduced motion).
   - "/" focuses the list search box; Esc leaves it.
   - Rows in admin lists get a pointer cursor and open on a click anywhere in
     the row (not on links, inputs or checkboxes), like a native table.
   - Select mode: on list pages the checkbox column and the actions bar stay
     hidden until "Select" (next to Filters) is pressed. Pressing it again
     (or Esc) hides them and clears the selection through Django's own
     checkboxes, so admin/js/actions.js keeps its counter right.
   - Filters sheet self-heal: if an ancestor traps the fixed sheet (Safari
     containment/transform), the trap is removed so the sheet opens fully. */
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


  /* ------------------------------------------------------------ select mode */
  function initSelectMode() {
    var table = document.getElementById("result_list");
    var form = document.getElementById("changelist-form");
    if (!table || !form || !table.querySelector("td.action-checkbox input.action-select")) return;
    var toggleAll = document.getElementById("action-toggle");
    var boxes = function () { return Array.prototype.slice.call(table.querySelectorAll("input.action-select")); };

    var btn = document.createElement("button");
    btn.type = "button";
    btn.className = "nbte-select-btn bg-white border-base-200 hover:text-primary-600 dark:bg-base-900 dark:border-base-700 dark:hover:text-primary-500 border cursor-pointer flex font-medium gap-2 items-center px-3 py-2 rounded-default shadow-xs text-sm";
    btn.setAttribute("aria-pressed", "false");
    btn.setAttribute("aria-controls", "result_list");
    btn.innerHTML = '<span class="material-symbols-outlined md-18" aria-hidden="true">checklist</span><span data-nbte-select-label>Select</span>';
    var label = btn.querySelector("[data-nbte-select-label]");

    // Toolbar: the row holding the search box and the Filters button.
    var filterBtn = document.querySelector(".filter-open-button");
    var search = document.getElementById("toolbar");
    var bar = (filterBtn && filterBtn.parentElement) || (search && search.parentElement);
    if (bar) {
      if (filterBtn) {
        // Filters uses lg:ml-auto to sit on the right; sit just before it.
        btn.className += " lg:ml-auto";
        filterBtn.classList.remove("lg:ml-auto");
        filterBtn.parentElement.insertBefore(btn, filterBtn);
      } else {
        btn.className += " lg:ml-auto";
        bar.appendChild(btn);
      }
    } else {
      form.parentElement.insertBefore(btn, form);
    }

    function clearSelection() {
      if (toggleAll && toggleAll.checked) toggleAll.click();      // Django: uncheck all + clear "select across"
      boxes().forEach(function (b) { if (b.checked) b.click(); }); // fires Django's own handlers
      var across = form.querySelector("input.select-across");
      if (across) across.value = "0";
    }
    function set(on) {
      root.classList.toggle("nbte-select-on", on);
      btn.setAttribute("aria-pressed", on ? "true" : "false");
      label.textContent = on ? "Done" : "Select";
      if (!on) clearSelection();
    }
    root.classList.add("nbte-select-ready");
    // Back/forward can restore ticked boxes: start in select mode then.
    set(boxes().some(function (b) { return b.checked; }));

    btn.addEventListener("click", function () { set(!root.classList.contains("nbte-select-on")); });
    document.addEventListener("keydown", function (e) {
      if (e.key !== "Escape" || !root.classList.contains("nbte-select-on")) return;
      var tag = (e.target && e.target.tagName) || "";
      if (/INPUT|TEXTAREA|SELECT/.test(tag) && !(e.target.classList && e.target.classList.contains("action-select"))) return;
      var sheet = document.getElementById("changelist-filter");
      if (sheet) {
        var cs = window.getComputedStyle(sheet);
        if (cs.position === "fixed" && cs.display !== "none") return; // Esc closes the Filters sheet first
      }
      set(false);
    });
  }

  /* ------------------------------------------------------ filter sheet heal */
  function initFilterSheet() {
    var sheet = document.getElementById("changelist-filter");
    var open = document.querySelector(".filter-open-button");
    if (!sheet || !open) return;
    function trapped(el) {
      var cs = window.getComputedStyle(el);
      return (cs.transform && cs.transform !== "none") ||
             (cs.filter && cs.filter !== "none") ||
             (cs.webkitBackdropFilter && cs.webkitBackdropFilter !== "none") ||
             (cs.backdropFilter && cs.backdropFilter !== "none") ||
             (cs.perspective && cs.perspective !== "none") ||
             (cs.contain && cs.contain !== "none") ||
             (cs.containerType && cs.containerType !== "normal") ||
             /transform|filter|perspective/.test(cs.willChange || "");
    }
    function heal() {
      var close = document.getElementById("changelist-filter-close");
      // Only the sheet layout (below 1536px) is position:fixed.
      if (window.getComputedStyle(sheet).position !== "fixed" || window.getComputedStyle(sheet).display === "none") return;
      var r = sheet.getBoundingClientRect();
      if (Math.abs(r.top) < 2 && Math.abs(r.height - window.innerHeight) < 4) return; // already covers the viewport
      for (var el = sheet.parentElement; el && el !== document.body; el = el.parentElement) {
        if (trapped(el)) {
          el.style.setProperty("transform", "none", "important");
          el.style.setProperty("filter", "none", "important");
          el.style.setProperty("-webkit-backdrop-filter", "none", "important");
          el.style.setProperty("backdrop-filter", "none", "important");
          el.style.setProperty("perspective", "none", "important");
          el.style.setProperty("contain", "none", "important");
          el.style.setProperty("container-type", "normal", "important");
          el.style.setProperty("will-change", "auto", "important");
        }
      }
      if (close) close.style.pointerEvents = "auto";
    }
    open.addEventListener("click", function () {
      window.setTimeout(heal, 30);
      window.setTimeout(heal, 200);
    });
    // Also when opened another way (the "f" shortcut): Alpine toggles style.
    if (window.MutationObserver) {
      new MutationObserver(function () { window.setTimeout(heal, 30); })
        .observe(sheet, { attributes: true, attributeFilter: ["style"] });
    }
  }

  function init() {
    brandLogin();
    try { initSelectMode(); } catch (e) { if (window.console) console.error(e); }
    try { initFilterSheet(); } catch (e) { if (window.console) console.error(e); }
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
