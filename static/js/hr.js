/* HR pages (Phase B): show the server's HX-Trigger "qa:toast" on the staff
   side (qa.js does this on QA pages only) and let 4xx HTMX replies with a
   fragment still swap, so form errors appear in place. */
(function () {
  "use strict";
  if (window.__hrToastBound) return;
  window.__hrToastBound = true;
  document.addEventListener("qa:toast", function (evt) {
    if (window.__qaToastBound) return;
    var t = evt.detail || {};
    var msg = (t.title || "Done") + (t.body ? " — " + t.body : "");
    if (window.NBTE && NBTE.toast) NBTE.toast(msg, t.kind || "info");
  });
  document.addEventListener("htmx:beforeSwap", function (evt) {
    var x = evt.detail && evt.detail.xhr;
    if (x && x.status === 422) { evt.detail.shouldSwap = true; evt.detail.isError = false; }
  });
})();

/* Staff profile (B.8): after the Access tab or a role save swaps in, mark the
   Access tab current and close the Manage role sheet on success. */
(function () {
  "use strict";
  document.addEventListener("htmx:afterSwap", function () {
    if (!document.getElementById("staff-access")) return;
    document.querySelectorAll(".hr-profile-tabs a").forEach(function (a) {
      var on = /tab=access/.test(a.getAttribute("href") || "");
      a.classList.toggle("is-active", on);
      if (on) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
    });
  });
  document.addEventListener("htmx:afterRequest", function (e) {
    var form = e.detail && e.detail.elt;
    if (!form || !form.matches || !form.matches("[data-role-form]") || !e.detail.successful) return;
    var root = form.closest("[data-role-modal]");
    if (root && window.NBTE && NBTE.modal) NBTE.modal(root).close();
  });
})();
