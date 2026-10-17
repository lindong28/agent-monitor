const STAGES = {
  queued: "排队中",
  checking: "检查登录", login_queued: "等待授权排队", login: "等待官方授权", sending: "正在发送", reading: "读取配额",
  succeeded: "已完成", partial: "消息完成 · 配额待确认", failed: "未完成",
  cancelled: "已取消", interrupted: "上次操作已中断",
};
const MESSAGE_STATUS = { not_sent: "未发送", unknown: "结果未知，可能已消耗额度", succeeded: "发送成功", failed: "发送失败，可能已消耗额度" };

function element(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
}

function timestamp(value, seconds = false) {
  if (value === null || value === undefined) return "服务端未返回";
  const date = new Date(seconds ? value * 1000 : value);
  return Number.isFinite(date.getTime()) ? window.AgentMonitor.formatDate(date.toISOString()) : "时间不可用";
}

export function init() {
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
  let timer;
  let loading = false;
  let reloadPending = false;
  let accounts = [];
  let batch = null;
  let unavailable = [];
  let submittingBatch = false;
  let selectedIds = null;
  let lastBatchStatus = "";
  const controller = new AbortController();

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

  async function load() {
    if (!current()) return;
    if (loading) { reloadPending = true; return; }
    if (document.hidden) { schedule(); return; }
    loading = true;
    try {
      const [data] = await Promise.all([request(), window.AgentMonitor.ensureTimezone()]);
      if (!current()) return;
      accounts = data.accounts;
      if (selectedIds === null) selectedIds = new Set(accounts.filter(a => a.eligible !== false).map(a => a.id));
      else selectedIds = new Set([...selectedIds].filter(id => accounts.some(a => a.id === id && a.eligible !== false)));
      batch = data.batch;
      unavailable = data.unavailable_accounts || [];
      window.AgentMonitor.updateCodexQuotaReadings?.(accounts);
      notice.textContent = "";
      render();
      lastBatchStatus = batchStatus.textContent;
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
    button.disabled = true;
    notice.textContent = action === "forget-login" ? "正在清除本页保存的登录态…" : "正在提交操作…";
    try {
      await request(action, { id });
      if (!current()) return;
      notice.textContent = action === "cancel" ? "正在取消；已发出的消息无法撤回。" : "";
      await load();
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

  async function performBatch(action, ids) {
    if (submittingBatch) return;
    // Confirmation and submission must refer to the same members and CAS.
    const payload = action === "batch-start"
      ? { expected_batch_id: batch?.id ?? null, profile_ids: ids || [...selectedIds] }
      : { batch_id: batch.id };
    if (action === "batch-start") {
      const targets = accounts.filter(a => payload.profile_ids.includes(a.id));
      if (!targets.length) return;
      const identities = targets.map(a => `${a.email}（${a.account_id}）`).join("\n");
      if (!window.confirm(`开始新一轮，向以下 ${targets.length} 个账号各发送一条 “Please reply with OK.”？\n\n${identities}\n\n会使用账号额度；这不是继续未发送账号，之前已发送的账号也会再次发送。`)) return;
    }
    submittingBatch = true;
    render();
    notice.textContent = "正在提交批次…";
    let failure = "";
    try {
      await request(action, payload);
      if (current()) notice.textContent = "";
    } catch (error) {
      if (current() && error.name !== "AbortError") failure = error.message + " 刷新会保留已接收的批次，请先查看结果。";
    } finally {
      submittingBatch = false;
      if (current()) await load();
      if (current() && failure) notice.textContent = failure;
    }
  }

  function renderBatch() {
    const busy = accounts.some((account) => account.busy);
    const eligible = accounts.filter((account) => account.eligible !== false);
    const count = eligible.filter(account => selectedIds?.has(account.id)).length;
    const selectionCount = root.querySelector("#codex-selection-count");
    if (selectionCount) selectionCount.textContent = `已选 ${count} / ${eligible.length} 个账号`;
    batchStart.hidden = Boolean(batch);
    batchStart.textContent = `给所选 ${count} 个账号发送`;
    batchStart.disabled = submittingBatch || busy || !count;
    batchNext.hidden = !batch;
    batchNext.textContent = `开始新一轮 · 所选 ${count} 个账号`;
    batchNext.disabled = submittingBatch || busy || !count;
    batchRetry.hidden = true;
    if (!batch) {
      batchStatus.textContent = eligible.length ? `${eligible.length} 个可操作账号；发送前会检查登录状态。` : "尚未发现可发送的 Codex 登录账号。";
      batchHelp.replaceChildren(
        element("p", "账号来自当前及历史登录记录，无需手动添加。"),
        element("p", "系统会检查并复用已保存的登录状态。需要授权时，系统逐个显示账号的设备码；完成或取消后，再处理下一个账号。")
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
    batchStatus.textContent = parts.join(" · ");
    batchRetry.hidden = statuses.unsent === 0;
    batchRetry.disabled = submittingBatch;
    batchRetry.textContent = `继续 ${statuses.unsent} 个未发送账号`;
    const help = [
      `本轮开始于 ${timestamp(batch.created_at)}。`,
      "授权：系统会检查并复用已保存的登录状态。需要授权时，系统逐个显示账号的设备码。遇到登录限流时，系统停止后续授权。",
      statuses.unsent
        ? `继续发送：如果遇到登录限流，先等限流解除。如需继续，再点击「${batchRetry.textContent}」。系统只处理仍在登录记录中且确定未发送的账号。`
        : "继续发送：本轮没有可以继续发送的账号。",
      "刷新：刷新页面或仅查询配额不会重发消息。",
      ...(statuses.unknown || statuses.failed ? ["结果未知或发送失败的账号可能已消耗额度，系统不会自动重试。"] : []),
      `再次发送：选择账号后，点击「${batchNext.textContent}」。系统不会自动判断配额是否已重置；发送前请核对所选账号。`,
    ];
    batchHelp.replaceChildren(...help.map(text => element("p", text)));
  }

  batchStart.addEventListener("click", () => performBatch("batch-start"));
  batchNext.addEventListener("click", () => performBatch("batch-start"));
  batchRetry.addEventListener("click", () => performBatch("batch-retry"));
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
      const latestReading = window.AgentMonitor.latestCodexQuotaReading?.(account) || account.operation?.after;
      const resetLabel = window.AgentMonitor.formatQuotaReset?.(latestReading?.seven_day_resets_at);
      const signature = JSON.stringify([account, batchItem, resetLabel, submittingBatch, accounts.some(a => a.busy)]);
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
      const heading = element("div", undefined, "codex-account-heading");
      const tone = op?.stage === "succeeded" ? "ok"
        : ["failed", "interrupted"].includes(op?.stage) ? "bad"
        : ["partial", "login", "login_queued"].includes(op?.stage) ? "warn"
        : account.busy ? "info" : "identity";
      const operationLabel = op?.stage === "succeeded"
        ? (op.refresh_only ? "最近查询完成" : "最近发送完成")
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
        label.append(checkbox, document.createTextNode("选择"));
        heading.prepend(label);
      }
      card.append(heading);
      const details = element("details", undefined, "note-disclosure");
      details.append(element("summary", "账号与操作详情"));
      if (account.eligible === false) card.append(element("p", "已不在当前登录记录中；保留本轮结果，不纳入下一轮或继续发送。", "quota-scope"));
      const duplicateEmail = accounts.some((other) => other.id !== account.id && other.email.toLowerCase() === account.email.toLowerCase());
      if (account.account_id) (duplicateEmail ? card : details).append(element("p", `工作区：${account.account_id}`, "quota-scope codex-workspace"));
      details.append(element("p", account.has_credentials ? "登录态：已保存，使用前仍需检查" : "登录态：需要官方授权", "quota-scope"));
      if (batchItem) heading.append(element("span", `本批：${MESSAGE_STATUS[batchItem.operation?.message_status || "not_sent"]}`, "codex-batch-result"));
      const retained = account.batch_result;
      if (retained && retained.batch_id !== batch?.id) details.append(element("p", `保留的批次结果：${MESSAGE_STATUS[retained.message_status] || "状态未知"} · 开始于 ${timestamp(retained.started_at)}`, "quota-scope"));
      if (op) {
        if (op.stage !== "succeeded") card.append(element("p", op.detail));
        details.append(element("p", `${op.refresh_only ? "本次仅查询配额，不发送消息" : "本次消息：" + MESSAGE_STATUS[op.message_status]} · 开始于 ${timestamp(op.started_at)}`, "quota-scope"));
        if (op.stage === "login" && op.verification_url && op.user_code) {
          const box = element("div", undefined, "codex-authorization");
          const link = element("a", "打开 OpenAI 官方授权页", "btn");
          link.href = op.verification_url;
          link.target = "_blank";
          link.rel = "noopener noreferrer";
          box.append(link, element("span", "设备码（不是邮箱验证码）："), element("code", op.user_code));
          card.append(box, element("p", `请确认官方页面登录的是 ${account.email}。授权成功后${op.refresh_only ? "查询配额" : "自动发送一条消息"}；最多等待 10 分钟。`, "quota-scope"));
        }
        if (latestReading) {
          const reading = element("dl", undefined, "codex-account-reading");
          reading.append(element("dt", "7 天配额窗口重置"), element("dd", `${resetLabel || "重置时间未知"} · ${timestamp(latestReading.seven_day_resets_at, true)}`));
          const used = latestReading.seven_day_used_pct;
          reading.append(element("dt", "7 天窗口已用"), element("dd", used == null ? "服务端未返回" : `${used}%`));
          reading.append(element("dt", "最近成功查询"), element("dd", timestamp(latestReading.observed_at)));
          if (op.before?.seven_day_resets_at != null) details.append(element("p", `${op.refresh_only ? "查询前" : "发送前"}的重置时间：${timestamp(op.before.seven_day_resets_at, true)}`, "quota-scope"));
          details.append(reading);
          if (latestReading.seven_day_resets_at != null && latestReading.seven_day_resets_at * 1000 <= Date.now()) details.append(element("p", "这份重置时间已过去，请仅刷新配额获取当前读数。", "quota-scope"));
        }
      }
      const controls = element("div", undefined, "codex-account-controls");
      if (account.eligible !== false) {
        const send = element("button", "向此账号发送一条消息", "btn");
        send.type = "button";
        send.disabled = submittingBatch || accounts.some(a => a.busy);
        send.addEventListener("click", () => performBatch("batch-start", [account.id]));
        controls.append(send);
      }
      controls.append(button("仅刷新配额", "refresh", account));
      if (account.busy) controls.append(button("取消操作", "cancel", account));
      else if (account.has_credentials) {
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
  });
  load();
  window.AgentMonitor.pageInterval(load, 30000);
}
