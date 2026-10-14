/* ==========================================================================
   Gallery Lightbox — keyboard nav, prev/next, escape to close.
   ========================================================================== */
(function () {
  "use strict";

  var dataEl = document.querySelector("[data-lightbox-data]");
  if (!dataEl) return;
  var items;
  try { items = JSON.parse(dataEl.textContent); } catch (e) { return; }
  if (!items || !items.length) return;

  var box      = document.querySelector("[data-lightbox]");
  var mediaBox = box.querySelector("[data-lightbox-media]");
  var capBox   = box.querySelector("[data-lightbox-caption]");
  var prev     = box.querySelector("[data-lightbox-prev]");
  var next     = box.querySelector("[data-lightbox-next]");
  var current  = -1;

  function render(i) {
    if (i < 0) i = items.length - 1;
    if (i >= items.length) i = 0;
    current = i;
    var item = items[i];
    mediaBox.innerHTML = "";
    if (item.kind === "video") {
      var v = document.createElement("video");
      v.src = item.src;
      v.controls = true;
      v.autoplay = true;
      v.playsInline = true;
      mediaBox.appendChild(v);
    } else {
      var img = document.createElement("img");
      img.src = item.src;
      img.alt = item.caption || "";
      mediaBox.appendChild(img);
    }
    capBox.innerHTML = "";
    if (item.caption) {
      var t = document.createElement("div");
      t.className = "gal-lb-title";
      t.textContent = item.caption;
      capBox.appendChild(t);
    }
    if (item.credit) {
      var c = document.createElement("div");
      c.className = "gal-lb-credit";
      c.textContent = "© " + item.credit;
      capBox.appendChild(c);
    }
    capBox.hidden = !item.caption && !item.credit;
  }

  function open(i) {
    box.hidden = false;
    document.body.style.overflow = "hidden";
    render(i);
  }
  function close() {
    box.hidden = true;
    document.body.style.overflow = "";
    mediaBox.innerHTML = "";
  }
  function step(dir) { render(current + dir); }

  document.querySelectorAll("[data-lightbox-index]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var i = parseInt(btn.getAttribute("data-lightbox-index"), 10) || 0;
      open(i);
    });
  });

  box.querySelectorAll("[data-lightbox-close]").forEach(function (el) {
    el.addEventListener("click", close);
  });
  prev.addEventListener("click", function (e) { e.stopPropagation(); step(-1); });
  next.addEventListener("click", function (e) { e.stopPropagation(); step(1); });

  document.addEventListener("keydown", function (e) {
    if (box.hidden) return;
    if (e.key === "Escape") close();
    else if (e.key === "ArrowLeft") step(-1);
    else if (e.key === "ArrowRight") step(1);
  });
})();