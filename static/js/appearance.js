/* Settings → Appearance (v2.1 Phase A, A9): live preview and the custom pair.
   The preview frame is same-origin, so a pick recolours it directly by
   setting the same attributes theme-init.js sets (data-palette, data-theme,
   data-motion, data-density, --custom-*); nothing is saved until "Save".
   The frame is scaled to the panel width. Re-binds after the HTMX swap. */
(function () {
  "use strict";
  var HEX = /^#[0-9a-f]{6}$/i;

  function lum(hex) {
    var n = parseInt(hex.slice(1), 16), c = [(n >> 16) & 255, (n >> 8) & 255, n & 255].map(function (v) {
      v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4);
    });
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2];
  }
  function contrast(hex) { var l = lum(hex); return Math.round((1.05 / (l + 0.05)) * 100) / 100; }

  function frameDoc() {
    var f = document.querySelector("[data-apx-iframe]");
    try { return f && f.contentDocument && f.contentDocument.documentElement ? f.contentDocument : null; } catch (e) { return null; }
  }
  function val(form, name) {
    var els = form.querySelectorAll('[name="' + name + '"]'), v = "";
    for (var i = 0; i < els.length; i++) {
      var e = els[i];
      if (e.type === "radio" || e.type === "checkbox") { if (e.checked) v = e.value; }
      else if (e.type !== "hidden" || !v) v = e.value;
    }
    return v;
  }
  function osDark() { return window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches; }

  function apply(form) {
    var doc = frameDoc(); if (!doc) return;
    var r = doc.documentElement;
    var palette = val(form, "palette") || "green";
    var theme = val(form, "theme") || "system";
    var density = val(form, "density") || "comfortable";
    var scene = val(form, "scene") || "laminar";
    var cp = val(form, "custom_primary"), ca = val(form, "custom_accent");
    r.setAttribute("data-palette", palette);
    r.setAttribute("data-theme", theme === "system" ? (osDark() ? "dark" : "light") : theme);
    r.setAttribute("data-density", density);
    r.setAttribute("data-motion", "off");   /* the preview stays still */
    if (HEX.test(cp)) r.style.setProperty("--custom-primary", cp);
    if (HEX.test(ca)) r.style.setProperty("--custom-accent", ca);
    var s = doc.querySelector(".home-hero-scene");
    if (s) {
      s.className = "scene scene--" + scene + " home-hero-scene";
      s.hidden = scene === "none";
    } else if (scene !== "none") {
      var hero = doc.querySelector(".home-hero");
      if (hero) {
        s = doc.createElement("div"); s.className = "scene scene--" + scene + " home-hero-scene"; s.setAttribute("aria-hidden", "true");
        s.innerHTML = "<i></i><i></i><i></i><i></i>"; hero.insertBefore(s, hero.firstChild);
      }
    }
    var link = document.querySelector("[data-apx-open-preview]");
    if (link) {
      var u = link.getAttribute("href").split("?")[0] + "?palette=" + encodeURIComponent(palette) +
        "&theme=" + encodeURIComponent(theme === "system" ? "" : theme) + "&density=" + density;
      if (palette === "custom" && HEX.test(cp)) u += "&cp=" + cp.slice(1) + (HEX.test(ca) ? "&ca=" + ca.slice(1) : "");
      link.setAttribute("href", u);
    }
  }

  function syncCustom(form) {
    var cp = val(form, "custom_primary"), ca = val(form, "custom_accent");
    var chip = form.querySelector("[data-apx-custom-chip]");
    if (chip) {
      if (HEX.test(cp)) chip.style.setProperty("--sw-a", cp);
      if (HEX.test(ca)) chip.style.setProperty("--sw-b", ca);
    }
    var out = form.querySelector("[data-apx-contrast]");
    if (out) {
      var min = parseFloat(out.getAttribute("data-min")) || 4.5;
      if (HEX.test(cp)) {
        var c = contrast(cp);
        out.innerHTML = "White text on the primary: <strong>" + c + ":1</strong> " + (c >= min ? "(passes)" : "(needs " + min + ":1 — pick a darker colour)");
        out.classList.toggle("is-bad", c < min);
      } else {
        out.textContent = "Use a colour in the form #rrggbb.";
        out.classList.add("is-bad");
      }
    }
  }

  function bind() {
    var form = document.querySelector("[data-apx-form]");
    if (!form || form._apx) return;
    form._apx = true;
    form.addEventListener("change", function (e) {
      var t = e.target;
      if (t.matches("[data-apx-colour-for]")) {
        var txt = form.querySelector('[name="' + t.getAttribute("data-apx-colour-for") + '"]');
        if (txt) txt.value = t.value;
        var custom = form.querySelector('input[name="palette"][value="custom"]'); if (custom) custom.checked = true;
      }
      syncCustom(form); apply(form);
    });
    form.addEventListener("input", function (e) {
      var t = e.target;
      if (t.matches("[data-apx-colour-for]")) {
        var txt = form.querySelector('[name="' + t.getAttribute("data-apx-colour-for") + '"]'); if (txt) txt.value = t.value;
      }
      if (t.matches("[data-apx-hex]")) {
        var v = t.value.trim(); if (v && v.charAt(0) !== "#") v = "#" + v;
        var picker = form.querySelector('[data-apx-colour-for="' + t.name + '"]');
        if (picker && HEX.test(v)) picker.value = v.toLowerCase();
      }
      if (t.matches("[data-apx-colour-for], [data-apx-hex]")) { syncCustom(form); apply(form); }
    });
    form.addEventListener("reset", function () { setTimeout(function () { syncCustom(form); apply(form); }, 0); });
  }

  function scale() {
    var box = document.querySelector("[data-apx-frame]");
    if (box) box.style.setProperty("--apx-scale", (box.clientWidth / 1280).toFixed(4));
  }

  function init() {
    bind(); scale();
    var f = document.querySelector("[data-apx-iframe]");
    if (f) f.addEventListener("load", function () { var form = document.querySelector("[data-apx-form]"); if (form) apply(form); });
    if ("ResizeObserver" in window) {
      var box = document.querySelector("[data-apx-frame]");
      if (box) new ResizeObserver(scale).observe(box);
    } else window.addEventListener("resize", scale);
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init); else init();
  document.addEventListener("htmx:afterSwap", function () { bind(); var form = document.querySelector("[data-apx-form]"); if (form) { syncCustom(form); apply(form); } });
})();
