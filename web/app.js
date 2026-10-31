// Shell, navigation and cross-page orchestration; page implementations live in modules.
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory;
  else factory(root);
})(globalThis, function createApp(platform = globalThis) {
  const { window, document } = platform;
  const fetch = (...args) => platform.fetch(...args);
  const modules = typeof module === "object" && module.exports
    ? { charts: require("./charts.js"), quota: require("./quota.js"), network: require("./network.js"), sessions: require("./sessions.js"), collection: require("./collection.js"), overview: require("./overview.js") }
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
  let runtime = null, runtimePromise = null;
  function runtimeContext() {
    if (!runtimePromise) runtimePromise = fetch(new URL('/api/runtime-context', window.location.origin))
      .then(response => response.ok ? response.json() : {})
      .then(value => { runtime = value; return value; }).catch(() => ({}));
    return runtimePromise;
  }
  const hubUrl = () => runtime?.statistics_source === 'hub' ? runtime.hub_url : null;
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
    ["控制台", "网络", "查看系统网络诊断", "/network"],
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
    await runtimeContext();
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
      const detail = await response.json().catch(() => ({}));
      throw new Error(detail.error || `${response.status} ${response.statusText}`);
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
  const { money, moneyPrecise, costCoverageText, costReading, integer, compactNumber, barValueLabels, shortText, chart, dataset, chartOptions, categoryTick, chartMoneyTick, chartReading, costTableReading, renderChartTable, SERIES_LIMIT, destroyCharts, agentLabel } = charts;

  const quota = modules.quota({ platform, fmtAbs, qs, api, hubUrl, currentPageUrl, collectionUrl, updateSyncOverviewLink, formatDate });
  const { latestCodexQuotaReading, updateCodexQuotaReadings, codexSessionAccountLabel, renderQuotaAccounts, renderQuotaSignedOut, formatQuotaReset, renderQuotaMachineRecords, quotaFilter, resetQuotaReadings } = quota;

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

  // Collection owns the shared status reading; shell owns server identity/lifetime.
  const collection = modules.collection({ platform, qs, qsa, params, getRange, api, bindShell, pageScope, watchPageData, GUIDE_ROUTES, integer, agentLabel, formatDate, escapeHtml, renderQuotaMachineRecords, renderQuotaSignedOut, onServerInstance: (id) => { if (!hubUrl()) pageServerInstance = id; } });
  const { initCollection, renderSyncStatus, waitForSyncTerminal, snapshotFingerprint, refreshStatistics, renderCostDiagnostics, costDiagnosticsQuery } = collection;

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

  const network = modules.network({ platform, qs, updateNavLinks, api, runtimeContext, withRefresh, formatDate, escapeHtml, onPageCleanup });
  const { initNetwork } = network;

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

  const overview = modules.overview({ platform, assetVersion, qs, qsa, params, getRange, api, runtimeContext, hubUrl, bindShell, pageScope, onPageCleanup, watchPageData, money, integer, compactNumber, barValueLabels, chart, dataset, chartOptions, categoryTick, chartMoneyTick, chartReading, costReading, costTableReading, renderChartTable, agentLabel, escapeHtml, quotaFilter, resetQuotaReadings, renderQuotaAccounts, renderSyncStatus, waitForSyncTerminal });
  const { initOverview, renderOverview, renderRangeCost, showOverviewLoadError } = overview;

  const sessions = modules.sessions({ platform, qs, qsa, params, getRange, setParam, copyText, api, bindShell, moneyPrecise, integer, codexSessionAccountLabel, collectionUrl, renderSyncStatus, waitForSyncTerminal, refreshStatistics, formatDate, dayKey, timeOfDay, escapeHtml, pageScope, watchPageData, replaceCurrentUrl });
  const { initSessions } = sessions;

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

  function onPageCleanup(cleanup) {
    pageCleanups.push(cleanup);
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
    runtimeContext, hubUrl,
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
    onPageCleanup,
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
