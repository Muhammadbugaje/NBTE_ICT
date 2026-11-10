/* ==========================================================================
   NBTE person picker (SOP-03) — templates/qa_core/partials/_person_picker.html

   Enhances <div data-pp><select class="npk__native">…</select></div> into an
   accessible combobox: a trigger showing the chosen person(s) and a roomy
   panel with a search box (name, staff no., rank, department), status filter
   chips (Recommended / Available / Busy / On leave / Not allowed), a
   department filter, and the people grouped by status — Recommended first —
   each with avatar, rank & grade, department, workload and reason chips.

   Desktop: the panel floats on <body> (position: fixed) anchored to the field,
   at least 520px wide (never wider than the viewport − 32px), flipping above
   the field when there is no room below — so it is never narrow, even in a
   table cell. Phones (≤ 760px): a bottom sheet.

   The <select> stays in the form (hidden) and is what is submitted, so the
   server and the no-JS fallback see the same field. Works after HTMX swaps
   (htmx:load). Keyboard: ↑ ↓ Home End move, Enter chooses, Esc closes,
   typing filters; Tab reaches the filters.
   ========================================================================== */
(function () {
  "use strict";

  var uid = 0;
  var open = null; // the open picker's state
  var STATUS = [
    ["recommended", "Recommended"], ["available", "Available"], ["busy", "Busy"],
    ["away", "On leave"], ["locked", "Not allowed"]
  ];
  var STATUS_LABEL = {};
  STATUS.forEach(function (s) { STATUS_LABEL[s[0]] = s[1]; });

  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }

  var SVG = {
    chevron: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="6 9 12 15 18 9"/></svg>',
    search: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="11" cy="11" r="7"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>',
    check: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="20 6 9 17 4 12"/></svg>',
    x: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>',
    plus: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>'
  };

  function isPhone() { return !!(window.matchMedia && window.matchMedia("(max-width: 760px)").matches); }

  function person(opt) {
    var d = opt.dataset;
    var reasons = (d.reasons || "").split("|").filter(Boolean).map(function (r) {
      var i = r.indexOf(":");
      return i > 0 ? { tone: r.slice(0, i), text: r.slice(i + 1) } : { tone: "info", text: r };
    });
    var blocked = d.blocked || "";
    var status = d.status || (blocked ? "locked" : d.away ? "away" : d.suggested === "1" ? "recommended" : "available");
    if (!d.reasons) {
      if (blocked) reasons.push({ tone: "bad", text: blocked });
      if (d.away) reasons.push({ tone: "warn", text: d.away });
    }
    var text = opt.textContent.trim();
    var name = d.name || text.split(" · ")[0];
    if (!d.name && !d.note && text.indexOf(" · ") > 0) d.note = text.slice(text.indexOf(" · ") + 3);
    return {
      value: opt.value, name: name, initials: d.initials || name.split(/\s+/).slice(0, 2).map(function (w) { return w.charAt(0); }).join("").toUpperCase() || "?",
      rank: d.rank || "", grade: d.grade || "", dept: d.dept || "", staffno: d.staffno || "",
      tasks: d.tasks != null ? (parseInt(d.tasks, 10) || 0) : null,
      cases: parseInt(d.cases || "0", 10) || 0, away: d.away || "", photo: d.photo || "", note: d.note || "",
      suggested: status === "recommended", blocked: blocked || (status === "locked" ? "Not allowed" : ""),
      status: status, reasons: reasons, opt: opt
    };
  }

  function avatar(p, big) {
    var a = el("span", "npk__av" + (big ? " npk__av--lg" : ""));
    a.setAttribute("aria-hidden", "true");
    if (p.photo) {
      var img = el("img"); img.src = p.photo; img.alt = ""; img.loading = "lazy";
      img.onerror = function () { a.textContent = p.initials; };
      a.appendChild(img);
    } else {
      a.textContent = p.initials;
    }
    var h = 0;
    for (var i = 0; i < p.name.length; i++) h = (h * 31 + p.name.charCodeAt(i)) % 6;
    a.setAttribute("data-tone", String(h));
    return a;
  }

  function metaText(p, withNo) {
    var bits = [];
    if (p.rank) bits.push(p.rank);
    if (p.grade) bits.push("GL " + String(p.grade).replace(/^GL\s*/i, ""));
    if (p.dept) bits.push(p.dept);
    if (withNo && p.staffno) bits.push(p.staffno);
    return bits.join(" · ");
  }

  function loadBadge(p) {
    if (p.tasks == null) return null;
    var tone = p.tasks >= 8 ? "is-high" : p.tasks >= 4 ? "is-mid" : "is-low";
    var b = el("span", "npk__load " + tone, p.tasks + " open");
    b.title = p.tasks + " open task" + (p.tasks === 1 ? "" : "s") + (p.cases ? " · " + p.cases + " QA case" + (p.cases === 1 ? "" : "s") : "");
    return b;
  }

  function statusChip(status) {
    return el("span", "npk__status is-" + status, STATUS_LABEL[status] || status);
  }

  function summary(p) {
    var wrap = el("span", "npk__value");
    wrap.appendChild(avatar(p));
    var body = el("span", "npk__body");
    body.appendChild(el("span", "npk__name", p.name));
    var meta = metaText(p);
    if (meta) body.appendChild(el("span", "npk__meta", meta));
    wrap.appendChild(body);
    if (p.status === "busy" || p.status === "away") wrap.appendChild(statusChip(p.status));
    return wrap;
  }

  function setup(root) {
    if (root.hasAttribute("data-pp-ready")) return;
    var select = root.querySelector("select.npk__native");
    if (!select) return;
    root.setAttribute("data-pp-ready", "1");
    uid += 1;
    var id = "pp" + uid;
    var multiple = select.multiple;
    var min = parseInt(root.getAttribute("data-pp-min") || (select.required ? "1" : "0"), 10) || 0;
    var noun = root.getAttribute("data-pp-noun") || "person";
    var placeholder = root.getAttribute("data-pp-placeholder") || "Choose a person";
    var label = root.querySelector(".npk__label");
    var errorBox = root.querySelector(".npk__error");
    var people = Array.prototype.filter.call(select.options, function (o) { return o.value !== ""; }).map(person);
    var title = label ? label.textContent.replace("*", "").trim() : "Choose a person";

    if (select.required) root.setAttribute("data-pp-required", "1");
    select.required = false;
    select.tabIndex = -1;
    select.setAttribute("aria-hidden", "true");
    root.classList.add("npk--enhanced");

    var ui = el("div", "npk__ui");
    var chips = null;
    if (multiple) {
      chips = el("ul", "npk__chips");
      chips.setAttribute("role", "list");
      ui.appendChild(chips);
    }
    var trigger = el("button", "npk__trigger" + (multiple ? " npk__trigger--add" : ""));
    trigger.type = "button";
    trigger.id = id + "-trigger";
    trigger.setAttribute("aria-haspopup", "dialog");
    trigger.setAttribute("aria-expanded", "false");
    trigger.setAttribute("aria-controls", id + "-pop");
    if (label) {
      if (!label.id) label.id = id + "-label";
      trigger.setAttribute("aria-labelledby", label.id + " " + trigger.id);
      label.setAttribute("for", trigger.id);
    }
    ui.appendChild(trigger);
    var clearBtn = null;
    if (!multiple && !root.hasAttribute("data-pp-required")) {
      clearBtn = el("button", "npk__clear");
      clearBtn.type = "button"; clearBtn.innerHTML = SVG.x;
      clearBtn.setAttribute("aria-label", "Clear the choice");
      clearBtn.hidden = true;
      clearBtn.addEventListener("click", function () {
        Array.prototype.forEach.call(select.options, function (o) { o.selected = o.value === ""; });
        changed(); trigger.focus();
      });
      ui.appendChild(clearBtn);
    }

    // ---- the panel --------------------------------------------------------
    var pop = el("div", "npk__pop" + (multiple ? " npk__pop--multi" : ""));
    pop.id = id + "-pop";
    pop.hidden = true;
    pop.setAttribute("role", "dialog");
    pop.setAttribute("aria-label", title);
    var backdrop = el("div", "npk__backdrop");
    backdrop.setAttribute("aria-hidden", "true");
    backdrop.addEventListener("mousedown", function (e) { e.preventDefault(); hide(true); });
    var head = el("div", "npk__head");
    var headText = el("div", "npk__head-text");
    headText.appendChild(el("strong", "", title));
    var tally = el("span", "npk__tally");
    headText.appendChild(tally);
    head.appendChild(headText);
    var close = el("button", "npk__close");
    close.type = "button"; close.innerHTML = SVG.x; close.setAttribute("aria-label", "Close");
    head.appendChild(close);
    pop.appendChild(head);

    var tools = el("div", "npk__tools");
    var searchWrap = el("label", "npk__search");
    searchWrap.innerHTML = SVG.search;
    var input = el("input", "npk__input");
    input.type = "search";
    input.setAttribute("role", "combobox");
    input.setAttribute("aria-autocomplete", "list");
    input.setAttribute("aria-expanded", "true");
    input.setAttribute("aria-controls", id + "-list");
    input.setAttribute("autocomplete", "off");
    input.setAttribute("spellcheck", "false");
    input.placeholder = "Search name, staff no., rank, dept.";
    input.setAttribute("aria-label", "Search people");
    searchWrap.appendChild(input);
    tools.appendChild(searchWrap);

    var depts = [];
    people.forEach(function (p) { if (p.dept && depts.indexOf(p.dept) === -1) depts.push(p.dept); });
    depts.sort();
    var deptSel = null;
    if (depts.length > 1) {
      deptSel = el("select", "npk__dept");
      deptSel.setAttribute("aria-label", "Department");
      deptSel.appendChild(new Option(root.getAttribute("data-pp-dept-label") || "All departments", ""));
      depts.forEach(function (d) { deptSel.appendChild(new Option(d, d)); });
      tools.appendChild(deptSel);
    }
    pop.appendChild(tools);

    var counts = {};
    people.forEach(function (p) { counts[p.status] = (counts[p.status] || 0) + 1; });
    var present = STATUS.filter(function (s) { return counts[s[0]]; });
    var statusFilter = "";
    var filterBar = null, filterBtns = [];
    if (present.length > 1) {
      filterBar = el("div", "npk__filters");
      filterBar.setAttribute("role", "group");
      filterBar.setAttribute("aria-label", "Show");
      [["", "All", people.length]].concat(present.map(function (s) { return [s[0], s[1], counts[s[0]]]; }))
        .forEach(function (f) {
          var b = el("button", "npk__fchip" + (f[0] ? " is-" + f[0] : ""));
          b.type = "button";
          b.setAttribute("aria-pressed", f[0] === "" ? "true" : "false");
          b.dataset.status = f[0];
          b.appendChild(document.createTextNode(f[1] + " "));
          b.appendChild(el("b", "", String(f[2])));
          b.addEventListener("click", function () {
            statusFilter = f[0];
            filterBtns.forEach(function (x) { x.setAttribute("aria-pressed", x === b ? "true" : "false"); });
            filter(); input.focus();
          });
          filterBtns.push(b);
          filterBar.appendChild(b);
        });
      pop.appendChild(filterBar);
    }

    var count = el("p", "npk__count");
    count.setAttribute("aria-live", "polite");
    pop.appendChild(count);

    var list = el("ul", "npk__list");
    list.id = id + "-list";
    list.setAttribute("role", "listbox");
    list.setAttribute("aria-label", title);
    if (multiple) list.setAttribute("aria-multiselectable", "true");
    pop.appendChild(list);

    var empty = el("p", "npk__empty");
    empty.hidden = true;
    pop.appendChild(empty);

    var foot = el("div", "npk__foot");
    var done = el("button", "btn btn-primary btn-block npk__done", multiple ? "Done" : "Close");
    done.type = "button";
    foot.appendChild(done);
    pop.appendChild(foot);
    ui.appendChild(pop);
    select.insertAdjacentElement("afterend", ui);

    var grouped = present.length > 1 || (present.length === 1 && present[0][0] !== "available");
    var heads = {};
    var items = [];
    STATUS.forEach(function (s) {
      var group = people.filter(function (p) { return p.status === s[0]; });
      if (!group.length) return;
      if (grouped) {
        var h = el("li", "npk__group is-" + s[0]);
        h.setAttribute("role", "presentation");
        h.appendChild(el("span", "", s[1]));
        h.appendChild(el("b", "", String(group.length)));
        list.appendChild(h);
        heads[s[0]] = h;
      }
      group.forEach(function (p) {
        var i = items.length;
        var li = el("li", "npk__opt is-" + p.status);
        li.id = id + "-o" + i;
        li.setAttribute("role", "option");
        li.dataset.value = p.value;
        if (p.status === "locked") li.setAttribute("aria-disabled", "true");
        li.appendChild(avatar(p, true));
        var body = el("span", "npk__body");
        var name = el("span", "npk__name");
        name.appendChild(el("span", "npk__nm", p.name));
        if (!grouped && p.status !== "available") name.appendChild(statusChip(p.status));
        body.appendChild(name);
        var meta = metaText(p, true);
        if (meta) body.appendChild(el("span", "npk__meta", meta));
        var rs = p.reasons.slice(0, 4);
        if (rs.length) {
          var why = el("span", "npk__reasons");
          rs.forEach(function (r) { why.appendChild(el("span", "npk__reason is-" + r.tone, r.text)); });
          if (p.reasons.length > 4) why.appendChild(el("span", "npk__reason", "+" + (p.reasons.length - 4)));
          body.appendChild(why);
        } else if (p.note) body.appendChild(el("span", "npk__meta", p.note));
        li.appendChild(body);
        var side = el("span", "npk__side");
        var lb = loadBadge(p);
        if (lb) side.appendChild(lb);
        li.appendChild(side);
        var tick = el("span", "npk__tick");
        tick.innerHTML = SVG.check;
        li.appendChild(tick);
        li.setAttribute("aria-label", p.name + (STATUS_LABEL[p.status] ? ", " + STATUS_LABEL[p.status] : "") +
          (meta ? ", " + meta : "") + (p.reasons.length ? ". " + p.reasons.map(function (r) { return r.text; }).join("; ") : ""));
        li.addEventListener("mousedown", function (e) { e.preventDefault(); });
        li.addEventListener("click", function () { choose(i); });
        list.appendChild(li);
        items.push({
          p: p, li: li,
          hay: (p.name + " " + p.rank + " " + p.dept + " " + p.grade + " gl" + p.grade + " " + p.staffno + " " + (STATUS_LABEL[p.status] || "")).toLowerCase()
        });
      });
    });
    var choosable = people.filter(function (p) { return p.status !== "locked"; }).length;
    tally.textContent = people.length + " " + noun + (people.length === 1 ? "" : "s") +
      (choosable !== people.length ? " · " + choosable + " can be chosen" : "");

    var active = -1;

    function selectedValues() {
      return Array.prototype.filter.call(select.options, function (o) { return o.selected && o.value; })
        .map(function (o) { return o.value; });
    }

    function render() {
      var vals = selectedValues();
      items.forEach(function (it) {
        var on = vals.indexOf(it.p.value) !== -1;
        it.li.setAttribute("aria-selected", on ? "true" : "false");
        it.li.classList.toggle("is-selected", on);
      });
      if (multiple) {
        chips.innerHTML = "";
        vals.forEach(function (v) {
          var it = items.filter(function (x) { return x.p.value === v; })[0];
          if (!it) return;
          var chip = el("li", "npk__chip");
          chip.appendChild(summary(it.p));
          var rm = el("button", "npk__chip-x");
          rm.type = "button"; rm.innerHTML = SVG.x;
          rm.setAttribute("aria-label", "Remove " + it.p.name);
          rm.addEventListener("click", function () { it.p.opt.selected = false; changed(); trigger.focus(); });
          chip.appendChild(rm);
          chips.appendChild(chip);
        });
        var need = Math.max(0, min - vals.length);
        trigger.innerHTML = SVG.plus + "<span></span>";
        trigger.lastChild.textContent = vals.length ? (need ? "Add " + need + " more " + noun + (need > 1 ? "s" : "") : "Add another " + noun)
          : (min > 1 ? "Choose " + min + " " + noun + "s" : placeholder);
        root.classList.toggle("npk--has-value", vals.length > 0);
      } else {
        trigger.innerHTML = "";
        var cur = items.filter(function (x) { return vals.indexOf(x.p.value) !== -1; })[0];
        if (cur) trigger.appendChild(summary(cur.p));
        else trigger.appendChild(el("span", "npk__placeholder", placeholder));
        var chev = el("span", "npk__chev"); chev.innerHTML = SVG.chevron;
        trigger.appendChild(chev);
        root.classList.toggle("npk--has-value", !!cur);
        if (clearBtn) clearBtn.hidden = !cur;
      }
    }

    function changed() {
      render();
      clearError();
      select.dispatchEvent(new Event("change", { bubbles: true }));
    }

    function visible() { return items.filter(function (it) { return !it.li.hidden; }); }
    function selectable() { return visible().filter(function (it) { return it.p.status !== "locked"; }); }

    function setActive(idx, scroll) {
      var vis = visible();
      items.forEach(function (it) { it.li.classList.remove("is-active"); });
      if (!vis.length) { active = -1; input.removeAttribute("aria-activedescendant"); return; }
      active = Math.max(0, Math.min(idx, vis.length - 1));
      var it = vis[active];
      it.li.classList.add("is-active");
      input.setAttribute("aria-activedescendant", it.li.id);
      if (scroll !== false) it.li.scrollIntoView({ block: "nearest" });
    }

    function filter() {
      var q = input.value.trim().toLowerCase();
      var terms = q.split(/\s+/).filter(Boolean);
      var dept = deptSel ? deptSel.value : "";
      var shown = 0, per = {};
      items.forEach(function (it) {
        var ok = terms.every(function (t) { return it.hay.indexOf(t) !== -1; }) &&
          (!dept || it.p.dept === dept) && (!statusFilter || it.p.status === statusFilter);
        it.li.hidden = !ok;
        if (ok) { shown += 1; per[it.p.status] = (per[it.p.status] || 0) + 1; }
      });
      Object.keys(heads).forEach(function (k) {
        heads[k].hidden = !per[k];
        heads[k].lastChild.textContent = String(per[k] || 0);
      });
      list.hidden = shown === 0;
      empty.hidden = shown !== 0;
      if (!shown) {
        empty.textContent = q ? "Nobody matches “" + input.value.trim() + "”" + (dept || statusFilter ? " with these filters." : ".")
          : "Nobody here with these filters.";
      }
      if (multiple) {
        var n = selectedValues().length;
        count.textContent = n + " chosen" + (min ? " of " + min + " needed" : "") + (q || dept || statusFilter ? " · " + shown + " shown" : "");
      } else {
        count.textContent = (q || dept || statusFilter) ? shown + " shown" : "";
      }
      count.hidden = !count.textContent;
      var first = visible().map(function (it) { return it.p.status; }).indexOf(
        (selectable()[0] || { p: {} }).p.status);
      setActive(Math.max(0, first), false);
    }

    function choose(i) {
      var it = items[i];
      if (!it) return;
      if (it.p.status === "locked") {
        count.hidden = false;
        count.textContent = it.p.name + " cannot be chosen: " + (it.p.blocked || "not allowed") + ".";
        return;
      }
      if (multiple) {
        it.p.opt.selected = !it.p.opt.selected;
        changed();
        filter();
        setActive(visible().indexOf(it), false);
      } else {
        Array.prototype.forEach.call(select.options, function (o) { o.selected = false; });
        it.p.opt.selected = true;
        changed();
        hide(true);
      }
    }

    function place() {
      if (pop.hidden || pop.classList.contains("npk__pop--sheet")) return;
      var r = trigger.getBoundingClientRect();
      var vw = document.documentElement.clientWidth, vh = window.innerHeight;
      var width = Math.min(Math.max(r.width, 560), vw - 32);
      var left = Math.min(Math.max(16, r.left), vw - 16 - width);
      var below = vh - r.bottom - 12, above = r.top - 12;
      if (below < 480 && above < 480 || (below < 480 && vh < 700)) {
        // Not enough room next to the field (low on a short screen): a centred dialog instead of a cramped panel.
        width = Math.min(600, vw - 32);
        pop.classList.add("is-centred"); pop.classList.remove("is-above");
        document.documentElement.classList.add("npk-dialog-open");
        if (!backdrop.parentNode) document.body.insertBefore(backdrop, pop);
        pop.style.width = width + "px";
        pop.style.left = Math.round((vw - width) / 2) + "px";
        pop.style.maxHeight = Math.min(640, vh - 32) + "px";
        pop.style.top = "16px"; pop.style.bottom = "auto";
        pop.style.top = Math.max(16, Math.round((vh - pop.offsetHeight) / 2)) + "px";
        return;
      }
      pop.classList.remove("is-centred");
      document.documentElement.classList.remove("npk-dialog-open");
      if (backdrop.parentNode) backdrop.parentNode.removeChild(backdrop);
      var maxH = Math.min(560, Math.max(below, above) - 8);
      pop.style.width = width + "px";
      pop.style.left = left + "px";
      pop.style.maxHeight = Math.max(260, maxH) + "px";
      if (below >= Math.min(420, maxH) || below >= above) {
        pop.style.top = (r.bottom + 6) + "px"; pop.style.bottom = "auto";
        pop.classList.remove("is-above");
      } else {
        pop.style.top = "auto"; pop.style.bottom = (vh - r.top + 6) + "px";
        pop.classList.add("is-above");
      }
    }

    function show() {
      if (open && open !== state) open.hide(false);
      pop.classList.toggle("npk__pop--sheet", isPhone());
      if (!isPhone()) {
        // Room for the panel below the field: bring the field up first (instead of a cramped panel).
        var r0 = trigger.getBoundingClientRect();
        var want = Math.min(520, window.innerHeight * 0.62);
        if (window.innerHeight - r0.bottom < want && r0.top > 140) {
          window.scrollBy({ top: Math.min(r0.top - 120, want - (window.innerHeight - r0.bottom)), behavior: "auto" });
        }
      }
      document.body.appendChild(pop); // never trapped by a table cell, overflow or transform
      pop.hidden = false;
      root.classList.add("is-open");
      trigger.setAttribute("aria-expanded", "true");
      if (isPhone()) document.documentElement.classList.add("npk-sheet-open");
      input.value = "";
      filter();
      place();
      var vals = selectedValues();
      var vis = visible();
      var at = vis.map(function (it) { return it.p.value; }).indexOf(vals[0]);
      if (at >= 0) setActive(at); else { list.scrollTop = 0; }
      open = state;
      window.setTimeout(function () { input.focus({ preventScroll: true }); }, 10);
    }

    function hide(focusTrigger) {
      if (pop.hidden) return;
      pop.hidden = true;
      if (pop.parentNode !== ui) { ui.appendChild(pop); }
      pop.classList.remove("npk__pop--sheet", "is-above", "is-centred");
      document.documentElement.classList.remove("npk-dialog-open");
      if (backdrop.parentNode) backdrop.parentNode.removeChild(backdrop);
      pop.style.cssText = "";
      root.classList.remove("is-open");
      trigger.setAttribute("aria-expanded", "false");
      document.documentElement.classList.remove("npk-sheet-open");
      if (open === state) open = null;
      if (focusTrigger) trigger.focus();
    }

    function clearError() {
      if (!errorBox) return;
      errorBox.hidden = true; errorBox.textContent = "";
      root.classList.remove("has-error");
    }

    function validate() {
      var n = selectedValues().length;
      var needed = root.getAttribute("data-pp-required") ? Math.max(min, 1) : min;
      if (n >= needed) { clearError(); return true; }
      if (errorBox) {
        errorBox.textContent = needed > 1 ? "Choose " + needed + " " + noun + "s (" + n + " chosen)." : "Choose a " + noun + " first.";
        errorBox.hidden = false;
      }
      root.classList.add("has-error");
      trigger.focus();
      return false;
    }

    trigger.addEventListener("click", function () { if (pop.hidden) show(); else hide(true); });
    trigger.addEventListener("keydown", function (e) {
      if (e.key === "ArrowDown" || e.key === "ArrowUp") { e.preventDefault(); show(); }
    });
    close.addEventListener("click", function () { hide(true); });
    done.addEventListener("click", function () { hide(true); });
    input.addEventListener("input", filter);
    if (deptSel) deptSel.addEventListener("change", function () { filter(); });
    pop.addEventListener("keydown", function (e) {
      if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); hide(true); }
    });
    input.addEventListener("keydown", function (e) {
      var vis = visible();
      if (e.key === "ArrowDown") { e.preventDefault(); setActive(active + 1); }
      else if (e.key === "ArrowUp") { e.preventDefault(); setActive(active - 1); }
      else if (e.key === "Home" && !input.value) { e.preventDefault(); setActive(0); }
      else if (e.key === "End" && !input.value) { e.preventDefault(); setActive(vis.length - 1); }
      else if (e.key === "Enter") {
        e.preventDefault();
        if (vis[active]) choose(items.indexOf(vis[active]));
      }
    });
    pop.addEventListener("focusout", function (e) {
      if (e.relatedTarget && !root.contains(e.relatedTarget) && !pop.contains(e.relatedTarget)) hide(false);
    });

    var state = { root: root, hide: hide, validate: validate, pop: pop, place: place };
    root._pp = state;
    render();
  }

  // Plain selects opt in with data-pp-auto (SSQ / onboarding / oversight / official-letter assign):
  // wrap them in the picker markup so they get the same roomy, searchable panel.
  function autoWrap(sel) {
    sel.setAttribute("data-pp-wrapped", "1");
    var wrap = el("div", "npk npk--auto");
    wrap.setAttribute("data-pp", "");
    var first = sel.options[0];
    wrap.setAttribute("data-pp-placeholder", first && !first.value ? first.textContent.replace(/[…\.]+$/, "").trim() : "Choose a person");
    wrap.setAttribute("data-pp-noun", sel.getAttribute("data-pp-noun") || "person");
    var outer = sel.closest("label");
    var lab = !outer && sel.id ? document.querySelector('label[for="' + sel.id + '"]') : null;
    var text = "";
    if (outer) {
      var sp = outer.querySelector("span");
      text = (sp || outer).textContent.trim();
      outer.parentNode.insertBefore(wrap, outer);
      outer.parentNode.removeChild(outer);
    } else if (lab) {
      text = lab.textContent.trim();
      lab.parentNode.insertBefore(wrap, lab);
      lab.parentNode.removeChild(lab);
    } else {
      sel.parentNode.insertBefore(wrap, sel);
    }
    var l = el("label", "npk__label", text || "Person");
    if (sel.id) { l.id = sel.id + "-label"; l.setAttribute("for", sel.id); }
    wrap.appendChild(l);
    sel.classList.add("npk__native");
    wrap.appendChild(sel);
    var err = el("p", "npk__error"); err.hidden = true; err.setAttribute("role", "alert");
    wrap.appendChild(err);
  }

  function initAll(scope) {
    var base = (scope && scope.querySelectorAll ? scope : document);
    Array.prototype.forEach.call(base.querySelectorAll("select[data-pp-auto]:not([data-pp-wrapped])"), autoWrap);
    var nodes = Array.prototype.slice.call(base.querySelectorAll("[data-pp]:not([data-pp-ready])"));
    if (base.matches && base.matches("[data-pp]:not([data-pp-ready])")) nodes.unshift(base);
    nodes.forEach(setup);
  }

  // Close on an outside press; keep the floating panel anchored while scrolling.
  document.addEventListener("mousedown", function (e) {
    if (open && !open.root.contains(e.target) && !open.pop.contains(e.target)) open.hide(false);
  });
  window.addEventListener("resize", function () { if (open) open.place(); });
  window.addEventListener("scroll", function (e) {
    if (open && !(e.target && e.target.nodeType === 1 && open.pop.contains(e.target))) open.place();
  }, true);
  // An HTMX swap that removes the picker also closes its floating panel.
  document.addEventListener("htmx:beforeSwap", function () {
    if (open) { var s = open; if (s.pop.parentNode === document.body) s.hide(false); }
  });

  // Required pickers: check before the form goes (HTMX listens on the form; this runs first).
  document.addEventListener("submit", function (e) {
    var form = e.target;
    if (!form || !form.querySelectorAll) return;
    var bad = false;
    Array.prototype.forEach.call(form.querySelectorAll("[data-pp-ready]"), function (root) {
      if (root._pp && !root._pp.validate()) bad = true;
    });
    if (bad) { e.preventDefault(); e.stopImmediatePropagation(); }
  }, true);

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", function () { initAll(document); });
  else initAll(document);
  document.addEventListener("htmx:load", function (e) { initAll(e.target); });
  window.NBTE = window.NBTE || {};
  window.NBTE.personPicker = { init: initAll };
})();
