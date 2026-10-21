/* NBTE QA Portal public pages (Phase 6): scroll-edge hairline on the nav,
   the mobile action dock that tucks away while reading, and the how-to
   table of contents that follows the reader. No dependencies. */
(function () {
  "use strict";
  var nav = document.querySelector("[data-qp-nav]");
  var dock = document.querySelector("[data-qp-dock]");
  if (dock) document.body.classList.add("qp-has-dock");
  var lastY = window.scrollY, ticking = false;

  function onScroll() {
    var y = window.scrollY;
    if (nav) nav.classList.toggle("is-scrolled", y > 4);
    if (dock) {
      // Hide while scrolling down through content; show on the way back up and near the end.
      var nearEnd = window.innerHeight + y >= document.documentElement.scrollHeight - 120;
      dock.classList.toggle("is-hidden", y > lastY + 4 && y > 240 && !nearEnd);
      if (y < lastY - 4 || nearEnd) dock.classList.remove("is-hidden");
    }
    lastY = y;
    ticking = false;
  }
  window.addEventListener("scroll", function () {
    if (!ticking) { ticking = true; window.requestAnimationFrame(onScroll); }
  }, { passive: true });
  onScroll();

  // Table of contents: mark the section in view, keep its pill visible on phones.
  var toc = document.querySelector("[data-qp-toc]");
  if (toc && "IntersectionObserver" in window) {
    var links = {};
    toc.querySelectorAll("a[href^='#']").forEach(function (a) { links[a.getAttribute("href").slice(1)] = a; });
    var current = null;
    var observer = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (!entry.isIntersecting) return;
        var link = links[entry.target.id];
        if (!link || link === current) return;
        if (current) { current.classList.remove("is-active"); current.removeAttribute("aria-current"); }
        link.classList.add("is-active");
        link.setAttribute("aria-current", "true");
        current = link;
        var strip = toc.querySelector("ol");
        if (strip && strip.scrollWidth > strip.clientWidth) {
          strip.scrollTo({ left: link.offsetLeft - 16, behavior: "smooth" });
        }
      });
    }, { rootMargin: "-20% 0px -70% 0px" });
    Object.keys(links).forEach(function (id) {
      var section = document.getElementById(id);
      if (section) observer.observe(section);
    });
  }
})();
