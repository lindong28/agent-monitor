// Charts view responsibilities. The shell supplies navigation and page lifetime.
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory;
  else (root.AgentMonitorModules ||= {}).charts = factory;
})(typeof window === "undefined" ? globalThis : window, function createCharts(dependencies) {
  const { platform, palette, OTHER_COLOR, qs } = dependencies;
  const { window, document } = platform;
  const charts = {};
  window.ttWebCharts = charts;
  // Grouped thousands: a five-digit total without them ($46736.41) has to be
  // counted digit by digit before it can be read as "about 47k".
  function money(value) {
    if (value === null || value === undefined) {
      return "—";
    }
    if (Number(value) > 0 && Number(value) < 0.01) return "<$0.01";
    if (Number(value) < 0 && Number(value) > -0.01) return "−<$0.01";
    return "$" + Number(value).toLocaleString("en-US", {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    });
  }

  function moneyPrecise(value) {
    if (value === null || value === undefined) {
      return "—";
    }
    const amount = Number(value);
    if (amount !== 0 && Math.abs(amount) < 0.0001) return "$" + amount.toPrecision(3);
    return "$" + amount.toLocaleString("en-US", { minimumFractionDigits: 4, maximumFractionDigits: 4 });
  }

  function costCoverageText(coverage) {
    if (!coverage || !Number.isInteger(coverage.known) || !Number.isInteger(coverage.unknown)) return "金额已知与未知的记录数未提供";
    return `金额记录：已知 ${integer(coverage.known)} · 未知 ${integer(coverage.unknown)}`;
  }

  function costReading(value, coverage) {
    const empty = coverage && coverage.known === 0 && coverage.unknown === 0;
    const amount = empty ? "无记录" : value == null ? "— 无已知金额" : moneyPrecise(value);
    return amount;
  }

  function integer(value) {
    return Number(value || 0).toLocaleString();
  }

  function pct(value) {
    if (value === null || value === undefined) {
      return "—";
    }
    return Math.round(Number(value)) + "%";
  }

  // Axis ticks and bar-end labels read as magnitudes, not as digit strings:
  // 20,000,000,000 costs a reader a digit count that "20B" does not.
  function compactNumber(value) {
    const n = Number(value || 0);
    const abs = Math.abs(n);
    if (abs >= 1e9) {
      return (n / 1e9).toFixed(abs >= 1e10 ? 0 : 1).replace(/\.0$/, "") + "B";
    }
    if (abs >= 1e6) {
      return (n / 1e6).toFixed(abs >= 1e7 ? 0 : 1).replace(/\.0$/, "") + "M";
    }
    if (abs >= 1e3) {
      return (n / 1e3).toFixed(abs >= 1e4 ? 0 : 1).replace(/\.0$/, "") + "K";
    }
    return String(Math.round(n));
  }

  // Values printed at the end of each bar. Three of the eight categorical slots
  // sit below 3:1 against the white panel, and that debt is only discharged by
  // labels the reader can see or a table beside the chart — so horizontal bar
  // charts here carry their values rather than relying on the axis alone.
  // The formatter is captured in a closure rather than read back off
  // `chart.options`: Chart.js resolves option values through a proxy that
  // treats a function as a scriptable option, and looking one up by name from
  // inside the plugin resolves to itself ("Recursion detected").
  function barValueLabels(format, missingText = null) {
    return {
      id: "barValueLabels",
      afterDatasetsDraw(instance) {
        const { ctx } = instance;
        ctx.save();
        // Read the label's face and colour from the stylesheet rather than
        // copying them here: a hard-coded copy silently diverges from the UI
        // labels beside it the next time the visual system changes.
        const rootStyle = getComputedStyle(document.documentElement);
        const numFace = rootStyle.getPropertyValue("--font-sans").trim() ||
          getComputedStyle(document.body).fontFamily;
        ctx.font = "600 12px " + numFace;
        ctx.fillStyle = rootStyle.getPropertyValue("--ink-muted").trim() || "#57616c";
        ctx.textBaseline = "middle";
        ctx.textAlign = "left";
        instance.data.datasets.forEach((set, setIndex) => {
          const meta = instance.getDatasetMeta(setIndex);
          if (meta.hidden) {
            return;
          }
          meta.data.forEach((element, pointIndex) => {
            const value = set.data[pointIndex];
            if (value === null || value === undefined) {
              if (missingText !== null) {
                ctx.fillText(missingText, instance.scales.x.getPixelForValue(0) + 8, element.y);
              }
              return;
            }
            ctx.fillText(format(value), element.x + 8, element.y);
          });
        });
        ctx.restore();
      },
    };
  }

  function shortText(value, length) {
    const text = String(value || "");
    if (text.length <= length) {
      return text;
    }
    return "…" + text.slice(text.length - length + 1);
  }

  const CHART_LIBRARY_MISSING =
    "图表不可用：/web/vendor/chart.umd.min.js 未加载。" +
    "请在提供服务的 agent-monitor 代码目录中运行 ./install.sh，然后重新加载。";

  function chart(key, canvasId, config) {
    const canvas = qs("#" + canvasId);
    if (!canvas) {
      return null;
    }
    if (!window.Chart) {
      // A silently empty box reads as "no data this period". The library
      // failing to load is the server's problem, and the box has to say so.
      // Deduplicate on a marker of its own: Explore's pivot keeps a hidden
      // no-data element with the shared .chart-empty-state class in the same
      // box, and matching on that class would suppress this note there.
      const box = canvas.parentElement;
      if (box && typeof box.querySelector === "function" && !box.querySelector(".chart-library-missing")) {
        const note = document.createElement("p");
        note.className = "empty-state chart-empty-state chart-library-missing";
        note.textContent = CHART_LIBRARY_MISSING;
        box.appendChild(note);
      }
      return null;
    }
    config.plugins = config.plugins || [];
    const existing = charts[key];
    if (existing && existing.canvas === canvas && existing.config.type === config.type) {
      existing.data = config.data;
      existing.options = config.options || {};
      existing.config.plugins.splice(0, existing.config.plugins.length, ...(config.plugins || []));
      existing.update("none");
      return existing;
    }
    if (existing) existing.destroy();
    charts[key] = new Chart(canvas, config);
    return charts[key];
  }

  function seriesColor(index) {
    return palette[index] || OTHER_COLOR;
  }

  // Agent identity must survive page-specific ordering and single-agent filters.
  const agentColors = new Map([["claude-code", palette[0]], ["codex", palette[1]]]);

  function dataset(label, data, index, extra) {
    const color = agentColors.get(extra?.agentId) || seriesColor(index);
    return Object.assign(
      {
        label,
        data,
        borderColor: color,
        backgroundColor: color,
        borderWidth: 2,
        // A single observation has no segment; keep its value visible.
        pointRadius: data.filter((value) => value != null && Number.isFinite(Number(value))).length === 1 ? 3 : 0,
        // Straight segments: smoothing a cost series draws intermediate values
        // between samples that were never spent.
        tension: 0,
      },
      extra || {}
    );
  }

  const SERIES_LIMIT = palette.length;

  function chartOptions(extra = {}) {
    const tooltip = {
      enabled: true, backgroundColor: "#fff", titleColor: "#171717", bodyColor: "#525252",
      borderColor: "#e5e5e5", borderWidth: 1, cornerRadius: 8, padding: 12,
      boxWidth: 8, boxHeight: 8, titleFont: { size: 12 }, bodyFont: { size: 12 },
    };
    const axis = { border: { display: false }, grid: { color: "#ededed", drawTicks: false },
      ticks: { color: "#737373", padding: 8, font: { size: 12 } } };
    const scales = {};
    for (const key of ["x", "y"]) {
      const specific = extra.scales?.[key] || {};
      scales[key] = { ...axis, ...specific,
        grid: { ...axis.grid, display: key === "y" && extra.indexAxis !== "y", ...specific.grid },
        ticks: { ...axis.ticks, maxRotation: 0, autoSkip: true, ...(key === "y" && extra.indexAxis === "y" ? { callback: categoryTick } : {}), ...specific.ticks },
      };
    }
    scales[extra.indexAxis === "y" ? "x" : "y"].beginAtZero = true;
    return {
      responsive: true, maintainAspectRatio: false,
      interaction: { mode: "index", intersect: false },
      elements: { point: { radius: 0, hoverRadius: 3, hitRadius: 8 }, bar: { borderRadius: 2 } },
      ...extra,
      plugins: { ...extra.plugins,
        legend: { position: "bottom", ...extra.plugins?.legend,
          labels: { boxWidth: 8, boxHeight: 8, padding: 16, color: "#525252", font: { size: 12 }, ...extra.plugins?.legend?.labels } },
        tooltip: { ...tooltip, ...extra.plugins?.tooltip },
      },
      scales,
    };
  }

  // Keep full labels in tooltips and data tables; fit only the painted axis.
  function categoryTick(value) {
    const full = String(this.getLabelForValue(value));
    const ctx = this.chart.ctx;
    // Chart.js may allocate less than half the canvas after layout padding.
    const budget = Math.max(12, Math.min(220, this.chart.width * 0.38, (this.maxWidth || Infinity) - 16));
    const font = this.options?.ticks?.font || {};
    ctx.save();
    ctx.font = `${font.style || "normal"} ${font.weight || "normal"} ${font.size || 12}px ${font.family || window.Chart?.defaults.font.family || getComputedStyle(document.body).fontFamily}`;
    let label = full;
    let length = full.length;
    while (length > 2 && ctx.measureText(label).width > budget) {
      length--;
      const head = Math.ceil(length / 2), tail = Math.floor(length / 2);
      label = full.slice(0, head) + "…" + full.slice(-tail);
    }
    ctx.restore();
    return label;
  }

  function chartMoneyTick(value) {
    const amount = Number(value);
    if (amount === 0) return "$0";
    return "$" + (Math.abs(amount) < 0.001
      ? amount.toExponential(2)
      : amount.toLocaleString("en-US", { maximumSignificantDigits: 3 }));
  }

  function chartReading(value, unit) {
    if (value === null || value === undefined || !Number.isFinite(Number(value))) return "未知";
    if (unit === "USD") {
      return moneyPrecise(value);
    }
    return `${integer(value)} tokens`;
  }

  function costTableReading(value, coverage) {
    return costReading(value, coverage);
  }

  function renderChartTable(id, headings, rows) {
    const table = qs(`#${id} table`);
    if (!table) return;
    const head = document.createElement("thead"), header = document.createElement("tr");
    for (const [index, title] of headings.entries()) {
      const cell = document.createElement("th");
      cell.scope = "col"; cell.textContent = title;
      if (index > 0) cell.className = "numeric";
      header.appendChild(cell);
    }
    head.appendChild(header);
    const body = document.createElement("tbody");
    for (const values of rows) {
      const row = document.createElement("tr");
      values.forEach((value, index) => {
        const cell = document.createElement(index === 0 ? "th" : "td");
        if (index === 0) cell.scope = "row";
        else cell.className = "numeric";
        if (value && typeof value === "object") {
          const amount = document.createElement("span");
          amount.className = "reading-amount";
          amount.textContent = value.amount;
          const coverage = document.createElement("small");
          coverage.className = "reading-coverage";
          coverage.textContent = value.coverage;
          cell.appendChild(amount);
          cell.appendChild(coverage);
        } else cell.textContent = value;
        row.appendChild(cell);
      });
      body.appendChild(row);
    }
    if (!rows.length) {
      const row = document.createElement("tr"), cell = document.createElement("td");
      cell.colSpan = headings.length; cell.textContent = "该范围暂无数据";
      row.appendChild(cell); body.appendChild(row);
    }
    // Preserve an open disclosure and its focused scroller during polling.
    table.replaceChildren(head, body);
  }

  function destroyCharts() {
    Object.values(charts).forEach(chart => chart.destroy());
    Object.keys(charts).forEach(key => delete charts[key]);
  }
  return { money, moneyPrecise, costCoverageText, costReading, integer, pct, compactNumber, barValueLabels, shortText, chart, seriesColor, dataset, chartOptions, categoryTick, chartMoneyTick, chartReading, costTableReading, renderChartTable, SERIES_LIMIT, destroyCharts };
});
