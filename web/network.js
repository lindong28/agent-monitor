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
      const context = await dependencies.runtimeContext?.() || {};
      if (!active) return;
      await initProcessNetwork(context);
      if (dependencies.runtimeContext) {
        try {
          const response = await platform.fetch('/api/network/cache');
          const cached = response.ok && await response.json();
          if (active && cached?.available) renderNetwork(cached, () => load(true));
        } catch (_) { /* Optional historical server evidence does not block local facts. */ }
      }
    } else {
      await load(false);
    }
  }

  async function initProcessNetwork(context = {}) {
    let current = true;
    const local = context.local_network === true;
    const button = qs("#process-network-refresh"), status = qs("#process-network-status");
    const select = qs("#process-network-select"), result = qs("#process-network-result");
    const search = qs("#process-network-search"), help = qs("#process-network-help"), cancel = qs("#process-network-cancel");
    const controls = {
      provider: {button: qs("#process-network-probe"), help: qs("#process-network-probe-help")},
      third_party: {button: qs("#process-network-third-party"), help: qs("#process-network-third-party-help")},
    };
    qs("#process-network-command").textContent = `./agent-monitor connect --server '${window.location.origin.replaceAll("'", "'\\''")}'`;
    let report = null, pending = null, selectedInstance = "", staleReport = false;
    const receipts = new Map();
    const storageKey = local ? `agent-monitor.network.v1:${context.subject_id}` : null;
    function save() {
      if (!storageKey || !report) return;
      try {
        const value = JSON.stringify({report, selectedInstance, receipts: [...receipts].slice(-20)});
        if (value.length <= 1048576) window.localStorage?.setItem(storageKey, value);
      } catch (_) { /* Storage denial/full leaves this page's evidence available. */ }
    }
    function historical() {
      for (const history of receipts.values()) for (const entries of Object.values(history)) entries.forEach(entry => { entry.historical = true; });
    }
    function renderHost() {
      const node = qs('#local-network-facts'), facts = report?.host_network;
      if (!node || !facts) return;
      const proxy = facts.system_proxy || {};
      node.innerHTML = '<h3>本机系统网络</h3>' + [
        kvRow('观测主机 / 时间', escapeHtml(`${report.host} / ${timestamp(facts.observed_at)}`)),
        kvRow('本机操作系统时区', escapeHtml(`${facts.timezone?.name || '未取得'} ${facts.timezone?.offset || ''}`)),
        kvRow('接口地址', escapeHtml(facts.addresses?.map(a => `${a.interface}: ${a.address}`).join('；') || '未取得')),
        kvRow('DNS 配置', escapeHtml(facts.dns?.join('；') || '未取得；不能据此断定未配置 DNS')),
        kvRow('系统代理配置', proxy.status === 'observed' ? escapeHtml((proxy.entries || []).map(p => `${p.protocol}: ${p.enabled ? p.address || '地址未取得' : '未启用'}`).join('；') + `；PAC ${proxy.pac_enabled ? '已启用（未下载或执行）' : '未启用'}；自动发现 ${proxy.auto_discovery_enabled ? '已启用' : '未启用'}`) : escapeHtml(proxy.reason || '未取得')),
        ...Object.entries(facts.monitor_environment || {}).map(([key, value]) => kvRow(`本地服务环境 ${key}`, escapeHtml(value.address ?? value.value ?? '未取得'))),
        '<p class="scope-note">以上为被动配置快照，不确认 DNS 的实际请求路径、浏览器或 Agent 的最终出口。公网出口尚未核实。</p>',
        ...(facts.errors || []).map(e => `<p class="error">部分系统信息未取得：${escapeHtml(e.stage)} / ${escapeHtml(e.type)}</p>`),
      ].join('');
    }
    const selectionKey = (p, index) => p.instance || `unavailable:${index}`;
    const selected = () => report?.processes?.find((p, index) => selectionKey(p, index) === selectedInstance);
    const timestamp = value => {
      if (!value) return '未记录';
      const date = new Date(typeof value === 'number' ? value * 1000 : value);
      if (local) {
        try {
          const timeZone = report?.host_network?.timezone?.name;
          const zone = new Intl.DateTimeFormat('en-US', {timeZone, timeZoneName: 'shortOffset'}).formatToParts(date).find(p => p.type === 'timeZoneName')?.value;
          return date.toLocaleString('zh-CN', {timeZone}) + (zone ? ` ${zone}` : '');
        }
        catch (_) { return date.toLocaleString('zh-CN'); }
      }
      return formatDate(date.toISOString());
    };
    const label = p => [p.agent, `PID ${p.pid}`, p.context?.cwd || "目录未取得", p.context?.terminal, p.context?.started_at ? `启动于 ${timestamp(p.context.started_at)}` : "启动时间未取得"].filter(Boolean).join(" · ");
    function options() {
      const query = search.value.trim().toLocaleLowerCase();
      const items = (report?.processes || []).map((p, index) => ({p, key: selectionKey(p, index)})).filter(({p, key}) => !query || label(p).toLocaleLowerCase().includes(query) || key === selectedInstance);
      select.innerHTML = '<option value="">请选择正在使用的进程</option>' + items.map(({p, key}) => `<option value="${escapeHtml(key)}">${escapeHtml(label(p))}</option>`).join("");
      select.value = selectedInstance;
      select.disabled = !!pending || !report?.processes?.length;
      const summary = qs('#process-network-summary');
      if (summary && report) summary.innerHTML = '<h3>已观察的 Agent</h3>' + (items.length ? items.map(({p, key}) => {
        const observed = p.proxy_observation?.connections || [];
        const path = observed.length ? observed.map(c => `${c.host || '目标未取得'}:${c.port || '?'} · ${[...(c.chains || [])].reverse().join(' → ') || '代理链未取得'}`).join('；')
          : p.connections?.length ? `TCP 对端 ${[...new Set(p.connections.map(c => c.peer))].join('；')}（可能是代理，未确认业务目标）`
          : p.connections_status === 'observed' ? '本次未观察到已建立的 TCP 连接；不代表没有联网。' : '当前连接信息未取得。';
        return '<div class="network-card-body">' + kvRow(`${p.agent} · PID ${p.pid}`, escapeHtml(p.context?.cwd || '项目目录未取得')) + kvRow('已观察路径', escapeHtml(path)) +
          `<button type="button" class="btn" data-process-instance="${escapeHtml(key)}">查看 ${escapeHtml(p.agent)} PID ${Number(p.pid)} 详情</button></div>`;
      }).join('') : '<p>本次没有匹配的进程；系统网络信息仍可查看。</p>');
    }
    function render() {
      const item = selected();
      for (const [kind, control] of Object.entries(controls)) {
        const option = item?.probe_options?.[kind];
        control.button.hidden = control.help.hidden = !item;
        control.button.disabled = !!pending || staleReport || !item?.instance || option?.status !== "available";
        const disclosure = kind === "third_party" ? "第三方为 HTTP 非加密服务，仅限非商业使用。" : "可不执行供应商探测，独立选择第三方观测。";
        control.help.textContent = staleReport ? "上次快照仅供查看；请重新读取本机，成功后再选择探测。" : item && !item.instance ? "未取得完整进程实例；仅可查看本次报告。请重新读取，实例身份核实后才能探测。" : option ? `向 ${option.target || "目标未取得"} 经该进程已核实的 HTTPS/ALL 代理 ${option.proxy_address || "（见准入结果）"} 发送一次匿名 GET；不携带账号或消息，无自动重试。${disclosure} ${option.status === "available" ? "点击下方按钮才发送。" : `暂不可执行：${option.reason || "前置条件未满足"}。${option.next_step || "请重新读取进程报告核对条件。"}`}` : "当前连接器未提供探测条件；请更新并重启连接器后重新读取。本次仍可查看已有进程报告。";
      }
      if (!item) { result.textContent = report?.processes?.length ? "请根据目录、启动时间和 PID 选择进程；不会自动推断你正在使用哪一个。" : local ? "本次未取得可选 Agent 进程；仍可查看上方系统信息。请以启动 Agent 的同一用户运行 agent-monitor open 后更新本机。" : "尚无可选进程。请以启动 Agent 的同一用户运行连接器，再读取本机。"; result.innerHTML = escapeHtml(result.textContent); return; }
      const observation = item.proxy_observation;
      const paths = new Map();
      for (const c of observation?.connections || []) {
        const key = JSON.stringify([c.host, c.port, c.chains, c.rule, c.observed_at]);
        const group = paths.get(key);
        if (group) group.count++;
        else paths.set(key, {...c, count: 1});
      }
      const connections = [...paths.values()];
      const history = receipts.get(item.instance) || {};
      result.innerHTML = [
        '<h3>所选 Agent 与已观察路径</h3>',
        kvRow("工作目录", escapeHtml(item.context?.cwd || "未取得；可用 PID 和启动时间核对")),
        kvRow("进程 / 启动时间", `${escapeHtml(item.agent)} · PID ${Number(item.pid)} / ${escapeHtml(timestamp(item.context?.started_at))}`),
        kvRow("终端 / 父进程", `${escapeHtml(item.context?.terminal || "未取得")} / ${escapeHtml(item.context?.parent_pid ?? "未取得")}`),
        kvRow("进程快照时间", escapeHtml(timestamp(report.observed_at))),
        observation?.status === "observed" && connections.length ? connections.map(c => [
          kvRow("已有连接目标", escapeHtml(`${c.host || "主机未取得"}:${c.port || "端口未取得"} · ${c.count} 条连接`)),
          kvRow("策略组到节点 / 规则", `${escapeHtml([...(c.chains || [])].reverse().join(" → ") || "未取得")} / ${escapeHtml(c.rule || "未取得")}`),
          kvRow("来源 / 观测时间", `本机 Mihomo 已有连接关联 / ${escapeHtml(timestamp(c.observed_at))}`),
        ].join("")).join("") : `<p>${escapeHtml(observation?.reason || "实际服务目标与代理链未取得。")} ${escapeHtml(observation?.next_step || "可展开连接明细核对当前 TCP；如使用 Mihomo，查看接入指南配置可选的本地控制器。")}</p>`,
        '<p class="scope-note">已有连接关联仅说明当时目标与代理链，不提供公网出口 IP；当前 TCP 对端也可能只是代理。</p>',
        '<h3>出口证据</h3>',
        !Object.keys(history).length ? '<p>公网出口尚未核实。可按需选择下方任一种探测，也可仅保留只读观察。</p>' : "",
        ...Object.entries(history).map(([kind, entries]) => entries.map(({data: probe, historical}) => [
          `<h4>${kind === "provider" ? "供应商固定目标" : "第三方出口观测"}${historical ? " · 历史回执（未重新核实路径）" : " · 探测回执"}</h4>`,
          kvRow("目标 / 代理", `${escapeHtml(probe.target || probe.host || "未记录")} / ${escapeHtml(probe.proxy_address || "未确认")}`),
          kvRow("此次探测 IP / 地区", `${escapeHtml(probe.ip || "未取得")} / ${escapeHtml([probe.country || probe.country_code, probe.region, probe.city].filter(Boolean).join(" / ") || "未取得")}`),
          kind === 'third_party' ? kvRow('ISP / 组织 / ASN', escapeHtml([probe.isp, probe.org, probe.as].filter(Boolean).join(' / ') || '未返回')) + kvRow('IP 归属时区 / 第三方标注', escapeHtml(`${probe.timezone || '未返回'}；代理 ${typeof probe.proxy === 'boolean' ? probe.proxy ? '是' : '否' : '未返回'}；托管 ${typeof probe.hosting === 'boolean' ? probe.hosting ? '是' : '否' : '未返回'}`)) : '',
          kvRow("观测时间", `${escapeHtml(timestamp(probe.observed_at))} · ${probe.cached ? "复用回执" : "本次结果"}`),
          '<p class="scope-note">仅证明这个目标在该时点的匿名探测结果，不代表 Agent 已发生的业务请求，也不代表其他目标的出口。</p>',
          probe.process_still_matches === false ? '<p class="error">探测结束时进程或连接已改变，请重新读取进程报告。</p>' : "",
          probe.error ? `<p class="error">${escapeHtml(probe.error)}</p>` : "",
          probe.retry_after_seconds ? `<p>最早 ${Number(probe.retry_after_seconds)} 秒后可重新探测；此前可能复用回执。</p>` : "",
        ].join("")).join("")),
        '<details class="note-disclosure"><summary>启动配置与 TCP 连接明细</summary><div class="disclosure-body">',
        kvRow("完整进程实例", escapeHtml(item.instance || "未取得；仅展示本次报告，不能探测")),
        item.connections_status === "observed" ? (item.connections?.length ? item.connections.map(c => kvRow(escapeHtml(c.state), `${escapeHtml(c.local)} → ${escapeHtml(c.peer)}`)).join("") : '<p>本次未观察到已建立的 TCP 连接；不代表没有联网或直连。</p>') : '<p>连接信息未取得。</p>',
        '<p class="scope-note">配置来自启动环境；客户端覆盖、运行中变更、DNS 和 UDP 路径未核实。未设置环境代理不代表没有系统代理、VPN 或透明代理。</p>',
        ...Object.entries(item.environment || {}).map(([key, value]) => kvRow(escapeHtml(key), escapeHtml(value.address ?? value.value ?? "未取得") + (value.credentials_present ? "（认证信息已隐藏）" : ""))),
        !Object.keys(item.environment || {}).length ? '<p>未取得支持的启动网络字段。</p>' : "",
        ...(item.errors || []).map(e => `<p class="error">部分采集失败：${escapeHtml(e.stage)} / ${escapeHtml(e.type)}</p>`),
        '</div></details>',
      ].join("");
    }
    onPageCleanup?.(() => { current = false; save(); pending?.finish("页面已离开，本次读取已取消。"); });
    search.addEventListener("input", options);
    select.addEventListener("change", () => { selectedInstance = select.value; render(); save(); });
    qs('#process-network-summary')?.addEventListener('click', event => {
      const choice = event.target.closest?.('[data-process-instance]');
      if (!choice || pending) return;
      selectedInstance = choice.dataset.processInstance; select.value = selectedInstance; render(); save();
    });
    cancel.addEventListener("click", () => pending?.finish(pending.probing ? "已停止等待；已发出的探测可能仍在完成，不会自动重试。" : "已取消读取。"));
    button.addEventListener("click", () => collect());
    for (const [kind, control] of Object.entries(controls)) control.button.addEventListener("click", () => collect(kind));
    function collect(kind) {
      if (local) return collectLocal(kind);
      if (pending) return;
      const probing = !!kind, item = selected();
      if (probing && (staleReport || !item?.instance || item.probe_options?.[kind]?.status !== "available")) return;
      const nonce = [...window.crypto.getRandomValues(new Uint8Array(16))].map(n => n.toString(16).padStart(2, "0")).join("");
      const path = probing ? `process-network-probe?kind=${kind}&pid=${Number(item.pid)}&instance=${encodeURIComponent(item.instance)}&` : "process-network?";
      const popup = window.open(`http://127.0.0.1:39002/${path}origin=${encodeURIComponent(window.location.origin)}&nonce=${nonce}`, "agent-monitor-process-network", "width=520,height=240");
      const retained = () => report ? ` 保留本机 ${report.host} 在 ${timestamp(report.observed_at)} 的上次快照。` : "";
      if (!popup) { status.textContent = "浏览器阻止了连接窗口。请允许弹窗后重试。" + retained(); help.open = true; return; }
      status.textContent = probing ? "正在核对进程与代理并探测所选目标…" : "正在读取浏览器所在电脑的 Agent 进程…";
      const attempt = { probing, finish(error, data) {
        if (pending !== attempt) return;
        pending = null; window.clearTimeout(attempt.timer);
        window.removeEventListener("message", receive); window.removeEventListener("pagehide", leave);
        popup.close(); button.disabled = false; cancel.hidden = true; search.disabled = false;
        if (error) { if (!probing && report) staleReport = true; status.textContent = error + retained(); help.open = true; options(); render(); return; }
        if (probing) {
          const history = receipts.get(item.instance) || {};
          // Retain the last successful evidence if a later attempt fails.
          const previous = history[kind] || [];
          history[kind] = data.error ? [...previous.filter(entry => !entry.data.error).map(entry => ({...entry, historical: true})), {data, historical: false}] : [{data, historical: false}];
          receipts.set(item.instance, history);
          status.textContent = `本机 ${report.host} · 进程快照 ${timestamp(report.observed_at)} · 探测已返回，结果见对应回执。`;
          options(); render(); return;
        }
        for (const history of receipts.values()) for (const entries of Object.values(history)) entries.forEach(entry => { entry.historical = true; });
        const previousInstance = selectedInstance;
        report = data; staleReport = false;
        if (previousInstance.startsWith("unavailable:") || !selected()) selectedInstance = "";
        status.textContent = `本机 ${data.host} · 观测于 ${timestamp(data.observed_at)} · ${data.processes.length} 个进程。此为当前一次性快照，不是网址时间范围内的历史。`;
        if (previousInstance && !selectedInstance) status.textContent += " 原选进程实例已不在本次报告中，可能已退出或无法读取；请重新选择。";
        if (data.errors?.length) status.textContent += " 采集不完整：" + data.errors.map(e => `${e.stage}/${e.type}`).join("；");
        help.open = false; options(); render();
      }};
      function receive(event) {
        if (event.origin !== "http://127.0.0.1:39002" || event.source !== popup || event.data?.nonce !== nonce) return;
        if (probing && event.data.type === "agent-monitor-probe-ready") {
          popup.postMessage({type: "agent-monitor-probe-ack", nonce}, event.origin); return;
        }
        if (event.data.type !== (probing ? "agent-monitor-process-network-probe" : "agent-monitor-process-network")) return;
        if (event.data.error) { attempt.finish(String(event.data.error)); return; }
        if (probing) {
          if (event.data.probe?.instance !== item.instance || event.data.probe?.probe_kind !== kind) { attempt.finish("探测回执的进程实例或类型不匹配；未接纳回执。请更新并重启连接器。"); return; }
          attempt.finish(null, event.data.probe); return;
        }
        const data = event.data.report;
        if (data?.schema_version !== 1 || typeof data.host !== "string" || !Array.isArray(data.processes)) { attempt.finish("本机报告格式不兼容，请更新并重启连接器。"); return; }
        if (!data.processes.length && data.errors?.length) {
          attempt.finish("本次进程采集失败：" + data.errors.map(e => `${e.stage}/${e.type}`).join("；") + "。请检查连接器后重新读取。"); return;
        }
        attempt.finish(null, data);
      }
      function leave() { attempt.finish("页面已离开，本次读取已取消。"); }
      pending = attempt; button.disabled = select.disabled = search.disabled = true; cancel.hidden = false;
      for (const control of Object.values(controls)) control.button.disabled = true;
      window.addEventListener("message", receive); window.addEventListener("pagehide", leave, {once: true});
      attempt.timer = window.setTimeout(() => attempt.finish("未收到本机结果。请检查连接器、版本与弹窗权限；不会改用服务端结果。"), probing ? 26000 : 18000);
    }
    async function collectLocal(kind) {
      if (pending) return;
      const item = selected();
      if (kind && (staleReport || !item?.instance || item.probe_options?.[kind]?.status !== 'available')) return;
      const controller = new AbortController();
      const attempt = {probing: !!kind, finish() { controller.abort(); }};
      pending = attempt; button.disabled = true; cancel.hidden = false;
      options(); render();
      status.textContent = kind ? '正在核对路径并发送所选诊断…' : `正在更新本机 ${context.host} 的被动快照；不发送公网诊断请求。`;
      const timer = window.setTimeout(() => controller.abort(), kind ? 26000 : 24000);
      try {
        const response = await platform.fetch(kind ? '/api/local-network/probe' : '/api/local-network', {
          method: kind ? 'POST' : 'GET', headers: {'X-Agent-Monitor': 'local-network', ...(kind ? {'Content-Type': 'application/json'} : {})},
          ...(kind ? {body: JSON.stringify({pid: item.pid, instance: item.instance, probe_kind: kind})} : {}), signal: controller.signal,
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || `读取失败（HTTP ${response.status}）`);
        if (!current) return;
        if (kind) {
          if (data.instance !== item.instance || data.probe_kind !== kind) throw new Error('诊断回执身份不符；未接纳。');
          const history = receipts.get(item.instance) || {}, previous = history[kind] || [];
          history[kind] = data.error ? [...previous.filter(entry => !entry.data.error).slice(-1).map(entry => ({...entry, historical: true})), {data, historical: false}] : [{data, historical: false}];
          receipts.set(item.instance, history);
          status.textContent = `本机 ${report.host} · 所选诊断已返回，结果见对应回执。`;
        } else {
          if (data.subject_id !== context.subject_id || data.schema_version !== 1 || !Array.isArray(data.processes)) throw new Error('本机报告身份或格式不符；未接纳。');
          historical();
          const failedProcesses = !data.processes.length && data.errors?.length;
          if (failedProcesses && report) report = {...report, host_network: data.host_network};
          else report = data;
          staleReport = !!failedProcesses;
          if (!selected()) selectedInstance = '';
          status.textContent = `本机 ${data.host} · 系统观测于 ${timestamp(data.host_network?.observed_at)} · ${data.processes.length} 个进程。` + (failedProcesses ? ' 本次进程读取失败；保留的上次进程快照仅供查看，不能探测。' : ' 未发送公网诊断请求。');
          if (data.errors?.length) status.textContent += ' 采集不完整：' + data.errors.map(e => `${e.stage}/${e.type}`).join('；');
          help.open = false; renderHost();
        }
        save();
      } catch (error) {
        if (!current) return;
        if (!kind) staleReport = true;
        status.textContent = `${error.name === 'AbortError' ? kind ? '已停止等待；已发出的诊断可能仍在完成，不会自动重试。' : '读取已取消或超时。' : error.message} ${report ? `保留本机 ${report.host} 在 ${timestamp(report.observed_at)} 的上次快照。` : '本机信息未取得；可点击更新本机重试。'}`;
      } finally {
        window.clearTimeout(timer); pending = null;
        if (current) { button.disabled = false; cancel.hidden = true; options(); render(); }
      }
    }
    if (local) {
      button.textContent = '更新本机';
      if (qs('#local-network-source')) qs('#local-network-source').textContent = `本机 ${context.host} · 本地 Web 接入；打开和返回本页自动更新被动信息。`;
      try {
        const saved = JSON.parse(window.localStorage?.getItem(storageKey) || 'null');
        if (saved?.report?.subject_id === context.subject_id && Array.isArray(saved.report.processes)) {
          report = saved.report; selectedInstance = saved.selectedInstance || ''; staleReport = true;
          for (const [key, value] of saved.receipts || []) receipts.set(key, value);
          historical(); options(); renderHost(); render();
        }
      } catch (_) { /* Invalid local cache never changes server admission. */ }
      await collectLocal();
    } else if (qs('#local-network-source')) {
      qs('#local-network-source').textContent = `当前是远程网页${context.host ? `（服务主机 ${context.host}）` : ''}，不能将它视为你的电脑。推荐在浏览器所在电脑安装后运行 ./agent-monitor open；随后从本地总览进入网络。也可继续使用下方连接器手动读取。`;
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
