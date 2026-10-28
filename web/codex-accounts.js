async function previewEgress(...args) {
  const version = document.querySelector('meta[name="asset-version"]')?.content;
  const preview = await import("./account-egress.js" + (version ? `?v=${version}` : ""));
  return preview.previewEgress(...args);
}

const STAGES = {
  queued: "排队中",
  checking: "检查登录", login_queued: "等待授权排队", login: "等待官方授权", sending: "正在发送", reading: "读取配额",
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

function element(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
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
  let upstreamPause = null;
  let unavailable = [];
  let selectedIds = null;
  let lastBatchStatus = "";
  const controller = new AbortController();
  let deviceConnection = null;
  const deviceStatus = root.querySelector("#codex-device-status");
  const deviceConnect = root.querySelector("#codex-device-connect");
  const deviceCancel = root.querySelector("#codex-device-cancel");
  const deviceHelp = root.querySelector("#codex-device-help");
  const deviceCommand = root.querySelector("#codex-device-command");
  if (deviceCommand) deviceCommand.textContent = `agent-monitor connect install --server '${location.origin}'`;
  let pairing = null;
  function deviceFeedback(message, failed = false) {
    if (!current()) return;
    if (deviceStatus) deviceStatus.textContent = message;
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
      if (selectedIds === null) selectedIds = new Set(accounts.filter(a => a.eligible !== false).map(a => a.id));
      else selectedIds = new Set([...selectedIds].filter(id => accounts.some(a => a.id === id && a.eligible !== false)));
      batch = data.batch;
      upstreamPause = data.upstream_pause || null;
      unavailable = data.unavailable_accounts || [];
      window.AgentMonitor.updateCodexQuotaReadings?.(accounts);
      notice.textContent = "";
      render();
      lastBatchStatus = batchStatus.textContent;
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
        notice.textContent = error.message + " 可重新展开此面板刷新。";
        batchStatus.textContent = lastBatchStatus
          ? lastBatchStatus + " · 刷新失败，以上为上次结果；重新展开可重试。"
          : "账号读取失败；重新展开此面板可重试。";
      }
    } finally {
      loading = false;
      if (reloadPending) { reloadPending = false; load(); }
      else schedule();
    }
  }

  async function perform(action, id, button) {
    if (action === "resume" && !window.confirm("我已核对此账号的原始失败、账号状态及服务端要求，确认解除此账号的本地限制。\n\n这不证明上游已恢复；不会自动登录、查询或发送。额度限制和本机 Codex 通道暂停仍保留。")) return;
    if (action === "release-rate-limit" && !window.confirm("仅解除此账号在本页的限流暂停，不会发送消息或查询配额。\n\n这不证明服务端限流已解除；请按服务端指引等待后再尝试。认证和额度限制仍会保留。")) return;
    button.disabled = true;
    notice.textContent = action === "forget-login" ? "正在清除本页保存的登录态…" : "正在提交操作…";
    try {
      const account = accounts.find(value => value.id === id);
      await request(action, { id, ...(action === "resume" ? {operation_id: account?.operation?.id} : {}) });
      if (!current()) return;
      notice.textContent = action === "cancel" ? "正在取消；已发出的消息无法撤回。" : "";
      await load();
      if (current() && action === "resume") notice.textContent = "此账号的本地限制已解除，未发出上游请求。本机通道暂停和原始失败记录保留；后续操作需重新点击。";
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
    if (current()) renderAccountPause(upstreamPause, window.AgentMonitor.codexPauseTarget?.(upstreamPause?.id), pauseControls);
  }
  pauseControls.pauseRecovery?.addEventListener("click", () => {
    if (!window.AgentMonitor.showCodexPauseRecovery?.(upstreamPause?.id)) {
      renderPause();
      notice.textContent = "未能定位此暂停的恢复记录；请刷新页面重新读取，暂停仍保留。";
    }
  });
  document.addEventListener("agent-monitor:quota-rendered", renderPause);

  batchStart.addEventListener("click", () => actions.performBatch("batch-start"));
  batchNext.addEventListener("click", () => actions.performBatch("batch-start"));
  batchRetry.addEventListener("click", () => actions.performBatch("batch-retry"));
  egressPreview?.addEventListener("click", actions.preview);
  root.querySelector("#codex-select-all").addEventListener("click", () => {
    selectedIds = new Set(accounts.filter(a => a.eligible !== false).map(a => a.id));
    render();
  });
  root.querySelector("#codex-select-none").addEventListener("click", () => {
    selectedIds.clear();
    render();
  });

  function render() {
    renderBatch();
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
      const latestReading = operationReading(account);
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
      const sharedSuccess = sameSuccessfulBatchOperation(op, batchItem?.operation);
      const heading = element("div", undefined, "codex-account-heading");
      const tone = op?.stage === "succeeded" ? "ok"
        : ["failed", "interrupted"].includes(op?.stage) ? "bad"
        : ["partial", "login", "login_queued"].includes(op?.stage) ? "warn"
        : account.busy ? "info" : "identity";
      const operationLabel = sharedSuccess ? "本批发送成功" : op?.stage === "succeeded"
        ? (op.login_only ? "最近登录完成" : op.refresh_only ? "最近查询完成" : "最近发送完成")
        : op ? STAGES[op.stage] || op.stage : "尚未操作";
      heading.append(element("strong", account.email), element("span", operationLabel, "status-pill " + tone));
      if (account.eligible !== false) {
        const label = element("label", undefined, "codex-select-account");
        const checkbox = element("input");
        checkbox.type = "checkbox";
        checkbox.checked = selectedIds.has(account.id);
        checkbox.setAttribute("aria-label", `选择 ${account.email}（${account.account_id}）`);
        checkbox.addEventListener("change", () => {
          if (checkbox.checked) selectedIds.add(account.id); else selectedIds.delete(account.id);
          renderBatch();
        });
        label.append(checkbox);
        heading.prepend(label);
      }
      card.append(heading);
      for (const detail of account.restriction_details || []) card.append(element("p", detail, "quota-scope"));
      const details = element("details", undefined, "note-disclosure");
      details.append(element("summary", "账号与操作详情"));
      if (account.eligible === false) card.append(element("p", "已不在当前登录记录中；保留本轮结果，不纳入下一轮或继续发送。", "quota-scope"));
      const duplicateEmail = accounts.some((other) => other.id !== account.id && other.email.toLowerCase() === account.email.toLowerCase());
      if (account.account_id) (duplicateEmail ? card : details).append(element("p", `工作区：${account.account_id}`, "quota-scope codex-workspace"));
      details.append(element("p", account.has_credentials ? "登录态：已保存，使用前仍需检查" : "登录态：需要官方授权", "quota-scope"));
      if (batchItem && !sharedSuccess) heading.append(element("span", `本批：${MESSAGE_STATUS[batchItem.operation?.message_status || "not_sent"]}`, "codex-batch-result"));
      const retained = account.batch_result;
      if (retained && retained.batch_id !== batch?.id) details.append(element("p", `保留的批次结果：${MESSAGE_STATUS[retained.message_status] || "状态未知"} · 开始于 ${timestamp(retained.started_at)}`, "quota-scope"));
      const shownMessageIds = new Set([op?.id]);
      for (const batchMessage of [retained, batchItem?.operation]) {
        if (batchMessage?.message && !shownMessageIds.has(batchMessage.id)) {
          details.append(element("p", `保留的批次消息：${batchMessage.message}`, "quota-scope"));
          shownMessageIds.add(batchMessage.id);
        }
      }
      if (op) {
        if (op.stage !== "succeeded") card.append(element("p", op.detail));
        details.append(element("p", `${op.refresh_only ? "本次仅查询配额，不发送消息" : "本次消息：" + MESSAGE_STATUS[op.message_status]} · 开始于 ${timestamp(op.started_at)}`, "quota-scope"));
        if (op.message) details.append(element("p", `本次抽取的消息：${op.message}`, "quota-scope"));
        if (op.failure) details.append(element("p", `停止阶段：${STAGES[op.failure.stage] || op.failure.stage} · 错误类别：${op.failure.category}${op.failure.http_status ? " · HTTP " + op.failure.http_status : ""}`, "quota-scope"));
        const diag = op.diagnostics;
        if (diag?.last_request) details.append(element("p", `最后请求：${diag.last_request} · ${timestamp(diag.last_request_at)}`, "quota-scope"));
        if (diag?.last_event) details.append(element("p", `最后进度：${diag.last_event} · ${timestamp(diag.last_event_at)}`, "quota-scope"));
        if (diag?.thread_id) details.append(element("p", `Thread：${diag.thread_id}${diag.turn_id ? " · Turn：" + diag.turn_id : ""}`, "quota-scope"));
        if (diag?.interrupt) details.append(element("p", `超时中止：${diag.interrupt === "acknowledged" ? "CLI 已确认" : "未取得确认"}；不代表消息未消耗额度。`, "quota-scope"));
        if (op.stage === "login" && op.verification_url && op.user_code) {
          const box = element("div", undefined, "codex-authorization");
          const link = element("a", "打开 OpenAI 官方授权页", "btn");
          link.href = op.verification_url;
          link.target = "_blank";
          link.rel = "noopener noreferrer";
          box.append(link, element("span", "设备码（不是邮箱验证码）："), element("code", op.user_code));
          card.append(box, element("p", `请确认官方页面登录的是 ${account.email}。授权成功后仅保存登录态，不自动发送或查询；最多等待 10 分钟。`, "quota-scope"));
        }
        if (latestReading) {
          const reading = element("dl", undefined, "codex-account-reading");
          reading.append(element("dt", "7 天配额窗口重置"), element("dd", `${resetLabel || "重置时间未知"} · ${timestamp(latestReading.seven_day_resets_at, true)}`));
          const used = latestReading.seven_day_used_pct;
          reading.append(element("dt", "7 天窗口已用"), element("dd", used == null ? "服务端未返回" : `${used}%`));
          reading.append(element("dt", "本次操作的最近观测"), element("dd", timestamp(latestReading.observed_at)));
          if (op.before?.seven_day_resets_at != null) details.append(element("p", `${op.refresh_only ? "查询前" : "发送前"}的重置时间：${timestamp(op.before.seven_day_resets_at, true)}`, "quota-scope"));
          details.append(reading);
          if (latestReading.seven_day_resets_at != null && latestReading.seven_day_resets_at * 1000 <= Date.now()) details.append(element("p", "这份重置时间已过去，请仅刷新配额获取当前读数。", "quota-scope"));
        }
      }
      const controls = element("div", undefined, "codex-account-controls");
      if (account.eligible !== false) {
        const send = element("button", "向此账号发送一条消息", "btn");
        send.type = "button";
        send.disabled = actions.busy || accounts.some(a => a.busy) || Boolean(upstreamPause) || !account.has_credentials || Object.keys(account.restrictions || {}).length > 0;
        send.addEventListener("click", () => actions.performBatch("batch-start", [account.id]));
        controls.append(send);
      }
      const refresh = button("仅刷新配额", "refresh", account);
      const restrictions = account.restrictions || {};
      refresh.disabled ||= !account.has_credentials || Boolean(upstreamPause || restrictions.authentication || restrictions.unknown || restrictions.rate_limit || restrictions.login_rate_limit);
      controls.append(refresh);
      if (!account.has_credentials) {
        const login = button("单独登录", "login", account);
        login.disabled ||= Boolean(upstreamPause || restrictions.authentication || restrictions.unknown || restrictions.rate_limit || restrictions.login_rate_limit);
        controls.append(login);
      }
      if (restrictions.authentication || restrictions.unknown || restrictions.rate_limit || restrictions.login_rate_limit) controls.append(button("核对后解除此账号限制", "resume", account));
      if (account.last_failure && account.last_failure.id !== op?.id) details.append(element("p", `保留的原始失败：${account.last_failure.detail}`, "quota-scope"));
      if (account.busy) controls.append(button("取消操作", "cancel", account));
      else if (account.has_credentials || restrictions.authentication) {
        const maintenance = element("div", undefined, "codex-account-controls");
        maintenance.append(button("清除本页登录态", "forget-login", account));
        details.append(maintenance);
      }
      details.open = Boolean(old?.querySelector("details")?.open);
      const footer = element("div", undefined, "codex-account-footer");
      footer.append(controls, details);
      card.append(footer);
      if (old) old.replaceWith(card); else list.append(card);
      if (list.children[index] !== card) list.insertBefore(card, list.children[index] || null);
    }
    for (const node of existing.values()) node.remove();
  }

  function visibility() { if (!document.hidden) load(); }
  document.addEventListener("visibilitychange", visibility);
  root.addEventListener("toggle", () => { if (root.open) load(); });
  window.AgentMonitor.onPageCleanup(() => {
    clearTimeout(timer);
    controller.abort();
    document.removeEventListener("visibilitychange", visibility);
    document.removeEventListener("agent-monitor:quota-rendered", renderPause);
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
    const payload = action === "batch-start"
      ? { expected_batch_id: getBatch()?.id ?? null, profile_ids: ids || [...getSelectedIds()] }
      : { batch_id: getBatch().id };
    const targets = action === "batch-start" ? getAccounts().filter(a => payload.profile_ids.includes(a.id)) : null;
    if (targets && !targets.length) return;
    submittingBatch = true;
    render();
    let failure = "";
    let submitted = false;
    try {
      payload.connection = await requireDevice();
      if (targets) {
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
  return { performBatch, preview, connect, get busy() { return submittingBatch; } };
}

export function operationReading(account) {
  return window.AgentMonitor.latestCodexQuotaReading?.({operation: account.operation}) || account.operation?.after;
}

export function renderAccountPause(pause, target, controls) {
  const { pausePanel, pauseSummary, pauseDetail, pauseRecovery } = controls;
  if (!pausePanel) return;
  pausePanel.hidden = !pause;
  if (!pause) return;
  const matched = target?.pause?.id === pause.id && target.machine && target.account_id && target.account_label;
  pauseSummary.textContent = `${matched ? target.machine : "服务端本机"} 的 Codex 通道已暂停，阻止本页所有账号发送、登录和查询配额。`;
  const time = pause.at ? timestamp(pause.at, true) : "未记录";
  const reason = pause.failure?.detail || (matched && target.failure?.detail) || "未记录原始失败原因，不能判断上游是否恢复。";
  pauseDetail.textContent = `暂停时间：${time}。原始失败：${reason}`;
  if (matched) pauseDetail.textContent += ` 恢复查询账号：${target.account_label}。`;
  else pauseDetail.textContent += pause.id === "unreadable"
    ? " 暂停状态无法读取，请在服务端修复状态文件；不能直接解除。"
    : " 尚未匹配到此暂停的完整机器与账号记录；请刷新页面重新读取。暂停仍保留，不会猜测恢复目标。";
  pauseRecovery.disabled = !matched || pause.id === "unreadable";
}

export function renderAccountBatch(state, controls) {
  const { accounts, batch, selectedIds, unavailable, busy: submittingBatch, upstreamPause } = state;
  const { root, batchStart, batchNext, batchRetry, batchStatus, batchHelp, egressPreview, deviceConnect, blockedReason } = controls;
    const busy = accounts.some((account) => account.busy);
    const eligible = accounts.filter((account) => account.eligible !== false);
    const count = eligible.filter(account => selectedIds?.has(account.id)).length;
    const selectionCount = root.querySelector("#codex-selection-count");
    if (selectionCount) selectionCount.textContent = `已选 ${count} / ${eligible.length} 个账号`;
    batchStart.hidden = Boolean(batch);
    batchStart.textContent = `给所选 ${count} 个账号发送`;
    batchStart.disabled = submittingBatch || busy || !count || Boolean(upstreamPause);
    batchNext.hidden = !batch;
    batchNext.textContent = `开始新一轮 · 所选 ${count} 个账号`;
    batchNext.disabled = submittingBatch || busy || !count || Boolean(upstreamPause);
    if (blockedReason) {
      blockedReason.textContent = upstreamPause ? "发送已暂停：请先查看下方暂停原因与恢复入口。"
        : submittingBatch ? "正在连接、确认或提交本次操作，请等待完成。"
        : busy ? "有账号操作正在进行；完成或取消后才能开始新一轮。"
        : !count ? "请至少选择一个账号。" : "";
      blockedReason.hidden = !blockedReason.textContent;
    }
    if (egressPreview) egressPreview.disabled = submittingBatch;
    if (deviceConnect) deviceConnect.disabled = submittingBatch;
    batchRetry.hidden = true;
    if (!batch) {
      batchStatus.textContent = eligible.length ? `${eligible.length} 个可操作账号；发送前会检查登录状态。` : "尚未发现可发送的 Codex 登录账号。";
      if (upstreamPause) batchStatus.textContent += " · 本机 Codex 通道已暂停，暂不能发送";
      batchHelp.replaceChildren(
        element("p", "账号来自当前及历史登录记录，无需手动添加。"),
        element("p", "按邮箱顺序逐个完成账号操作，前一个结束并清理后才处理下一个。缺登录态的账号跳过，请单独登录后再发起操作。"),
        element("p", upstreamPause ? "本机 Codex 请求已暂停；请从按钮旁的恢复入口核对原始失败并批准一次配额探测。" : "仅刷新配额不会发送消息。发送会实际使用额度，不保证固定或重置窗口；异常会停止后续账号。")
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
      const applicableRefresh = refresh?.refresh_only && Date.parse(refresh.started_at) >= Date.parse(batch.created_at) ? refresh : null;
      const reading = window.AgentMonitor.latestCodexQuotaReading({operation: applicableRefresh, batch_result: op});
      if (typeof reading?.seven_day_resets_at === "number" && reading.seven_day_resets_at * 1000 > Date.now()) resetRead++;
    }
    const parts = [`${timestamp(batch.created_at)} 开始的批次`, `本轮发送成功 ${statuses.succeeded}/${batch.items.length}`];
    for (const [key, label] of Object.entries({ waiting: "等待授权", queued: "授权排队", running: "处理中", unsent: "未发送", unknown: "结果未知", failed: "发送失败" })) {
      if (statuses[key]) parts.push(`${label} ${statuses[key]}`);
    }
    parts.push(`下次重置时间已知 ${resetRead}/${batch.items.length}`);
    if (upstreamPause) parts.push("本机 Codex 通道已暂停，暂不能发送");
    batchStatus.textContent = parts.join(" · ");
    batchRetry.hidden = statuses.unsent === 0;
    batchRetry.disabled = submittingBatch || busy || Boolean(upstreamPause);
    batchRetry.textContent = `继续 ${statuses.unsent} 个未发送账号`;
    const help = [
      `本轮开始于 ${timestamp(batch.created_at)}。`,
      "顺序：按邮箱逐个完成账号操作，包含子进程清理；不会同时发送。缺登录态时跳过，单独登录完成后需重新发起操作。",
      statuses.unsent
        ? `继续发送：先处理缺登录或暂停原因，再点击「${batchRetry.textContent}」。系统只处理仍在登录记录中且确定未发送的账号。`
        : "继续发送：本轮没有可以继续发送的账号。",
      "刷新：刷新页面或仅查询配额不会重发消息。发送前查询失败或服务端明确拒绝额度使用时，本次不发送。",
      ...(statuses.unknown || statuses.failed ? ["结果未知或发送失败的账号可能已消耗额度，系统不会自动重试。"] : []),
      ...(upstreamPause ? ["本机 Codex 请求已暂停，机器配额读取也受此保护。请从按钮旁的恢复入口核对并批准一次配额探测；恢复不会自动发送消息。"] : []),
      `再次发送：选择账号后，点击「${batchNext.textContent}」。这会实际使用额度，不保证固定或重置窗口；暂停中的账号仍不会发送。`,
    ];
    batchHelp.replaceChildren(...help.map(text => element("p", text)));
  }
