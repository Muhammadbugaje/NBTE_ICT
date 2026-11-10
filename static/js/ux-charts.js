/* v2.3 — charts for NBTE pages. Reads <script id="ux-chart-data"> (a list of
   panels: {id, type: bar|doughnut|money, data, colors}) and draws each into
   <canvas data-ux-chart="id">. Loads Chart.js on demand, follows the theme,
   and re-draws after HTMX swaps. */
(function () {
  "use strict";
  var LIB = (document.currentScript && document.currentScript.dataset.chartLib) || "/static/vendor/chart-4.4.1.umd.js";
  var loading = null;

  function lib() {
    if (window.Chart) return Promise.resolve(window.Chart);
    if (loading) return loading;
    loading = new Promise(function (resolve, reject) {
      var s = document.createElement("script");
      s.src = LIB; s.async = true;
      s.onload = function () { resolve(window.Chart); };
      s.onerror = reject;
      document.head.appendChild(s);
    });
    return loading;
  }

  function css(name, fallback) {
    var v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    return v || fallback;
  }

  function naira(v) {
    if (v >= 1e9) return "₦" + (v / 1e9).toFixed(1) + "bn";
    if (v >= 1e6) return "₦" + (v / 1e6).toFixed(1) + "m";
    if (v >= 1e3) return "₦" + (v / 1e3).toFixed(0) + "k";
    return "₦" + Math.round(v);
  }

  function draw(Chart, panel, canvas) {
    var text = css("--text-muted", "#5a616b"), grid = css("--border", "#e3e6ea");
    Chart.defaults.font.family = css("--font-sans", "system-ui");
    Chart.defaults.color = text;
    var old = Chart.getChart(canvas); if (old) old.destroy();
    var colors = panel.colors || ["#1f6b3e", "#c8962a", "#2f7cf6", "#13a37c", "#9a4fd0", "#e5484d"];
    var labels = (panel.data || []).map(function (d) { return d.label || d.month; });
    var cfg;
    if (panel.type === "money") {
      cfg = {
        type: "bar",
        data: {
          labels: labels,
          datasets: [
            { label: "Income (confirmed fees)", data: panel.data.map(function (d) { return d.income; }),
              backgroundColor: "rgba(31,107,62,.85)", borderRadius: 8, maxBarThickness: 26 },
            { label: "Expenditure (honoraria)", data: panel.data.map(function (d) { return d.spend; }),
              backgroundColor: "rgba(200,150,42,.9)", borderRadius: 8, maxBarThickness: 26 }
          ]
        },
        options: {
          responsive: true, maintainAspectRatio: false,
          plugins: { legend: { position: "bottom", labels: { usePointStyle: true, boxWidth: 8 } },
                     tooltip: { callbacks: { label: function (c) { return c.dataset.label + ": " + naira(c.parsed.y); } } } },
          scales: { x: { grid: { display: false } },
                    y: { grid: { color: grid }, ticks: { callback: function (v) { return naira(v); } } } }
        }
      };
    } else if (panel.type === "doughnut") {
      cfg = {
        type: "doughnut",
        data: { labels: labels, datasets: [{ data: panel.data.map(function (d) { return d.value; }),
          backgroundColor: colors, borderWidth: 2, borderColor: css("--surface", "#fff"), hoverOffset: 6 }] },
        options: { responsive: true, maintainAspectRatio: false, cutout: "62%",
          plugins: { legend: { position: "bottom", labels: { usePointStyle: true, boxWidth: 8 } } } }
      };
    } else {
      cfg = {
        type: "bar",
        data: { labels: labels, datasets: [{ data: panel.data.map(function (d) { return d.value; }),
          backgroundColor: labels.map(function (_l, i) { return colors[i % colors.length]; }), borderRadius: 8,
          maxBarThickness: 34 }] },
        options: { indexAxis: labels.length > 5 ? "y" : "x", responsive: true, maintainAspectRatio: false,
          plugins: { legend: { display: false } },
          scales: { x: { grid: { display: false }, ticks: { precision: 0 } },
                    y: { grid: { color: grid }, ticks: { precision: 0 } } } }
      };
    }
    if (!panel.data || !panel.data.length) {
      var p = document.createElement("p"); p.className = "ux-muted"; p.textContent = "No data yet.";
      canvas.replaceWith(p); return;
    }
    new Chart(canvas, cfg);
  }

  function init() {
    var node = document.getElementById("ux-chart-data");
    if (!node) return;
    var panels;
    try { panels = JSON.parse(node.textContent); } catch (e) { return; }
    lib().then(function (Chart) {
      panels.forEach(function (p) {
        var canvas = document.querySelector('canvas[data-ux-chart="' + p.id + '"]');
        if (canvas) draw(Chart, p, canvas);
      });
    }).catch(function () {});
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
  document.addEventListener("htmx:afterSettle", init);
  new MutationObserver(function (m) {
    if (m.some(function (x) { return x.attributeName === "data-theme"; })) init();
  }).observe(document.documentElement, { attributes: true });
})();
