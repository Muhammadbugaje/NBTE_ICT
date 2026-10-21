/* Staff file timeline on the staff detail page: filter chips. */
(function () {
  "use strict";
  function boot() {
    var bar = document.querySelector("[data-sf-filters]");
    var list = document.querySelector("[data-sf-list]");
    if (!bar || !list) return;
    bar.addEventListener("click", function (e) {
      var btn = e.target.closest("[data-sf-filter]");
      if (!btn) return;
      var kind = btn.getAttribute("data-sf-filter");
      Array.prototype.forEach.call(bar.querySelectorAll("[data-sf-filter]"), function (b) {
        var on = b === btn;
        b.classList.toggle("is-active", on);
        b.setAttribute("aria-pressed", on ? "true" : "false");
      });
      Array.prototype.forEach.call(list.querySelectorAll("[data-sf-kind]"), function (li) {
        li.hidden = kind !== "all" && li.getAttribute("data-sf-kind") !== kind;
      });
    });
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();
})();
