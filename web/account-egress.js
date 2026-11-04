function node(tag, text, className) {
  const result = document.createElement(tag);
  if (text !== undefined) result.textContent = text;
  if (className) result.className = className;
  return result;
}

export function countryName(code) {
  if (!code) return "地区未知";
  if (code === "CN") return "中国（CN）";
  try { return `${new Intl.DisplayNames(["zh-CN"], { type: "region" }).of(code)}（${code}）`; }
  catch { return code; }
}

export async function previewEgress(request, targets, signal, payload = {}) {
  const sending = Array.isArray(targets) && ["batch-start", "batch-retry"].includes(payload.approval_action);
  const needsApproval = Boolean(payload.approval_action);
  const actionLabel = ({"batch-start":"发送消息","batch-retry":"继续发送未发送账号","refresh":"刷新配额","refresh-all":"刷新全部账号配额","refresh-selected":"查询所选账号配额","refresh-retry":"继续未完成的配额查询","login":"单独登录","quota-recovery":"检查并恢复配额","quota-settings":"启用所选账号的自动查询"})[payload.approval_action] || "发起上游操作";
  const operationPending = ({"quota-settings":"自动查询设置尚未改变","quota-recovery":"尚未发送配额检查请求", refresh:"尚未发送配额刷新请求", "refresh-all":"尚未发送配额刷新请求", "refresh-selected":"尚未发送配额刷新请求", "refresh-retry":"尚未发送配额刷新请求", login:"尚未发送登录请求"})[payload.approval_action] || "尚未发送账号消息";
  let receipt = null;
  const dialog = node("dialog", undefined, "account-egress-dialog");
  dialog.setAttribute("aria-labelledby", "account-egress-title");
  const title = node("h2", needsApproval ? `${actionLabel}前确认出口` : "查看账号请求出口");
  title.id = "account-egress-title";
  const status = node("p", `正在探测出口，${operationPending}…`, "quota-scope");
  status.setAttribute("role", "status");
  const results = node("div", undefined, "account-egress-results");
  const controls = node("div", undefined, "codex-account-controls");
  const cancel = node("button", sending ? "取消" : "关闭", "btn");
  cancel.type = "button";
  cancel.addEventListener("click", () => dialog.close());
  controls.append(cancel);
  const confirm = node("button", needsApproval ? `确认${actionLabel}` : "确认发送", "btn btn-primary");
  confirm.type = "button";
  confirm.disabled = true;
  if (needsApproval) controls.append(confirm);
  dialog.append(title, status, results,
    node("p", "分别探测 ChatGPT 业务与认证域名，不携带账号凭据。以下是探测时的出口；后续连接、其他目标或代理切换仍可能改变出口。", "quota-scope"));
  if (sending) {
    dialog.append(node("p", `将为以下 ${targets.length} 个账号各随机抽取并发送一条日常短消息（可能重复）：`));
    const identities = node("ul", undefined, "account-egress-identities");
    for (const account of targets) identities.append(node("li", `${account.email}（${account.account_id}）`));
    dialog.append(identities, node("p", "会使用账号额度；这是新一轮，之前已发送的账号也会再次发送。暂停中的账号不会发送；发送不保证固定或重置额度窗口。", "quota-scope"));
  } else if (needsApproval) {
    dialog.append(node("p", `确认后将${actionLabel}。预览只读取匿名出口信息，不携带账号凭据；取消或无法取得完整出口时不会继续。`, "quota-scope"));
    if (["refresh-all", "refresh-selected", "refresh-retry", "quota-settings"].includes(payload.approval_action)) {
      dialog.append(node("p", payload.approval_action === "quota-settings"
        ? `将持续为以下 ${targets.length} 个账号按页面所示周期查询配额，直到取消勾选。新发现或身份变化的账号不会自动加入；不会发送消息：`
        : `本次将逐个查询以下 ${targets.length} 个账号的配额，不发送消息：`));
      const identities = node("ul", undefined, "account-egress-identities");
      for (const account of targets) identities.append(node("li", `${account.email}（${account.account_id}）`));
      dialog.append(identities);
    }
    const machine = Array.isArray(payload.approval_scope) ? payload.approval_scope[0] : null;
    if (["quota-recovery", "quota-settings"].includes(payload.approval_action) && machine) {
      dialog.append(node("p", `本次检查将在 ${machine} 上执行，并使用该机器的 Agent Monitor 路由桥；当前浏览器不会直接访问上游。`, "quota-scope"));
    }
  }
  dialog.append(controls);
  document.body.append(dialog);
  const closed = new Promise(resolve => {
    const abort = () => dialog.close();
    signal.addEventListener("abort", abort, { once: true });
    dialog.addEventListener("close", () => {
      signal.removeEventListener("abort", abort);
      const accepted = dialog.returnValue === "send" && !signal.aborted;
      dialog.remove();
      resolve(accepted ? receipt : null);
    }, { once: true });
  });
  confirm.addEventListener("click", () => { if (!confirm.disabled) dialog.close("send"); });
  dialog.showModal();
  if (signal.aborted) dialog.close();
  // Do not await the probe before returning the close promise: Cancel/Escape
  // must remain immediate, and late results must not reopen a dismissed dialog.
  request("egress-preview", payload).then(data => {
    if (!dialog.open) return;
    if (!Array.isArray(data.targets) || !data.targets.length) throw new Error("未取得出口信息");
    if (needsApproval && !data.confirmation) throw new Error("服务未返回本轮出口确认，请重新预览");
    receipt = data.confirmation || null;
    status.textContent = `出站设备：${data.checked_host} · 探测不会登录、发送消息或读取额度。`;
    for (const target of data.targets) {
      const row = node("section", undefined, "account-egress-target");
      row.append(node("h3", target.host === "chatgpt.com" ? "ChatGPT 业务 · chatgpt.com" : "认证 · auth.openai.com"));
      row.append(node("p", `${target.ip || "IP 未知"} · ${countryName(target.country_code)}`,
        target.country_code === "CN" ? "account-egress-address is-china" : "account-egress-address"));
      if (target.country_code === "CN") row.append(node("p", "出口地区为中国，请确认后再发送。", "is-china"));
      if (target.error) row.append(node("p", target.error, "quota-scope"));
      const observed = new Date(target.observed_at * 1000);
      row.append(node("p", `观测于 ${Number.isFinite(observed.getTime()) ? observed.toLocaleString("zh-CN") : "未知时间"}${target.cached ? " · 缓存结果" : ""}；${target.retry_after_seconds} 秒后可重新探测。`, "quota-scope"));
      results.append(row);
    }
    const incomplete = data.targets.some(target => !target.ip || !target.country_code);
    confirm.textContent = incomplete ? `出口信息不完整，仍然${actionLabel}` : `确认${actionLabel}`;
    confirm.disabled = false;
  }).catch(error => {
    if (!dialog.open) return;
    const detail = String(error?.message || "出口预览失败").replace(/[。！？；：]+$/u, "");
    const prefix = ["quota-recovery", "quota-settings"].includes(payload.approval_action) ? "目标机器的 Agent Monitor 路由桥出口预览未完成" : "出口 IP 和地区未知";
    status.textContent = `${prefix}：${detail}；${operationPending}。`;
    confirm.textContent = "未能确认设备出口，请关闭后重试";
    confirm.disabled = true;
  });
  return closed;
}
