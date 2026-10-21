/* NBTE "Vivid" public pages (static/css/vivid.css).
   - .vv-js on <html>: reveals become opt-in (content is visible without JS).
   - .vv-motion on <html>: the map arcs, pulses and tracker animate, unless the
     visitor prefers reduced motion.
   - Elements with data-vv-reveal fade up once, staggered within their group.
   - Hero animation pauses while the hero is off screen (battery). */
(function () {
  "use strict";
  var root = document.documentElement;
  var reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  root.classList.add("vv-js");
  if (!reduce) root.classList.add("vv-motion");

  function ready(fn) {
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", fn);
    else fn();
  }

  ready(function () {
    var items = Array.prototype.slice.call(document.querySelectorAll("[data-vv-reveal]"));
    items.forEach(function (el) {
      var group = el.parentElement ? Array.prototype.filter.call(el.parentElement.children, function (c) {
        return c.hasAttribute("data-vv-reveal");
      }) : [el];
      el.style.setProperty("--i", Math.min(group.indexOf(el), 6));
    });
    if (reduce || !("IntersectionObserver" in window)) {
      items.forEach(function (el) { el.classList.add("is-in"); });
    } else {
      var io = new IntersectionObserver(function (entries) {
        entries.forEach(function (e) {
          if (e.isIntersecting) { e.target.classList.add("is-in"); io.unobserve(e.target); }
        });
      }, { rootMargin: "0px 0px -8% 0px", threshold: 0.08 });
      items.forEach(function (el) { io.observe(el); });
    }

    if (!reduce && "IntersectionObserver" in window) {
      var heroes = document.querySelectorAll(".vv-hero");
      var hio = new IntersectionObserver(function (entries) {
        entries.forEach(function (e) { e.target.classList.toggle("vv-paused", !e.isIntersecting); });
      });
      Array.prototype.forEach.call(heroes, function (h) { hio.observe(h); });
    }
  });
})();
