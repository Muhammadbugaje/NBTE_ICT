/* =========================================================================
   Quick file preview (Phase 8 UX)

   Any element with [data-preview="<download url>"] opens a large viewer
   (a slide-over sheet on phones) that shows the file without downloading it:
     - PDF   → <iframe> of "<url>?inline=1"
     - image → <img> with fit / 100% zoom and +/- buttons
     - text  → fetched and shown in a <pre>
     - other → friendly "download it instead" panel
   Header: file name, type · size, Open in new tab, Download, Close.
   Esc closes, focus is trapped, focus returns to the button. Dark mode via
   the semantic tokens in tokens.css. Server views honour ?inline=1 with the
   same permission checks as a download (core/file_preview.py).

   Optional attributes: data-preview-name, data-preview-size,
   data-preview-type ("pdf" | "image" | "text" | "other"),
   data-preview-download (download URL if different).
   ========================================================================= */
(function () {
  "use strict";
  if (window.NBTEPreview) return;

  var EXT = {
    pdf: "pdf",
    png: "image", jpg: "image", jpeg: "image", gif: "image", webp: "image", bmp: "image",
    txt: "text", csv: "text", log: "text"
  };
  var TYPE_LABEL = { pdf: "PDF document", image: "Image", text: "Text file" };
  var OTHER_LABEL = {
    doc: "Word document", docx: "Word document", xls: "Excel workbook", xlsx: "Excel workbook",
    ppt: "PowerPoint deck", pptx: "PowerPoint deck", zip: "ZIP archive", odt: "Document",
    ods: "Spreadsheet", rtf: "Rich text document"
  };
  var TEXT_LIMIT = 1024 * 1024; // show at most 1 MB of text

  function extOf(name) {
    var m = /\.([a-z0-9]{1,6})$/i.exec(String(name || "").split("?")[0]);
    return m ? m[1].toLowerCase() : "";
  }
  function kindOf(name, override) {
    if (override) return override;
    return EXT[extOf(name)] || "other";
  }
  function withInline(url) {
    try {
      var u = new URL(url, window.location.href);
      u.searchParams.set("inline", "1");
      return u.pathname + u.search + u.hash;
    } catch (e) {
      return url + (url.indexOf("?") === -1 ? "?" : "&") + "inline=1";
    }
  }
  function svg(path, size) {
    size = size || 18;
    return '<svg width="' + size + '" height="' + size + '" viewBox="0 0 24 24" fill="none" stroke="currentColor" ' +
      'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' + path + "</svg>";
  }
  var ICON = {
    close: '<line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>',
    external: '<path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/>',
    download: '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/>',
    file: '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/>',
    zoomIn: '<circle cx="11" cy="11" r="7"/><line x1="21" y1="21" x2="16.65" y2="16.65"/><line x1="11" y1="8" x2="11" y2="14"/><line x1="8" y1="11" x2="14" y2="11"/>',
    zoomOut: '<circle cx="11" cy="11" r="7"/><line x1="21" y1="21" x2="16.65" y2="16.65"/><line x1="8" y1="11" x2="14" y2="11"/>'
  };

  function el(tag, cls, html) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (html != null) n.innerHTML = html;
    return n;
  }

  var current = null;

  function close() {
    if (!current) return;
    var c = current;
    current = null;
    document.removeEventListener("keydown", c.onKey, true);
    document.removeEventListener("focusin", c.onFocusIn, true);
    c.root.classList.remove("is-open");
    document.documentElement.classList.remove("fpv-locked");
    var done = function () { c.root.remove(); };
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) done();
    else window.setTimeout(done, 220);
    if (c.opener && c.opener.isConnected && typeof c.opener.focus === "function") {
      c.opener.focus({ preventScroll: true });
    }
  }

  function open(opts) {
    if (current) close();
    var url = opts.url;
    if (!url) return;
    var name = opts.name || decodeURIComponent((url.split("?")[0].split("/").filter(Boolean).pop()) || "File");
    var kind = kindOf(name, opts.type);
    var ext = extOf(name);
    var download = opts.download || url;
    var inlineUrl = withInline(url);
    var typeLabel = TYPE_LABEL[kind] || OTHER_LABEL[ext] || (ext ? ext.toUpperCase() + " file" : "File");
    var meta = [typeLabel, opts.size].filter(Boolean).join(" · ");

    var root = el("div", "fpv fpv--" + kind);
    root.setAttribute("role", "dialog");
    root.setAttribute("aria-modal", "true");
    root.setAttribute("aria-labelledby", "fpv-title");
    var backdrop = el("div", "fpv__backdrop");
    var panel = el("div", "fpv__panel");
    panel.tabIndex = -1;

    var head = el("header", "fpv__head");
    var titleWrap = el("div", "fpv__title-wrap");
    titleWrap.appendChild(el("span", "fpv__file-icon", svg(ICON.file, 20)));
    var titles = el("div", "fpv__titles");
    var title = el("h2", "fpv__title");
    title.id = "fpv-title";
    title.textContent = name;
    title.title = name;
    var sub = el("p", "fpv__meta");
    sub.textContent = meta;
    titles.append(title, sub);
    titleWrap.appendChild(titles);

    var actions = el("div", "fpv__actions");
    var zoomOut, zoomIn;
    if (kind === "image") {
      zoomOut = el("button", "fpv__icon-btn", svg(ICON.zoomOut));
      zoomOut.type = "button"; zoomOut.setAttribute("aria-label", "Zoom out"); zoomOut.title = "Zoom out";
      zoomIn = el("button", "fpv__icon-btn", svg(ICON.zoomIn));
      zoomIn.type = "button"; zoomIn.setAttribute("aria-label", "Zoom in"); zoomIn.title = "Zoom in";
      actions.append(zoomOut, zoomIn);
    }
    if (kind !== "other") {
      var newTab = el("a", "fpv__action", svg(ICON.external, 16) + '<span class="fpv__action-label">Open in new tab</span>');
      newTab.href = inlineUrl; newTab.target = "_blank"; newTab.rel = "noopener";
      newTab.setAttribute("aria-label", "Open in new tab");
      actions.appendChild(newTab);
    }
    var dl = el("a", "fpv__action fpv__action--primary", svg(ICON.download, 16) + '<span class="fpv__action-label">Download</span>');
    dl.href = download; dl.setAttribute("download", ""); dl.setAttribute("aria-label", "Download " + name);
    actions.appendChild(dl);
    var closeBtn = el("button", "fpv__icon-btn fpv__close", svg(ICON.close));
    closeBtn.type = "button"; closeBtn.setAttribute("aria-label", "Close preview"); closeBtn.title = "Close (Esc)";
    actions.appendChild(closeBtn);
    head.append(titleWrap, actions);

    var body = el("div", "fpv__body");
    body.setAttribute("aria-busy", "true");
    var loading = el("div", "fpv__loading", '<span class="fpv__spinner" aria-hidden="true"></span><span>Loading preview…</span>');
    body.appendChild(loading);

    function done() { body.removeAttribute("aria-busy"); loading.remove(); }
    function fallback(message) {
      done();
      body.innerHTML = "";
      var box = el("div", "fpv__fallback");
      box.appendChild(el("span", "fpv__fallback-icon", svg(ICON.file, 40)));
      var h = el("h3", "fpv__fallback-title");
      h.textContent = message || "Preview isn't available for this file type";
      var p = el("p", "fpv__fallback-text");
      p.textContent = message ? "You can still download it and open it on your device."
        : "Download it instead and open it with " + (OTHER_LABEL[ext] ? "Word, Excel or a similar app" : "an app on your device") + ".";
      var b = el("a", "btn btn-primary fpv__fallback-btn", svg(ICON.download, 16) + "<span>Download</span>");
      b.href = download; b.setAttribute("download", "");
      box.append(h, p, b);
      body.appendChild(box);
    }

    if (kind === "pdf") {
      var frame = el("iframe", "fpv__frame");
      frame.title = "Preview of " + name;
      frame.src = inlineUrl;
      frame.addEventListener("load", done);
      body.appendChild(frame);
      window.setTimeout(done, 4000);
    } else if (kind === "image") {
      var stage = el("div", "fpv__stage");
      var img = el("img", "fpv__img");
      img.alt = name;
      img.decoding = "async";
      var scale = 1; var fit = true;
      function apply() {
        stage.classList.toggle("is-zoomed", !fit);
        img.style.width = fit ? "" : (img.naturalWidth * scale) + "px";
        img.style.maxWidth = fit ? "" : "none";
        img.style.maxHeight = fit ? "" : "none";
      }
      img.addEventListener("load", function () { done(); apply(); });
      img.addEventListener("error", function () { fallback("We couldn't load this image"); });
      img.addEventListener("click", function () { fit = !fit; scale = 1; apply(); });
      if (zoomIn) zoomIn.addEventListener("click", function () { fit = false; scale = Math.min(scale * 1.25, 8); apply(); });
      if (zoomOut) zoomOut.addEventListener("click", function () {
        if (fit) return; scale = scale / 1.25; if (scale <= 0.4) { fit = true; scale = 1; } apply();
      });
      img.src = inlineUrl;
      stage.appendChild(img);
      body.appendChild(stage);
    } else if (kind === "text") {
      fetch(inlineUrl, { credentials: "same-origin", headers: { "X-Requested-With": "fetch" } })
        .then(function (r) { if (!r.ok) throw new Error(r.status); return r.text(); })
        .then(function (t) {
          done();
          var pre = el("pre", "fpv__text");
          pre.textContent = t.length > TEXT_LIMIT ? t.slice(0, TEXT_LIMIT) + "\n\n… (preview cut short — download the file to see the rest)" : t;
          pre.tabIndex = 0;
          body.appendChild(pre);
        })
        .catch(function () { fallback("We couldn't load this file"); });
    } else {
      fallback();
    }

    panel.append(head, body);
    root.append(backdrop, panel);
    document.body.appendChild(root);
    document.documentElement.classList.add("fpv-locked");
    // next frame → transition in
    window.requestAnimationFrame(function () { root.classList.add("is-open"); });

    function focusables() {
      return Array.prototype.filter.call(
        panel.querySelectorAll('a[href], button:not([disabled]), iframe, [tabindex]:not([tabindex="-1"])'),
        function (n) { return n.offsetParent !== null || n === document.activeElement; });
    }
    var state = {
      root: root,
      opener: opts.opener || document.activeElement,
      onKey: function (e) {
        if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); close(); return; }
        if (e.key !== "Tab") return;
        var f = focusables(); if (!f.length) return;
        var first = f[0], last = f[f.length - 1];
        if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
        else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
      },
      onFocusIn: function (e) { if (!root.contains(e.target)) closeBtn.focus(); }
    };
    current = state;
    backdrop.addEventListener("click", close);
    closeBtn.addEventListener("click", close);
    document.addEventListener("keydown", state.onKey, true);
    document.addEventListener("focusin", state.onFocusIn, true);
    closeBtn.focus({ preventScroll: true });
  }

  document.addEventListener("click", function (e) {
    var t = e.target.closest && e.target.closest("[data-preview]");
    if (!t) return;
    e.preventDefault();
    open({
      url: t.getAttribute("data-preview"),
      name: t.getAttribute("data-preview-name"),
      size: t.getAttribute("data-preview-size"),
      type: t.getAttribute("data-preview-type"),
      download: t.getAttribute("data-preview-download"),
      opener: t
    });
  });

  window.NBTEPreview = { open: open, close: close, kindOf: kindOf };
  window.NBTE = window.NBTE || {};
  window.NBTE.Preview = window.NBTEPreview;
})();
