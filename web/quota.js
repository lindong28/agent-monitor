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
  let initialQuotaHistoryExpanded = false;
  const quotaFilter = { provider: "all", search: "", window: "five_hour", sort: "default" };
  let boundQuotaExplorer = null;

  function latestCodexQuotaReading(account) {
    return [account.operation?.after, account.operation?.before, account.batch_result?.after, account.batch_result?.before, account.last_quota]
      .filter((reading) => Number.isFinite(Date.parse(reading?.observed_at)))
      .sort((a, b) => Date.parse(b.observed_at) - Date.parse(a.observed_at))[0];
  }

  function updateCodexQuotaReadings(accounts) {
    const signature = (profiles) => JSON.stringify(profiles.map((account) => [
      account.id, account.account_id, account.email.toLowerCase(), Boolean(account.disabled), latestCodexQuotaReading(account),
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
    if (!reading || (Number.isFinite(Date.parse(account.updated_at)) && Date.parse(reading.observed_at) <= Date.parse(account.updated_at))) return account;
    const display = { ...account, updated_at: reading.observed_at, quota_source: "account_action",
      quota_observation: {source: "active_query", observed_at: reading.observed_at} };
    for (const spec of QUOTA_COLUMNS) {
      for (const key of [spec.used, spec.reset]) {
        display[key] = typeof reading[key] === "number" && Number.isFinite(reading[key]) ? reading[key] : null;
      }
    }
    display.reading_plan = typeof reading.plan === "string" && reading.plan ? reading.plan : null;
    display.account_plan = display.reading_plan || display.credential_plan || null;
    display.plan_verification = display.account_plan ? null : "unverified";
    return display;
  }

  function quotaAccountDisabled(provider, account) {
    return provider.key === "codex" && codexQuotaReadings.some(profile => profile.disabled
      && profile.account_id === account.account_id
      && profile.email.toLowerCase() === account.account_label?.toLowerCase());
  }

  function sortQuotaEntries(entries, now = Date.now() / 1000) {
    if (quotaFilter.sort === "default") return entries.slice();
    const readingValue = ({ provider, account }) => {
      const reading = quotaDisplayReading(provider, account);
      if (["observed", "observed_asc"].includes(quotaFilter.sort)) {
        const observed = Date.parse(reading.updated_at);
        return Number.isFinite(observed) ? [quotaFilter.sort === "observed" ? -observed : observed] : null;
      }
      const remaining = ["remaining", "remaining_desc"].includes(quotaFilter.sort);
      const key = quotaFilter.window + (remaining ? "_used_pct" : "_resets_at");
      const value = reading[key];
      if (typeof value !== "number" || !Number.isFinite(value)) return null;
      if (remaining) {
        if (!quotaUsage(value).known) return null;
        return [quotaFilter.sort === "remaining_desc" ? value - 100 : 100 - value];
      }
      return value > now ? [quotaFilter.sort === "reset_desc" ? -value : value] : null;
    };
    return entries.map((entry, index) => ({ entry, index, value: readingValue(entry) }))
      .sort((a,b) => a.value === null ? (b.value === null ? a.index-b.index : 1)
        : b.value === null ? -1 : a.value[0]-b.value[0] || (a.value[1] || 0)-(b.value[1] || 0) || a.index-b.index)
      .map(item => item.entry);
  }

  function sortQuotaAccounts(provider, accounts, now = Date.now() / 1000) {
    return sortQuotaEntries(accounts.map(account => ({provider, account})), now).map(entry => entry.account);
  }

  function quotaMatches(provider, account) {
    if (quotaFilter.provider !== "all" && quotaFilter.provider !== provider.key) return false;
    const terms = quotaFilter.search.trim().toLowerCase().split(/\s+/).filter(Boolean);
    account = quotaDisplayReading(provider, account);
    const text = [account.account_label, account.account_id, account.account_profile, quotaAccountName(account),
      account.account_plan, quotaPlanLabel(account.account_plan, provider),
      account.reading_plan, account.credential_plan, ...(account.machines || [])]
      .filter(Boolean).join(" ").toLowerCase();
    return terms.every((term) => text.includes(term));
  }

  const QUOTA_SORT_DIRECTIONS = {
    usage: ["remaining_desc", "remaining"],
    reset: ["reset", "reset_desc"],
    observed: ["observed_asc", "observed"],
  };

  function quotaSortButtons() {
    return qs("#quota-accounts")?.querySelectorAll?.("button[data-quota-sort]") || [];
  }

  function syncQuotaSort() {
    ["window", "sort"].forEach(key => {
      const select = qs("#quota-" + key);
      if (select) select.value = quotaFilter[key];
    });
    quotaSortButtons().forEach(button => {
      const directions = QUOTA_SORT_DIRECTIONS[button.dataset.quotaSort];
      const active = directions.includes(quotaFilter.sort)
        && (!button.dataset.quotaWindow || button.dataset.quotaWindow === quotaFilter.window);
      const ascending = quotaFilter.sort === directions[0];
      button.parentNode.setAttribute("aria-sort", active ? (ascending ? "ascending" : "descending") : "none");
      button.querySelector(".quota-sort-icon").textContent = active ? (ascending ? "↑" : "↓") : "↕";
    });
  }

  function bindQuotaExplorer() {
    const explorer = qs("#quota-explorer");
    if (!explorer || boundQuotaExplorer === explorer) return;
    boundQuotaExplorer = explorer;
    quotaSortButtons().forEach(button => button.addEventListener("click", () => {
      const directions = QUOTA_SORT_DIRECTIONS[button.dataset.quotaSort];
      const sameWindow = !button.dataset.quotaWindow || button.dataset.quotaWindow === quotaFilter.window;
      const initial = button.dataset.quotaSort === "observed" ? directions[1] : directions[0];
      quotaFilter.sort = sameWindow && directions.includes(quotaFilter.sort)
        ? directions[1 - directions.indexOf(quotaFilter.sort)] : initial;
      if (button.dataset.quotaWindow) quotaFilter.window = button.dataset.quotaWindow;
      renderQuotaAccounts(renderedQuotaRateLimits);
    }));
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

  function quotaFailureMatchesAccount(failure, account, accounts = []) {
    if (!failure || !account || !failure.machine || !(account.machines || []).includes(failure.machine)) {
      return false;
    }
    const failureId = typeof failure.account_id === "string" && failure.account_id ? failure.account_id : null;
    const failureLabel = typeof failure.account_label === "string" && failure.account_label
      ? failure.account_label.toLowerCase() : null;
    // A machine-only failure can be kept compatible for a machine with one
    // live account, but cannot be projected onto one of several account rows.
    if (!failureId && !failureLabel) {
      const candidates = accounts.filter((candidate) => quotaPresence(candidate) === "in_use"
        && (candidate.machines || []).includes(failure.machine));
      return candidates.length === 1 && candidates[0] === account;
    }
    const accountId = typeof account.account_id === "string" && account.account_id ? account.account_id : null;
    const accountLabel = typeof account.account_label === "string" && account.account_label
      ? account.account_label.toLowerCase() : null;
    return (!failureId || failureId === accountId) && (!failureLabel || failureLabel === accountLabel);
  }

  function codexSessionAccountLabel(account) {
    return account ? `账号：${account.name} · ${account.email}` : "账号归属未记录或冲突";
  }

  function renderQuotaSettings(rateLimits) {
    const settings = qs("#quota-auto-settings");
    if (!settings) return;
    const machines = [...new Set((rateLimits?.codex?.accounts || []).flatMap(row => row.machines || []))].sort();
    for (const machine of machines) {
      if (Array.from(settings.querySelectorAll("[data-quota-settings-machine]")).some(node => node.dataset.quotaSettingsMachine === machine)) continue;
      const group = document.createElement("details");
      group.dataset.quotaSettingsMachine = machine;
      const title = document.createElement("summary");
      title.textContent = machine;
      const content = document.createElement("div");
      group.append(title, content);
      settings.appendChild(group);
      group.addEventListener("toggle", () => {
        if (group.open && !content.childNodes.length) content.appendChild(createQuotaSettingsPanel(machine));
      });
    }
  }

  function createQuotaSettingsPanel(machine) {
    const panel = document.createElement("section");
    const intro = document.createElement("p");
    const form = document.createElement("div");
    const notice = document.createElement("p");
    notice.setAttribute("role", "status");
    const read = document.createElement("button"), save = document.createElement("button");
    read.type = save.type = "button";
    read.className = save.className = "btn";
    read.textContent = "更新设置状态"; save.textContent = "保存自动查询设置";
    panel.append(intro, form, save, read, notice);
    let state = null, busy = false;
    async function request(options) {
      const response = await fetch(`/api/quota-settings${options ? "" : "?machine=" + encodeURIComponent(machine)}`, {
        headers: {"Content-Type": "application/json", "X-Agent-Monitor": "action"},
        signal: AbortSignal.timeout(30000), ...options,
      });
      const value = await response.json();
      if (!response.ok) throw new Error(value.error || "设置未能确认");
      return value;
    }
    function render(value) {
      state = value; form.textContent = "";
      intro.hidden = false;
      intro.textContent = `原生 Codex 继续按现有采集周期更新。下列额外登录账号可每 ${Math.round(value.interval_seconds / 60)} 分钟查询一次配额；请求通过 ${machine} 的 Agent Monitor 路由桥。不会发送消息。`;
      const followLabel = document.createElement("label"), follow = document.createElement("input");
      follow.type = "checkbox"; follow.dataset.followRunning = ""; follow.checked = Boolean(value.follow_running);
      followLabel.append(follow, document.createTextNode(" 自动跟随运行中的 codex-account 账号（含以后新增的账号）"));
      const followLine = document.createElement("p"); followLine.append(followLabel); form.append(followLine);
      const explanation = document.createElement("p");
      explanation.textContent = "任务运行期间按上述周期查询，最后一个任务退出后停止跟随。下列固定勾选的账号退出后仍会查询。只检测新版启动器登记的任务。";
      form.append(explanation);
      for (const row of value.accounts) {
        const label = document.createElement("label"), checkbox = document.createElement("input");
        checkbox.type = "checkbox"; checkbox.dataset.quotaProfile = ""; checkbox.value = row.key; checkbox.checked = row.enabled;
        label.appendChild(checkbox);
        const running = Number(row.running_tasks || 0);
        const activity = running ? `${running} 个任务运行中` : "未检测到运行任务";
        const following = value.follow_running && running ? " · 自动跟随中" : "";
        label.appendChild(document.createTextNode(` ${row.account_label} · ${row.profile} — ${activity}${following}`));
        if (row.activity_checked_at) {
          const observed = document.createElement("small");
          observed.textContent = `（检测于 ${new Date(row.activity_checked_at).toLocaleTimeString()}）`;
          label.append(observed);
        }
        if (row.activity_error) label.append(document.createTextNode(` · ${row.activity_error}`));
        const line = document.createElement("p"); line.appendChild(label); form.appendChild(line);
      }
      if (!value.accounts.length) {
        const empty = document.createElement("p"); empty.textContent = "这台机器尚未发现额外登录账号；开启跟随后，新注册账号运行任务时也会加入。"; form.append(empty);
      }
      save.hidden = false;
    }
    async function refresh() {
      if (busy) return;
      busy = true; read.disabled = save.disabled = true;
      notice.textContent = "正在读取设置…";
      try { render(await request()); notice.textContent = ""; }
      catch (error) { state = null; notice.textContent = error.message; }
      finally { busy = false; read.disabled = false; save.disabled = !state; }
    }
    read.addEventListener("click", refresh);
    save.addEventListener("click", async () => {
      if (busy || !state) return;
      const selected = Array.from(form.querySelectorAll("input[data-quota-profile]:checked")).map(node => node.value).sort();
      const previous = state.accounts.filter(row => row.enabled).map(row => row.key).sort();
      const follow_running = form.querySelector("[data-follow-running]").checked;
      if (follow_running === Boolean(state.follow_running) && JSON.stringify(selected) === JSON.stringify(previous)) { notice.textContent = "设置未变化。"; return; }
      busy = true; read.disabled = save.disabled = true;
      let submitted = false;
      try {
        let confirmation;
        if (selected.some(key => !previous.includes(key)) || (follow_running && !state.follow_running)) {
          const rows = state.accounts.filter(row => selected.includes(row.key));
          confirmation = await window.AgentMonitor.previewUpstream?.(
            rows.map(row => ({email: row.account_label, account_id: row.account_id})),
            {approval_action: "quota-settings", approval_scope: [machine, ...selected, ...(follow_running ? ["follow-running"] : [])], approval_expected: state.revision, approval_machine: machine});
          if (!confirmation) { notice.textContent = "已取消，设置未改变。"; return; }
        }
        submitted = true;
        render(await request({method: "POST", body: JSON.stringify({machine, revision: state.revision, selected, follow_running, confirmation})}));
        notice.textContent = "设置已保存；固定勾选或运行跟随中的账号将按周期查询。关闭后停止后续查询，已发出的请求会完成。";
      } catch (error) {
        notice.textContent = submitted ? "保存结果未确认；请先更新设置状态，勿重复提交。" : error.message;
        if (submitted) state = null;
      } finally { busy = false; read.disabled = false; save.disabled = !state; }
    });
    refresh();
    return panel;
  }

  function renderQuotaAccounts(rateLimits, options = {}) {
    const table = qs("#quota-accounts");
    const reset = qs("#quota-clear");
    const windowControl = qs("#quota-window-control");
    if (windowControl) windowControl.hidden = !["remaining", "remaining_desc", "reset", "reset_desc"].includes(quotaFilter.sort);
    if (reset) reset.hidden = quotaFilter.provider === "all" && !quotaFilter.search
      && quotaFilter.window === "five_hour" && quotaFilter.sort === "default";
    if (!table) {
      return;
    }
    const viewport = {
      left: Number.isFinite(window.scrollX) ? window.scrollX : 0,
      top: Number.isFinite(window.scrollY) ? window.scrollY : 0,
    };
    renderedQuotaRateLimits = rateLimits;
    bindQuotaExplorer();
    syncQuotaSort();
    const history = qs("#quota-past-accounts");
    const rememberedExpanded = options.rememberedExpanded ?? (history ? !history.hidden : initialQuotaHistoryExpanded);
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
    const currentEntries = [];
    const allEntries = QUOTA_PROVIDERS.flatMap((provider) =>
      (rateLimits?.[provider.key]?.accounts || []).filter((account) => account.account_state === "known")
        .map((account) => ({ provider, account })));
    const matches = allEntries.filter(({ provider, account }) => quotaMatches(provider, account));
    const count = qs("#quota-match-count");
    if (count) {
      const known = matches.filter(({ account }) => account.account_state === "known");
      const disabled = known.filter(({ provider, account }) => quotaAccountDisabled(provider, account)).length;
      const current = known.filter(({ provider, account }) => quotaPresence(account) === "in_use" && !quotaAccountDisabled(provider, account)).length;
      const history = known.filter(({ account }) => quotaPresence(account) === "remembered").length;
      const filtered = quotaFilter.provider !== "all" || quotaFilter.search.trim();
      count.textContent = (filtered ? `匹配 ${matches.length} / ${allEntries.length} 个账号 · ` : "")
        + `机器关联 ${current} · 历史账号 ${history}` + (disabled ? ` · 已停用 ${disabled}` : "");
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
      sourceAccounts.filter((account) => account.account_state === "known" && quotaMatches(provider, account))
        .forEach((account) => {
          if (quotaPresence(account) === "remembered" || quotaAccountDisabled(provider, account)) remembered.push({provider, account});
          else if (quotaPresence(account) === "in_use") currentEntries.push({provider, account});
        });
    });

    if (currentEntries.length) {
      const body = document.createElement("tbody");
      sortQuotaEntries(currentEntries).forEach(({provider, account}) => body.appendChild(quotaAccountRow(provider, account)));
      table.appendChild(body);
    }
    renderQuotaMachineRecords(rateLimits);
    appendRememberedAccounts(table, sortQuotaEntries(remembered), rememberedExpanded);
    if (remembered.length) initialQuotaHistoryExpanded = false;
    if (!matches.length && (quotaFilter.provider !== "all" || quotaFilter.search.trim())) {
      const body = document.createElement("tbody");
      body.className = "quota-filter-empty";
      const cell = document.createElement("td");
      cell.colSpan = 9;
      cell.textContent = "没有匹配的配额记录。可清空筛选查看全部；这不表示配额可用或已用尽。";
      const row = document.createElement("tr");
      row.appendChild(cell);
      body.appendChild(row);
      table.appendChild(body);
    }
    const overviewStatus = qs('#quota-overview-status');
    if (overviewStatus) {
      const affected = QUOTA_PROVIDERS.flatMap(provider => (rateLimits?.[provider.key]?.refresh_errors || []).map(f => f.machine).filter(Boolean));
      overviewStatus.textContent = affected.length ? `${new Set(affected).size} 台机器的配额更新有异常，已有读数保留。查看采集与诊断了解原因。` : '';
    }
    QUOTA_PROVIDERS.forEach((provider) => {
      if (overviewStatus) return;
      const failures = rateLimits?.[provider.key]?.refresh_errors || [];
      const previous = previousDiagnostics.get(provider.key);
      if (failures.length || previous?.quotaDiagnostics.entries.some(record => record.panel)) {
        table.appendChild(quotaFailureDetails(provider, failures, previous));
      }
    });
    Array.from(table.querySelectorAll?.(".quota-observation-details") || []).forEach((node) => {
      if (node.tagName === "DETAILS") node.open = openObservations.has(node.dataset.observationKey);
      if (focusedObservation && node.dataset.observationKey === focusedObservation) {
        node.querySelector?.("summary")?.focus({ preventScroll: true });
      }
    });
    if (focusedRecovery && table.contains?.(focusedRecovery)) focusedRecovery.focus?.({preventScroll:true});
    renderQuotaSignedOut(rateLimits);
    updateSyncOverviewLink();
    document.dispatchEvent?.(new Event("agent-monitor:quota-rendered"));
    window.scrollTo?.({ left: viewport.left, top: viewport.top, behavior: "auto" });
  }

  function codexPauseTarget(pauseId) {
    if (!pauseId || pauseId === "unreadable") return null;
    const matches = (renderedQuotaRateLimits?.codex?.refresh_errors || [])
      .filter((failure) => failure.pause?.id === pauseId);
    const machines = new Set(matches.map(failure => failure.machine));
    if (!matches.length || machines.size !== 1 || !matches[0].machine) return null;
    // The destination is the machine-wide pause card. Its read-only status
    // resolves the recovery account; affected accounts are not probe targets.
    return matches[0];
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
    if (queryState === "blocked" || queryState === "skipped") return "skipped";
    const text = String(reason || "");
    if (text.startsWith("本轮前序机器查询异常；本机尚未尝试")) return "skipped";
    if (text.startsWith("本轮同服务账号已有失败")) return "skipped";
    if (text.startsWith("本机已暂停该服务的自动请求")) return "paused";
    if (text.startsWith("同一服务已有操作，本次未发出请求。")) return "busy";
    return "error";
  }

  function quotaFailureGuidance(failure) {
    if (failure?.query_state === "skipped") {
      return {
        key: "egress_skipped", label: "自动查询已跳过", summary: "出口地区保护",
        action: "保留上次额度读数；下一个正常采集周期会重新检查出口。无需解除账号暂停。本地统计与 GitHub 更新继续。",
      };
    }
    const detail = failure?.failure || {};
    const reason = String(failure?.reason || "");
    if (detail.kind === "account_mismatch" || reason.includes("登录账号已变化")) {
      return {
        key: "identity",
        label: "身份变化",
        summary: "账号身份变化",
        action: "先确认这台机器当前登录的是目标账号；页面不能替你切换机器账号。确认后打开恢复卡片更新状态。",
      };
    }
    const tokenCode = ["token_revoked", "token_expired"].find(code => detail.upstream_code === code || reason.includes(`upstream_code=${code}`));
    const hasTokenCode = Boolean(tokenCode);
    const hasHttp401 = detail.http_status === 401 || reason.includes("http_status=401");
    const hasAuthHint = reason.includes("invalidated oauth token");
    if (hasTokenCode || hasHttp401 || hasAuthHint) {
      const signal = hasTokenCode && hasHttp401 ? `401/${tokenCode}`
        : hasHttp401 ? "HTTP 401" : hasTokenCode ? tokenCode : "认证错误摘要";
      return {
        key: "upstream_auth",
        label: "上游认证响应",
        summary: `上游返回 ${signal}，原因未核实`,
        action: "先打开“查看当前状态与恢复”，通过目标机器的 Agent Monitor 路由桥只检查一次；若仍失败，需由 Agent Monitor 管理员核对该机器的账号、部署与上游认证。",
      };
    }
    if (detail.kind === "rpc_error" || detail.stage === "quota_read"
        || reason.includes("kind=rpc_error") || reason.includes("stage=quota_read")) {
      return {
        key: "read_error",
        label: "读取失败",
        summary: "配额读取失败，原因未核实",
        action: "先打开“查看当前状态与恢复”；检查会沿目标机器的 Agent Monitor 路由桥进行，仍失败时复制诊断交由管理员排查。",
      };
    }
    if (quotaFailureKind(failure.reason, failure.query_state) === "paused") {
      return {
        key: "paused",
        label: "本地暂停",
        summary: "本地保护暂停",
        action: "先查看恢复状态，原因未核实前不要重试。",
      };
    }
    if (quotaFailureKind(failure.reason, failure.query_state) === "skipped") {
      return {
        key: "skipped",
        label: "未执行",
        summary: "本轮未执行",
        action: "先处理前序机器的失败，再决定是否检查配额。",
      };
    }
    if (quotaFailureKind(failure.reason, failure.query_state) === "busy") {
      return {
        key: "busy",
        label: "通道占用",
        summary: "配额通道占用",
        action: "等待当前操作结束后再检查，不要并发重试。",
      };
    }
    return {
      key: "unknown",
      label: "原因未核实",
      summary: "配额读取未完成，原因未核实",
      action: "先查看采集记录与错误详情，不要直接重试。",
    };
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
    box.className = "recovery-diagnostic";
    box.appendChild(summary);
    const body = document.createElement("div");
    body.className = "disclosure-body";
    box.appendChild(body);
    const add = text => { const p = document.createElement("p"); p.textContent = text; body.appendChild(p); };
    const f = receipt.failure || {};
    add(receipt.ok ? "本次配额读取成功；当前暂停状态以重新读取的结果为准。" : f.stage === "recovery_commit" ? "配额复验已有候选结果，本地保存或发布未完成；请先核对已有结果，不要重复授权或查询。" : "本次配额读取未完成；原因尚未确认，错误码本身不能证明网络故障或登录失效。");
    if (receipt.observed_at) add(`记录时间：${receipt.observed_at}`);
    if (receipt.request) add(`本次对象：${receipt.request.machine} · ${receipt.request.account_label} · 工作区 ${receipt.request.account_id}；原暂停 ${receipt.request.pause_id}`);
    if (receipt.previous_recovery?.authentication_recovery?.outcome === "no_new_credentials") add("此前官方刷新未确认取得新凭据，之后才执行本次重新授权；此前恢复记录仍保留。 ");
    if (httpStatus) add(`Monitor HTTP：${httpStatus}（本地接口结果，不是供应商 HTTP 状态）`);
    add(`停止阶段：${f.stage === "quota_read" ? "读取配额" : f.stage === "recovery_commit" ? "本地保存与发布" : f.stage || "未记录"}；Codex RPC：${f.rpc_code ?? "未取得"}；上游 HTTP：${f.http_status ?? "未取得"}`);
    if (f.error_type) add(`错误类别：${f.error_type}${f.errno != null ? ` · errno ${f.errno}` : ""}`);
    if (f.detail) add(`已记录的错误摘要：${f.detail}`);
    const auth = receipt.authentication_recovery;
    if (auth) {
      const outcomes = {quota_confirmed: "已取得新配额", quota_failed: "新凭据后的配额查询仍失败", no_new_credentials: "未确认取得新凭据，未查询配额", new_credentials: "已保存新候选凭据，配额未确认", identity_changed: "身份发生变化，已停止", refresh_failed: "认证修复未完成"};
      add(`认证修复：${outcomes[auth.outcome] || "结果未确认"}。`);
      if (auth.initial_failure) add(`修复前：上游 HTTP ${auth.initial_failure.http_status ?? "未取得"} · ${auth.initial_failure.upstream_code || "未取得错误代码"}`);
      if (auth.refresh_failure) add(`官方认证 RPC 摘要：${auth.refresh_failure.detail || "未返回详细原因"}；RPC ${auth.refresh_failure.rpc_code ?? "未取得"}。此 RPC 结果不能单独证明凭据刷新成败。`);
      if (auth.client_diagnostics) add(`客户端诊断摘录：${auth.client_diagnostics}。已脱敏并截断至最多 512 字符；仅为客户端日志，不作为上游错误分类或刷新成败依据。`);
    }
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

  function createRecoveryPanel(machine = "self", {onContinue, onSaved, sourceFailure = null, profile = "", pauseScope = ""} = {}) {
    function canRenewAuth(value) {
      // Recovery eligibility belongs to the server; do not duplicate its code list.
      return value?.account?.auth_recovery_available === true;
    }
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
    const relogin = make("button", "重新授权并复验配额");
    relogin.hidden = true;
    const commit = make("button", "保存已有配额结果");
    commit.hidden = true;
    const help = make("button", "查看排查步骤");
    const resume = make("button", "返回账号操作");
    const copy = make("button", "复制诊断信息", "btn");
    for (const [button, action] of [[read,"read"],[probe,"probe"],[relogin,"reauthorize"],[commit,"commit"],[help,"help"],[resume,"continue"],[copy,"copy"]]) {
      button.type = "button";
      button.className = "btn";
      button.setAttribute("data-recovery-action", action);
    }
    const report = make("textarea");
    report.hidden = true; report.readOnly = true; report.rows = 10;
    report.setAttribute("aria-label", "诊断信息，可选择后复制");
    const copyNotice = make("p");
    copyNotice.setAttribute("role", "status");
    actions.append(probe, relogin, commit, help, resume, read);
    const technicalBody = make("div", "", "disclosure-body recovery-technical-body");
    technicalBody.append(context, details, copy, copyNotice, report);
    technical.appendChild(technicalBody);
    panel.append(title, guidance, target, result, actions, effect, notice, steps, technical);
    let state = null, busy = false, pendingPauseId = null, unrecordedOutcome = false, refreshPending = false;
    const controller = new AbortController();
    panel.dispose = () => controller.abort();
    window.AgentMonitor?.onPageCleanup?.(panel.dispose);
    const when = value => value ? formatDate(value) : "时间未记录";
    async function requestJSON(url, options = {}, timeout = 20000) {
      const attempt = new AbortController();
      const abort = () => attempt.abort();
      controller.signal.addEventListener("abort", abort, {once:true});
      const timer = setTimeout(abort, timeout);
      try {
        if (controller.signal.aborted) abort();
        const response = await fetch(url, {...options, headers:{'X-Agent-Monitor':'action', ...options.headers}, signal:attempt.signal});
        return {response, value:await response.json()};
      } finally {
        clearTimeout(timer);
        controller.signal.removeEventListener("abort", abort);
      }
    }
    function render(value) {
      const a = value?.account || {}, pause = value?.pause, last = value?.last_recovery;
      const sharedPause = value?.shared_pause || (value?.pause_scope !== "account" && a.account_quota_pause ? pause : null);
      const layeredPause = Boolean(sharedPause && (value?.pause_scope === "account" ? pause : a.account_quota_pause));
      const sharedResumeCommand = layeredPause && /^[a-f0-9]{32}$/.test(sharedPause.id)
        ? `agent-monitor upstream-holds resume codex --pause-id ${sharedPause.id}` : null;
      const lastRequest = last?.request;
      const lastMatchesPause = last?.ok === false && pause?.id && lastRequest
        && (lastRequest.pause_id === pause.id || last.pause_id === pause.id)
        && lastRequest.machine === value.machine && lastRequest.account_id === a.account_id
        && String(lastRequest.account_label || "").toLowerCase() === String(a.account_label || "").toLowerCase()
        && (lastRequest.profile || "") === (a.quota_profile || "");
      const failure = pause?.failure || (lastMatchesPause ? last.failure : null);
      const authUnresolved = lastMatchesPause && lastRequest.mode === "renew_auth"
        && last.authentication_recovery?.outcome === "no_new_credentials";
      const hasFailure = failure && typeof failure === "object" && Object.keys(failure).length > 0;
      const failureGuidance = hasFailure ? quotaFailureGuidance({
        reason: failure.detail || "", failure, query_state: "paused",
      }) : null;
      const sourceGuidance = sourceFailure ? quotaFailureGuidance(sourceFailure) : null;
      const sourceMismatch = sourceGuidance && failureGuidance
        && ["identity", "upstream_auth", "read_error"].includes(sourceGuidance.key)
        && ["identity", "upstream_auth", "read_error"].includes(failureGuidance.key)
        && sourceGuidance.key !== failureGuidance.key;
      const authEvidence = failureGuidance?.key === "upstream_auth"
        ? failureGuidance.summary : "上游认证响应，原因未核实";
      const mismatchEvidence = sourceMismatch
        ? `页面汇总显示“${sourceGuidance.summary}”，目标机器状态显示“${failureGuidance.summary}”`
        : "";
      const mode = !value ? "unknown" : value.active ? "active"
        : pendingPauseId ? "unknown-outcome" : !pause ? "ready"
        : pause.id === "unreadable" ? "unreadable" : pause.probe_attempt ? "used"
        : a.blocked_reason || value.shared_pause ? "account-blocked"
        : !a.account_id || !a.account_label ? "identity"
        : failure?.stage === "recovery_commit" ? "local-commit"
        : authUnresolved ? "auth-unresolved"
        : lastMatchesPause ? "failed"
        : sourceMismatch ? "source-mismatch"
        : failureGuidance?.key === "identity" ? "identity"
        : failureGuidance?.key === "upstream_auth" ? "upstream-auth"
        : failureGuidance?.key === "read_error" ? "read-error"
        : "paused";
      const copyText = {
        unknown: ["暂时无法确认恢复状态", "先更新状态。读取失败期间不能检查配额；下方技术详情可能是此前的记录。"],
        "unknown-outcome": ["本次检查结果尚未确认", "先更新状态核对本次检查的结果，暂不再次提交，避免重复检查。"],
        active: ["已有操作正在进行", "等待当前操作结束后更新状态。已有发送结果和账号选择保留，请勿重复提交。"],
        ready: ["当前没有通道暂停", onContinue ? "可以返回账号操作，核对待完成的查询或发送。已有结果和选择保留，不会自动发起请求。" : "该机器的通道暂停已解除或当前不存在；其他机器和账号的可用性需分别查看。不会自动发送消息。"],
        "account-blocked": ["此账号有本地限制，不执行恢复查询", a.blocked_reason || "请在账号卡片核对状态；已停用账号不应作为其他账号继续查询的前置条件。"],
        unreadable: ["暂停记录无法读取", "发送仍暂停。先按排查步骤修复状态记录，再更新状态；不能直接清除暂停。"],
        used: ["本次暂停的检查机会已使用", "先更新状态查看已有结果。若仍未结束，按下方排查步骤处理；此处不能重复检查或重置机会。"],
        identity: ["查询账号身份需要确认", "发送仍暂停。先在目标机器确认当前登录的是这个账号；页面不能替你切换机器账号。确认后更新状态。"],
        "source-mismatch": ["恢复状态来源不一致，需要核对", `${mismatchEvidence}；两份记录不一致，页面不能据此判断根因。先通过目标机器的 Agent Monitor 路由桥只检查一次；若仍失败，页面不能替你修复该机器的账号或部署。`],
        "upstream-auth": ["上游认证响应需要核对", `已记录${authEvidence}；这条读数不能区分凭据、权限或服务端原因。先通过目标机器的 Agent Monitor 路由桥只检查一次；若仍失败，页面不能替你修复该机器的账号或部署。`],
        "read-error": ["配额读取失败，需要一次受控检查", "已记录读取阶段失败，但原因未核实。先通过目标机器的 Agent Monitor 路由桥只检查一次；仍失败时复制诊断交由管理员排查。"],
        failed: ["配额检查未成功，发送仍暂停", "具体原因尚未确认。先查看排查步骤，处理后可再次检查；已有发送结果和账号选择保留。"],
        "auth-unresolved": ["认证刷新未取得新凭据", "本次没有继续查询配额，暂停保留。官方接口未提供明确失败原因；先查看诊断，不要重复查询同一份凭据。"],
        "local-commit": ["配额已有候选结果，本地结清未完成", "暂停保留。先查看本地保存或发布错误及已有结果；不要重新授权或再次查询配额。"],
        paused: ["Codex 请求已暂停", "此前请求异常触发本机通道保护，不表示所有账号不可用。已确认仅某账号异常时，可在账号卡片停用该账号并核对暂停归属；其他情况先检查下面的目标与诊断。"],
      };
      [title.textContent, guidance.textContent] = copyText[mode];
      if (mode === "account-blocked" && layeredPause) {
        title.textContent = "账号与本机通道均已暂停";
        guidance.textContent = "先由维护者核对并处理通道故障，再明确解除通道暂停；此账号的暂停继续保留。查看下方排查步骤。";
      }
      if (value?.pause_scope === "account") {
        if (mode === "ready") {
          title.textContent = "此账号当前没有配额暂停";
          guidance.textContent = "可以返回账号操作。不会自动查询、登录或发送；其他账号及通道限制分别保留。";
        } else if (mode === "paused") {
          title.textContent = "此账号的配额查询已暂停";
          guidance.textContent = "本次失败已隔离，其他独立账号可继续刷新。处理此账号后才恢复它的查询。";
        } else if (mode === "account-blocked" && value.shared_pause) {
          title.textContent = "账号与本机通道均已暂停";
          guidance.textContent = "先由维护者核对并处理通道故障，再明确解除通道暂停；此账号的暂停继续保留。查看下方排查步骤。";
        } else if (mode === "failed") {
          title.textContent = "此账号的配额检查未成功";
          guidance.textContent = "此账号暂停保留。先查看本次诊断；其他账号的结果与限制分别保留。";
        } else if (mode === "identity") {
          title.textContent = "此账号的登录身份需要确认";
          guidance.textContent = "此账号暂停保留。先核对目标账号与工作区，不能用其他身份的凭据继续恢复。";
        } else if (mode === "unreadable") {
          guidance.textContent = "此账号的暂停记录无法读取。先修复状态记录，再更新状态；不能直接清除暂停。";
        }
      }
      if (!["local-commit","auth-unresolved","failed","unreadable","identity","source-mismatch","upstream-auth","read-error","used"].includes(mode)) {
        steps.hidden = true; help.textContent = "查看排查步骤";
      }
      target.textContent = value ? `检查对象：${value.machine} · ${a.account_label || "账号未识别"}` : "";
      result.textContent = value ? (last
        ? `最近检查：${when(last.observed_at)} · ${last.ok ? "当次成功" : "未成功"}。当前状态以本卡片为准。`
        : `暂停记录：${when(pause?.at ? pause.at * 1000 : null)}。`) : "";
      if (last && pause?.at && Date.parse(last.observed_at) < pause.at * 1000) result.textContent += " 该检查早于本次暂停，不能证明本次故障已恢复。";
      if (value && !pause && !last) result.textContent = `状态更新：${when(value.observed_at)}。`;
      if (value && unrecordedOutcome) result.textContent = "最近检查：未取得该次结果记录。当前是否暂停以上方状态为准。";
      probe.hidden = !["paused", "failed", "source-mismatch", "upstream-auth", "read-error"].includes(mode);
      probe.disabled = busy || probe.hidden;
      probe.textContent = mode === "failed" ? "处理后再次检查配额" : "通过目标机器路由桥检查配额";
      effect.textContent = probe.hidden ? "更新状态只读取已有记录，不查询上游。" : "检查只查询一次配额，沿用该机器原有系统代理；不会发送消息。";
      const renewAuth = canRenewAuth(value) && !probe.hidden;
      relogin.hidden = mode !== "auth-unresolved" || !a.login_recovery
        || a.login_recovery.pause_id !== pause?.id;
      relogin.disabled = busy || relogin.hidden;
      commit.hidden = mode !== "local-commit" || !a.local_commit || a.local_commit.pause_id !== pause?.id;
      commit.disabled = busy || commit.hidden;
      if (!commit.hidden) effect.textContent = "只保存并发布已取得的结果，不登录、不查询上游，也不发送消息。";
      if (!relogin.hidden) effect.textContent = "需要本人在官方页面授权。设备确认等待最多 10 分钟，另有初始化、身份及配额检查的有界阶段；期间占用本机 Codex 通道，不发送消息。";
      if (renewAuth) {
        probe.textContent = "刷新认证并检查配额";
        guidance.textContent = "配额接口拒绝了当前认证。可通过官方接口尝试刷新本账号登录态；只有取得同身份的新凭据才检查配额，未恢复时保留暂停与诊断。";
        effect.textContent = "最多一次认证刷新 RPC 和一次配额 RPC，共用原总时限与目标机器路由；官方内部可能产生多次请求，不发送消息。";
      }
      help.hidden = !(mode === "account-blocked" && layeredPause) && !["local-commit", "auth-unresolved", "failed", "unreadable", "identity", "source-mismatch", "upstream-auth", "read-error", "used"].includes(mode);
      resume.hidden = mode !== "ready" || !onContinue;
      read.disabled = busy;
      read.textContent = ["unknown", "unknown-outcome"].includes(mode) ? "重新读取状态" : mode === "active" ? "更新进度" : "更新状态";
      const primary = !commit.hidden ? commit : !relogin.hidden ? relogin : mode === "account-blocked" && layeredPause ? help : renewAuth || ["paused", "source-mismatch", "upstream-auth", "read-error"].includes(mode) ? probe : ["local-commit","auth-unresolved","failed","unreadable","identity"].includes(mode) ? help : !resume.hidden ? resume : read;
      for (const button of [read,probe,relogin,commit,help,resume]) button.className = "btn" + (button === primary ? " btn-primary" : "");
      steps.replaceChildren();
      const list = make("ol");
      const host = value?.machine || "执行机器";
      const instructions = mode === "account-blocked" && layeredPause ? [
        `先由维护者检查 ${host} 的本次通道失败记录，修复共享网络或服务故障。不要为了继续查询而直接清除保护。`,
        sharedResumeCommand
          ? `确认通道故障已处理后，在 ${host} 本机运行：${sharedResumeCommand}。这是既有的人工确认命令，只解除该通道暂停，不查询上游、不解除账号暂停，也不证明上游已恢复。`
          : "通道暂停标识无法核实，先修复状态记录；不能直接解除保护。",
        "处理后点击“更新状态”，再从账号卡片的“修复登录”继续此账号。其他限制和结果保留，不会自动查询或发送。"
      ] : mode === "local-commit" ? [
        "本次配额复验已有候选结果，但本地核验、保存或正常配额页发布尚未结清，暂停仍保留。",
        !commit.hidden ? "点击“保存已有配额结果”只继续本地保存。若仍失败，保留诊断交由维护者修复，不先重复授权或查询。" : `由维护者核对 ${host} 上本次本地保存/发布错误与原任务的候选观测，修复后利用已有结果处理，不先重复授权或查询。`,
        "保留原任务和恢复链；不要删除暂停记录。技术详情列出已取得的本地错误类别。"
      ] : mode === "auth-unresolved" ? [
        "本次官方认证刷新没有确认写入新凭据，程序未查询配额，原错误和暂停仍保留。",
        "展开技术详情查看认证修复结果及已保存的客户端诊断；没有明确上游原因时，不据此认定必须重新登录。",
        !relogin.hidden ? "若决定重新授权，点击“重新授权并复验配额”。账号卡片将显示本次官方设备码；在官方页面确认本账号身份，程序只在同身份新凭据与一次配额验证成功后结清原任务及暂停。" : `由维护者核对 ${host} 上本账号的后续认证恢复路径。普通登录可能仍受当前暂停约束；不要清除暂停、切换出口或重复检查。`
      ] : mode === "unreadable" ? [
        `在 ${host} 的终端运行 agent-monitor status，核对服务状态及显示的日志位置。`,
        "查看日志中的状态文件读取错误，修复对应文件的权限或格式；不要删除暂停记录来恢复发送。",
        "修复后点击“更新状态”。需要协助时，展开技术详情并复制诊断。"
      ] : mode === "identity" ? [
        "展开技术详情，确认页面读取到的账号来源、工作区和机器是否与目标账号一致。",
        `页面不能替你切换 ${host} 的登录账号；请在该机器按既有登录流程确认目标账号，不要在浏览器所在设备换出口绕过。`,
        "账号确认后点击“更新状态”。如果仍显示身份变化，这一步需要 Agent Monitor 管理员核对账号目录和同步状态。"
      ] : mode === "source-mismatch" ? [
        "保留两份记录，不要删除暂停或自行切换出口。",
        `下一次检查将在 ${host} 执行，并使用该机器的 Agent Monitor 路由桥；预览失败时不会发送配额检查请求。`,
        `若一次检查仍失败，页面不能替 ${host} 修复账号或部署；复制技术详情，交由 Agent Monitor 管理员核对汇总与机器记录。`
      ] : mode === "upstream-auth" ? [
        `核对上方检查对象。下一次检查将在 ${host} 执行，并先展示该机器的 Agent Monitor 路由桥出口。`,
        `已记录${authEvidence}；这条记录不能单独证明是凭据、权限还是服务端原因。不要直接换出口、重复登录或连续重试。`,
        `若一次检查仍失败，页面不能替 ${host} 修复账号或部署；复制技术详情，交由 Agent Monitor 管理员继续核对。`
      ] : mode === "read-error" ? [
        `核对上方检查对象。检查将在 ${host} 执行，并使用该机器的 Agent Monitor 路由桥，不使用打开网页设备的直连。`,
        "点击“通过目标机器路由桥检查配额”前先确认出口预览完整；预览失败时不会发送配额检查请求。",
        "若仍失败，保留暂停状态并复制技术详情交由管理员排查，不要重复盲试。"
      ] : mode === "failed" ? [
        "本次检查已经失败。保留暂停和诊断，先展开技术详情并复制诊断信息，不要直接重复检查。",
        `由 Agent Monitor 维护者核对 ${host} 上本次失败的阶段、错误码、账号与路由记录，按已核实原因处理；错误码本身不能证明需要换出口或重新登录。`,
        "完成处理后先更新状态；仍需验证配额时，再点击“处理后再次检查配额”并确认原路由出口。已有发送结果不会重放。"
      ] : [
        `核对上方检查对象。检查发生在 ${host}，不是自动使用打开网页这台设备的网络。`,
        `检查会通过 ${host} 的 Agent Monitor 路由桥执行；保持该机器原有出口，不切换节点。错误码本身不能确认是代理或登录故障。`,
        mode === "used" ? "点击“更新状态”查看是否已有检查结果。若检查机会一直占用，复制诊断排查服务日志；不要删除暂停或重复提交。" : "确认出口后点击“通过目标机器路由桥检查配额”，只检查一次。若仍失败，保留结果并复制诊断继续定位，不反复盲试。"
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
        const diagnostic = recoveryDiagnostics(pause?.failure?.stage === "recovery_commit"
          ? {ok:false,failure:pause.failure} : last || {ok:false,failure:pause.failure});
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
        const query = new URLSearchParams({machine});
        if (pauseScope === "account") { query.set("profile", profile); query.set("pause_scope", "account"); }
        const {response, value} = await requestJSON(`/api/quota-recovery?${query}`);
        if (!response.ok || !value.machine || !value.observed_at || !Object.hasOwn(value, "pause") || !Object.hasOwn(value, "active")) throw new Error(value.error || "状态回执不完整");
        if (pauseScope === "account" && (value.pause_scope !== "account" || value.account?.quota_profile !== profile)) throw new Error("账号恢复状态与所选对象不一致");
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
    panel.updateSourceFailure = failure => { sourceFailure = failure; if (state) render(state); };
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
    async function requestRecovery(reauthorize = false) {
      if (busy || (reauthorize ? relogin.disabled : probe.disabled) || !state) return;
      const request = {machine:state.machine,account_id:state.account.account_id,account_label:state.account.account_label,pause_id:state.pause.id,profile:state.account.quota_profile || ""};
      if (state.pause_scope === "account") request.pause_scope = "account";
      if (canRenewAuth(state)) request.mode = "renew_auth";
      if (reauthorize) {
        request.mode = "reauthorize";
        request.operation_id = state.account.login_recovery.operation_id;
      }
      unrecordedOutcome = false;
      busy = true; read.disabled = probe.disabled = relogin.disabled = true;
      panel.setAttribute("aria-busy", "true");
      title.textContent = "正在检查配额…";
      guidance.textContent = "请等待本次检查结束。不会发送消息，请勿重复提交。";
      notice.textContent = "";
      let receipt, code, attempted = false;
      try {
        const approval = await window.AgentMonitor.previewUpstream?.(
          [{email: request.account_label, account_id: request.account_id}],
          {approval_action: request.mode === "reauthorize" ? "quota-login-recovery" : request.mode ? "quota-auth-recovery" : "quota-recovery", approval_scope: [request.machine, request.account_id, request.account_label, request.profile || "native", ...(request.mode ? [request.mode] : []), ...(request.operation_id ? [request.operation_id] : []), ...(request.pause_scope ? [request.pause_scope] : [])], approval_expected: request.pause_id, approval_machine: request.machine},
          controller.signal);
        if (!approval) throw new Error("已取消检查；未发出上游请求。");
        attempted = true;
        const {response, value} = await requestJSON("/api/quota-recovery", {method:"POST",headers:{"Content-Type":"application/json","X-Agent-Monitor":"action"},body:JSON.stringify({...request, confirmation: approval})},90000);
        code = response.status; receipt = value;
      } catch (error) { notice.textContent = error?.message || "出口确认未完成；未发出检查请求。"; }
      pendingPauseId = attempted && (!receipt || (typeof receipt.ok !== "boolean" && !receipt.failure)) ? request.pause_id : null;
      busy = false;
      await refresh();
      if (!receipt?.login_pending && (receipt?.ok || receipt?.failure)) {
        const diagnostic = recoveryDiagnostics({...receipt,request}, code);
        diagnostic.open = true; details.replaceChildren(diagnostic);
      }
      if (code && !receipt?.ok && !receipt?.failure) notice.textContent = `检查请求未完成（Monitor HTTP ${code}）。请先更新状态；详情见诊断。`;
    }
    probe.addEventListener("click", () => requestRecovery());
    relogin.addEventListener("click", () => requestRecovery(true));
    commit.addEventListener("click", async () => {
      const binding = state?.account?.local_commit;
      const profile = state?.account?.quota_profile;
      if (busy || !binding || !profile?.startsWith("monitor-")) return;
      busy = true; render(state);
      notice.textContent = "正在保存已有结果，不查询上游…";
      let success = false, message = "";
      try {
        const {response, value} = await requestJSON("/api/codex-accounts/commit-recovery", {
          method:"POST", headers:{"Content-Type":"application/json", "X-Agent-Monitor":"codex-accounts"},
          body:JSON.stringify({id:profile.slice("monitor-".length), machine:state.machine,
            operation_id:binding.operation_id, pause_id:binding.pause_id,
            ...(state.pause_scope === "account" ? {pause_scope:"account"} : {})}),
        });
        success = response.ok;
        message = success ? "已有配额结果已保存，原查询任务已结清；未发出上游请求。" : value.error || "本地保存未完成；先更新状态，不要重新授权。";
      } catch {
        message = "本地保存回执未取得；先更新状态核对结果，不要重复授权或查询。";
      }
      busy = false;
      await refresh();
      if (success) await onSaved?.();
      notice.textContent = message;
    });
    refresh();
    return panel;
  }

  function failureAccountKey(provider, failure) {
    if (typeof failure.account_id !== "string" || !failure.account_id.trim()) return null;
    if (provider.key !== "codex") return failure.account_id;
    if (typeof failure.account_label !== "string" || !failure.account_label.trim()) return null;
    return JSON.stringify([failure.account_id, failure.account_label.trim().toLowerCase()]);
  }

  function quotaFailureGroups(provider, failures) {
    const groups = new Map();
    failures.forEach((failure, index) => {
      const kind = failure.pause?.id ? "paused" : quotaFailureKind(failure.reason, failure.query_state);
      const key = JSON.stringify([failure.machine, kind, kind === "paused"
        ? failure.pause?.id || null
        : failureAccountKey(provider, failure) || failure.account_profile || index]);
      if (!groups.has(key)) groups.set(key, {key, machine:failure.machine, kind, failures:[]});
      groups.get(key).failures.push(failure);
    });
    return [...groups.values()];
  }

  function renderQuotaDiagnostics(rateLimits) {
    const table = qs('#quota-diagnostic-table');
    if (!table) return;
    if (!table.children.length) table.textContent = '';
    QUOTA_PROVIDERS.forEach(provider => {
      const old = Array.from(table.children).find(body => body.quotaDiagnostics?.provider === provider.key);
      const failures = rateLimits?.[provider.key]?.refresh_errors || [];
      if (failures.length || old) table.appendChild(quotaFailureDetails(provider, failures, old));
    });
    if (!table.children.length) table.textContent = '没有已记录的配额更新异常。';
  }

  function quotaFailureDetails(provider, failures, previous) {
    const body = previous || document.createElement("tbody");
    if (!body.quotaDiagnostics) {
      const row = document.createElement("tr"), cell = document.createElement("td");
      cell.colSpan = 9;
      const details = document.createElement("details");
      details.className = "note-disclosure quota-failure-details";
      const summary = document.createElement("summary");
      details.appendChild(summary); cell.appendChild(details); row.appendChild(cell); body.appendChild(row);
      body.quotaDiagnostics = {provider:provider.key, details, summary, entries:[]};
    }
    const saved = body.quotaDiagnostics;
    const groups = quotaFailureGroups(provider, failures);
    const counts = {paused:new Set(), skipped:new Set(), busy:new Set(), error:new Set()};
    groups.forEach(group => { if (group.machine) counts[group.kind].add(group.machine); });
    const labels = [["paused","暂停"],["skipped",failures.some(f => f.query_state === "skipped") ? "本轮跳过或停止" : "本轮未尝试"],["busy","通道占用未查询"],["error","查询异常"]];
    const status = labels.filter(([key]) => counts[key].size).map(([key,label]) => `${counts[key].size} 台${label}`).join("，");
    const identities = new Set(failures.map(f => failureAccountKey(provider, f)).filter(Boolean));
    const unknown = failures.some(failure => !failureAccountKey(provider, failure));
    const accountsText = identities.size ? `；涉及 ${identities.size} 个账号${unknown ? "，另有账号身份未确认" : ""}` : "；账号身份未确认";
    saved.summary.textContent = failures.length ? `${provider.label} 采集详情（配额查询：${status || "机器未确认"}${accountsText}）` : `${provider.label} 最近恢复记录`;
    const remaining = [...saved.entries], next = [];
    const currentKeys = new Set(groups.map(group => group.key));
    for (const group of groups) {
      // Keep recorded failure context without counting affected observations
      // as attempts or inferring which account triggered the shared pause.
      const failure = group.failures.find(f => f.failure && Object.keys(f.failure).length) || group.failures[0];
      let index = remaining.findIndex(record => record.key === group.key);
      if (index < 0 && group.kind === "paused") index = remaining.findIndex(record =>
        record.machine === group.machine && record.kind === "paused" && !currentKeys.has(record.key));
      let record = index < 0 ? null : remaining.splice(index,1)[0];
      if (!record) {
        const entry = document.createElement("div"), note = document.createElement("p");
        entry.appendChild(note);
        record = {machine:failure.machine, entry, note, panel:null, pauseId:failure.pause?.id};
        record.accounts = document.createElement("ul");
        entry.appendChild(record.accounts);
        if (failure.machine && (provider.key === "codex" || failure.query_state === "skipped")) {
          const open = document.createElement("button");
          open.type = "button"; open.textContent = "查看当前状态与恢复";
          open.addEventListener("click", () => {
            if (record.panel) return;
            open.disabled = true;
            record.panel = createRecoveryPanel(record.machine, {sourceFailure: record.failure});
            entry.appendChild(record.panel);
          });
          entry.appendChild(open); record.open = open;
          const sourceDetails = document.createElement("details"), sourceSummary = document.createElement("summary");
          sourceDetails.className = "recovery-source";
          sourceSummary.textContent = "采集记录与错误详情";
          record.sourceText = document.createElement("p");
          sourceDetails.append(sourceSummary, record.sourceText);
          entry.appendChild(sourceDetails);
        }
      }
      record.key = group.key; record.kind = group.kind; record.failure = failure;
      if (record.open) {
        if (failure.query_state === "skipped") record.open.remove();
        else if (!record.open.parentNode) record.entry.insertBefore(record.open, record.sourceText.parentElement);
      }
      record.accounts.textContent = "";
      const accountGroups = new Map();
      group.failures.forEach((item, index) => {
        const key = failureAccountKey(provider, item) || JSON.stringify([item.account_profile, index]);
        if (!accountGroups.has(key)) accountGroups.set(key, []);
        accountGroups.get(key).push(item);
      });
      for (const items of accountGroups.values()) {
        const item = items[0], row = document.createElement("li");
        const sources = [...new Set(items.map(f => f.account_profile ? `托管账号 ${f.account_profile}`
          : f.account_profile === "" ? "默认来源" : "来源未记录"))];
        const label = typeof item.account_label === "string" && item.account_label.trim()
          ? item.account_label : failureAccountKey(provider, item) ? item.account_id : "账号身份未确认";
        const states = [...new Set(items.map(f => ({paused:"受暂停影响", skipped:f.query_state === "skipped" ? "已跳过或停止" : "未执行", busy:"通道占用", error:"查询未完成"})[quotaFailureKind(f.reason, f.query_state)]))];
        row.textContent = `${label}${provider.key === "codex" && typeof item.account_id === "string" ? " · 工作区 " + item.account_id : ""} · ${sources.join("、")} · ${states.join("、")}`;
        record.accounts.appendChild(row);
      }
      record.panel?.updateSourceFailure(failure);
      if (record.sourceText) {
        const statusText = {paused:"已暂停", skipped:failure.query_state === "skipped" ? "已跳过或停止" : "未执行", busy:"通道占用", error:"未完成"}[group.kind];
        record.sourceText.textContent = group.failures.length === 1 ? failure.reason || "未记录错误详情"
          : group.failures.map(f => `${f.account_label || "账号身份未确认"}${f.account_profile ? " · " + f.account_profile : ""}：${f.reason || "未记录错误详情"}`).join("\n\n");
        if (failure.refresh_skip?.checked_at) {
          record.sourceText.textContent += `\n出口检查时间：${failure.refresh_skip.checked_at}（不是额度观测时间）。`;
        }
        const guidance = quotaFailureGuidance(failure);
        record.note.dataset.failureCategory = guidance.key;
        record.note.textContent = failure.machine + "：上次配额采集" + statusText + "；"
          + (group.kind === "paused" ? "下列账号受该机器暂停影响，不代表每个账号均查询失败。暂停关联的采集记录：" : "") + guidance.summary + "。" + guidance.action;
      } else record.note.textContent = `${failure.machine}：${[...new Set(group.failures.map(f => f.reason))].join("；")}`;
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

  function quotaRefreshNotice(provider, account, concise = false) {
    if (account.quota_status_reason) return account.quota_status_reason;
    const block = renderedQuotaRateLimits?.[provider.key];
    const failures = (block?.refresh_errors || []).filter((failure) => quotaFailureMatchesAccount(
      failure, account, block?.accounts || []));
    if (quotaPresence(account) !== "in_use" || !failures.length) {
      return null;
    }
    const failed = new Set(failures.map((failure) => failure.machine));
    const hasValue = provider.windows.some((spec) => quotaUsage(account[spec.used]).known);
    const sourceHealthy = account.reading_from && !failed.has(account.reading_from)
      && !(block?.signed_out_machines || []).includes(account.reading_from);
    const fresh = Number.isFinite(Date.parse(account.updated_at)) && !quotaIsStale(account.updated_at);
    if (sourceHealthy && fresh && hasValue) return null;
    const kinds = new Set(failures.map(({ reason, query_state }) => quotaFailureKind(reason, query_state)));
    const state = kinds.has("paused") ? "查询已暂停" : kinds.has("skipped") ? (failures.some(f => f.query_state === "skipped") ? "查询已跳过或停止" : "上次查询未执行")
      : kinds.has("busy") ? "查询通道占用" : null;
    if (state) {
      if (concise) return `主动${state}`;
      const reading = hasValue ? (fresh ? (account.quota_observation?.source === "codex_session" ? "当前读数来自会话日志" : "当前读数仍在更新时限内") : "读数已过期，保留上次读数") : "暂无读数";
      const machines = [...new Set(failures.map((failure) => failure.machine).filter(Boolean))].join("、");
      return `${reading}；${machines} 的主动${state}，请在“采集与诊断”查看原因`;
    }
    if (concise) return hasValue ? "更新延迟" : "查询失败，暂无读数";
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
    summaryCell.colSpan = 9;
    const toggle = document.createElement("button");
    toggle.type = "button";
    toggle.className = "quota-toggle quota-past-toggle";
    toggle.setAttribute("aria-expanded", String(expanded));
    toggle.setAttribute("aria-controls", "quota-past-accounts");
    toggle.textContent = `${entries.some(({provider, account}) => quotaAccountDisabled(provider, account)) ? "已停用与历史账号" : "历史账号"}（${entries.length}）`;
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
    row.dataset.disabled = String(quotaAccountDisabled(provider, account));
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
    row.appendChild(quotaPlanCell(display, provider));
    QUOTA_COLUMNS.forEach((spec) => {
      row.appendChild(quotaWindowCell(provider, spec, display));
      row.appendChild(quotaResetCell(provider, spec, display));
    });
    row.appendChild(quotaMachinesCell(account, display));
    row.appendChild(quotaUpdatedCell(provider, display, stale, display.quota_source === "account_action"
      ? null : quotaRefreshNotice(provider, account)));
    Array.from(row.children).forEach((cell, index) => {
      cell.dataset.label = ["账号", "服务商", "套餐记录", "5 小时已用", "5 小时重置时间", "7 天已用", "7 天重置时间", "机器", "配额更新"][index];
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

  function quotaPlanCell(account, provider = {}) {
    const cell = document.createElement("td");
    if (account.quota_status === "no_subscription") {
      cell.className = "quota-plan-status";
      cell.textContent = "无有效订阅";
      return cell;
    }
    if (account.plan_verification === "unverified") {
      cell.className = "quota-plan-status";
      cell.textContent = "套餐未核实";
      return cell;
    }
    if (!account.account_plan) {
      cell.textContent = "—";
      return cell;
    }
    const plan = document.createElement("span");
    plan.className = "status-pill identity";
    plan.textContent = quotaPlanLabel(account.account_plan, provider);
    cell.appendChild(plan);
    if (account.account_state === "known") {
      const source = document.createElement("span");
      source.className = "quota-history-note";
      source.textContent = (quotaPresence(account) === "remembered" && account.quota_source !== "account_action")
        || (account.quota_status && account.quota_status !== "current") ? "历史记录"
        : quotaPlanSourcesDisagree(account) ? "记录不同"
        : typeof account.reading_plan === "string" && account.reading_plan ? ""
        : typeof account.credential_plan === "string" && account.credential_plan ? ""
        : "来源未记录";
      if (source.textContent) cell.appendChild(source);
    }
    return cell;
  }

  function appendQuotaPlanSources(details, account, provider) {
    if (account.account_state !== "known"
        || (quotaPresence(account) === "remembered" && account.quota_source !== "account_action")
        || !account.account_plan || account.quota_status === "no_subscription"
        || account.plan_verification === "unverified"
        || (provider.key !== "codex" && !quotaPlanSourcesDisagree(account))) return;
    const note = (text) => {
      const item = document.createElement("span");
      item.className = "quota-history-note";
      item.textContent = text;
      details.appendChild(item);
    };
    const rawPlan = (value) => typeof value === "string" && value ? value : null;
    const reading = rawPlan(account.reading_plan), credential = rawPlan(account.credential_plan);
    const describe = (value) => `${quotaPlanLabel(value, provider)}（原始值：${value}）`;
    // Observation time and source are already shown by this disclosure.
    note(reading ? `配额记录中的套餐：${describe(reading)}。` : "配额记录中的套餐未取得。");
    note(credential ? `本地登录信息：${describe(credential)}；更新时间未知。` : "本地登录信息中的套餐未取得。");
    if (!reading && !credential) note("保存的套餐来源未记录，不代表当前订阅。");
    if (quotaPlanSourcesDisagree(account)) {
      note("两份套餐记录不同，原因尚未核实；不能据此判断升级失败或需要重新登录。");
    }
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
  // This is a raw-record difference, not a subscription or login failure.
  // Missing sources are stated separately in the accessible source details.
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
    if (quotaAccountDisabled(provider, account)) {
      const status = document.createElement("span");
      status.className = "status-pill bad";
      status.textContent = "已停用";
      wrap.appendChild(status);
    }
    if (account.account_state !== "known" && account.account_state !== "signed_out") {
      const note = document.createElement("span");
      note.className = "quota-history-note";
      note.textContent = "请更新对应机器上的 agent-monitor";
      wrap.appendChild(note);
    }
    const actions = document.createElement("div");
    actions.className = "quota-account-links";
    const profiles = provider.key === "codex" && account.account_state === "known"
      ? codexQuotaReadings.filter(profile => profile.account_id === account.account_id
        && profile.email.toLowerCase() === account.account_label?.toLowerCase()) : [];
    if (profiles.length === 1 && typeof profiles[0].id === "string" && profiles[0].id) {
      const open = document.createElement("button");
      open.type = "button";
      open.className = "btn";
      open.textContent = "管理账号";
      open.setAttribute("aria-label", `${quotaAccountName(account)}（${account.account_id}）的账号操作`);
      open.addEventListener("click", () => window.AgentMonitor?.openCodexAccount?.(profiles[0].id));
      actions.appendChild(open);
      const refresh = document.createElement('button');
      refresh.type = 'button'; refresh.className = 'btn'; refresh.textContent = '刷新配额';
      refresh.setAttribute('aria-label', `${quotaAccountName(account)}（${account.account_id}）的配额刷新`);
      refresh.addEventListener('click', () => window.AgentMonitor?.openCodexAccount?.(profiles[0].id, 'refresh'));
      if (!quotaAccountDisabled(provider, account)) actions.appendChild(refresh);
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
    const rows = Array.from(historyBody.children);
    const count = rows.length;
    if (!count) {
      historyBody.remove();
      summaryBody.remove();
      return;
    }
    const row = summaryBody.children[0];
    const cell = row && row.children[0];
    const toggle = cell && cell.children[0];
    if (toggle) {
      toggle.textContent = `${rows.some(row => row.dataset.disabled === "true") ? "已停用与历史账号" : "历史账号"}（${count}）`;
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
    if (!known) {
      const missing = document.createElement("span");
      missing.className = "quota-window-reason";
      missing.textContent = "未知";
      missing.setAttribute("aria-label", "用量未知");
      missing.title = "用量未知";
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

    return cell;
  }

  function quotaResetCell(provider, spec, account) {
    const cell = document.createElement("td");
    cell.className = "quota-reset-cell";
    if (account.quota_status === "no_subscription" || !provider.windows.some(window => window.key === spec.key)) {
      cell.textContent = "不适用";
      return cell;
    }
    const renderedAtMs = Date.now();
    const resetEpoch = account[spec.reset];
    const resetState = quotaResetState(resetEpoch, renderedAtMs);
    const reset = document.createElement("div");
    reset.className = "quota-window-reset";
    reset.textContent = quotaPresence(account) === "remembered" && resetState === "unknown"
      ? "重置时间未知"
      : quotaResetText(resetEpoch, resetState, renderedAtMs);
    if (resetState !== "unknown") {
      reset.title = resetText(resetEpoch, renderedAtMs);
    }
    cell.appendChild(reset);
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
      }
      const tags = [name === account.this_machine ? "本机" : "", names.length > 1 && name === source ? "读数" : ""].filter(Boolean);
      if (tags.length) node.appendChild(document.createTextNode(`（${tags.join("·")}）`));
      cell.appendChild(node);
    });
    if (source && !names.includes(source)) {
      cell.appendChild(document.createTextNode(` · 读数：${source}`));
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
    summary.setAttribute("aria-label", `配额更新：${summary.textContent}，查看观测与套餐详情`);
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
    appendQuotaPlanSources(details, account, provider);
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
        + (history.account_plan ? `保存的套餐信息：${quotaPlanLabel(history.account_plan, provider)}（不代表当前订阅）。` : "");
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
    if (stale && (quotaPresence(account) !== "remembered" || account.quota_source === "account_action")) {
      note("读数过期");
      const age = document.createElement("span");
      age.className = "quota-history-note";
      age.textContent = "读数已超过更新时限，可能早于一次登录变更；不代表当前剩余额度。";
      details.appendChild(age);
    }
    const sourceUnavailable = (renderedQuotaRateLimits?.[provider.key]?.signed_out_machines || [])
      .includes(account.reading_from);
    if (account.quota_source === "account_action") {
      const context = document.createElement("span");
      context.className = "quota-history-note";
      context.textContent = account.reading_plan
        ? "配额与读数套餐来自此次账号操作查询；机器归属与凭据套餐来自机器采集。套餐信息不代表当前订阅权益。"
        : "配额来自此次账号操作查询；机器归属与可用的凭据套餐来自机器采集。套餐信息不代表当前订阅权益。";
      details.appendChild(context);
      note("账号操作读数");
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
      if (sourceUnavailable) {
        note("来源机器未登录");
        const recovery = document.createElement("span");
        recovery.className = "quota-history-note";
        recovery.textContent = `当前显示来自 ${account.reading_from} 的最后一次配额读数。该机器未登录 ${provider.label}；如需继续从此机器更新，请在该机器登录此账号，再点击“更新统计与配额”。`;
        details.appendChild(recovery);
      }
      if (refreshNotice) {
        note(quotaRefreshNotice(provider, account, true));
        const cause = document.createElement("span");
        cause.className = "quota-history-note";
        cause.textContent = refreshNotice;
        details.appendChild(cause);
      }
    }

    return cell;
  }

  // Plan tiers arrive as the provider's own identifiers. Known ones use display
  // names; anything unrecognised is shown as it came,
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

  // Codex auth.rs e7f32cf1928a35a9232a633a51c2a03b5a7e2c72;
  // these are distinct plan identifiers, not price or entitlement guarantees.
  const CODEX_PLAN_LABELS = {pro: "Pro (More)", prolite: "Pro", promax: "Pro (Max)"};

  function quotaPlanLabel(plan, provider = {}) {
    const labels = provider.key === "codex" && Object.hasOwn(CODEX_PLAN_LABELS, plan)
      ? CODEX_PLAN_LABELS : QUOTA_PLAN_LABELS;
    return Object.hasOwn(labels, plan) ? labels[plan] : plan;
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
    renderQuotaSettings(rateLimits);
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
      if (account.account_plan) values.push(`采集时套餐：${quotaPlanLabel(account.account_plan, provider)}`);
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
    if (hadFocus) summary.focus({ preventScroll: true });
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
    detail.colSpan = 6;
    detail.className = "quota-updated-cell";
    detail.textContent = reason || "无已纳入的数据源";
    row.appendChild(detail);

    body.appendChild(row);
    return body;
  }

  function resetQuotaReadings(options = {}) {
    codexQuotaReadings = [];
    renderedQuotaRateLimits = null;
    initialQuotaHistoryExpanded = Boolean(options.rememberedExpanded);
  }
  function quotaSnapshot() { return renderedQuotaRateLimits; }
  return { renderQuotaDiagnostics, createQuotaSettingsPanel, renderQuotaSettings, createRecoveryPanel, recoveryDiagnostics, codexPauseTarget, showCodexPauseRecovery, quotaSnapshot, QUOTA_PROVIDERS, QUOTA_WINDOW_5H, resetText, latestCodexQuotaReading, updateCodexQuotaReadings, quotaDisplayReading, sortQuotaAccounts, quotaMatches, bindQuotaExplorer, quotaPresence, codexSessionAccountLabel, renderQuotaAccounts, quotaFailureKind, quotaFailureDetails, quotaRefreshNotice, renderQuotaSignedOut, appendRememberedAccounts, quotaBody, quotaAccountRow, quotaProviderCell, quotaPlanCell, quotaPlanSourcesDisagree, quotaAccountCell, quotaRemovalReading, removeRenderedAccount, refreshRememberedAccountSummary, removeRememberedAccount, quotaUsage, quotaBandPill, quotaWindowCell, quotaResetCell, quotaMeter, quotaResetText, quotaResetState, formatQuotaReset, quotaMachinesCell, quotaObservationAge, quotaUpdatedCell, quotaPlanLabel, quotaAccountName, quotaIsStale, quotaProviderPill, renderQuotaMachineRecords, renderQuotaMachineNotice, quotaWorstUsage, quotaUnavailableBody, quotaFilter, resetQuotaReadings };
});
