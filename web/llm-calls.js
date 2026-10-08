(function () {
  const filterRegistry = [
    { query: "machine", selector: "#llm-machine", scope: "request", source: "machines" },
    { query: "project", selector: "#llm-project", scope: "request", source: "projects" },
    { query: "session_ref", selector: "#llm-session", scope: "request", source: "session_refs" },
    { query: "logical_model", selector: "#llm-logical-model", scope: "request", source: "logical_models" },
    { query: "request_outcome", selector: "#llm-request-outcome", scope: "request", source: "request_outcomes" },
    { query: "provider", selector: "#llm-provider", scope: "attempt", source: "providers" },
    { query: "account_profile", selector: "#llm-profile", scope: "attempt", source: "account_profiles", kind: "profile" },
    { query: "route", selector: "#llm-route", scope: "attempt", source: "routes" },
    { query: "actual_model", selector: "#llm-actual-model", scope: "attempt", source: "actual_models" },
    { query: "attempt_outcome", selector: "#llm-attempt-outcome", scope: "attempt", source: "attempt_outcomes" },
    { query: "cost_basis", selector: "#llm-cost-basis", scope: "attempt", source: "cost_bases" },
    { query: "credential_source_kind", selector: "#llm-credential-source-kind", scope: "attempt", source: "credential_source_kinds" },
    { query: "credential_source_ref", selector: "#llm-credential-source", scope: "attempt", source: "credential_source_refs" },
  ];
  const state = {
    requestCursor: null,
    attemptCursor: null,
    requestHistory: [],
    attemptHistory: [],
    requestPage: 1,
    attemptPage: 1,
    payload: null,
    loadSequence: 0,
    reloadSequence: 0,
    pageLoadSequence: null,
    optionsLoaded: false,
    optionsSequence: 0,
    analysisSequence: null,
  };

  function node(tag, text, className) {
    const element = document.createElement(tag);
    if (text !== undefined && text !== null) element.textContent = String(text);
    if (className) element.className = className;
    return element;
  }

  function clear(element) {
    while (element.firstChild) element.removeChild(element.firstChild);
  }

  function formatTime(value) {
    if (!value) return "—";
    return new Date(value).toLocaleString("zh-CN");
  }

  function formatAmount(value, currency) {
    if (value === null || value === undefined) return "—";
    try {
      const amount = Number(value);
      if (amount !== 0 && Math.abs(amount) < 0.000001) return `${amount.toExponential(3)} ${currency || ""}`.trim();
      return new Intl.NumberFormat(undefined, { style: "currency", currency, minimumFractionDigits: 2, maximumFractionDigits: 6 }).format(amount);
    } catch (error) {
      return `${Number(value).toFixed(4)} ${currency || ""}`.trim();
    }
  }

  function human(value) {
    return String(value || "—").replaceAll("_", " ");
  }

  async function apiJSON(path, query) {
    const url = new URL(path, window.location.origin);
    Object.entries(query || {}).forEach(([key, value]) => {
      if (value !== null && value !== undefined && value !== "") url.searchParams.set(key, value);
    });
    const response = await fetch(url, { cache: "no-store" });
    const payload = await response.json().catch(() => null);
    if (!response.ok) {
      const message = payload && payload.error && payload.error.message;
      throw new Error(message || `LLM 调用接口返回 ${response.status}`);
    }
    return payload;
  }

  function query(cursorOverrides) {
    const result = { range: AgentMonitor.getRange(), page_size: 50 };
    filterRegistry.forEach((filter) => {
      const control = document.querySelector(`[data-filter="${filter.query}"]`);
      if (control && control.value) result[filter.query] = control.value;
    });
    const cursors = cursorOverrides || {};
    if (cursors.request !== undefined ? cursors.request : state.requestCursor) {
      result.request_cursor = cursors.request !== undefined ? cursors.request : state.requestCursor;
    }
    if (cursors.attempt !== undefined ? cursors.attempt : state.attemptCursor) {
      result.attempt_cursor = cursors.attempt !== undefined ? cursors.attempt : state.attemptCursor;
    }
    return result;
  }

  function setLoading() {
    const status = AgentMonitor.qs("#llm-state");
    status.className = "llm-state loading";
    AgentMonitor.qs("#llm-state-title").textContent = "加载中…";
    AgentMonitor.qs("#llm-state-detail").textContent = "正在读取已纳入的请求与尝试快照。";
    ["#llm-cost-body", "#llm-requests-body", "#llm-attempts-body"].forEach((selector) => {
      AgentMonitor.qs(selector).setAttribute("aria-busy", "true");
    });
  }

  function setTerminal(payload) {
    const hubScope = payload.scope && payload.scope.kind === "admitted_machines";
    AgentMonitor.qs("#llm-scope").textContent = hubScope ? "全部已纳入机器" : "仅本机";
    AgentMonitor.qs("#llm-scope-detail").textContent = hubScope
      ? "数据取自每台机器最后一次采集的快照，各机的观测时间与缺口见下方。"
      : "数据取自本页所在机器的本地账本，不含其它机器。";
    const status = AgentMonitor.qs("#llm-state");
    const sources = payload.sources || [];
    const coverage = sources.map((source) => {
      const stateLabel = source.state === "not_collected" ? "未采集明细，请更新该机 exporter"
        : source.state === "missing" ? "采集时无 gateway 账本" : "已记录";
      return `${source.machine}: ${stateLabel} · ${formatTime(source.observed_at)}`;
    }).join("; ");
    const incomplete = sources.some((source) => source.state !== "available");
    const empty = payload.requests.matching_count === null ? payload.requests.items.length === 0
      : payload.requests.matching_count === 0 && payload.attempts.matching_count === 0;
    status.className = `llm-state ${incomplete || empty ? "empty" : "data"}`;
    AgentMonitor.qs("#llm-state-title").textContent = incomplete ? "Gateway 覆盖不完整"
      : payload.ledger.state === "not_collected" ? "尚未采集 Gateway 明细"
      : payload.ledger.state === "missing" ? "采集时无 Gateway 账本"
      : empty ? "没有符合当前范围和筛选的调用" : "已记录的 LLM 调用";
    AgentMonitor.qs("#llm-state-detail").textContent = coverage || (payload.as_of
      ? `观测于 ${formatTime(payload.as_of)}${empty ? " · 试试放宽范围或清除筛选。" : ""}`
      : "还没有已纳入的统计快照，点刷新采集数据。");
  }

  function renderSummary(payload) {
    AgentMonitor.qs(".llm-kpis").hidden = !payload.request_summary;
    if (!payload.request_summary) {
      ["#llm-request-count", "#llm-success-count", "#llm-attempt-count", "#llm-unknown-cost-count"].forEach(selector => { AgentMonitor.qs(selector).textContent = "—"; });
      ["#llm-request-context", "#llm-outcome-context", "#llm-attempt-context", "#llm-cost-context"].forEach(selector => { AgentMonitor.qs(selector).textContent = "全范围统计未加载"; });
      return;
    }
    const requests = payload.request_summary;
    const attempts = payload.attempt_summary;
    AgentMonitor.qs("#llm-request-count").textContent = AgentMonitor.integer(payload.requests.matching_count);
    AgentMonitor.qs("#llm-success-count").textContent = AgentMonitor.integer(requests.successful_requests);
    AgentMonitor.qs("#llm-attempt-count").textContent = AgentMonitor.integer(payload.attempts.matching_count);
    AgentMonitor.qs("#llm-unknown-cost-count").textContent = AgentMonitor.integer(attempts.unknown_cost_attempts);
    AgentMonitor.qs("#llm-outcome-context").textContent = `拒绝 ${AgentMonitor.integer(requests.rejected_requests)} · 失败 ${AgentMonitor.integer(requests.failed_requests)} · 中断或未知 ${AgentMonitor.integer(requests.interrupted_or_unknown_requests)} · 进行中 ${AgentMonitor.integer(requests.in_progress_requests)}`;
    AgentMonitor.qs("#llm-request-context").textContent = `按请求时间 · ${human(payload.range.value)} · 已终结 ${requests.terminal_requests}`;
    AgentMonitor.qs("#llm-attempt-context").textContent = `按尝试时间 · ${human(payload.range.value)}`;
    AgentMonitor.qs("#llm-cost-context").textContent = attempts.unknown_cost_attempts ? "未计入金额小计" : "所选尝试的成本状态都是明确的";
  }

  function renderCosts(payload) {
    const body = AgentMonitor.qs("#llm-cost-body");
    clear(body);
    const rows = payload.cost_summary.monetary_subtotals;
    if (!rows.length) {
      const row = node("tr");
      const cell = node("td", "当前选择没有金额小计。", "empty-state");
      cell.colSpan = 7;
      row.appendChild(cell);
      body.appendChild(row);
    } else {
      rows.forEach((item) => {
        const row = node("tr");
        [item.currency, human(item.funding_type), human(item.cost_basis)].forEach((value) => row.appendChild(node("td", value)));
        [
          AgentMonitor.integer(item.exact_attempts),
          formatAmount(item.exact_amount, item.currency),
          AgentMonitor.integer(item.estimated_attempts),
          formatAmount(item.estimated_amount, item.currency),
        ].forEach((value) => row.appendChild(node("td", value, "numeric")));
        body.appendChild(row);
      });
    }
    body.setAttribute("aria-busy", "false");
    const noCharge = payload.cost_summary.no_per_call_charge_attempts;
    AgentMonitor.qs("#llm-no-charge").textContent = noCharge ? `${AgentMonitor.integer(noCharge)} 次尝试未报告逐调用收费` : "没有订阅额度下的免收费尝试";
  }

  function outcomePill(value) {
    let kind = "info";
    if (value === "success") kind = "ok";
    else if (["failed", "failed_pre_dispatch", "http_error", "transport_error", "timeout", "validation_error", "parse_error"].includes(value)) kind = "bad";
    else if (["local_rejected", "cancelled", "unknown", "interrupted", "interrupted_unknown"].includes(value)) kind = "warn";
    return node("span", human(value), `status-pill ${kind}`);
  }

  function cell(text, className, title) {
    const result = node("td", text, className);
    if (title) result.title = title;
    return result;
  }

  function auditLabelCell(text, className, title) {
    const value = text === null || text === undefined || text === "" ? "—" : String(text);
    const result = node("td", undefined, className);
    const label = node("span", value, "audit-label");
    label.title = title || value;
    result.appendChild(label);
    return result;
  }

  let detailSequence = 0;
  let detailOrigin = null;
  let exportSequence = 0;

  function requestLink(label, identity) {
    const result = node("td", undefined, "mono-cell");
    const button = node("button", label, "llm-request-link audit-label");
    button.type = "button";
    button.title = `查看完整请求：${label}`;
    button.setAttribute("data-request-identity", JSON.stringify(identity));
    button.addEventListener("click", () => openDetail(identity, button));
    result.appendChild(button);
    return result;
  }

  function reported(value) {
    if (value === null || value === undefined || value === "") return "未报告";
    return typeof value === "object" ? JSON.stringify(value, null, 2) : String(value);
  }

  function fields(container, entries) {
    const list = node("dl", undefined, "llm-detail-fields");
    entries.forEach(([label, value]) => {
      list.appendChild(node("dt", label));
      list.appendChild(node("dd", reported(value)));
    });
    container.appendChild(list);
  }

  function auditDisclosure(container, title, entries) {
    const details = node("details", undefined, "llm-audit-disclosure");
    details.appendChild(node("summary", title));
    fields(details, entries);
    container.appendChild(details);
  }

  function identityLabel(value) {
    return value && typeof value === "object" ? `${value.machine} / ${value.id}` : value;
  }

  function attemptRouteLabel(attempts, index) {
    if (index === 0) return "首次尝试";
    const previous = attempts[index - 1].route_id;
    const current = attempts[index].route_id;
    if (!previous || !current) return "路由关系未知";
    return previous === current ? "同路由重试" : "切换路由";
  }

  function routingSummary(container, request, attempts) {
    const section = node("section", undefined, "llm-routing-summary");
    section.appendChild(node("h3", "路由诊断"));
    section.appendChild(node("p", "本次请求的历史记录，不代表路由当前可用；尝试记录不证明服务商已收到请求，派发边界见下方证据。", "scope-note"));
    const sourceLabels = { policy: "按策略选择", explicit_pin: "调用方指定路由" };
    const allowedRoutes = request.caller_route_constraint?.allowed_routes;
    fields(section, [
      ["选择方式", sourceLabels[request.route_selection_source] || "未报告或未知"],
      ["请求模式", request.requested_mode],
      ["指定路由", request.requested_route_id || "未指定"],
      ["指定路由解析", request.requested_route_id ? request.resolved_route_id : "不适用（未指定路由）"],
      ["首次尝试路由", attempts.length ? attempts[0].route_id : "无已记录尝试"],
      ["调用方允许路由", Array.isArray(allowedRoutes)
        ? (allowedRoutes.length ? allowedRoutes.join("、") : "允许列表为空")
        : "未报告允许列表"],
    ]);
    const candidates = request.preselection_candidates;
    if (!Array.isArray(candidates) || !candidates.length) {
      section.appendChild(node("p", Array.isArray(candidates)
        ? "历史候选快照为空；不能据此推断其他路由的状态。"
        : "未记录候选快照，无法解释各候选的资格。", "scope-note"));
    } else {
      const details = node("details", undefined, "llm-routing-candidates");
      details.appendChild(node("summary", `历史候选 · ${candidates.length} 条`));
      details.appendChild(node("p", "资格与实际尝试分别记录；优先级是记录值，不是执行排名，也不能据此证明最低价或完整选择原因。", "scope-note"));
      const reasonLabels = {
        caller_route_not_allowed: "不在调用方允许的路由内",
        request_incompatible: "与本次请求配置不兼容",
        route_not_effectively_eligible: "未满足路由有效资格",
        inventory_only: "仅列入库存，未参与派发",
        availability_cooldown: "当时处于可用性冷却期",
        deployment_unknown: "当时部署状态未知",
        self_hosted_not_deployed: "自托管服务当时未部署",
        inventory_only_not_callable: "仅列入库存，不可调用",
        policy_disabled: "策略未启用",
        route_policy_disabled: "路由策略未启用",
        unassigned_billing_scope: "未分配计费范围",
        billing_scope_mismatch: "计费范围不匹配",
        not_allowed_for_project: "项目未获准使用",
        route_not_allowed_for_project: "项目未获准使用此路由",
        credential_missing: "缺少凭据",
        credential_unavailable_on_this_machine: "当时所在机器没有可用凭据",
        runtime_ineligible: "运行时不符合资格",
        provider_endpoint_missing: "缺少服务商端点",
        vertex_flex_requires_global_endpoint: "Vertex Flex 需要全球端点",
        registry_revision_mismatch: "注册配置版本不匹配",
        runtime_profile_mismatch: "运行时账号配置不匹配",
        runtime_model_mismatch: "运行时模型不匹配",
        runtime_adapter_mismatch: "运行时适配器不匹配",
        actual_model_unverified: "实际模型尚未验证",
      };
      candidates.forEach((candidate) => {
        const card = node("article", undefined, "llm-routing-candidate");
        card.appendChild(node("h4", reported(candidate.route_id)));
        const eligible = candidate.effectively_eligible;
        const matched = candidate.route_id && attempts.some((attempt) => attempt.route_id === candidate.route_id);
        const hasUnknownRoute = attempts.some((attempt) => !attempt.route_id);
        fields(card, [
          ["当时资格", eligible === true ? "符合资格" : eligible === false ? "不符合资格" : "未知"],
          ["尝试记录", matched ? "有对应尝试" : !candidate.route_id || hasUnknownRoute ? "无法确认（路由标识缺失）" : "无对应尝试"],
          ["服务商 / 模型", `${reported(candidate.provider_id)} / ${reported(candidate.actual_model)}`],
          ["优先级记录值", candidate.priority],
        ]);
        const reasons = candidate.eligibility_reasons;
        if (Array.isArray(reasons) && reasons.length) {
          const list = node("ul", undefined, "llm-routing-reasons");
          reasons.forEach((reason) => list.appendChild(node("li",
            `${reasonLabels[reason] || "未解释的记录原因"} · ${reported(reason)}`)));
          card.appendChild(list);
        } else {
          card.appendChild(node("p", "未记录具体资格原因。", "scope-note"));
        }
        details.appendChild(card);
      });
      section.appendChild(details);
    }
    container.appendChild(section);
  }

  function renderDetail(payload, container) {
    clear(container);
    const request = payload.request;
    container.appendChild(node("p", `详情快照截至：${formatTime(payload.as_of)}`, "scope-note"));
    (payload.sources || []).forEach((source) => container.appendChild(node("p",
      `${source.machine} · ${human(source.state)} · 观测于 ${formatTime(source.observed_at)}`, "scope-note")));
    fields(container, [
      ["机器", request.machine || "本机"], ["项目", request.canonical_project_id],
      ["逻辑请求", request.logical_request_id],
      ["请求时间", formatTime(request.request_timestamp)], ["逻辑模型", request.logical_model],
      ["结果", request.request_outcome], ["拒绝原因", request.request_reject_reason],
    ]);
    const attempts = request.attempts || [];
    routingSummary(container, request, attempts);
    auditDisclosure(container, "请求身份与路由证据", [
      ["会话", request.session_ref], ["调用者", request.caller_username],
      ["请求模式", request.requested_mode], ["路由选择来源", request.route_selection_source],
      ["指定路由", request.requested_route_id], ["指定路由解析", request.resolved_route_id],
      ["准入版本", request.admission_revision], ["调用方路由约束", request.caller_route_constraint],
      ["候选路由", request.preselection_candidates],
    ]);
    container.appendChild(node("h3", `完整尝试链 · ${attempts.length} 次`));
    if (!attempts.length) container.appendChild(node("p", "此请求没有已记录的服务商尝试。"));
    attempts.forEach((item, index) => {
      const section = node("section", undefined, "llm-detail-attempt");
      const title = node("h3", `#${item.attempt_no} · ${attemptRouteLabel(attempts, index)} · ${reported(item.provider_id)} · `);
      title.appendChild(outcomePill(item.outcome));
      section.appendChild(title);
      fields(section, [
        ["实际模型", item.actual_model], ["路由", item.route_id],
        ["错误类别", item.error_class], ["HTTP 状态", item.http_status],
        ["延迟", item.latency_ms == null ? null : `${Math.round(item.latency_ms)} ms`],
        ["用量", attemptUsage(item)], ["成本", attemptCost(item)],
      ]);
      auditDisclosure(section, "身份、路由与凭据来源", [
        ["尝试 ID", identityLabel(item.attempt_id)], ["上游尝试", identityLabel(item.parent_attempt_id)],
        ["时间", formatTime(item.attempt_timestamp)],
        ["派发边界", item.dispatch_boundary], ["传输", item.transport],
        ["路由来源", item.route_selection_source],
        ["路由版本", item.routing_revision], ["运行 ID", item.run_id],
        ["账号配置", item.credential_profile_id], ["账号标签", item.credential_profile_display_name],
        ["标签来源", profileSourceLabel(item.credential_profile_display_name_source)],
        ["凭据来源类型", item.credential_source_kind], ["凭据来源", item.credential_source_ref],
        ["生成控制", item.generation_controls], ["已验证能力", item.route_verified_capabilities],
      ]);
      auditDisclosure(section, "用量、成本与定价证据", [
        ["用量状态", item.usage_state], ["完整用量", item.usage],
        ["付费来源", item.funding_source], ["成本状态", item.cost_state], ["成本来源", item.cost_provenance],
        ["定价状态", item.pricing_state], ["定价依据", item.pricing_basis],
        ["定价值", item.pricing_value == null ? null : formatAmount(item.pricing_value, item.pricing_currency)],
        ["定价来源", item.pricing_source], ["定价检查时间", item.pricing_checked_at],
        ["定价生效时间", item.pricing_effective_at], ["定价有效期", item.pricing_valid_until],
      ]);
      container.appendChild(section);
    });
  }

  function closeDetail() {
    detailSequence += 1;
    const dialog = AgentMonitor.qs("#llm-detail");
    if (dialog.open) dialog.close();
    if (detailOrigin && detailOrigin.isConnected) detailOrigin.focus();
    else {
      const identity = detailOrigin?.getAttribute("data-request-identity");
      const replacement = identity && Array.from(document.querySelectorAll(".llm-main [data-request-identity]"))
        .find(button => button.getAttribute("data-request-identity") === identity);
      (replacement || AgentMonitor.qs("#llm-requests-title"))?.focus();
    }
    detailOrigin = null;
  }

  async function openDetail(identity, origin) {
    const current = AgentMonitor.pageScope();
    const sequence = ++detailSequence;
    detailOrigin = origin;
    const dialog = AgentMonitor.qs("#llm-detail");
    const content = AgentMonitor.qs("#llm-detail-content");
    clear(content);
    content.appendChild(node("p", "正在读取完整请求…"));
    if (!dialog.open) dialog.showModal();
    try {
      const payload = await apiJSON("/api/llm-call-request", identity);
      if (current() && sequence === detailSequence && dialog.open) renderDetail(payload, content);
    } catch (error) {
      if (current() && sequence === detailSequence && dialog.open) {
        clear(content);
        content.appendChild(node("p", `请求诊断不可用：${error.message}`, "error"));
      }
    }
  }

  async function exportRecords() {
    const current = AgentMonitor.pageScope();
    const sequence = ++exportSequence;
    const values = query({ request: null, attempt: null });
    delete values.page_size;
    values.kind = AgentMonitor.qs("#llm-export-kind").value;
    values.format = AgentMonitor.qs("#llm-export-format").value;
    const selection = JSON.stringify(values);
    const status = AgentMonitor.qs("#llm-export-status");
    const button = AgentMonitor.qs("#llm-export");
    const isSameSelection = () => {
      const next = query({ request: null, attempt: null });
      delete next.page_size;
      next.kind = AgentMonitor.qs("#llm-export-kind").value;
      next.format = AgentMonitor.qs("#llm-export-format").value;
      return JSON.stringify(next) === selection;
    };
    button.disabled = true;
    status.textContent = "正在导出全部匹配记录…";
    try {
      const endpoint = new URL("/api/llm-calls-export", window.location.origin);
      Object.entries(values).forEach(([key, value]) => endpoint.searchParams.set(key, value));
      const response = await fetch(endpoint, { cache: "no-store" });
      if (!response.ok) {
        const error = await response.json().catch(() => null);
        throw new Error(error?.error?.message || `LLM 导出接口返回 ${response.status}`);
      }
      // Keep large exports as bytes: parsing and re-stringifying the complete
      // envelope can fail even after a successful HTTP response.
      const blob = await response.blob();
      if (!current() || sequence !== exportSequence) return;
      const url = URL.createObjectURL(blob);
      const link = node("a");
      link.href = url;
      const filterName = filterRegistry.filter(f => values[f.query]).map(f => `${f.query}-${values[f.query]}`).join("_");
      link.download = `llm-${values.kind}_${values.range}_${filterName || "all-filters"}_${new Date().toISOString().replaceAll(":", "-")}.json`.replace(/[\/\\]/g, "-");
      link.click();
      URL.revokeObjectURL(url);
      if (isSameSelection()) status.textContent = "已导出全部匹配记录（JSON）。";
      else status.textContent = "已导出点击时的筛选；当前筛选已变化。";
    } catch (error) {
      if (current() && sequence === exportSequence) status.textContent = isSameSelection() ? `导出失败：${error.message}` : "上一次筛选的导出失败；可导出当前筛选。";
    } finally {
      if (current() && sequence === exportSequence) button.disabled = false;
    }
  }

  function bindDiagnostics() {
    const dialog = AgentMonitor.qs("#llm-detail");
    // All listeners belong to main's subtree; SPA removal also removes the
    // modal. pageScope prevents pending responses from touching its successor.
    dialog.addEventListener("cancel", (event) => { event.preventDefault(); closeDetail(); });
    AgentMonitor.qs("#llm-detail-close").addEventListener("click", closeDetail);
    AgentMonitor.qs("#llm-requests-title").setAttribute("tabindex", "-1");
    AgentMonitor.qs("#llm-export").addEventListener("click", exportRecords);
  }

  function renderRequests(payload) {
    const body = AgentMonitor.qs("#llm-requests-body");
    clear(body);
    const items = payload.requests.items;
    if (!items.length) {
      const row = node("tr");
      const message = node("td", "本页没有逻辑请求。", "empty-state");
      message.colSpan = 10;
      row.appendChild(message);
      body.appendChild(row);
    } else {
      items.forEach((item) => {
        const row = node("tr");
        row.appendChild(cell(formatTime(item.request_timestamp), "nowrap"));
        row.appendChild(cell(item.machine || "本机"));
        row.appendChild(auditLabelCell(item.canonical_project_id, "project-cell"));
        row.appendChild(auditLabelCell(item.session_ref, "session-cell"));
        row.appendChild(requestLink(item.logical_request_id, { machine: item.machine, project: item.canonical_project_id, logical_request_id: item.logical_request_id }));
        row.appendChild(auditLabelCell(item.logical_model, "model-cell"));
        const outcome = node("td"); outcome.appendChild(outcomePill(item.request_outcome)); row.appendChild(outcome);
        row.appendChild(auditLabelCell(item.requested_route_id || human(item.route_selection_source), "route-cell"));
        row.appendChild(cell(AgentMonitor.integer(item.matching_attempt_count), "numeric"));
        row.appendChild(cell(AgentMonitor.integer(item.total_attempt_count), "numeric"));
        body.appendChild(row);
      });
    }
    body.setAttribute("aria-busy", "false");
    AgentMonitor.qs("#llm-requests-meta").textContent = payload.requests.matching_count === null
      ? `本页 ${items.length} 条请求 · 全范围总数未加载`
      : `匹配 ${AgentMonitor.integer(payload.requests.matching_count)} 条请求`;
    const filtered = payload.request_selection.attempt_filter_relation === "matching_child_in_attempt_time_range";
    AgentMonitor.qs("#llm-request-selection").textContent = filtered ? "按请求时间排列，并关联尝试时间范围内的匹配尝试。" : "按请求时间排列；未启用尝试级筛选。";
    AgentMonitor.qs("#llm-requests-prev").disabled = state.requestHistory.length === 0;
    AgentMonitor.qs("#llm-requests-next").disabled = !payload.requests.next_cursor;
    AgentMonitor.qs("#llm-requests-page").textContent = `第 ${state.requestPage} 页`;
  }

  function attemptUsage(item) {
    if (item.usage_state !== "reported" || !item.usage) return "未报告";
    return `${AgentMonitor.integer(item.usage.total_tokens)} token`;
  }

  function attemptCost(item) {
    if (!item.cost_state) return "未报告";
    if (item.cost_state === "unknown") return "未知";
    if (item.cost_state === "not_incurred") return ["company_subscription", "personal_subscription", "subscription_unassigned"].includes(item.funding_source) ? "未报告逐调用收费" : "未产生费用";
    return `${formatAmount(item.cost_value, item.cost_currency)} · ${human(item.cost_state)} · ${human(item.cost_basis)}`;
  }

  function renderAttempts(payload) {
    const body = AgentMonitor.qs("#llm-attempts-body");
    clear(body);
    const items = payload.attempts.items;
    if (!items.length) {
      const row = node("tr");
      const message = node("td", "本页没有服务商尝试。", "empty-state");
      message.colSpan = 15;
      row.appendChild(message);
      body.appendChild(row);
    } else {
      items.forEach((item) => {
        const row = node("tr");
        row.appendChild(cell(formatTime(item.attempt_timestamp), "nowrap"));
        row.appendChild(cell(item.machine || "本机"));
        row.appendChild(requestLink(`${item.parent_request.canonical_project_id} · ${item.parent_request.logical_request_id}`, {
          machine: item.machine || item.parent_request.machine,
          attempt_id: typeof item.attempt_id === "object" ? item.attempt_id.id : item.attempt_id,
        }));
        const attemptLabel = `#${item.attempt_no} · ${human(item.route_selection_source)}`;
        const attemptTitle = item.parent_attempt_id ? `${attemptLabel} · 上游 ${typeof item.parent_attempt_id === "object" ? `${item.parent_attempt_id.machine}: ${item.parent_attempt_id.id}` : item.parent_attempt_id}` : attemptLabel;
        row.appendChild(auditLabelCell(attemptLabel, "attempt-cell", attemptTitle));
        row.appendChild(cell(item.provider_id));
        const profileSource = profileSourceLabel(item.credential_profile_display_name_source);
        const profileLabel = item.credential_profile_display_name ? `${item.credential_profile_display_name} (${item.credential_profile_id}) · ${profileSource}` : `${item.credential_profile_id} · ${profileSource}`;
        row.appendChild(auditLabelCell(profileLabel, "profile-cell"));
        row.appendChild(auditLabelCell(`${human(item.credential_source_kind)} · ${item.credential_source_ref}`, "credential-cell"));
        row.appendChild(auditLabelCell(human(item.funding_source), "scope-funding-cell"));
        row.appendChild(auditLabelCell(item.route_id, "route-cell"));
        row.appendChild(auditLabelCell(item.actual_model, "model-cell"));
        const outcome = node("td"); outcome.appendChild(outcomePill(item.outcome)); row.appendChild(outcome);
        row.appendChild(cell(item.latency_ms === null ? "未报告" : `${Math.round(item.latency_ms)} ms`, "numeric"));
        row.appendChild(cell(attemptUsage(item)));
        row.appendChild(cell(attemptCost(item), item.cost_state === "unknown" ? "cost-unknown" : ""));
        const pricing = item.pricing_basis ? `${human(item.pricing_state)} · ${human(item.pricing_basis)}` : human(item.pricing_state);
        row.appendChild(cell(pricing));
        body.appendChild(row);
      });
    }
    body.setAttribute("aria-busy", "false");
    AgentMonitor.qs("#llm-attempts-meta").textContent = `匹配 ${AgentMonitor.integer(payload.attempts.matching_count)} 次尝试`;
    AgentMonitor.qs("#llm-attempts-prev").disabled = state.attemptHistory.length === 0;
    AgentMonitor.qs("#llm-attempts-next").disabled = !payload.attempts.next_cursor;
    AgentMonitor.qs("#llm-attempts-page").textContent = `第 ${state.attemptPage} 页`;
  }

  function render(payload) {
    renderFilterSelection();
    state.payload = payload;
    setTerminal(payload);
    renderSummary(payload);
    renderRequests(payload);
    if (payload.request_summary) {
      renderCosts(payload);
      renderAttempts(payload);
      AgentMonitor.qs("#llm-analysis-status").textContent = "统计、请求与尝试来自同一次读取。";
    } else {
      ["#llm-cost-body", "#llm-attempts-body"].forEach(selector => {
        const body = AgentMonitor.qs(selector); clear(body);
        const row = node("tr"), cell = node("td", "尚未加载；点击“加载全范围统计与尝试”。", "empty-state");
        cell.colSpan = selector === "#llm-cost-body" ? 7 : 15; row.appendChild(cell); body.appendChild(row);
        body.setAttribute("aria-busy", "false");
      });
      AgentMonitor.qs("#llm-no-charge").textContent = "—";
      AgentMonitor.qs("#llm-attempts-meta").textContent = "尚未加载";
      ["#llm-attempts-prev", "#llm-attempts-next"].forEach(selector => { AgentMonitor.qs(selector).disabled = true; });
      AgentMonitor.qs("#llm-analysis-status").textContent = "当前仅加载请求；全范围统计尚未加载。";
    }
  }

  function clearAuditData() {
    state.payload = null;
    resetPages();
    ["#llm-cost-body", "#llm-requests-body", "#llm-attempts-body"].forEach((selector) => {
      const body = AgentMonitor.qs(selector);
      clear(body);
      body.setAttribute("aria-busy", "false");
    });
    [
      "#llm-request-count", "#llm-success-count", "#llm-attempt-count",
      "#llm-unknown-cost-count", "#llm-outcome-context", "#llm-request-context",
      "#llm-attempt-context", "#llm-cost-context", "#llm-no-charge",
      "#llm-requests-meta", "#llm-attempts-meta", "#llm-request-selection",
    ].forEach((selector) => { AgentMonitor.qs(selector).textContent = "—"; });
    [
      "#llm-requests-prev", "#llm-requests-next",
      "#llm-attempts-prev", "#llm-attempts-next",
    ].forEach((selector) => { AgentMonitor.qs(selector).disabled = true; });
    AgentMonitor.qs("#llm-requests-page").textContent = "第 1 页";
    AgentMonitor.qs("#llm-attempts-page").textContent = "第 1 页";
  }

  function setError(error) {
    clearAuditData();
    const status = AgentMonitor.qs("#llm-state");
    status.className = "llm-state error";
    AgentMonitor.qs("#llm-state-title").textContent = "LLM 调用审计不可用";
    AgentMonitor.qs("#llm-state-detail").textContent = error.message || "刷新重试。";
  }

  async function load(full = false, background = false) {
    if (state.pageLoadSequence !== null) return;
    const current = AgentMonitor.pageScope();
    const sequence = ++state.loadSequence;
    if (full) state.analysisSequence = sequence;
    setLoading();
    try {
      const values = query();
      if (!full) delete values.attempt_cursor;
      const response = await apiJSON(full ? "/api/llm-calls" : "/api/llm-call-list", values);
      if (!current() || sequence !== state.loadSequence) return;
      const payload = full ? response : response.calls;
      if (background && state.payload?.request_summary && JSON.stringify(payload.sources) === JSON.stringify(state.payload.sources) && JSON.stringify(payload.high_watermark) === JSON.stringify(state.payload.high_watermark) && payload.as_of === state.payload.as_of && payload.range.start_at === state.payload.range.start_at) {
        setTerminal(state.payload);
        ["#llm-cost-body", "#llm-requests-body", "#llm-attempts-body"].forEach(selector => AgentMonitor.qs(selector).setAttribute("aria-busy", "false"));
        return;
      }
      if (!full) populateFilters(response.selection_options, state.optionsLoaded);
      render(payload);
    } catch (error) {
      if (current() && sequence === state.loadSequence) setError(error);
    } finally {
      if (state.analysisSequence === sequence) state.analysisSequence = null;
    }
  }

  function option(select, value, label) {
    const item = node("option", label === undefined ? human(value) : label);
    item.value = value;
    select.appendChild(item);
  }

  function populate(selector, values, selected) {
    const select = AgentMonitor.qs(selector);
    const first = select.options[0].cloneNode(true);
    clear(select);
    select.appendChild(first);
    select.value = "";
    values.forEach((value) => option(select, value, value));
    if (selected && values.includes(selected)) select.value = selected;
  }

  function profileSourceLabel(source) {
    if (source === "source_registry_not_collected") return "来源机未采集该标签";
    if (source === "current_registry") return "当前 registry 标签";
    if (source === "registry_unreadable") return "当前 registry 不可读";
    if (source === "current_registry_missing_profile") return "当前标签不可用";
    return "当前标签不可用";
  }

  function profileOptionLabel(item) {
    const source = profileSourceLabel(item.display_name_source);
    return item.display_name ? `${item.display_name} (${item.id}) · ${source}` : `${item.id} · ${source}`;
  }

  function populateProfiles(selector, values, selected) {
    const select = AgentMonitor.qs(selector);
    const first = select.options[0].cloneNode(true);
    clear(select);
    select.appendChild(first);
    select.value = "";
    values.forEach((item) => option(select, item.id, profileOptionLabel(item)));
    if (selected && values.some((item) => item.id === selected)) select.value = selected;
  }

  function populateFilters(data, preserveOptions = false) {
    const request = data.request_dimensions || {};
    const attempt = data.attempt_dimensions || {};
    const current = AgentMonitor.params();
    filterRegistry.forEach((filter) => {
      const dimensions = filter.scope === "request" ? request : attempt;
      const values = dimensions[filter.source] || [];
      const selected = current.get(filter.query);
      if (!preserveOptions) {
        if (filter.kind === "profile") populateProfiles(filter.selector, values, selected);
        else populate(filter.selector, values, selected);
      }
      const selectedAvailable = filter.kind === "profile"
        ? values.some((item) => item.id === selected)
        : values.includes(selected);
      if (selected && !selectedAvailable) {
        AgentMonitor.setParam(filter.query, "");
        AgentMonitor.qs(filter.selector).value = "";
      }
    });
  }

  async function loadPage() {
    const pageCurrent = AgentMonitor.pageScope();
    const sequence = ++state.loadSequence;
    state.analysisSequence = null;
    state.pageLoadSequence = sequence;
    AgentMonitor.qs("#llm-load-analysis").disabled = true;
    const range = AgentMonitor.getRange();
    const values = { range, page_size: 50 };
    const params = AgentMonitor.params();
    filterRegistry.forEach((filter) => {
      const selected = params.get(filter.query);
      if (selected) values[filter.query] = selected;
    });
    const current = () => pageCurrent() && sequence === state.loadSequence && range === AgentMonitor.getRange();
    setLoading();
    ["requests", "attempts"].forEach((kind) => {
      AgentMonitor.qs(`#llm-${kind}-next`).disabled = true;
      AgentMonitor.qs(`#llm-${kind}-prev`).disabled = true;
    });
    try {
      const payload = await apiJSON("/api/llm-call-list", values);
      if (!current()) return;
      populateFilters(payload.selection_options, state.optionsLoaded);
      render(payload.calls);
    } catch (error) {
      if (current()) setError(error);
    } finally {
      if (state.pageLoadSequence === sequence) {
        state.pageLoadSequence = null;
        if (pageCurrent()) AgentMonitor.qs("#llm-load-analysis").disabled = false;
      }
    }
  }

  function loadSelection() {
    // A selection may supersede calls, but must also finish loading the
    // current range's options when its combined page is still in flight.
    return loadPage();
  }

  async function loadOptions() {
    const current = AgentMonitor.pageScope(), range = AgentMonitor.getRange();
    const sequence = ++state.optionsSequence, reload = state.reloadSequence;
    const status = AgentMonitor.qs("#llm-options-status");
    status.textContent = "正在加载完整选项…";
    try {
      const data = await apiJSON("/api/llm-call-filters", { range });
      if (!current() || sequence !== state.optionsSequence || range !== AgentMonitor.getRange() || reload !== state.reloadSequence) return;
      populateFilters(data); state.optionsLoaded = true;
      status.textContent = "完整筛选选项已加载。";
      resetPages(); await loadPage();
    } catch (error) {
      if (current() && sequence === state.optionsSequence && reload === state.reloadSequence) status.textContent = `筛选选项加载失败：${error.message}；可重试。`;
    }
  }

  function resetPages() {
    state.requestCursor = null; state.attemptCursor = null;
    state.requestHistory = []; state.attemptHistory = [];
    state.requestPage = 1; state.attemptPage = 1;
  }

  function renderFilterSelection() {
    const selected = filterRegistry.map(filter => {
      const control = document.querySelector(`[data-filter="${filter.query}"]`);
      return control?.value ? `${document.querySelector(`label[for="${filter.selector.slice(1)}"]`)?.textContent || filter.query}: ${control.value}` : null;
    }).filter(Boolean);
    AgentMonitor.qs("#llm-active-filters").textContent = selected.length ? selected.join(" · ") : "未启用筛选";
  }

  function bindFilters() {
    AgentMonitor.qs("#llm-filter-disclosure").open = !window.matchMedia?.("(max-width: 1000px)").matches;
    renderFilterSelection();
    filterRegistry.forEach((filter) => {
      const control = document.querySelector(`[data-filter="${filter.query}"]`);
      control.addEventListener("change", () => {
        state.reloadSequence += 1;
        AgentMonitor.setParam(filter.query, control.value);
        resetPages();
        renderFilterSelection();
        loadSelection();
      });
    });
    AgentMonitor.qs("#llm-clear-filters").addEventListener("click", () => {
      state.reloadSequence += 1;
      filterRegistry.forEach((filter) => {
        const control = document.querySelector(`[data-filter="${filter.query}"]`);
        control.value = "";
        AgentMonitor.setParam(filter.query, "");
      });
      resetPages();
      renderFilterSelection();
      loadSelection();
    });
  }

  function bindPager(kind) {
    const lower = kind.toLowerCase();
    AgentMonitor.qs(`#llm-${lower}-next`).addEventListener("click", () => {
      if (state.pageLoadSequence !== null) return;
      const section = state.payload[lower];
      if (!section.next_cursor) return;
      state.reloadSequence += 1;
      const cursorKey = kind === "requests" ? "requestCursor" : "attemptCursor";
      const historyKey = kind === "requests" ? "requestHistory" : "attemptHistory";
      const pageKey = kind === "requests" ? "requestPage" : "attemptPage";
      state[historyKey].push(state[cursorKey]);
      state[cursorKey] = section.next_cursor;
      state[pageKey] += 1;
      load(Boolean(state.payload?.request_summary));
    });
    AgentMonitor.qs(`#llm-${lower}-prev`).addEventListener("click", () => {
      if (state.pageLoadSequence !== null) return;
      const cursorKey = kind === "requests" ? "requestCursor" : "attemptCursor";
      const historyKey = kind === "requests" ? "requestHistory" : "attemptHistory";
      const pageKey = kind === "requests" ? "requestPage" : "attemptPage";
      if (!state[historyKey].length) return;
      state.reloadSequence += 1;
      state[cursorKey] = state[historyKey].pop();
      state[pageKey] -= 1;
      load(Boolean(state.payload?.request_summary));
    });
  }

  let activeRefreshes = 0;
  async function observeSync(current) {
    const sync = await apiJSON("/api/sync-status");
    if (!current()) return;
    AgentMonitor.renderSyncStatus(sync);
    if (sync.syncing || sync.queued_refresh) {
      const terminal = await AgentMonitor.waitForSyncTerminal(sync, { onProgress: () => load(false, true), isCurrent: current });
      if (terminal.polling_error) throw new Error(terminal.polling_error);
      if (current()) await load(false, true);
    }
  }

  async function reloadRange(force = false) {
    const pageCurrent = AgentMonitor.pageScope();
    const reload = ++state.reloadSequence;
    const current = () => pageCurrent() && reload === state.reloadSequence;
    // Invalidate any old page/list response before refresh awaits network I/O.
    state.loadSequence += 1;
    state.optionsSequence += 1;
    state.optionsLoaded = false;
    AgentMonitor.qs("#llm-options-status").textContent = "当前仅显示已选条件。";
    resetPages();
    activeRefreshes += 1;
    try {
      if (force === true) {
        await AgentMonitor.refreshStatistics({ onProgress: () => load(false, true), isCurrent: current });
      }
      if (!current()) return;
      await loadPage();
      if (current()) await observeSync(current);
    } catch (error) {
      if (current()) setError(error);
      return;
    } finally {
      activeRefreshes -= 1;
    }
  }

  async function init() {
    AgentMonitor.bindShell(reloadRange);
    bindFilters();
    bindDiagnostics();
    bindPager("requests");
    bindPager("attempts");
    AgentMonitor.qs("#llm-load-analysis").addEventListener("click", () => {
      if (state.pageLoadSequence !== null) return;
      state.reloadSequence += 1;
      AgentMonitor.qs("#llm-analysis-status").textContent = "正在加载全范围统计与尝试…";
      load(true);
    });
    AgentMonitor.qs("#llm-load-options").addEventListener("click", loadOptions);
    [".llm-cost-panel", ".llm-attempt-panel"].forEach(selector => {
      const section = AgentMonitor.qs(selector);
      section.addEventListener("toggle", () => {
        if (section.open && state.payload && !state.payload.request_summary && state.analysisSequence === null) {
          state.reloadSequence += 1;
          load(true);
        }
      });
    });
    await reloadRange();
    // Page-scoped, so leaving this tab stops it. Registered through AgentMonitor
    // rather than window: with client navigation the document outlives the
    // page, and a raw setInterval here would accumulate one live poller per
    // visit, each still writing into a <main> that has been replaced.
    AgentMonitor.watchPageData(async () => {
      if (activeRefreshes || state.pageLoadSequence !== null || state.analysisSequence !== null) return;
      const pageCurrent = AgentMonitor.pageScope();
      const reload = state.reloadSequence;
      const current = () => pageCurrent() && reload === state.reloadSequence;
      activeRefreshes += 1;
      try {
        await load(false, true);
        if (current()) await observeSync(current);
      } catch (error) {
        if (current()) setError(error);
      } finally {
        activeRefreshes -= 1;
      }
    });
  }

  window.AgentMonitorLLMCalls = { init };
})();
