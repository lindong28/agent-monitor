// Collection page, shared collection status and refresh observation.
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory;
  else (root.AgentMonitorModules ||= {}).collection = factory;
})(typeof window === "undefined" ? globalThis : window, function createCollection(dependencies) {
  const { platform, qs, qsa, params, getRange, api, bindShell, pageScope, watchPageData, GUIDE_ROUTES, integer, agentLabel, formatDate, escapeHtml, renderQuotaMachineRecords, renderQuotaSignedOut, onServerInstance } = dependencies;
  const { window, document } = platform;
  function renderSyncStatus(status) {
    renderQuotaRefreshStatus(status?.quota_refresh);
    if (!status) return;
    if (status.instance_id) onServerInstance(status.instance_id);
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
    const pageCurrent = pageScope();
    const isCurrent = () => pageCurrent() && callerIsCurrent();
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
    const current = pageScope();
    const initial = await api("/api/refresh");
    if (!current()) return initial;
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

  return { initCollection, renderSyncStatus, waitForSyncTerminal, snapshotFingerprint, refreshStatistics, renderCostDiagnostics, costDiagnosticsQuery };
});
