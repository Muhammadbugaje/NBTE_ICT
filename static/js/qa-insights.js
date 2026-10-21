/* NBTE — QA overview charts (Chart.js 4, vendored). Draws each chart the
   first time it scrolls into view so the animation is seen; follows the
   light / dark theme; no dependencies beyond Chart.js. */
(function () {
  "use strict";
  if (typeof Chart === "undefined") return;
  var src = document.getElementById("qi-data");
  if (!src) return;
  var data;
  try { data = JSON.parse(src.textContent); } catch (e) { return; }

  function css(name, fallback) {
    var v = getComputedStyle(document.documentElement).getPropertyValue(name);
    return (v && v.trim()) || fallback;
  }
  var green = css("--primary", "#1f6b3e");
  var text = css("--text-muted", "#6e6e73");
  var grid = css("--divider", "rgba(0,0,0,.06)");
  var reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  Chart.defaults.font.family = "-apple-system, BlinkMacSystemFont, 'SF Pro Text', 'Segoe UI', Roboto, sans-serif";
  Chart.defaults.color = text;

  function naira(v) {
    if (v >= 1e9) return "₦" + (v / 1e9).toFixed(1) + "bn";
    if (v >= 1e6) return "₦" + (v / 1e6).toFixed(1) + "m";
    if (v >= 1e3) return "₦" + Math.round(v / 1e3) + "k";
    return "₦" + v;
  }
  function base(extra) {
    var o = {
      responsive: true, maintainAspectRatio: false,
      animation: reduce ? false : { duration: 1100, easing: "easeOutQuart" },
      plugins: { legend: { display: false }, tooltip: { backgroundColor: "#1d1d1f", padding: 10, cornerRadius: 10, displayColors: false } },
      scales: {
        x: { grid: { display: false }, border: { display: false }, ticks: { color: text } },
        y: { grid: { color: grid }, border: { display: false }, ticks: { color: text, padding: 6 }, beginAtZero: true }
      }
    };
    return Object.assign(o, extra || {});
  }
  function gradient(ctx, area, color) {
    var g = ctx.createLinearGradient(0, area.bottom, 0, area.top);
    g.addColorStop(0, color + "33"); g.addColorStop(1, color);
    return g;
  }

  var builders = {
    revenue: function (canvas) {
      var d = data.revenue || { labels: [], values: [] };
      return new Chart(canvas, {
        type: "bar",
        data: { labels: d.labels, datasets: [{ data: d.values, borderRadius: 8, borderSkipped: false, maxBarThickness: 34,
          backgroundColor: function (c) { var a = c.chart.chartArea; return a ? gradient(c.chart.ctx, a, green) : green; } }] },
        options: base({ plugins: { legend: { display: false }, tooltip: { backgroundColor: "#1d1d1f", displayColors: false,
          callbacks: { label: function (c) { return "₦" + Number(c.raw).toLocaleString("en-NG"); } } } },
          scales: { x: { grid: { display: false }, border: { display: false } },
                    y: { grid: { color: grid }, border: { display: false }, beginAtZero: true, ticks: { callback: naira } } } })
      });
    },
    institutions: function (canvas) {
      var rows = data.institutions || [];
      var palette = { approved: green, under_review: "#0a84ff", proposed: "#ff9f0a", suspended: "#ff453a", delisted: "#8e8e93" };
      return new Chart(canvas, {
        type: "doughnut",
        data: { labels: rows.map(function (r) { return r.label; }),
                datasets: [{ data: rows.map(function (r) { return r.count; }), borderWidth: 0, spacing: 2,
                             backgroundColor: rows.map(function (r) { return palette[r.code] || "#8e8e93"; }) }] },
        options: { responsive: true, maintainAspectRatio: false, cutout: "72%",
                   animation: reduce ? false : { animateRotate: true, duration: 1200, easing: "easeOutQuart" },
                   plugins: { legend: { display: true, position: "bottom", labels: { boxWidth: 10, boxHeight: 10, usePointStyle: true, color: text } } } }
      });
    },
    sla: function (canvas) {
      var d = data.sla || { labels: [], values: [] };
      return new Chart(canvas, {
        type: "line",
        data: { labels: d.labels, datasets: [{ data: d.values, spanGaps: true, borderColor: "#30d158", borderWidth: 2.5, tension: 0.4,
          pointRadius: 3, pointBackgroundColor: "#30d158", fill: true,
          backgroundColor: function (c) { var a = c.chart.chartArea; if (!a) return "transparent";
            var g = c.chart.ctx.createLinearGradient(0, a.bottom, 0, a.top); g.addColorStop(0, "rgba(48,209,88,0)"); g.addColorStop(1, "rgba(48,209,88,.25)"); return g; } }] },
        options: base({ scales: { x: { grid: { display: false }, border: { display: false } },
                                  y: { min: 0, max: 100, grid: { color: grid }, border: { display: false }, ticks: { callback: function (v) { return v + "%"; } } } } })
      });
    },
    cases: function (canvas) {
      var rows = data.cases || [];
      return new Chart(canvas, {
        type: "bar",
        data: { labels: rows.map(function (r) { return r.label; }),
                datasets: [{ data: rows.map(function (r) { return r.count; }), backgroundColor: green, borderRadius: 6, maxBarThickness: 22 }] },
        options: base({ indexAxis: "y", scales: { x: { grid: { color: grid }, border: { display: false }, beginAtZero: true, ticks: { precision: 0 } },
                                                  y: { grid: { display: false }, border: { display: false } } } })
      });
    }
  };

  function draw(canvas) {
    var kind = canvas.getAttribute("data-qi");
    if (builders[kind] && !canvas.__qi) { canvas.__qi = builders[kind](canvas); }
  }
  var canvases = document.querySelectorAll("canvas[data-qi]");
  if ("IntersectionObserver" in window && !reduce) {
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (e) { if (e.isIntersecting) { io.unobserve(e.target); draw(e.target); } });
    }, { threshold: 0.2 });
    canvases.forEach(function (c) { io.observe(c); });
    // Never leave a chart blank (printing, full-page captures, tall screens).
    window.setTimeout(function () { canvases.forEach(draw); }, 3000);
    window.addEventListener("beforeprint", function () { canvases.forEach(draw); });
  } else {
    canvases.forEach(draw);
  }
})();
