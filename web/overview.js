// Overview rendering, quota restoration and refresh lifecycle.
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory;
  else (root.AgentMonitorModules ||= {}).overview = factory;
})(typeof window === "undefined" ? globalThis : window, function createOverview(dependencies) {
  const { platform, assetVersion, qs, qsa, params, getRange, api, bindShell, pageScope, onPageCleanup, watchPageData, money, integer, compactNumber, barValueLabels, chart, dataset, chartOptions, categoryTick, chartMoneyTick, chartReading, costReading, costTableReading, renderChartTable, agentLabel, escapeHtml, quotaFilter, resetQuotaReadings, renderQuotaAccounts, renderSyncStatus, waitForSyncTerminal } = dependencies;
  const { window, document } = platform;
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
    onPageCleanup(resetQuotaReadings);
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

  return { initOverview, renderOverview, renderRangeCost, showOverviewLoadError };
});
