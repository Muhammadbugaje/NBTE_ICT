/* Advanced query builder (staff/query/*). Reads the field schema from
   #qb-schema (json_script), narrows operators per field, swaps the value
   input (text / date / number / select), adds and removes rows, copies the
   live conditions into the Save dialog, and confirms saved-query deletes. */
(function () {
  "use strict";

  var NO_VALUE = { empty: true, notempty: true };
  var uid = 0;

  function $(sel, root) { return (root || document).querySelector(sel); }
  function $all(sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); }

  function readSchema() {
    var el = document.getElementById("qb-schema");
    if (!el) return null;
    try { return JSON.parse(el.textContent); } catch (e) { return null; }
  }

  function inputType(kind) {
    if (kind === "date") return "text";       // allows 'today-12m' as well as dates
    if (kind === "int" || kind === "grade") return "text";
    return "text";
  }

  function placeholder(kind, op) {
    if (op === "in") return kind === "int" || kind === "grade" ? "e.g. 7, 8, 9" : "comma-separated";
    if (kind === "date") return "YYYY-MM-DD or today-12m";
    if (kind === "int" || kind === "grade") return "number";
    return "";
  }

  function buildSelect(options, current, name, extraAttr) {
    var s = document.createElement("select");
    s.className = "input";
    s.name = name;
    if (extraAttr) s.setAttribute(extraAttr, "");
    var blank = document.createElement("option");
    blank.value = "";
    blank.textContent = "Choose…";
    s.appendChild(blank);
    options.forEach(function (o) {
      var opt = document.createElement("option");
      opt.value = o[0];
      opt.textContent = o[1] + (o[2] ? " (outside your department)" : "");
      if (o[2]) opt.disabled = true;
      if (String(o[0]) === String(current)) opt.selected = true;
      s.appendChild(opt);
    });
    return s;
  }

  function initRow(row, schema) {
    var field = $("[data-qb-field]", row);
    var op = $("[data-qb-op]", row);
    var cell = $("[data-qb-value-cell] .qb-values", row);
    var label = $("[data-qb-value-cell] .qb-label", row);
    if (!field || !op || !cell) return;
    uid += 1;
    var base = "qb-" + uid;
    field.id = base + "-field"; $(".qb-cell--field .qb-label", row).setAttribute("for", field.id);
    op.id = base + "-op"; $(".qb-cell--op .qb-label", row).setAttribute("for", op.id);

    function currentValue(sel) { var el = $(sel, cell); return el ? el.value : ""; }

    function renderValue() {
      var meta = schema[field.value];
      var v1 = currentValue("[data-qb-value]");
      var v2 = currentValue("[data-qb-value2]");
      cell.innerHTML = "";
      var main;
      if (meta && (meta.kind === "fk" || meta.kind === "choice") && (op.value === "eq" || op.value === "ne")) {
        main = buildSelect(meta.options || [], v1, "value", "data-qb-value");
      } else if (meta && meta.kind === "date" && op.value !== "in") {
        main = document.createElement("input");
        main.type = "text";
        main.className = "input";
        main.name = "value";
        main.value = v1;
        main.setAttribute("data-qb-value", "");
        main.setAttribute("inputmode", "numeric");
        main.setAttribute("list", "qb-date-hints");
      } else {
        main = document.createElement("input");
        main.type = inputType(meta && meta.kind);
        main.className = "input";
        main.name = "value";
        main.value = v1;
        main.setAttribute("data-qb-value", "");
        if (meta && meta.kind === "fk" && op.value !== "in") main.setAttribute("list", base + "-opts");
      }
      main.id = base + "-value";
      main.autocomplete = "off";
      main.placeholder = placeholder(meta && meta.kind, op.value);
      if (label) label.setAttribute("for", main.id);
      cell.appendChild(main);
      if (meta && meta.kind === "fk" && main.tagName === "INPUT") {
        var dl = document.createElement("datalist");
        dl.id = base + "-opts";
        (meta.options || []).forEach(function (o) {
          if (o[2]) return;
          var opt = document.createElement("option");
          opt.value = o[0];
          dl.appendChild(opt);
        });
        cell.appendChild(dl);
      }
      var and = document.createElement("span");
      and.className = "qb-and";
      and.textContent = "and";
      and.setAttribute("data-qb-and", "");
      var second = document.createElement("input");
      second.type = "text";
      second.className = "input";
      second.name = "value2";
      second.value = v2;
      second.setAttribute("data-qb-value2", "");
      second.setAttribute("aria-label", "Second value");
      second.placeholder = placeholder(meta && meta.kind, "between");
      second.autocomplete = "off";
      var between = op.value === "between";
      and.hidden = !between;
      second.hidden = !between;
      second.disabled = !between;
      cell.appendChild(and);
      cell.appendChild(second);
      var none = !!NO_VALUE[op.value];
      main.hidden = none;
      main.disabled = none;
      row.classList.toggle("qb-row--novalue", none);
      row.classList.toggle("qb-row--between", between);
    }

    function renderOps() {
      var meta = schema[field.value];
      var keep = op.value;
      op.innerHTML = "";
      var ops = meta ? meta.ops : [["eq", "equals"]];
      ops.forEach(function (o) {
        var opt = document.createElement("option");
        opt.value = o[0];
        opt.textContent = o[1];
        op.appendChild(opt);
      });
      if (ops.some(function (o) { return o[0] === keep; })) op.value = keep;
      op.disabled = !meta;
      renderValue();
    }

    field.addEventListener("change", renderOps);
    op.addEventListener("change", renderValue);
    renderOps();
  }

  function initBuilder(form, schema) {
    var list = $("[data-qb-rows]", form);
    var tpl = $("[data-qb-template]", form);
    $all("[data-qb-row]", list).forEach(function (r) { initRow(r, schema); });

    function renumber() {
      var rows = $all("[data-qb-row]", list);
      rows.forEach(function (r, i) {
        var btn = $("[data-qb-remove]", r);
        if (btn) btn.setAttribute("aria-label", "Remove condition " + (i + 1));
      });
      list.classList.toggle("qb-rows--single", rows.length === 1);
    }

    $("[data-qb-add]", form).addEventListener("click", function () {
      var frag = tpl.content.cloneNode(true);
      var row = frag.querySelector("[data-qb-row]");
      list.appendChild(frag);
      initRow(row, schema);
      renumber();
      var f = $("[data-qb-field]", row);
      if (f) f.focus();
    });
    list.addEventListener("click", function (e) {
      var btn = e.target.closest("[data-qb-remove]");
      if (!btn) return;
      var rows = $all("[data-qb-row]", list);
      var row = btn.closest("[data-qb-row]");
      if (rows.length === 1) {
        // Keep one row; just clear it.
        $("[data-qb-field]", row).value = "";
        $("[data-qb-field]", row).dispatchEvent(new Event("change"));
        return;
      }
      var next = row.nextElementSibling || row.previousElementSibling;
      row.remove();
      renumber();
      var focus = next && $("[data-qb-field]", next);
      if (focus) focus.focus();
    });
    form.addEventListener("submit", function () {
      // Drop untouched empty rows so the URL stays clean.
      $all("[data-qb-row]", list).forEach(function (r) {
        if (!$("[data-qb-field]", r).value) $all("select, input", r).forEach(function (el) { el.disabled = true; });
      });
    });
    renumber();

    // Save dialog: copy the live builder state into hidden fields.
    var saveForm = $("[data-qb-save-form]");
    if (saveForm) {
      saveForm.addEventListener("submit", function () {
        var box = $("[data-qb-save-fields]", saveForm);
        box.innerHTML = "";
        function hidden(name, value) {
          var i = document.createElement("input");
          i.type = "hidden"; i.name = name; i.value = value || "";
          box.appendChild(i);
        }
        var logic = $("input[name=logic]:checked", form);
        hidden("logic", logic ? logic.value : "AND");
        $all("[data-qb-row]", list).forEach(function (r) {
          var f = $("[data-qb-field]", r).value;
          if (!f) return;
          hidden("field", f);
          hidden("op", $("[data-qb-op]", r).value);
          var v = $("[data-qb-value]", r);
          var v2 = $("[data-qb-value2]", r);
          hidden("value", v && !v.disabled ? v.value : "");
          hidden("value2", v2 && !v2.disabled ? v2.value : "");
        });
      });
    }

    var results = $("[data-qb-preview-toast]");
    if (results && window.NBTE && NBTE.toast) {
      NBTE.toast("You're seeing a preview; click Save to keep this query.", "info", { duration: 5000 });
    }

    // Relative-date suggestions for date fields.
    if (!document.getElementById("qb-date-hints")) {
      var dl = document.createElement("datalist");
      dl.id = "qb-date-hints";
      ["today", "today-12m", "today-6m", "today-30d", "today+30d", "today-35y"].forEach(function (v) {
        var o = document.createElement("option"); o.value = v; dl.appendChild(o);
      });
      document.body.appendChild(dl);
    }
  }

  function initDeletes() {
    var modalEl = $("[data-qb-delete-modal]");
    if (!modalEl || !window.NBTE || !NBTE.modal) return;
    var pending = null;
    $all("[data-qb-delete]").forEach(function (f) {
      f.addEventListener("submit", function (e) {
        if (f.getAttribute("data-confirmed")) return;
        e.preventDefault();
        pending = f;
        $("[data-qb-delete-label]", modalEl).textContent = f.getAttribute("data-qb-delete-name");
        NBTE.modal(modalEl).open();
      });
    });
    $("[data-qb-delete-confirm]", modalEl).addEventListener("click", function () {
      if (!pending) return;
      pending.setAttribute("data-confirmed", "1");
      pending.submit();
    });
  }

  function boot() {
    var schema = readSchema();
    var form = $("[data-qb-form]");
    if (form && schema) initBuilder(form, schema);
    initDeletes();
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();
})();
