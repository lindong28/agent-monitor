const STAGES = {
  checking: "检查登录", login: "等待官方授权", sending: "正在发送", reading: "读取配额",
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
  const form = root.querySelector("form");
  const email = root.querySelector("input");
  let expectedAccountId = null;
  let timer;
  let loading = false;
  let reloadPending = false;
  let accounts = [];
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
      render();
    } catch (error) {
      if (current() && error.name !== "AbortError") notice.textContent = error.message + " 可重新展开此面板刷新。";
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
    const node = element("button", label, "btn");
    node.type = "button";
    node.disabled = account.busy && action !== "cancel";
    node.addEventListener("click", () => perform(action, account.id, node));
    return node;
  }

  function render() {
    // Keep existing cards and authorization links in place while polling:
    // rebuilding a focused link every second interrupts keyboard/copy use.
    const existing = new Map(Array.from(list.children).map((node) => [node.dataset.id, node]));
    if (!accounts.length) {
      list.replaceChildren(element("p", "尚未添加账号。输入邮箱开始，或从配额表选择一个 Codex 账号。", "quota-scope"));
      return;
    }
    for (const account of accounts) {
      const signature = JSON.stringify(account);
      const old = existing.get(account.id);
      existing.delete(account.id);
      if (old?.dataset.signature === signature) continue;
      const card = element("article", undefined, "codex-account-card");
      card.dataset.id = account.id;
      card.dataset.signature = signature;
      const op = account.operation;
      const heading = element("div", undefined, "codex-account-heading");
      heading.append(element("strong", account.email), element("span", op ? STAGES[op.stage] || op.stage : "尚未操作", "status-pill"));
      card.append(heading);
      if (account.account_id) card.append(element("p", `工作区：${account.account_id}`, "quota-scope codex-workspace"));
      card.append(element("p", account.has_credentials ? "本页已保存登录态，将在操作时检查有效性。" : "需要官方授权。", "quota-scope"));
      if (op) {
        card.append(element("p", op.detail));
        card.append(element("p", `${op.refresh_only ? "本次仅查询配额，不发送消息" : "本次消息：" + MESSAGE_STATUS[op.message_status]} · 开始于 ${timestamp(op.started_at)}`, "quota-scope"));
        if (op.stage === "login" && op.verification_url && op.user_code) {
          const box = element("div", undefined, "codex-authorization");
          const link = element("a", "打开 OpenAI 官方授权页", "btn");
          link.href = op.verification_url;
          link.target = "_blank";
          link.rel = "noopener noreferrer";
          box.append(link, element("span", "设备码（不是邮箱验证码）："), element("code", op.user_code));
          card.append(box, element("p", `请确认官方页面登录的是 ${account.email}。授权成功后${op.refresh_only ? "查询配额" : "自动发送一条消息"}；最多等待 10 分钟。`, "quota-scope"));
        }
        if (op.after) {
          const reading = element("dl", undefined, "codex-account-reading");
          reading.append(element("dt", "下次七天窗口重置"), element("dd", timestamp(op.after.seven_day_resets_at, true)));
          const used = op.after.seven_day_used_pct;
          reading.append(element("dt", "七天窗口已用"), element("dd", used === null ? "服务端未返回" : `${used}%`));
          reading.append(element("dt", "查询时间"), element("dd", timestamp(op.after.observed_at)));
          if (op.before?.seven_day_resets_at != null) reading.append(element("dt", "发送前的重置时间"), element("dd", timestamp(op.before.seven_day_resets_at, true)));
          card.append(reading);
          if (op.after.seven_day_resets_at != null && op.after.seven_day_resets_at * 1000 <= Date.now()) card.append(element("p", "这份重置时间已过去，请仅刷新配额获取当前读数。", "quota-scope"));
        }
      }
      const controls = element("div", undefined, "codex-account-controls");
      controls.append(button(account.has_credentials ? "发送一条消息" : "登录并发送一条消息", "start", account), button("仅刷新配额", "refresh", account));
      if (account.busy) controls.append(button("取消操作", "cancel", account));
      else if (account.has_credentials) controls.append(button("清除本页登录态", "forget-login", account));
      card.append(controls);
      if (old) old.replaceWith(card); else list.append(card);
    }
    for (const node of existing.values()) node.remove();
  }

  email.addEventListener("input", () => { expectedAccountId = null; });
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const submit = form.querySelector("button");
    if (submit.disabled) return;
    submit.disabled = true;
    notice.textContent = "正在准备账号…";
    try {
      const account = await request("add", { email: email.value.trim(), account_id: expectedAccountId });
      await request("start", { id: account.id });
      if (!current()) return;
      notice.textContent = "";
      await load();
    } catch (error) {
      if (current() && error.name !== "AbortError") notice.textContent = error.message;
    } finally {
      if (current()) submit.disabled = false;
    }
  });
  function select(event) {
    const target = event.target.closest?.("[data-codex-email]");
    if (!target || !current()) return;
    root.open = true;
    email.value = target.dataset.codexEmail;
    expectedAccountId = target.dataset.codexAccountId || null;
    notice.textContent = `已选择 ${email.value}，点击「登录并发送一条消息」开始。`;
    root.scrollIntoView({ block: "nearest" });
    email.focus({ preventScroll: true });
  }
  function visibility() { if (!document.hidden) load(); }
  document.addEventListener("click", select);
  document.addEventListener("visibilitychange", visibility);
  root.addEventListener("toggle", () => { if (root.open) load(); });
  window.AgentMonitor.onPageCleanup(() => {
    clearTimeout(timer);
    controller.abort();
    document.removeEventListener("click", select);
    document.removeEventListener("visibilitychange", visibility);
  });
  load();
}
