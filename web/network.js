// Network view responsibilities. The shell supplies navigation and page lifetime.
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory;
  else (root.AgentMonitorModules ||= {}).network = factory;
})(typeof window === "undefined" ? globalThis : window, function createNetwork(dependencies) {
  const { platform, qs, updateNavLinks, api, withRefresh, formatDate, escapeHtml, onPageCleanup } = dependencies;
  const { window, document } = platform;
  async function initNetwork() {
    let active = true;
    onPageCleanup?.(() => { active = false; });
    async function load(force) {
      try {
        const data = await api("/api/network", force ? { force: "1" } : {});
        if (!active) return;
        renderNetwork(data, () => load(true));
      } catch (error) {
        if (!active) return;
        renderNetwork({
          error: error && error.message ? error.message : String(error),
          verdict: "unknown",
        }, () => load(true));
      }
    }
    const refresh = qs("#refresh");
    if (refresh) {
      refresh.addEventListener("click", () => withRefresh(refresh, () => load(true)));
    }
    updateNavLinks();
    if (qs("#process-network")) {
      initProcessNetwork();
      const server = qs("#server-network");
      server?.addEventListener("toggle", () => {
        if (server.open && !server.dataset.loaded) {
          server.dataset.loaded = "1";
          load(false);
        }
      });
    } else {
      await load(false);
    }
  }

  function initProcessNetwork() {
    const button = qs("#process-network-refresh"), status = qs("#process-network-status");
    const select = qs("#process-network-select"), result = qs("#process-network-result");
    const help = qs("#process-network-help"), cancel = qs("#process-network-cancel");
    const probeButton = qs("#process-network-probe"), probeHelp = qs("#process-network-probe-help");
    qs("#process-network-command").textContent = `agent-monitor connect --server '${window.location.origin.replaceAll("'", "'\\''")}'`;
    let report = null, pending = null;
    onPageCleanup?.(() => pending?.finish("页面已离开，本次读取已取消。"));
    function render() {
      const item = report?.processes?.[Number(select.value)];
      if (!item) { result.textContent = ""; return; }
      probeButton.hidden = !item.instance;
      probeHelp.hidden = !item.instance;
      probeHelp.textContent = `可主动通过该进程当前连接的代理，向固定目标 https://${item.agent === "codex" ? "chatgpt.com" : "api.anthropic.com"}/cdn-cgi/trace 发出一次匿名诊断请求。无账号认证、无重试；目标不一定是此进程实际使用的服务。绕过规则、配置冲突或连接无法核实时不发送。`;
      const errors = (item.errors || []).map(e => `${e.stage}: ${e.type}`).join("；");
      result.innerHTML = [
        kvRow("进程实例", escapeHtml(item.instance || "未取得；进程可能已退出或无权读取")),
        kvRow("公网出口 IP / 地区", "未核实；当前 TCP 对端可能只是代理或服务器，不能当作公网出口。"),
        item.probe ? '<h3>固定目标探测回执</h3>' + [
          kvRow("探测目标 / 代理", `${escapeHtml(item.probe.host || "未发出")} / ${escapeHtml(item.probe.proxy_address || "未确认")}`),
          kvRow("此次探测 IP / 地区", `${escapeHtml(item.probe.ip || "未取得")} / ${escapeHtml(item.probe.country_code || "未取得")}`),
          kvRow("观测 / 缓存", `${escapeHtml(item.probe.observed_at ? formatDate(new Date(item.probe.observed_at * 1000).toISOString()) : "未记录")} · ${item.probe.cached ? "复用回执" : "本次结果"}`),
          `<p class="scope-note">此结果仅属于上述目标的匿名探测，不代表 Agent 已发生的请求。${item.probe.process_still_matches === false ? "探测结束时进程或连接已改变。" : ""}${item.probe.retry_after_seconds ? ` 最早 ${Number(item.probe.retry_after_seconds)} 秒后可重新探测；此前点击只读取缓存。` : ""}</p>`,
          item.probe.error ? `<p class="error">${escapeHtml(item.probe.error)}</p>` : "",
        ].join("") : "",
        '<h3>当前 TCP 连接</h3>',
        item.connections_status === "observed"
          ? (item.connections?.length ? item.connections.map(c => kvRow(escapeHtml(c.state), `${escapeHtml(c.local)} → ${escapeHtml(c.peer)}`)).join("")
            : '<p>本次未观察到已建立的 TCP 连接；不代表进程没有联网，也不代表直连。</p>')
          : '<p>连接信息未取得。</p>',
        '<h3>启动网络配置</h3>',
        '<p class="scope-note">来自该进程的启动环境；不读取账号或配置文件。客户端配置覆盖、运行中变更、DNS 和 UDP 路径未核实。未设置环境代理不代表没有系统代理、VPN 或透明代理。</p>',
        ...Object.entries(item.environment || {}).map(([key, value]) => kvRow(key,
          escapeHtml(value.address ?? value.value ?? "未取得/格式无法展示") + (value.credentials_present ? "（认证信息已隐藏）" : ""))),
        !Object.keys(item.environment || {}).length ? `<p>${item.environment_status === "startup_snapshot" ? "启动环境中未发现支持的网络字段。" : "启动环境未取得或为空。"}</p>` : "",
        errors ? `<p class="error">部分采集失败：${escapeHtml(errors)}</p>` : "",
      ].join("");
    }
    select.addEventListener("change", render);
    cancel.addEventListener("click", () => pending?.finish(pending.probing ? "已停止等待；已发出的探测可能仍在完成，冷却期间不会重复请求。" : "已取消读取；未更新本机报告。"));
    button.addEventListener("click", () => collect(false));
    probeButton.addEventListener("click", () => collect(true));
    function collect(probing) {
      if (pending) return;
      const item = report?.processes?.[Number(select.value)];
      if (probing && !item?.instance) return;
      if (!probing) {
        report = null; result.textContent = ""; select.innerHTML = '<option>正在读取…</option>';
        probeButton.hidden = true; probeHelp.hidden = true;
      }
      select.disabled = true;
      const nonce = [...window.crypto.getRandomValues(new Uint8Array(16))].map(n => n.toString(16).padStart(2, "0")).join("");
      const path = probing ? `process-network-probe?pid=${Number(item.pid)}&instance=${encodeURIComponent(item.instance)}&` : "process-network?";
      const popup = window.open(`http://127.0.0.1:39002/${path}origin=${encodeURIComponent(window.location.origin)}&nonce=${nonce}`, "agent-monitor-process-network", "width=520,height=240");
      if (!popup) { status.textContent = "浏览器阻止了连接窗口。请允许弹窗后重试。"; if (!probing) select.innerHTML = '<option>未取得本机报告</option>'; select.disabled = !report?.processes?.length; help.open = true; return; }
      status.textContent = probing ? "正在核对进程与代理并探测固定目标…" : "正在读取浏览器所在电脑的 Agent 进程…";
      button.disabled = true; probeButton.disabled = true; cancel.hidden = false;
      const attempt = { probing, finish(error, data) {
        if (pending !== attempt) return;
        pending = null; window.clearTimeout(attempt.timer);
        window.removeEventListener("message", receive); window.removeEventListener("pagehide", leave);
        popup.close(); button.disabled = false; probeButton.disabled = false; cancel.hidden = true;
        select.disabled = !report?.processes?.length;
        if (error) {
          status.textContent = error; if (!probing) select.innerHTML = '<option>未取得本机报告</option>'; help.open = true;
          return;
        }
        if (probing) {
          item.probe = data; status.textContent = `本机 ${report.host} · 进程快照 ${formatDate(report.observed_at)} · 固定目标探测已返回，详情见回执。`;
          render(); return;
        }
        report = data;
        status.textContent = `本机 ${data.host} · 观测于 ${formatDate(data.observed_at)} · ${data.processes.length} 个进程。此为一次性快照，进程和连接可能随后变化。`;
        if (data.errors?.length) status.textContent += " 采集不完整：" + data.errors.map(e => `${e.stage}/${e.type}`).join("；");
        select.innerHTML = data.processes.map((p, i) => `<option value="${i}">${escapeHtml(p.agent)} · PID ${Number(p.pid)}</option>`).join("") || '<option>未发现可识别进程</option>';
        select.disabled = !data.processes.length;
        if (!data.processes.length) result.textContent = "请以启动 Codex/Claude 的同一操作系统用户运行连接器。仅覆盖本机原生进程和已识别的 Node CLI 入口；SSH、容器内及其他用户的进程不属于此报告。";
        else render();
      }};
      function receive(event) {
        if (event.origin !== "http://127.0.0.1:39002" || event.source !== popup || event.data?.nonce !== nonce) return;
        if (probing && event.data.type === 'agent-monitor-probe-ready') {
          popup.postMessage({type: 'agent-monitor-probe-ack', nonce}, event.origin); return;
        }
        if (event.data.type !== (probing ? "agent-monitor-process-network-probe" : "agent-monitor-process-network")) return;
        if (event.data.error) { attempt.finish(String(event.data.error)); return; }
        if (probing) {
          if (event.data.probe?.instance !== item.instance) { attempt.finish("探测回执的进程实例不匹配。"); return; }
          attempt.finish(null, event.data.probe); return;
        }
        const data = event.data.report;
        if (data?.schema_version !== 1 || typeof data.host !== "string" || !Array.isArray(data.processes)) {
          attempt.finish("本机报告格式不兼容，请更新并重启连接器。"); return;
        }
        attempt.finish(null, data);
      }
      function leave() { attempt.finish("页面已离开，本次读取已取消。"); }
      pending = attempt;
      window.addEventListener("message", receive); window.addEventListener("pagehide", leave, {once: true});
      attempt.timer = window.setTimeout(() => attempt.finish("未收到本机报告。请检查连接器是否运行、版本是否已更新，以及是否允许弹窗；不会改用服务端结果。"), probing ? 26000 : 18000);
    }
  }

  function renderNetwork(data, retry) {
    const target = qs("#network-checked-host");
    if (target) target.textContent = `仅检测服务端主机 ${data.checked_host || "身份未取得"}；不检测浏览器所在设备或其他机器。`;
    renderNetworkBanner(data);
    if (data.installed === false) {
      renderNetworkError({
        title: "ip-check 未安装，请在 agent-monitor 代码目录中运行 ./install.sh。",
        message: data.hint || data.error || "使用 /network 前请先安装 ip-check。",
        docs: true,
      }, retry);
      setNetworkCardsUnavailable("ip-check 未安装。");
      return;
    }
    if (data.error) {
      renderNetworkError({
        title: "网络检查失败",
        message: data.error,
        retry: true,
      }, retry);
      setNetworkCardsUnavailable("网络检查失败。");
      return;
    }
    clearNetworkError();
    renderLocalNetwork(data);
    renderPublicNetwork(data);
    renderRiskNetwork(data);
    renderTimezoneNetwork(data);
    renderConclusions(data);
  }

  function renderNetworkBanner(data) {
    const banner = qs("#network-banner");
    const verdict = qs("#network-verdict");
    const updated = qs("#network-updated");
    const refreshStatus = qs("#network-refresh-status");
    if (refreshStatus) {
      const refresh = data.refresh;
      const remaining = refresh?.retry_in_seconds;
      const next = Number.isFinite(remaining) && remaining > 0
        ? `；最早可重新检测于 ${formatDate(new Date(Date.now() + remaining * 1000).toISOString())}` : "";
      refreshStatus.textContent = typeof refresh?.reused === "boolean"
        ? `${refresh.reused ? "本次读取复用上次结果，未重新探测" : data.error || data.installed === false ? "本次检测未完成" : "本次读取取得新检测结果"}${next}。缓存期内点击不会增加探测请求；上游限流可能延长等待。`
        : "本次读取是否复用缓存未记录。重新检测受服务端缓存与上游等待要求约束。";
    }
    const coverage = data.coverage;
    const missing = coverage?.missing;
    const incomplete = !Array.isArray(missing) || missing.length > 0;
    const level = data.verdict || "unknown";
    banner.className = `verdict-banner ${level === "high" ? "high" : incomplete ? "unknown" : level === "low" ? "low" : level === "proxy-in-use" ? "proxy-in-use" : "unknown"}`;
    if (data.installed === false) {
      verdict.textContent = "—";
      updated.textContent = "原因：ip-check 未安装";
      return;
    } else if (data.error) {
      verdict.textContent = "—";
      updated.textContent = `原因：${data.error}`;
      return;
    } else if (level === "high") {
      const hits = (data.conclusions || []).filter(item => item.level === "bad").length;
      verdict.textContent = hits ? `本地规则检出 ${hits} 项异常` : "本地规则提示异常";
    } else if (level === "proxy-in-use") {
      verdict.textContent = incomplete ? "部分检测 · 检出代理" : "检出代理 · 本地规则评估";
    } else if (level === "low") {
      verdict.textContent = incomplete ? "部分检测 · 未发现已知高风险信号" : "未发现已知高风险信号 · 本地规则评估";
    } else {
      verdict.textContent = "网络状态未知";
    }
    updated.textContent = data.timestamp ? `观测于 ${formatDate(data.timestamp)}` : "观测时间未记录";
    const names = {public: "公网", dns: "DNS 名单归属", risk: "风险分", timezone: "时区"};
    updated.textContent += !Array.isArray(missing) ? " · 检测覆盖未知" : missing.length ? " · 缺测：" + missing.map(key => names[key] || key).join("、") : "";
    updated.textContent += " · IPv6 公网泄漏未核实";
  }

  function renderNetworkError(error, retry) {
    const panel = qs("#network-error");
    if (!panel) {
      return;
    }
    const docsLink = error.docs ? '<a href="/ip-check-docs">文档</a>' : "";
    const retryButton = error.retry ? '<button type="button" data-network-retry>重试</button>' : "";
    const actions = [docsLink, retryButton].filter(Boolean).join("");
    panel.hidden = false;
    panel.innerHTML = [
      `<h2>${escapeHtml(error.title)}</h2>`,
      `<p>${escapeHtml(error.message || "未知错误")}</p>`,
      actions ? `<div class="network-error-actions">${actions}</div>` : "",
    ].join("");
    const button = panel.querySelector("[data-network-retry]");
    if (button && retry) {
      button.addEventListener("click", () => withRefresh(button, retry));
    }
  }

  function clearNetworkError() {
    const panel = qs("#network-error");
    if (panel) {
      panel.hidden = true;
      panel.innerHTML = "";
    }
  }

  function setNetworkCardsUnavailable(message) {
    const html = `<div class="section-failure">${escapeHtml(message)}</div>`;
    ["local", "public", "risk", "timezone", "conclusion"].forEach((id) => {
      qs(`#network-${id}`).innerHTML = html;
    });
  }

  function renderLocalNetwork(data) {
    const local = data.local;
    if (!local) {
      renderSectionFailure("#network-local", data, "local");
      return;
    }
    const ipv6 = local.ipv6 ? statusPill("warn", `取得源地址 ${local.ipv6}`) : statusPill("warn", "未取得路由源地址");
    const dnsUnknown = !local.dns?.length || local.dns.some(entry => !entry.country);
    const dns = local.dns && local.dns.length ? local.dns.map(renderDns).join("") : "—";
    qs("#network-local").innerHTML = [
      kvRow("IPv4 路由源地址", escapeHtml(local.lan_ip || "—")),
      kvRow("IPv6 路由源地址", ipv6),
      '<p class="scope-note">地址来自系统向固定目标选路的结果，并未枚举全部网卡；未取得 IPv6 源地址不代表没有 IPv6，公网泄漏未核实。</p>',
      kvRow("DNS 服务器", `<div class="dns-list">${dns}</div>`),
      kvRow("DNS 名单归属", local.dns_has_cn ? statusPill("bad", "命中国内 DNS 服务名单") : dnsUnknown ? statusPill("warn", "DNS 服务归属未知或部分未知") : statusPill("ok", "未命中国内 DNS 服务名单")),
      '<p class="scope-note">这里只读取 DNS 配置和静态服务商名单，国家标注不是解析节点的实测位置。本机代理或内网地址的上游未核实，也未验证目标请求实际使用的解析器。</p>',
    ].join("");
  }

  function renderPublicNetwork(data) {
    const pub = data.public;
    if (!pub || pub.ok === false) {
      renderSectionFailure("#network-public", data, "public", pub && pub.error);
      return;
    }
    const location = [pub.country, pub.region, pub.city].filter(Boolean).join(" / ") || "—";
    const timezone = pub.timezone ? `${escapeHtml(pub.timezone)} (${escapeHtml(pub.tz_offset || "—")})` : "—";
    qs("#network-public").innerHTML = [
      kvRow("本次探测 IP", escapeHtml(pub.ip || "—")),
      kvRow("探测目标", escapeHtml(pub.probe_url || data.proxy_effective?.probe_url || "旧记录未保存目标")),
      kvRow("位置", escapeHtml(location)),
      kvRow("运营商", escapeHtml(pub.isp || "—")),
      kvRow("机构", escapeHtml(pub.org || "—")),
      kvRow("时区", timezone),
      '<p class="scope-note">这个 IP 属于本次探测。不同目标可能走不同代理，不能据此确认账号请求的公网 IP。</p>',
    ].join("");
  }

  function renderRiskNetwork(data) {
    const pub = data.public || {};
    const risk = data.risk;
    const spam = data.spam;
    const proxyEnvEntries = Object.entries(data.proxy_envs || {});
    const present = value => value !== null && value !== undefined && value !== "";
    const score = result => statusPill(result.level === "high" ? "bad" : result.level === "medium" ? "warn" : "ok", `${result.score}/100${result.level ? ` ${result.level}` : ""}`);
    const markedProxy = pub.proxy;
    const booleanReading = value => value === true ? statusPill("warn", "是") : value === false ? statusPill("ok", "否") : statusPill("warn", "未知");
    const provider = (label, section, rows, rawLines = [], missing = []) => {
      const error = sectionMessage(data, section);
      let absent = "未取得读数";
      if (!data[section]) {
        if (pub.ok === false || (pub.ok === true && !pub.ip)) absent = "未查询：未取得可用公网 IP。";
        else if (pub.ok === true && pub.ip && pub.proxy === false && pub.hosting === false) absent = "未查询：ip-api 未标记为代理或机房，未触发信誉查询；不代表信誉库没有记录。";
        else if (pub.ok === true && pub.ip && pub.proxy !== true && pub.hosting !== true) absent = "未查询：代理或机房标记不完整，未触发信誉查询。";
      }
      const raw = rawLines.length ? `<p class="scope-note">${rawLines.map(escapeHtml).join("<br>")}</p>` : "";
      return `<div class="network-provider"><h3>${label}</h3>${rows.length ? rows.join("") : error || rawLines.length ? "" : `<p class="scope-note">${absent}</p>`}${rows.length && missing.length ? `<p class="scope-note">未取得：${missing.join("、")}</p>` : ""}${error ? `<p class="scope-note">${escapeHtml(error)}</p>` : ""}${raw && rows.length ? `<details class="note-disclosure"><summary>供应商返回文本</summary>${raw}</details>` : raw}</div>`;
    };
    const riskRows = [];
    if (present(risk?.score)) riskRows.push(kvRow("风险分", score(risk)));
    if (present(risk?.type)) riskRows.push(kvRow("类型", escapeHtml(risk.type)));
    if (present(risk?.display)) riskRows.push(kvRow("供应商读数", escapeHtml(risk.display)));
    const spamRows = [];
    const spamPlaceholder = spam?.frequency === 255;
    if (present(spam?.score)) spamRows.push(kvRow("滥用置信度", `${escapeHtml(String(spam.score))}%`));
    if (present(spam?.frequency)) spamRows.push(kvRow(spamPlaceholder ? "供应商频次字段" : "举报次数", escapeHtml(String(spam.frequency))));
    if (present(spam?.last_seen)) spamRows.push(kvRow(spamPlaceholder ? "供应商时间" : "最近举报", escapeHtml(spam.last_seen)));
    if (spamPlaceholder) spamRows.push('<p class="scope-note">255 可能是供应商黑名单占位值，时间可能随查询更新；真实举报次数与最近举报时间未核实。</p>');
    const envs = proxyEnvEntries.length
      ? proxyEnvEntries.map(([key, value]) => `${escapeHtml(key)} = ${escapeHtml(value)}`).join("<br>")
      : "未记录代理环境变量";
    qs("#network-risk").innerHTML = [
      provider("proxycheck · 风险", "risk", riskRows, risk?.raw_lines || [], [!present(risk?.score) && "风险分", !present(risk?.type) && "类型"].filter(Boolean)),
      '<div class="network-provider"><h3>代理与机房标记</h3>',
      kvRow("标记为代理", booleanReading(markedProxy)),
      kvRow("机房 IP", booleanReading(pub.hosting)),
      '<p class="scope-note">代理标记与机房判定来自 ip-api 的 IP 分类；「否」不表示请求没有经过代理，也不证明账号可用。</p></div>',
      provider("stopforumspam · 举报", "spam", spamRows, spam?.raw_lines || [], [!present(spam?.score) && "滥用置信度", !present(spam?.frequency) && "举报次数", !present(spam?.last_seen) && "最近举报"].filter(Boolean)),
      present(spam?.score) ? '<p class="scope-note">滥用置信度是 stopforumspam 根据举报频次与时间计算的估计，不是 proxycheck 风险分，也不是账号封禁概率。</p>' : "",
      '<div class="network-provider"><h3>服务端出站策略</h3>',
      kvRow("策略", data.proxy_effective?.source === "system"
        ? "执行主机的系统代理，按每个目标解析"
        : data.proxy_effective?.source === "published" ? "旧记录：个人代理文件指定" : "旧记录未保存有效系统策略"),
      kvRow("环境变量", sectionMessage(data, "proxy_envs") ? escapeHtml(sectionMessage(data, "proxy_envs")) : envs),
      `<p class="scope-note">这里是诊断子进程的代理变量，内部代理桥已隐藏；不代表服务原始环境或本机 Agent 的配置。${data.proxy_effective?.source === "system" ? "诊断使用服务端系统策略，目标不同仍可能使用不同出口。" : "这条旧记录不能确认当前请求的出口。"}</p></div>`,
    ].join("");
  }

  function renderTimezoneNetwork(data) {
    const tz = data.tz_check;
    const pub = data.public || {};
    if (!tz) {
      renderSectionFailure("#network-timezone", data, "tz_check");
      return;
    }
    const match = tz.matched === true
      ? statusPill("ok", tz.match_label || "一致")
      : tz.matched === false
        ? statusPill("bad", tz.match_label || "不一致")
        : statusPill("warn", "无法比较");
    qs("#network-timezone").innerHTML = [
      kvRow("服务端主机时区", `${escapeHtml(tz.cli_tz || "—")} (${escapeHtml(tz.cli_offset || "—")})`),
      kvRow("公网时区", pub.timezone ? `${escapeHtml(pub.timezone)} (${escapeHtml(pub.tz_offset || "—")})` : "—"),
      kvRow("是否一致", match),
      '<p class="scope-note">优先比较时区名称，取不到名称时比较当时的 UTC 偏移；名称不同也可能偏移相同。差异只触发本地规则，不证明网络故障，无需仅据此修改主机时区。</p>',
    ].join("");
  }

  function renderConclusions(data) {
    const items = (data.conclusions || []).slice().sort((a, b) => ({bad: 0, warn: 1, ok: 2}[a.level] ?? 3) - ({bad: 0, warn: 1, ok: 2}[b.level] ?? 3));
    const note = '<p class="scope-note">「异常」表示命中本地启发式规则，不等于网络故障或账号不可用。请结合具体读数判断；本页不验证服务可用性。</p><details class="note-disclosure"><summary>规则与检测边界</summary><p><strong>异常条件</strong></p><p>本地规则将以下任一情况标为「异常」：</p><ul><li>取得本地 IPv6 路由源地址。</li><li>命中国内 DNS 服务静态名单。</li><li>proxycheck 风险分不低于 70（低于 30 为低，30–69 为中）。</li><li>服务端主机与公网 IP 的时区名称或 UTC 偏移比较不一致。</li></ul><p><strong>检测不完整</strong></p><ul><li>未命中上述规则，但缺少公网、DNS 名单归属或时区结果时，风险状态为未知。</li><li>公网 IP 被标记为代理或机房时才查询 proxycheck 和 stopforumspam；此时缺少风险分则标为部分检测。</li></ul><p><strong>结论范围</strong></p><p>本地源地址不证明公网泄漏，DNS 名单不证明实际解析地域，IP 分类不证明真实连接路线。未命中上述规则不保证服务可用。</p></details>';
    if (!items.length) {
      qs("#network-conclusion").innerHTML = `<div class="empty-state">—</div>${note}`;
      return;
    }
    qs("#network-conclusion").innerHTML = `<ul class="conclusion-list">${items.map((item) => {
      const kind = item.level === "bad" ? "bad" : item.level === "warn" ? "warn" : "ok";
      const levelText = { bad: "异常", warn: "注意", ok: "正常" }[item.level] || item.level;
      return `<li>${statusPill(kind, levelText)} <span>${escapeHtml(item.text)}</span></li>`;
    }).join("")}</ul>${note}`;
  }

  function renderSectionFailure(selector, data, section, fallback) {
    const message = fallback || sectionMessage(data, section) || "未知";
    qs(selector).innerHTML = `<div class="section-failure">${statusPill("warn", "查询失败")} ${escapeHtml(message)}</div>`;
  }

  function sectionMessage(data, section) {
    const error = (data.errors || []).find((item) => item.section === section);
    return error && error.message;
  }

  function kvRow(label, value) {
    return `<div class="kv-row"><div class="label">${escapeHtml(label)}</div><div class="value">${value}</div></div>`;
  }

  function renderDns(entry) {
    const country = entry.country ? ` ${statusPill(entry.country === "CN" ? "bad" : "ok", `名单标注 ${entry.country}`)}` : ` ${statusPill("warn", "上游归属未核实")}`;
    const label = entry.label ? ` <span class="muted">${escapeHtml(entry.label)}</span>` : "";
    return `<div class="dns-row"><span>${escapeHtml(entry.ip)}</span>${label}${country}</div>`;
  }

  function statusPill(kind, text) {
    return `<span class="status-pill ${kind}">${escapeHtml(text)}</span>`;
  }

  return { initNetwork, renderNetwork, renderNetworkBanner, renderNetworkError, clearNetworkError, setNetworkCardsUnavailable, renderLocalNetwork, renderPublicNetwork, renderRiskNetwork, renderTimezoneNetwork, renderConclusions, renderSectionFailure, sectionMessage, kvRow, renderDns, statusPill };
});
