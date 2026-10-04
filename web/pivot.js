(function () {
  const presets = {
    "daily-cost": { x: "day", group: "agent", metric: "cost" },
    "project-cost": { x: "project", group: "none", metric: "cost" },
    "model-tokens": { x: "model", group: "agent", metric: "total" },
    "agent-project": { x: "agent", group: "project", metric: "cost" },
    cache: { x: "day", group: "agent", metric: "cache_read" },
  };

  const timeDims = new Set(["day", "week", "month"]);
  const filterNames = ["agent", "project", "model", "machine"];
  const filterAllLabels = { agent: "全部 Agent", project: "全部会话目录", model: "全部模型", machine: "全部机器" };
  const dimLabels = { day: "天", week: "周", month: "月", agent: "Agent 类型", project: "会话目录", model: "模型", machine: "机器" };
  let dimensions = [];
  let xDimPinned = false;
  let loadGeneration = 0;
  let pageCurrent = () => true;

  async function init() {
    pageCurrent = AgentMonitor.pageScope();
    const controls = ["#time-dim", "#metric", "#range", "#start-date", "#end-date"].map((selector) => AgentMonitor.qs(selector));
    applyQuery();
    let activeLoads = 0;
    async function load(force, background = false) {
      if (background && activeLoads) return;
      activeLoads += 1;
      try { await loadView(force); } finally { activeLoads -= 1; }
    }
    async function loadView(force) {
      const generation = ++loadGeneration;
      if (!validDates()) return;
      AgentMonitor.qs("#pivot-status").textContent = "加载中…";
      const before = await AgentMonitor.api("/api/sync-status");
      if (!isCurrentLoad(generation)) return;
      await loadFilterOptions(force, undefined, generation);
      if (!isCurrentLoad(generation)) return;
      await loadPivot(false, undefined, generation);
      if (!isCurrentLoad(generation)) return;
      let status = await AgentMonitor.api("/api/sync-status");
      if (!isCurrentLoad(generation)) return;
      AgentMonitor.renderSyncStatus(status);
      if (status.syncing) {
        status = await AgentMonitor.waitForSyncTerminal(status, {
          isCurrent: () => isCurrentLoad(generation),
          onProgress: async () => {
            await loadFilterOptions(false, false, generation);
            if (!isCurrentLoad(generation)) return;
            await loadPivot(false, false, generation);
          },
        });
        if (!isCurrentLoad(generation)) return;
        if (status.polling_error) return;
      }
      if (force || status.completed_at !== before.completed_at) {
        await loadFilterOptions(false, false, generation);
        if (!isCurrentLoad(generation)) return;
        await loadPivot(false, false, generation);
      }
    }
    AgentMonitor.bindShell(load, { range: false });
    AgentMonitor.watchPageData(() => load(false, true));
    controls.forEach((control) => {
      if (control) {
        control.addEventListener("change", async () => {
          updateDateControls();
          if (control.id === "range" && !xDimPinned && control.value !== "custom") {
            setDimensions([AgentMonitor.autoTimeDim(control.value), ...dimensions.filter((dim) => !timeDims.has(dim))]);
          }
          if (control.id === "time-dim") {
            xDimPinned = true;
            setDimensions([control.value, ...dimensions.filter((dim) => !timeDims.has(dim))]);
          }
          syncQuery();
          await load(false);
        });
      }
    });
    AgentMonitor.qsa("[data-group-dim]").forEach((control) => {
      control.addEventListener("change", async () => {
        const dim = control.dataset.groupDim;
        setDimensions(control.checked ? dimensions.concat(dim) : dimensions.filter((item) => item !== dim));
        xDimPinned = true;
        syncQuery();
        await load(false);
      });
    });
    AgentMonitor.qsa("[data-filter-control]").forEach((control) => {
      control.addEventListener("change", async () => {
        syncQuery();
        await load(false);
      });
    });
    AgentMonitor.qsa(".preset-btn").forEach((button) => {
      button.addEventListener("click", async () => {
        const preset = presets[button.dataset.preset];
        setDimensions([preset.x, preset.group]);
        AgentMonitor.qs("#metric").value = preset.metric;
        xDimPinned = true;
        syncQuery();
        await load(false);
      });
    });
    await load(false);
  }

  function applyQuery() {
    const query = AgentMonitor.params();
    const range = query.get("range") || "30d";
    xDimPinned = query.has("group_by") || query.has("x");
    setDimensions(query.has("group_by") ? query.get("group_by").split(",")
      : [query.get("x") || AgentMonitor.autoTimeDim(range), query.get("group") || "agent"]);
    setValue("#metric", query.get("metric") || "cost");
    setValue("#range", range);
    setValue("#start-date", query.get("start") || "");
    setValue("#end-date", query.get("end") || "");
    updateDateControls();
    applyFilterQuery();
  }

  function updateDateControls() {
    const custom = AgentMonitor.getRange() === "custom";
    AgentMonitor.qsa(".custom-date").forEach((field) => { field.hidden = !custom; });
  }

  function dateParams() {
    return AgentMonitor.getRange() === "custom" ? {
      start: AgentMonitor.qs("#start-date").value,
      end: AgentMonitor.qs("#end-date").value,
    } : {};
  }

  function validDates() {
    const { start, end } = dateParams();
    const custom = AgentMonitor.getRange() === "custom";
    const error = custom && (!start || !end) ? "请选择起始和终止日期。"
      : custom && (!AgentMonitor.qs("#start-date").checkValidity() || !AgentMonitor.qs("#end-date").checkValidity()) ? "请选择有效日期。"
      : custom && start > end ? "起始日期不能晚于终止日期。" : "";
    const message = AgentMonitor.qs("#date-error");
    if (message) { message.textContent = error; message.hidden = !error; }
    if (error) {
      AgentMonitor.qs("#pivot-panel").hidden = true;
      AgentMonitor.qs("#ranking-panel").hidden = true;
    }
    return !error;
  }

  function setDimensions(values) {
    dimensions = [...new Set(values)].filter((dim) => Object.hasOwn(dimLabels, dim));
    const time = dimensions.find((dim) => timeDims.has(dim));
    dimensions = dimensions.filter((dim) => !timeDims.has(dim));
    if (time) dimensions.unshift(time);
    setValue("#time-dim", time || "none");
    AgentMonitor.qsa("[data-group-dim]").forEach((control) => {
      control.checked = dimensions.includes(control.dataset.groupDim);
    });
    const summary = AgentMonitor.qs("#group-summary");
    if (summary) summary.textContent = dimensions.length
      ? `按 ${dimensions.map((dim) => dimLabels[dim]).join(" × ")} 汇总`
      : "不分组 · 查看筛选范围内的总量";
  }

  function setValue(selector, value) {
    const control = AgentMonitor.qs(selector);
    if (control) {
      control.value = value;
    }
  }

  function isCurrentLoad(generation) {
    return pageCurrent() && generation === loadGeneration;
  }

  async function loadFilterOptions(force, allowSync, generation) {
    const options = await AgentMonitor.api("/api/pivot-filters", {
      range: AgentMonitor.getRange(),
      ...dateParams(),
      force: force ? "1" : undefined,
      sync: allowSync === false ? "0" : undefined,
    });
    if (generation !== undefined && !isCurrentLoad(generation)) {
      return;
    }
    filterNames.forEach((name) => populateFilter(name, options[name] || []));
    applyFilterQuery();
  }

  function populateFilter(name, values) {
    const select = AgentMonitor.qs(`#${name}-filter`);
    if (!select) {
      return;
    }
    const selected = desiredFilterValue(name);
    const allValues = values.slice();
    if (selected && !allValues.includes(selected)) {
      allValues.push(selected);
    }
    select.innerHTML = "";
    const allOption = document.createElement("option");
    allOption.value = "";
    allOption.textContent = filterAllLabels[name];
    select.appendChild(allOption);
    allValues.forEach((value) => {
      const option = document.createElement("option");
      option.value = value;
      option.textContent = value;
      select.appendChild(option);
    });
    select.value = selected || "";
    select.disabled = false;
  }

  function desiredFilterValue(name) {
    return AgentMonitor.params().get(name) || (AgentMonitor.qs(`#${name}-filter`) && AgentMonitor.qs(`#${name}-filter`).value) || "";
  }

  function applyFilterQuery() {
    const query = AgentMonitor.params();
    filterNames.forEach((name) => {
      setValue(`#${name}-filter`, query.get(name) || "");
    });
  }

  function syncQuery() {
    AgentMonitor.setParam("x", null);
    AgentMonitor.setParam("group", null);
    AgentMonitor.setParam("group_by", dimensions.join(",") || "none");
    AgentMonitor.setParam("metric", AgentMonitor.qs("#metric").value);
    AgentMonitor.setParam("range", AgentMonitor.qs("#range").value);
    const dates = dateParams();
    AgentMonitor.setParam("start", dates.start || null);
    AgentMonitor.setParam("end", dates.end || null);
    syncFilterQuery();
  }

  function syncFilterQuery() {
    const url = new URL(window.location.href);
    filterNames.forEach((name) => url.searchParams.delete(name));
    Object.entries(selectedFilters()).forEach(([name, value]) => {
      url.searchParams.set(name, value);
    });
    // Through the shared writer: a raw replaceState here left the navigation
    // layer's idea of the current route at the pre-filter URL.
    AgentMonitor.replaceCurrentUrl(url.pathname + url.search);
  }

  function selectedFilters() {
    const filters = {};
    filterNames.forEach((name) => {
      const control = AgentMonitor.qs(`#${name}-filter`);
      const value = control ? control.value : "";
      if (value) {
        filters[name] = value;
      }
    });
    return filters;
  }

  async function loadPivot(force, allowSync, generation) {
    const group_by = dimensions.join(",") || "none";
    const metric = AgentMonitor.qs("#metric").value;
    const range = AgentMonitor.getRange();
    AgentMonitor.qs("#pivot-status").textContent = "加载中…";
    const data = await AgentMonitor.api(
      "/api/pivot",
      Object.assign({ group_by, metric, range, ...dateParams(), force: force ? "1" : undefined, sync: allowSync === false ? "0" : undefined }, selectedFilters())
    );
    if (generation !== undefined && !isCurrentLoad(generation)) {
      return;
    }
    const showTrend = data.dimensions.some((dim) => timeDims.has(dim)) || !data.dimensions.length;
    AgentMonitor.qs("#pivot-panel").hidden = !showTrend;
    if (showTrend) renderChart(chartData(data, metric), data.dimensions[0] || "none", metric);
    renderRanking(data, metric);
    AgentMonitor.qs("#pivot-status").textContent = data.rows.length ? "就绪" : "无数据";
  }

  function chartData(data, metric) {
    const dims = data.dimensions;
    const columns = new Map();
    const rows = new Map();
    data.rows.forEach((record) => {
      const x = record.key[0] ?? "总量";
      const rest = record.key.slice(1);
      const column = JSON.stringify(rest);
      const labels = rest.map((value, i) => displayLabel(value, dims[i + 1]));
      columns.set(column, {
        text: rest.length ? labels.map((label, i) => `${dimLabels[dims[i + 1]]}：${label.text}`).join(" · ") : "合计",
        fullLabel: rest.length ? labels.map((label, i) => `${dimLabels[dims[i + 1]]}：${label.fullLabel}`).join(" · ") : "合计",
      });
      if (!rows.has(x)) rows.set(x, { x, values: Object.create(null) });
      rows.get(x).values[column] = record.value;
    });
    const resultRows = [...rows.values()];
    resultRows.forEach((row) => columns.forEach((_label, column) => {
      if (!(column in row.values)) row.values[column] = metric === "cost" ? null : 0;
    }));
    if (!timeDims.has(dims[0])) resultRows.sort((a, b) =>
      Object.values(b.values).reduce((sum, v) => sum + (v || 0), 0)
      - Object.values(a.values).reduce((sum, v) => sum + (v || 0), 0));
    return { columns: [...columns.keys()], rows: resultRows, labels: Object.fromEntries(columns) };
  }

  function chartType(x) {
    return timeDims.has(x) ? "line" : "bar";
  }

  function hasNoPivotData(data) {
    return !data.rows.length || !data.columns.length;
  }

  // A chart that quietly shows fewer series than the data has reads as if it
  // showed all of them, so say what was left out and where to find it.
  function renderSeriesLimitNote(plotted, columnCount) {
    const note = AgentMonitor.qs("#pivot-series-note");
    if (!note) {
      return;
    }
    if (plotted.dropped > 0) {
      note.hidden = false;
      // Not "the N smallest": ranking counts a null as zero, and for cost a
      // null is either no activity or an unknown price. A series nobody could
      // price therefore ranks at the bottom whatever it actually cost, so the
      // note states the basis instead of asserting a size order it cannot know.
      note.textContent = `趋势图显示按已知成本排名靠前的 ${AgentMonitor.SERIES_LIMIT} 个组合，另 ${plotted.dropped} 个未绘制。下方排名保留全部组合，悬浮可看各期数值；未知成本显示 —。`;
      return;
    }
    note.hidden = true;
    note.textContent = "";
  }

  // The palette has a fixed number of slots and no ninth hue to hand out, so a
  // grouping that produces more series than that (group by model, typically)
  // cannot draw them all. What happens to the remainder depends on the metric.
  //
  // For token and message metrics the server writes 0 for a bucket a column had
  // no activity in, so the tail sums cleanly into one "Other" line.
  //
  // Cost is different: `aggregators.pivot` writes null both when a column had no
  // activity in that bucket AND when it had activity whose price agent-monitor does not
  // know. Those two are indistinguishable by the time they reach us, so summing
  // the non-null members would publish a figure that silently omits real spend
  // and still reads as a complete total. Rather than fabricate that number, the
  // smallest columns are left out of the chart and the omission is stated; the
  // ranking below retains every combination and its time buckets.
  function foldColumnsToSeriesLimit(data, metric) {
    const seriesOf = (column) => data.rows.map((row) => row.values[column]);
    const columns = data.columns.map((key) => ({ key, series: seriesOf(key) }));
    if (columns.length <= AgentMonitor.SERIES_LIMIT) {
      return { columns, dropped: 0, folded: 0 };
    }
    const total = (series) => series.reduce((sum, value) => sum + Math.abs(Number(value) || 0), 0);
    const ranked = columns.slice().sort((a, b) => total(b.series) - total(a.series));

    if (metric === "cost") {
      return {
        columns: ranked.slice(0, AgentMonitor.SERIES_LIMIT),
        dropped: columns.length - AgentMonitor.SERIES_LIMIT,
        folded: 0,
      };
    }

    const head = ranked.slice(0, AgentMonitor.SERIES_LIMIT - 1);
    const tail = ranked.slice(AgentMonitor.SERIES_LIMIT - 1);
    const merged = data.rows.map((row, rowIndex) =>
      tail.reduce((sum, column) => sum + Number(column.series[rowIndex] || 0), 0)
    );
    return {
      columns: head.concat([{ other: true, label: `其它（${tail.length}）`, series: merged }]),
      dropped: 0,
      folded: tail.length,
    };
  }

  function renderChart(data, x, metric) {
    if (hasNoPivotData(data)) {
      // Clear the note here too, or an empty chart keeps claiming it is
      // plotting a subset of series that are no longer on screen.
      renderSeriesLimitNote({ dropped: 0 }, 0);
      renderEmptyChart();
      return;
    }
    setChartEmptyState(false);
    const type = chartType(x);
    const xLabels = data.rows.map((row) => displayLabel(row.x, x));
    const plotted = foldColumnsToSeriesLimit(data, metric);
    renderSeriesLimitNote(plotted, data.columns.length);
    const datasets = plotted.columns.map((column, index) => {
      if (column.other) {
        return AgentMonitor.dataset(column.label, column.series, index, {
          fill: false,
          spanGaps: true,
          fullLabel: column.label,
        });
      }
      const label = data.labels[column.key];
      return AgentMonitor.dataset(label.text, column.series, index, {
        fill: false,
        spanGaps: true,
        fullLabel: label.fullLabel,
      });
    });
    AgentMonitor.chart("pivot", "pivot-chart", {
      type,
      data: {
        labels: xLabels.map((label) => label.text),
        datasets,
      },
      options: AgentMonitor.chartOptions({
        indexAxis: type === "bar" ? "y" : "x",
        plugins: {
          legend: { position: "bottom" },
          tooltip: {
            callbacks: {
              title(items) {
                if (!items.length) {
                  return "";
                }
                const label = xLabels[items[0].dataIndex];
                return label ? label.fullLabel : "";
              },
              label(context) {
                const value = context.raw;
                const label = context.dataset.fullLabel || context.dataset.label;
                return `${label}: ${formatValue(value, metric)}`;
              },
            },
          },
        },
      }),
    });
  }

  function renderEmptyChart() {
    if (window.ttWebCharts && window.ttWebCharts.pivot) {
      window.ttWebCharts.pivot.destroy();
      delete window.ttWebCharts.pivot;
    }
    const canvas = AgentMonitor.qs("#pivot-chart");
    if (canvas) {
      const context = canvas.getContext("2d");
      if (context) {
        context.clearRect(0, 0, canvas.width, canvas.height);
      }
    }
    setChartEmptyState(true);
  }

  function setChartEmptyState(show) {
    const canvas = AgentMonitor.qs("#pivot-chart");
    if (!canvas) {
      return;
    }
    const box = canvas.closest(".chart-box");
    if (!box) {
      return;
    }
    let empty = AgentMonitor.qs("#pivot-chart-empty", box);
    if (!empty) {
      empty = document.createElement("div");
      empty.id = "pivot-chart-empty";
      empty.className = "empty-state chart-empty-state";
      empty.textContent = "— 无数据";
      box.appendChild(empty);
    }
    empty.hidden = !show;
    canvas.hidden = show;
  }

  function rankingData(data) {
    const indices = data.dimensions.map((dim, i) => timeDims.has(dim) ? -1 : i).filter((i) => i >= 0);
    const timeIndex = data.dimensions.findIndex((dim) => timeDims.has(dim));
    const groups = new Map();
    data.rows.forEach((row) => {
      const key = indices.map((i) => row.key[i]);
      const id = JSON.stringify(key);
      if (!groups.has(id)) groups.set(id, { key, value: null, periods: [] });
      const group = groups.get(id);
      if (row.value !== null) group.value = (group.value ?? 0) + row.value;
      if (timeIndex >= 0) group.periods.push({ label: row.key[timeIndex], value: row.value });
    });
    return { dimensions: indices.map((i) => data.dimensions[i]), rows: [...groups.values()].sort((a, b) =>
      a.value === null ? (b.value === null ? 0 : 1) : b.value === null ? -1 : b.value - a.value) };
  }

  function renderRanking(data, metric) {
    const ranked = rankingData(data);
    const panel = AgentMonitor.qs("#ranking-panel");
    panel.hidden = !ranked.dimensions.length;
    if (panel.hidden) return;
    const metricLabel = AgentMonitor.qs("#metric").selectedOptions[0].textContent;
    AgentMonitor.qs("#ranking-title").textContent = ranked.dimensions.map((dim) => dimLabels[dim]).join(" × ") + " · " + metricLabel;
    AgentMonitor.qs("#ranking-count").textContent = AgentMonitor.integer(ranked.rows.length) + " 个组合";
    AgentMonitor.qs("#ranking-description").textContent = "所选时间范围内合计 · 按" + (metric === "cost" ? "已知成本" : metricLabel) + "降序" +
      (data.dimensions.some((dim) => timeDims.has(dim)) ? " · 悬浮或聚焦查看各期数值" : "");
    const max = ranked.rows.reduce((largest, row) => Math.max(largest, row.value || 0), 0);
    AgentMonitor.qs("#group-ranking").innerHTML = ranked.rows.length ? ranked.rows.map((row) => {
      const label = row.key.map((value, i) => dimLabels[ranked.dimensions[i]] + "：" + value).join(" · ");
      const periods = row.periods.map((period) => `<div><span>${escapeHtml(period.label)}</span><span>${formatValue(period.value, metric)}</span></div>`).join("");
      const width = max > 0 && row.value !== null ? Math.max(0, row.value / max * 100) : 0;
      return `<li class="ranking-item" tabindex="0"><div class="ranking-heading"><span>${escapeHtml(label)}</span><strong>${formatValue(row.value, metric)}</strong></div><div class="ranking-track" aria-hidden="true"><span style="width:${width}%"></span></div>${periods ? `<div class="ranking-periods" aria-label="各期数值">${periods}</div>` : ""}</li>`;
    }).join("") : '<li class="empty-state">— 无数据</li>';
  }
  function displayLabel(value, dim) {
    const fullLabel = String(value || "");
    if (dim !== "project") {
      return { text: fullLabel, fullLabel };
    }
    return { text: projectDisplayLabel(value), fullLabel };
  }

  function projectDisplayLabel(value) {
    const text = String(value || "");
    if (!text.startsWith("/") || text === "Other") {
      return text;
    }
    const parts = text.split("/").filter(Boolean);
    if (parts.length <= 2) {
      return parts.join("/");
    }
    return parts.slice(-2).join("/");
  }

  function formatValue(value, metric) {
    if (metric === "cost") {
      return AgentMonitor.moneyPrecise(value);
    }
    if (value === null || value === undefined) {
      return "—";
    }
    return AgentMonitor.integer(value);
  }

  function escapeHtml(value) {
    return String(value || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  window.AgentMonitorPivot = { init, chartType, chartData, rankingData };
})();
