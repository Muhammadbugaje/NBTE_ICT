/* NBTE — attendance analytics (v2.1 Phase D, G8): weekly chart and the
   person drill-down dialog. Chart.js 4 (vendored). */
(function () {
  "use strict";
  var chart;
  function css(name, fallback) {
    var v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    return v || fallback;
  }
  function draw() {
    var el = document.getElementById("an-trend");
    var data = document.getElementById("an-chart-data");
    if (!el || !data || typeof Chart === "undefined") return;
    var d = JSON.parse(data.textContent);
    if (chart) chart.destroy();
    var reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    chart = new Chart(el, {
      type: "bar",
      data: {
        labels: d.labels,
        datasets: [
          { label: "On time", data: d.present, backgroundColor: css("--success", "#2f8f57"), stack: "d" },
          { label: "Late", data: d.late, backgroundColor: css("--warning", "#d99a00"), stack: "d" },
          { label: "Absent", data: d.absent, backgroundColor: css("--danger", "#e5484d"), stack: "d" },
          { label: "Leave, duty, holiday", data: d.away, backgroundColor: css("--info", "#2f7cf6"), stack: "d" }
        ]
      },
      options: {
        animation: reduce ? false : { duration: 500 },
        maintainAspectRatio: false,
        plugins: { legend: { position: "bottom", labels: { color: css("--text-muted", "#666"), boxWidth: 12 } } },
        scales: {
          x: { stacked: true, ticks: { color: css("--text-muted", "#666") }, grid: { display: false } },
          y: { stacked: true, beginAtZero: true, ticks: { precision: 0, color: css("--text-muted", "#666") },
               grid: { color: css("--border", "rgba(0,0,0,.08)") } }
        }
      }
    });
  }
  function boot() {
    draw();
    document.body.addEventListener("htmx:afterSwap", function (e) {
      if (e.target && e.target.id === "an-body") draw();
    });
    var dlg = document.getElementById("an-person");
    document.addEventListener("click", function (e) {
      var a = e.target.closest && e.target.closest("a[data-person]");
      if (a && dlg && typeof dlg.showModal === "function" && window.htmx) {
        e.preventDefault();
        htmx.ajax("GET", a.getAttribute("href"), { target: "#an-person", swap: "innerHTML" }).then(function () {
          dlg.showModal();
        });
        return;
      }
      if (e.target.closest && e.target.closest("[data-close-dialog]") && dlg) dlg.close();
    });
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot); else boot();
})();
