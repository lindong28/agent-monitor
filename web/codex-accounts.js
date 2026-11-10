async function previewEgress(...args) {
  const version = document.querySelector('meta[name="asset-version"]')?.content;
  const preview = await import("./account-egress.js" + (version ? `?v=${version}` : ""));
  return preview.previewEgress(...args);
}

const STAGES = {
  queued: "排队中",
  checking: "检查登录", login_queued: "等待授权排队", login: "等待官方授权", sending: "正在发送", reading: "读取配额", saving: "保存配额结果",
  succeeded: "已完成", partial: "配额待确认", failed: "未完成",
  cancelled: "已取消", interrupted: "上次操作已中断",
};
const MESSAGE_STATUS = { not_sent: "未发送", unknown: "结果未知，可能已消耗额度", succeeded: "发送成功", failed: "发送失败，可能已消耗额度" };

export function sameSuccessfulBatchOperation(op, batchOp) {
  return Boolean(op?.id && op.batch_id && op.id === batchOp?.id
    && op.batch_id === batchOp.batch_id && !op.refresh_only
    && op.stage === "succeeded" && batchOp.stage === "succeeded"
    && op.message_status === "succeeded" && batchOp.message_status === "succeeded");
}

export function accountNeedsLogin(account) {
  return Boolean(account?.login_required || account?.has_credentials === false
    || account?.operation?.failure?.category === "needsLogin");
}

function accountRefreshBlocked(account) {
  return account.disabled || Boolean(account.account_quota_pause) || account.quota_query_allowed === false || accountNeedsLogin(account) || ["authentication", "unknown", "rate_limit", "login_rate_limit"]
    .some(key => account.restrictions?.[key]);
}

function quotaHoldMessage(account, pause) {
  const reading = account.last_quota;
  const since = account.restrictions?.quota?.created_at;
  const reason = reading?.rate_limit_reached_type;
  const explanation = reason === "workspace_member_credits_depleted" ? "工作区成员额度已耗尽"
    : reading?.spend_control_reached === true ? "已达到支出限制"
    : reason ? `额度限制（${reason}）` : "服务端限制了额度使用";
  const evidence = reading && (reading.ordinary_usage_allowed === false || reading.spend_control_reached === true || reason)
    ? `最近成功观测（${timestamp(reading.observed_at)}）：${explanation}。` : "";
  const next = pause ? "请先查看本页的通道暂停与检查。"
    : accountRefreshBlocked(account) ? "请先处理此账号的其他限制。" : "可仅刷新配额。";
  return `发送仍因此前额度限制暂停${since ? `（记录于 ${timestamp(since)}）` : ""}。${evidence}${next}只有服务端明确允许使用后才解除额度暂停，0% 或重置时间经过不代表恢复。`;
}

export function quotaFailureExplanation(account, sharedPause, accounts = []) {
  const op = account.operation;
  if (!op?.refresh_only || op.stage !== "failed") return "";
  if (op.failure?.category === "locallyPaused") {
    const source = accounts.find(a => a.id === sharedPause?.source?.profile_id);
    const history = "本账号上次未发出请求：当时被本地保护暂停。";
    if (sharedPause) return history + (source ? `当前 ${source.email} 的查询触发的共享暂停仍在生效。` : "当前共享暂停仍在生效。") + "请先处理共享暂停。";
    return history + "当前无共享暂停；" + (account.account_quota_pause ? "本账号仍有配额暂停，请打开登录修复。"
      : accountRefreshBlocked(account) ? "请先处理此账号的其他限制。" : "可继续查询此未完成项。");
  }
  const failure = op.diagnostics?.rpc_failure;
  if (failure?.http_status === 401 && ["token_expired", "token_revoked"].includes(failure.upstream_code)) {
    const next = sharedPause ? "共享暂停仍在生效，请先处理共享暂停，再分别修复认证和继续未完成项。"
      : account.account_quota_pause ? "此账号的配额暂停仍保留，其他未受限账号可继续查询。"
      : "当前无共享暂停；请按此账号的当前限制和登录修复提示继续处理。";
    return `配额接口返回 401/${failure.upstream_code}，本次未取得配额；认证原因尚未核实。${next}`;
  }
  return "";
}

export function accountQuotaPauseGuidance(account) {
  return account.disabled
    ? "恢复此账号前，请先重新启用；启用后仍需处理配额暂停。"
    : "本机账号已启用，配额暂停仍保留。打开登录修复后，按面板当前提示完成恢复；仅打开面板不会发起上游请求。";
}

export function accountToggleNotice(account) {
  if (account.disabled) return "已停用此账号；未发出上游请求，历史记录和其他限制保留。";
  return account.account_quota_pause
    ? "已重新启用此账号；配额暂停仍保留，请打开登录修复继续处理。"
    : "已重新启用此账号；未自动查询、登录或发送，其他限制保留。";
}

export function quotaBatchSummary(batch) {
  if (!batch) return "";
  const counts = { succeeded: 0, failed: 0, unattempted: 0, skipped: 0, active: 0 };
  for (const item of batch.items) {
    const op = item.operation;
    if (op?.stage === "succeeded") counts.succeeded++;
    else if (item.disabled || ["accountDisabled", "needsLogin"].includes(op?.failure?.category)) counts.skipped++;
    else if (!op || op.failure?.category === "locallyPaused") counts.unattempted++;
    else if (["failed", "interrupted", "cancelled"].includes(op.stage)) counts.failed++;
    else counts.active++;
  }
  return `配额查询 ${counts.succeeded}/${batch.items.length} 已完成 · 失败 ${counts.failed} · 未查询 ${counts.unattempted} · 已跳过 ${counts.skipped}`
    + (counts.active ? ` · 处理中 ${counts.active}` : "");
}

function element(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
}

function appendRpcFailure(section, diag, label = "请求错误") {
  const rpc = diag?.rpc_failure;
  if (rpc || diag?.rpc_error_code != null) section.append(element("p", `${label}：RPC ${rpc?.rpc_code ?? diag?.rpc_error_code ?? "未返回"}${rpc?.http_status ? " · 上游 HTTP " + rpc.http_status : ""}${rpc?.upstream_code ? " · 上游代码 " + rpc.upstream_code : ""}`, "quota-scope"));
  if (rpc?.detail) section.append(element("p", `${label}摘要（已脱敏）：${rpc.detail}`, "quota-scope"));
}

export function timestamp(value, seconds = false) {
  if (value === null || value === undefined) return "服务端未返回";
  const date = new Date(seconds ? value * 1000 : value);
  return Number.isFinite(date.getTime()) ? window.AgentMonitor.formatDate(date.toISOString()) : "时间不可用";
}

export function init({ autoLoad = true } = {}) {
  const root = document.querySelector("#codex-account-actions");
  if (!root || root.dataset.initialized) return;
  root.dataset.initialized = "true";
  const current = window.AgentMonitor.pageScope();
  const list = root.querySelector("#codex-account-list");
  const notice = root.querySelector("#codex-account-notice");
  const batchStart = root.querySelector("#codex-batch-start");
  const batchNext = root.querySelector("#codex-batch-next");
  const batchRetry = root.querySelector("#codex-batch-retry");
  const batchStatus = root.querySelector("#codex-batch-status");
  const batchHelp = root.querySelector("#codex-batch-help");
  const refreshSelected = root.querySelector("#codex-refresh-selected");
  const quotaRetry = root.querySelector("#codex-quota-retry");
  const quotaStatus = root.querySelector("#codex-quota-batch-status");
  const quotaProgress = root.querySelector("#codex-quota-progress");
  const operationHistory = root.querySelector("#codex-operation-history");
  const egressPreview = root.querySelector("#codex-egress-preview");
  const blockedReason = root.querySelector("#codex-batch-blocked");
  const pauseControls = {
    pausePanel: root.querySelector("#codex-upstream-pause"),
    pauseSummary: root.querySelector("#codex-pause-summary"),
    pauseDetail: root.querySelector("#codex-pause-detail"),
    pauseRecovery: root.querySelector("#codex-pause-recovery"),
  };
  let timer;
  let loading = false;
  let reloadPending = false;
  let accounts = [];
  let batch = null;
  let quotaBatch = null;
  let upstreamPause = null;
  let unavailable = [];
  let selectedIds = new Set();
  let batchMode = false;
  const searchInput = root.querySelector("#codex-account-search");
  const stateFilter = root.querySelector("#codex-account-filter");
  const batchToggle = root.querySelector("#codex-batch-mode");
  let lastBatchStatus = "";
  let hasSnapshot = false;
  let locatePending = new URLSearchParams(window.location?.search || "").get("profile");
  let refreshingAll = false;
  const controller = new AbortController();
  let deviceConnection = null;
  const deviceStatus = root.querySelector("#codex-device-status");
  const deviceConnect = root.querySelector("#codex-device-connect");
  const deviceCancel = root.querySelector("#codex-device-cancel");
  const deviceHelp = root.querySelector("#codex-device-help");
  const deviceCommand = root.querySelector("#codex-device-command");
  if (deviceCommand) deviceCommand.textContent = `agent-monitor connect install --server '${window.AgentMonitor.hubUrl?.() || location.origin}'`;
  let pairing = null;
  function deviceFeedback(message, failed = false) {
    if (!current()) return;
    if (deviceStatus) {
      deviceStatus.textContent = message;
      deviceStatus.hidden = !message;
    }
    if (failed && deviceHelp) deviceHelp.open = true;
  }
  controller.signal.addEventListener("abort", () => pairing?.finish(new DOMException("页面已离开", "AbortError")), { once: true });
  deviceCancel?.addEventListener("click", () => pairing?.finish(new Error("已取消本机连接；本次操作未继续。需要时重新点击原操作即可。")));
  deviceConnect?.addEventListener("click", () => actions.connect());

  function requireDevice() {
    if (pairing) return pairing.promise;
    if (controller.signal.aborted) return Promise.reject(new DOMException("页面已离开", "AbortError"));
    // Keep window.open inside this user gesture, before any await/import/fetch.
    // Re-pair on each action so a refresh or a restarted local service needs no
    // separate connect click, and no capability is persisted in browser storage.
    deviceConnection = null;
    const nonce = [...crypto.getRandomValues(new Uint8Array(16))].map(n => n.toString(16).padStart(2, "0")).join("");
    const popup = window.open(`http://127.0.0.1:39002/pair?origin=${encodeURIComponent(location.origin)}&nonce=${nonce}`, "agent-monitor-device", "width=520,height=240");
    if (!popup) {
      const message = "浏览器阻止了本机连接窗口。请允许此页面打开弹窗，再重试原操作；本次操作未继续。";
      deviceFeedback(message, true);
      return Promise.reject(new Error(message));
    }
    let resolve, reject;
    const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
    const attempt = { promise, validating: false, finished: false, timer: null, poll: null, receive: null };
    pairing = attempt;
    attempt.finish = (error, connection, result) => {
      if (attempt.finished) return;
      attempt.finished = true;
      clearTimeout(attempt.timer);
      clearInterval(attempt.poll);
      window.removeEventListener("message", attempt.receive);
      window.removeEventListener("pagehide", attempt.leave);
      popup.close();
      if (pairing === attempt) pairing = null;
      if (deviceCancel) deviceCancel.hidden = true;
      if (deviceConnect) deviceConnect.disabled = false;
      if (error) {
        deviceFeedback(error.message, error.name !== "AbortError");
        reject(error);
      } else {
        deviceConnection = connection;
        deviceFeedback(`已连接当前设备：${result.label}。断开时停止操作，不会切回服务器出口。`);
        if (current()) notice.textContent = "";
        resolve(connection);
      }
    };
    attempt.receive = async event => {
      if (attempt.finished || attempt.validating || event.origin !== "http://127.0.0.1:39002" || event.source !== popup
          || event.data?.type !== "agent-monitor-network" || event.data?.nonce !== nonce) return;
      attempt.validating = true;
      clearInterval(attempt.poll);
      popup.close();
      if (event.data.error) {
        attempt.finish(new Error(`本机连接端未能完成配对：${event.data.error} 请按下方连接帮助恢复后重试。`));
        return;
      }
      deviceFeedback("已收到本机响应，正在确认它与服务器的连接…");
      try {
        const connection = event.data.connection;
        if (!connection || typeof connection.id !== "string" || typeof connection.capability !== "string") throw new Error("本机返回的配对信息不完整。");
        const result = await request("network-status", { connection });
        if (!result.connected) throw new Error("服务器尚未确认本机连接可用。");
        if (!current()) { attempt.finish(new DOMException("页面已离开", "AbortError")); return; }
        attempt.finish(null, connection, result);
      } catch (error) {
        attempt.finish(error.name === "AbortError" ? error : new Error(`本机与服务器的连接校验未通过：${error.message} 请在本机执行 agent-monitor connect status，恢复连接后重试原操作。`));
      }
    };
    attempt.leave = () => attempt.finish(new DOMException("页面已离开", "AbortError"));
    window.addEventListener("pagehide", attempt.leave);
    window.addEventListener("message", attempt.receive);
    attempt.timer = setTimeout(() => attempt.finish(new Error(attempt.validating
      ? "本机与服务器的连接校验超时，本次操作未继续。请执行 agent-monitor connect status，恢复后重试原操作。"
      : "未收到本机连接端的配对响应，本次操作未继续。请在当前设备按下方帮助启用或检查连接服务，再重试原操作。")), 20000);
    attempt.poll = setInterval(() => {
      if (popup.closed && !attempt.validating) attempt.finish(new Error("本机连接窗口已关闭，配对未完成；本次操作未继续。可重试原操作，或展开连接帮助检查服务。"));
    }, 250);
    if (deviceConnect) deviceConnect.disabled = true;
    if (deviceCancel) deviceCancel.hidden = false;
    deviceFeedback("正在连接当前设备的网络…首次使用需启用本机连接服务；完成连接后将继续当前操作。");
    return promise;
  }

  const actions = createAccountActions({
    getAccounts: () => accounts, getBatch: () => batch, getSelectedIds: () => selectedIds,
    current, render, load, requireDevice, request, controller, notice, previewEgress,
  });

  async function request(action, payload) {
    const response = await fetch("/api/codex-accounts" + (action ? "/" + action : ""), {
      method: action ? "POST" : "GET",
      headers: { "X-Agent-Monitor": "codex-accounts", ...(action ? { "Content-Type": "application/json" } : {}) },
      body: action ? JSON.stringify(payload) : undefined,
      signal: controller.signal,
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "账号操作请求失败。");
    return data;
  }

  function schedule() {
    clearTimeout(timer);
    if (current() && accounts.some((account) => account.busy)) timer = setTimeout(load, 1000);
  }

  function update(data) {
      accounts = data.accounts;
      if (selectedIds === null) selectedIds = new Set();
      else selectedIds = new Set([...selectedIds].filter(id => accounts.some(a => a.id === id && a.eligible !== false && !a.disabled)));
      batch = data.batch;
      quotaBatch = data.quota_batch || null;
      upstreamPause = data.upstream_pause || null;
      unavailable = data.unavailable_accounts || [];
      window.AgentMonitor.updateCodexQuotaReadings?.(accounts, upstreamPause);
      notice.textContent = "";
      render();
      updateRefreshAllButton();
      lastBatchStatus = batchStatus.textContent;
      hasSnapshot = true;
      if (locatePending) {
        const id = locatePending; locatePending = null;
        if (!openAccount(id)) notice.textContent = '该账号已不在当前托管列表中；未执行任何操作。';
        else if (new URLSearchParams(window.location?.search || "").get('task') === 'refresh') {
          const card = Array.from(list.children).find(node => node.dataset.id === id);
          const refresh = card?.querySelector('[data-reader-state$=":refresh"]');
          if (refresh && !refresh.disabled) refresh.focus();
        }
      }
      if (window.location?.pathname === '/accounts') window.AgentMonitor.restoreReaderState?.();
  }

  function updateRefreshAllButton() {
    const targets = accounts.filter(account => account.eligible !== false);
    const available = targets.filter(account => selectedIds?.has(account.id) && !accountRefreshBlocked(account));
    if (refreshSelected) {
      refreshSelected.disabled = refreshingAll || actions.busy || !available.length || Boolean(upstreamPause) || accounts.some(account => account.busy);
      refreshSelected.textContent = refreshingAll ? "正在提交配额查询…" : `查询所选配额（${available.length}）`;
    }
    const pending = pendingQuotaAccounts();
    if (quotaRetry) {
      quotaRetry.hidden = !quotaBatch || !pending.length;
      quotaRetry.disabled = refreshingAll || actions.busy || Boolean(upstreamPause) || accounts.some(a => a.busy);
      quotaRetry.textContent = `继续未完成的 ${pending.length} 个账号配额查询`;
    }
    if (quotaStatus) {
      quotaStatus.hidden = !quotaBatch;
      quotaStatus.textContent = quotaBatch ? `${quotaBatchSummary(quotaBatch)} · 开始于 ${timestamp(quotaBatch.created_at)}。查询不会发送消息。` : "";
    }
    if (operationHistory) operationHistory.hidden = !quotaBatch && !batch;
    if (quotaProgress) {
      const active = accounts.some(account => account.busy && account.operation?.refresh_only);
      quotaProgress.hidden = !refreshingAll && !active;
      quotaProgress.textContent = refreshingAll ? "正在连接、确认或提交所选配额查询…"
        : active ? quotaBatchSummary(quotaBatch) || "正在查询配额…" : "";
    }
  }

  function pendingQuotaAccounts() {
    return accounts.filter(a => a.eligible !== false && !accountRefreshBlocked(a)
      && quotaBatch?.items.some(item => item.profile_id === a.id && item.operation?.stage !== "succeeded"));
  }

  async function load() {
    if (!current()) return;
    if (loading) { reloadPending = true; return; }
    if (document.hidden) { schedule(); return; }
    loading = true;
    try {
      const [data] = await Promise.all([request(), window.AgentMonitor.ensureTimezone()]);
      if (!current()) return;
      update(data);
    } catch (error) {
      if (current() && error.name !== "AbortError") {
        notice.textContent = error.message + " 可点击“重读本地状态”重试。";
        batchStatus.textContent = hasSnapshot
          ? (lastBatchStatus ? lastBatchStatus + " · " : "") + "刷新失败，当前展示为上次记录；点击“重读本地状态”可重试。"
          : "账号读取失败；点击“重读本地状态”可重试。";
      }
    } finally {
      loading = false;
      if (reloadPending) { reloadPending = false; load(); }
      else schedule();
    }
  }

  async function refreshAllQuotas(action = "refresh-selected") {
    if (refreshingAll || actions.busy) return;
    const eligible = accounts.filter(account => account.eligible !== false);
    const targets = action === "refresh-retry" ? pendingQuotaAccounts()
      : eligible.filter(account => !accountRefreshBlocked(account) && (action !== "refresh-selected" || selectedIds.has(account.id)));
    if (!targets.length || upstreamPause || accounts.some(account => account.busy)) return;
    refreshingAll = true;
    updateRefreshAllButton();
    try {
      const expected = quotaBatch?.revision ?? null;
      const connection = await actions.requireDevice();
      const approval = await previewEgress(request, targets, controller.signal, {
        connection, profile_ids: targets.map(account => account.id), approval_action: action,
        approval_expected: expected,
      });
      if (!approval) return;
      notice.textContent = "正在提交所选账号配额查询…";
      await request(action, {
        profile_ids: targets.map(account => account.id), connection, confirmation: approval,
        approval_expected: expected,
      });
      await load();
      if (current()) notice.textContent = `已提交 ${targets.length} 个账号的配额查询；账号会逐个显示结果。`
        + (targets.length < eligible.length ? `其余 ${eligible.length - targets.length} 个账号未纳入本次查询。` : "");
    } catch (error) {
      if (current() && error.name !== "AbortError") notice.textContent = error.message;
    } finally {
      refreshingAll = false;
      updateRefreshAllButton();
    }
  }

  async function perform(action, id, button) {
    const account = accounts.find(value => value.id === id);
    if (action === "repair-login") { openRecovery(true, account); return; }
    if (action === "set-disabled" && !window.confirm(account?.disabled
      ? `重新启用 ${account.email}？仅改变本机 Monitor 的停用状态；其他限制保留，不会自动查询、登录或发送。`
      : `停用 ${account.email}？此账号将从本机 Monitor 的查询、登录和发送中排除，已有身份、登录态与配额记录保留。`)) return;
    if (action === "attribute-account-pause" && !window.confirm(`我已确认本次暂停仅属于 ${account.email} 的账号问题。\n暂停：${upstreamPause?.id}\n失败操作：${account?.operation?.id}\n\n将本次共享暂停归属到此账号，保留此账号的保护及其他限制，允许其他账号另行查询。这不证明认证、网络或服务已恢复，不发起任何请求。`)) return;
    if (action === "resume" && !window.confirm("我已核对此账号已记录的失败信息、账号状态及服务端要求，确认解除此账号的本地限制。\n\n这不证明上游已恢复；不会自动登录、查询或发送。额度限制和本机 Codex 通道暂停仍保留。")) return;
    if (action === "release-rate-limit" && !window.confirm("仅解除此账号在本页的限流暂停，不会发送消息或查询配额。\n\n这不证明服务端限流已解除；请按服务端指引等待后再尝试。认证和额度限制仍会保留。")) return;
    const upstream = ["refresh", "login"].includes(action);
    let approval = null;
    if (upstream) {
      try {
        const connection = await actions.requireDevice();
        const expected = batch?.id ?? null;
        approval = await previewEgress(request, account ? [account] : [], controller.signal, {
          connection, profile_ids: [id], approval_action: action, approval_expected: expected,
        });
        if (!approval) return;
        button.disabled = true;
        notice.textContent = action === "refresh" ? "正在提交配额查询…" : "正在提交登录操作…";
        await request(action, { id, connection, confirmation: approval, approval_expected: expected });
      } catch (error) {
        if (current() && error.name !== "AbortError") notice.textContent = error.message;
        return;
      }
      if (current()) await load();
      return;
    }
    button.disabled = true;
    notice.textContent = action === "forget-login" ? "正在清除本页保存的登录态…" : "正在提交操作…";
    try {
      await request(action, { id, ...(["resume", "set-disabled", "attribute-account-pause"].includes(action) ? {operation_id: account?.operation?.id} : {}),
        ...(action === "set-disabled" ? {disabled: !account?.disabled} : {}),
        ...(action === "attribute-account-pause" ? {pause_id: upstreamPause?.id} : {}) });
      if (!current()) return;
      notice.textContent = action === "cancel" ? "正在取消；已发出的消息无法撤回。" : "";
      await load();
      if (current() && action === "resume") notice.textContent = "此账号的本地限制已解除，未发出上游请求。本机通道暂停和已记录的失败信息保留；后续操作需重新点击。";
      if (current() && action === "set-disabled") {
        const updated = accounts.find(value => value.id === id);
        if (updated) notice.textContent = accountToggleNotice(updated);
      }
    } catch (error) {
      if (current() && error.name !== "AbortError") notice.textContent = error.message;
    } finally {
      if (current()) button.disabled = false;
    }
  }

  function button(label, action, account) {
    const variant = action === "start" ? " btn-primary" : action === "forget-login" ? " btn-danger" : "";
    const node = element("button", label, "btn" + variant);
    node.type = "button";
    node.dataset.readerState = `codex-account:${account.id}:${action}`;
    node.disabled = account.busy && action !== "cancel";
    node.addEventListener("click", () => perform(action, account.id, node));
    return node;
  }

  function renderBatch() {
    renderAccountBatch({ accounts, batch, selectedIds, unavailable, busy: actions.busy, upstreamPause },
      { root, batchStart, batchNext, batchRetry, batchStatus, batchHelp, egressPreview, deviceConnect, blockedReason });
    renderPause();
  }

  function renderPause() {
    if (!current()) return;
    renderAccountPause(upstreamPause, window.AgentMonitor.codexPauseTarget?.(upstreamPause?.id), pauseControls);
    const panel = recoveryBody?.querySelector(".quota-recovery-panel");
    if (panel) {
      const selected = accounts.find(account => account.id === recoveryAccountId);
      const pauseKey = recoveryAccountId ? `${recoveryAccountId}:${selected?.account_quota_pause?.id || ""}:disabled:${Boolean(selected?.disabled)}:global:${upstreamPause?.id || ""}` : `global:${upstreamPause?.id || ""}`;
      if (recoveryPauseId !== pauseKey) {
        recoveryPauseId = pauseKey;
        panel.refresh?.();
      }
    }
    const summary = recoveryHistory?.querySelector("summary");
    if (summary) summary.textContent = recoveryAccountId ? "查看此账号的登录修复" : upstreamPause ? "查看通道暂停与检查" : "查看最近恢复记录";
  }
  const recoveryHistory = root.querySelector("#codex-recovery-history");
  const recoveryBody = root.querySelector("#codex-recovery-body");
  let recoveryPauseId, recoveryAccountId = null;
  function openRecovery(scroll = true, account = null) {
    if (!recoveryBody) return;
    let panel = recoveryBody.querySelector(".quota-recovery-panel");
    const selectedId = account?.id || null;
    if (panel && selectedId !== recoveryAccountId) {
      panel.dispose?.();
      panel.remove();
      panel = null;
    }
    recoveryAccountId = selectedId;
    const pauseKey = selectedId ? `${selectedId}:${account.account_quota_pause?.id || ""}:disabled:${Boolean(account.disabled)}:global:${upstreamPause?.id || ""}` : `global:${upstreamPause?.id || ""}`;
    if (!panel) {
      panel = window.AgentMonitor.createRecoveryPanel?.("self", {
        ...(selectedId ? {profile: `monitor-${selectedId}`, pauseScope: "account"} : {}),
        onSaved: () => load(), onContinue: async () => {
        await load();
        setBatchMode(true);
        const next = quotaBatch ? (quotaRetry && !quotaRetry.hidden ? quotaRetry : refreshSelected)
          : !batchRetry.hidden && !batchRetry.disabled ? batchRetry : batch ? batchNext : batchStart;
        next.scrollIntoView({block: "center"});
        if (!next.disabled) next.focus({preventScroll: true});
      }});
      if (panel) recoveryBody.appendChild(panel);
      recoveryPauseId = pauseKey;
    } else {
      recoveryPauseId = pauseKey;
      panel.refresh?.();
    }
    if (recoveryHistory) recoveryHistory.open = true;
    const summary = recoveryHistory?.querySelector("summary");
    if (summary) summary.textContent = selectedId ? "查看此账号的登录修复" : upstreamPause ? "查看通道暂停与检查" : "查看最近恢复记录";
    if (scroll) panel?.scrollIntoView({block: "nearest"});
  }
  pauseControls.pauseRecovery?.addEventListener("click", () => openRecovery());
  recoveryHistory?.addEventListener("toggle", () => {
    if (recoveryHistory.open && recoveryBody && !recoveryBody.querySelector(".quota-recovery-panel")) openRecovery();
  });
  document.addEventListener("agent-monitor:recovery-state", load);
  document.addEventListener("agent-monitor:quota-rendered", renderPause);

  batchStart.addEventListener("click", () => actions.performBatch("batch-start"));
  batchNext.addEventListener("click", () => actions.performBatch("batch-start"));
  batchRetry.addEventListener("click", () => actions.performBatch("batch-retry"));
  egressPreview?.addEventListener("click", actions.preview);
  root.querySelector("#codex-select-all").addEventListener("click", () => {
    selectedIds = new Set(accounts.filter(a => a.eligible !== false && !a.disabled && matchesAccount(a)).map(a => a.id));
    render();
  });
  root.querySelector("#codex-select-none").addEventListener("click", () => {
    selectedIds.clear();
    render();
  });
  refreshSelected?.addEventListener("click", () => refreshAllQuotas("refresh-selected"));
  quotaRetry?.addEventListener("click", () => refreshAllQuotas("refresh-retry"));

  function matchesAccount(account) {
    const query = (searchInput?.value || "").trim().toLowerCase();
    const state = stateFilter?.value || "all";
    const matchesState = state === "all" || state === "login" && accountNeedsLogin(account)
      || state === "restricted" && (Boolean(account.account_quota_pause) || Object.keys(account.restrictions || {}).length > 0)
      || state === "busy" && account.busy || state === "disabled" && account.disabled;
    return matchesState && `${account.email} ${account.account_id || ""}`.toLowerCase().includes(query);
  }
  function filterAccounts() {
    let visible = 0, selectedOutside = 0;
    for (const card of list.children) {
      const account = accounts.find(account => account.id === card.dataset.id);
      if (!account) continue;
      card.hidden = !matchesAccount(account);
      if (card.hidden && selectedIds.has(account.id)) selectedOutside++;
      if (!card.hidden) visible++;
      const checkbox = card.querySelector(".codex-select-account");
      if (checkbox) checkbox.hidden = !batchMode;
    }
    const selection = root.querySelector("#codex-selection-count");
    if (selection) selection.textContent = `已选 ${selectedIds.size} / ${accounts.filter(a => a.eligible !== false && !a.disabled).length} 个账号${selectedOutside ? `（筛选外 ${selectedOutside} 个）` : ""}`;
    const count = root.querySelector("#codex-account-count");
    if (count) count.textContent = `显示 ${visible} / ${accounts.length} 个账号`;
    const empty = root.querySelector("#codex-account-empty");
    if (empty) empty.hidden = visible > 0 || !accounts.length;
  }
  searchInput?.addEventListener("input", filterAccounts);
  stateFilter?.addEventListener("change", filterAccounts);
  function setBatchMode(value) {
    batchMode = value;
    root.querySelector("#codex-batch-panel").hidden = !batchMode;
    batchToggle?.setAttribute("aria-expanded", String(batchMode));
    if (batchToggle) batchToggle.textContent = batchMode ? "结束选择" : "批量操作";
    if (!batchMode) { selectedIds.clear(); render(); }
    else filterAccounts();
  }
  batchToggle?.addEventListener("click", () => setBatchMode(!batchMode));

  function render() {
    renderBatch();
    updateRefreshAllButton();
    const warnings = root.querySelector("#codex-account-unavailable");
    if (warnings) {
      warnings.replaceChildren(...unavailable.map((account) => element("p", `未纳入发送：${account.account_id}。${account.detail}`)));
    }
    // Keep existing cards and authorization links in place while polling:
    // rebuilding a focused link every second interrupts keyboard/copy use.
    const existing = new Map(Array.from(list.children).map((node) => [node.dataset.id, node]));
    if (!accounts.length) {
      list.replaceChildren(element("p", "尚无可操作账号；来源机器登录 Codex 并同步账号记录后会自动出现。", "quota-scope"));
      return;
    }
    const priority = (account) => account.operation?.stage === "login" ? 0 : account.busy ? 1 : 2;
    const ordered = [...accounts].sort((a, b) => priority(a) - priority(b));
    for (const [index, account] of ordered.entries()) {
      const batchItem = batch?.items.find((item) => item.profile_id === account.id);
      const ownReading = operationReading(account);
      const latestReading = ownReading || account.last_quota;
      const resetLabel = window.AgentMonitor.formatQuotaReset?.(latestReading?.seven_day_resets_at);
      const signature = JSON.stringify([account, batchItem, resetLabel, actions.busy, upstreamPause, accounts.some(a => a.busy)]);
      const old = existing.get(account.id);
      existing.delete(account.id);
      if (old?.dataset.signature === signature) {
        const checkbox = old.querySelector("input");
        if (checkbox) checkbox.checked = selectedIds.has(account.id);
        if (list.children[index] !== old) list.insertBefore(old, list.children[index] || null);
        continue;
      }
      const card = element("article", undefined, "codex-account-card");
      card.dataset.id = account.id;
      card.dataset.signature = signature;
      const op = account.operation;
      const loginRequired = accountNeedsLogin(account);
      const heading = element("div", undefined, "codex-account-heading");
      const tone = account.disabled ? "bad" : op?.stage === "succeeded" ? "ok"
        : ["failed", "interrupted"].includes(op?.stage) ? "bad"
        : ["partial", "login", "login_queued"].includes(op?.stage) ? "warn"
        : account.busy ? "info" : "identity";
      const operationLabel = account.disabled ? "已停用" : !account.busy && loginRequired && account.has_credentials
        ? "需要重新登录" : !account.busy && loginRequired ? "需要登录" : op?.failure?.category === "needsLogin"
        ? "需要重新登录" : op?.stage === "succeeded" ? ""
        : op?.failure?.category === "needsReauth"
        ? "配额认证被拒绝"
        : op?.refresh_only && op.stage === "failed"
        ? (op.failure?.category === "locallyPaused" ? "配额查询未执行" : "配额查询失败")
        : op ? STAGES[op.stage] || op.stage : "";
      heading.append(element("strong", account.email));
      if (operationLabel) heading.append(element("span", operationLabel, "status-pill " + tone));
      if (account.eligible !== false) {
        const label = element("label", undefined, "codex-select-account");
        const checkbox = element("input");
        checkbox.type = "checkbox";
        checkbox.disabled = Boolean(account.disabled);
        checkbox.checked = selectedIds.has(account.id);
        checkbox.setAttribute("aria-label", `选择 ${account.email}（${account.account_id}）`);
        checkbox.addEventListener("change", () => {
          if (checkbox.checked) selectedIds.add(account.id); else selectedIds.delete(account.id);
          renderBatch();
          updateRefreshAllButton();
          filterAccounts();
        });
        label.append(checkbox);
        heading.prepend(label);
      }
      if (new URLSearchParams(window.location?.search || "").get('profile') === account.id) {
        card.dataset.located = 'true';
        heading.append(element('span', '当前账号', 'codex-batch-result'));
      }
      card.append(heading);
      if (account.disabled) card.append(element("p", "用户已确认此账号不可用；本机 Monitor 已跳过查询、登录和发送，历史记录保留。", "quota-scope"));
      const restrictionDetails = account.restriction_messages
        ? Object.entries(account.restriction_messages).map(([key, detail]) => key === "quota" ? quotaHoldMessage(account, upstreamPause) : detail)
        : account.restriction_details || [];
      if (restrictionDetails.length) heading.append(element("span", (accountNeedsLogin(account) || account.restrictions?.authentication) ? "登录态不可用" : account.restrictions?.quota ? "发送受限" : "账号有限制", "codex-batch-result"));
      if (op) card.append(element("p", `最近${op.login_only ? "登录" : op.refresh_only ? "配额查询" : "发送"}：${STAGES[op.stage] || op.stage} · ${timestamp(op.recovery_pause_id && op.stage === "succeeded" ? op.after?.observed_at : op.started_at)}`, "quota-scope account-last-result"));
      const details = element("details", undefined, "note-disclosure");
      details.append(element("summary", "详情与维护"));
      const detailBody = element("div", undefined, "disclosure-body");
      const currentDetail = element("section", undefined, "account-detail-section");
      for (const detail of restrictionDetails) currentDetail.append(element("p", detail, "quota-scope"));
      if (loginRequired && !account.has_credentials) currentDetail.append(element("p", "登录态：需要官方授权", "quota-scope"));
      const quotaDetail = element("section", undefined, "account-detail-section");
      const historyDetail = element("section", undefined, "account-detail-section");
      const technicalDetail = element("section", undefined, "account-detail-section");
      const managementDetail = element("section", undefined, "account-detail-section");
      if (account.eligible === false) card.append(element("p", "已不在当前登录记录中；保留本轮结果，不纳入下一轮或继续发送。", "quota-scope"));
      const duplicateEmail = accounts.some((other) => other.id !== account.id && other.email.toLowerCase() === account.email.toLowerCase());
      if (account.account_id) (duplicateEmail ? card : managementDetail).append(element("p", `工作区：${account.account_id}`, "quota-scope codex-workspace"));

      if (batchItem) {
        const result = element("span", `本批：${MESSAGE_STATUS[batchItem.operation?.message_status || "not_sent"]}`, "codex-batch-result");
        const attention = account.busy || ["unknown", "failed"].includes(batchItem.operation?.message_status);
        (attention ? heading : historyDetail).append(result);
      }
      const retained = account.batch_result;
      if (retained && retained.batch_id !== batch?.id) historyDetail.append(element("p", `保留的批次结果：${MESSAGE_STATUS[retained.message_status] || "状态未知"} · 开始于 ${timestamp(retained.started_at)}`, "quota-scope"));
      const shownMessageIds = new Set([op?.id]);
      for (const batchMessage of [retained, batchItem?.operation]) {
        if (batchMessage?.message && !shownMessageIds.has(batchMessage.id)) {
          historyDetail.append(element("p", `保留的批次消息：${batchMessage.message}`, "quota-scope"));
          shownMessageIds.add(batchMessage.id);
        }
      }
      if (op) {
        if (op.stage === "succeeded") currentDetail.append(element("p", `最近${op.login_only ? "登录" : op.refresh_only ? "查询" : "发送"}已完成：${op.detail || "操作完成"}`, "quota-scope"));
        if (op.stage !== "succeeded" && !(account.restriction_details || []).includes(op.detail)) currentDetail.append(element("p", `最近${op.login_only ? "登录" : op.refresh_only ? "配额查询" : "发送操作"}（${timestamp(op.started_at)}）：${op.detail}`));
        if (!op.refresh_only && !op.login_only) currentDetail.append(element("p", `消息结果：${MESSAGE_STATUS[op.message_status]}`, "quota-scope"));
        if (op.message) currentDetail.append(element("p", `本次抽取的消息：${op.message}`, "quota-scope"));
        if (op.failure) technicalDetail.append(element("p", `停止阶段：${STAGES[op.failure.stage] || op.failure.stage} · 错误类别：${op.failure.category}${op.failure.http_status ? " · HTTP " + op.failure.http_status : ""}`, "quota-scope"));
        const diag = op.recovery_pause_id && op.stage === "succeeded" ? null : op.diagnostics;
        appendRpcFailure(technicalDetail, diag);
        if (diag?.login_completion) technicalDetail.append(element("p", `官方授权完成回执未成功：${diag.login_completion.detail || "未提供详细原因"}。回执失败可能已保存凭据；本次未继续查询配额，不自动重新申请设备码。`, "quota-scope"));
        if (diag?.local_commit) technicalDetail.append(element("p", `本地配额保存或发布未完成：${diag.local_commit.error_type || "原因未确认"}。保留暂停及已有候选结果，不要重复授权。`, "quota-scope"));
        if (op.candidate_quota && op.stage !== "succeeded") technicalDetail.append(element("p", `配额复验候选观测：${timestamp(op.candidate_quota.observed_at)}；尚未完成有效窗口核验、保存和结清，不作为恢复成功。`, "quota-scope"));
        if (diag?.last_request) technicalDetail.append(element("p", `最后请求：${diag.last_request} · ${timestamp(diag.last_request_at)}`, "quota-scope"));
        if (diag?.last_event) technicalDetail.append(element("p", `最后进度：${diag.last_event} · ${timestamp(diag.last_event_at)}`, "quota-scope"));
        if (diag?.thread_id) technicalDetail.append(element("p", `Thread：${diag.thread_id}${diag.turn_id ? " · Turn：" + diag.turn_id : ""}`, "quota-scope"));
        if (diag?.interrupt) technicalDetail.append(element("p", `超时中止：${diag.interrupt === "acknowledged" ? "CLI 已确认" : "未取得确认"}；不代表消息未消耗额度。`, "quota-scope"));
        if (op.stage === "login" && op.verification_url && op.user_code) {
          const box = element("div", undefined, "codex-authorization");
          const link = element("a", "打开 OpenAI 官方授权页", "btn");
          link.href = op.verification_url;
          link.target = "_blank";
          link.rel = "noopener noreferrer";
          box.append(link, element("span", "设备码（不是邮箱验证码）："), element("code", op.user_code));
          card.append(box, element("p", op.recovery
            ? `请确认官方页面登录的是 ${account.email} 及原工作区。官方授权成功且新凭据身份核验通过后，将复验一次配额；有效读数保存并发布后才结清原任务和暂停。不发送消息。设备确认最多等待 10 分钟，另有有界的身份、配额与保存阶段；期间占用本机 Codex 通道。`
            : `请确认官方页面登录的是 ${account.email}。授权成功后仅保存登录态，不自动发送或查询；最多等待 10 分钟。`, "quota-scope"));
          if (op.recovery) card.append(element("p", "后台认证与配额检查使用执行机器的 Monitor 路由桥；官方授权网页使用浏览器自己的网络。需要网页同出口时，请先核实浏览器路由。", "quota-scope"));
        }
        if (latestReading) {
          const reading = element("dl", undefined, "codex-account-reading");
          reading.append(element("dt", "7 天配额窗口重置"), element("dd", `${resetLabel || "重置时间未知"} · ${timestamp(latestReading.seven_day_resets_at, true)}`));
          const used = latestReading.seven_day_used_pct;
          reading.append(element("dt", "7 天窗口已用"), element("dd", used == null ? "服务端未返回" : `${used}%`));
          reading.append(element("dt", ownReading ? "本次操作的最近观测" : "最近成功配额观测"), element("dd", timestamp(latestReading.observed_at)));
          if (op.before?.seven_day_resets_at != null) quotaDetail.append(element("p", `${op.refresh_only ? "查询前" : "发送前"}的重置时间：${timestamp(op.before.seven_day_resets_at, true)}`, "quota-scope"));
          quotaDetail.append(reading);
          if (latestReading.seven_day_resets_at != null && latestReading.seven_day_resets_at * 1000 <= Date.now()) quotaDetail.append(element("p", `这份重置时间已过去，不代表额度已恢复。${upstreamPause ? "请先查看本页的通道暂停与检查。" : accountRefreshBlocked(account) ? "请先处理此账号的其他限制。" : "请仅刷新配额获取当前读数。"}`, "quota-scope"));
        }
      }
      const failureExplanation = quotaFailureExplanation(account, upstreamPause, accounts);
      if (failureExplanation) card.append(element("p", failureExplanation, "quota-scope"));
      const controls = element("div", undefined, "codex-account-controls");
      if (account.account_quota_pause) {
        heading.append(element("span", "此账号配额查询已暂停", "codex-batch-result"));
        card.append(element("p", accountQuotaPauseGuidance(account), "quota-scope account-quota-pause-guidance"));
        const repair = button("打开登录修复", "repair-login", account);
        repair.disabled = false;
        controls.append(repair);
      }
      if (account.eligible !== false) {
        const send = element("button", "向此账号发送一条消息", "btn");
        send.type = "button";
        send.disabled = actions.busy || accounts.some(a => a.busy) || Boolean(upstreamPause) || Boolean(account.account_quota_pause) || Boolean(account.disabled) || loginRequired || Object.keys(account.restrictions || {}).length > 0;
        send.addEventListener("click", () => actions.performBatch("batch-start", [account.id]));
        currentDetail.append(send);
      }
      const refresh = button("仅刷新配额", "refresh", account);
      const restrictions = account.restrictions || {};
      refresh.disabled ||= accountRefreshBlocked(account) || Boolean(upstreamPause) || Boolean(account.account_quota_pause);
      controls.append(refresh);
      if (loginRequired && !account.disabled) {
        const login = button("单独登录", "login", account);
        login.disabled ||= Boolean(upstreamPause || account.account_quota_pause || restrictions.authentication || restrictions.unknown || restrictions.rate_limit || restrictions.login_rate_limit);
        controls.append(login);
      }
      if (restrictions.authentication || restrictions.unknown || restrictions.rate_limit || restrictions.login_rate_limit) controls.append(button("核对后解除此账号限制", "resume", account));
      if (account.last_failure && (account.last_failure.id !== op?.id || op?.recovery_pause_id)) {
        historyDetail.append(element("p", `保留的失败摘要（${timestamp(account.last_failure.started_at)}）：${account.last_failure.detail}`, "quota-scope"));
        appendRpcFailure(historyDetail, account.last_failure.diagnostics, "历史请求错误");
        if (account.last_failure.diagnostics?.login_completion) historyDetail.append(element("p", `历史官方授权完成回执：${account.last_failure.diagnostics.login_completion.detail || "未提供详细原因"}。回执失败不证明未保存凭据。`, "quota-scope"));
      }
      if (account.busy) controls.append(button("取消操作", "cancel", account));
      else if (account.has_credentials || restrictions.authentication) {
        const maintenance = element("div", undefined, "codex-account-controls");
        const forget = button("清除本页登录态", "forget-login", account);
        if (account.account_quota_pause) {
          forget.disabled = true;
          maintenance.append(element("p", "此账号的配额暂停尚未结清；先打开登录修复并完成复验，再清除登录态。", "quota-scope"));
        }
        maintenance.append(forget);
        managementDetail.append(maintenance);
      }
      if (!account.busy) controls.append(button(account.disabled ? "重新启用此账号" : "停用此账号", "set-disabled", account));
      if ((account.disabled || ["token_expired", "token_revoked"].includes(op?.diagnostics?.rpc_failure?.upstream_code)
          && op.diagnostics.rpc_failure.http_status === 401 && op.diagnostics.rpc_failure.stage === "quota_read"
          && op.diagnostics.rpc_failure.kind === "rpc_error") && upstreamPause?.reason === "operationFailed" && op?.refresh_only
          && op.stage === "failed" && op.message_status === "not_sent" && op.diagnostics?.last_request
          && (!upstreamPause.source || upstreamPause.source.profile_id === account.id && upstreamPause.source.operation_id === op.id)) {
        controls.append(button("确认仅此账号异常，解除本次通道暂停", "attribute-account-pause", account));
      }
      for (const [section, label] of [[currentDetail, "当前状态与操作"], [quotaDetail, "最近配额观测"], [historyDetail, "历史记录"], [technicalDetail, "技术诊断"], [managementDetail, "账号维护"]]) {
        if (!section.children.length) continue;
        section.prepend(element("h4", label));
        detailBody.append(section);
      }
      details.open = Boolean(old?.querySelector("details")?.open);
      const footer = element("div", undefined, "codex-account-footer");
      details.append(detailBody);
      footer.append(controls);
      card.append(footer, details);
      if (old) old.replaceWith(card); else list.append(card);
      if (list.children[index] !== card) list.insertBefore(card, list.children[index] || null);
    }
    for (const node of existing.values()) node.remove();
    filterAccounts();
  }

  function openAccount(id) {
    const card = Array.from(list.children).find(node => node.dataset.id === id);
    if (!current() || !card) return false;
    if (searchInput) searchInput.value = "";
    if (stateFilter) stateFilter.value = "all";
    filterAccounts();
    root.open = true;
    card.tabIndex = -1;
    card.scrollIntoView({ block: "center" });
    card.focus({ preventScroll: true });
    return true;
  }
  document.querySelector('#accounts-reload')?.addEventListener('click', load);
  const back = document.querySelector('#accounts-return');
  const rawBack = new URLSearchParams(window.location?.search || "").get('return');
  if (back && rawBack) {
    try { const url = new URL(rawBack, location.origin); if (url.origin === location.origin && url.pathname === '/') back.href = url.pathname + url.search + url.hash; } catch (_) {}
  }
  if (window.location?.pathname !== '/accounts') window.AgentMonitor.openCodexAccount = openAccount;
  function visibility() { if (!document.hidden) load(); }
  document.addEventListener("visibilitychange", visibility);
  root.addEventListener("toggle", () => { if (root.open) load(); });
  window.AgentMonitor.onPageCleanup(() => {
    clearTimeout(timer);
    controller.abort();
    if (window.AgentMonitor.openCodexAccount === openAccount) delete window.AgentMonitor.openCodexAccount;
    document.removeEventListener("visibilitychange", visibility);
    document.removeEventListener("agent-monitor:quota-rendered", renderPause);
    document.removeEventListener("agent-monitor:recovery-state", load);
  });
  if (autoLoad) {
    load();
    window.AgentMonitor.pageInterval(load, 30000);
  }
  return { load, update };
}

// Actions capture their submission snapshot before awaiting device/egress confirmation.
export function createAccountActions(dependencies) {
  const { getAccounts, getBatch, getSelectedIds, current, render, load,
    requireDevice, request, controller, notice, previewEgress } = dependencies;
  let submittingBatch = false;
  async function performBatch(action, ids) {
    if (submittingBatch) return;
    // Confirmation and submission must refer to the same members and CAS.
    const expected = getBatch()?.id ?? null;
    const payload = action === "batch-start"
      ? { expected_batch_id: expected, profile_ids: ids || [...getSelectedIds()], approval_action: action, approval_expected: expected }
      : { batch_id: expected, approval_action: action, approval_expected: expected };
    const targets = action === "batch-start"
      ? getAccounts().filter(a => payload.profile_ids.includes(a.id))
      : getAccounts().filter(a => {
        const item = getBatch()?.items?.find(row => row.profile_id === a.id);
        return item && (!item.operation || item.operation.message_status === "not_sent");
      });
    if (action === "batch-start" && !targets.length) return;
    submittingBatch = true;
    render();
    let failure = "";
    let submitted = false;
    try {
      payload.connection = await requireDevice();
      if (action === "batch-start" || action === "batch-retry") {
        if (targets.length) payload.profile_ids = targets.map(account => account.id);
        payload.confirmation = await previewEgress(request, targets, controller.signal, payload);
        if (!payload.confirmation) return;
      }
      if (!current()) return;
      notice.textContent = "正在提交批次…";
      submitted = true;
      await request(action, payload);
      if (current()) notice.textContent = "";
    } catch (error) {
      if (current() && error.name !== "AbortError") failure = error.message + (submitted ? " 刷新会保留已接收的批次，请先查看结果。" : "");
    } finally {
      submittingBatch = false;
      if (current()) await load();
      if (current() && failure) notice.textContent = failure;
    }
  }

  async function connect() {
    if (submittingBatch) return;
    submittingBatch = true;
    render();
    try { await requireDevice(); }
    catch (error) { if (current() && error.name !== "AbortError") notice.textContent = error.message; }
    finally { submittingBatch = false; if (current()) render(); }
  }

  async function preview() {
    if (submittingBatch) return;
    submittingBatch = true;
    render();
    try {
      const connection = await requireDevice();
      await previewEgress(request, null, controller.signal, { connection });
    }
    catch (error) {
      if (current() && error.name !== "AbortError") notice.textContent = error.message;
    }
    finally { submittingBatch = false; if (current()) render(); }
  }
  return { performBatch, preview, connect, requireDevice, get busy() { return submittingBatch; } };
}

export function operationReading(account) {
  return window.AgentMonitor.latestCodexQuotaReading?.({operation: account.operation}) || account.operation?.after;
}

export function renderAccountPause(pause, target, controls) {
  const { pausePanel, pauseSummary, pauseDetail, pauseRecovery } = controls;
  if (!pausePanel) return;
  pausePanel.hidden = !pause;
  if (!pause) return;
  pauseSummary.textContent = "Codex 请求已暂停，请核对原因后继续。";
  const time = pause.at ? timestamp(pause.at, true) : "未记录";
  pauseDetail.textContent = `暂停记录：${time}。已有结果保留；账号自身异常可在停用后单独处理，其他原因请查看通道检查。`;
  pauseRecovery.disabled = false;
  pauseRecovery.textContent = "查看恢复操作";
}

export function renderAccountBatch(state, controls) {
  const { accounts, batch, selectedIds, unavailable, busy: submittingBatch, upstreamPause } = state;
  const { root, batchStart, batchNext, batchRetry, batchStatus, batchHelp, egressPreview, deviceConnect, blockedReason } = controls;
    const observation = root.querySelector("#codex-batch-observation");
    const receipt = root.querySelector("#codex-send-receipt");
    if (receipt) { receipt.textContent = ""; receipt.hidden = !batch; }
    if (observation) { observation.textContent = ""; observation.hidden = !batch; }
    const busy = accounts.some((account) => account.busy);
    const eligible = accounts.filter((account) => account.eligible !== false);
    const count = eligible.filter(account => selectedIds?.has(account.id)).length;
    const selectedLoginRequired = eligible.filter(account => selectedIds?.has(account.id) && accountNeedsLogin(account)).length;
    const selectedPaused = eligible.filter(account => selectedIds?.has(account.id) && account.account_quota_pause).length;
    const selectionCount = root.querySelector("#codex-selection-count");
    if (selectionCount) selectionCount.textContent = `已选 ${count} / ${eligible.length} 个账号`;
    batchStart.hidden = Boolean(batch);
    batchStart.textContent = `给所选 ${count} 个账号发送`;
    batchStart.disabled = submittingBatch || busy || !count || selectedLoginRequired > 0 || selectedPaused > 0 || Boolean(upstreamPause);
    batchNext.hidden = !batch;
    batchNext.textContent = `开始新一轮 · 所选 ${count} 个账号`;
    batchNext.disabled = submittingBatch || busy || !count || selectedLoginRequired > 0 || selectedPaused > 0 || Boolean(upstreamPause);
    if (blockedReason) {
      blockedReason.textContent = upstreamPause ? ""
        : submittingBatch ? "正在连接、确认或提交本次操作，请等待完成。"
        : busy ? "有账号操作正在进行；完成或取消后才能开始新一轮。"
        : selectedPaused ? `所选账号中有 ${selectedPaused} 个配额查询已暂停，请先“修复登录”，或取消选择这些账号后发送。`
        : selectedLoginRequired ? `所选账号中有 ${selectedLoginRequired} 个需要登录，请先「单独登录」，或取消勾选这些账号后发送。`
        : !count ? "请至少选择一个账号。" : "";
      blockedReason.hidden = !blockedReason.textContent;
    }
    if (egressPreview) egressPreview.disabled = submittingBatch;
    if (deviceConnect) deviceConnect.disabled = submittingBatch;
    batchRetry.hidden = true;
    if (!batch) {
      batchStatus.textContent = upstreamPause ? "本机 Codex 通道已暂停，暂不能查询或发送"
        : busy ? "账号操作进行中" : eligible.length ? "" : "尚未发现可操作的 Codex 账号。";
      batchHelp.replaceChildren(
        element("p", "账号来自当前及历史登录记录，无需手动添加。"),
        element("p", "发送时为每个账号从 100 条预设消息中随机抽取一条；发送前会检查登录与账号限制。"),
        element("p", "按邮箱顺序逐个完成账号操作，前一个结束并清理后才处理下一个。登录态不可用的账号会明确要求单独登录，完成后再发起操作。"),
        element("p", upstreamPause ? "本机 Codex 请求已暂停；请从按钮旁的恢复入口查看原因、当前可做的操作与检查后的接续。检查配额需要单独确认。" : "仅刷新配额不会发送消息；已确认的单账号凭据撤销会隔离该账号，其他独立账号继续。共享或原因未明的故障仍会停止后续查询。发送会实际使用额度，不保证固定或重置窗口。")
      );
      return;
    }
    const statuses = { succeeded: 0, unknown: 0, failed: 0, waiting: 0, queued: 0, running: 0, unsent: 0 };
    let resetRead = 0;
    for (const item of batch.items) {
      const account = accounts.find((value) => value.id === item.profile_id);
      const op = item.operation;
      if (op?.message_status === "succeeded") statuses.succeeded++;
      if (account?.busy && op?.stage === "login") statuses.waiting++;
      else if (account?.busy && op?.stage === "login_queued") statuses.queued++;
      else if (account?.busy && account.operation?.batch_id === batch.id) statuses.running++;
      else if (op?.message_status === "unknown") statuses.unknown++;
      else if (op?.message_status === "failed") statuses.failed++;
      else if (account?.eligible !== false && (!op || op.message_status === "not_sent")) statuses.unsent++;
      const refresh = account?.operation;
      const applicable = refresh?.refresh_only && Date.parse(refresh.started_at) >= Date.parse(batch.created_at) ? refresh : null;
      const reading = window.AgentMonitor.latestCodexQuotaReading({operation: applicable, batch_result: item.operation});
      if (typeof reading?.seven_day_resets_at === "number" && reading.seven_day_resets_at * 1000 > Date.now()) resetRead++;
    }
    const parts = [`本轮发送成功 ${statuses.succeeded}/${batch.items.length}`];
    for (const [key, label] of Object.entries({ waiting: "等待授权", queued: "授权排队", running: "处理中", unsent: "未发送", unknown: "结果未知", failed: "发送失败" })) {
      if (statuses[key]) parts.push(`${label} ${statuses[key]}`);
    }
    if (receipt) receipt.textContent = `${parts.join(" · ")} · 开始于 ${timestamp(batch.created_at)}`;
    const currentParts = [];
    if (upstreamPause) currentParts.push("本机 Codex 通道已暂停，暂不能查询或发送");
    if (submittingBatch) currentParts.push("正在连接、确认或提交发送");
    else if (busy) currentParts.push("账号操作进行中");
    for (const [key, label] of Object.entries({ waiting: "等待授权", queued: "授权排队", running: "处理中", unknown: "结果未知", failed: "发送失败" })) {
      if (statuses[key]) currentParts.push(`${label} ${statuses[key]}`);
    }
    if (statuses.unknown || statuses.failed) currentParts.push("可能已消耗额度，不会自动重试");
    batchStatus.textContent = currentParts.join(" · ");
    batchRetry.hidden = statuses.unsent === 0;
    batchRetry.disabled = submittingBatch || busy || Boolean(upstreamPause);
    batchRetry.textContent = `继续 ${statuses.unsent} 个未发送账号`;
    if (observation) observation.textContent = `本轮开始于 ${timestamp(batch.created_at)}。7 天窗口重置时间已知 ${resetRead}/${batch.items.length}；这是配额读数，不代表发送结果。`;
    const quotaUnsent = batch.items.flatMap(item => {
      const account = accounts.find(value => value.id === item.profile_id);
      return account && account.eligible !== false && (!item.operation || item.operation.message_status === "not_sent") && account.restrictions?.quota ? [account] : [];
    });
    const loginUnsent = batch.items.flatMap(item => {
      const account = accounts.find(value => value.id === item.profile_id);
      return account && account.eligible !== false && (!item.operation || item.operation.message_status === "not_sent") && accountNeedsLogin(account) ? [account] : [];
    });
    const pausedUnsent = batch.items.filter(item => accounts.some(account => account.id === item.profile_id
      && account.eligible !== false && account.account_quota_pause && (!item.operation || item.operation.message_status === "not_sent")));
    if (loginUnsent.length) batchRetry.disabled = true;
    if (blockedReason && loginUnsent.length && !submittingBatch && !busy && !upstreamPause) {
      blockedReason.textContent = `未发送账号中有 ${loginUnsent.length} 个登录态不可用，请先单独登录后再继续未发送账号。`;
      blockedReason.hidden = false;
    }
    if (pausedUnsent.length) batchRetry.disabled = true;
    if (blockedReason && pausedUnsent.length && !submittingBatch && !busy && !upstreamPause) {
      blockedReason.textContent = `未发送账号中有 ${pausedUnsent.length} 个配额查询已暂停，请先“修复登录”，再继续发送。`;
      blockedReason.hidden = false;
    }
    if (blockedReason && quotaUnsent.length && !submittingBatch && !busy && !upstreamPause) {
      const prerequisites = quotaUnsent.filter(account => accountRefreshBlocked(account)).length;
      const nextStep = prerequisites
        ? `其中 ${prerequisites} 个需先完成登录或处理其他限制，再「仅刷新配额」。${prerequisites < quotaUnsent.length ? "其余可在对应账号「仅刷新配额」。" : ""}`
        : "请先在对应账号「仅刷新配额」。";
      blockedReason.textContent += `未发送账号中有 ${quotaUnsent.length} 个受额度限制。${nextStep}只有服务端明确允许额度使用，才会解除额度限制；解除后再继续发送。`;
      blockedReason.hidden = false;
    }
    const help = [
      "发送时为每个账号从 100 条预设消息中随机抽取一条；发送前会检查登录与账号限制。",
      "顺序：按邮箱逐个完成账号操作，包含子进程清理；不会同时发送。登录态不可用时会要求单独登录，完成后需重新发起操作。",
      statuses.unsent
        ? `继续发送：先处理缺登录或暂停原因，再点击「${batchRetry.textContent}」。系统只处理仍在登录记录中且确定未发送的账号。`
        : "继续发送：本轮没有可以继续发送的账号。",
      "刷新：刷新页面或仅查询配额不会重发消息。发送前查询失败或服务端明确拒绝额度使用时，本次不发送。",
      ...(statuses.unknown || statuses.failed ? ["结果未知或发送失败的账号可能已消耗额度，系统不会自动重试。"] : []),
      ...(upstreamPause ? ["本机 Codex 请求已暂停，机器配额读取也受此保护。请从按钮旁的恢复入口查看当前可做的操作；检查配额需要单独确认，恢复不会自动发送消息。"] : []),
      `再次发送：选择账号后，点击「${batchNext.textContent}」。这会实际使用额度，不保证固定或重置窗口；暂停中的账号仍不会发送。`,
    ];
    batchHelp.replaceChildren(...help.map(text => element("p", text)));
  }
