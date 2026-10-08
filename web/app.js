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
    const range = getRange();
    function setLinkRange(url) {
      const custom = range === "custom" && url.pathname === "/explore";
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
      link.href = url.pathname + url.search;
      const current = url.pathname === window.location.pathname;
      link.setAttribute("aria-current", current ? "page" : "false");
    });
    qsa("[data-preserve-range]").forEach((link) => {
      const url = new URL(link.getAttribute("href"), window.location.origin);
      setLinkRange(url);
      link.href = url.pathname + url.search;
    });
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
    const response = await fetch(url);
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
    button.textContent = "⟳ 刷新中";
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
    return "$" + Number(value).toLocaleString("en-US", {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    });
  }

  function moneyPrecise(value) {
    if (value === null || value === undefined) {
      return "—";
    }
    return "$" + Number(value).toFixed(4);
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
  function barValueLabels(format) {
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
    "请在提供服务的 agent-monitor checkout 上运行 ./install.sh，然后重新加载。";

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

  function dataset(label, data, index, extra) {
    return Object.assign(
      {
        label,
        data,
        borderColor: seriesColor(index),
        backgroundColor: seriesColor(index),
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
        ticks: { ...axis.ticks, maxRotation: 0, autoSkip: true, ...specific.ticks },
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
    chart("dailyCost", "daily-cost-chart", {
      type: "line",
      data: {
        labels: costHistory.rows.map((row) => row.x),
        datasets: costHistory.columns.map((column, index) =>
          dataset(agentLabel(column), costHistory.rows.map((row) => row.values[column]), index, {
            fill: false,
            spanGaps: true,
          })
        ),
      },
      options: chartOptions({ scales: { x: { ticks: { maxTicksLimit: 7,
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

    const projects = data.top_projects_week.slice();
    chart("topProjects", "top-projects-chart", {
      type: "bar",
      data: {
        labels: projects.map((row) => projectLabel(row.project)),
        datasets: [dataset("成本", projects.map((row) => row.cost_usd), 0)],
      },
      options: chartOptions({
        indexAxis: "y",
        // One series: the panel heading already names it, so a legend box would
        // only repeat the title. Bar ends carry the values instead.
        plugins: {
          legend: { display: false },
          tooltip: { callbacks: { title: (items) => projects[items[0].dataIndex].project } },
        },
        scales: { x: { beginAtZero: true }, y: { ticks: { autoSkip: false } } },
        layout: { padding: { right: 64 } },
      }),
      plugins: [barValueLabels(money)],
    });

    // Model mix is a magnitude comparison across models. Stacking eight series
    // onto a single "This month" column made five of them thinner than a pixel
    // while each still claimed a legend entry; ranked horizontal bars let every
    // model be read and compared directly.
    const mix = data.model_mix_month.slice().sort((a, b) => Number(b.tokens) - Number(a.tokens));
    chart("modelMix", "model-mix-chart", {
      type: "bar",
      data: {
        labels: mix.map((row) => row.model),
        datasets: [dataset("token", mix.map((row) => row.tokens), 0)],
      },
      options: chartOptions({
        indexAxis: "y",
        plugins: { legend: { display: false } },
        scales: {
          x: { beginAtZero: true, ticks: { callback: (value) => compactNumber(value) } },
          y: { ticks: { autoSkip: false } },
        },
        layout: { padding: { right: 64 } },
      }),
      plugins: [barValueLabels(compactNumber)],
    });
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
      "7d": "7d",
      "30d": "30d",
      "90d": "90d",
      "6m": "6m",
      "1y": "1y",
      "2y": "2y",
      all: "全部历史",
    };
    return labels[value] || value || "30d";
  }

  function rangeSummaryLabel(value) {
    const labels = {
      "7d": "近 7 天",
      "30d": "近 30 天",
      "90d": "近 90 天",
      "6m": "近 6 个月",
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

  // An agent keeps one colour everywhere, so these reuse the .pill classes the
  // charts and tables already use rather than introducing a second scheme.
  // Each window owns a fixed column for every row, regardless of which windows a
  // provider reports. Codex has no 5h, so its 5h cell says "n/a" (see
  // quotaWindowCell — an em dash there would mean something else) rather than
  // sliding its 7d leftward: a flowed layout did that, putting Codex's 7d and
  // Claude's 5h in one column and moving the two 7d figures apart, which are the
  // ones that need comparing.
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
    // Codex reports no 5h window in current sessions, so its 5h cell says so
    // rather than carrying a value.
    { key: "codex", label: "Codex", pill: "agent-codex", windows: [QUOTA_WINDOW_7D] },
  ];
  let renderedQuotaRateLimits = null;
  let codexQuotaReadings = [];
  const quotaFilter = { provider: "all", search: "" };
  let boundQuotaExplorer = null;

  function latestCodexQuotaReading(account) {
    return [account.operation?.after, account.batch_result?.after, account.last_quota]
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
    if (!table) {
      return;
    }
    renderedQuotaRateLimits = rateLimits;
    bindQuotaExplorer();
    const rememberedExpanded = options.rememberedExpanded ?? Boolean(qs("#quota-past-accounts")
      && !qs("#quota-past-accounts").hidden);
    // Only the bodies are ours; the header row is in the markup.
    Array.from(table.tBodies).forEach((body) => body.remove());

    const remembered = [];
    const allEntries = QUOTA_PROVIDERS.flatMap((provider) =>
      (rateLimits?.[provider.key]?.accounts || []).map((account) => ({ provider, account })));
    const matches = allEntries.filter(({ provider, account }) => quotaMatches(provider, account));
    const count = qs("#quota-match-count");
    if (count) {
      const known = matches.filter(({ account }) => account.account_state === "known");
      const current = known.filter(({ account }) => quotaPresence(account) === "in_use").length;
      const history = known.filter(({ account }) => quotaPresence(account) === "remembered").length;
      count.textContent = `匹配 ${matches.length} / ${allEntries.length} 条记录 · 在用账号 ${current} · 历史账号 ${history} · 账号未知的机器记录 ${matches.length - known.length}`;
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
      const accounts = sourceAccounts.filter((account) => quotaMatches(provider, account));
      const live = accounts.filter((account) => quotaPresence(account) === "in_use");
      const named = live.filter((account) => account.account_state === "known");
      const unknown = live.filter((account) => account.account_state !== "known");
      if (named.length) {
        table.appendChild(quotaBody(provider, named));
      }
      if (unknown.length === 1) {
        table.appendChild(quotaBody(provider, unknown));
      }
      accounts
        .filter((account) => quotaPresence(account) === "remembered")
        .forEach((account) => remembered.push({ provider, account }));
    });

    // Rows without an account are one per machine, not one per account. Keep
    // larger groups collapsed, but still inside the live section so no current
    // machine falls into the past-account disclosure.
    QUOTA_PROVIDERS.forEach((provider) => {
      const accounts = rateLimits?.[provider.key]?.accounts || [];
      const unknown = accounts.filter(
        (account) => quotaMatches(provider, account)
          && quotaPresence(account) === "in_use" && account.account_state !== "known",
      );
      if (unknown.length > 1) {
        table.appendChild(quotaUnknownBody(provider, unknown));
      }
    });

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
    renderQuotaSignedOut(rateLimits);
  }

  function quotaFailureDetails(provider, failures) {
    const body = document.createElement("tbody");
    const row = document.createElement("tr");
    const cell = document.createElement("td");
    cell.colSpan = 7;
    const details = document.createElement("details");
    details.className = "note-disclosure";
    const summary = document.createElement("summary");
    summary.textContent = `${provider.label} 采集详情（${failures.length} 台机器最近查询未成功）`;
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
    return hasValue ? "更新延迟，保留上次读数；可点击刷新重试" : "暂不可用，可点击刷新重试";
  }

  // Stated, not warned about. A machine that is not signed in to a provider has
  // nothing to fix, but its absence from the figures above is still a fact the
  // reader needs — without this line, "macmini is not in the Claude row" and
  // "macmini's Claude number failed to arrive" look identical.
  //
  // It lives outside the table on purpose: a row would put a machine with no
  // account in the same visual class as accounts that have quota, and the
  // columns it would have to leave empty are the reason to read the table.
  function renderQuotaSignedOut(rateLimits) {
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
        (block?.accounts || []).map((account) => account.reading_from).filter(Boolean),
      );
      (block?.signed_out_machines || []).forEach((machine) => {
        (showing.has(machine) ? frozen : absent).push(`${machine} 的 ${provider.label}`);
      });
    });

    const sentences = [];
    if (absent.length) {
      sentences.push(`未登录，未计入上表 —— ${absent.join("、")}。`);
    }
    if (frozen.length) {
      sentences.push(
        `登录已失效，上表数字停在最后一次读数 —— ${frozen.join("、")}。`,
      );
    }
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
      if (typeof account.updated_at === "string" && account.updated_at) {
        row.dataset.observedAt = account.updated_at;
      }
    }
    if (account.account_state !== "known") {
      row.classList.add("unattributed");
    }
    const display = quotaDisplayReading(provider, account);
    const stale = quotaIsStale(display.updated_at);
    if (stale) {
      // Deliberately carries no styling — the pill in the Updated cell marks
      // staleness where it applies. This is the machine-readable half, which
      // the pill's wording is not: keep it even though no CSS rule selects it.
      row.classList.add("stale");
    }

    row.appendChild(quotaProviderCell(provider, display));
    const plan = quotaPlanCell(account);
    if (display.quota_source === "account_action") {
      const source = document.createElement("span");
      source.className = "quota-history-note";
      source.textContent = "机器记录";
      plan.appendChild(source);
    }
    row.appendChild(plan);
    row.appendChild(quotaAccountCell(provider, account));
    QUOTA_COLUMNS.forEach((spec) => row.appendChild(quotaWindowCell(provider, spec, display)));
    row.appendChild(quotaMachinesCell(account));
    row.appendChild(quotaUpdatedCell(display, stale, display.quota_source === "account_action"
      ? null : quotaRefreshNotice(provider, account)));
    return row;
  }

  function quotaProviderCell(provider, account) {
    const cell = document.createElement("td");
    const wrap = document.createElement("div");
    wrap.className = "quota-provider-cell";
    wrap.appendChild(quotaProviderPill(provider));
    if (quotaPresence(account) === "remembered") {
      const marker = document.createElement("span");
      marker.className = "status-pill quota-history-marker";
      marker.textContent = account.quota_source === "account_action" ? "机器历史账号" : "已登出";
      wrap.appendChild(marker);
    }
    cell.appendChild(wrap);
    return cell;
  }

  function quotaPlanCell(account) {
    const cell = document.createElement("td");
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
    const wrap = document.createElement("div");
    wrap.className = "quota-account-cell";

    const name = document.createElement("span");
    name.className = "quota-account-label";
    name.textContent = quotaAccountName(account);
    wrap.appendChild(name);
    const actions = document.createElement("div");
    actions.className = "quota-account-links";

    if (provider.key === "codex" && account.account_state === "known" && account.account_label) {
      const action = document.createElement("button");
      action.type = "button";
      action.className = "quota-toggle codex-account-select";
      action.textContent = "登录 / 发消息";
      action.dataset.codexEmail = account.account_label;
      action.dataset.codexAccountId = account.account_id;
      actions.appendChild(action);
    }

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
    const sevenDay = quotaUsage(account.seven_day_used_pct);
    if (sevenDay.known) {
      return `7d 已用 ${sevenDay.used}%`;
    }
    const fiveHour = quotaUsage(account.five_hour_used_pct);
    if (fiveHour.known) {
      return `5h 已用 ${fiveHour.used}%`;
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
          account.updated_at === observedAt
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
    const observedAt = formatDate(account.updated_at);
    const confirmed = window.confirm(
      `移除 ${accountName} 的记录？\n` +
        `最后观测 ${observedAt}，${quotaRemovalReading(account)}。\n` +
        "此操作不可恢复。"
    );
    if (!confirmed) {
      return;
    }
    button.disabled = true;
    try {
      const response = await fetch(new URL("/api/account-memory/remove", window.location.origin), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          provider: provider.key,
          account_id: account.account_id,
          account_label: account.account_label ?? null,
          observed_at: account.updated_at,
        }),
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        window.alert(payload.error || `移除失败（HTTP ${response.status}）`);
        button.disabled = false;
        return;
      }
      removeRenderedAccount(provider.key, account.account_id, account.updated_at, account.account_label ?? null);
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
      cell.title = `${provider.label} 没有 ${spec.key} 窗口`;
      return cell;
    }

    const { known, used, band } = quotaUsage(account[spec.used]);
    const renderedAtMs = Date.now();
    const resetEpoch = account[spec.reset];
    const resetState = quotaResetState(resetEpoch, renderedAtMs);
    const rememberedHistoricalWindow =
      quotaPresence(account) === "remembered" && resetState !== "future";
    if (band) {
      cell.classList.add(band);
    }

    const line = document.createElement("div");
    line.className = "quota-window-line";

    const amount = document.createElement("span");
    amount.className = "quota-window-value";
    amount.textContent = known ? `${used}%` : "—";
    if (known) {
      amount.title = `本窗口已用 ${used}%`;
    }
    line.appendChild(amount);

    if (known) {
      line.appendChild(quotaMeter(used));
    }
    // The frozen usage value and meter remain facts after their own window has
    // reset, or when its reset is unknown; withdrawing the pill does not infer
    // that current usage is zero.
    // The pill is a present-tense warning, and D4 explicitly narrows contract
    // G4b for this historical case rather than accidentally omitting an alert.
    // A missing reset is not evidence that the old window is still running.
    if (band && !rememberedHistoricalWindow) {
      line.appendChild(quotaBandPill(band, band === "critical" ? "即将用尽" : "余量偏低"));
    }
    cell.appendChild(line);

    const reset = document.createElement("div");
    reset.className = "quota-window-reset";
    reset.textContent = quotaPresence(account) === "remembered" && resetState === "unknown"
      ? "重置时间未知"
      : quotaResetText(resetEpoch, resetState, renderedAtMs);
    if (resetState !== "unknown") {
      reset.title = resetText(resetEpoch, renderedAtMs);
    }
    cell.appendChild(reset);
    if (rememberedHistoricalWindow) {
      const observed = document.createElement("div");
      observed.className = "quota-window-observed";
      observed.textContent = `观测于 ${formatDate(account.updated_at)}`;
      cell.appendChild(observed);
    }
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

  function quotaMachinesCell(account) {
    const cell = document.createElement("td");
    const names = account.machines || [];
    if (!names.length) {
      cell.textContent = "—";
      return cell;
    }
    names.forEach((name, index) => {
      if (index) {
        cell.appendChild(document.createTextNode(" · "));
      }
      const node = document.createElement("span");
      node.textContent = name;
      // Which of these is the machine in front of the reader. Without it, three
      // rows of e-mail addresses do not say which account this session spends.
      if (name === account.this_machine) {
        node.className = "quota-machine-self";
        node.appendChild(document.createTextNode("（本机）"));
      }
      cell.appendChild(node);
    });
    return cell;
  }

  function quotaUpdatedCell(account, stale, refreshNotice) {
    const cell = document.createElement("td");
    cell.className = "quota-updated-cell";
    if (account.quota_source === "account_action") {
      cell.appendChild(document.createTextNode(`本页查询 · ${updatedText(account.updated_at)}`));
      cell.title = formatDate(account.updated_at);
      const note = document.createElement("span");
      note.className = "quota-history-note";
      note.textContent = stale ? "查询读数较旧，可在账号操作中仅刷新配额" : "机器归属与套餐仍按采集记录显示";
      cell.appendChild(note);
      return cell;
    }
    if (quotaPresence(account) === "remembered") {
      cell.appendChild(document.createTextNode(`最后观测 ${formatDate(account.updated_at)}`));
      const note = document.createElement("span");
      note.className = "quota-history-note";
      note.textContent = "最后观测值，不代表当前状态";
      cell.appendChild(note);
      return cell;
    }
    cell.appendChild(document.createTextNode(updatedText(account.updated_at)));
    if (refreshNotice) {
      const note = document.createElement("span");
      note.className = "quota-history-note";
      note.textContent = refreshNotice;
      cell.appendChild(note);
    }
    if (stale) {
      const warn = document.createElement("span");
      warn.className = "status-pill warn";
      warn.textContent = "可能早于一次登录变更";
      cell.appendChild(document.createTextNode(" "));
      cell.appendChild(warn);
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

  function quotaUnknownBody(provider, accounts) {
    const body = document.createElement("tbody");
    body.className = "quota-unknown";

    const summary = document.createElement("tr");
    summary.className = "quota-row unattributed quota-summary";

    const head = document.createElement("td");
    head.colSpan = 3;
    const toggle = document.createElement("button");
    toggle.type = "button";
    toggle.className = "quota-toggle";
    toggle.setAttribute("aria-expanded", "false");
    toggle.appendChild(quotaProviderPill(provider));
    const label = document.createElement("span");
    label.className = "quota-account-label";
    label.textContent = quotaUnknownSummaryText(accounts);
    toggle.appendChild(label);
    // Collapsed rows are hidden in CSS, which puts them out of reach of the
    // browser's find-in-page. `hidden="until-found"` is the platform answer to
    // exactly that and was tried here: it computes `content-visibility: hidden`
    // on the rows, and they stay fully laid out at 81px each — that property has
    // no effect on `display: table-row`, which establishes no independent
    // formatting context. Leaving it in would have made the collapse a no-op.
    // What is left is the summary row naming every machine in the group, so the
    // names stay findable even though the figures beside them do not.
    toggle.addEventListener("click", () => {
      const open = body.classList.toggle("open");
      toggle.setAttribute("aria-expanded", String(open));
    });
    head.appendChild(toggle);
    summary.appendChild(head);

    // Collapsing must not swallow the one thing the reader has to act on. A
    // machine at 96% used is still at 96% used when it has no account stamp, and
    // "the fleet is behind on updates" is exactly when that happens.
    //
    // The pill goes in the column of the window it came from. In a table the
    // column IS the claim: a 5h figure parked under "7d used" is read as a 7d
    // figure, which is the misalignment this whole change exists to remove.
    const worst = quotaWorstUsage(provider, accounts);
    QUOTA_COLUMNS.forEach((spec) => {
      const cell = document.createElement("td");
      cell.className = "numeric";
      if (worst && worst.spec === spec) {
        cell.appendChild(quotaBandPill(worst.band, `${worst.machine} 已用 ${worst.used}%`));
      }
      summary.appendChild(cell);
    });

    const machines = document.createElement("td");
    machines.textContent = accounts.flatMap((a) => a.machines || []).join(" · ");
    summary.appendChild(machines);
    summary.appendChild(document.createElement("td"));

    body.appendChild(summary);
    accounts.forEach((account) => body.appendChild(quotaAccountRow(provider, account)));
    return body;
  }

  // Each state names the machines it applies to, because the remedies differ:
  // one needs agent-monitor updated, the other needs someone to sign in. A summary that
  // says only "N machines report no account" sends the reader to neither.
  function quotaUnknownSummaryText(accounts) {
    const byState = (state) =>
      accounts.filter((a) => a.account_state === state).flatMap((a) => a.machines || []);
    const clauses = [];
    const unstamped = byState("unstamped");
    const signedOut = byState("signed_out");
    if (unstamped.length) {
      clauses.push(`更新 ${unstamped.join("、")} 上的 agent-monitor`);
    }
    if (signedOut.length) {
      clauses.push(`${signedOut.join("、")} 未登录`);
    }
    const total = accounts.flatMap((a) => a.machines || []).length;
    return clauses.length
      ? `${total} 台机器无账号标记 —— ${clauses.join("；")}`
      : `${total} 台机器无账号标记`;
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
          worst = { spec, used, band, machine: (account.machines || []).join("、") || "未知" };
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

  function renderSyncStatus(status) {
    const coverageElement = qs("#sync-coverage");
    const summaryElement = qs("#sync-summary");
    const machineList = qs("#sync-machines");
    if (!coverageElement || !summaryElement || !machineList || !status) {
      return;
    }
    pageServerInstance = status.instance_id || pageServerInstance;
    const coverage = status.coverage || { admitted: 0, declared: 0 };
    coverageElement.textContent = `已纳入 ${coverage.admitted}/${coverage.declared} 台`;
    const warnings = (status.machines || []).filter(
      (machine) =>
        !machine.admitted ||
        machine.stale ||
        machine.availability === "unreachable" ||
        machine.availability === "unknown" ||
        ["failure", "cleanup_failed", "malformed_result"].includes(machine.last_attempt_outcome)
    ).length;
    summaryElement.textContent = status.polling_error
      ? `${status.polling_error} · 页面保留上次渲染的数据`
      : status.syncing
      ? (status.phase === "quota" ? "统计轮已结束 · 后台查询配额" : "后台更新统计与配额 · 新数据陆续显示")
      : warnings
      ? `${warnings} 台需要关注`
      : "使用已采集数据 · 自动更新";
    machineList.innerHTML = (status.machines || []).map(renderMachineStatus).join("");
    renderSpendCoverage(status.machines || []);
    revealSyncDetailOnTrouble(Boolean(warnings || status.polling_error));
  }

  // The Spend figures are fleet totals, so a machine whose refresh failed is
  // silently missing from them. The sync panel says which machine is behind,
  // but it sits a section above the numbers and closes on healthy days; the
  // qualifier has to travel with the figures it undercounts.
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
    // that its newer usage is absent, stated as a bound, not a certainty.
    note.hidden = false;
    note.textContent =
      `${listed}的较新用量尚未计入，下面的合计可能低于实际花费。`;
  }

  // The panel opens itself the moment something needs attention, and stays
  // wherever the reader last put it for as long as that condition holds — so a
  // poll every few seconds cannot keep reopening a panel they just closed.
  let syncTroubleLatch = false;

  function revealSyncDetailOnTrouble(inTrouble) {
    const panel = qs("#sync-panel");
    if (!panel) {
      return;
    }
    if (inTrouble && !syncTroubleLatch) {
      panel.open = true;
    }
    syncTroubleLatch = inTrouble;
  }

  function renderMachineStatus(machine) {
    const chips = [];
    const refreshAttemptFailed = ["failure", "malformed_result"].includes(machine.last_attempt_outcome);
    if (!machine.admitted) {
      chips.push(statusChip("bad", "未纳入"));
    }
    if (machine.syncing) {
      chips.push(statusChip("info", machine.statistics_syncing === false ? "统计已更新 · 查询配额中" : "刷新中"));
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
      chips.push(statusChip("ok", "可用"));
    }
    const marker = machine.this_machine ? ' <span class="status-pill identity">本机</span>' : "";
    const facts = [];
    const observedAt = machine.statistics?.observed_at || machine.generated_at;
    facts.push(observedAt ? `数据更新于 ${formatDate(observedAt)} · ${updatedText(observedAt)}` : "尚无数据");
    if (machine.data_start_date) {
      facts.push(`历史自 ${escapeHtml(machine.data_start_date)}`);
    }
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
      consequence = machine.reason
        ? machine.availability === "never" ? machine.reason : `${exclusion}. ${machine.reason}`
        : exclusion;
    } else if (machine.availability === "unreachable") {
      const age = machine.stale ? "（已过期）" : "";
      consequence = `用的是上次的数据${age}。刷新失败：${machine.reason || "联系不上"}`;
    } else if (refreshAttemptFailed) {
      const age = machine.stale ? "（已过期）" : "";
      consequence = `用的是上次的数据${age}。最近一次刷新失败：${machine.reason || "未知错误"}`;
    } else if (machine.availability === "unknown") {
      consequence = "用的是上次的数据；本服务启动后还没检查过这台机器。";
    } else if (machine.stale) {
      const attemptContext = machine.reason ? `最近一次刷新失败：${machine.reason}` : "";
      consequence = `用的是较旧的数据。${attemptContext}`;
    } else if (machine.last_attempt_outcome === "cleanup_failed") {
      consequence = machine.reason
        ? `数据已更新，但刷新后的清理失败：${machine.reason}`
        : "数据已更新，但刷新后的清理失败，未报告原因。";
    } else if (machine.syncing) {
      consequence = "刷新中，现有数据仍可用。";
    } else if (machine.reason) {
      consequence = `数据已更新，但刷新报告：${machine.reason}`;
    }
    const consequenceElement = consequence
      ? `<div class="sync-machine-reason">${escapeHtml(consequence)}</div>`
      : "";
    return `<article class="sync-machine ${machine.admitted ? "included" : "excluded"}">
      <div class="sync-machine-title"><strong>${escapeHtml(machine.name)}</strong>${marker}<span class="sync-chips">${chips.join("")}</span></div>
      <div class="sync-machine-time">${facts.join(" · ")}</div>
      ${consequenceElement}
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
    let fingerprint = snapshotFingerprint(status);
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
      const next = snapshotFingerprint(status);
      if (next !== fingerprint && options && options.onProgress) {
        await options.onProgress(status);
        fingerprint = next;
      }
    }
    return status;
  }

  function snapshotFingerprint(status) {
    return JSON.stringify((status.machines || []).map((machine) =>
      [machine.name, machine.admitted, machine.generation_id]).sort());
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
    renderNetworkBanner(data);
    if (data.installed === false) {
      renderNetworkError({
        title: "ip-check 未安装，请在 agent-monitor checkout 中运行 ./install.sh。",
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
    const level = data.verdict || "unknown";
    banner.className = `verdict-banner ${level === "high" ? "high" : level === "low" ? "low" : level === "proxy-in-use" ? "proxy-in-use" : "unknown"}`;
    if (data.installed === false) {
      verdict.textContent = "—";
      updated.textContent = "原因：ip-check 未安装";
      return;
    } else if (data.error) {
      verdict.textContent = "—";
      updated.textContent = `原因：${data.error}`;
      return;
    } else if (level === "high") {
      verdict.textContent = "Claude 使用风险高";
    } else if (level === "proxy-in-use") {
      verdict.textContent = "可用，但正在走代理";
    } else if (level === "low") {
      verdict.textContent = "Claude 使用风险低";
    } else {
      verdict.textContent = "网络状态未知";
    }
    updated.textContent = data.timestamp ? `更新于 ${formatDate(data.timestamp)}` : "—";
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
    const ipv6 = local.ipv6_leaked ? statusPill("bad", local.ipv6 || "已泄漏") : statusPill("ok", "已关闭");
    const dns = local.dns && local.dns.length ? local.dns.map(renderDns).join("") : "—";
    qs("#network-local").innerHTML = [
      kvRow("内网 IP", escapeHtml(local.lan_ip || "—")),
      kvRow("IPv6", ipv6),
      kvRow("DNS 服务器", `<div class="dns-list">${dns}</div>`),
      kvRow("DNS 地域", local.dns_has_cn ? statusPill("bad", "检出国内 DNS 解析器") : statusPill("ok", "无国内 DNS 解析器")),
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
      kvRow("IP", escapeHtml(pub.ip || "—")),
      kvRow("位置", escapeHtml(location)),
      kvRow("运营商", escapeHtml(pub.isp || "—")),
      kvRow("机构", escapeHtml(pub.org || "—")),
      kvRow("时区", timezone),
    ].join("");
  }

  function renderRiskNetwork(data) {
    const pub = data.public || {};
    const risk = data.risk;
    const spam = data.spam;
    const proxyEnvEntries = Object.entries(data.proxy_envs || {});
    const score = risk && risk.score !== null && risk.score !== undefined
      ? statusPill(risk.level === "high" ? "bad" : risk.level === "medium" ? "warn" : "ok", `${risk.score}/100 ${risk.level}`)
      : statusPill("warn", sectionMessage(data, "risk") || "未查询");
    const type = risk && risk.type ? escapeHtml(risk.type) : "—";
    const markedProxy = Boolean(pub.proxy || (risk && risk.marked_proxy));
    const spamParsed = spam && (
      spam.score !== null && spam.score !== undefined ||
      spam.frequency !== null && spam.frequency !== undefined ||
      spam.last_seen
    );
    const spamFallback = spam && spam.raw_lines && spam.raw_lines.length
      ? spam.raw_lines.map(escapeHtml).join("<br>")
      : "";
    const spamScore = spamParsed && spam.score !== null && spam.score !== undefined
      ? statusPill(spam.level === "high" ? "bad" : spam.level === "medium" ? "warn" : "ok", `${spam.score}/100 ${spam.level || ""}`.trim())
      : spamFallback || sectionMessage(data, "spam") || "—";
    const spamReports = spamParsed && spam.frequency !== null && spam.frequency !== undefined
      ? escapeHtml(spam.frequency)
      : "—";
    const lastSpamReport = spamParsed && spam.last_seen ? escapeHtml(spam.last_seen) : "—";
    const envs = proxyEnvEntries.length
      ? proxyEnvEntries.map(([key, value]) => `${escapeHtml(key)} = ${escapeHtml(value)}`).join("<br>")
      : "—";
    // The provider name belongs to the card, not to each row: repeated six
    // times it filled the label column and wrapped every label onto two lines.
    qs("#network-risk").innerHTML = [
      kvRow("风险分", score),
      kvRow("类型", type),
      kvRow("标记为代理", markedProxy ? statusPill("warn", "是") : statusPill("ok", "否")),
      kvRow("机房 IP", pub.hosting ? statusPill("warn", "是") : statusPill("ok", "否")),
      kvRow("垃圾评分", spamScore),
      kvRow("举报次数", spamReports),
      kvRow("最近举报", lastSpamReport),
      kvRow("代理环境变量", envs),
      '<p class="conclusion-note">风险分与类型来自 proxycheck，代理标记与机房判定来自 ip-api，垃圾评分来自 stopforumspam；代理环境变量读自本机 shell。</p>',
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
      kvRow("本机时区", `${escapeHtml(tz.cli_tz || "—")} (${escapeHtml(tz.cli_offset || "—")})`),
      kvRow("公网时区", pub.timezone ? `${escapeHtml(pub.timezone)} (${escapeHtml(pub.tz_offset || "—")})` : "—"),
      kvRow("是否一致", match),
    ].join("");
  }

  function renderConclusions(data) {
    const items = data.conclusions || [];
    const note = '<p class="conclusion-note">判定：IPv6 泄漏、国内 DNS、风险分 &gt;= 70、时区不一致，任一命中即「风险高」；只检出代理则「走代理」；其余为「风险低」。</p>';
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
    const country = entry.country ? ` ${statusPill(entry.country === "CN" ? "bad" : "ok", entry.country)}` : "";
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
          const summary = qs("#sync-summary");
          if (summary) summary.textContent = "刷新请求已受理 · 后台更新，当前数据仍可查看";
        } catch (error) {
          if (pageCurrent() && generation === overviewGeneration) showOverviewLoadError(error);
        }
        return;
      }
      let data;
      try {
        data = await api("/api/overview", { range: selectedRange });
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
              const fresh = await api("/api/overview", { range: selectedRange, sync: "0" });
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
        const finalData = await api("/api/overview", { range: selectedRange, sync: "0" });
        if (!pageCurrent() || generation !== overviewGeneration) {
          return;
        }
        renderOverview(finalData, selectedRange);
        renderSyncStatus(finalData.sync);
        qs("#overview-load-error")?.remove();
    }
    bindShell(load);
    watchPageData(() => load(false, true));
    await load(false);
  }

  function showOverviewLoadError(error) {
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
    page: 0,
    status: "loading",
    filters: { agent: "", project: "", model: "", machine: "" },
  };
  let sessionsGeneration = 0;
  const SESSION_FILTERS = [
    { key: "machine", select: "#filter-machine", field: "machine" },
    { key: "agent", select: "#filter-agent", field: "agent_id" },
    { key: "project", select: "#filter-project", field: "project" },
    { key: "model", select: "#filter-model", field: "model" },
  ];

  async function initSessions() {
    if (params().has("session")) return initSessionDetail();
    SESSION_FILTERS.forEach(({key}) => { sessionsView.filters[key] = params().get(key) || ""; });
    sessionsView.page = Math.max(0, parseInt(params().get("page"), 10) || 0);
    const initialSort = qs("#sort");
    if (initialSort && params().has("sort")) initialSort.value = params().get("sort");
    const pageCurrent = pageScope();
    let activeLoads = 0;
    async function load(force, background = false) {
      if (background && activeLoads) return;
      activeLoads += 1;
      const generation = ++sessionsGeneration;
      sessionsView.status = "loading";
      renderSessionState("loading");
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
        const coverage = qs("#session-source-coverage");
        if (coverage) coverage.textContent = sessionCoverageText(status.machines || []);
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
        const data = normalizeSessionsPayload(
          await api("/api/sessions", {
            range: getRange(),
            sort: qs("#sort") ? qs("#sort").value : "time",
            order: "desc",
            sync: force === true || observeOnly ? "0" : undefined,
          })
        );
        if (!isValidSessionsPayload(data)) {
          throw new Error("会话数据包含无效行。");
        }
        if (!pageCurrent() || generation !== sessionsGeneration) {
          return;
        }
        sessionsView.rows = data;
        if (!keepPage) sessionsView.page = 0;
        populateSessionFilters(data);
        sessionsView.status = "ready";
        setSessionFiltersDisabled(false);
        renderSessions();
      }
    }
    bindShell(load);
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
          renderSessions();
        });
      }
    });
    const prev = qs("#page-prev");
    const next = qs("#page-next");
    if (prev) {
      prev.addEventListener("click", () => turnSessionPage(-1));
    }
    if (next) {
      next.addEventListener("click", () => turnSessionPage(1));
    }
    await load(false, true);
  }

  // One clause per machine, and the retention date pulled out to the end — but
  // only when it is true of every machine that reported one. Hoisting it any
  // other time states a fleet-wide fact the data does not support: from one
  // machine's reading (an older exporter beside it reports no date at all), or
  // from several different dates, which arrive at the end as a bare list with
  // no machine attached to any of them. Both of those read as answers and are
  // wrong, so those cases keep the date on the machine it came from.
  function sessionCoverageText(machines) {
    const withData = [];
    const clauses = machines.map((machine) => {
      const details = machine.statistics;
      if (!details) {
        return { text: `${machine.name}：采集情况未知` };
      }
      if (details.state !== "available") {
        return { text: `${machine.name}：未采集会话明细` };
      }
      const clause = {
        text: `${machine.name}：${updatedText(details.observed_at)}${details.scan_complete ? "" : "（源扫描未完成）"}`,
        from: details.history_authoritative_from || "",
      };
      withData.push(clause);
      return clause;
    });
    const dates = new Set(withData.map((clause) => clause.from));
    const hoistable = withData.length > 0 && dates.size === 1 && !dates.has("");
    if (hoistable) {
      // Machines that reported nothing at all are not in withData, so the
      // hoisted date says nothing about them. Naming the subject keeps the
      // de-duplication without widening the claim to cover them.
      const subject = withData.length === clauses.length ? "" : "有数据的机器";
      return `${clauses.map((clause) => clause.text).join(" · ")}。${subject}日粒度历史自 ${withData[0].from} 起保留。`;
    }
    return clauses
      .map((clause) => clause.text + (clause.from ? `（日粒度历史自 ${clause.from} 起保留）` : ""))
      .join(" · ");
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

  // Options come from the rows actually loaded, so a filter can never offer a
  // value that would return nothing. A selection that survives a reload stays
  // selected; one whose value has aged out of the window resets to "all".
  function populateSessionFilters(rows) {
    SESSION_FILTERS.forEach((filter) => {
      const select = qs(filter.select);
      if (!select) {
        return;
      }
      const values = Array.from(new Set(rows.map((row) => row[filter.field]).filter(Boolean))).sort();
      const previous = sessionsView.filters[filter.key];
      const keep = values.includes(previous) ? previous : "";
      const label = select.options[0] ? select.options[0].textContent : "All";
      const labels = optionLabels(filter.key, values);
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
    return sessionsView.rows.filter((row) =>
      SESSION_FILTERS.every((filter) => {
        const wanted = sessionsView.filters[filter.key];
        return !wanted || row[filter.field] === wanted;
      })
    );
  }

  function renderSessions() {
    const tbody = qs("#sessions-body");
    const matched = filteredSessions();
    const lastPage = Math.max(0, Math.ceil(matched.length / SESSIONS_PAGE_SIZE) - 1);
    sessionsView.page = Math.min(sessionsView.page, lastPage);
    const listParams = params();
    SESSION_FILTERS.forEach(({key}) => {
      if (sessionsView.filters[key]) listParams.set(key, sessionsView.filters[key]);
      else listParams.delete(key);
    });
    listParams.set("page", String(sessionsView.page));
    listParams.set("sort", qs("#sort")?.value || "time");
    replaceCurrentUrl("/sessions?" + listParams);
    const start = sessionsView.page * SESSIONS_PAGE_SIZE;
    const pageRows = matched.slice(start, start + SESSIONS_PAGE_SIZE);
    const filtering = matched.length !== sessionsView.rows.length;
    if (!pageRows.length) {
      renderSessionState("empty", filtering ? "没有符合筛选条件的会话。" : "此范围内没有会话。");
      qs("#session-count").textContent = filtering
        ? `0 / ${integer(sessionsView.rows.length)} 个会话`
        : "0 个会话";
      return;
    }
    // Dates only group meaningfully while the list is in time order; sorted by
    // cost or tokens the days interleave and a heading would assert an order
    // the table does not have.
    const grouped = (qs("#sort") ? qs("#sort").value : "time") === "time";
    let lastDay = null;

    tbody.innerHTML = "";
    tbody.setAttribute("aria-busy", "false");
    pageRows.forEach((row) => {
      const day = dayKey(row.started_at);
      if (grouped && day && day !== lastDay) {
        const heading = document.createElement("tr");
        heading.className = "date-group";
        heading.innerHTML = `<th colspan="7" scope="colgroup">${escapeHtml(day)}</th>`;
        tbody.appendChild(heading);
        lastDay = day;
      }
      const tr = document.createElement("tr");
      tr.className = "session-row";
      tr.dataset.sessionId = row.session_id;
      tr.dataset.machine = row.machine || "";
      const detailParams = new URLSearchParams(listParams);
      detailParams.set("session", row.session_id);
      detailParams.set("session_machine", row.machine || "");
      tr.innerHTML = `
        <td><span class="pill agent-${escapeHtml(row.agent_id)}">${escapeHtml(row.agent_id)}</span><br><small>${escapeHtml(row.machine || "本机")}</small></td>
        <td class="project-cell"><a href="/sessions?${escapeHtml(detailParams.toString())}" title="${escapeHtml(row.project)}">${escapeHtml(projectLabel(row.project))}</a></td>
        <td class="nowrap">${escapeHtml(row.model)}${row.estimated ? ' <span class="muted">推算</span>' : ""}</td>
        <td class="nowrap">${grouped ? escapeHtml(timeOfDay(row.started_at)) : formatDate(row.started_at)}</td>
        <td class="numeric">${moneyPrecise(row.cost_usd)}</td>
        <td class="numeric">${integer(row.tokens)}</td>
        <td class="numeric">${integer(row.usage_events)}</td>
      `;
      tr.addEventListener("click", (event) => {
        if (!event.target.closest("a, button") && !window.getSelection()?.toString()) tr.querySelector("a").click();
      });
      tbody.appendChild(tr);
    });

    syncStickyHeaderOffset();
    qs("#session-count").textContent = filtering
      ? `${integer(matched.length)} / ${integer(sessionsView.rows.length)} 个会话`
      : `${integer(sessionsView.rows.length)} 个会话`;
    renderSessionPager(matched.length, start, pageRows.length);
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
    cell.colSpan = 7;
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
    const status = qs("#page-status");
    if (status) {
      status.textContent = text;
    }
    const prev = qs("#page-prev");
    const next = qs("#page-next");
    if (prev) {
      prev.disabled = true;
    }
    if (next) {
      next.disabled = true;
    }
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
    const status = qs("#page-status");
    const prev = qs("#page-prev");
    const next = qs("#page-next");
    if (status) {
      status.textContent = total
        ? `第 ${integer(start + 1)}–${integer(start + shown)} 条，共 ${integer(total)} 条`
        : "没有符合筛选条件的会话";
    }
    if (prev) {
      prev.disabled = sessionsView.page === 0;
    }
    if (next) {
      next.disabled = start + shown >= total;
    }
  }

  // Transcript identifiers are local/provider IDs, not Gateway request IDs.
  async function initSessionDetail() {
    const current = pageScope();
    const selected = params();
    const id = selected.get("session");
    const machine = selected.get("session_machine") || "";
    const back = new URLSearchParams(selected);
    ["session", "session_machine", "usage_model", "usage_page"].forEach(key => back.delete(key));
    qs("#session-list-panel").hidden = true;
    qs("main h1").textContent = "会话详情";
    ["#range", "#sort"].forEach(selector => { const el = qs(selector); if (el) el.hidden = true; });
    const panel = document.createElement("section");
    panel.className = "session-detail";
    panel.innerHTML = `<a class="session-back" href="/sessions?${escapeHtml(back.toString())}">‹ 返回会话列表</a>
      <div class="panel session-identity"><div><h2>会话</h2><code id="session-identity"></code><p>${escapeHtml(machine || "本机")} · 已留存的完整会话</p></div><button id="session-copy" type="button">复制 ID</button></div>
      <div id="session-detail-content" aria-live="polite"></div>`;
    qs("main").appendChild(panel);
    qs("#session-identity").textContent = id;
    qs("#session-copy").addEventListener("click", async (event) => {
      try { await navigator.clipboard.writeText(id); event.target.textContent = "已复制"; }
      catch { event.target.textContent = "复制失败，请选中 ID 复制"; }
    });
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
        const detail = await api("/api/session/" + encodeURIComponent(id), {machine});
        if (!current() || ticket !== generation) return;
        if (!Array.isArray(detail.entries)) throw new Error("会话数据格式无效");
        renderSessionDetail(detail.entries, content);
      } catch (error) {
        if (!current() || ticket !== generation) return;
        content.innerHTML = `<div class="panel"><p class="error">会话读取失败：${escapeHtml(error.message || error)}</p><button id="session-retry" type="button">重试</button></div>`;
        qs("#session-retry").addEventListener("click", load);
      } finally {
        activeLoads--;
      }
    }
    bindShell(load, {range: false});
    watchPageData(() => load(false, true));
    await load();
  }

  function sessionEntryCost(value) {
    if (value === null || value === undefined) return "未知";
    if (value > 0 && value < 0.0001) return "$" + Number(value).toPrecision(3);
    return moneyPrecise(value);
  }

  function renderSessionDetail(entries, content) {
    if (!entries.length) {
      content.innerHTML = '<div class="panel empty-state">未找到此机器上的已留存用量。会话可能未采集，或机器尚未纳入。</div>';
      return;
    }
    const distinct = key => [...new Set(entries.map(entry => entry[key]).filter(Boolean))];
    const models = distinct("model");
    const tokens = entries.reduce((n, e) => n + ["input_tokens", "output_tokens", "cache_creation_tokens", "cache_read_tokens"].reduce((sum, key) => sum + (Number(e[key]) || 0), 0), 0);
    const unknown = entries.filter(e => e.cost_usd == null).length;
    const cost = unknown ? null : entries.reduce((n, e) => n + e.cost_usd, 0);
    const events = entries.reduce((n, e) => n + (Number(e.usage_event_count) || 0), 0);
    const dates = entries.map(e => new Date(e.timestamp).getTime()).filter(Number.isFinite);
    const first = dates.length ? dates.reduce((a, b) => Math.min(a, b)) : null;
    const last = dates.length ? dates.reduce((a, b) => Math.max(a, b)) : null;
    const span = first === null ? "未知" : integer(Math.round((last - first) / 60000)) + " 分钟";
    const field = (name, value) => `<div><dt>${name}</dt><dd>${escapeHtml(value || "—")}</dd></div>`;
    content.innerHTML = `<div class="session-metrics">
      <article><h3>记录成本</h3><strong>${sessionEntryCost(cost)}</strong><p>${unknown ? `${unknown} 条成本未知，未合计为总额` : "按现有定价口径 · 非账单"}</p></article>
      <article><h3>Token</h3><strong>${integer(tokens)}</strong><p>输入、输出与缓存合计</p></article>
      <article><h3>用量条目</h3><strong>${integer(events)}</strong><p>${integer(entries.length)} 条记录 · 非请求次数</p></article>
      <article><h3>观测跨度</h3><strong>${span}</strong><p>首末记录之间 · 非活跃时长</p></article></div>
      <details class="panel session-metadata"><summary>会话信息 · ${models.length} 个模型</summary><dl class="session-fields">
        ${field("Agent", distinct("agent_id").join("、"))}${field("会话目录", distinct("project").join("、"))}
        ${field("模型", models.join("、"))}${field("首条记录", first === null ? "" : formatDate(first))}${field("末条记录", last === null ? "" : formatDate(last))}
      </dl><p class="scope-note">范围为该会话已留存的全部记录，不受列表时间窗口限制。Codex 与部分模型采用推算定价；未知费用不归零。这里不包含对话正文，也未关联 Gateway 尝试链。</p></details>
      <section class="panel"><div class="panel-head"><h2>用量明细</h2><span id="usage-count" class="status-line"></span></div>
      <div class="controls"><div class="field"><label for="usage-model">模型</label><select id="usage-model"><option value="">全部模型</option>${models.map(m => `<option value="${escapeHtml(m)}">${escapeHtml(m)}</option>`).join("")}</select></div><button id="usage-clear" type="button">清除筛选</button></div>
      <div class="table-wrap"><table><thead><tr><th>记录时间</th><th>模型</th><th class="numeric">输入</th><th class="numeric">输出</th><th class="numeric">缓存读取 / 写入</th><th class="numeric">成本</th><th>详情</th></tr></thead><tbody id="usage-body"></tbody></table></div>
      <div class="pager"><button id="usage-prev" type="button">‹ 上一页</button><span id="usage-status" role="status"></span><button id="usage-next" type="button">下一页 ›</button></div></section>`;
    let page = Math.max(0, parseInt(params().get("usage_page"), 10) || 0);
    const select = qs("#usage-model");
    select.value = models.includes(params().get("usage_model")) ? params().get("usage_model") : "";
    function render() {
      const rows = entries.filter(e => !select.value || e.model === select.value);
      page = Math.min(page, Math.max(0, Math.ceil(rows.length / 50) - 1));
      const next = params();
      if (select.value) next.set("usage_model", select.value); else next.delete("usage_model");
      next.set("usage_page", String(page));
      replaceCurrentUrl("/sessions?" + next);
      qs("#usage-count").textContent = `${rows.length} / ${entries.length} 条记录`;
      qs("#usage-body").innerHTML = rows.slice(page * 50, (page + 1) * 50).map((e, index) => `<tr>
        <td class="nowrap">${escapeHtml(formatDate(e.timestamp))}</td><td>${escapeHtml(e.model)}</td>
        <td class="numeric">${integer(e.input_tokens)}</td><td class="numeric">${integer(e.output_tokens)}</td>
        <td class="numeric">${integer(e.cache_read_tokens)} / ${integer(e.cache_creation_tokens)}</td><td class="numeric">${sessionEntryCost(e.cost_usd)}</td>
        <td><button type="button" data-usage="${index}" aria-expanded="false">查看</button></td></tr>`).join("") || '<tr><td colspan="7">没有符合条件的记录。</td></tr>';
      qsa("[data-usage]").forEach(button => button.addEventListener("click", () => {
        if (button.getAttribute("aria-expanded") === "true") { button.closest("tr").nextElementSibling.remove(); button.setAttribute("aria-expanded", "false"); return; }
        const e = rows[page * 50 + Number(button.dataset.usage)];
        const row = document.createElement("tr");
        row.className = "session-entry-detail";
        row.innerHTML = `<td colspan="7"><dl class="session-fields">${field("消息 ID", e.message_id)}${field("日志请求 ID", e.request_id)}${field("Agent", e.agent_id)}${field("会话目录", e.project)}${field("用量条目", String(e.usage_event_count ?? "未知"))}</dl><p class="scope-note">ID 来自源日志，不能直接作为 Gateway 请求标识。</p></td>`;
        button.closest("tr").after(row); button.setAttribute("aria-expanded", "true");
      }));
      qs("#usage-status").textContent = rows.length ? `第 ${page * 50 + 1}–${Math.min(rows.length, (page + 1) * 50)} 条，共 ${rows.length} 条` : "没有记录";
      qs("#usage-prev").disabled = page === 0;
      qs("#usage-next").disabled = (page + 1) * 50 >= rows.length;
    }
    select.addEventListener("change", () => { page = 0; render(); });
    qs("#usage-clear").addEventListener("click", () => { select.value = ""; page = 0; render(); });
    qs("#usage-prev").addEventListener("click", () => { page--; render(); });
    qs("#usage-next").addEventListener("click", () => { page++; render(); });
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
      const res = await fetch(new URL("/api/restart", window.location.origin), { method: "POST" });
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
    ["/llm-calls", () => window.AgentMonitorLLMCalls.init()],
    ["/network", () => initNetwork()],
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
          '<p class="error">此页面加载失败，刷新重试。</p>',
        );
      }
    }
    if (pageIsCurrent(generation)) {
      window.scrollTo({ top: 0, behavior: "auto" });
    }
    return true;
  }

  function initClientNavigation() {
    if (document.documentElement.dataset.clientNav === "on") {
      return;
    }
    document.documentElement.dataset.clientNav = "on";
    renderedRoute = routeKey(
      new URL(
        window.location.pathname + (window.location.search || ""),
        window.location.origin || "http://localhost",
      ),
    );

    document.addEventListener("click", (event) => {
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
      if (!clientRouteFor(url)) {
        return;
      }

      event.preventDefault();
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
    refreshStatistics,
    pollFreshness,
    initNetwork,
    initOverview,
    initSessions,
  };
})();
