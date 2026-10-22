/* =========================================================================
   Select and act in bulk (Phase 8 UX)

   Markup (rendered by the {% bulk_check %}, {% bulk_all %} and
   {% bulk_bar %} tags in core/templatetags/ux_tags.py):
     <form id="bulk-x" class="bulk-bar" data-uxb-bar="bulk-x" ...>  ← the bar
     <input type="checkbox" data-uxb-all="bulk-x">                 ← select all on page
     <input type="checkbox" name="ids" value="7" form="bulk-x" data-uxb-item="bulk-x">
   A row is the checkbox's closest [data-uxb-row] (or <tr>/<li>/<article>).

   Behaviour: the bar slides up showing "N selected · actions · Cancel";
   destructive verbs ask NBTEConfirm first; the rows disappear at once
   (optimistic) and come back with a toast if the server says no or the
   network fails. Works without JS as a plain form post (the server
   redirects back with a message). Esc or Cancel clears the selection.
   Everything is delegated, so HTMX-swapped lists keep working.
   ========================================================================= */
(function () {
  "use strict";
  if (window.NBTEBulk) return;
  document.documentElement.classList.add("bulk-js");
  document.documentElement.classList.add("bulk-modes");

  function $all(sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); }
  function items(id) { return $all('input[data-uxb-item="' + id + '"]'); }
  function bar(id) { return document.querySelector('[data-uxb-bar="' + id + '"]'); }
  function rowOf(input) {
    return input.closest("[data-uxb-row]") || input.closest("tr, li, article") || input.parentElement;
  }
  function toast(kind, title, body) {
    if (window.NBTE && NBTE.Toast && NBTE.Toast.push) NBTE.Toast.push(kind, title, body || "", { duration: 6000 });
  }

  function refresh(id) {
    var b = bar(id);
    if (!b) return;
    var list = items(id).filter(function (i) { return !i.disabled && i.getClientRects().length; });
    var checked = list.filter(function (i) { return i.checked; });
    var n = checked.length;
    var noun = n === 1 ? (b.getAttribute("data-uxb-noun") || "item") : (b.getAttribute("data-uxb-nouns") || "items");
    var count = b.querySelector("[data-uxb-count]");
    if (count) count.textContent = n + " " + noun + " selected";
    b.classList.toggle("is-visible", n > 0);
    document.documentElement.classList.toggle("has-bulk-bar", !!document.querySelector(".bulk-bar.is-visible"));
    list.forEach(function (i) {
      var r = rowOf(i);
      if (r) r.classList.toggle("is-bulk-selected", i.checked);
    });
    $all('input[data-uxb-all="' + id + '"]').forEach(function (all) {
      all.checked = n > 0 && n === list.length;
      all.indeterminate = n > 0 && n < list.length;
    });
  }

  function clear(id) {
    items(id).forEach(function (i) { i.checked = false; });
    refresh(id);
  }

  // Select mode: the checkboxes stay hidden until the "Select" button is
  // pressed. Lists without a Select button (no {% bulk_all %}) always show them.
  var modeOn = {};
  function hasToggle(id) { return !!document.querySelector('[data-uxb-toggle="' + id + '"]'); }
  function applyMode(id) {
    var on = !hasToggle(id) || !!modeOn[id];
    items(id).forEach(function (i) {
      var box = i.closest("[data-uxb-check]");
      if (box) box.classList.toggle("is-on", on);
    });
    $all('input[data-uxb-all="' + id + '"]').forEach(function (a) {
      var box = a.closest("[data-uxb-check]");
      if (box) box.classList.toggle("is-on", on);
    });
    $all('[data-uxb-toggle="' + id + '"]').forEach(function (btn) {
      btn.setAttribute("aria-pressed", on && hasToggle(id) && modeOn[id] ? "true" : "false");
      btn.classList.toggle("is-on", !!modeOn[id]);
      var l = btn.querySelector("[data-uxb-toggle-label]");
      if (l) l.textContent = modeOn[id] ? "Done" : "Select";
    });
    $all('[data-uxb-mode="' + id + '"]').forEach(function (m) { m.classList.toggle("is-on", !!modeOn[id]); });
    document.documentElement.classList.toggle("bulk-any-on", Object.keys(modeOn).some(function (k) { return modeOn[k]; }));
  }
  function setMode(id, on) {
    modeOn[id] = !!on;
    if (!on) clear(id);
    applyMode(id);
    if (on) {
      var first = items(id).filter(function (i) { return i.getClientRects().length; })[0];
      if (first) first.focus({ preventScroll: true });
    }
  }
  function allIds() {
    var ids = {};
    $all("[data-uxb-bar]").forEach(function (b) { ids[b.getAttribute("data-uxb-bar")] = 1; });
    $all("[data-uxb-toggle]").forEach(function (b) { ids[b.getAttribute("data-uxb-toggle")] = 1; });
    return Object.keys(ids);
  }
  function applyAll() { allIds().forEach(applyMode); }

  document.addEventListener("change", function (e) {
    var t = e.target;
    if (!(t instanceof HTMLInputElement)) return;
    var id = t.getAttribute("data-uxb-item");
    if (id) { refresh(id); return; }
    id = t.getAttribute("data-uxb-all");
    if (id) {
      items(id).forEach(function (i) { if (!i.disabled && i.getClientRects().length) i.checked = t.checked; });
      refresh(id);
    }
  });

  // Shift-click selects a range.
  var lastClicked = {};
  document.addEventListener("click", function (e) {
    var t = e.target;
    if (t instanceof HTMLInputElement && t.hasAttribute("data-uxb-item")) {
      var id = t.getAttribute("data-uxb-item");
      var list = items(id);
      var prev = lastClicked[id];
      if (e.shiftKey && prev && prev !== t && list.indexOf(prev) !== -1) {
        var a = list.indexOf(prev), b = list.indexOf(t);
        list.slice(Math.min(a, b), Math.max(a, b) + 1).forEach(function (i) { i.checked = t.checked; });
        refresh(id);
      }
      lastClicked[id] = t;
      return;
    }
    var toggle = t.closest && t.closest("[data-uxb-toggle]");
    if (toggle) {
      var tid = toggle.getAttribute("data-uxb-toggle");
      setMode(tid, !modeOn[tid]);
      return;
    }
    var cancel = t.closest && t.closest("[data-uxb-cancel]");
    if (cancel) {
      var form = cancel.closest("[data-uxb-bar]");
      if (form) {
        var cid = form.getAttribute("data-uxb-bar");
        if (hasToggle(cid)) setMode(cid, false); else clear(cid);
      }
    }
  });

  document.addEventListener("keydown", function (e) {
    if (e.key !== "Escape") return;
    if (document.querySelector(".nbte-confirm, .fpv, [role=dialog][aria-modal=true]:not([hidden])")) return;
    var open = document.querySelector(".bulk-bar.is-visible");
    if (open) { clear(open.getAttribute("data-uxb-bar")); return; }
    Object.keys(modeOn).forEach(function (id) { if (modeOn[id]) setMode(id, false); });
  });

  function restoreRows(rows) {
    rows.forEach(function (r) { r.classList.remove("is-bulk-removing"); r.style.removeProperty("height"); });
  }

  function run(form, submitter) {
    var id = form.getAttribute("data-uxb-bar");
    var checked = items(id).filter(function (i) { return i.checked; });
    if (!checked.length) return;
    var verb = submitter ? submitter.value : "";
    var label = submitter ? (submitter.getAttribute("data-uxb-label") || verb) : verb;
    var removes = submitter && submitter.getAttribute("data-uxb-removes") === "1";
    var danger = submitter && submitter.getAttribute("data-uxb-tone") === "danger";
    var n = checked.length;
    var noun = n === 1 ? form.getAttribute("data-uxb-noun") : form.getAttribute("data-uxb-nouns");

    var confirmFirst = danger && window.NBTEConfirm
      ? NBTEConfirm.open({
          title: (form.getAttribute("data-uxb-confirm-title") || (label + " {n} {noun}?"))
            .replace("{n}", n).replace("{noun}", noun),
          body: (form.getAttribute("data-uxb-confirm-body") || "This can't be undone.").replace("{n}", n).replace("{noun}", noun),
          confirmLabel: label + " " + n,
          tone: "danger"
        })
      : Promise.resolve(true);

    confirmFirst.then(function (ok) {
      if (!ok) return;
      var byId = {};
      checked.forEach(function (i) { byId[i.value] = rowOf(i); });
      var rows = checked.map(rowOf).filter(Boolean);
      if (removes) {
        rows.forEach(function (r) {
          r.style.height = r.getBoundingClientRect().height + "px";
          void r.offsetHeight;
          r.classList.add("is-bulk-removing");
        });
      }
      form.classList.add("is-busy");
      var data = new FormData(form);
      data.delete("ids");
      checked.forEach(function (i) { data.append("ids", i.value); });
      data.set("action", verb);
      // form.action is shadowed by the <button name="action"> controls.
      fetch(form.getAttribute("action"), {
        method: "POST",
        body: data,
        credentials: "same-origin",
        headers: { "Accept": "application/json", "X-Requested-With": "fetch" }
      })
        .then(function (r) {
          return r.json().catch(function () { return { ok: false, message: "" }; })
            .then(function (j) { j.ok = j.ok && r.ok; return j; });
        })
        .then(function (res) {
          form.classList.remove("is-busy");
          if (!res.ok) {
            restoreRows(rows);
            toast("error", "Nothing was changed", res.message || "The server refused this action. Try again.");
            return;
          }
          var done = (res.done || []).map(String);
          var skipped = res.skipped || [];
          skipped.forEach(function (s) {
            var r = byId[String(s.id)];
            if (r) restoreRows([r]);
          });
          done.forEach(function (d) {
            var r = byId[d];
            var input = r && r.querySelector('input[data-uxb-item="' + id + '"]');
            if (input) input.checked = false;
            if (r && removes) window.setTimeout(function () { r.remove(); refresh(id); maybeEmpty(id); }, 260);
          });
          skipped.forEach(function (s) {
            var r = byId[String(s.id)];
            var input = r && r.querySelector('input[data-uxb-item="' + id + '"]');
            if (input) input.checked = true;
          });
          refresh(id);
          var reasons = skipped.map(function (s) { return s.reason; }).filter(Boolean);
          toast(done.length ? "success" : "warning", res.message || (label + " done"),
                reasons.length ? Array.from(new Set(reasons)).join(" ") : "");
          if (!removes && done.length) window.setTimeout(function () { window.location.reload(); }, 600);
          document.dispatchEvent(new CustomEvent("nbte:bulk-done", { detail: { form: id, action: verb, result: res } }));
        })
        .catch(function () {
          form.classList.remove("is-busy");
          restoreRows(rows);
          toast("error", "Nothing was changed", "We couldn't reach the server. Check your connection and try again.");
        });
    });
  }

  function maybeEmpty(id) {
    if (items(id).length) return;
    // The page is now empty: reload so pagination and the empty state are right.
    window.setTimeout(function () { window.location.reload(); }, 300);
  }

  document.addEventListener("submit", function (e) {
    var form = e.target;
    if (!(form instanceof HTMLFormElement) || !form.hasAttribute("data-uxb-bar")) return;
    if (!window.fetch || !window.FormData) return; // plain post fallback
    e.preventDefault();
    run(form, e.submitter || form.querySelector("button[name=action]"));
  });

  // Lists swapped by HTMX: recount.
  document.addEventListener("htmx:afterSettle", function () {
    applyAll();
    $all("[data-uxb-bar]").forEach(function (b) { refresh(b.getAttribute("data-uxb-bar")); });
  });
  window.addEventListener("pageshow", function () {
    applyAll();
    $all("[data-uxb-bar]").forEach(function (b) { refresh(b.getAttribute("data-uxb-bar")); });
  });
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", applyAll); else applyAll();

  window.NBTEBulk = { refresh: refresh, clear: clear, select: setMode };
})();
