/*
  NBTE QA — SSQ workspace autosave (Phase 3, §20).

  The section form ([data-ssq-form]) is saved by HTMX (hx-trigger="ssq:save").
  This script decides *when*: after a pause in typing (data-ssq-delay ms),
  at once on a choice/checkbox change, before moving to another section, and
  before submitting. It also keeps the "Saving… / Saved / Not saved" status
  honest, adds and removes table rows, and warns before leaving with unsaved
  answers. Everything is event delegation, so it survives boosted navigation.
*/
(function () {
  "use strict";

  var timer = null;
  var dirty = false;
  var saving = false;

  function form() { return document.querySelector("[data-ssq-form]"); }

  function setStatus(state, text) {
    var el = document.getElementById("ssq-save-status");
    if (!el) return;
    el.className = "qa-save-status is-" + state;
    el.setAttribute("data-ssq-state", state);
    el.innerHTML = '<span class="qa-save-status__dot" aria-hidden="true"></span>' + text;
  }

  function isAnswer(target) {
    return target && target.name && target.name.indexOf("f__") === 0 && !target.hasAttribute("data-ssq-ignore") &&
      target.closest && target.closest("[data-ssq-form]");
  }

  function saveNow() {
    var f = form();
    if (timer) { window.clearTimeout(timer); timer = null; }
    if (!f || !dirty || !window.htmx) return false;
    dirty = false;
    saving = true;
    setStatus("saving", "Saving…");
    window.htmx.trigger(f, "ssq:save");
    return true;
  }

  function schedule(delay) {
    var f = form();
    if (!f) return;
    dirty = true;
    setStatus("dirty", "Unsaved changes…");
    if (timer) window.clearTimeout(timer);
    var ms = delay != null ? delay : parseInt(f.getAttribute("data-ssq-delay") || "900", 10);
    timer = window.setTimeout(saveNow, ms);
  }

  document.addEventListener("input", function (e) {
    if (isAnswer(e.target) && e.target.type !== "checkbox" && e.target.type !== "radio") schedule();
  });
  document.addEventListener("change", function (e) {
    if (!isAnswer(e.target)) return;
    var quick = e.target.type === "checkbox" || e.target.type === "radio" || e.target.tagName === "SELECT";
    schedule(quick ? 150 : null);
  });

  // Table rows: add from the <template>, remove, then save.
  document.addEventListener("click", function (e) {
    var add = e.target.closest && e.target.closest("[data-ssq-add-row]");
    if (add) {
      var key = add.getAttribute("data-ssq-add-row");
      var wrap = add.closest(".qa-ssq-field");
      var tpl = wrap && wrap.querySelector('template[data-ssq-row-template="' + key + '"]');
      var body = wrap && wrap.querySelector("[data-ssq-rows]");
      if (!tpl || !body) return;
      var index = body.querySelectorAll("[data-ssq-row]").length;
      // Never reuse an index already present (rows may have been removed).
      var used = {};
      Array.prototype.forEach.call(body.querySelectorAll("input[name]"), function (i) {
        var m = i.name.match(/__(\d+)__/);
        if (m) used[m[1]] = true;
      });
      while (used[String(index)]) index += 1;
      var html = tpl.innerHTML.replace(/__INDEX__/g, "__" + index + "__");
      body.insertAdjacentHTML("beforeend", html);
      var first = body.lastElementChild && body.lastElementChild.querySelector("input");
      if (first) first.focus();
      return;
    }
    var rm = e.target.closest && e.target.closest("[data-ssq-remove-row]");
    if (rm) {
      var row = rm.closest("[data-ssq-row]");
      if (row) { row.remove(); schedule(150); }
    }
  });

  // After the save request: dirty again if the person kept typing.
  document.addEventListener("htmx:afterRequest", function (e) {
    var elt = e.detail && e.detail.elt;
    if (!elt || !elt.hasAttribute || !elt.hasAttribute("data-ssq-form")) return;
    saving = false;
    if (!e.detail.successful) {
      dirty = true;
      setStatus("error", "Not saved — check your connection; we will retry.");
      window.setTimeout(function () { if (dirty) saveNow(); }, 4000);
    } else if (dirty) {
      setStatus("dirty", "Unsaved changes…");
    }
  });

  // Moving to another section, submitting or sending: save first.
  document.addEventListener("htmx:confirm", function (e) {
    var elt = e.detail && e.detail.elt;
    if (!elt || !elt.closest) return;
    var leaving = elt.closest("[data-ssq-nav]") || elt.closest("[data-ssq-flush]") ||
      (elt.closest("#qa-main") && !elt.closest("[data-ssq-form]") && elt.getAttribute("hx-get"));
    if (!leaving || (!dirty && !saving)) return;
    e.preventDefault();
    var f = form();
    var go = function () { e.detail.issueRequest(true); };
    if (!f) { go(); return; }
    var done = function (ev) {
      if (ev.detail && ev.detail.elt === f) { document.removeEventListener("htmx:afterRequest", done); go(); }
    };
    document.addEventListener("htmx:afterRequest", done);
    if (!saveNow() && !saving) { document.removeEventListener("htmx:afterRequest", done); go(); }
  });

  window.addEventListener("beforeunload", function (e) {
    if (dirty || saving) { e.preventDefault(); e.returnValue = ""; }
  });
})();
