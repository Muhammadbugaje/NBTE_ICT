/* ==========================================================================
   MultiPicker — progressive enhancement for <select multiple>
   v2 — auto-detects any <select multiple> inside a form.
   ========================================================================== */
(function () {
  "use strict";

  function el(tag, cls, html) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (html !== undefined) e.innerHTML = html;
    return e;
  }

  function optionLabel(opt) {
    return (opt.textContent || "").trim();
  }

  function build(select) {
    if (select.__mp) return;
    if (!select.multiple) return;
    if (select.closest(".mp")) return;     // already enhanced by ancestor

    var options = Array.from(select.options).map(function (o) {
      return { value: o.value, label: optionLabel(o), selected: o.selected };
    });

    // Zero options — render the placeholder anyway so it's clear there's
    // nothing to pick (better than showing a dead select).
    select.__mp = true;

    var name = select.getAttribute("name") || "values";
    var placeholder = select.getAttribute("data-placeholder") || "Select…";

    // Hide the native select but keep it in the form for progressive fallback.
    select.style.position = "absolute";
    select.style.left = "-10000px";
    select.style.width = "1px";
    select.style.height = "1px";
    select.setAttribute("aria-hidden", "true");
    select.tabIndex = -1;

    var root = el("div", "mp");
    var control = el("div", "mp-control");
    var chips   = el("div", "mp-chips");
    var search  = el("input", "mp-search");
    search.type = "text";
    search.placeholder = options.length ? placeholder : "Nothing to choose from";
    search.autocomplete = "off";
    search.disabled = options.length === 0;
    var caret = el("button", "mp-caret",
      '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"/></svg>');
    caret.type = "button";
    caret.tabIndex = -1;

    var panel = el("div", "mp-panel");
    panel.style.display = "none";
    var head  = el("div", "mp-panel-head");
    var count = el("span", null, "0 selected");
    var clear = el("button", "mp-clear", "Clear all");
    clear.type = "button";
    head.appendChild(count);
    head.appendChild(clear);
    var list  = el("ul", "mp-list");
    list.setAttribute("role", "listbox");

    if (options.length === 0) {
      var emptyLi = el("li", "mp-item mp-item-empty");
      emptyLi.textContent = "No options available.";
      list.appendChild(emptyLi);
    } else {
      options.forEach(function (o) {
        var item = el("li", "mp-item");
        item.dataset.value = o.value;
        item.dataset.search = o.label.toLowerCase();
        var lab = el("label");
        var cb  = document.createElement("input");
        cb.type = "checkbox";
        cb.className = "mp-checkbox";
        cb.value = o.value;
        cb.checked = o.selected;
        var span = el("span", "mp-label");
        span.textContent = o.label;
        var tick = el("span", "mp-tick", "✓");
        lab.appendChild(cb);
        lab.appendChild(span);
        lab.appendChild(tick);
        item.appendChild(lab);
        list.appendChild(item);
      });
    }

    var hiddenBox = el("div", "mp-hidden");
    hiddenBox.style.display = "none";

    chips.appendChild(search);
    control.appendChild(chips);
    control.appendChild(caret);
    panel.appendChild(head);
    panel.appendChild(list);
    root.appendChild(control);
    root.appendChild(panel);
    root.appendChild(hiddenBox);

    select.parentNode.insertBefore(root, select.nextSibling);

    function checkedBoxes() {
      return Array.from(list.querySelectorAll(".mp-checkbox:checked"));
    }
    function syncFromCheckboxes() {
      hiddenBox.innerHTML = "";
      checkedBoxes().forEach(function (cb) {
        var h = document.createElement("input");
        h.type = "hidden";
        h.name = name;
        h.value = cb.value;
        hiddenBox.appendChild(h);
      });
      // Sync the underlying <select> too.
      options.forEach(function (o) {
        var opt = Array.from(select.options).find(function (x) {
          return x.value === o.value;
        });
        if (opt) opt.selected = checkedBoxes().some(function (cb) {
          return cb.value === o.value;
        });
      });
      // Chips
      chips.querySelectorAll(".mp-chip").forEach(function (c) { c.remove(); });
      checkedBoxes().forEach(function (cb) {
        var item = cb.closest(".mp-item");
        if (!item) return;
        var label = item.querySelector(".mp-label").textContent.trim();
        var chip = el("span", "mp-chip");
        var nameSpan = el("span", "mp-chip-label");
        nameSpan.textContent = label;
        var x = el("button", "mp-chip-x", "×");
        x.type = "button";
        x.dataset.value = cb.value;
        chip.appendChild(nameSpan);
        chip.appendChild(x);
        chips.insertBefore(chip, search);
      });
      count.textContent = checkedBoxes().length + " selected";
      // Fire change so HTMX forms pick it up.
      select.dispatchEvent(new Event("change", { bubbles: true }));
    }

    list.addEventListener("change", function (e) {
      if (e.target.classList.contains("mp-checkbox")) syncFromCheckboxes();
    });

    chips.addEventListener("click", function (e) {
      var x = e.target.closest(".mp-chip-x");
      if (!x) return;
      e.stopPropagation();
      var cb = list.querySelector('.mp-checkbox[value="' +
        (window.CSS && CSS.escape ? CSS.escape(x.dataset.value) : x.dataset.value) + '"]');
      if (cb) cb.checked = false;
      syncFromCheckboxes();
    });

    clear.addEventListener("click", function () {
      list.querySelectorAll(".mp-checkbox").forEach(function (cb) {
        cb.checked = false;
      });
      syncFromCheckboxes();
    });

    search.addEventListener("input", function () {
      var q = search.value.trim().toLowerCase();
      list.querySelectorAll(".mp-item").forEach(function (it) {
        if (it.classList.contains("mp-item-empty")) return;
        it.style.display = (!q || it.dataset.search.indexOf(q) !== -1) ? "" : "none";
      });
    });

    function open()  { panel.style.display = ""; setTimeout(function () { search.focus(); }, 0); }
    function close() { panel.style.display = "none"; }

    control.addEventListener("click", function (e) {
      if (e.target.closest(".mp-chip-x")) return;
      panel.style.display === "none" ? open() : close();
    });
    search.addEventListener("focus", open);
    caret.addEventListener("click", function (e) {
      e.stopPropagation();
      panel.style.display === "none" ? open() : close();
    });
    document.addEventListener("click", function (e) {
      if (!root.contains(e.target)) close();
    });
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape") close();
    });

    syncFromCheckboxes();
  }

  function enhance(root) {
    root = root || document;
    // 1. Explicit marker
    root.querySelectorAll("select[data-multi-picker]").forEach(build);
    // 2. Any <select multiple> inside a form (auto)
    root.querySelectorAll("form select[multiple]").forEach(build);
  }

  window.NBTE = window.NBTE || {};
  window.NBTE.MultiPicker = { enhance: enhance };

  document.addEventListener("DOMContentLoaded", function () { enhance(); });
  document.addEventListener("htmx:afterSwap", function (evt) {
    enhance(evt.detail && evt.detail.target ? evt.detail.target : document);
  });
  document.body && document.body.addEventListener("htmx:load", function (evt) {
    enhance(evt.detail && evt.detail.elt ? evt.detail.elt : document);
  });
})();