/* NBTE — motion-plus behaviour (companion to static/css/motion-plus.css).
   No dependencies, no inline handlers, no eval (CSP-safe); loaded with defer.

   1. HTMX swap-in: content that a person asked for (click / submit / change)
      fades up 6px over 220ms when it lands. Uses the Web Animations API, so
      no class or inline style is left on the element: if the animation never
      runs, is cancelled, or the page comes back from the Back button, the
      content is simply there. Polling and load-triggered swaps do not animate.
   2. details/summary: marks a <details> the person has toggled, so its body
      eases in (CSS) without animating ones that are open at page load.
   Honours prefers-reduced-motion and html[data-motion=off|reduce|reduced]. */
(function (w, d) {
  "use strict";
  var mq = w.matchMedia ? w.matchMedia("(prefers-reduced-motion: reduce)") : null;
  function still() {
    return (mq && mq.matches) || /^(off|reduce|reduced)$/.test(d.documentElement.getAttribute("data-motion") || "");
  }

  var USER_EVENTS = { click: 1, submit: 1, change: 1 };
  var IN = [{ opacity: 0.35, translate: "0 6px" }, { opacity: 1, translate: "0 0" }];
  var IN_OPTS = { duration: 220, easing: "cubic-bezier(.25, 1, .5, 1)" };

  d.addEventListener("htmx:afterSwap", function (e) {
    var el = e.target, cfg = e.detail && e.detail.requestConfig, ev = cfg && cfg.triggeringEvent;
    if (!el || el.nodeType !== 1 || !el.animate || still()) return;
    if (!ev || !USER_EVENTS[ev.type]) return;                         // polling, load, revealed, typing
    if (el === d.body || el === d.documentElement) return;            // whole-page swaps keep the page transition
    if (el.hasAttribute("data-mp-still") || el.closest("[data-mp-still]")) return;
    if (/^(TR|TD|TH|TBODY|THEAD|OPTION|SELECT|INPUT)$/.test(el.tagName)) { IN[0].translate = "0 0"; }
    else { IN[0].translate = "0 6px"; }
    try { el.animate(IN, IN_OPTS); } catch (err) { /* older engine: content is already visible */ }
  });

  d.addEventListener("click", function (e) {
    var s = e.target && e.target.closest ? e.target.closest("summary") : null;
    if (s && s.parentNode && s.parentNode.tagName === "DETAILS") s.parentNode.classList.add("mp-toggled");
  });
})(window, document);
