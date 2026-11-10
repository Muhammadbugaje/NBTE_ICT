/* NBTE — Phase 7 polish behaviour (no dependencies).
   * [data-reveal]: fades content in the first time it scrolls into view.
   * [data-countup]: counts a number up from zero when it first appears
     (data-countup-prefix / -suffix / -decimals; the final text is in the
     element already, so it reads correctly without JavaScript).
   * Settings switches with [data-autosubmit] post their form on change.
   Respects prefers-reduced-motion and re-runs after every HTMX swap. */
(function () {
  "use strict";
  var reduce = (window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches) || /^(reduce|reduced|off)$/.test(document.documentElement.getAttribute("data-motion") || "");
  var root = document.documentElement;
  if (!reduce && "IntersectionObserver" in window) root.classList.add("reveal-ready");

  function fmt(n, decimals) {
    return n.toLocaleString("en-NG", { minimumFractionDigits: decimals, maximumFractionDigits: decimals });
  }
  function countUp(el) {
    var target = parseFloat(el.getAttribute("data-countup"));
    if (isNaN(target)) return;
    var decimals = parseInt(el.getAttribute("data-countup-decimals") || "0", 10);
    var prefix = el.getAttribute("data-countup-prefix") || "";
    var suffix = el.getAttribute("data-countup-suffix") || "";
    if (reduce) { el.textContent = prefix + fmt(target, decimals) + suffix; return; }
    var start = null, dur = Math.min(1600, 600 + Math.log10(Math.abs(target) + 1) * 180);
    function step(ts) {
      if (start === null) start = ts;
      var t = Math.min(1, (ts - start) / dur);
      var eased = 1 - Math.pow(1 - t, 3);
      el.textContent = prefix + fmt(target * eased, decimals) + suffix;
      if (t < 1) window.requestAnimationFrame(step);
    }
    window.requestAnimationFrame(step);
  }

  var io = ("IntersectionObserver" in window) ? new IntersectionObserver(function (entries) {
    entries.forEach(function (entry) {
      if (!entry.isIntersecting) return;
      var el = entry.target;
      io.unobserve(el);
      if (el.hasAttribute("data-reveal")) el.classList.add("is-revealed");
      if (el.hasAttribute("data-countup")) countUp(el);
    });
  }, { rootMargin: "0px 0px -8% 0px", threshold: 0.08 }) : null;

  function scan(scope) {
    (scope || document).querySelectorAll("[data-reveal]:not([data-reveal-bound]), [data-countup]:not([data-reveal-bound])")
      .forEach(function (el, i) {
        el.setAttribute("data-reveal-bound", "");
        if (el.hasAttribute("data-reveal") && !el.style.transitionDelay) {
          var d = el.getAttribute("data-reveal-delay");
          if (d) el.style.transitionDelay = d + "ms";
        }
        if (io) io.observe(el);
        else { el.classList.add("is-revealed"); if (el.hasAttribute("data-countup")) countUp(el); }
      });
  }

  document.addEventListener("change", function (e) {
    var input = e.target;
    if (!input.matches || !input.matches("[data-autosubmit]")) return;
    var form = input.form;
    if (!form) return;
    if (window.htmx && form.hasAttribute("hx-post")) window.htmx.trigger(form, "submit");
    else if (form.requestSubmit) form.requestSubmit();
    else form.submit();
  });

  function init() { scan(document); }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
  document.addEventListener("htmx:afterSwap", function (e) { scan(e.target); });
})();
