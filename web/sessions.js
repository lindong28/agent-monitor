// Sessions view responsibilities. The shell supplies navigation and page lifetime.
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory;
  else (root.AgentMonitorModules ||= {}).sessions = factory;
})(typeof window === "undefined" ? globalThis : window, function createSessions(dependencies) {
  const { platform, qs, qsa, params, getRange, setParam, copyText, api, bindShell, moneyPrecise, integer, projectLabel, codexSessionAccountLabel, collectionUrl, renderSyncStatus, waitForSyncTerminal, refreshStatistics, formatDate, dayKey, timeOfDay, escapeHtml, pageScope, watchPageData, replaceCurrentUrl } = dependencies;
  const { window, document } = platform;
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
        <td class="project-cell"><a class="session-title" href="/sessions?${escapeHtml(detailParams.toString())}" title="${escapeHtml(row.title || row.project)}">${escapeHtml(row.title || projectLabel(row.project))}</a>${row.title ? `<small>${escapeHtml(projectLabel(row.project))}</small>` : ""}${row.subagent_count ? `<small>含 ${integer(row.subagent_count)} 个子代理</small>` : ""}${row.agent_id === "codex" ? `<small>${escapeHtml(codexSessionAccountLabel(row.account))}</small>` : ""}</td>
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
      <div class="panel session-identity"><div><h2 id="session-project">会话</h2><p id="session-times"></p><p id="session-account"></p><p>${escapeHtml(machine || "本机")} · <span id="session-scope"></span></p><button id="session-scope-toggle" type="button"></button><details class="note-disclosure"><summary>会话标识与记录范围</summary><div class="session-id-line"><span>ID</span><code id="session-identity"></code><button id="session-copy" type="button">复制 ID</button></div><p class="scope-note">仅含已采集的用量和会话标题，不含对话正文。<a data-collection-link href="${escapeHtml(collectionUrl())}">采集与诊断</a></p></details></div></div>
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
    qs("#session-account").textContent = meta.agent_id === "codex" ? codexSessionAccountLabel(meta.account) : "";
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

  return { initSessions, turnSessionPage, populateSessionFilters, setSessionFiltersDisabled, isValidSessionsPayload, normalizeSessionsPayload, optionLabels, filteredSessions, sessionDateGroups, renderSessions, renderSessionState, syncStickyHeaderOffset, renderSessionPager, initSessionDetail, sessionEntryCost, renderSessionDetail, sessionsView };
});
