// Quota view responsibilities. The shell supplies navigation and page lifetime.
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory;
  else (root.AgentMonitorModules ||= {}).quota = factory;
})(typeof window === "undefined" ? globalThis : window, function createQuota(dependencies) {
  const { platform, fmtAbs, qs, api, currentPageUrl, collectionUrl, updateSyncOverviewLink, formatDate } = dependencies;
  const { window, document } = platform;
  const fetch = (...args) => platform.fetch(...args);
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
    const text = [account.account_label, account.account_id, account.account_profile, quotaAccountName(account),
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

  function codexSessionAccountLabel(account) {
    return account ? `账号：${account.name} · ${account.email}` : "账号归属未记录或冲突";
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
    // Keep recovery cards alive across snapshot redraws, including an in-flight check.
    const previousDiagnostics = new Map(Array.from(table.tBodies)
      .filter(body => body.quotaDiagnostics).map(body => [body.quotaDiagnostics.provider, body]));
    const focusedRecovery = document.activeElement?.closest?.(".quota-failure-details") ? document.activeElement : null;
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
      const previous = previousDiagnostics.get(provider.key);
      if (failures.length || previous?.quotaDiagnostics.entries.some(record => record.panel)) {
        table.appendChild(quotaFailureDetails(provider, failures, previous));
      }
    });
    Array.from(table.querySelectorAll?.(".quota-observation-details") || []).forEach((node) => {
      if (node.tagName === "DETAILS") node.open = openObservations.has(node.dataset.observationKey);
      if (focusedObservation && node.dataset.observationKey === focusedObservation) node.querySelector?.("summary")?.focus();
    });
    if (focusedRecovery && table.contains?.(focusedRecovery)) focusedRecovery.focus?.({preventScroll:true});
    renderQuotaSignedOut(rateLimits);
    updateSyncOverviewLink();
    document.dispatchEvent?.(new Event("agent-monitor:quota-rendered"));
  }

  function codexPauseTarget(pauseId) {
    if (!pauseId || pauseId === "unreadable") return null;
    const matches = (renderedQuotaRateLimits?.codex?.refresh_errors || [])
      .filter((failure) => failure.pause?.id === pauseId);
    if (matches.length !== 1) return null;
    const target = matches[0];
    return target.machine && target.account_id && target.account_label ? target : null;
  }

  function showCodexPauseRecovery(pauseId) {
    if (!codexPauseTarget(pauseId)) return false;
    const entries = Array.from(document.querySelectorAll("[data-codex-pause-id]"))
      .filter((entry) => entry.dataset.codexPauseId === pauseId);
    if (entries.length !== 1) return false;
    const entry = entries[0];
    for (let parent = entry.parentElement; parent; parent = parent.parentElement) {
      if (parent.tagName === "DETAILS") parent.open = true;
    }
    entry.scrollIntoView({block: "center"});
    entry.focus({preventScroll: true});
    return true;
  }

  function quotaFailureKind(reason, queryState) {
    if (queryState === "paused") return "paused";
    if (queryState === "blocked") return "skipped";
    const text = String(reason || "");
    if (text.startsWith("本轮前序机器查询异常；本机尚未尝试")) return "skipped";
    if (text.startsWith("本轮同服务账号已有失败")) return "skipped";
    if (text.startsWith("本机已暂停该服务的自动请求")) return "paused";
    if (text.startsWith("同一服务已有操作，本次未发出请求。")) return "busy";
    return "error";
  }

  function describeDiagnosticSummary(add, detail) {
    add("错误摘要经过凭据脱敏、空白整理，最多保留 512 个字符；不是完整原始响应。");
    if (["[url redacted]", "[opaque value redacted]"].some(marker => detail?.includes(marker))) {
      add("这条摘要含有脱敏占位符，仅凭这条摘要无法还原被隐藏的地址或标识；其他日志或历史记录是否保留原文，尚未核实。占位符本身不证明地址有风险，也不能据此确认是哪一版规则处理的。新发生的失败会按当前规则记录，不会为补全缺失信息自动重试。");
    }
  }

  function recoveryDiagnostics(receipt, httpStatus) {
    const box = document.createElement("details");
    const summary = document.createElement("summary");
    summary.textContent = "查看本次诊断";
    box.appendChild(summary);
    const add = text => { const p = document.createElement("p"); p.textContent = text; box.appendChild(p); };
    const f = receipt.failure || {};
    add(receipt.ok ? "本次配额读取成功；当前暂停状态以重新读取的结果为准。" : "本次配额读取未完成；原因尚未确认，错误码本身不能证明网络故障或登录失效。");
    if (receipt.observed_at) add(`记录时间：${receipt.observed_at}`);
    if (receipt.request) add(`本次对象：${receipt.request.machine} · ${receipt.request.account_label} · 工作区 ${receipt.request.account_id}；原暂停 ${receipt.request.pause_id}`);
    if (httpStatus) add(`Monitor HTTP：${httpStatus}（本地接口结果，不是供应商 HTTP 状态）`);
    add(`停止阶段：${f.stage === "quota_read" ? "读取配额" : f.stage || "未记录"}；Codex RPC：${f.rpc_code ?? "未取得"}；上游 HTTP：${f.http_status ?? "未取得"}`);
    if (f.detail) add(`已记录的错误摘要：${f.detail}`);
    describeDiagnosticSummary(add, f.detail);
    if (f.returncode != null) add(`CLI 退出码：${f.returncode}`);
    if (receipt.record_error) add(receipt.record_error);
    const network = receipt.network;
    add(`出站策略：${network?.policy === "chromium-system" ? "执行主机的系统代理" : "未记录"}；公网出口 IP／地区：未核实。`);
    add("以下仅为本次连接观测；connected 表示代理桥建立了连接，不证明配额请求成功，也不能证明最终公网出口。其他设备的出口预览不能代替此读数。");
    if (!network?.events?.length) add("未保存可用的连接观测；不能据此断定没有发出请求。");
    for (const e of network?.events || []) add(`${e.at || "时间未记录"} · ${e.target} → ${e.route} · ${e.result}`);
    return box;
  }

  function createRecoveryPanel(machine = "self", {onContinue} = {}) {
    const make = (tag, text, className) => {
      const node = document.createElement(tag);
      if (text) node.textContent = text;
      if (className) node.className = className;
      return node;
    };
    const panel = make("section", "", "quota-recovery-panel");
    const title = make("h3", "正在读取恢复状态…");
    const guidance = make("p");
    const target = make("p", "", "recovery-target");
    const result = make("p", "", "recovery-result");
    const actions = make("div", "", "codex-account-controls");
    const effect = make("p", "", "recovery-effect");
    const notice = make("p");
    notice.setAttribute("role", "status");
    const technical = make("details", "", "recovery-technical");
    technical.appendChild(make("summary", "技术详情与复制诊断"));
    const context = make("div");
    const details = make("div");
    const steps = make("div", "", "recovery-steps");
    steps.hidden = true;
    const read = make("button", "更新状态");
    const probe = make("button", "重新检查配额");
    const help = make("button", "查看排查步骤");
    const resume = make("button", "返回发送操作");
    const copy = make("button", "复制诊断信息", "btn");
    for (const [button, action] of [[read,"read"],[probe,"probe"],[help,"help"],[resume,"continue"],[copy,"copy"]]) {
      button.type = "button";
      button.className = "btn";
      button.setAttribute("data-recovery-action", action);
    }
    const report = make("textarea");
    report.hidden = true; report.readOnly = true; report.rows = 10;
    report.setAttribute("aria-label", "诊断信息，可选择后复制");
    const copyNotice = make("p");
    copyNotice.setAttribute("role", "status");
    actions.append(probe, help, resume, read);
    technical.append(context, details, copy, copyNotice, report);
    panel.append(title, guidance, target, result, actions, effect, notice, steps, technical);
    let state = null, busy = false, pendingPauseId = null, unrecordedOutcome = false, refreshPending = false;
    const controller = new AbortController();
    window.AgentMonitor?.onPageCleanup?.(() => controller.abort());
    const when = value => value ? formatDate(value) : "时间未记录";
    async function requestJSON(url, options = {}, timeout = 20000) {
      const attempt = new AbortController();
      const abort = () => attempt.abort();
      controller.signal.addEventListener("abort", abort, {once:true});
      const timer = setTimeout(abort, timeout);
      try {
        if (controller.signal.aborted) abort();
        const response = await fetch(url, {...options, signal:attempt.signal});
        return {response, value:await response.json()};
      } finally {
        clearTimeout(timer);
        controller.signal.removeEventListener("abort", abort);
      }
    }
    function render(value) {
      const a = value?.account || {}, pause = value?.pause;
      const mode = !value ? "unknown" : value.active ? "active"
        : pendingPauseId ? "unknown-outcome" : !pause ? "ready"
        : pause.id === "unreadable" ? "unreadable" : pause.probe_attempt ? "used"
        : !a.account_id || !a.account_label ? "identity"
        : value.last_recovery?.ok === false ? "failed" : "paused";
      const copyText = {
        unknown: ["暂时无法确认恢复状态", "先更新状态。读取失败期间不能检查配额；下方技术详情可能是此前的记录。"],
        "unknown-outcome": ["本次检查结果尚未确认", "先更新状态核对本次检查的结果，暂不再次提交，避免重复检查。"],
        active: ["已有操作正在进行", "等待当前操作结束后更新状态。已有发送结果和账号选择保留，请勿重复提交。"],
        ready: ["当前没有通道暂停", onContinue ? "可以返回发送操作，核对账号登录及限制后继续。已有结果和选择保留，不会自动发送消息。" : "该机器的通道暂停已解除或当前不存在；其他机器和账号的可用性需分别查看。不会自动发送消息。"],
        unreadable: ["暂停记录无法读取", "发送仍暂停。先按排查步骤修复状态记录，再更新状态；不能直接清除暂停。"],
        used: ["本次暂停的检查机会已使用", "先更新状态查看已有结果。若仍未结束，按下方排查步骤处理；此处不能重复检查或重置机会。"],
        identity: ["查询账号身份不完整", "发送仍暂停。先核对该机器使用的账号配置，再更新状态；不会自动切换到其他账号。"],
        failed: ["配额检查未成功，发送仍暂停", "具体原因尚未确认。先查看排查步骤，处理后可再次检查；已有发送结果和账号选择保留。"],
        paused: ["发送已暂停，需要先检查配额", "Monitor 在此前请求异常后暂停了此机器的 Codex 发送、登录和配额查询。这是本地保护，不表示所有账号均不可用。"],
      };
      [title.textContent, guidance.textContent] = copyText[mode];
      if (!["failed","unreadable","identity","used"].includes(mode)) {
        steps.hidden = true; help.textContent = "查看排查步骤";
      }
      target.textContent = value ? `检查对象：${value.machine} · ${a.account_label || "账号未识别"}` : "";
      const last = value?.last_recovery;
      result.textContent = value ? (last
        ? `最近检查：${when(last.observed_at)} · ${last.ok ? "当次成功" : "未成功"}。当前状态以本卡片为准。`
        : `暂停记录：${when(pause?.at ? pause.at * 1000 : null)}。`) : "";
      if (value && !pause && !last) result.textContent = `状态更新：${when(value.observed_at)}。`;
      if (value && unrecordedOutcome) result.textContent = "最近检查：未取得该次结果记录。当前是否暂停以上方状态为准。";
      probe.hidden = !["paused", "failed"].includes(mode);
      probe.disabled = busy || probe.hidden;
      probe.textContent = mode === "failed" ? "处理后再次检查配额" : "检查配额并尝试恢复";
      effect.textContent = probe.hidden ? "更新状态只读取已有记录，不查询上游。" : "检查只查询一次配额，沿用该机器原有系统代理；不会发送消息。";
      help.hidden = !["failed", "unreadable", "identity", "used"].includes(mode);
      resume.hidden = mode !== "ready" || !onContinue;
      read.disabled = busy;
      read.textContent = ["unknown", "unknown-outcome"].includes(mode) ? "重新读取状态" : mode === "active" ? "更新进度" : "更新状态";
      const primary = mode === "paused" ? probe : ["failed","unreadable","identity"].includes(mode) ? help : !resume.hidden ? resume : read;
      for (const button of [read,probe,help,resume]) button.className = "btn" + (button === primary ? " btn-primary" : "");
      steps.replaceChildren();
      const list = make("ol");
      const host = value?.machine || "执行机器";
      const instructions = mode === "unreadable" ? [
        `在 ${host} 的终端运行 agent-monitor status，核对服务状态及显示的日志位置。`,
        "查看日志中的状态文件读取错误，修复对应文件的权限或格式；不要删除暂停记录来恢复发送。",
        "修复后点击“更新状态”。需要协助时，展开技术详情并复制诊断。"
      ] : mode === "identity" ? [
        "展开技术详情，查看账号来源和凭据位置，确认它是否为你要使用的 Codex 目录。",
        `在 ${host} 核对该目录的账号配置；如需重新登录，先处理本机暂停，不要在其他账号上试登录来绕过。`,
        "账号配置修正后点击“更新状态”。无法判断配置时，复制诊断供协助排查。"
      ] : [
        `核对上方检查对象。检查发生在 ${host}，不是自动使用打开网页这台设备的网络。`,
        `在 ${host} 打开现有代理程序或系统网络设置，确认原有代理正在运行、配置仍有效；保持原来的出口，不切换节点。错误码本身不能确认是代理或登录故障。`,
        mode === "used" ? "点击“更新状态”查看是否已有检查结果。若检查机会一直占用，复制诊断排查服务日志；不要删除暂停或重复提交。" : "处理后点击“处理后再次检查配额”，确认对象后只检查一次。若仍失败，保留结果并复制诊断继续定位，不反复盲试。"
      ];
      for (const text of instructions) list.appendChild(make("li", text));
      steps.append(make("strong", "排查步骤"), list);
      if (!value) return;
      context.replaceChildren();
      const add = text => context.appendChild(make("p", text));
      add(`执行机器：${value.machine}；状态读取于 ${when(value.observed_at)}。`);
      add(`查询账号：${a.account_label || "未取得"}；工作区：${a.account_id || "未取得"}。`);
      add(`账号来源：${({default:"默认 Codex 目录",managed:"托管账号 " + (a.profile || ""),configured:"服务配置指定目录"})[a.source] || "未知"}；凭据位置：${a.credential_path || "未取得"}。`);
      add("本地身份记录不证明上游登录有效。状态更新只读本地记录，不查询上游。检查配额使用执行机器的原有系统代理，不会发送或重发消息。");
      if (pause) {
        add(`当前暂停：${pause.id}；已记录的错误摘要：${pause.failure?.detail || "未记录，原因未核实"}`);
        describeDiagnosticSummary(add, pause.failure?.detail);
      }
      add("诊断包含账号、机器、路径和地址。对外分享前请核对接收者及内容，不要直接公开发布。");
      details.replaceChildren();
      if (last || pause?.failure) {
        const diagnostic = recoveryDiagnostics(last || {ok:false,failure:pause.failure});
        diagnostic.open = true;
        details.appendChild(diagnostic);
      } else details.appendChild(make("p", "尚无已保存的检查诊断。"));
    }
    async function refresh() {
      if (controller.signal.aborted) return;
      if (busy) { refreshPending = true; return; }
      refreshPending = false;
      busy = true; read.disabled = probe.disabled = true;
      panel.setAttribute("aria-busy", "true");
      notice.textContent = "正在读取状态，不查询上游…";
      try {
        const {response, value} = await requestJSON(`/api/quota-recovery?machine=${encodeURIComponent(machine)}`);
        if (!response.ok || !value.machine || !value.observed_at || !Object.hasOwn(value, "pause") || !Object.hasOwn(value, "active")) throw new Error(value.error || "状态回执不完整");
        if (controller.signal.aborted) return;
        state = value;
        if (pendingPauseId && value.last_recovery?.request?.pause_id === pendingPauseId) pendingPauseId = null;
        if (pendingPauseId && !value.active && !value.pause) {
          pendingPauseId = null;
          unrecordedOutcome = true;
        }
        busy = false; render(value);
        notice.textContent = "";
        document.dispatchEvent?.(new Event("agent-monitor:recovery-state"));
      } catch (error) {
        state = null; busy = false; render(null);
        notice.textContent = `状态读取失败：${error.message}`;
      } finally {
        busy = false; read.disabled = false; panel.setAttribute("aria-busy", "false");
        if (refreshPending && !controller.signal.aborted) refresh();
      }
    }
    panel.refresh = refresh;
    read.addEventListener("click", refresh);
    help.addEventListener("click", () => { steps.hidden = !steps.hidden; help.textContent = steps.hidden ? "查看排查步骤" : "收起排查步骤"; });
    resume.addEventListener("click", () => onContinue?.());
    copy.addEventListener("click", async () => {
      const text = ["Agent Monitor 恢复诊断", state ? "当前状态读取于 " + state.observed_at : "当前状态未核实；以下可能含历史记录", title.textContent, guidance.textContent, target.textContent, result.textContent, notice.textContent, context.textContent, details.textContent].join("\n\n");
      report.value = text; report.hidden = true;
      try {
        if (!window.navigator?.clipboard?.writeText) throw new Error("clipboard unavailable");
        await window.navigator.clipboard.writeText(text);
        copyNotice.textContent = "诊断信息已复制。";
      } catch (_) {
        report.hidden = false; report.focus(); report.select();
        copyNotice.textContent = "浏览器未允许自动复制；已选中下方信息，请手动复制。";
      }
    });
    probe.addEventListener("click", async () => {
      if (busy || probe.disabled || !state) return;
      const request = {machine:state.machine,account_id:state.account.account_id,account_label:state.account.account_label,pause_id:state.pause.id};
      if (!window.confirm(`在 ${request.machine} 为 ${request.account_label} 检查一次 Codex 配额？\n沿用该机器原有系统代理。不会发送消息，也不会自动重试。\n成功保存有效配额后解除该机器的暂停；失败仍暂停。`)) return;
      unrecordedOutcome = false;
      busy = true; read.disabled = probe.disabled = true;
      panel.setAttribute("aria-busy", "true");
      title.textContent = "正在检查配额…";
      guidance.textContent = "请等待本次检查结束。不会发送消息，请勿重复提交。";
      notice.textContent = "";
      let receipt, code;
      try {
        const {response, value} = await requestJSON("/api/quota-recovery", {method:"POST",headers:{"Content-Type":"application/json","X-Agent-Monitor":"action"},body:JSON.stringify(request)},90000);
        code = response.status; receipt = value;
      } catch (_) { /* Reconcile before enabling any further provider action. */ }
      pendingPauseId = !receipt || (typeof receipt.ok !== "boolean" && !receipt.failure) ? request.pause_id : null;
      busy = false;
      await refresh();
      if (receipt?.ok || receipt?.failure) {
        const diagnostic = recoveryDiagnostics({...receipt,request}, code);
        diagnostic.open = true; details.replaceChildren(diagnostic);
      }
      if (code && !receipt?.ok && !receipt?.failure) notice.textContent = `检查请求未完成（Monitor HTTP ${code}）。请先更新状态；详情见诊断。`;
    });
    refresh();
    return panel;
  }

  function quotaFailureDetails(provider, failures, previous) {
    const body = previous || document.createElement("tbody");
    if (!body.quotaDiagnostics) {
      const row = document.createElement("tr"), cell = document.createElement("td");
      cell.colSpan = 7;
      const details = document.createElement("details");
      details.className = "note-disclosure quota-failure-details";
      const summary = document.createElement("summary");
      details.appendChild(summary); cell.appendChild(details); row.appendChild(cell); body.appendChild(row);
      body.quotaDiagnostics = {provider:provider.key, details, summary, entries:[]};
    }
    const saved = body.quotaDiagnostics;
    const counts = {paused:0, skipped:0, busy:0, error:0};
    failures.forEach(({reason,query_state}) => { counts[quotaFailureKind(reason,query_state)]++; });
    const labels = [["paused","暂停"],["skipped","本轮未尝试"],["busy","通道占用未查询"],["error","查询异常"]];
    const status = labels.filter(([key]) => counts[key]).map(([key,label]) => `${counts[key]} 台${label}`).join("，");
    saved.summary.textContent = failures.length ? `${provider.label} 采集详情（${status}）` : `${provider.label} 最近恢复记录`;
    const remaining = [...saved.entries], next = [];
    for (const failure of failures) {
      const index = remaining.findIndex(record => record.machine === failure.machine);
      let record = index < 0 ? null : remaining.splice(index,1)[0];
      if (!record) {
        const entry = document.createElement("div"), note = document.createElement("p");
        entry.appendChild(note);
        record = {machine:failure.machine, entry, note, panel:null, pauseId:failure.pause?.id};
        if (provider.key === "codex" && failure.machine) {
          const open = document.createElement("button");
          open.type = "button"; open.textContent = "查看当前状态与恢复";
          open.addEventListener("click", () => {
            if (record.panel) return;
            open.disabled = true;
            record.panel = createRecoveryPanel(record.machine);
            entry.appendChild(record.panel);
          });
          entry.appendChild(open);
        }
      }
      record.note.textContent = `${failure.machine}：${failure.reason}`;
      if (provider.key === "codex" && failure.pause?.id) {
        record.entry.dataset.codexPauseId = failure.pause.id; record.entry.tabIndex = -1;
      } else delete record.entry.dataset.codexPauseId;
      if (record.pauseId !== failure.pause?.id) record.panel?.refresh();
      record.pauseId = failure.pause?.id;
      next.push(record);
    }
    for (const record of remaining) {
      if (record.panel) {
        record.note.textContent = `${record.machine} · 最近恢复记录；当前状态见下方卡片。`;
        delete record.entry.dataset.codexPauseId;
        if (record.pauseId !== null) record.panel.refresh();
        record.pauseId = null;
        next.push(record);
      } else record.entry.remove();
    }
    for (const record of next) saved.details.appendChild(record.entry);
    saved.entries = next;
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
      .map(({ reason, query_state }) => quotaFailureKind(reason, query_state)));
    const state = kinds.has("paused") ? "查询已暂停" : kinds.has("skipped") ? "上次查询未执行"
      : kinds.has("busy") ? "查询通道占用" : null;
    if (state) {
      const reading = hasValue ? (fresh ? (account.quota_observation?.source === "codex_session" ? "当前读数来自会话日志" : "当前读数仍在更新时限内") : "读数已过期，保留上次读数") : "暂无读数";
      return `${reading}；主动${state}，请展开“${provider.label} 采集详情”核对原因`;
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
  // See docs/references/metrics.md#account-attribution-and-quota-plans.
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
    const names = [...new Set(account.machines || [])];
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
    const sourceLabel = account.quota_source === "account_action" ? "账号操作中的配额查询"
      : {codex_session: "会话日志", active_query: "主动查询", claude_statusline: "状态栏"}[account.quota_observation?.source];
    exact.textContent += `；读数来源：${sourceLabel || "旧记录未注明"}`;
    details.appendChild(exact);
    if (account.account_profile) {
      const profile = document.createElement("span");
      profile.className = "quota-history-note";
      profile.textContent = `配置名：${account.account_profile}`;
      details.appendChild(profile);
    }
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

  function resetQuotaReadings() {
    codexQuotaReadings = [];
    renderedQuotaRateLimits = null;
  }
  function quotaSnapshot() { return renderedQuotaRateLimits; }
  return { createRecoveryPanel, recoveryDiagnostics, codexPauseTarget, showCodexPauseRecovery, quotaSnapshot, QUOTA_PROVIDERS, QUOTA_WINDOW_5H, resetText, latestCodexQuotaReading, updateCodexQuotaReadings, quotaDisplayReading, sortQuotaAccounts, quotaMatches, bindQuotaExplorer, quotaPresence, codexSessionAccountLabel, renderQuotaAccounts, quotaFailureKind, quotaFailureDetails, quotaRefreshNotice, renderQuotaSignedOut, appendRememberedAccounts, quotaBody, quotaAccountRow, quotaProviderCell, quotaPlanCell, quotaPlanSourcesDisagree, quotaAccountCell, quotaRemovalReading, removeRenderedAccount, refreshRememberedAccountSummary, removeRememberedAccount, quotaUsage, quotaBandPill, quotaWindowCell, quotaMeter, quotaResetText, quotaResetState, formatQuotaReset, quotaMachinesCell, quotaObservationAge, quotaUpdatedCell, quotaPlanLabel, quotaAccountName, quotaIsStale, quotaProviderPill, renderQuotaMachineRecords, renderQuotaMachineNotice, quotaWorstUsage, quotaUnavailableBody, quotaFilter, resetQuotaReadings };
});
