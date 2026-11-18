/* QA e-mail outbox: two-pane reader (templates/qa_core/_outbox_panel.html).
   Each list row carries its message in <template> elements (HTML-escaped by Django, so
   reading .innerHTML of the template gives back inert text, never live markup). The HTML
   body is shown in a sandboxed iframe without allow-scripts. Delegated listeners, so it
   keeps working after HTMX swaps the list or the whole panel. */
(function () {
  "use strict";
  if (window.__vxMail) { window.__vxMail(); return; }

  function root() { return document.querySelector("[data-vx-mail]"); }
  function q(sel, el) { return (el || root() || document).querySelector(sel); }
  function isWide() { return window.matchMedia("(min-width: 900px)").matches; }
  function raw(tpl) {   // the escaped text Django wrote inside the <template>
    if (!tpl) return "";
    var ta = document.createElement("textarea");
    ta.innerHTML = tpl.innerHTML;
    return ta.value;
  }

  function setView(view) {
    var r = root(); if (!r) return;
    var frame = q("[data-vx-mail-frame]"), text = q("[data-vx-mail-text]");
    frame.hidden = view !== "html"; text.hidden = view === "html";
    r.querySelectorAll("[data-vx-mail-view]").forEach(function (b) {
      b.setAttribute("aria-selected", b.getAttribute("data-vx-mail-view") === view ? "true" : "false");
    });
  }

  function fitFrame(frame) {
    try {
      var doc = frame.contentDocument;
      if (doc && doc.documentElement) frame.style.height = Math.max(320, doc.documentElement.scrollHeight + 8) + "px";
    } catch (e) { frame.style.height = "70vh"; }
  }

  function open(item, focus) {
    var r = root(); if (!r || !item) return;
    r.querySelectorAll("[data-vx-mail-item].is-active").forEach(function (b) { b.classList.remove("is-active"); b.removeAttribute("aria-current"); });
    item.classList.add("is-active"); item.setAttribute("aria-current", "true");
    q("[data-vx-mail-placeholder]").hidden = true;
    q("[data-vx-mail-message]").hidden = false;
    q("[data-vx-mail-subject]").textContent = item.getAttribute("data-subject") || "(no subject)";
    q("[data-vx-mail-to]").textContent = item.getAttribute("data-to") || "—";
    q("[data-vx-mail-time]").textContent = item.getAttribute("data-time") || "";
    var chip = q("[data-vx-mail-chip]");
    chip.className = "vx-chip " + (item.className.match(/vx-t-\w+/) || [""])[0];
    chip.textContent = item.getAttribute("data-category") || "";
    q("[data-vx-mail-avatar]").textContent = (item.getAttribute("data-to") || "?").trim().charAt(0).toUpperCase();
    q("[data-vx-mail-avatar]").className = "vx-mail__avatar " + (item.className.match(/vx-t-\w+/) || [""])[0];

    var html = raw(item.querySelector("template[data-vx-mail-html]"));
    var text = raw(item.querySelector("template[data-vx-mail-text]"));
    q("[data-vx-mail-text]").textContent = text || "(This message has no plain-text version.)";
    var frame = q("[data-vx-mail-frame]"), views = q("[data-vx-mail-views]");
    if (html.trim()) {
      frame.style.height = "320px";
      frame.onload = function () { fitFrame(frame); setTimeout(function () { fitFrame(frame); }, 250); };
      frame.srcdoc = '<base target="_blank">' + html;
      views.hidden = false; setView("html");
    } else {
      frame.removeAttribute("srcdoc"); views.hidden = true; setView("text");
    }
    r.classList.add("is-reading");
    if (focus) { var h = q("[data-vx-mail-subject]"); if (h && !isWide()) h.focus({ preventScroll: false }); }
    var reader = q("[data-vx-mail-reader]"); if (reader) reader.scrollTop = 0;
  }

  function init() {
    var r = root(); if (!r) return;
    var active = r.querySelector("[data-vx-mail-item].is-active");
    if (!active) {
      // New list (first load, filter or page change): reset the reader.
      r.classList.remove("is-reading");
      var first = r.querySelector("[data-vx-mail-item]");
      if (first && isWide()) { open(first, false); r.classList.remove("is-reading"); }
      else {
        q("[data-vx-mail-placeholder]").hidden = false;
        q("[data-vx-mail-message]").hidden = true;
      }
    }
  }
  window.__vxMail = init;

  document.addEventListener("click", function (e) {
    var item = e.target.closest && e.target.closest("[data-vx-mail-item]");
    if (item) { open(item, true); return; }
    var back = e.target.closest && e.target.closest("[data-vx-mail-back]");
    if (back) {
      var r = root(); if (!r) return;
      r.classList.remove("is-reading");
      var cur = r.querySelector("[data-vx-mail-item].is-active"); if (cur) cur.focus();
      return;
    }
    var view = e.target.closest && e.target.closest("[data-vx-mail-view]");
    if (view) setView(view.getAttribute("data-vx-mail-view"));
  });
  document.addEventListener("htmx:afterSwap", function (e) {
    if (e.target && (e.target.id === "qa-rows" || e.target.id === "qa-panel")) init();
  });
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init); else init();
})();
