/* ==========================================================================
   NBTE ICT — Chart helpers
   Uses Chart.js (already loaded from base.html).
   Attach with:  <div data-chart data-chart-id="activity">
                   {{ activity_data|json_script:"activity" }}
                   <canvas></canvas>
                 </div>
   The canvas + the <script type="application/json"> sit inside the wrapper.
   ========================================================================== */
(function () {
  "use strict";

  if (typeof Chart === "undefined") {
    console.warn("[charts.js] Chart.js not loaded — charts disabled.");
    return;
  }

  // ---- Brand palette ------------------------------------------------
  const PALETTE = {
    primary:  "#1F6B3E",
    primarySoft: "rgba(31, 107, 62, 0.18)",
    green:    "#10B981",
    greenSoft: "rgba(16, 185, 129, 0.18)",
    blue:     "#0369A1",
    blueSoft: "rgba(3, 105, 161, 0.18)",
    amber:    "#F59E0B",
    amberSoft: "rgba(245, 158, 11, 0.18)",
    rose:     "#BE185D",
    roseSoft: "rgba(219, 39, 119, 0.18)",
    purple:   "#7E22CE",
    purpleSoft: "rgba(126, 34, 206, 0.18)",
    teal:     "#0F766E",
    tealSoft: "rgba(13, 148, 136, 0.18)",
    indigo:   "#4F46E5",
    indigoSoft: "rgba(99, 102, 241, 0.18)",
  };

  function isDark() {
    return document.documentElement.getAttribute("data-theme") === "dark"
        || document.body.getAttribute("data-theme") === "dark";
  }

  function textColor()    { return isDark() ? "#94A3B8" : "#64748B"; }
  function gridColor()    { return isDark() ? "rgba(148,163,184,0.14)" : "rgba(15,23,42,0.06)"; }
  function tooltipBg()    { return isDark() ? "#0F172A" : "#1E293B"; }
  function tooltipFg()    { return "#F8FAFC"; }

  // ---- Defaults -----------------------------------------------------
  Chart.defaults.font.family = "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif";
  Chart.defaults.font.size = 12;
  Chart.defaults.color = textColor();
  Chart.defaults.plugins.legend.display = false;

  function baseOptions(overrides) {
    const grid = gridColor();
    const txt  = textColor();
    const opts = {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: "index", intersect: false },
      animation: { duration: 480, easing: "easeOutCubic" },
      plugins: {
        legend: { display: false },
        tooltip: {
          backgroundColor: tooltipBg(),
          titleColor: tooltipFg(),
          bodyColor: tooltipFg(),
          padding: 10,
          cornerRadius: 8,
          displayColors: true,
          boxPadding: 4,
          titleFont: { weight: "600" },
        },
      },
      scales: {
        x: {
          grid: { display: false },
          ticks: { color: txt, font: { size: 11 } },
          border: { display: false },
        },
        y: {
          grid: { color: grid, drawTicks: false },
          ticks: { color: txt, font: { size: 11 }, padding: 6 },
          border: { display: false },
          beginAtZero: true,
        },
      },
    };
    return Object.assign(opts, overrides || {});
  }

  // ---- Chart factories ---------------------------------------------
  const factories = {
    line(wrapper, data, canvas) {
      const datasets = (data.series || []).map((s, i) => {
        const color = s.color || [PALETTE.primary, PALETTE.blue, PALETTE.amber, PALETTE.rose][i % 4];
        return {
          label: s.name,
          data: s.values,
          borderColor: color,
          backgroundColor: hexA(color, 0.14),
          fill: s.fill !== false,
          tension: 0.35,
          pointRadius: 2,
          pointHoverRadius: 4,
          borderWidth: 2,
        };
      });
      return new Chart(canvas, {
        type: "line",
        data: { labels: data.labels, datasets },
        options: baseOptions(data.options),
      });
    },

    area(wrapper, data, canvas) {
      const datasets = (data.series || []).map((s, i) => {
        const color = s.color || [PALETTE.primary, PALETTE.blue][i % 2];
        return {
          label: s.name,
          data: s.values,
          borderColor: color,
          backgroundColor: hexA(color, 0.20),
          fill: "origin",
          tension: 0.4,
          pointRadius: 0,
          pointHoverRadius: 4,
          borderWidth: 2,
        };
      });
      return new Chart(canvas, {
        type: "line",
        data: { labels: data.labels, datasets },
        options: baseOptions(data.options),
      });
    },

    bar(wrapper, data, canvas) {
      const datasets = (data.series || []).map((s, i) => {
        const color = s.color || [PALETTE.primary, PALETTE.blue, PALETTE.amber][i % 3];
        return {
          label: s.name,
          data: s.values,
          backgroundColor: hexA(color, 0.8),
          hoverBackgroundColor: color,
          borderRadius: 6,
          borderSkipped: false,
          maxBarThickness: 40,
        };
      });
      return new Chart(canvas, {
        type: "bar",
        data: { labels: data.labels, datasets },
        options: baseOptions(data.options),
      });
    },

    stackedBar(wrapper, data, canvas) {
      const opts = baseOptions(data.options);
      opts.scales.x.stacked = true;
      opts.scales.y.stacked = true;
      const datasets = (data.series || []).map((s, i) => {
        const color = s.color || [PALETTE.primary, PALETTE.blue, PALETTE.amber, PALETTE.rose][i % 4];
        return {
          label: s.name,
          data: s.values,
          backgroundColor: hexA(color, 0.85),
          hoverBackgroundColor: color,
          borderRadius: 6,
          borderSkipped: false,
          maxBarThickness: 40,
        };
      });
      return new Chart(canvas, {
        type: "bar",
        data: { labels: data.labels, datasets },
        options: opts,
      });
    },

    donut(wrapper, data, canvas) {
      const colors = data.colors || [
        PALETTE.primary, PALETTE.blue, PALETTE.amber,
        PALETTE.rose, PALETTE.purple, PALETTE.teal,
      ];
      const opts = baseOptions(data.options);
      opts.cutout = "68%";
      opts.plugins.legend = {
        display: true,
        position: "bottom",
        labels: {
          color: textColor(),
          boxWidth: 10,
          boxHeight: 10,
          borderRadius: 3,
          useBorderRadius: true,
          padding: 10,
          font: { size: 11 },
        },
      };
      opts.scales = {};
      return new Chart(canvas, {
        type: "doughnut",
        data: {
          labels: data.labels,
          datasets: [{
            data: data.values,
            backgroundColor: colors,
            borderWidth: 0,
            hoverOffset: 4,
          }],
        },
        options: opts,
      });
    },
  };

  function hexA(hex, alpha) {
    const h = hex.replace("#", "");
    const full = h.length === 3 ? h.split("").map(c => c + c).join("") : h;
    const r = parseInt(full.slice(0, 2), 16);
    const g = parseInt(full.slice(2, 4), 16);
    const b = parseInt(full.slice(4, 6), 16);
    return `rgba(${r}, ${g}, ${b}, ${alpha})`;
  }

  // ---- Auto-init ----------------------------------------------------
  function initWrapper(wrapper) {
    if (wrapper.dataset.chartReady === "1") return;
    const canvas = wrapper.querySelector("canvas");
    if (!canvas) return;
    const dataEl = wrapper.querySelector('script[type="application/json"]');
    if (!dataEl) return;

    let data;
    try {
      data = JSON.parse(dataEl.textContent);
    } catch (e) {
      console.warn("[charts.js] bad JSON in wrapper", wrapper, e);
      return;
    }

    const type = wrapper.dataset.chartType || "line";
    const factory = factories[type] || factories.line;
    factory(wrapper, data, canvas);
    wrapper.dataset.chartReady = "1";
  }

  function initAll(root) {
    (root || document).querySelectorAll("[data-chart-type]").forEach(initWrapper);
  }

  // Auto-run on DOMContentLoaded
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", () => initAll());
  } else {
    initAll();
  }

  // Re-render on theme change (best-effort: destroy + re-init)
  const observer = new MutationObserver(() => {
    Chart.defaults.color = textColor();
    document.querySelectorAll('[data-chart-ready="1"]').forEach(w => {
      const canvas = w.querySelector("canvas");
      const existing = Chart.getChart(canvas);
      if (existing) existing.destroy();
      w.dataset.chartReady = "0";
    });
    initAll();
  });
  observer.observe(document.documentElement, {
    attributes: true,
    attributeFilter: ["data-theme"],
  });

  // Public API
  window.NBTECharts = {
    init: initAll,
    palette: PALETTE,
    make: factories,
  };
})();