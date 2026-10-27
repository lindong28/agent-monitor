// Shell, navigation and cross-page orchestration; page implementations live in modules.
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory;
  else factory(root);
})(globalThis, function createApp(platform = globalThis) {
  const { window, document } = platform;
  const fetch = (...args) => platform.fetch(...args);
  const modules = typeof module === "object" && module.exports
    ? { charts: require("./charts.js"), quota: require("./quota.js"), network: require("./network.js"), sessions: require("./sessions.js") }
    : window.AgentMonitorModules;
  const assetVersion = document.querySelector?.('meta[name="asset-version"]')?.content || "";

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

  const charts = modules.charts({ platform, palette, OTHER_COLOR, qs });
  const { money, moneyPrecise, costCoverageText, costReading, integer, pct, compactNumber, barValueLabels, shortText, chart, seriesColor, dataset, chartOptions, categoryTick, chartMoneyTick, chartReading, costTableReading, renderChartTable, SERIES_LIMIT, destroyCharts } = charts;

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

  const quota = modules.quota({ platform, fmtAbs, qs, api, currentPageUrl, collectionUrl, updateSyncOverviewLink, formatDate });
  const { resetText, latestCodexQuotaReading, updateCodexQuotaReadings, quotaDisplayReading, sortQuotaAccounts, quotaMatches, bindQuotaExplorer, quotaPresence, codexSessionAccountLabel, renderQuotaAccounts, quotaFailureKind, quotaFailureDetails, quotaRefreshNotice, renderQuotaSignedOut, appendRememberedAccounts, quotaBody, quotaAccountRow, quotaProviderCell, quotaPlanCell, quotaPlanSourcesDisagree, quotaAccountCell, quotaRemovalReading, removeRenderedAccount, refreshRememberedAccountSummary, removeRememberedAccount, quotaUsage, quotaBandPill, quotaWindowCell, quotaMeter, quotaResetText, quotaResetState, formatQuotaReset, quotaMachinesCell, quotaObservationAge, quotaUpdatedCell, quotaPlanLabel, quotaAccountName, quotaIsStale, quotaProviderPill, renderQuotaMachineRecords, renderQuotaMachineNotice, quotaWorstUsage, quotaUnavailableBody, quotaFilter, resetQuotaReadings } = quota;

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

  const network = modules.network({ platform, qs, updateNavLinks, api, withRefresh, formatDate, escapeHtml });
  const { initNetwork, renderNetwork, renderNetworkBanner, renderNetworkError, clearNetworkError, setNetworkCardsUnavailable, renderLocalNetwork, renderPublicNetwork, renderRiskNetwork, renderTimezoneNetwork, renderConclusions, renderSectionFailure, sectionMessage, kvRow, renderDns, statusPill } = network;

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
    resetQuotaReadings();
    pageCleanups.push(resetQuotaReadings);
    if (qs("#codex-account-actions")) {
      const currentAccountsPage = pageScope();
      import("/web/codex-accounts.js" + (assetVersion ? `?v=${assetVersion}` : "")).then((module) => {
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

  const sessions = modules.sessions({ platform, qs, qsa, params, getRange, setParam, copyText, api, bindShell, moneyPrecise, integer, projectLabel, codexSessionAccountLabel, collectionUrl, renderSyncStatus, waitForSyncTerminal, refreshStatistics, formatDate, dayKey, timeOfDay, escapeHtml, pageScope, watchPageData, replaceCurrentUrl });
  const { initSessions, turnSessionPage, populateSessionFilters, setSessionFiltersDisabled, isValidSessionsPayload, normalizeSessionsPayload, optionLabels, filteredSessions, sessionDateGroups, renderSessions, renderSessionState, syncStickyHeaderOffset, renderSessionPager, initSessionDetail, sessionEntryCost, renderSessionDetail, sessionsView } = sessions;

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
  let pageWebSignature = assetVersion || null;
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
      '<span class="stale-hint">客户端本机网页：<code>agent-monitor local restart</code>；Hub 或独立模式：<code>agent-monitor restart</code></span>';
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
        button.textContent = res.status === 409 ? "正在部署或重启，请稍后重试" : "重启失败，请检查服务状态";
        button.title = json.error || "";
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
    destroyCharts();
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
    ...charts, ...quota, ...network, ...sessions,
    restartAndReload, updateAreaNavigation, copyGuideCode, showOverviewLoadError,
    renderRoute, renderOverview, collectionUrl, currentPageUrl, renderCostDiagnostics, costDiagnosticsQuery,
    updateSyncOverviewLink, renderRangeCost, endPageLifetime,
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
  return window.AgentMonitor;
});
