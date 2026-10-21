/* NBTE ICT — Phase 6 design layer behaviour (no dependencies).
   * Top bar: hairline only once content scrolls under it.
   * Mobile tab bar: tucks away while scrolling down, returns on the way up;
     hidden while the menu drawer is open.
   * Segmented tabs and the case stepper: keep the active item in view, and
     drop the edge fade once scrolled to the end.
   Re-runs after every HTMX swap so swapped panels behave the same. */
(function () {
  "use strict";
  var topbar = document.querySelector(".app-topbar");
  var tabbar = document.querySelector("[data-tabbar]");
  var shell = document.querySelector(".app-shell");
  var lastY = window.scrollY;
  var ticking = false;

  function onScroll() {
    var y = window.scrollY;
    if (topbar) topbar.classList.toggle("is-scrolled", y > 2);
    if (tabbar) {
      var nearEnd = window.innerHeight + y >= document.documentElement.scrollHeight - 80;
      if (y > lastY + 6 && y > 160 && !nearEnd) tabbar.classList.add("is-hidden");
      else if (y < lastY - 6 || nearEnd || y < 160) tabbar.classList.remove("is-hidden");
    }
    lastY = y;
    ticking = false;
  }
  window.addEventListener("scroll", function () {
    if (!ticking) { ticking = true; window.requestAnimationFrame(onScroll); }
  }, { passive: true });

  if (tabbar && shell && "MutationObserver" in window) {
    new MutationObserver(function () {
      tabbar.classList.toggle("is-hidden-by-drawer", shell.classList.contains("sidebar-open"));
    }).observe(shell, { attributes: true, attributeFilter: ["class"] });
  }

  function centre(strip, item) {
    if (!strip || !item || strip.scrollWidth <= strip.clientWidth) return;
    var left = item.offsetLeft - (strip.clientWidth - item.offsetWidth) / 2;
    strip.scrollLeft = Math.max(0, left);
  }
  function fadeState(strip) {
    strip.classList.toggle("is-scrolled-end", strip.scrollLeft + strip.clientWidth >= strip.scrollWidth - 4);
  }
  function enhance(root) {
    (root || document).querySelectorAll(".qa-tabs").forEach(function (strip) {
      centre(strip, strip.querySelector(".is-active"));
      fadeState(strip);
      if (!strip.hasAttribute("data-fade-bound")) {
        strip.setAttribute("data-fade-bound", "");
        strip.addEventListener("scroll", function () { fadeState(strip); }, { passive: true });
      }
    });
    (root || document).querySelectorAll(".qa-stepper").forEach(function (strip) {
      centre(strip, strip.querySelector(".is-current"));
    });
  }

  function init() { onScroll(); enhance(document); }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
  document.addEventListener("htmx:afterSettle", function () { enhance(document); });
})();
