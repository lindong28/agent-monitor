(function () {
  const charts = {};
  window.ttWebCharts = charts;

  // Eight categorical slots, assigned in fixed order and never cycled. Validated
  // against this dashboard's white panel surface on the adjacent pairlist that
  // lines, bars and stacks use: worst CVD ΔE 9.1 (protan), worst normal-vision
  // ΔE 19.6. The previous set put claude-opus-5 (#0f766e) and claude-sonnet-5
  // (#0e7490) at ΔE 5.8 — the same legend, indistinguishable to full colour
  // vision — and paired violet with blue at ΔE 0.4 under deuteranopia.
  // Slots 3, 4 and 5 fall below 3:1 against white, so any chart that reaches
  // them owes the reader direct labels or a table view beside it.
  const palette = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"];
  // A ninth series is not a generated hue: callers fold the tail into "Other".
  const OTHER_COLOR = "#8a8a80";
  const rangeDays = { "7d": 7, "30d": 30, "90d": 90, "6m": 180, "1y": 365, "2y": 730 };

  // Absolute timestamps are rendered in the timezone the server (this machine)
  // currently resolves from its OS/TZ setting — never a hardcoded zone, and never
  // the browser's own zone (which can be a stale value cached at browser startup).
  // The server reads the live setting per request, so the display can't drift from
  // the actual system configuration. Until the server's zone is fetched, fall back
  // to the browser's local zone.
  let serverTimeZone = null;

  let _timezonePromise = null;
  function ensureTimezone() {
    if (!_timezonePromise) {
      _timezonePromise = fetch(new URL("/api/timezone", window.location.origin))
        .then((response) => (response.ok ? response.json() : null))
        .then((json) => {
          if (json && typeof json.timezone === "string") {
            serverTimeZone = json.timezone;
          }
        })
        .catch(() => {});
    }
    return _timezonePromise;
  }

  function fmtAbs(date) {
    const opts = serverTimeZone ? { timeZone: serverTimeZone } : undefined;
    try {
      const main = date.toLocaleString("zh-CN", opts);
      const part = new Intl.DateTimeFormat("en-US", Object.assign({ timeZoneName: "shortOffset" }, opts))
        .formatToParts(date)
        .find((p) => p.type === "timeZoneName");
      return part ? main + " " + part.value : main;
    } catch (e) {
      return date.toLocaleString("zh-CN");
    }
  }

  function qs(selector, root) {
    return (root || document).querySelector(selector);
  }

  function qsa(selector, root) {
    return Array.from((root || document).querySelectorAll(selector));
  }

  function params() {
    return new URLSearchParams(window.location.search);
  }

  function getRange() {
    const select = qs("#range");
    return (select && select.value) || params().get("range") || "30d";
  }

  function autoTimeDim(range) {
    if (range === "all") {
      return "month";
    }
    const days = rangeDays[range] || 30;
    if (days <= 90) {
      return "day";
    }
    if (days <= 365) {
      return "week";
    }
    return "month";
  }

  function setParam(key, value) {
    const next = params();
    if (value) {
      next.set(key, value);
    } else {
      next.delete(key);
    }
    const query = next.toString();
    replaceCurrentUrl(window.location.pathname + (query ? "?" + query : ""));
    updateNavLinks();
  }

  function updateNavLinks() {
    updateSyncOverviewLink();
    const range = getRange();
    function setLinkRange(url) {
      const custom = range === "custom" && ["/explore", "/sessions", "/collection"].includes(url.pathname);
      url.searchParams.set("range", range === "custom" && !custom ? "30d" : range);
      ["start", "end"].forEach((key) => {
        const value = custom && params().get(key);
        if (value) url.searchParams.set(key, value);
        else url.searchParams.delete(key);
      });
    }
    qsa("[data-nav]").forEach((link) => {
      const url = new URL(link.getAttribute("href"), window.location.origin);
      setLinkRange(url);
      if (url.pathname === "/collection" && window.location.pathname !== "/collection") url.searchParams.set("return", currentPageUrl());
      link.href = url.pathname + url.search;
      const current = url.pathname === window.location.pathname;
      link.setAttribute("aria-current", current ? "page" : "false");
    });
    qsa("[data-preserve-range]").forEach((link) => {
      const url = new URL(link.getAttribute("href"), window.location.origin);
      setLinkRange(url);
      link.href = url.pathname + url.search;
    });
    updateAreaNavigation();
  }

  // The shell survives main swaps. Keep the full console URL separately from
  // ordinary range propagation, which deliberately normalizes custom on guide.
  const GUIDE_CHAPTERS = ["sources", "collection", "setup", "questions"];
  const GUIDE_ROUTES = new Set(["/guide", ...GUIDE_CHAPTERS.map(chapter => `/guide/${chapter}`)]);
  let lastConsoleUrl = null;
  function updateAreaNavigation() {
    const guide = GUIDE_ROUTES.has(window.location.pathname);
    if (!guide) lastConsoleUrl = window.location.pathname + window.location.search + (window.location.hash || "");
    const consoleLink = qs('[data-area="console"]');
    if (consoleLink) consoleLink.href = lastConsoleUrl || "/?range=" + encodeURIComponent(getRange() === "custom" ? "30d" : getRange());
    qsa("[data-area]").forEach((link) => {
      link.setAttribute("aria-current", (link.dataset.area === "guide") === guide ? "page" : "false");
    });
    const consoleNav = qs(".sidebar .nav");
    const guideNav = qs(".guide-nav");
    if (consoleNav) consoleNav.hidden = guide;
    if (guideNav) guideNav.hidden = !guide;
    // Guide pages share the date context while keeping console return state separate.
    qsa(".guide-nav a").forEach((link) => {
      const url = new URL(link.href, window.location.origin);
      url.search = window.location.search;
      link.href = url.pathname + url.search + url.hash;
      const currentPath = window.location.pathname === "/guide" ? "/guide/sources" : window.location.pathname;
      link.setAttribute("aria-current", url.pathname === currentPath ? "page" : "false");
    });
  }

  const PAGE_SEARCH_ENTRIES = [
    ["控制台", "总览", "用量、成本趋势与账号配额", "/"],
    ["控制台", "采集与诊断", "机器采集、历史质量与未归属账号的观测", "/collection"],
    ["控制台", "透视", "按目录、模型、机器分析成本和 token", "/explore"],
    ["控制台", "会话", "查找会话、筛选记录并查看明细", "/sessions"],
    ["控制台", "网络", "查看服务端主机的出口和网络诊断", "/network"],
    ["控制台", "配额与账号", "Claude / Codex 账号余量与更新时间", "/#quota-explorer"],
    ["使用与接入", "数据来源", "本地日志、服务商账号与服务端网络", "/guide/sources"],
    ["使用与接入", "采集状态", "检查已纳入机器与快照状态", "/guide/collection"],
    ["使用与接入", "首次接入", "安装、命令行 CLI 与多机配置", "/guide/setup"],
    ["使用与接入", "查找证据", "成本变化、会话追溯、账号选择与空页面", "/guide/questions"],
  ];

  function initPageSearch() {
    const dialog = qs("#page-search");
    const opener = qs("#open-page-search");
    if (!dialog || !opener) return;
    const input = qs("#page-search-input");
    const results = qs("#page-search-results");
    function render() {
      const words = input.value.trim().toLocaleLowerCase().split(/\s+/).filter(Boolean);
      const matches = PAGE_SEARCH_ENTRIES.filter((entry) => words.every((word) => entry.slice(0, 3).join(" ").toLocaleLowerCase().includes(word)));
      qs("#page-search-count").textContent = matches.length ? `${matches.length} 个入口` : "没有匹配的页面或帮助，请换个关键词或清空搜索。";
      results.replaceChildren();
      let group = "";
      matches.forEach(([category, title, description, path]) => {
        if (group !== category) {
          const heading = document.createElement("h3");
          heading.textContent = category;
          results.appendChild(heading);
          group = category;
        }
        const link = document.createElement("a");
        const url = new URL(path, window.location.origin);
        const range = getRange();
        const custom = range === "custom" && ["/explore", "/sessions", "/collection"].includes(url.pathname);
        url.searchParams.set("range", range === "custom" && !custom ? "30d" : range);
        if (custom) ["start", "end"].forEach((key) => { if (params().get(key)) url.searchParams.set(key, params().get(key)); });
        if (url.pathname === "/collection" && window.location.pathname !== "/collection") url.searchParams.set("return", currentPageUrl());
        link.href = url.pathname + url.search + url.hash;
        const label = document.createElement("strong");
        label.textContent = title;
        const detail = document.createElement("span");
        detail.textContent = description;
        link.append(label, detail);
        results.appendChild(link);
      });
    }
    opener.addEventListener("click", () => { render(); dialog.showModal(); input.focus(); });
    qs("#close-page-search").addEventListener("click", () => dialog.close());
    dialog.addEventListener("keydown", (event) => {
      if (event.key === "Escape") { event.preventDefault(); dialog.close(); }
    });
    dialog.addEventListener("close", () => opener.focus());
    results.addEventListener("click", (event) => { if (event.target.closest("a")) dialog.close(); });
    input.addEventListener("input", render);
    qs("#clear-page-search").addEventListener("click", () => { input.value = ""; render(); input.focus(); });
  }

  async function copyText(button, text, failure = "复制失败，请手动选择") {
    try {
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(text);
      } else {
        // Personal-network HTTP deployments do not expose Clipboard API.
        const field = document.createElement("textarea");
        field.value = text;
        field.style.cssText = "position:fixed;opacity:0;pointer-events:none";
        document.body.appendChild(field);
        field.select();
        let copied;
        try { copied = document.execCommand("copy"); }
        finally { field.remove(); button.focus(); }
        if (!copied) throw new Error("Clipboard unavailable");
      }
      button.textContent = "已复制";
    } catch (_) {
      button.textContent = failure;
    }
  }

  async function copyGuideCode(button) {
    const code = document.getElementById(button.dataset.copyCode);
    if (code) await copyText(button, code.textContent);
  }

  async function api(path, query) {
    const tzReady = ensureTimezone();
    const url = new URL(path, window.location.origin);
    Object.entries(query || {}).forEach(([key, value]) => {
      if (Array.isArray(value)) {
        value.forEach((item) => url.searchParams.append(key, item));
      } else if (value !== undefined && value !== null && value !== "") {
        url.searchParams.set(key, value);
      }
    });
    const action = path === "/api/refresh" || (path === "/api/network" && query?.force === "1");
    const response = await fetch(url, action ? {
      method: "POST", headers: { "X-Agent-Monitor": "action" },
    } : undefined);
    if (!response.ok) {
      throw new Error(`${response.status} ${response.statusText}`);
    }
    const json = await response.json();
    await tzReady;
    return json;
  }

  function bindShell(load, options) {
    const range = qs("#range");
    const bindRange = !options || options.range !== false;
    const requested = params().get("range");
    if (range && requested) {
      range.value = requested;
    }
    if (range && bindRange) {
      range.addEventListener("change", () => {
        setParam("range", range.value);
        load(false);
      });
    }
    const refresh = qs("#refresh");
    if (refresh) {
      refresh.addEventListener("click", () => withRefresh(refresh, () => load(true)));
    }
    updateNavLinks();
  }

  async function withRefresh(button, load) {
    const label = button.textContent;
    const wasDisabled = button.disabled;
    button.setAttribute("aria-busy", "true");
    button.disabled = true;
    button.textContent = "刷新中";
    try {
      await load();
    } finally {
      button.textContent = label;
      button.setAttribute("aria-busy", "false");
      button.disabled = wasDisabled;
    }
  }

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

  function renderOverview(data, selectedRange) {
    qs("#today-cost").textContent = money(data.today.cost_usd);
    qs("#week-cost").textContent = money(data.week.cost_usd);
    const weekContext = qs("#week-context");
    if (weekContext) {
      weekContext.textContent = data.week.window && data.week.window.start
        ? "自周一 00:00 · GMT+8"
        : "本周";
    }
    renderQuotaAccounts(data.rate_limits);
    // After the quota table, and guarded: a cached copy of the older HTML has no
    // #range-label, and an unguarded write here would throw before the table,
    // charts and sync panel rendered — turning "one card is missing" into a
    // blank page, which is exactly the skew initOverview already handles.
    renderRangeCost(data.range, selectedRange || getRange(), data.rollup_coverage);
    const costHistory = data.cost_over_time || legacyCostHistory(data.daily_cost_30d || []);
    const costGranularity = data.cost_over_time_granularity || "day";
    renderChartTable("daily-cost-data", ["时间", ...costHistory.columns.map((column) => `${agentLabel(column)} · USD`)],
      costHistory.rows.map((row) => [row.x, ...costHistory.columns.map((column) => costTableReading(row.values[column], row.cost_coverage?.[column]))]));
    chart("dailyCost", "daily-cost-chart", {
      type: "line",
      data: {
        labels: costHistory.rows.map((row) => row.x),
        datasets: costHistory.columns.map((column, index) =>
          dataset(agentLabel(column), costHistory.rows.map((row) => row.values[column]), index, {
            agentId: column,
            fill: false,
            spanGaps: true,
          })
        ),
      },
      options: chartOptions({ plugins: { tooltip: { callbacks: {
        label: (context) => `${context.dataset.label}: ${costReading(context.raw, costHistory.rows[context.dataIndex].cost_coverage?.[costHistory.columns[context.datasetIndex]])}`,
      } } }, scales: { y: { ticks: { callback: (value) => chartMoneyTick(value) } }, x: { ticks: { maxTicksLimit: 7,
        callback(value) {
          const label = String(this.getLabelForValue(value));
          if (!/^\d{4}-\d{2}-\d{2}$/.test(label)) return label;
          const years = new Set(this.chart.data.labels.map((date) => String(date).slice(0, 4)));
          return years.size > 1 ? label.replaceAll("-", "/") : label.slice(5).replace("-", "/");
        },
      } } } }),
    });
    const costMeta = qs("#cost-over-time-meta");
    if (costMeta) {
      costMeta.textContent = `${rangeSummaryLabel(selectedRange || getRange())} · ${granularityLabel(costGranularity)}`;
    }
    const coverage = qs("#cost-over-time-coverage");
    if (coverage) {
      const earliest = data.rollup_coverage && data.rollup_coverage.earliest_date;
      if (earliest && data.rollup_coverage.partial_before_range) {
        coverage.hidden = false;
        coverage.textContent = `历史自 ${earliest} 起累积；更早未采集。`;
      } else {
        coverage.hidden = true;
        coverage.textContent = "";
      }
    }
    const costLink = qs("#cost-over-time-link");
    if (costLink) {
      const url = new URL("/explore", window.location.origin);
      url.searchParams.set("range", selectedRange || getRange());
      url.searchParams.set("x", costGranularity);
      url.searchParams.set("group", "agent");
      url.searchParams.set("metric", "cost");
      costLink.href = url.pathname + url.search;
    }

    [["#top-projects-link", "week"], ["#model-mix-link", "month"]].forEach(([selector, key]) => {
      const link = qs(selector), dates = data.attribution_windows?.[key];
      if (!link || !dates) return;
      const url = new URL(link.href, window.location.origin);
      Object.entries({ range: "custom", start: dates.start, end: dates.end }).forEach(([k,v]) => url.searchParams.set(k,v));
      link.href = url.pathname + url.search;
      link.title = `${dates.start} 至 ${dates.end} · GMT+8`;
    });
    if (data.comparison) renderComparison(data.comparison);
    const projects = data.top_projects_week.slice();
    const projectLabels = projects.map(row => {
      const parts = row.project.split("/").filter(Boolean);
      for (let count = 1; count < parts.length; count++) {
        const label = parts.slice(-count).join("/");
        if (projects.filter(other => other.project.split("/").filter(Boolean).slice(-count).join("/") === label).length === 1) return label;
      }
      return row.project;
    });
    renderChartTable("top-projects-data", ["会话目录", "成本 · USD"],
      projects.map((row) => [row.project, costTableReading(row.cost_usd, row.cost_coverage)]));
    chart("topProjects", "top-projects-chart", {
      type: "bar",
      data: {
        labels: projectLabels,
        datasets: [dataset("成本", projects.map((row) => row.cost_usd), 0)],
      },
      options: chartOptions({
        indexAxis: "y",
        // One series: the panel heading already names it, so a legend box would
        // only repeat the title. Bar ends carry the values instead.
        plugins: {
          legend: { display: false },
          tooltip: { callbacks: {
            title: (items) => projects[items[0].dataIndex].project,
            label: (context) => costReading(context.raw, projects[context.dataIndex].cost_coverage),
          } },
        },
        scales: { x: { beginAtZero: true, ticks: { callback: (value) => "$" + compactNumber(value) } }, y: { ticks: { autoSkip: false, callback: categoryTick } } },
        layout: { padding: { right: 96 } },
      }),
      plugins: [barValueLabels(money)],
    });

    // Model mix is a magnitude comparison across models. Stacking eight series
    // onto a single "This month" column made five of them thinner than a pixel
    // while each still claimed a legend entry; ranked horizontal bars let every
    // model be read and compared directly.
    const mix = data.model_mix_month.slice().sort((a, b) => Number(b.tokens) - Number(a.tokens));
    renderChartTable("model-mix-data", ["模型", "用量 · tokens"],
      mix.map((row) => [row.model, chartReading(row.tokens, "tokens")]));
    chart("modelMix", "model-mix-chart", {
      type: "bar",
      data: {
        labels: mix.map((row) => row.model),
        datasets: [dataset("token", mix.map((row) => row.tokens), 0)],
      },
      options: chartOptions({
        indexAxis: "y",
        plugins: { legend: { display: false }, tooltip: { callbacks: {
          label: (context) => `${context.dataset.label}: ${chartReading(context.raw, "tokens")}`,
        } } },
        scales: {
          x: { beginAtZero: true, ticks: { callback: (value) => compactNumber(value) } },
          y: { ticks: { autoSkip: false, callback: categoryTick } },
        },
        layout: { padding: { right: 64 } },
      }),
      plugins: [barValueLabels(compactNumber)],
    });
  }

  function renderComparison(data) {
    const target = qs("#comparison-result");
    if (!target) return;
    if (!data.available) { target.textContent = data.reason; return; }
    const period = (label, p) => {
      const query = new URLSearchParams({range: "custom", start: p.start, end: p.end, group_by: "model", metric: "cost"});
      return `<tr><th>${label}<br>${p.start} — ${p.end}<br><a class="panel-link" href="/explore?${escapeHtml(query.toString())}">透视此周期</a></th><td>${p.entries ? money(p.known_cost_usd) : "无记录"}</td><td>${p.entries ? integer(p.tokens) : "无记录"}</td></tr>`;
    };
    const delta = data.known_cost_delta_usd;
    const change = delta === null ? "无法比较（一期无已知金额）" : delta === 0 ? "相同" : `${delta > 0 ? "增加" : "减少"} ${money(Math.abs(delta))}`;
    target.innerHTML = `<p class="comparison-summary">最近完整周期的已知成本较前一周期${change}。</p><div class="table-wrap"><table><thead><tr><th>周期 · ${data.days} 天</th><th>已知成本 · USD</th><th>已记录 token</th></tr></thead><tbody>${period("最近完整周期", data.current)}${period("前一周期", data.previous)}</tbody></table></div><p class="scope-note">— 表示没有已知金额；$0.00 表示已知零成本。金额未知的记录未计入差额；数据源的新鲜度与采集缺口见采集状态。</p>`;
  }

  // Project identifiers share a long host-and-owner prefix, so truncating from
  // the left kept "hub.com/" and dropped the part that tells them apart. Keep
  // the trailing segments; the full path stays available in the tooltip.
  function projectLabel(value) {
    const parts = String(value || "").split("/").filter(Boolean);
    const tail = parts.slice(-2).join("/");
    return tail.length > 34 ? tail.slice(0, 33) + "…" : tail;
  }

  function legacyCostHistory(rows) {
    return {
      columns: ["claude-code", "codex"],
      rows: rows.map((row) => ({
        x: row.date,
        values: {
          "claude-code": row.claude_cost,
          codex: row.codex_cost,
        },
      })),
    };
  }

  function agentLabel(column) {
    if (column === "claude-code") {
      return "Claude Code";
    }
    if (column === "codex") {
      return "Codex";
    }
    return column || "value";
  }

  function rangeLabel(value) {
    const labels = {
      "7d": "近 7 天",
      "30d": "近 30 天",
      "90d": "近 90 天",
      "6m": "近半年",
      "1y": "近 1 年",
      "2y": "近 2 年",
      all: "全部历史",
    };
    return labels[value] || value || "近 30 天";
  }

  function rangeSummaryLabel(value) {
    const labels = {
      "7d": "近 7 天",
      "30d": "近 30 天",
      "90d": "近 90 天",
      "6m": "近半年",
      "1y": "近 1 年",
      "2y": "近 2 年",
      all: "全部历史",
    };
    return labels[value] || rangeLabel(value);
  }

  function granularityLabel(value) {
    return { day: "按天", week: "按周", month: "按月" }[value] || value;
  }

  // The card's own label carries the window, so the figure stays readable
  // without glancing back at the toolbar to see which range is selected.
  // A payload without `range` predates this field; the card then says so rather
  // than showing a stale or zeroed figure under a live-looking heading.
  function renderRangeCost(range, selectedRange, coverage) {
    const labelEl = qs("#range-label");
    const costEl = qs("#range-cost");
    const contextEl = qs("#range-context");
    if (!labelEl || !costEl || !contextEl) {
      return;
    }
    labelEl.textContent = `${rangeSummaryLabel(selectedRange)}成本`;
    if (!range) {
      costEl.textContent = "—";
      contextEl.hidden = false;
      contextEl.textContent = "此服务未提供";
      return;
    }
    costEl.textContent = money(range.cost_usd);
    // The heading names a window the data may not actually span: this host's
    // rollup starts at a collection date, so "All-history cost" over four months
    // of history is a bigger claim than the number supports. The Cost-over-time
    // panel already discloses the same fact, but it sits a screen below the
    // card — the qualifier has to travel with the figure that makes the claim.
    //
    // Three outcomes, and the test is what coverage actually *told* us, not
    // whether the object arrived. `_rollup_coverage` returns a full dict even
    // when it knows nothing: with an empty rollup DB `earliest_rollup_date()`
    // is null, so it reports `partial_before_range: false, earliest_date: null`
    // — indistinguishable, to a truthy check on the object, from a positive
    // "this window is covered". Keying on the object would then print
    // "All-history cost $0.00 / 0 tokens" unqualified on a host that has simply
    // never rolled up, while B4's panel note stays hidden for the same reason.
    // "We have no data" and "you spent nothing" are the two readings that must
    // never share a rendering.
    const known =
      coverage && typeof coverage.partial_before_range === "boolean" && coverage.earliest_date;
    if (!known) {
      contextEl.hidden = false;
      contextEl.textContent = "覆盖范围未知";
    } else if (coverage.partial_before_range) {
      contextEl.hidden = false;
      contextEl.textContent = `自 ${coverage.earliest_date} 起`;
    } else {
      contextEl.hidden = true;
      contextEl.textContent = "";
    }
  }

  function resetText(epoch, nowMs = Date.now()) {
    if (!epoch) {
      return "—";
    }
    const resetAt = new Date(epoch * 1000);
    if (resetAt.getTime() <= nowMs) {
      return "已于 " + fmtAbs(resetAt) + " 重置";
    }
    return "将于 " + fmtAbs(resetAt) + " 重置";
  }

  // Provider identity reuses the neutral .pill treatment from session tables.
  // Fixed columns keep the same quota windows aligned across providers.
  const QUOTA_WINDOW_7D = { key: "7d", used: "seven_day_used_pct", reset: "seven_day_resets_at" };
  const QUOTA_WINDOW_5H = { key: "5h", used: "five_hour_used_pct", reset: "five_hour_resets_at" };
  // Order here IS the column order on the page and must match the header row in
  // index.html. The shorter window comes first: the 5h figure is the one that
  // decides whether work can continue in the next few minutes, so it is read
  // first and belongs closest to the account it belongs to.
  const QUOTA_COLUMNS = [QUOTA_WINDOW_5H, QUOTA_WINDOW_7D];
  const QUOTA_PROVIDERS = [
    {
      key: "claude",
      label: "Claude",
      pill: "agent-claude-code",
      // Same order as QUOTA_COLUMNS. Placement does not read this list — the
      // column a value lands in comes from its own spec — but the collapsed
      // group's "worst usage" pill breaks an exact tie by taking the first
      // window here, and that tie should break the way the columns read.
      windows: [QUOTA_WINDOW_5H, QUOTA_WINDOW_7D],
    },
    { key: "codex", label: "Codex", pill: "agent-codex", windows: [QUOTA_WINDOW_5H, QUOTA_WINDOW_7D] },
  ];
  let renderedQuotaRateLimits = null;
  let codexQuotaReadings = [];
  const quotaFilter = { provider: "all", search: "", window: "five_hour", sort: "default" };
  let boundQuotaExplorer = null;

  function latestCodexQuotaReading(account) {
    return [account.operation?.after, account.operation?.before, account.batch_result?.after, account.batch_result?.before, account.last_quota]
      .filter((reading) => Number.isFinite(Date.parse(reading?.observed_at)))
      .sort((a, b) => Date.parse(b.observed_at) - Date.parse(a.observed_at))[0];
  }

  function updateCodexQuotaReadings(accounts) {
    const signature = (profiles) => JSON.stringify(profiles.map((account) => [
      account.account_id, account.email.toLowerCase(), latestCodexQuotaReading(account),
    ]).sort((a, b) => JSON.stringify(a).localeCompare(JSON.stringify(b))));
    const changed = signature(accounts) !== signature(codexQuotaReadings);
    codexQuotaReadings = accounts;
    if (changed && renderedQuotaRateLimits) renderQuotaAccounts(renderedQuotaRateLimits);
  }

  function quotaDisplayReading(provider, account) {
    if (provider.key !== "codex" || account.account_state !== "known" || !account.account_label) return account;
    const profile = codexQuotaReadings.find((item) => item.account_id === account.account_id
      && item.email.toLowerCase() === account.account_label.toLowerCase());
    const reading = profile && latestCodexQuotaReading(profile);
    if (!reading || Date.parse(reading.observed_at) <= Date.parse(account.updated_at)) return account;
    const display = { ...account, updated_at: reading.observed_at, quota_source: "account_action" };
    for (const spec of QUOTA_COLUMNS) {
      for (const key of [spec.used, spec.reset]) {
        display[key] = typeof reading[key] === "number" && Number.isFinite(reading[key]) ? reading[key] : null;
      }
    }
    return display;
  }

  function sortQuotaAccounts(provider, accounts, now = Date.now() / 1000) {
    if (quotaFilter.sort === "default") return accounts.slice();
    const readingValue = (account) => {
      const reading = quotaDisplayReading(provider, account);
      if (quotaFilter.sort === "observed") {
        const observed = Date.parse(reading.updated_at);
        return Number.isFinite(observed) ? -observed : null;
      }
      const remaining = ["remaining", "remaining_desc"].includes(quotaFilter.sort);
      const key = quotaFilter.window + (remaining ? "_used_pct" : "_resets_at");
      const value = reading[key];
      if (typeof value !== "number" || !Number.isFinite(value)) return null;
      if (remaining) {
        if (!quotaUsage(value).known) return null;
        return quotaFilter.sort === "remaining_desc" ? value - 100 : 100 - value;
      }
      return value > now ? value : null;
    };
    return accounts.map((account, index) => ({ account, index, value: readingValue(account) }))
      .sort((a,b) => a.value === null ? (b.value === null ? a.index-b.index : 1)
        : b.value === null ? -1 : a.value-b.value || a.index-b.index).map(item => item.account);
  }

  function quotaMatches(provider, account) {
    if (quotaFilter.provider !== "all" && quotaFilter.provider !== provider.key) return false;
    const terms = quotaFilter.search.trim().toLowerCase().split(/\s+/).filter(Boolean);
    const text = [account.account_label, account.account_id, quotaAccountName(account),
      account.account_plan, quotaPlanLabel(account.account_plan),
      account.reading_plan, account.credential_plan, ...(account.machines || [])]
      .filter(Boolean).join(" ").toLowerCase();
    return terms.every((term) => text.includes(term));
  }

  function bindQuotaExplorer() {
    const explorer = qs("#quota-explorer");
    if (!explorer || boundQuotaExplorer === explorer) return;
    boundQuotaExplorer = explorer;
    const search = qs("#quota-search");
    const buttons = explorer.querySelectorAll("[data-quota-provider]");
    search.value = quotaFilter.search;
    ["window", "sort"].forEach((key) => {
      const select = qs("#quota-" + key);
      if (!select) return;
      select.value = quotaFilter[key];
      select.addEventListener("change", () => {
        quotaFilter[key] = select.value;
        renderQuotaAccounts(renderedQuotaRateLimits);
      });
    });
    buttons.forEach((button) => button.setAttribute("aria-pressed",
      String(button.dataset.quotaProvider === quotaFilter.provider)));
    const refresh = () => {
      buttons.forEach((button) => button.setAttribute("aria-pressed",
        String(button.dataset.quotaProvider === quotaFilter.provider)));
      renderQuotaAccounts(renderedQuotaRateLimits);
    };
    buttons.forEach((button) => button.addEventListener("click", () => {
      quotaFilter.provider = button.dataset.quotaProvider;
      refresh();
    }));
    search.addEventListener("input", () => {
      quotaFilter.search = search.value;
      refresh();
    });
    qs("#quota-clear").addEventListener("click", () => {
      quotaFilter.provider = "all";
      quotaFilter.search = "";
      quotaFilter.window = "five_hour";
      quotaFilter.sort = "default";
      ["window", "sort"].forEach(key => { const el = qs("#quota-" + key); if (el) el.value = quotaFilter[key]; });
      search.value = "";
      refresh();
    });
  }

  function quotaPresence(account) {
    // Older API payloads have no presence field and contain only live data, so
    // retain the prior rendering instead of matching nothing and emptying the
    // table.
    return account.presence === undefined ? "in_use" : account.presence;
  }

  function renderQuotaAccounts(rateLimits, options = {}) {
    const table = qs("#quota-accounts");
    const reset = qs("#quota-clear");
    if (reset) reset.hidden = quotaFilter.provider === "all" && !quotaFilter.search
      && quotaFilter.window === "five_hour" && quotaFilter.sort === "default";
    if (!table) {
      return;
    }
    renderedQuotaRateLimits = rateLimits;
    bindQuotaExplorer();
    const rememberedExpanded = options.rememberedExpanded ?? Boolean(qs("#quota-past-accounts")
      && !qs("#quota-past-accounts").hidden);
    const focusedObservation = document.activeElement?.closest?.(".quota-observation-details")?.dataset.observationKey;
    const openObservations = new Set(Array.from(table.querySelectorAll?.(".quota-observation-details[open]") || [])
      .filter((node) => node.tagName === "DETAILS").map((node) => node.dataset.observationKey));
    // Only the bodies are ours; the header row is in the markup.
    Array.from(table.tBodies).forEach((body) => body.remove());

    const remembered = [];
    const allEntries = QUOTA_PROVIDERS.flatMap((provider) =>
      (rateLimits?.[provider.key]?.accounts || []).filter((account) => account.account_state === "known")
        .map((account) => ({ provider, account })));
    const matches = allEntries.filter(({ provider, account }) => quotaMatches(provider, account));
    const count = qs("#quota-match-count");
    if (count) {
      const known = matches.filter(({ account }) => account.account_state === "known");
      const current = known.filter(({ account }) => quotaPresence(account) === "in_use").length;
      const history = known.filter(({ account }) => quotaPresence(account) === "remembered").length;
      count.textContent = `匹配 ${matches.length} / ${allEntries.length} 个账号 · 机器关联 ${current} · 历史账号 ${history}`;
    }
    QUOTA_PROVIDERS.forEach((provider) => {
      const block = rateLimits?.[provider.key];
      const sourceAccounts = block?.accounts || [];
      if (!sourceAccounts.length) {
        table.appendChild(quotaUnavailableBody(provider, block?.refresh_errors?.length
          ? "暂未取得配额读数，可点击刷新重试；原因见采集详情。" : block?.unavailable_reason));
        return;
      }
      if (quotaFilter.provider !== "all" && quotaFilter.provider !== provider.key) return;
      if (!sourceAccounts.some((account) => account.account_state === "known")) {
        table.appendChild(quotaUnavailableBody(provider,
          "机器观测见“采集与诊断”，不能据此选择可用账号。", "无已识别账号"));
        return;
      }
      const accounts = sortQuotaAccounts(provider, sourceAccounts.filter((account) => account.account_state === "known" && quotaMatches(provider, account)));
      const live = accounts.filter((account) => quotaPresence(account) === "in_use");
      const named = live.filter((account) => account.account_state === "known");
      if (named.length) {
        table.appendChild(quotaBody(provider, named));
      }
      accounts
        .filter((account) => quotaPresence(account) === "remembered")
        .forEach((account) => remembered.push({ provider, account }));
    });

    renderQuotaMachineRecords(rateLimits);
    appendRememberedAccounts(table, remembered, rememberedExpanded);
    if (!matches.length && (quotaFilter.provider !== "all" || quotaFilter.search.trim())) {
      const body = document.createElement("tbody");
      body.className = "quota-filter-empty";
      const cell = document.createElement("td");
      cell.colSpan = 7;
      cell.textContent = "没有匹配的配额记录。可清空筛选查看全部；这不表示配额可用或已用尽。";
      const row = document.createElement("tr");
      row.appendChild(cell);
      body.appendChild(row);
      table.appendChild(body);
    }
    QUOTA_PROVIDERS.forEach((provider) => {
      const failures = rateLimits?.[provider.key]?.refresh_errors || [];
      if (failures.length) table.appendChild(quotaFailureDetails(provider, failures));
    });
    Array.from(table.querySelectorAll?.(".quota-observation-details") || []).forEach((node) => {
      if (node.tagName === "DETAILS") node.open = openObservations.has(node.dataset.observationKey);
      if (focusedObservation && node.dataset.observationKey === focusedObservation) node.querySelector?.("summary")?.focus();
    });
    renderQuotaSignedOut(rateLimits);
    updateSyncOverviewLink();
  }

  function quotaFailureKind(reason) {
    const text = String(reason || "");
    if (text.startsWith("本轮前序机器查询异常；本机尚未尝试")) return "skipped";
    if (text.startsWith("本机已暂停该服务的自动请求")) return "paused";
    if (text.startsWith("同一服务已有操作，本次未发出请求。")) return "busy";
    return "error";
  }

  function quotaFailureDetails(provider, failures) {
    const body = document.createElement("tbody");
    const row = document.createElement("tr");
    const cell = document.createElement("td");
    cell.colSpan = 7;
    const details = document.createElement("details");
    details.className = "note-disclosure";
    const summary = document.createElement("summary");
    const counts = { paused: 0, skipped: 0, busy: 0, error: 0 };
    failures.forEach(({ reason }) => {
      counts[quotaFailureKind(reason)] += 1;
    });
    const labels = [["paused", "暂停"], ["skipped", "本轮未尝试"],
      ["busy", "通道占用未查询"], ["error", "查询异常"]];
    const status = labels.filter(([key]) => counts[key]).map(([key, label]) => `${counts[key]} 台${label}`).join("，");
    summary.textContent = `${provider.label} 采集详情（${status}）`;
    details.appendChild(summary);
    failures.forEach((failure) => {
      const note = document.createElement("p");
      note.textContent = `${failure.machine}：${failure.reason}`;
      details.appendChild(note);
    });
    cell.appendChild(details);
    row.appendChild(cell);
    body.appendChild(row);
    return body;
  }

  function quotaRefreshNotice(provider, account) {
    if (account.quota_status_reason) return account.quota_status_reason;
    const block = renderedQuotaRateLimits?.[provider.key];
    const failed = new Set((block?.refresh_errors || []).map((failure) => failure.machine));
    if (quotaPresence(account) !== "in_use" || !(account.machines || []).some((name) => failed.has(name))) {
      return null;
    }
    const hasValue = provider.windows.some((spec) => quotaUsage(account[spec.used]).known);
    const sourceHealthy = account.reading_from && !failed.has(account.reading_from)
      && !(block?.signed_out_machines || []).includes(account.reading_from);
    const fresh = Number.isFinite(Date.parse(account.updated_at)) && !quotaIsStale(account.updated_at);
    if (sourceHealthy && fresh && hasValue) return null;
    const kinds = new Set((block?.refresh_errors || [])
      .filter(({ machine }) => (account.machines || []).includes(machine))
      .map(({ reason }) => quotaFailureKind(reason)));
    const state = kinds.has("paused") ? "查询已暂停" : kinds.has("skipped") ? "上次查询未执行"
      : kinds.has("busy") ? "查询通道占用" : null;
    if (state) {
      return `${hasValue ? "更新延迟，保留上次读数" : "暂不可用"}；${state}，请展开“${provider.label} 采集详情”核对原因`;
    }
    return hasValue ? "更新延迟，保留上次读数；可点击刷新重试" : "暂不可用，可点击刷新重试";
  }

  // Stated, not warned about. A machine that is not signed in to a provider has
  // nothing to fix, but its absence from the figures above is still a fact the
  // reader needs — without this line, "node-c is not in the Claude row" and
  // "node-c's Claude number failed to arrive" look identical.
  //
  // It lives outside the table on purpose: a row would put a machine with no
  // account in the same visual class as accounts that have quota, and the
  // columns it would have to leave empty are the reason to read the table.
  function renderQuotaSignedOut(rateLimits, includeAbsent = false) {
    const note = qs("#quota-signed-out");
    if (!note) {
      return;
    }
    // Two different facts, and one sentence cannot carry both. A machine that
    // never signed in contributes nothing, so "not counted above" is true of
    // it. A machine whose sign-in lapsed usually still has a row up there,
    // holding the last reading it managed to take — saying that one is "not
    // counted above" contradicts the table directly above the sentence.
    //
    // The discriminator is `reading_from`, not membership of `machines`. One
    // account is commonly signed in on several machines and its row shows the
    // freshest of them, so a lapsed machine that is merely *listed* on a row
    // whose figure came from a different, still-working machine is not frozen
    // at all — the number beside the sentence is 20 minutes old and advancing.
    // Anything with no row of its own contributes nothing and reads as absent.
    const absent = [];
    const frozen = [];
    QUOTA_PROVIDERS.forEach((provider) => {
      const block = rateLimits?.[provider.key];
      const showing = new Set(
        (block?.accounts || []).filter((account) => account.account_state === "known")
          .map((account) => account.reading_from).filter(Boolean),
      );
      (block?.signed_out_machines || []).forEach((machine) => {
        (showing.has(machine) ? frozen : absent).push(`${machine} 的 ${provider.label}`);
      });
    });

    const sentences = [];
    if (includeAbsent && absent.length) {
      sentences.push(`未登录，未提供账号配额 —— ${absent.join("、")}。`);
    }
    if (frozen.length) {
      sentences.push(
        `来源机器未登录：${frozen.join("、")}。`,
      );
    }
    note.dataset.tone = frozen.length ? "warning" : "neutral";
    note.textContent = sentences.join(" ");
    note.hidden = !sentences.length;
  }

  function appendRememberedAccounts(table, entries, expanded) {
    if (!entries.length) {
      return;
    }
    const summaryBody = document.createElement("tbody");
    summaryBody.className = "quota-past-summary";
    const summaryRow = document.createElement("tr");
    summaryRow.className = "quota-row quota-summary";
    const summaryCell = document.createElement("td");
    summaryCell.colSpan = 7;
    const toggle = document.createElement("button");
    toggle.type = "button";
    toggle.className = "quota-toggle quota-past-toggle";
    toggle.setAttribute("aria-expanded", String(expanded));
    toggle.setAttribute("aria-controls", "quota-past-accounts");
    toggle.textContent = `历史账号（${entries.length}）`;
    summaryCell.appendChild(toggle);
    summaryRow.appendChild(summaryCell);
    summaryBody.appendChild(summaryRow);

    const historyBody = document.createElement("tbody");
    historyBody.id = "quota-past-accounts";
    historyBody.className = "quota-history";
    historyBody.hidden = !expanded;
    entries.forEach(({ provider, account }) => {
      historyBody.appendChild(quotaAccountRow(provider, account));
    });
    toggle.addEventListener("click", () => {
      const open = historyBody.hidden;
      historyBody.hidden = !open;
      toggle.setAttribute("aria-expanded", String(open));
      updateSyncOverviewLink();
    });
    table.appendChild(summaryBody);
    table.appendChild(historyBody);
  }

  function quotaBody(provider, accounts) {
    const body = document.createElement("tbody");
    accounts.forEach((account) => body.appendChild(quotaAccountRow(provider, account)));
    return body;
  }

  function quotaAccountRow(provider, account) {
    const row = document.createElement("tr");
    row.className = "quota-row";
    row.dataset.presence = quotaPresence(account);
    if (quotaPresence(account) === "remembered") {
      row.classList.add("remembered");
    }
    if (account.account_state === "known" && typeof account.account_id === "string" && account.account_id) {
      row.dataset.provider = provider.key;
      row.dataset.accountId = account.account_id;
      row.dataset.accountLabel = JSON.stringify(account.account_label ?? null);
      const recordTime = account.last_observation?.recorded_at || account.updated_at;
      if (typeof recordTime === "string" && recordTime) {
        row.dataset.observedAt = recordTime;
      }
    }
    if (account.account_state !== "known") {
      row.classList.add("unattributed");
    }
    const display = quotaDisplayReading(provider, account);
    const stale = quotaIsStale(display.updated_at);
    if (stale) {
      // Keep the machine-readable age state; observation age is visible in
      // the update cell without repeating a stale warning on each row.
      row.classList.add("stale");
    }

    row.appendChild(quotaAccountCell(provider, account));
    row.appendChild(quotaProviderCell(provider));
    row.appendChild(quotaPlanCell(account));
    QUOTA_COLUMNS.forEach((spec) => row.appendChild(quotaWindowCell(provider, spec, display)));
    row.appendChild(quotaMachinesCell(account, display));
    row.appendChild(quotaUpdatedCell(provider, display, stale, display.quota_source === "account_action"
      ? null : quotaRefreshNotice(provider, account)));
    Array.from(row.children).forEach((cell, index) => {
      cell.dataset.label = ["账号", "服务商", "套餐", "5 小时已用", "7 天已用", "机器", "配额更新"][index];
    });
    return row;
  }

  function quotaProviderCell(provider) {
    const cell = document.createElement("td");
    const wrap = document.createElement("div");
    wrap.className = "quota-provider-cell";
    wrap.appendChild(quotaProviderPill(provider));
    cell.appendChild(wrap);
    return cell;
  }

  function quotaPlanCell(account) {
    const cell = document.createElement("td");
    if (account.quota_status === "no_subscription") {
      cell.textContent = "无有效订阅";
      return cell;
    }
    if (account.plan_verification === "unverified") {
      cell.textContent = "套餐未核实";
      return cell;
    }
    if (!account.account_plan) {
      cell.textContent = "—";
      return cell;
    }
    const plan = document.createElement("span");
    plan.className = "status-pill identity";
    plan.textContent = quotaPlanLabel(account.account_plan);
    cell.appendChild(plan);
    if (quotaPlanSourcesDisagree(account)) {
      // The whole claim in visible text, not in a title. A hover is not a
      // channel on touch or from the keyboard, and the reader who needs this
      // is precisely the one who would otherwise read the row as a single
      // observation — so the words "不一致" and both sources are in the cell.
      const mismatch = document.createElement("span");
      mismatch.className = "quota-plan-mismatch";
      mismatch.textContent =
        `plan 不一致 · 配额读数 ${quotaPlanLabel(account.reading_plan)}` +
        ` / 机器凭据 ${quotaPlanLabel(account.credential_plan)}`;
      mismatch.title = "两者取自不同来源，无法判断哪一个更旧。";
      cell.appendChild(mismatch);
    }
    return cell;
  }

  // Whether the row's two plan facts are both present and disagree.
  //
  // Three states, not two, and the third one is why this reads the two raw
  // fields rather than comparing `account_plan` against the credential: a
  // reading that carries no plan falls back to the credential one, which would
  // make a lone source compare equal to itself and be indistinguishable from
  // two sources that agree. Signed out, an unreadable credential file and an
  // API-key machine leave one source; a remembered row and an exporter older
  // than this field leave none. Fewer than two either way, so nothing is
  // compared.
  //
  // The page stays silent for it, as it does for agreement — but silence here
  // is the absence of a claim, not a claim of agreement, and the two states
  // remain distinguishable in the payload for anyone who needs them apart.
  // See ADR 20260822-586a.
  function quotaPlanSourcesDisagree(account) {
    if (account.account_state !== "known") {
      return false;
    }
    const reading = account.reading_plan;
    const credential = account.credential_plan;
    if (typeof reading !== "string" || !reading) {
      return false;
    }
    if (typeof credential !== "string" || !credential) {
      return false;
    }
    return reading !== credential;
  }

  function quotaAccountCell(provider, account) {
    const cell = document.createElement("td");
    cell.className = "quota-identity";
    const wrap = document.createElement("div");
    wrap.className = "quota-account-cell";

    const name = document.createElement("span");
    name.className = "quota-account-label";
    name.textContent = account.account_state === "known" ? quotaAccountName(account)
      : account.account_state === "signed_out" ? "未登录" : "账号未知";
    wrap.appendChild(name);
    if (account.account_state !== "known" && account.account_state !== "signed_out") {
      const note = document.createElement("span");
      note.className = "quota-history-note";
      note.textContent = "请更新对应机器上的 agent-monitor";
      wrap.appendChild(note);
    }
    const actions = document.createElement("div");
    actions.className = "quota-account-links";

    if (quotaPresence(account) === "remembered") {
      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "quota-account-remove";
      remove.textContent = "移除";
      remove.setAttribute("aria-label", `移除 ${quotaAccountName(account)} 的记录`);
      remove.addEventListener("click", () => removeRememberedAccount(remove, provider, account));
      actions.appendChild(remove);
    }
    if (actions.children.length) wrap.appendChild(actions);
    cell.appendChild(wrap);
    return cell;
  }

  function quotaRemovalReading(account) {
    account = account.last_observation || account;
    const sevenDay = quotaUsage(account.seven_day_used_pct);
    if (sevenDay.known) {
      return `7 天已用 ${sevenDay.used}%`;
    }
    const fiveHour = quotaUsage(account.five_hour_used_pct);
    if (fiveHour.known) {
      return `5 小时已用 ${fiveHour.used}%`;
    }
    return "最后读数不可用";
  }

  function removeRenderedAccount(providerKey, accountId, observedAt, accountLabel = null) {
    const table = qs("#quota-accounts");
    if (!table) {
      return;
    }
    const matchingRows = [];
    Array.from(table.children).forEach((section) => {
      Array.from(section.children).forEach((row) => {
        if (
          row.dataset.provider === providerKey &&
          row.dataset.accountId === accountId &&
          (providerKey !== "codex" || row.dataset.accountLabel === JSON.stringify(accountLabel)) &&
          row.dataset.presence === "remembered" &&
          row.dataset.observedAt === observedAt
        ) {
          matchingRows.push(row);
        }
      });
    });
    const providerBlock = renderedQuotaRateLimits?.[providerKey];
    const accounts = providerBlock?.accounts;
    if (Array.isArray(accounts)) {
      const remaining = accounts.filter(
        (account) => !(
          quotaPresence(account) === "remembered" &&
          account.account_id === accountId &&
          (providerKey !== "codex" || (account.account_label ?? null) === accountLabel) &&
          (account.last_observation?.recorded_at || account.updated_at) === observedAt
        ),
      );
      if (remaining.length !== accounts.length) {
        const historyBody = Array.from(table.tBodies).find(
          (body) => body.className === "quota-history",
        );
        renderQuotaAccounts({
          ...renderedQuotaRateLimits,
          [providerKey]: { ...providerBlock, accounts: remaining },
        }, { rememberedExpanded: Boolean(historyBody && !historyBody.hidden) });
        return;
      }
    }
    if (!matchingRows.length) return;
    matchingRows.forEach((row) => row.remove());
    refreshRememberedAccountSummary(table);
  }

  function refreshRememberedAccountSummary(table) {
    const bodies = Array.from(table.tBodies);
    const historyBody = bodies.find((body) => body.className === "quota-history");
    const summaryBody = bodies.find((body) => body.className === "quota-past-summary");
    if (!historyBody || !summaryBody) {
      return;
    }
    const count = Array.from(historyBody.children).filter(
      (row) => row.dataset.presence === "remembered",
    ).length;
    if (!count) {
      historyBody.remove();
      summaryBody.remove();
      return;
    }
    const row = summaryBody.children[0];
    const cell = row && row.children[0];
    const toggle = cell && cell.children[0];
    if (toggle) {
      toggle.textContent = `历史账号（${count}）`;
    }
  }

  async function removeRememberedAccount(button, provider, account) {
    const accountName = quotaAccountName(account);
    const recordTime = account.last_observation?.recorded_at || account.updated_at;
    const observedAt = formatDate(recordTime);
    const confirmed = window.confirm(
      `移除 ${accountName} 的记录？\n` +
        `保存的记录时间 ${observedAt}，${quotaRemovalReading(account)}。\n` +
        "此操作不可恢复。"
    );
    if (!confirmed) {
      return;
    }
    button.disabled = true;
    try {
      const response = await fetch(new URL("/api/account-memory/remove", window.location.origin), {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Agent-Monitor": "action" },
        body: JSON.stringify({
          provider: provider.key,
          account_id: account.account_id,
          account_label: account.account_label ?? null,
          observed_at: recordTime,
        }),
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        window.alert(payload.error || `移除失败（HTTP ${response.status}）`);
        button.disabled = false;
        return;
      }
      removeRenderedAccount(provider.key, account.account_id, recordTime, account.account_label ?? null);
    } catch (error) {
      window.alert(`移除失败：${error && error.message ? error.message : error}`);
      button.disabled = false;
    }
  }

  // Usage bands. High usage means little headroom, so these decide when the page
  // stops being neutral about the number under it. Colour never carries this
  // alone — a low-quota row also says so in words, for readers who cannot see
  // the difference.
  const QUOTA_BAND_LOW = 25;
  const QUOTA_BAND_CRITICAL = 10;

  // One definition, used by the row and by the collapsed group's summary. Two
  // copies would let a machine be styled critical in one place and unflagged in
  // the other, which is exactly the case the collapse creates.
  function quotaUsage(usedPct) {
    // Out of range is unknown, not clamped. These come from another machine's
    // block, which the schema only checks is an object — clamping 105 would
    // render "0%" under an "almost out" pill and raise an alarm out of a bad
    // value.
    const known =
      typeof usedPct === "number" && Number.isFinite(usedPct) && usedPct >= 0 && usedPct <= 100;
    if (!known) {
      return { known: false, used: null, band: "" };
    }
    // Banded on the number the reader sees, not the one behind it: values that
    // both print "90%" must not carry different words.
    const used = Math.round(usedPct);
    const band = used > 100 - QUOTA_BAND_CRITICAL ? "critical" : used > 100 - QUOTA_BAND_LOW ? "low" : "";
    return { known: true, used, band };
  }

  function quotaBandPill(band, text) {
    const pill = document.createElement("span");
    pill.className = `status-pill ${band === "critical" ? "bad" : "warn"}`;
    pill.textContent = text;
    return pill;
  }

  function quotaWindowCell(provider, spec, account) {
    const cell = document.createElement("td");
    cell.className = "numeric quota-window-cell";
    if (account.quota_status === "no_subscription") {
      cell.textContent = "不适用";
      return cell;
    }

    // Matched by key, not object identity: identity holds only while every
    // provider's `windows` points at these same literals, and the day one is
    // built from copies this branch would stamp "reports no 5h window" over a
    // perfectly good reading — it fails toward a confident false statement.
    if (!provider.windows.some((window) => window.key === spec.key)) {
      // "n/a", not the em dash used for a missing reading. This provider has no
      // such window at all, which is a different fact from "we have no number",
      // and one glyph in two weights does not carry that difference — not for a
      // screen reader, and not for anyone who cannot hover a tooltip.
      cell.classList.add("not-applicable");
      cell.textContent = "不适用";
      const reason = document.createElement("span");
      reason.className = "quota-window-reason";
      reason.textContent = `${provider.label} 没有 ${spec.key} 窗口`;
      cell.appendChild(reason);
      return cell;
    }

    const { known, used, band } = quotaUsage(account[spec.used]);
    const renderedAtMs = Date.now();
    const resetEpoch = account[spec.reset];
    const resetState = quotaResetState(resetEpoch, renderedAtMs);
    if (!known && resetState === "unknown") {
      const missing = document.createElement("span");
      missing.className = "quota-window-reason";
      missing.textContent = "用量与重置时间未知";
      cell.appendChild(missing);
      return cell;
    }
    if (band) {
      cell.classList.add(band);
    }

    const line = document.createElement("div");
    line.className = "quota-window-line";

    const usage = document.createElement("div");
    usage.className = "quota-window-usage";

    const amount = document.createElement("span");
    amount.className = "quota-window-value";
    amount.textContent = known ? `${used}%` : "—";
    if (known) {
      amount.title = `本窗口已用 ${used}%`;
    }
    usage.appendChild(amount);

    if (known) {
      usage.appendChild(quotaMeter(used));
    }
    line.appendChild(usage);
    cell.appendChild(line);

    const reset = document.createElement("div");
    reset.className = "quota-window-reset";
    reset.textContent = quotaPresence(account) === "remembered" && resetState === "unknown"
      ? "重置时间未知"
      : quotaResetText(resetEpoch, resetState, renderedAtMs);
    if (resetState !== "unknown") {
      reset.title = resetText(resetEpoch, renderedAtMs);
    }
    line.appendChild(reset);
    return cell;
  }

  // Fills with what is used — the column reads "7d used", and a bar running the
  // other way would pair a long bar with a small number. Both encode usage, so
  // a nearly full bar means little quota remains.
  function quotaMeter(usedPct) {
    const track = document.createElement("span");
    track.className = "quota-meter";
    // Presentational: the adjacent text already states the value, and a second
    // announcement of the same number is noise on a screen reader.
    track.setAttribute("aria-hidden", "true");
    const fill = document.createElement("span");
    fill.className = "quota-meter-fill";
    fill.style.setProperty("--quota-fill", String(usedPct / 100));
    track.appendChild(fill);
    return track;
  }

  // A reset time is read to answer "how long must I wait", so it says exactly
  // that — a duration, at every scale. A clock time would need the reader to
  // subtract, and is ambiguous the moment the reset is not today: "resets 11:32"
  // on one that is 18 hours out reads as this morning, already past. The full
  // timestamp stays on the title attribute for anyone who wants the wall clock.
  function quotaResetText(epoch, resetState, nowMs) {
    if (resetState === "unknown") {
      return "重置时间未知";
    }
    if (resetState === "passed") {
      return "上次重置时间已过";
    }
    const deltaMs = epoch * 1000 - nowMs;
    // Each unit is chosen from the value it will actually print, so rounding
    // cannot carry a figure past its own unit — 59.6 minutes says "1h", not
    // "60m". Never "in 0m": the state closest to relief must not read empty.
    const minutes = Math.max(1, Math.round(deltaMs / 60000));
    if (minutes < 60) {
      return `${minutes} 分钟后重置`;
    }
    const hours = Math.round(minutes / 60);
    if (hours < 48) {
      return `${hours} 小时后重置`;
    }
    return `${Math.round(hours / 24)} 天后重置`;
  }

  function quotaResetState(epoch, nowMs) {
    if (typeof epoch !== "number" || !Number.isFinite(epoch)) {
      return "unknown";
    }
    return epoch * 1000 <= nowMs ? "passed" : "future";
  }

  function formatQuotaReset(epoch, nowMs = Date.now()) {
    return quotaResetText(epoch, quotaResetState(epoch, nowMs), nowMs);
  }

  function quotaMachinesCell(account, display = account) {
    const cell = document.createElement("td");
    const names = account.machines || [];
    const source = display.quota_source === "account_action" ? null : display.reading_from;
    const remembered = quotaPresence(account) === "remembered";
    if (!names.length) cell.textContent = "—";
    else if (remembered) cell.appendChild(document.createTextNode("历史关联："));
    names.forEach((name, index) => {
      if (index) cell.appendChild(document.createTextNode(" · "));
      const node = document.createElement("span");
      node.textContent = name;
      if (name === account.this_machine) {
        node.className = "quota-machine-self";
        node.appendChild(document.createTextNode("（本机）"));
      }
      if (names.length > 1 && name === source) {
        node.appendChild(document.createTextNode("（配额来源）"));
      }
      cell.appendChild(node);
    });
    if (source && !names.includes(source)) {
      cell.appendChild(document.createTextNode(` · 配额来源：${source}`));
    }
    return cell;
  }

  function quotaObservationAge(iso, nowMs = Date.now()) {
    const observed = typeof iso === "string" && iso ? Date.parse(iso) : NaN;
    if (!Number.isFinite(observed)) return "时间未知";
    const elapsed = nowMs - observed;
    if (elapsed < 0) return "时间超前";
    if (elapsed < 60000) return "刚刚";
    if (elapsed < 3600000) return `${Math.floor(elapsed / 60000)} 分钟前`;
    if (elapsed < 86400000) return `${Math.floor(elapsed / 3600000)} 小时前`;
    return `${Math.floor(elapsed / 86400000)} 天前`;
  }

  function quotaUpdatedCell(provider, account, stale, refreshNotice) {
    const cell = document.createElement("td");
    cell.className = "quota-updated-cell";
    const details = document.createElement("details");
    details.className = "quota-observation-details";
    details.dataset.observationKey = JSON.stringify([provider.key, account.account_id, account.account_label,
      account.account_state, account.account_state === "known" ? null : [...(account.machines || [])].sort()]);
    const summary = document.createElement("summary");
    const historicalOnly = account.quota_status && account.quota_status !== "current";
    summary.textContent = historicalOnly ? "历史配额与状态" : quotaObservationAge(account.updated_at);
    summary.setAttribute("aria-label", `配额更新：${summary.textContent}，查看时间与来源`);
    details.appendChild(summary);
    const exact = document.createElement("span");
    exact.className = "quota-history-note";
    const observationTime = account.updated_at || account.last_observation?.observed_at;
    const knownTime = typeof observationTime === "string" && observationTime && Number.isFinite(Date.parse(observationTime));
    exact.textContent = knownTime ? `${historicalOnly ? "历史观测" : "观测时间"}：${formatDate(observationTime)}` : "未取得观测时间";
    details.appendChild(exact);
    if (account.last_observation) {
      const history = account.last_observation;
      const retained = document.createElement("span");
      retained.className = "quota-history-note";
      const percent = (value) => quotaUsage(value).known ? `${value}%` : "未知";
      retained.textContent = `历史配额：5 小时已用 ${percent(history.five_hour_used_pct)}；7 天已用 ${percent(history.seven_day_used_pct)}。`
        + (history.observed_at ? `原始观测：${formatDate(history.observed_at)}。` : "原始观测时间未核实。")
        + (history.reading_from ? `来源机器：${history.reading_from}。` : "来源机器未记录。")
        + (history.account_plan ? `保存的套餐信息：${quotaPlanLabel(history.account_plan)}（不代表当前订阅）。` : "");
      details.appendChild(retained);
      if (account.subscription_confirmation && !account.subscription_confirmation.restored_at) {
        const confirmation = document.createElement("span");
        confirmation.className = "quota-history-note";
        confirmation.textContent = `用户确认无订阅：${formatDate(account.subscription_confirmation.confirmed_at)}。续订后需明确检查新的有效配额；各机器的本地暂停需分别核对，旧缓存不会恢复当前额度。`;
        details.appendChild(confirmation);
      }
    }
    for (const failure of account.historical_query_failures || []) {
      const diagnostic = document.createElement("span");
      diagnostic.className = "quota-history-note";
      diagnostic.textContent = `历史查询诊断（无订阅时的预期拒绝）：${failure.machine}：${failure.reason}`;
      details.appendChild(diagnostic);
    }
    if (quotaPresence(account) === "remembered") {
      const membership = document.createElement("span");
      membership.className = "quota-history-note";
      membership.textContent = "当前未在已纳入的机器上使用"
        + (provider.key === "codex" ? "；独立保存的登录状态见账号操作" : "");
      details.appendChild(membership);
    }
    cell.appendChild(details);
    const note = (text) => {
      const node = document.createElement("span");
      node.className = "quota-history-note";
      node.textContent = text;
      cell.appendChild(node);
    };
    if (account.quota_source === "account_action") {
      const context = document.createElement("span");
      context.className = "quota-history-note";
      context.textContent = "配额来自账号操作中的配额查询；机器归属与套餐来自机器采集";
      details.appendChild(context);
      return cell;
    }
    if (!account.reading_from) {
      const source = document.createElement("span");
      source.className = "quota-history-note";
      source.textContent = quotaPresence(account) === "remembered"
        ? "历史保存未保留来源机器" : "未取得读数来源机器";
      details.appendChild(source);
    }
    if (quotaPresence(account) !== "remembered") {
      const invalidSource = (renderedQuotaRateLimits?.[provider.key]?.signed_out_machines || []).includes(account.reading_from);
      if (invalidSource) {
        note("来源机器未登录");
        const recovery = document.createElement("span");
        recovery.className = "quota-history-note";
        recovery.textContent = `当前显示来自 ${account.reading_from} 的最后一次配额读数。该机器未登录 ${provider.label}；如需继续从此机器更新，请在该机器登录此账号，再点击“更新统计与配额”。`;
        details.appendChild(recovery);
      }
      if (refreshNotice) note(refreshNotice);
      if (stale && !invalidSource) note("可能早于一次登录变更");
    }

    return cell;
  }

  // Plan tiers arrive as the provider's own identifiers. Known ones get the name
  // the provider bills them under; anything unrecognised is shown as it came,
  // because inventing a label for a tier we do not know is worse than a raw one.
  const QUOTA_PLAN_LABELS = {
    default_claude_max_20x: "Max 20×",
    default_claude_max_5x: "Max 5×",
    default_claude_pro: "Pro",
    prolite: "Pro Lite",
    self_serve_business_prolite: "Business Pro Lite",
    pro: "Pro",
    plus: "Plus",
    team: "Team",
    enterprise: "Enterprise",
  };

  function quotaPlanLabel(plan) {
    return QUOTA_PLAN_LABELS[plan] || plan;
  }

  function quotaAccountName(account) {
    if (typeof account.account_label === "string" && account.account_label) {
      return account.account_label;
    }
    if (typeof account.account_id === "string" && account.account_id) {
      return `账号 ${account.account_id.slice(0, 8)}`;
    }
    const machines = (account.machines || []).join(" · ");
    // Two ways to have no account, with different remedies. Telling someone to
    // update a machine that is already current sends them after the wrong thing.
    if (account.account_state === "signed_out") {
      return machines ? `${machines} 未登录` : "未登录";
    }
    return machines ? `账号未知 —— 请更新 ${machines} 上的 agent-monitor` : "账号未知";
  }

  // Old enough that the reading may predate whatever the machine is signed into
  // now. Attribution assumes the latest reading belongs to the current account,
  // so age is the reader's only cue that the assumption is stretched.
  const QUOTA_STALE_MS = 6 * 60 * 60 * 1000;

  function quotaIsStale(updatedAt) {
    const observed = Date.parse(updatedAt);
    return Number.isFinite(observed) && Date.now() - observed > QUOTA_STALE_MS;
  }

  function quotaProviderPill(provider) {
    const pill = document.createElement("span");
    pill.className = `pill ${provider.pill}`;
    pill.textContent = provider.label;
    return pill;
  }

  function renderQuotaMachineRecords(rateLimits) {
    renderQuotaMachineNotice(rateLimits);
    const details = qs("#quota-machine-records");
    if (!details) return;
    const entries = QUOTA_PROVIDERS.flatMap((provider) =>
      (rateLimits?.[provider.key]?.accounts || []).filter((account) => account.account_state !== "known")
        .map((account) => ({ provider, account })));
    details.hidden = !entries.length;
    const signature = JSON.stringify(entries);
    if (details.dataset.readings === signature) return;
    details.dataset.readings = signature;
    const hadFocus = details.contains?.(document.activeElement);
    // Preserve the disclosure node and its open state across polling.
    details.textContent = "";
    if (!entries.length) return;
    const summary = document.createElement("summary");
    summary.textContent = `未归属账号的机器读数（${entries.length}）：${entries.map(({ provider, account }) => `${(account.machines || []).join("、") || account.reading_from || "机器未知"} · ${provider.label}（${account.account_state === "signed_out" ? "未登录" : "需更新 agent-monitor"}）`).join("；")}`;
    QUOTA_PROVIDERS.forEach((provider) => {
      const worst = quotaWorstUsage(provider, entries.filter((entry) => entry.provider === provider).map((entry) => entry.account));
      if (!worst) return;
      const band = worst.band === "critical" ? "即将用尽" : "余量偏低";
      summary.appendChild(quotaBandPill(worst.band,
        ` · ${provider.label} ${worst.machine} 观测：${worst.spec.key} 已用 ${worst.used}%（${band}；${worst.updatedAt ? formatDate(worst.updatedAt) : "观测时间未知"}）`));
    });
    details.appendChild(summary);
    const context = document.createElement("p");
    context.textContent = "以下为采集时读数，不计入账号比较和筛选。";
    details.appendChild(context);
    entries.forEach(({ provider, account }) => {
      const record = document.createElement("div");
      record.className = "quota-machine-record";
      const heading = document.createElement("strong");
      const machines = (account.machines || []).join("、") || account.reading_from || "机器未知";
      heading.textContent = `${machines} · ${provider.label} · ${account.account_state === "signed_out" ? "未登录" : "账号未识别"}`;
      record.appendChild(heading);
      const historical = account.last_observation || account;
      const observedAt = account.last_observation ? historical.observed_at : account.updated_at;
      const values = [observedAt ? `观测于 ${formatDate(observedAt)}` : "观测时间未知"];
      if (account.account_plan) values.push(`采集时套餐：${quotaPlanLabel(account.account_plan)}`);
      provider.windows.forEach((spec) => {
        const reading = quotaUsage(historical[spec.used]);
        const reset = historical[spec.reset];
        const resetDate = typeof reset === "number" && Number.isFinite(reset) && reset > 0
          ? new Date(reset * 1000) : null;
        const resetText = !resetDate ? "重置时间未知" : Number.isFinite(resetDate.getTime())
          ? `记录的重置时间 ${formatDate(resetDate.toISOString())}` : `重置时间无效（原值 ${reset}）`;
        values.push(`${spec.key}：${reading.known ? `已用 ${reading.used}%` : "用量未知"}；${resetText}`);
      });
      values.forEach((value) => {
        const fact = document.createElement("p");
        fact.textContent = value;
        record.appendChild(fact);
      });
      if (account.account_state === "unstamped") {
        const remedy = document.createElement("p");
        remedy.textContent = `请更新 ${machines} 上的 agent-monitor，以采集账号标记。`;
        record.appendChild(remedy);
      }
      details.appendChild(record);
    });
    if (hadFocus) summary.focus();
  }

  function renderQuotaMachineNotice(rateLimits) {
    const notice = qs("#quota-machine-notice");
    if (!notice) return;
    const entries = QUOTA_PROVIDERS.flatMap(provider => (rateLimits?.[provider.key]?.accounts || [])
      .filter(account => account.account_state !== "known").map(account => ({provider, account})));
    const warnings = QUOTA_PROVIDERS.map(provider => ({provider, worst: quotaWorstUsage(provider, entries.filter(entry => entry.provider === provider).map(entry => entry.account))})).filter(entry => entry.worst);
    notice.hidden = !warnings.length;
    const signature = JSON.stringify(entries) + currentPageUrl();
    if (notice.dataset.readings === signature) return;
    notice.dataset.readings = signature;
    notice.textContent = "";
    warnings.forEach(({provider, worst}) => {
      if (worst) notice.appendChild(quotaBandPill(worst.band,
        `${provider.label} · ${worst.machine} · ${worst.spec.key} 已用 ${worst.used}%（${worst.band === "critical" ? "即将用尽" : "余量偏低"}；机器观测 ${worst.updatedAt ? formatDate(worst.updatedAt) : "时间未知"}，不代表当前可用账号）`));
    });
    const link = document.createElement("a");
    link.textContent = "查看采集与诊断";
    link.href = collectionUrl();
    notice.appendChild(link);
  }

  function quotaWorstUsage(provider, accounts) {
    let worst = null;
    accounts.forEach((account) => {
      provider.windows.forEach((spec) => {
        const { known, used, band } = quotaUsage(account[spec.used]);
        if (!known || !band) {
          return;
        }
        if (!worst || used > worst.used) {
          // `spec` travels with the value: the caller places the pill in that
          // window's own column, and a figure under the wrong header is a
          // wrong figure.
          worst = { spec, used, band, updatedAt: account.updated_at, machine: (account.machines || []).join("、") || "未知" };
        }
      });
    });
    return worst;
  }

  function quotaUnavailableBody(provider, reason, statusLabel = "不可用") {
    const body = document.createElement("tbody");
    const row = document.createElement("tr");
    row.className = "quota-row unattributed";

    const head = document.createElement("td");
    head.colSpan = 3;
    const wrap = document.createElement("div");
    wrap.className = "quota-account-cell";
    wrap.appendChild(quotaProviderPill(provider));
    const label = document.createElement("span");
    label.className = "quota-account-label";
    label.textContent = statusLabel;
    wrap.appendChild(label);
    head.appendChild(wrap);
    row.appendChild(head);

    const detail = document.createElement("td");
    detail.colSpan = 4;
    detail.className = "quota-updated-cell";
    detail.textContent = reason || "无已纳入的数据源";
    row.appendChild(detail);

    body.appendChild(row);
    return body;
  }

  function currentPageUrl() {
    const query = params();
    if (window.location.pathname === "/") {
      for (const key of ["provider", "search", "window", "sort"]) query.set("quota_" + key, quotaFilter[key]);
      query.set("quota_history", String(Boolean(qs("#quota-past-accounts") && !qs("#quota-past-accounts").hidden)));
      query.set("account_actions", String(Boolean(qs("#codex-account-actions")?.open)));
      query.delete("quota_observation");
      qsa(".quota-observation-details[open]").forEach(node => query.append("quota_observation", node.dataset.observationKey));
    }
    const search = window.location.pathname === "/" ? "?" + query.toString() : window.location.search;
    return window.location.pathname + search + (window.location.hash || "");
  }

  function collectionUrl(hash = "") {
    const url = new URL("/collection", window.location.origin);
    for (const key of ["range", "start", "end", "machine", "agent", "model", "project"]) {
      for (const value of params().getAll(key)) url.searchParams.append(key, value);
    }
    url.searchParams.set("return", currentPageUrl());
    return url.pathname + url.search + hash;
  }

  function updateSyncOverviewLink() {
    if (window.location.pathname === "/collection") return;
    qsa('[data-collection-link], a[data-nav][href^="/collection"]').forEach(link => { link.href = collectionUrl(new URL(link.href, window.location.origin).hash); });
  }

  function renderSyncStatus(status) {
    renderQuotaRefreshStatus(status?.quota_refresh);
    if (!status) return;
    pageServerInstance = status.instance_id || pageServerInstance;
    const attention = (status.machines || []).filter(
      (machine) =>
        !machine.admitted ||
        machine.stale ||
        machine.availability === "unreachable" ||
        machine.availability === "unknown" ||
        ["failure", "cleanup_failed", "malformed_result"].includes(machine.last_attempt_outcome)
    );
    const refreshStatus = qs("#refresh-status");
    if (refreshStatus?.dataset.pending === "true") {
      refreshStatus.textContent = status.polling_error ? "更新状态暂不可用，请查看采集与诊断"
        : status.syncing ? "已受理 · 后台更新中"
        : attention.length ? "更新已结束，部分机器需关注" : "更新已结束";
      if (!status.syncing && !status.polling_error) delete refreshStatus.dataset.pending;
    }
    renderSpendCoverage(status.machines || []);
    const coverageElement = qs("#sync-coverage");
    const summaryElement = qs("#sync-summary");
    const machineList = qs("#sync-machines");
    if (!summaryElement || !machineList) return;
    const coverage = status.coverage || { admitted: 0, declared: 0 };
    if (coverageElement) coverageElement.textContent = `机器快照可读：${coverage.admitted}/${coverage.declared} 台`;
    const warningSummary = attention.length ? `${attention.length} 台需要关注：${attention.map(machine => machine.name).join("、")}` : "";
    summaryElement.textContent = status.polling_error
      ? `${status.polling_error} · 页面保留上次渲染的数据`
      : status.syncing
      ? (status.phase === "quota" ? "统计轮已结束 · 后台查询配额" : "后台更新统计与配额 · 新数据陆续显示") + (warningSummary ? ` · ${warningSummary}` : "")
      : attention.length
      ? warningSummary
      : "使用已采集数据 · 自动更新";
    const expandedMachines = new Set(qsa("#sync-machines details[open]").map(detail => detail.dataset.machine));
    const markup = (status.machines || []).map(machine => renderMachineStatus(machine, expandedMachines.has(machine.name))).join("");
    if (machineList.innerHTML !== markup) {
      const focusedMachine = document.activeElement?.closest?.("details[data-machine]")?.dataset.machine;
      machineList.innerHTML = markup;
      if (focusedMachine) Array.from(machineList.querySelectorAll("details[data-machine]")).find(node => node.dataset.machine === focusedMachine)?.querySelector("summary")?.focus();
    }
  }

  function renderQuotaRefreshStatus(status) {
    const table = qs("#quota-accounts");
    if (!table || !status) return;
    let note = qs("#quota-refresh-status");
    if (!note) {
      note = document.createElement("p");
      note.id = "quota-refresh-status";
      note.className = "quota-scope";
      note.setAttribute("role", "status");
      table.parentElement.before(note);
    }
    const pending = status.pending_machines || [];
    const failed = Object.keys(status.errors || {});
    note.textContent = status.running
      ? `正在查询配额${pending.length ? "：" + pending.join("、") : ""} · 查询结果会直接显示`
      : status.cache_error || (failed.length
        ? `配额查询未全部完成：${failed.join("、")} · 保留上次读数，详情见采集说明`
        : status.completed ? "配额查询已完成 · 统计同步单独进行" : "");
    note.hidden = !note.textContent;
  }

  // Fleet totals can omit newer records from machines whose refresh failed.
  // Keep that qualifier beside the figures; machine details live in Collection.
  function renderSpendCoverage(machines) {
    const note = qs("#spend-coverage");
    if (!note) {
      return;
    }
    const behind = machines.filter(
      (machine) => machine.admitted && (machine.stale || machine.availability === "unreachable")
    );
    if (!behind.length) {
      note.hidden = true;
      note.textContent = "";
      return;
    }
    const clauses = behind.map((machine) =>
      machine.generated_at
        ? `${machine.name}（数据截至 ${formatDate(machine.generated_at)}）`
        : `${machine.name}（尚无数据）`
    );
    const listed =
      clauses.length > 1
        ? `${clauses.slice(0, -1).join("、")}和${clauses[clauses.length - 1]}`
        : clauses[0];
    // Why the machine is behind (refresh failed, server just restarted, data
    // simply older than 6h) is on its card; the figures only need the fact
    // that newer records are absent, without comparing estimates with bills.
    note.hidden = false;
    note.textContent =
      `${listed}的较新记录尚未计入，当前金额小计可能不完整。`;
  }

  function renderMachineStatus(machine, expanded = false) {
    const chips = [];
    const refreshAttemptFailed = ["failure", "malformed_result"].includes(machine.last_attempt_outcome);
    if (!machine.admitted) {
      chips.push(statusChip("bad", "未纳入"));
    }
    if (machine.syncing) {
      chips.push(statusChip("info", machine.statistics_syncing === false ? "统计已更新 · 查询配额中" : (machine.statistics?.observed_at || machine.generated_at ? "刷新中 · 显示上次快照" : "刷新中 · 等待首份快照")));
    }
    if (machine.availability === "unreachable" || refreshAttemptFailed) {
      chips.push(statusChip("warn", "刷新失败"));
    } else if (machine.availability === "unknown") {
      chips.push(statusChip("warn", "未检查"));
    }
    if (machine.stale) {
      chips.push(statusChip("warn", "数据过期"));
    }
    if (machine.last_attempt_outcome === "cleanup_failed") {
      chips.push(statusChip("warn", "清理失败"));
    }
    if (!chips.length) {
      chips.push(statusChip("ok", "快照可读"));
    }
    const marker = machine.this_machine ? ' <span class="status-pill identity">本机</span>' : "";
    const facts = [];
    const observedAt = machine.statistics?.observed_at || machine.generated_at;
    facts.push(observedAt ? `数据更新于 ${formatDate(observedAt)} · ${updatedText(observedAt)}` : "尚无数据");
    const quality = machine.statistics?.usage_quality_counts;
    const qualityDetail = quality == null ? "Claude 用量记录完整性未知" :
      `留存 Claude 记录：有结束标记 ${integer(quality.observed)} · 不完整 ${integer(quality.incomplete)} · 未知 ${integer(quality.unknown)}（全部留存，非当前日期范围；结束标记不保证最终用量完整）`;
    if (["unreachable", "unknown"].includes(machine.availability) && machine.last_successful_contact_ts) {
      facts.push(`最后联系 ${formatDate(machine.last_successful_contact_ts)}`);
    }
    const attemptFailed = ["failure", "cleanup_failed", "malformed_result"].includes(machine.last_attempt_outcome);
    if (machine.last_attempt_ts && (!machine.admitted || machine.availability === "unreachable" || attemptFailed)) {
      facts.push(`最后尝试 ${formatDate(machine.last_attempt_ts)}`);
    }
    let consequence = "";
    if (!machine.admitted) {
      const exclusion = exclusionText(machine);
      consequence = `${exclusion}；该机数据未计入合计。检查这台机器的接入配置和采集状态。`;
    } else if (machine.availability === "unreachable") {
      const age = machine.stale ? "（已过期）" : "";
      consequence = `用的是上次的数据${age}。检查这台机器的连接和采集状态，恢复后返回业务页更新统计与配额。`;
    } else if (refreshAttemptFailed) {
      const age = machine.stale ? "（已过期）" : "";
      consequence = `用的是上次的数据${age}。检查这台机器的采集状态和技术详情，处理后返回业务页更新统计与配额。`;
    } else if (machine.availability === "unknown") {
      consequence = machine.syncing ? "正在进行首次检查，暂用上次的数据；完成后会自动更新。" : "用的是上次的数据；本服务启动后还没检查过这台机器，可返回业务页更新统计与配额。";
    } else if (machine.stale) {
      consequence = "用的是较旧的数据。返回业务页更新统计与配额；若仍未更新，请检查这台机器的采集状态。";
    } else if (machine.last_attempt_outcome === "cleanup_failed") {
      consequence = "数据已更新，但刷新后的清理未完成；请查看技术详情和该机采集日志。";
    } else if (machine.reason && !machine.syncing) {
      consequence = "数据已更新，采集另有提示，见技术详情。";
    }
    const consequenceElement = consequence
      ? `<div class="sync-machine-reason">${escapeHtml(consequence)}</div>`
      : "";
    return `<article class="sync-machine ${machine.admitted ? "included" : "excluded"}">
      <div class="sync-machine-title"><strong>${escapeHtml(machine.name)}</strong>${marker}<span class="sync-chips">${chips.join("")}</span></div>
      <div class="sync-machine-time">${facts.join(" · ")}</div>
      ${consequenceElement}
      <details class="note-disclosure" data-machine="${escapeHtml(machine.name)}" ${expanded ? "open" : ""}><summary>质量、历史范围与技术详情</summary><p>${qualityDetail}</p>${machine.data_start_date ? `<p>历史自 ${escapeHtml(machine.data_start_date)}</p>` : ""}${machine.reason ? `<p>${escapeHtml(machine.reason)}</p>` : ""}</details>
    </article>`;
  }

  function exclusionText(machine) {
    if (machine.availability === "never") {
      return "尚未同步过数据";
    }
    const labels = {
      machine_config_fingerprint: "机器配置指纹不一致",
      bucket_timezone: "分桶时区不一致",
      generation_id: "数据版本标识不一致",
      digest: "数据版本摘要不一致",
      source_host_identity_collision: "来源机器身份冲突",
      invalid_generation: "数据版本校验失败",
      removed_from_config: "最近一次同步期间该机器被移出配置",
      no_longer_declared_after_latest_attempt: "最近一次同步已尝试该机器，但它已不在配置中",
    };
    return labels[machine.exclusion_reason] || machine.exclusion_reason || "数据版本未被采纳";
  }

  function statusChip(kind, text) {
    return `<span class="status-pill ${kind}">${escapeHtml(text)}</span>`;
  }

  async function waitForSyncTerminal(initialStatus, options) {
    const callerIsCurrent = options && typeof options.isCurrent === "function" ? options.isCurrent : () => true;
    // Its deadline is 12 minutes, and its caller's predicate tracks that page's
    // own load generation, which nothing advances on a tab change. Left at
    // that, leaving Overview mid-sync keeps a poller rewriting the sync panel
    // of whatever the reader opened next, twice a second, until the deadline.
    // The page generation is the part that knows the tab changed.
    const generation = pageGeneration;
    const isCurrent = () => pageIsCurrent(generation) && callerIsCurrent();
    const renderIfCurrent = (status) => {
      if (isCurrent()) {
        renderSyncStatus(status);
      }
    };
    let status = initialStatus || await api("/api/sync-status");
    const instance = status.instance_id;
    const includeQuota = !options || options.includeQuota !== false;
    let fingerprint = snapshotFingerprint(status, includeQuota);
    const requested = status.refresh_request || status.refresh_requested || 0;
    const targeted = !(options && options.waitForIdle) && requested > 0;
    const deadline = Date.now() + 720000;
    renderIfCurrent(status);
    while (targeted ? (status.refresh_completed || 0) < requested : (status.syncing || status.queued_refresh)) {
      if (!isCurrent()) return status;
      if (Date.now() >= deadline) throw new Error("刷新仍在进行，已保存的数据仍可用。");
      await new Promise((resolve) => setTimeout(resolve, 2000));
      try {
        status = await api("/api/sync-status");
      } catch (error) {
        status = Object.assign({}, status, {
          syncing: false,
          terminal: false,
          polling_error: `同步状态不可用：${error && error.message ? error.message : error}`,
          machines: (status.machines || []).map((machine) => Object.assign({}, machine, { syncing: false })),
        });
        renderIfCurrent(status);
        return status;
      }
      if (!isCurrent()) return status;
      if (status.instance_id !== instance) throw new Error("刷新期间服务重启，请重试。");
      renderIfCurrent(status);
      const next = snapshotFingerprint(status, includeQuota);
      if (next !== fingerprint && options && options.onProgress) {
        await options.onProgress(status);
        fingerprint = next;
      }
    }
    return status;
  }

  function snapshotFingerprint(status, includeQuota = true) {
    return JSON.stringify([(status.machines || []).map((machine) =>
      [machine.name, machine.admitted, machine.generation_id]).sort(), includeQuota ? status.quota_refresh : null]);
  }

  async function refreshStatistics(options) {
    // Same reason as the poller above: Refresh is pressed on one tab and can
    // still be running when the reader is on another.
    const generation = pageGeneration;
    const initial = await api("/api/refresh");
    if (!pageIsCurrent(generation)) return initial;
    const status = await waitForSyncTerminal(initial, options);
    if (status.polling_error) throw new Error(status.polling_error);
    return status;
  }

  function updatedText(iso) {
    if (!iso) {
      return "无数据";
    }
    const updatedAt = new Date(iso).getTime();
    if (Number.isNaN(updatedAt)) {
      return "无数据";
    }
    const diffMs = Date.now() - updatedAt;
    if (diffMs < 0) {
      return "刚刚";
    }
    const mins = Math.floor(diffMs / 60000);
    if (mins < 1) {
      return "刚刚";
    }
    if (mins < 60) {
      return `${mins} 分钟前更新`;
    }
    const hours = Math.floor(mins / 60);
    if (hours < 24) {
      return `${hours} 小时前更新`;
    }
    return `${Math.floor(hours / 24)} 天前更新`;
  }

  function costReasonText(reason) {
    return ({usage_incomplete: "用量记录不完整", unknown_model: "模型价格未知",
      missing_rates: "缺少适用费率", unsupported_service_context: "服务模式无法定价",
      conflicting_service_context: "服务模式记录冲突", invalid_recorded_cost: "源记录金额无效",
      unrecorded: "原因未记录"})[reason] || "原因未识别";
  }

  function costDiagnosticsQuery() {
    const query = new URLSearchParams({range: getRange()});
    for (const key of ["start", "end", "machine", "agent", "model", "project"]) {
      for (const value of params().getAll(key)) query.append(key, value);
    }
    return query;
  }

  function renderCostDiagnostics(data) {
    const groups = data.groups;
    qs("#cost-diagnostics-status").textContent = groups.length
      ? `已留存明细中 ${integer(groups.reduce((total, row) => total + row.entries, 0))} 条金额未知记录`
      : "所选范围的已留存明细中，没有金额未知记录。";
    const incomplete = groups.filter(row => row.reason === "usage_incomplete");
    const other = groups.filter(row => row.reason !== "usage_incomplete");
    const rows = items => items.map(row => {
      const query = costDiagnosticsQuery();
      for (const key of ["machine", "agent", "model"]) query.set(key, row[key]);
      query.set("cost_status", "unknown");
      query.set("return", window.location.pathname + window.location.search + "#cost-diagnostics");
      return `<tr><th scope="row">${escapeHtml(costReasonText(row.reason))}</th><td data-label="机器">${escapeHtml(row.machine)}</td><td data-label="Agent / 模型">${escapeHtml(agentLabel(row.agent))}<br>${escapeHtml(row.model)}</td><td data-label="记录数" class="numeric">${integer(row.entries)}</td><td data-label="明细"><a href="/sessions?${escapeHtml(query.toString())}">查看该模型未知记录</a></td></tr>`;
    }).join("");
    qs("#cost-diagnostics-body").innerHTML = rows(other);
    qs("#cost-diagnostics-table").hidden = !other.length;
    qs("#incomplete-cost-records").hidden = !incomplete.length;
    qs("#incomplete-cost-summary").textContent = `缺少完整用量的记录（${integer(incomplete.reduce((total, row) => total + row.entries, 0))}）`;
    qs("#incomplete-cost-body").innerHTML = rows(incomplete);
  }

  async function initCollection() {
    const current = pageScope();
    const back = qs("#collection-return");
    const raw = params().get("return");
    if (raw) {
      let url;
      try { url = new URL(raw, window.location.origin); } catch (_) { url = new URL("/", window.location.origin); }
      if (url.origin === window.location.origin && (["/", "/explore", "/sessions", "/network"].includes(url.pathname) || GUIDE_ROUTES.has(url.pathname))) {
        back.href = url.pathname + url.search + url.hash;
        back.textContent = "返回来源页（保留日期、筛选与详情）";
      }
    }
    const query = costDiagnosticsQuery();
    const dates = query.get("range") === "custom"
      ? `${query.get("start") || "未指定"} 至 ${query.get("end") || "未指定"}`
      : query.get("range") === "all" ? "全部历史" : `最近 ${query.get("range").replace("d", " 天")}`;
    const filters = ["machine", "agent", "model", "project"].flatMap(key => query.getAll(key));
    qs("#collection-context").hidden = true;
    qs("#cost-diagnostics-scope").textContent = dates + (filters.length ? " · " + filters.join("、") : " · 全部来源");
    async function loadCosts() {
      const button = qs("#reload-cost-diagnostics");
      button.disabled = true;
      qs("#cost-diagnostics-status").textContent = "读取中…";
      try {
        const data = await api("/api/cost-diagnostics", Object.fromEntries([...new Set(query.keys())].map(key => [key, query.getAll(key)])));
        if (current()) renderCostDiagnostics(data);
      } catch (error) {
        if (current()) qs("#cost-diagnostics-status").textContent = `费用诊断读取失败：${error.message}。可重新读取。`;
      } finally { if (current()) button.disabled = false; }
    }
    qs("#reload-cost-diagnostics").addEventListener("click", loadCosts);
    loadCosts();
    async function load() {
      try {
        const data = await api("/api/collection");
        if (!current()) return;
        renderSyncStatus(data.sync);
        renderQuotaMachineRecords(data.rate_limits);
        renderQuotaSignedOut(data.rate_limits, true);
        qs("#collection-quota-empty").hidden = !qs("#quota-machine-records").hidden;
      } catch (error) {
        if (current()) qs("#sync-summary").textContent = `读取失败：${error.message}；保留上次显示的观测，可重新读取。`;
      }
    }
    bindShell(load);
    watchPageData(load);
    await load();
  }

  async function initGuide() {
    // Fragments were the old public entry points. Replace, rather than push,
    // so Back does not get trapped on an alias; the query is retained verbatim.
    if (window.location.pathname === "/guide") {
      const chapter = (window.location.hash || "").slice(1);
      const target = GUIDE_CHAPTERS.includes(chapter) ? chapter : "sources";
      window.location.replace(`/guide/${target}${window.location.search}`);
      return;
    }
    if (!qs("#sync-machines")) {
      updateNavLinks();
      return;
    }
    const current = pageScope();
    async function load() {
      try {
        const status = await api("/api/sync-status");
        if (current()) renderSyncStatus(status);
      } catch (error) {
        if (current()) qs("#sync-summary").textContent = `状态检查失败：${error.message}。可重新检查。`;
      }
    }
    bindShell(load);
    await load();
  }

  async function initNetwork() {
    async function load(force) {
      try {
        const data = await api("/api/network", force ? { force: "1" } : {});
        renderNetwork(data, () => load(true));
      } catch (error) {
        renderNetwork({
          error: error && error.message ? error.message : String(error),
          verdict: "unknown",
        }, () => load(true));
      }
    }
    const refresh = qs("#refresh");
    if (refresh) {
      refresh.addEventListener("click", () => withRefresh(refresh, () => load(true)));
    }
    updateNavLinks();
    await load(false);
  }

  function renderNetwork(data, retry) {
    const target = qs("#network-checked-host");
    if (target) target.textContent = `仅检测服务端主机 ${data.checked_host || "身份未取得"}；不检测浏览器所在设备或其他机器。`;
    renderNetworkBanner(data);
    if (data.installed === false) {
      renderNetworkError({
        title: "ip-check 未安装，请在 agent-monitor 代码目录中运行 ./install.sh。",
        message: data.hint || data.error || "使用 /network 前请先安装 ip-check。",
        docs: true,
      }, retry);
      setNetworkCardsUnavailable("ip-check 未安装。");
      return;
    }
    if (data.error) {
      renderNetworkError({
        title: "网络检查失败",
        message: data.error,
        retry: true,
      }, retry);
      setNetworkCardsUnavailable("网络检查失败。");
      return;
    }
    clearNetworkError();
    renderLocalNetwork(data);
    renderPublicNetwork(data);
    renderRiskNetwork(data);
    renderTimezoneNetwork(data);
    renderConclusions(data);
  }

  function renderNetworkBanner(data) {
    const banner = qs("#network-banner");
    const verdict = qs("#network-verdict");
    const updated = qs("#network-updated");
    const coverage = data.coverage;
    const missing = coverage?.missing;
    const incomplete = !Array.isArray(missing) || missing.length > 0;
    const level = data.verdict || "unknown";
    banner.className = `verdict-banner ${level === "high" ? "high" : incomplete ? "unknown" : level === "low" ? "low" : level === "proxy-in-use" ? "proxy-in-use" : "unknown"}`;
    if (data.installed === false) {
      verdict.textContent = "—";
      updated.textContent = "原因：ip-check 未安装";
      return;
    } else if (data.error) {
      verdict.textContent = "—";
      updated.textContent = `原因：${data.error}`;
      return;
    } else if (level === "high") {
      const hits = (data.conclusions || []).filter(item => item.level === "bad").length;
      verdict.textContent = hits ? `本地规则检出 ${hits} 项异常` : "本地规则提示异常";
    } else if (level === "proxy-in-use") {
      verdict.textContent = incomplete ? "部分检测 · 检出代理" : "检出代理 · 本地规则评估";
    } else if (level === "low") {
      verdict.textContent = incomplete ? "部分检测 · 未发现已知高风险信号" : "未发现已知高风险信号 · 本地规则评估";
    } else {
      verdict.textContent = "网络状态未知";
    }
    updated.textContent = data.timestamp ? `更新于 ${formatDate(data.timestamp)}` : "—";
    const names = {public: "公网", dns: "DNS 地域", risk: "风险分", timezone: "时区"};
    updated.textContent += !Array.isArray(missing) ? " · 检测覆盖未知" : missing.length ? " · 缺测：" + missing.map(key => names[key] || key).join("、") : "";
    updated.textContent += " · IPv6 公网泄漏未核实";
  }

  function renderNetworkError(error, retry) {
    const panel = qs("#network-error");
    if (!panel) {
      return;
    }
    const docsLink = error.docs ? '<a href="/ip-check-docs">文档</a>' : "";
    const retryButton = error.retry ? '<button type="button" data-network-retry>重试</button>' : "";
    const actions = [docsLink, retryButton].filter(Boolean).join("");
    panel.hidden = false;
    panel.innerHTML = [
      `<h2>${escapeHtml(error.title)}</h2>`,
      `<p>${escapeHtml(error.message || "未知错误")}</p>`,
      actions ? `<div class="network-error-actions">${actions}</div>` : "",
    ].join("");
    const button = panel.querySelector("[data-network-retry]");
    if (button && retry) {
      button.addEventListener("click", () => withRefresh(button, retry));
    }
  }

  function clearNetworkError() {
    const panel = qs("#network-error");
    if (panel) {
      panel.hidden = true;
      panel.innerHTML = "";
    }
  }

  function setNetworkCardsUnavailable(message) {
    const html = `<div class="section-failure">${escapeHtml(message)}</div>`;
    ["local", "public", "risk", "timezone", "conclusion"].forEach((id) => {
      qs(`#network-${id}`).innerHTML = html;
    });
  }

  function renderLocalNetwork(data) {
    const local = data.local;
    if (!local) {
      renderSectionFailure("#network-local", data, "local");
      return;
    }
    const ipv6 = local.ipv6 ? statusPill("warn", `检出本地地址 ${local.ipv6}；公网泄漏未核实`) : statusPill("warn", "未检出本地地址；公网泄漏未核实");
    const dnsUnknown = !local.dns?.length || local.dns.some(entry => !entry.country);
    const dns = local.dns && local.dns.length ? local.dns.map(renderDns).join("") : "—";
    qs("#network-local").innerHTML = [
      kvRow("内网 IP", escapeHtml(local.lan_ip || "—")),
      kvRow("IPv6", ipv6),
      kvRow("DNS 服务器", `<div class="dns-list">${dns}</div>`),
      kvRow("DNS 地域", local.dns_has_cn ? statusPill("bad", "检出国内 DNS 解析器") : dnsUnknown ? statusPill("warn", "DNS 地域未知或部分未知") : statusPill("ok", "已知解析器中未检出国内 DNS")),
    ].join("");
  }

  function renderPublicNetwork(data) {
    const pub = data.public;
    if (!pub || pub.ok === false) {
      renderSectionFailure("#network-public", data, "public", pub && pub.error);
      return;
    }
    const location = [pub.country, pub.region, pub.city].filter(Boolean).join(" / ") || "—";
    const timezone = pub.timezone ? `${escapeHtml(pub.timezone)} (${escapeHtml(pub.tz_offset || "—")})` : "—";
    qs("#network-public").innerHTML = [
      kvRow("本次探测 IP", escapeHtml(pub.ip || "—")),
      kvRow("探测目标", escapeHtml(pub.probe_url || data.proxy_effective?.probe_url || "旧记录未保存目标")),
      kvRow("位置", escapeHtml(location)),
      kvRow("运营商", escapeHtml(pub.isp || "—")),
      kvRow("机构", escapeHtml(pub.org || "—")),
      kvRow("时区", timezone),
      '<p class="scope-note">这个 IP 属于本次探测。不同目标可能走不同代理，不能据此确认账号请求的公网 IP。</p>',
    ].join("");
  }

  function renderRiskNetwork(data) {
    const pub = data.public || {};
    const risk = data.risk;
    const spam = data.spam;
    const proxyEnvEntries = Object.entries(data.proxy_envs || {});
    const present = value => value !== null && value !== undefined && value !== "";
    const score = result => statusPill(result.level === "high" ? "bad" : result.level === "medium" ? "warn" : "ok", `${result.score}/100${result.level ? ` ${result.level}` : ""}`);
    const markedProxy = pub.proxy === true || risk?.marked_proxy === true ? true
      : pub.proxy === false || risk?.marked_proxy === false ? false : null;
    const booleanReading = value => value === true ? statusPill("warn", "是") : value === false ? statusPill("ok", "否") : statusPill("warn", "未知");
    const provider = (label, section, rows, rawLines = [], missing = []) => {
      const error = sectionMessage(data, section);
      return `<div class="network-provider"><h3>${label}</h3>${rows.length ? rows.join("") : error || rawLines.length ? "" : '<p class="scope-note">未取得读数</p>'}${rows.length && missing.length ? `<p class="scope-note">未取得：${missing.join("、")}</p>` : ""}${error ? `<p class="scope-note">${escapeHtml(error)}</p>` : ""}${rawLines.length ? `<p class="scope-note">${rawLines.map(escapeHtml).join("<br>")}</p>` : ""}</div>`;
    };
    const riskRows = [];
    if (present(risk?.score)) riskRows.push(kvRow("风险分", score(risk)));
    if (present(risk?.type)) riskRows.push(kvRow("类型", escapeHtml(risk.type)));
    const spamRows = [];
    if (present(spam?.score)) spamRows.push(kvRow("垃圾评分", score(spam)));
    if (present(spam?.frequency)) spamRows.push(kvRow("举报次数", escapeHtml(String(spam.frequency))));
    if (present(spam?.last_seen)) spamRows.push(kvRow("最近举报", escapeHtml(spam.last_seen)));
    const envs = proxyEnvEntries.length
      ? proxyEnvEntries.map(([key, value]) => `${escapeHtml(key)} = ${escapeHtml(value)}`).join("<br>")
      : "未记录代理环境变量";
    qs("#network-risk").innerHTML = [
      provider("proxycheck · 风险", "risk", riskRows, risk?.raw_lines || (risk?.display && !present(risk?.score) ? [risk.display] : []), [!present(risk?.score) && "风险分", !present(risk?.type) && "类型"].filter(Boolean)),
      '<div class="network-provider"><h3>代理与机房标记</h3>',
      kvRow("标记为代理", booleanReading(markedProxy)),
      kvRow("机房 IP", booleanReading(pub.hosting)),
      '<p class="scope-note">代理标记与机房判定来自 ip-api。</p></div>',
      provider("stopforumspam · 举报", "spam", spamRows, spam?.raw_lines || [], [!present(spam?.score) && "垃圾评分", !present(spam?.frequency) && "举报次数", !present(spam?.last_seen) && "最近举报"].filter(Boolean)),
      '<div class="network-provider"><h3>服务端出站策略</h3>',
      kvRow("策略", data.proxy_effective?.source === "system"
        ? "执行主机的系统代理，按每个目标解析"
        : data.proxy_effective?.source === "published" ? "旧记录：个人代理文件指定" : "旧记录未保存有效系统策略"),
      kvRow("环境变量", envs),
      `<p class="scope-note">环境变量读自服务进程，不代表请求实际选用的代理。${data.proxy_effective?.source === "system" ? "账号操作与探测采用同一系统策略，目标不同仍可能使用不同出口。" : "这条旧记录不能确认当前账号请求的出口。"}</p></div>`,
    ].join("");
  }

  function renderTimezoneNetwork(data) {
    const tz = data.tz_check;
    const pub = data.public || {};
    if (!tz) {
      renderSectionFailure("#network-timezone", data, "tz_check");
      return;
    }
    const match = tz.matched === true
      ? statusPill("ok", tz.match_label || "一致")
      : tz.matched === false
        ? statusPill("bad", tz.match_label || "不一致")
        : statusPill("warn", "无法比较");
    qs("#network-timezone").innerHTML = [
      kvRow("服务端主机时区", `${escapeHtml(tz.cli_tz || "—")} (${escapeHtml(tz.cli_offset || "—")})`),
      kvRow("公网时区", pub.timezone ? `${escapeHtml(pub.timezone)} (${escapeHtml(pub.tz_offset || "—")})` : "—"),
      kvRow("是否一致", match),
    ].join("");
  }

  function renderConclusions(data) {
    const items = (data.conclusions || []).slice().sort((a, b) => ({bad: 0, warn: 1, ok: 2}[a.level] ?? 3) - ({bad: 0, warn: 1, ok: 2}[b.level] ?? 3));
    const note = '<details class="note-disclosure"><summary>规则与检测边界</summary><p><strong>异常条件</strong></p><p>本地规则将以下任一情况标为「异常」：</p><ul><li>检出本地 IPv6 地址。</li><li>检出国内 DNS 解析器。</li><li>风险分不低于 70。</li><li>服务端主机时区与公网 IP 时区不一致。</li></ul><p><strong>检测不完整</strong></p><ul><li>未检出上述异常，但缺少公网、DNS 地域或时区结果时，风险状态为未知。</li><li>缺少风险分时，标为部分检测。</li></ul><p><strong>结论范围</strong></p><p>本地 IPv6 地址不证明公网泄漏。未检出上述异常不保证服务可用。</p></details>';
    if (!items.length) {
      qs("#network-conclusion").innerHTML = `<div class="empty-state">—</div>${note}`;
      return;
    }
    qs("#network-conclusion").innerHTML = `<ul class="conclusion-list">${items.map((item) => {
      const kind = item.level === "bad" ? "bad" : item.level === "warn" ? "warn" : "ok";
      const levelText = { bad: "异常", warn: "注意", ok: "正常" }[item.level] || item.level;
      return `<li>${statusPill(kind, levelText)} <span>${escapeHtml(item.text)}</span></li>`;
    }).join("")}</ul>${note}`;
  }

  function renderSectionFailure(selector, data, section, fallback) {
    const message = fallback || sectionMessage(data, section) || "未知";
    qs(selector).innerHTML = `<div class="section-failure">${statusPill("warn", "查询失败")} ${escapeHtml(message)}</div>`;
  }

  function sectionMessage(data, section) {
    const error = (data.errors || []).find((item) => item.section === section);
    return error && error.message;
  }

  function kvRow(label, value) {
    return `<div class="kv-row"><div class="label">${escapeHtml(label)}</div><div class="value">${value}</div></div>`;
  }

  function renderDns(entry) {
    const country = entry.country ? ` ${statusPill(entry.country === "CN" ? "bad" : "ok", entry.country)}` : ` ${statusPill("warn", "地域未知")}`;
    const label = entry.label ? ` <span class="muted">${escapeHtml(entry.label)}</span>` : "";
    return `<div class="dns-row"><span>${escapeHtml(entry.ip)}</span>${label}${country}</div>`;
  }

  function statusPill(kind, text) {
    return `<span class="status-pill ${kind}">${escapeHtml(text)}</span>`;
  }

  function formatDate(iso) {
    const date = new Date(iso);
    return Number.isNaN(date.getTime()) ? iso : fmtAbs(date);
  }

  // Session rows all carried a full date and zone suffix, which wrapped every
  // row onto a second line for information that is identical down long runs of
  // the table. The date moves to a group heading; the row keeps the time.
  function dayKey(iso) {
    const date = new Date(iso);
    if (Number.isNaN(date.getTime())) {
      return "";
    }
    const opts = serverTimeZone ? { timeZone: serverTimeZone } : undefined;
    try {
      return new Intl.DateTimeFormat(
        "en-CA",
        Object.assign({ year: "numeric", month: "2-digit", day: "2-digit" }, opts)
      ).format(date);
    } catch (e) {
      return date.toISOString().slice(0, 10);
    }
  }

  function timeOfDay(iso) {
    const date = new Date(iso);
    if (Number.isNaN(date.getTime())) {
      return iso;
    }
    const opts = serverTimeZone ? { timeZone: serverTimeZone } : undefined;
    try {
      return new Intl.DateTimeFormat(
        undefined,
        Object.assign({ hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false }, opts)
      ).format(date);
    } catch (e) {
      return date.toLocaleTimeString();
    }
  }

  async function initOverview() {
    const restored = params();
    if (restored.has("quota_provider")) {
      quotaFilter.provider = ["all", "claude", "codex"].includes(restored.get("quota_provider")) ? restored.get("quota_provider") : "all";
      quotaFilter.search = restored.get("quota_search") || "";
      quotaFilter.window = restored.get("quota_window") === "seven_day" ? "seven_day" : "five_hour";
      quotaFilter.sort = ["default", "remaining", "remaining_desc", "reset", "observed"].includes(restored.get("quota_sort")) ? restored.get("quota_sort") : "default";
    }
    let restoreQuotaReading = true;
    const accountActions = qs("#codex-account-actions");
    if (accountActions && restored.get("account_actions") === "true") accountActions.open = true;
    codexQuotaReadings = [];
    renderedQuotaRateLimits = null;
    pageCleanups.push(() => { codexQuotaReadings = []; renderedQuotaRateLimits = null; });
    if (qs("#codex-account-actions")) {
      const currentAccountsPage = pageScope();
      import("/web/codex-accounts.js").then((module) => {
        if (currentAccountsPage()) module.init();
      }).catch(() => {
        const notice = qs("#codex-account-notice");
        if (currentAccountsPage() && notice) notice.textContent = "账号操作未能加载，请刷新页面重试。";
      });
    }
    const retiredFiveHour = qs("#codex-five-hour");
    const legacyCard = retiredFiveHour && retiredFiveHour.closest(".kpi-card");
    const compatibilityNodes = qs("#codex-five-hour-compat");
    if (legacyCard || compatibilityNodes) {
      (legacyCard || compatibilityNodes).remove();
    }
    // Same cache-skew guard, one generation on: a cached copy of the old page
    // has the per-provider quota cards but no #quota-accounts to render into,
    // which would leave those cards frozen at their placeholder forever.
    if (!qs("#quota-accounts")) {
      const legacyGrid = qs(".kpi-grid.quota");
      if (legacyGrid) {
        // Say why the section is empty. Removing the cards without a word leaves
        // a titled hole, which reads as "no quota" rather than "reload me".
        const note = document.createElement("p");
        note.className = "quota-scope";
        note.textContent = "配额需要重新加载本页（当前是旧版缓存）。";
        legacyGrid.replaceWith(note);
      }
    }
    let overviewGeneration = 0;
    let renderedOverviewGeneration = 0;
    const pageCurrent = pageScope();
    let activeLoads = 0;
    async function load(force, background = false) {
      if (background && activeLoads) return;
      activeLoads += 1;
      try { await loadOverview(force); } finally { activeLoads -= 1; }
    }
    async function loadOverview(force) {
      const generation = ++overviewGeneration;
      const selectedRange = getRange();
      if (force === true) {
        try {
          const receipt = await api("/api/refresh", { ack: "1" });
          if (!pageCurrent() || generation !== overviewGeneration) return;
          observeRefresh(receipt, generation, selectedRange).catch((error) => {
            if (pageCurrent() && generation === overviewGeneration) showOverviewLoadError(error);
          });
          const summary = qs("#refresh-status");
          if (summary) {
            summary.dataset.pending = "true";
            summary.textContent = "刷新请求已受理 · 后台更新，当前数据仍可查看";
          }
        } catch (error) {
          if (pageCurrent() && generation === overviewGeneration) showOverviewLoadError(error);
        }
        return;
      }
      let data;
      try {
        data = await api("/api/overview", { range: selectedRange, compare: qs("#period-comparison")?.open ? "1" : undefined });
      } catch (error) {
        if (generation === overviewGeneration) {
          showOverviewLoadError(error);
        }
        return;
      }
      if (!pageCurrent() || generation < renderedOverviewGeneration) {
        return;
      }
      renderedOverviewGeneration = generation;
      const error = qs("#overview-load-error");
      if (error) {
        error.remove();
      }
      renderOverview(data, selectedRange);
      if (restoreQuotaReading) {
        restoreQuotaReading = false;
        if (restored.get("quota_history") === "true") {
          const history = qs("#quota-past-accounts");
          if (history) history.hidden = false;
          qs(".quota-past-toggle")?.setAttribute("aria-expanded", "true");
        }
        const open = new Set(restored.getAll("quota_observation"));
        qsa(".quota-observation-details").forEach(node => { if (open.has(node.dataset.observationKey)) node.open = true; });
      }
      renderSyncStatus(data.sync);
      if (data.sync && data.sync.refresh_pending) {
        // The click acknowledges a refresh request; this observer owns background
        // progress and must not keep the button waiting for remote collectors.
        observeRefresh(data.sync, generation, selectedRange).catch((error) => {
          if (pageCurrent() && generation === overviewGeneration) {
            renderSyncStatus(Object.assign({}, data.sync, { polling_error: error.message || String(error) }));
          }
        });
      }
    }
    async function observeRefresh(status, generation, selectedRange) {
        if (!status.machines) status = await api("/api/sync-status");
        if (status.syncing) {
          const terminalStatus = await waitForSyncTerminal(status, {
            waitForIdle: true,
            isCurrent: () => pageCurrent() && generation === overviewGeneration,
            onProgress: async () => {
              const fresh = await api("/api/overview", { range: selectedRange, sync: "0", compare: qs("#period-comparison")?.open ? "1" : undefined });
              if (!pageCurrent() || generation !== overviewGeneration) return;
              renderOverview(fresh, selectedRange);
              renderSyncStatus(fresh.sync);
              qs("#overview-load-error")?.remove();
            },
          });
          if (terminalStatus.polling_error) {
            return;
          }
        }
        if (!pageCurrent() || generation !== overviewGeneration) {
          return;
        }
        const finalData = await api("/api/overview", { range: selectedRange, sync: "0", compare: qs("#period-comparison")?.open ? "1" : undefined });
        if (!pageCurrent() || generation !== overviewGeneration) {
          return;
        }
        renderOverview(finalData, selectedRange);
        renderSyncStatus(finalData.sync);
        qs("#overview-load-error")?.remove();
    }
    qs("#period-comparison")?.addEventListener("toggle", () => {
      if (qs("#period-comparison").open) {
        qs("#comparison-result").textContent = "正在读取两个周期…";
        load(false);
      }
    });
    bindShell(load);
    watchPageData(() => load(false, true));
    await load(false);
  }

  function showOverviewLoadError(error) {
    const refreshStatus = qs("#refresh-status");
    if (refreshStatus?.dataset.pending === "true") {
      refreshStatus.textContent = "更新未完成，请查看下方提示";
    }
    // Inside <main>, not on <body>. It belongs to Overview, and only Overview's
    // next successful load removes it — parked on the body it outlived the swap
    // and followed the reader onto every other tab. Its `.main` class was doing
    // the centring that made it look placed there; with that class no longer
    // centring anything, a body-level banner would also sit off-grid below a
    // full-height shell.
    let message = qs("#overview-load-error");
    if (!message) {
      const main = document.querySelector("main");
      if (!main) {
        return;
      }
      message = document.createElement("p");
      message.id = "overview-load-error";
      message.className = "error";
      main.appendChild(message);
    }
    message.textContent = `总览刷新失败：${error.message || error}`;
  }

  // Every session ever recorded arrived as one unbroken table — 2,512 rows and
  // 162,000 pixels on this machine — with no way to narrow it and a header that
  // scrolled out of sight within a screen. Narrowing comes first because the
  // question is almost always "which session was that", not "show me all".
  const SESSIONS_PAGE_SIZE = 100;
  const sessionsView = {
    rows: [],
    expandedDays: new Map(),
    dayLimits: new Map(),
    page: 0,
    status: "loading",
    filters: { agent: "", project: "", model: "", machine: "", cost_status: "" },
  };
  let sessionsGeneration = 0;
  const SESSION_FILTERS = [
    { key: "machine", select: "#filter-machine", field: "machine" },
    { key: "agent", select: "#filter-agent", field: "agent_id" },
    { key: "project", select: "#filter-project", field: "project" },
    { key: "model", select: "#filter-model", field: "model" },
    { key: "cost_status", select: "#filter-cost-status", values: ["unknown"] },
  ];

  async function initSessions() {
    const diagnosticBack = qs("#sessions-diagnostics-return");
    if (diagnosticBack && params().has("return")) {
      try {
        const target = new URL(params().get("return"), window.location.origin);
        if (target.origin === window.location.origin && target.pathname === "/collection") {
          diagnosticBack.href = target.pathname + target.search + target.hash;
          diagnosticBack.hidden = false;
        }
      } catch (_) { /* Ignore invalid return URLs; the session remains usable. */ }
    }
    if (params().has("session")) return initSessionDetail();
    SESSION_FILTERS.forEach(({key}) => { sessionsView.filters[key] = params().get(key) || ""; });
    sessionsView.page = Math.max(0, parseInt(params().get("page"), 10) || 0);
    const initialSort = qs("#sort");
    if (initialSort && params().has("sort")) initialSort.value = params().get("sort");
    const pageCurrent = pageScope();
    let activeLoads = 0;
    let hasLoaded = false;
    async function load(force, background = false) {
      if (background && activeLoads) return;
      const generation = ++sessionsGeneration;
      const start = qs("#session-start"), end = qs("#session-end");
      const dateError = getRange() !== "custom" ? ""
        : !start.value || !end.value ? "请选择起始和终止日期。"
        : ![start, end].every(input => /^(?!0000)\d{4}-\d{2}-\d{2}$/.test(input.value) && input.checkValidity()) ? "请选择有效日期（年份 0001–9999）。"
        : start.value > end.value ? "起始日期不能晚于终止日期。" : "";
      if (dateError) {
        sessionsView.status = "error";
        renderSessionState("error", dateError);
        return;
      }
      activeLoads += 1;
      if (!background || !hasLoaded || sessionsView.status !== "ready") {
        sessionsView.status = "loading";
        renderSessionState("loading");
      }
      try {
        if (force === true) await refreshStatistics({
          isCurrent: () => pageCurrent() && generation === sessionsGeneration,
          onProgress: () => readData(true),
        });
        if (!pageCurrent() || generation !== sessionsGeneration) return;
        await readData(background);
        if (!pageCurrent() || generation !== sessionsGeneration) return;
        const status = await api("/api/sync-status");
        if (!pageCurrent() || generation !== sessionsGeneration) return;
        renderSyncStatus(status);
        if (force !== true && (status.syncing || status.queued_refresh)) {
          const terminal = await waitForSyncTerminal(status, {
            waitForIdle: true,
            isCurrent: () => pageCurrent() && generation === sessionsGeneration,
            onProgress: () => readData(true, true),
          });
          if (terminal.polling_error) throw new Error(terminal.polling_error);
          if (pageCurrent() && generation === sessionsGeneration) await readData(true, true);
        }
      } catch (error) {
        if (!pageCurrent() || generation !== sessionsGeneration) return;
        sessionsView.status = "error";
        renderSessionState("error", `会话加载失败：${error.message || error}`, load);
      } finally {
        activeLoads -= 1;
      }
      async function readData(keepPage, observeOnly = false) {
        const payload = await api("/api/sessions", {
            range: getRange(),
            start: params().get("start"), end: params().get("end"),
            with_filters: "1", ...sessionsView.filters,
            sort: qs("#sort") ? qs("#sort").value : "time",
            order: "desc",
            sync: force === true || observeOnly ? "0" : undefined,
          });
        const data = normalizeSessionsPayload(payload.sessions || payload);
        if (!isValidSessionsPayload(data)) {
          throw new Error("会话数据包含无效行。");
        }
        if (!pageCurrent() || generation !== sessionsGeneration) {
          return;
        }
        sessionsView.rows = data;
        hasLoaded = true;
        if (!keepPage) sessionsView.page = 0;
        populateSessionFilters(data, payload.filters);
        sessionsView.status = "ready";
        setSessionFiltersDisabled(false);
        renderSessions();
      }
    }
    const dateControls = qs("#session-dates");
    const syncDates = () => { if (dateControls) dateControls.hidden = getRange() !== "custom"; };
    [["start", "#session-start"], ["end", "#session-end"]].forEach(([key, selector]) => {
      const input = qs(selector);
      if (!input) return;
      input.value = params().get(key) || (params().get("range") === "custom" ? "" : new Date().toLocaleDateString("en-CA", {timeZone: "Asia/Shanghai"}));
      input.addEventListener("change", () => { setParam(key, input.value); load(false); });
    });
    qs("#range")?.addEventListener("change", () => {
      syncDates();
      if (getRange() === "custom") {
        setParam("start", qs("#session-start").value);
        setParam("end", qs("#session-end").value);
      }
    });
    bindShell(load);
    syncDates();
    // Page-scoped: this poll belongs to the Sessions tab, and without the
    // registry it would keep reloading sessions from under whichever tab the
    // reader moved on to, once per visit.
    watchPageData(() => load(false, true));
    const sort = qs("#sort");
    if (sort) {
      sort.addEventListener("change", () => { sessionsView.page = 0; load(false); });
    }
    SESSION_FILTERS.forEach((filter) => {
      const select = qs(filter.select);
      if (select) {
        select.addEventListener("change", () => {
          if (sessionsView.status !== "ready") {
            return;
          }
          sessionsView.filters[filter.key] = select.value;
          sessionsView.page = 0;
          load(false);
        });
      }
    });
    ["", "-top"].forEach(suffix => {
      qs(`#page-prev${suffix}`)?.addEventListener("click", () => turnSessionPage(-1));
      qs(`#page-next${suffix}`)?.addEventListener("click", () => turnSessionPage(1));
    });
    await load(false, true);
  }

  function turnSessionPage(step) {
    if (sessionsView.status !== "ready") {
      return;
    }
    const total = filteredSessions().length;
    const lastPage = Math.max(0, Math.ceil(total / SESSIONS_PAGE_SIZE) - 1);
    sessionsView.page = Math.min(lastPage, Math.max(0, sessionsView.page + step));
    renderSessions();
    const wrap = qs(".table-wrap.sticky-head");
    if (wrap) {
      wrap.scrollTop = 0;
    }
  }

  // Options describe usage events in the date window, before dimension filters.
  // Retain an unmatched selection so an empty result never silently broadens it.
  function populateSessionFilters(rows, available) {
    SESSION_FILTERS.forEach((filter) => {
      const select = qs(filter.select);
      if (!select) {
        return;
      }
      const previous = sessionsView.filters[filter.key];
      const values = Array.from(new Set([...(filter.values || available?.[filter.key] || rows.map(row => row[filter.field])), previous].filter(Boolean))).sort();
      const keep = previous;
      const label = select.options[0] ? select.options[0].textContent : "All";
      const labels = filter.key === "cost_status" ? values.map(value => value === "unknown" ? "仅未知成本条目" : value) : optionLabels(filter.key, values);
      select.innerHTML =
        `<option value="">${escapeHtml(label)}</option>` +
        values
          .map((value, index) => `<option value="${escapeHtml(value)}">${escapeHtml(labels[index])}</option>`)
          .join("");
      select.value = keep;
      sessionsView.filters[filter.key] = keep;
    });
  }

  function setSessionFiltersDisabled(disabled) {
    SESSION_FILTERS.forEach((filter) => {
      const select = qs(filter.select);
      if (select) {
        select.disabled = disabled;
      }
    });
  }

  function isValidSessionsPayload(data) {
    const textFields = ["session_id", "agent_id", "project", "model", "started_at"];
    const numberFields = ["tokens", "usage_events"];
    return (
      Array.isArray(data) &&
      data.every(
        (row) =>
          row &&
          typeof row === "object" &&
          !Array.isArray(row) &&
          textFields.every((field) => typeof row[field] === "string" && row[field]) &&
          numberFields.every((field) => Number.isFinite(row[field])) &&
          (row.cost_usd === null || Number.isFinite(row.cost_usd)) &&
          typeof row.estimated === "boolean"
      )
    );
  }

  function normalizeSessionsPayload(data) {
    if (!Array.isArray(data)) {
      return data;
    }
    return data.map((row) => {
      if (
        row &&
        typeof row === "object" &&
        !Array.isArray(row) &&
        !Number.isFinite(row.usage_events) &&
        Number.isFinite(row.messages)
      ) {
        return Object.assign({}, row, { usage_events: row.messages });
      }
      return row;
    });
  }

  // Shortening a project to its trailing segments can map two different paths
  // onto one label, and an <option> has nowhere to put the full value — the
  // reader would face two identical entries with no way to choose. Where that
  // happens, those entries keep their full path.
  function optionLabels(key, values) {
    if (key !== "project") {
      return values.slice();
    }
    const short = values.map((value) => projectLabel(value));
    const seen = short.reduce((counts, label) => {
      counts[label] = (counts[label] || 0) + 1;
      return counts;
    }, {});
    return short.map((label, index) => (seen[label] > 1 ? values[index] : label));
  }

  function filteredSessions() {
    return sessionsView.rows;
  }

  // Group the complete filtered result before rendering: a date total must not
  // depend on the old 100-row page boundary. A cross-day session belongs once,
  // to the date of its last matching usage event.
  function sessionDateGroups(rows) {
    const groups = new Map();
    rows.forEach(row => {
      const day = (row.ended_at && dayKey(row.ended_at)) || "日期未知";
      if (!groups.has(day)) groups.set(day, { day, rows: [], tokens: 0, cost: null, partial: false });
      const group = groups.get(day);
      group.rows.push(row);
      group.tokens += row.tokens;
      if (Number.isFinite(row.cost_usd)) group.cost = (group.cost ?? 0) + row.cost_usd;
      if (row.cost_usd == null || row.unpriced_records > 0) group.partial = true;
    });
    return [...groups.values()];
  }

  function renderSessions() {
    const tbody = qs("#sessions-body");
    const matched = filteredSessions();
    const grouped = (qs("#sort")?.value || "time") === "time";
    const lastPage = Math.max(0, Math.ceil(matched.length / SESSIONS_PAGE_SIZE) - 1);
    sessionsView.page = grouped ? 0 : Math.min(sessionsView.page, lastPage);
    const listParams = params();
    SESSION_FILTERS.forEach(({key}) => {
      if (sessionsView.filters[key]) listParams.set(key, sessionsView.filters[key]);
      else listParams.delete(key);
    });
    listParams.set("page", String(sessionsView.page));
    listParams.set("sort", qs("#sort")?.value || "time");
    replaceCurrentUrl("/sessions?" + listParams);
    const start = sessionsView.page * SESSIONS_PAGE_SIZE;
    const filtering = Object.values(sessionsView.filters).some(Boolean);
    if (!matched.length) {
      renderSessionState("empty", filtering ? "没有符合筛选条件的会话。" : "此范围内没有会话。");
      qs("#session-count").textContent = filtering ? "找到 0 个匹配会话" : "0 个会话";
      return;
    }
    tbody.innerHTML = "";
    tbody.setAttribute("aria-busy", "false");
    const appendRow = (row) => {
      const tr = document.createElement("tr");
      tr.className = "session-row";
      tr.dataset.sessionId = row.session_id;
      tr.dataset.machine = row.machine || "";
      const detailParams = new URLSearchParams(listParams);
      detailParams.set("session", row.session_id);
      detailParams.set("session_machine", row.machine || "");
      detailParams.set("session_agent", row.agent_id);
      tr.innerHTML = `
        <td><span class="pill agent-${escapeHtml(row.agent_id)}">${escapeHtml(row.agent_id)}</span><br><small>${escapeHtml(row.machine || "本机")}</small></td>
        <td class="project-cell"><a class="session-title" href="/sessions?${escapeHtml(detailParams.toString())}" title="${escapeHtml(row.title || row.project)}">${escapeHtml(row.title || projectLabel(row.project))}</a>${row.title ? `<small>${escapeHtml(projectLabel(row.project))}</small>` : ""}${row.subagent_count ? `<small>含 ${integer(row.subagent_count)} 个子代理</small>` : ""}</td>
        <td class="nowrap">${grouped ? escapeHtml(timeOfDay(row.ended_at)) : formatDate(row.ended_at)}</td>
        <td class="numeric">${sessionEntryCost(row.cost_usd)}${row.unpriced_records && row.cost_usd != null ? '<br><small>部分未计价</small>' : ""}</td>
        <td class="numeric">${integer(row.tokens)}</td>
      `;
      tr.addEventListener("click", (event) => {
        if (!event.target.closest("a, button") && !window.getSelection()?.toString()) tr.querySelector("a").click();
      });
      tbody.appendChild(tr);
    };
    if (grouped) {
      const groups = sessionDateGroups(matched);
      groups.forEach((group, index) => {
        const expanded = sessionsView.expandedDays.has(group.day) ? sessionsView.expandedDays.get(group.day) : index === 0;
        const heading = document.createElement("tr");
        heading.className = "date-group";
        heading.dataset.day = group.day;
        const cell = document.createElement("th");
        cell.colSpan = 5;
        cell.scope = "colgroup";
        const toggle = document.createElement("button");
        toggle.type = "button";
        toggle.className = "session-day-toggle quota-toggle";
        toggle.setAttribute("aria-expanded", String(expanded));
        toggle.innerHTML = `<span>${escapeHtml(group.day)}</span><span>${integer(group.rows.length)} 个会话</span><span class="session-day-total">${sessionEntryCost(group.cost)}${group.partial && group.cost !== null ? ' · 部分未计价' : ''} · ${integer(group.tokens)} tokens</span>`;
        toggle.addEventListener("click", () => {
          sessionsView.expandedDays.set(group.day, !expanded);
          renderSessions();
          [...tbody.querySelectorAll(".date-group")].find(row => row.dataset.day === group.day)?.querySelector("button").focus({ preventScroll: true });
        });
        cell.appendChild(toggle);
        heading.appendChild(cell);
        tbody.appendChild(heading);
        if (expanded) {
          const limit = sessionsView.dayLimits.get(group.day) || SESSIONS_PAGE_SIZE;
          group.rows.slice(0, limit).forEach(appendRow);
          if (group.rows.length > limit) {
            const moreRow = document.createElement("tr");
            moreRow.className = "session-day-more";
            const moreCell = document.createElement("td");
            moreCell.colSpan = 5;
            const more = document.createElement("button");
            more.type = "button";
            more.textContent = `再显示 ${integer(Math.min(SESSIONS_PAGE_SIZE, group.rows.length - limit))} 个会话（已显示 ${integer(limit)} / ${integer(group.rows.length)}）`;
            more.addEventListener("click", () => {
              sessionsView.dayLimits.set(group.day, limit + SESSIONS_PAGE_SIZE);
              renderSessions();
              [...tbody.querySelectorAll(".date-group")].find(row => row.dataset.day === group.day)?.querySelector("button").focus({ preventScroll: true });
            });
            moreCell.appendChild(more);
            moreRow.appendChild(moreCell);
            tbody.appendChild(moreRow);
          }
        }
      });
      ["", "-top"].forEach(suffix => {
        const status = qs(`#page-status${suffix}`);
        if (status) status.textContent = `${integer(groups.length)} 个日期 · 共 ${integer(matched.length)} 个会话`;
        for (const direction of ["prev", "next"]) {
          const button = qs(`#page-${direction}${suffix}`);
          if (button) button.disabled = true;
        }
      });
    } else {
      const pageRows = matched.slice(start, start + SESSIONS_PAGE_SIZE);
      pageRows.forEach(appendRow);
      renderSessionPager(matched.length, start, pageRows.length);
    }
    syncStickyHeaderOffset();
    qs("#session-count").textContent = filtering ? `找到 ${integer(matched.length)} 个匹配会话` : `${integer(matched.length)} 个会话`;
  }

  function renderSessionState(state, message, load) {
    const tbody = qs("#sessions-body");
    if (!tbody) {
      return;
    }
    tbody.innerHTML = "";
    tbody.setAttribute("aria-busy", state === "loading" ? "true" : "false");
    if (state === "loading" || state === "error") {
      setSessionFiltersDisabled(true);
    }
    const row = document.createElement("tr");
    row.dataset.sessionState = state;
    const cell = document.createElement("td");
    cell.colSpan = 5;
    cell.className = state === "error" ? "error" : "empty-state";
    const text = message || "加载中…";
    cell.appendChild(document.createTextNode(text));
    if (state === "error" && load) {
      const retry = document.createElement("button");
      retry.type = "button";
      retry.textContent = "重试";
      retry.addEventListener("click", load);
      cell.appendChild(document.createTextNode(" "));
      cell.appendChild(retry);
    }
    row.appendChild(cell);
    tbody.appendChild(row);

    const count = qs("#session-count");
    if (count) {
      count.textContent = state === "loading" ? "加载中…" : state === "error" ? "会话不可用" : "0 个会话";
    }
    ["", "-top"].forEach(suffix => {
      const status = qs(`#page-status${suffix}`);
      if (status) status.textContent = text;
      for (const direction of ["prev", "next"]) {
        const button = qs(`#page-${direction}${suffix}`);
        if (button) button.disabled = true;
      }
    });
    syncStickyHeaderOffset();
  }

  // The date headings stick directly beneath the column header, so they need
  // its rendered height — which moves with font metrics and zoom — rather than
  // a constant chosen when the stylesheet was written.
  function syncStickyHeaderOffset() {
    const wrap = qs(".table-wrap.sticky-head");
    const head = wrap && wrap.querySelector("thead");
    if (!wrap || !head) {
      return;
    }
    wrap.style.setProperty("--sessions-head-h", Math.round(head.getBoundingClientRect().height) + "px");
  }

  function renderSessionPager(total, start, shown) {
    ["", "-top"].forEach(suffix => {
      const status = qs(`#page-status${suffix}`);
      const prev = qs(`#page-prev${suffix}`);
      const next = qs(`#page-next${suffix}`);
      if (status) status.textContent = total
        ? `第 ${integer(start + 1)}–${integer(start + shown)} 条，共 ${integer(total)} 条`
        : "没有符合筛选条件的会话";
      if (prev) prev.disabled = sessionsView.page === 0;
      if (next) next.disabled = start + shown >= total;
    });
  }

  // Transcript identifiers are local/provider IDs, not Gateway request IDs.
  async function initSessionDetail() {
    const current = pageScope();
    const selected = params();
    const id = selected.get("session");
    const machine = selected.get("session_machine") || "";
    const back = new URLSearchParams(selected);
    ["session", "session_machine", "session_agent", "scope", "usage_model", "usage_page"].forEach(key => back.delete(key));
    qs("#session-list-panel").hidden = true;
    qs("main h1").textContent = "会话详情";
    ["#range", "#sort"].forEach(selector => { const el = qs(selector); if (el) el.hidden = true; });
    const panel = document.createElement("section");
    panel.className = "session-detail";
    panel.innerHTML = `<a class="session-back" href="/sessions?${escapeHtml(back.toString())}">返回会话列表</a>
      <div class="panel session-identity"><div><h2 id="session-project">会话</h2><p id="session-times"></p><p>${escapeHtml(machine || "本机")} · <span id="session-scope"></span></p><button id="session-scope-toggle" type="button"></button><details class="note-disclosure"><summary>会话标识与记录范围</summary><div class="session-id-line"><span>ID</span><code id="session-identity"></code><button id="session-copy" type="button">复制 ID</button></div><p class="scope-note">仅含已采集的用量和会话标题，不含对话正文。<a data-collection-link href="${escapeHtml(collectionUrl())}">采集与诊断</a></p></details></div></div>
      <div id="session-detail-content" aria-live="polite"></div>`;
    qs("main").appendChild(panel);
    qs("#session-identity").textContent = id;
    qs("#session-copy").addEventListener("click", async (event) => {
      await copyText(event.currentTarget, qs("#session-identity").textContent, "复制失败，请选中 ID 复制");
    });
    const readingState = {expanded: new Set(), technical: new Set(), metadata: false, usage: false, executions: false};
    let generation = 0;
    let activeLoads = 0;
    async function load(force = false, background = false) {
      if (background && activeLoads) return;
      activeLoads++;
      const ticket = ++generation;
      const content = qs("#session-detail-content");
      if (!background) content.innerHTML = '<p class="panel status-line" role="status">正在读取会话…</p>';
      try {
        if (force === true) await refreshStatistics({isCurrent: () => current() && ticket === generation});
        if (!current() || ticket !== generation) return;
        const detailQuery = Object.fromEntries(params());
        detailQuery.machine = machine;
        const all = detailQuery.scope === "all";
        qs("#session-scope").textContent = all ? "全部留存记录" : "与列表相同的日期和筛选";
        qs("#session-scope-toggle").textContent = all ? "返回列表筛选范围" : "查看全部留存记录";
        const detail = await api("/api/session/" + encodeURIComponent(id), detailQuery);
        if (!current() || ticket !== generation) return;
        if (!Array.isArray(detail.entries)) throw new Error("会话数据格式无效");
        if (detail.meta?.session_id) qs("#session-identity").textContent = detail.meta.session_id;
        renderSessionDetail(detail.entries, content, readingState, detail.meta || {});
      } catch (error) {
        if (!current() || ticket !== generation) return;
        content.innerHTML = `<div class="panel"><p class="error">会话读取失败：${escapeHtml(error.message || error)}</p><button id="session-retry" type="button">重试</button></div>`;
        qs("#session-retry").addEventListener("click", load);
      } finally {
        activeLoads--;
      }
    }
    qs("#session-scope-toggle").addEventListener("click", () => {
      const next = params();
      if (next.get("scope") === "all") next.delete("scope"); else next.set("scope", "all");
      next.delete("usage_page"); next.delete("usage_model");
      replaceCurrentUrl("/sessions?" + next);
      load(false);
    });
    bindShell(load, {range: false});
    watchPageData(() => load(false, true));
    await load();
  }

  function sessionEntryCost(value) {
    if (value === null || value === undefined) return "未知";
    if (value > 0 && value < 0.0001) return "$" + Number(value).toPrecision(3);
    return moneyPrecise(value);
  }

  function renderSessionDetail(entries, content, readingState = {expanded: new Set(), technical: new Set(), metadata: false}, meta = {}) {
    if (!entries.length) {
      content.innerHTML = '<div class="panel empty-state">此范围内没有留存用量。可切换全部留存记录，或返回列表调整筛选。</div>';
      return;
    }
    const executionIds = readingState.executionIds || (readingState.executionIds = new Set());
    const distinct = key => [...new Set(entries.map(entry => entry[key]).filter(Boolean))];
    const models = distinct("model");
    const tokens = entries.reduce((n, e) => n + ["input_tokens", "output_tokens", "cache_creation_tokens", "cache_read_tokens"].reduce((sum, key) => sum + (Number(e[key]) || 0), 0), 0);
    const unknown = entries.filter(e => e.cost_usd == null).length;
    const cost = unknown === entries.length ? null : entries.reduce((n, e) => n + (e.cost_usd || 0), 0);
    const events = entries.reduce((n, e) => n + (Number(e.usage_event_count) || 0), 0);
    const dates = entries.map(e => new Date(e.timestamp).getTime()).filter(Number.isFinite);
    const first = dates.length ? dates.reduce((a, b) => Math.min(a, b)) : null;
    const last = dates.length ? dates.reduce((a, b) => Math.max(a, b)) : null;
    const span = first === null ? "未知" : last - first < 60000 ? "少于 1 分钟" : integer(Math.round((last - first) / 60000)) + " 分钟";
    const field = (name, value) => `<div><dt>${name}</dt><dd>${escapeHtml(value || "—")}</dd></div>`;
    const costBasis = entry => entry.cost_usd == null ? "金额未知" :
      ({recorded: "日志记录金额", nominal: "按配置价格估算"}[entry.cost_provenance?.basis] || "金额依据未知");
    const qualityText = entry => entry.agent_id !== "claude-code" ? "此质量分类不适用于 Codex" :
      ({observed: "有结束标记，最终用量仍未确认", incomplete: "不完整：只计已记录 token", unknown: "用量记录完整性未知"}[entry.usage_quality] || "用量记录完整性未知");
    const costReason = reason => ({usage_incomplete: "用量记录不完整", unknown_model: "模型价格未知",
      missing_rates: "缺少适用费率", unsupported_service_context: "服务模式无法定价",
      conflicting_service_context: "服务模式记录冲突", mode_unreported: "部分服务模式未记录，按标准模式估值",
      invalid_recorded_cost: "源记录金额无效"}[reason] || "计价原因未识别");
    qs("#session-project").textContent = meta.title || projectLabel(meta.project || distinct("project").join("、")) || "会话目录未记录";
    qs("#session-times").textContent = `最近用量：${last === null ? "时间未知" : formatDate(last)}`;
    content.innerHTML = `<div class="session-metrics">
      <article><h3>记录费用</h3><strong>${sessionEntryCost(cost)}</strong><p>${cost === null ? "当前记录均无法计价" : unknown ? "部分未计价 · 仅合计可计价记录 · 非账单" : "仅合计可计价记录 · 非账单"}</p></article>
      <article><h3>Token</h3><strong>${integer(tokens)}</strong><p>已记录的输入、输出与缓存合计</p></article></div>
      <details class="panel session-metadata" ${readingState.metadata ? "open" : ""}><summary>会话信息 · ${models.length} 个模型</summary><dl class="session-fields">
        ${field("Agent", distinct("agent_id").join("、"))}
        ${field("会话目录", meta.project || distinct("project").join("、"))}
        ${field("模型", models.join("、"))}
        ${field("创建时间", meta.created_at ? formatDate(meta.created_at) : "源数据未提供")}
        ${field("当前范围首条用量", first === null ? "未知" : formatDate(first))}
        ${field("观测跨度（非活跃时长）", span)}
        ${field("用量事件（非请求次数）", integer(events))}
        ${unknown ? field("未计价记录", integer(unknown)) : ""}
        ${meta.relationship_status && meta.relationship_status !== "linked" ? field("关联信息", ({missing_parent: "父会话未采集，当前记录独立显示", conflict: "父关系冲突，当前记录独立显示", cycle: "父关系循环，当前记录独立显示"}[meta.relationship_status] || "未取得完整父关系，当前记录独立显示")) : ""}
      </dl><p class="scope-note"><strong>模型与计价：</strong>Codex 模型是请求或配置值，实际路由模型未知；估值使用计算时价格，非历史账单。未知费用不归零。</p></details>
      ${(meta.executions || []).length > 1 || meta.subagent_count ? `<details class="panel session-executions" ${readingState.executions ? "open" : ""}><summary>主会话与子代理用量</summary><p class="scope-note">仅合计当前范围内的匹配记录。</p><div class="table-wrap"><table><thead><tr><th>执行会话</th><th>费用</th><th>Token</th></tr></thead><tbody>${(meta.executions || []).map(e => `<tr><td>${escapeHtml(e.is_main ? "主会话" : e.title || "子代理")}<details data-execution-key="${escapeHtml(JSON.stringify([e.agent_id, e.session_id]))}" ${executionIds.has(JSON.stringify([e.agent_id, e.session_id])) ? "open" : ""}><summary>执行标识</summary><code>${escapeHtml(e.session_id)}</code>${e.parent_session_id ? `<p>父 ID：<code>${escapeHtml(e.parent_session_id)}</code></p>` : ""}</details></td><td>${sessionEntryCost(e.cost_usd)}${e.unpriced_records && e.cost_usd != null ? "（部分未计价）" : ""}</td><td>${integer(e.tokens)}</td></tr>`).join("")}</tbody></table></div></details>` : ""}
      <details class="panel" id="usage-section" ${readingState.usage ? "open" : ""}><summary id="usage-heading">用量记录 · ${integer(entries.length)} 条</summary><div class="panel-head"><span id="usage-count" class="status-line"></span></div>
      <div class="controls"><div class="field"><label for="usage-model">模型</label><select id="usage-model"><option value="">全部模型</option>${models.map(m => `<option value="${escapeHtml(m)}">${escapeHtml(m)}</option>`).join("")}</select></div><button id="usage-clear" type="button">清除筛选</button></div>
      <div class="table-wrap"><table><thead><tr><th>记录时间</th><th>模型</th><th class="numeric">输入</th><th class="numeric">输出</th><th class="numeric">缓存读取 / 写入</th><th class="numeric">成本</th><th>详情</th></tr></thead><tbody id="usage-body"></tbody></table></div>
      <div class="pager"><button id="usage-prev" type="button">上一页</button><span id="usage-status" role="status"></span><button id="usage-next" type="button">下一页</button></div></details>`;
    qs(".session-metadata")?.addEventListener("toggle", event => { readingState.metadata = event.currentTarget.open; });
    qs("#usage-section")?.addEventListener("toggle", event => { readingState.usage = event.currentTarget.open; });
    qs(".session-executions")?.addEventListener("toggle", event => { readingState.executions = event.currentTarget.open; });
    qsa(".session-executions [data-execution-key]").forEach(el => el.addEventListener("toggle", () => {
      if (el.open) executionIds.add(el.dataset.executionKey); else executionIds.delete(el.dataset.executionKey);
    }));
    const entryKey = e => JSON.stringify([e.timestamp, e.agent_id, e.session_id, e.message_id, e.request_id, e.model, e.project]);
    let page = Math.max(0, parseInt(params().get("usage_page"), 10) || 0);
    const select = qs("#usage-model");
    select.value = models.includes(params().get("usage_model")) ? params().get("usage_model") : "";
    function render() {
      const rows = entries.filter(e => !select.value || e.model === select.value);
      qs("#usage-clear").hidden = !select.value;
      page = Math.min(page, Math.max(0, Math.ceil(rows.length / 50) - 1));
      const next = params();
      if (select.value) next.set("usage_model", select.value); else next.delete("usage_model");
      next.set("usage_page", String(page));
      replaceCurrentUrl("/sessions?" + next);
      qs("#usage-count").textContent = `${rows.length} / ${entries.length} 条记录`;
      qs("#usage-body").innerHTML = rows.slice(page * 50, (page + 1) * 50).map((e, index) => `<tr>
        <td data-label="记录时间" class="nowrap">${escapeHtml(formatDate(e.timestamp))}</td><td data-label="模型">${escapeHtml(e.model)}</td>
        <td data-label="输入" class="numeric">${integer(e.input_tokens)}</td><td data-label="输出" class="numeric">${integer(e.output_tokens)}</td>
        <td data-label="缓存读取 / 写入" class="numeric">${integer(e.cache_read_tokens)} / ${integer(e.cache_creation_tokens)}</td><td data-label="成本" class="numeric">${sessionEntryCost(e.cost_usd)}${e.cost_usd == null ? "" : `<br><small>${({recorded: "日志金额", nominal: "估算"}[e.cost_provenance?.basis] || "依据未知")}</small>`}</td>
        <td><button type="button" data-usage="${index}" aria-expanded="false">用量依据</button></td></tr>`).join("") || '<tr><td colspan="7">没有符合条件的记录。</td></tr>';
      qsa("[data-usage]").forEach(button => {
        const e = rows[page * 50 + Number(button.dataset.usage)];
        const key = entryKey(e);
        function expand() {
          const row = document.createElement("tr");
          row.className = "session-entry-detail";
          row.innerHTML = `<td colspan="7"><div class="session-entry-evidence"><p class="scope-note">${escapeHtml(formatDate(e.timestamp))} · ${escapeHtml(e.model || "模型未记录")}</p><dl class="session-fields">${e.cost_provenance?.reason ? field("计价说明", costReason(e.cost_provenance.reason)) : ""}${field("金额依据", costBasis(e))}${field("用量记录完整性", qualityText(e))}${field("用量事件数", String(e.usage_event_count ?? "未知"))}</dl><details class="entry-technical" ${readingState.technical.has(key) ? "open" : ""}><summary>技术证据 · ID、服务模式与价格来源</summary><dl class="session-fields">${field("执行会话 ID", e.session_id)}${field("消息 ID", e.message_id)}${field("日志请求 ID", e.request_id)}${field("Agent", e.agent_id)}${field("会话目录", e.project)}${field("服务模式（日志值）", e.service_context ? JSON.stringify(e.service_context) : "未记录")}${field("价格来源", e.cost_provenance?.sources?.join("、"))}${field("价格核验日期", e.cost_provenance?.verified_at)}${field("计价时间口径", e.cost_provenance?.time_basis === "valuation_time" ? "计算时价格，非历史实际费率" : "未知")}</dl><p class="scope-note">ID 来自源日志，用于核对原始记录。</p></details></div></td>`;
          row.querySelector("details").addEventListener("toggle", event => {
            if (event.currentTarget.open) readingState.technical.add(key); else readingState.technical.delete(key);
          });
          button.closest("tr").after(row);
          button.setAttribute("aria-expanded", "true");
          button.textContent = "收起";
        }
        if (readingState.expanded.has(key)) expand();
        button.addEventListener("click", () => {
          if (button.getAttribute("aria-expanded") === "true") {
            button.closest("tr").nextElementSibling.remove();
            button.setAttribute("aria-expanded", "false");
            button.textContent = "用量依据";
            readingState.expanded.delete(key);
          } else { readingState.expanded.add(key); expand(); }
        });
      });
      qs("#usage-status").textContent = rows.length ? `第 ${page * 50 + 1}–${Math.min(rows.length, (page + 1) * 50)} 条，共 ${rows.length} 条` : "没有记录";
      qs("#usage-prev").disabled = page === 0;
      qs("#usage-next").disabled = (page + 1) * 50 >= rows.length;
    }
    select.addEventListener("change", () => { page = 0; render(); });
    qs("#usage-clear").addEventListener("click", () => { select.value = ""; page = 0; render(); });
    function turnPage(step) {
      page += step;
      render();
      qs("#usage-heading").focus({preventScroll: true});
      qs("#usage-section").scrollIntoView({block: "start"});
    }
    qs("#usage-prev").addEventListener("click", () => turnPage(-1));
    qs("#usage-next").addEventListener("click", () => turnPage(1));
    render();
  }

  function escapeHtml(value) {
    return String(value || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  // Stale-code watch. Static assets are always served fresh, but the Python
  // process freezes its code at boot — a long-lived daemon can serve outdated
  // logic without any visible signal (the worst case: reading wrong data
  // unknowingly). The server self-reports staleness via /api/health.stale; this
  // banner makes it visible on every access path, including a long-open tab or a
  // phone on the Tailnet that never went through `agent-monitor open`.
  let pageWebSignature = null;
  let pageServerInstance = null;
  async function pollFreshness() {
    try {
      const url = new URL("/api/health", window.location.origin);
      url.searchParams.set("asset_watch", "1");
      const res = await fetch(url, { cache: "no-store" });
      if (!res.ok) {
        return;
      }
      const json = await res.json();
      if (pageWebSignature && json.web_signature && json.web_signature !== pageWebSignature) {
        window.location.reload();
        return;
      }
      pageWebSignature = json.web_signature || pageWebSignature;
      if (pageServerInstance && json.instance_id && json.instance_id !== pageServerInstance) {
        const status = await api("/api/sync-status");
        renderSyncStatus(status);
      }
      pageServerInstance = json.instance_id || pageServerInstance;
      if (json && json.stale) {
        showStaleBanner();
      }
    } catch (e) {
      /* transient; try again next tick */
    }
  }

  function showStaleBanner() {
    if (qs("#stale-banner")) {
      return;
    }
    const banner = document.createElement("div");
    banner.id = "stale-banner";
    banner.className = "stale-banner";
    banner.innerHTML =
      '<span>服务代码已更新，当前页面数据可能来自旧版本。</span>' +
      '<button type="button" id="stale-restart">重启并刷新</button>' +
      '<span class="stale-hint">或在终端运行 <code>agent-monitor restart</code></span>';
    document.body.insertAdjacentElement("afterbegin", banner);
    const button = qs("#stale-restart", banner);
    if (button) {
      button.addEventListener("click", () => restartAndReload(button));
    }
  }

  async function restartAndReload(button) {
    button.disabled = true;
    button.textContent = "重启中…";
    try {
      const res = await fetch(new URL("/api/restart", window.location.origin), {
        method: "POST", headers: { "X-Agent-Monitor": "action" },
      });
      const json = await res.json().catch(() => ({}));
      if (json && json.restarting === false) {
        button.disabled = false;
        button.textContent = "重启失败：新代码有语法错误";
        return;
      }
    } catch (e) {
      /* connection reset is expected: the server is re-exec'ing */
    }
    await waitForHealthy();
    window.location.reload();
  }

  async function waitForHealthy() {
    for (let attempt = 0; attempt < 40; attempt += 1) {
      await new Promise((resolve) => setTimeout(resolve, 500));
      try {
        const url = new URL("/api/health", window.location.origin);
        url.searchParams.set("asset_watch", "1");
        const res = await fetch(url, { cache: "no-store" });
        if (res.ok) {
          const json = await res.json();
          if (json && !json.stale) {
            return;
          }
        }
      } catch (e) {
        /* still restarting */
      }
    }
  }

  function startFreshnessWatch() {
    pollFreshness();
    // Deliberately not a page timer. It watches whether the server's code is
    // newer than what this document loaded, which is a property of the
    // document, not of whichever tab is showing — and restarting it on every
    // navigation would stack one more copy each time.
    setInterval(pollFreshness, 30000);
  }

  // ---------------------------------------------------------------- page lifetime
  //
  // Swapping <main> takes its elements away, and with them every listener bound
  // to one. Timers are what survives: they hold their closures, keep firing
  // against a page that is no longer shown, and each navigation adds another.
  // Anything scheduled for the duration of one tab registers here instead.
  let pageTimers = [];
  let pageCleanups = [];
  // Bumped on every teardown. An init that is still awaiting when the reader
  // clicks another tab finishes into a document that no longer belongs to it —
  // its element ids may be gone (it throws, and the catch writes a failure onto
  // a page that is actually fine) or, worse, shared with the new page
  // (`#sync-coverage` exists on four of the five), where it overwrites live
  // content with no error anywhere. Anything resuming after an await compares
  // this before it writes.
  let pageGeneration = 0;

  function pageIsCurrent(generation) {
    return generation === pageGeneration;
  }

  function pageScope() {
    const generation = pageGeneration;
    return () => pageIsCurrent(generation);
  }

  function watchPageData(reload) {
    const current = pageScope();
    let busy = false;
    const update = async () => {
      if (!current() || document.hidden || busy) return;
      busy = true;
      try { await reload(); } catch (error) { /* Page loaders retain their error surface. */ }
      finally { busy = false; }
    };
    pageInterval(update, 30000);
    window.addEventListener?.("focus", update);
    document.addEventListener?.("visibilitychange", update);
    pageCleanups.push(() => {
      window.removeEventListener?.("focus", update);
      document.removeEventListener?.("visibilitychange", update);
    });
  }

  function pageInterval(handler, ms) {
    // The static contract tests evaluate this file in a bare context whose
    // `window` has no timers. Polling is not what they assert, so its absence
    // is not an error — but it has to be absent quietly, or every page's init
    // throws before reaching the rendering those tests do assert.
    if (!window.setInterval) {
      return null;
    }
    const id = window.setInterval(handler, ms);
    pageTimers.push(id);
    return id;
  }

  function endPageLifetime() {
    pageGeneration += 1;
    pageTimers.forEach((id) => window.clearInterval?.(id));
    pageTimers = [];
    pageCleanups.forEach((cleanup) => cleanup());
    pageCleanups = [];
    // Chart.js keeps its instances reachable from its own registry, so one left
    // behind holds a detached canvas for as long as the document lives.
    Object.keys(charts).forEach((key) => {
      charts[key].destroy();
      delete charts[key];
    });
  }

  // ---------------------------------------------------------------- client navigation
  //
  // The five tabs share a shell and differ only in <main>. Letting the browser
  // navigate rebuilt the whole document each time: ~326KB of JS and CSS
  // re-executed, the webfonts re-applied, the sidebar repainted — for 2.6–10.3KB
  // of markup that actually differed. The flash that produced is what this
  // removes; it is not a data cache, and every page still fetches its own API
  // data exactly as before.
  const CLIENT_ROUTES = new Map([
    ["/", () => initOverview()],
    ["/explore", () => window.AgentMonitorPivot.init()],
    ["/sessions", () => initSessions()],
    ["/network", () => initNetwork()],
    ...Array.from(GUIDE_ROUTES, path => [path, () => initGuide()]),
    ["/collection", () => initCollection()],
  ]);

  // Path *and* query. Two history entries for the same page differing only in
  // range are two different views here — the range is read out of the URL on
  // init — so comparing pathnames alone made Back between them a silent no-op,
  // leaving the address bar and the figures on screen permanently disagreeing.
  let renderedRoute = null;
  let inFlightNavigation = null;

  function routeKey(url) {
    return url.pathname + url.search;
  }

  // The one way to rewrite the current entry's URL without navigating. Route
  // identity has to follow it, or the next popstate measures against a URL that
  // is no longer anywhere in the history — and the key has to be built the same
  // way every time, because `URLSearchParams.toString()` and `location.search`
  // disagree about escaping (a filter value containing `/` is `%2F` from one and
  // literal from the other), which would make two spellings of one URL compare
  // unequal. Exported so `pivot.js`, which also rewrites the URL, goes through
  // here rather than reimplementing the bookkeeping.
  function replaceCurrentUrl(next) {
    window.history.replaceState(null, "", next);
    renderedRoute = routeKey(
      new URL(next, window.location.origin || "http://localhost"),
    );
  }

  function clientRouteFor(url) {
    if (url.origin !== window.location.origin) {
      return null;
    }
    // A fragment is the browser's own job; swallowing it would take away the
    // scroll the reader asked for.
    if (url.hash) {
      return null;
    }
    return CLIENT_ROUTES.get(url.pathname) || null;
  }

  function markActiveNav(pathname) {
    qsa("[data-nav]").forEach((link) => {
      const current = new URL(link.getAttribute("href"), window.location.origin).pathname === pathname;
      link.setAttribute("aria-current", current ? "page" : "false");
    });
  }

  function applyDocument(html, pathname) {
    const parsed = new DOMParser().parseFromString(html, "text/html");
    const nextMain = parsed.querySelector("main");
    const currentMain = document.querySelector("main");
    // A 200 is not the same as a usable page. Checking before the swap is the
    // whole point: afterwards the DOM and the history entry are already
    // replaced, and the only way out is a reload that fetches the same broken
    // response again. Refusing here hands the URL back to the browser.
    if (!nextMain || !currentMain) {
      return false;
    }
    currentMain.replaceWith(nextMain);
    document.title = parsed.title || document.title;
    markActiveNav(pathname);
    updateAreaNavigation();
    return true;
  }

  async function renderRoute(url, { push }) {
    const init = clientRouteFor(url);
    if (!init) {
      return false;
    }

    inFlightNavigation?.abort();
    const navigation = new AbortController();
    inFlightNavigation = navigation;

    let html;
    try {
      const response = await fetch(url.href);
      if (!response.ok) {
        return false;
      }
      html = await response.text();
    } catch (error) {
      return false; // the caller falls back to a real navigation
    }
    if (navigation.signal.aborted) {
      return true; // superseded by a later click, not a failure
    }

    endPageLifetime();
    const generation = pageGeneration;
    if (push) {
      window.history.pushState({ clientNav: true }, "", url.pathname + url.search);
    }
    if (!applyDocument(html, url.pathname)) {
      return false;
    }
    renderedRoute = routeKey(url);
    // Start reading at the new page immediately. Its initializer may keep
    // observing a background collection after useful data is already visible.
    window.scrollTo({ top: 0, behavior: "auto" });

    try {
      await init();
    } catch (error) {
      console.error(error);
      // Only onto the page this init was for. A superseded init throws because
      // its own elements are gone, and reporting that on the page the reader
      // moved to puts a failure banner on a view that loaded perfectly.
      const main = pageIsCurrent(generation) ? document.querySelector("main") : null;
      if (main) {
        main.insertAdjacentHTML(
          "beforeend",
          '<p class="error">页面未能完整加载，请重新加载页面。</p>',
        );
      }
    }
    return true;
  }

  function initClientNavigation() {
    if (document.documentElement.dataset.clientNav === "on") {
      return;
    }
    document.documentElement.dataset.clientNav = "on";
    updateAreaNavigation();
    // Keep the actual href current for native new-tab and copy-link actions too.
    document.addEventListener("toggle", (event) => {
      if (event.target.matches?.("#codex-account-actions, .quota-observation-details")) updateSyncOverviewLink();
    }, true);
    initPageSearch();
    renderedRoute = routeKey(
      new URL(
        window.location.pathname + (window.location.search || ""),
        window.location.origin || "http://localhost",
      ),
    );

    document.addEventListener("click", (event) => {
      const copy = event.target.closest?.("[data-copy-code]");
      if (copy) { copyGuideCode(copy); return; }
      if (event.defaultPrevented || event.button !== 0) {
        return;
      }
      // Modified clicks mean "open in a new tab"; swallowing them takes that away.
      if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) {
        return;
      }
      const link = event.target.closest?.("a[href]");
      if (!link || link.target === "_blank" || link.hasAttribute("download")) {
        return;
      }
      const url = new URL(link.href, window.location.origin);
      if (url.pathname === "/collection" && window.location.pathname !== "/collection"
          && link.id !== "sessions-diagnostics-return") {
        url.searchParams.set("return", currentPageUrl());
        link.href = url.pathname + url.search + url.hash;
      }
      if (!clientRouteFor(url)) {
        return;
      }

      event.preventDefault();
      if (!GUIDE_ROUTES.has(window.location.pathname)) {
        lastConsoleUrl = window.location.pathname + window.location.search + (window.location.hash || "");
      }
      renderRoute(url, { push: true })
        .then((handled) => {
          // Anything this swap cannot apply goes to a real navigation rather
          // than leaving the reader on a page whose sidebar now disagrees with
          // what they clicked.
          if (!handled) {
            window.location.assign(url.href);
          }
        })
        .catch(() => window.location.assign(url.href));
    });

    window.addEventListener("popstate", () => {
      const url = new URL(window.location.href);
      if (routeKey(url) === renderedRoute) {
        // Genuinely the same view; the page already shows it.
        return;
      }
      // Past this line the address bar already shows a different page than the
      // one rendered, so every path from here either renders or reloads.
      renderRoute(url, { push: false }).then((handled) => {
        if (!handled) {
          window.location.reload();
        }
      });
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", startFreshnessWatch);
  } else {
    startFreshnessWatch();
  }

  window.AgentMonitor = {
    costReading,
    costCoverageText,
    formatQuotaReset,
    latestCodexQuotaReading,
    updateCodexQuotaReadings,
    ensureTimezone,
    formatDate,
    onPageCleanup: (cleanup) => pageCleanups.push(cleanup),
    initClientNavigation,
    pageInterval,
    pageScope,
    watchPageData,
    replaceCurrentUrl,
    api,
    autoTimeDim,
    bindShell,
    chart,
    chartOptions,
    barValueLabels,
    chartMoneyTick,
    compactNumber,
    dataset,
    getRange,
    integer,
    money,
    moneyPrecise,
    params,
    palette,
    SERIES_LIMIT,
    qsa,
    qs,
    setParam,
    shortText,
    renderSyncStatus,
    waitForSyncTerminal,
    snapshotFingerprint,
    refreshStatistics,
    pollFreshness,
    initNetwork,
    initGuide,
    initCollection,
    initOverview,
    initSessions,
  };
})();
