"""Explicit, private Codex account actions, separate from machine collection."""

import atexit
import copy
import fcntl
import json
import math
import os
from pathlib import Path
import selectors
import secrets
import shutil
import signal
import subprocess
import threading
import time
import uuid
from datetime import datetime, timezone
from urllib.parse import urlparse

from parsers.accounts import codex_account
from parsers.codex import select_codex_rate_limit
from upstream_controls import Controls, Paused, Busy
from codex_messages import MESSAGES
import network_policy
import client_network


MESSAGE = f"从 {len(MESSAGES)} 条日常短消息中独立随机抽取一条，可能重复。"
ACTIVE = {"queued", "checking", "login_queued", "login", "sending", "reading"}
LOGIN_SECONDS = 600
SEND_SECONDS = 120
# Local startup pacing, not a provider rate limit or an anti-abuse guarantee.
START_INTERVAL_SECONDS = 2
INTERRUPT_SECONDS = 2


class ActionError(Exception):
    def __init__(self, message, status=400, category="unknown"):
        super().__init__(message)
        self.status = status
        self.category = category


class Cancelled(ActionError):
    pass


class LoginRateLimited(ActionError):
    def __init__(self):
        super().__init__("OpenAI 登录服务限流（429），本次授权已停止，设备码不可再用于此操作。请按服务端指引等待，再解除本地限流暂停；本次未发送消息。", 429, "loginRateLimited")


# Exact, data-free WorkspaceRoutingError strings from Codex 0.162.1,
# 092d3acd6bec3e3a14bdc7e7a2810ab628ab759d. See ADR 20261023-b72e.
# These explain a failure; they never decide admission, retry or recovery.
ACCOUNT_READ_ERRORS = {
    "failed to load workspace requirements": ("requirementsLoad", "无法加载工作区配置要求，请检查服务主机的 Codex 配置。"),
    "workspace routing requires a ChatGPT account id": ("missingAccountId", "登录态缺少 ChatGPT 工作区标识，请核对所选账号的登录状态。"),
    "workspace routing discovery cancelled": ("discoveryCancelled", "工作区路由查询已取消。"),
    "account changed during workspace routing discovery": ("accountChanged", "查询期间登录账号发生变化，请先核对当前账号。"),
    "workspace routing discovery unauthorized (401)": ("discoveryUnauthorized", "工作区路由查询的认证被拒绝（401），请核对账号登录状态；此错误不能证明账号被封禁。"),
    "workspace routing discovery failed": ("discoveryFailed", "工作区路由查询失败，上游未提供更具体原因；请检查服务主机到 OpenAI 的连接及账号状态。"),
    "selected workspace missing from routing discovery": ("missingWorkspace", "返回的工作区列表中没有所选工作区，请核对登录账号与工作区访问权限。"),
    "duplicate workspace in routing discovery": ("duplicateWorkspace", "上游返回了重复的工作区，无法安全确定账号路由。"),
    "failed to reload workspace requirements": ("requirementsReload", "无法重新加载工作区配置要求，请检查服务主机的 Codex 配置。"),
    "configuration changed during workspace routing discovery; retry account/read": ("configurationChanged", "查询期间 Codex 配置发生变化，请先确认配置稳定；本次未自动重试。"),
    "invalid model provider URL": ("invalidProviderUrl", "模型服务地址无效，请检查服务主机的 Codex 配置。"),
    "invalid ChatGPT backend URL": ("invalidBootstrapUrl", "ChatGPT 服务地址无效，请检查服务主机的 Codex 配置。"),
    "invalid session ChatGPT backend URL": ("invalidSessionBootstrapUrl", "会话的 ChatGPT 服务地址无效，请检查服务主机的 Codex 配置。"),
    "ChatGPT backend changed; start a new thread before sending more content": ("sessionBackendChanged", "ChatGPT 服务地址已变化，原会话不能继续发送；本次未自动创建会话。"),
    "workspace routing discovery cancelled during shutdown": ("shutdown", "Codex 正在关闭，工作区路由查询已取消。"),
    "workspace routing discovery timed out": ("discoveryTimeout", "工作区路由查询超时，请检查服务主机到 OpenAI 的连接；超时不能证明账号状态异常。"),
    "workspace routing discovery missing backend origin": ("missingBackendOrigin", "工作区路由响应缺少服务地址，无法安全继续。"),
    "workspace routing discovery has invalid account routing override": ("invalidRoutingOverride", "工作区路由响应中的路由设置无效，无法安全继续。"),
    "workspace routing discovery must return an origin": ("backendIsNotOrigin", "工作区路由响应中的服务地址格式不符合要求，无法安全继续。"),
    "required ChatGPT backend conflicts with workspace routing": ("backendConflict", "配置要求的 ChatGPT 服务地址与工作区路由冲突，请核对服务主机配置。"),
    "invalid workspace backend URL": ("invalidBackendUrl", "工作区返回的服务地址无效，无法安全继续。"),
    "workspace backend must use an HTTPS origin without credentials": ("invalidBackendOrigin", "工作区服务地址不符合 HTTPS 安全要求，无法安全继续。"),
}


# Only structured protocol discriminants enter public/persisted failure data.
TURN_FAILURES = {
    "usageLimitExceeded": "账号额度已用尽。",
    "rateLimitExceeded": "服务端限流。",
    "unauthorized": "服务端拒绝了账号认证。",
    "serverOverloaded": "服务端繁忙。",
    "internalServerError": "服务端内部错误。",
    "httpConnectionFailed": "HTTP 连接失败。",
    "responseStreamConnectionFailed": "响应流连接失败。",
    "responseStreamDisconnected": "响应流中断。",
    "responseTooManyFailedAttempts": "响应重试次数已耗尽。",
    "contextWindowExceeded": "请求超过上下文窗口。",
    "sessionBudgetExceeded": "会话预算已耗尽。",
    "badRequest": "服务端拒绝了请求参数。",
}

REACHED_LIMITS = {"rate_limit_reached", "workspace_owner_credits_depleted",
                  "workspace_member_credits_depleted", "workspace_owner_usage_limit_reached",
                  "workspace_member_usage_limit_reached"}
RESTRICTION_DETAILS = {
    "authentication": "认证被拒绝，已暂停此账号操作。请先核对账号状态；清除登录态不会解除暂停。",
    "unknown": "上次操作结果未能安全确认，已暂停此账号。请核对原始结果后明确解除本地暂停；不会自动重发。",
    "quota": "服务端明确限制了额度使用，已暂停发送。可仅刷新配额；只有服务端明确允许使用后才解除此暂停，0% 或重置时间经过不代表恢复。",
    "rate_limit": "消息请求遇到限流，已暂停此账号操作。请按服务端指引等待，再解除本地限流暂停。",
    "login_rate_limit": "登录服务遇到限流，已暂停此账号操作。请按服务端指引等待，再解除本地限流暂停。",
}


def restriction_key(failure):
    category, status = failure.get("category"), failure.get("http_status")
    if category == "unauthorized" or status in (401, 403):
        return "authentication"
    if category == "loginRateLimited":
        return "login_rate_limit"
    if category == "rateLimitExceeded" or status == 429:
        return "rate_limit"
    if category == "usageLimitExceeded":
        return "quota"
    return None


def quota_denied(reading):
    return (reading.get("ordinary_usage_allowed") is False or reading.get("spend_control_reached") is True
            or reading.get("rate_limit_reached_type") is not None)


def safe_protocol_id(value):
    # IDs have a protocol-defined shape. Never persist arbitrary upstream strings.
    if not isinstance(value, str):
        return None
    if value.isascii() and value.isdecimal() and len(value) <= 20:
        return value
    try:
        return str(uuid.UUID(value)) if str(uuid.UUID(value)) == value else None
    except ValueError:
        return None


def turn_failure(error):
    info = error.get("codexErrorInfo") if isinstance(error, dict) else None
    category = info if isinstance(info, str) else next(iter(info), None) if isinstance(info, dict) and len(info) == 1 else None
    result = {"stage": "sending", "category": category if category in TURN_FAILURES else "unknown"}
    if isinstance(info, dict) and category in TURN_FAILURES:
        data = info[category]
        status = data.get("httpStatusCode") if isinstance(data, dict) else None
        if type(status) is int and 100 <= status <= 599:
            result["http_status"] = status
    return result


# Codex rust-v0.162.1 temporary_structured_request.rs plus the independent
# sleep/environment-advertisement flags. Model-catalog internal tools can remain;
# this is not a universal "zero tools" switch.
RESTRICTED_FEATURES = (
    "apps", "code_mode", "code_mode_only", "code_mode_host", "context_management",
    "current_time_reminder", "deferred_executor", "enable_fanout", "goals", "hooks",
    "image_generation", "memories", "multi_agent", "multi_agent_v2", "plugins",
    "request_permissions_tool", "shell_snapshot", "shell_tool", "standalone_web_search",
    "token_budget", "tool_suggest", "unified_exec", "view_image", "sleep_tool",
    "stable_environment_tools", "send_message_to_user_async",
)
RESTRICTED_CONFIG = {"features." + name: False for name in RESTRICTED_FEATURES}
RESTRICTED_CONFIG.update({"cloud.skills.enabled": False, "skills.include_instructions": False,
                         "tools.experimental_request_user_input.enabled": False,
                         "tools.update_plan.enabled": False, "web_search": "disabled"})


def restricted_thread_options(response):
    """Verify visible constraints and construct the producer's scoped overlay.

    HOME does not remove system/managed layers. Unverifiable or conflicting
    constraints stop the action; they are never silently overridden here.
    """
    failure = "无法核验 Codex 受限配置，或系统策略与本次操作约束冲突；未发送消息。"
    config, layers = response.get("config"), response.get("layers")
    if not isinstance(config, dict) or not isinstance(layers, list) or not layers:
        raise ActionError(failure, 409)
    features = config.get("features")
    def disabled(value):
        return value is False or isinstance(value, dict) and value.get("enabled") is False
    if (not isinstance(features, dict) or any(not disabled(features.get(name)) for name in RESTRICTED_FEATURES)
            or config.get("web_search") != "disabled"):
        raise ActionError(failure, 409)
    active_layers = []
    for layer in layers:
        if not isinstance(layer, dict) or not isinstance(layer.get("config"), dict):
            raise ActionError(failure, 409)
        if not layer.get("disabledReason"):
            active_layers.append(layer)
    # ConfigReadResponse.ToolsV2 omits these keys. Its absence is not false;
    # inspect the high-to-low raw layers supplied by the fixed producer.
    for tool in ("update_plan", "experimental_request_user_input"):
        observed = None
        for layer in active_layers:
            tools = layer["config"].get("tools", {})
            if isinstance(tools, dict) and isinstance(tools.get(tool), dict) and "enabled" in tools[tool]:
                observed = tools[tool]["enabled"]
                break
        if observed is not False:
            raise ActionError(failure, 409)
    servers = config.get("mcp_servers")
    if servers is None:
        servers = {}
    if not isinstance(servers, dict) or any(not isinstance(value, dict) for value in servers.values()):
        raise ActionError(failure, 409)
    managed = {"mdm", "legacyManagedConfigTomlFromFile", "legacyManagedConfigTomlFromMdm", "enterpriseManaged"}
    for layer in active_layers:
        if (layer.get("name") or {}).get("type") in managed:
            declared = layer["config"].get("mcp_servers", {})
            if not isinstance(declared, dict):
                raise ActionError(failure, 409)
            if any(isinstance(value, dict) and value.get("enabled") is True for value in declared.values()):
                raise ActionError(failure, 409)
    profile = config.get("default_permissions")
    if profile is not None and (not isinstance(profile, str) or not profile):
        raise ActionError(failure, 409)
    profile = profile if profile and not profile.startswith(":") else None
    overlay = dict(RESTRICTED_CONFIG)
    overlay["mcp_servers"] = {name: {"enabled": False} for name in servers}
    options = {"config": overlay, "environments": [], "dynamicTools": [],
               "selectedCapabilityRoots": [], "runtimeWorkspaceRoots": []}
    if profile:
        options["permissions"] = profile
    else:
        overlay["default_permissions"] = ":read-only"
        options["sandbox"] = "read-only"
    return options, profile


def verify_restricted_thread(response, profile):
    if (not isinstance(response.get("sandbox"), dict) or response["sandbox"].get("type") != "readOnly"
            or profile and (response.get("activePermissionProfile") or {}).get("id") != profile):
        raise ActionError("Codex 未保留所需权限配置与只读限制，未发送消息。", 409)


def now():
    return datetime.now(timezone.utc).isoformat()


def private_dir(path):
    if path.is_symlink():
        raise ActionError("账号目录不可使用符号链接。", 500)
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.chmod(0o700)
    return path


def write_record(path, value):
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with os.fdopen(os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as out:
            json.dump(value, out, ensure_ascii=False)
            out.flush()
            os.fsync(out.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def lock_file(path):
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    handle = os.fdopen(fd, "a+")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        raise ActionError("这个账号已有操作正在进行，请等待或取消。", 409)
    return handle


def child_environment(home, scope=None):
    # Inherit network transport settings, not API keys, alternate auth or a
    # parent's Codex session/configuration. Resolve executables before HOME changes.
    names = ("PATH", "LANG", "LC_ALL", "TMPDIR", "SSL_CERT_FILE", "SSL_CERT_DIR",
             "CODEX_CA_CERTIFICATE", "http_proxy", "https_proxy", "all_proxy", "no_proxy",
             "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY", "AGENT_PROXY_ADDR")
    env = {key: os.environ[key] for key in names if key in os.environ}
    env["PATH"] = os.pathsep.join([str(Path.home() / ".local/bin"), "/opt/homebrew/bin",
                                  "/usr/local/bin", env.get("PATH", "/usr/bin:/bin")])
    env.update(HOME=str(home / "home"), CODEX_HOME=str(home / "codex"))
    return network_policy.child_environment(env, scope=scope)


class Rpc:
    """One owned app-server; private user directories, system policy retained."""

    def __init__(self, home, cancelled):
        self.cancelled = cancelled
        self.sequence = 0
        self.pending = []
        self.diagnostics = {}
        self.buffer = b""
        self.process_guard = threading.Lock()
        for name in ("home", "codex", "work"):
            private_dir(home / name)
        try:
            env = child_environment(home, scope=uuid.uuid4().hex)
        except network_policy.NetworkPolicyError as exc:
            raise ActionError(str(exc), 503, "networkPolicyUnavailable") from None
        self.network_route = env[network_policy.INTERNAL]
        shim = Path.home() / ".local/libexec/agent-shims/codex"
        executable = str(shim) if shim.is_file() else shutil.which("codex", path=env["PATH"])
        if not executable:
            raise ActionError("服务所在机器未找到 Codex CLI，请安装后重试。", 503, "missingCli")
        config = {
            "cli_auth_credentials_store": '"file"', "forced_login_method": '"chatgpt"',
            "model_provider": '"agent-monitor-http"', "web_search": '"disabled"',
            # Built-in provider IDs cannot be overridden. The OpenAI name and
            # first-party auth retain ChatGPT routing; only this child uses HTTP.
            "model_providers.agent-monitor-http.name": '"OpenAI"',
            "model_providers.agent-monitor-http.wire_api": '"responses"',
            "model_providers.agent-monitor-http.requires_openai_auth": "true",
            "model_providers.agent-monitor-http.supports_websockets": "false",
            "model_providers.agent-monitor-http.request_max_retries": "0",
            "model_providers.agent-monitor-http.stream_max_retries": "0",
            "features.shell_tool": "false", "features.code_mode": "false",
            "features.respect_system_proxy": "false",
            "features.code_mode_host": "false", "features.multi_agent": "false",
            "features.hooks": "false", "features.apps": "false", "features.plugins": "false",
            "project_doc_max_bytes": "0",
        }
        config.update({key: json.dumps(value) for key, value in RESTRICTED_CONFIG.items()})
        command = [executable, "app-server", "--listen", "stdio://"]
        for key, value in config.items():
            command.extend(["-c", key + "=" + value])
        self.process = subprocess.Popen(command, cwd=home / "work", env=env,
                                        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.DEVNULL, start_new_session=True, umask=0o077)
        self.selector = selectors.DefaultSelector()
        self.selector.register(self.process.stdout, selectors.EVENT_READ)
        self.home = home

    def send(self, value):
        if self.cancelled.is_set():
            raise Cancelled("操作已取消。")
        try:
            self.process.stdin.write((json.dumps(value) + "\n").encode())
            self.process.stdin.flush()
        except (BrokenPipeError, OSError):
            raise ActionError("Codex 进程已退出，请检查 CLI 与网络。", 502, "processExited")

    def receive(self, deadline):
        while True:
            if self.cancelled.is_set():
                raise Cancelled("操作已取消。")
            if time.monotonic() >= deadline:
                raise ActionError("等待 Codex 超时；请检查登录状态或网络。", 504, "timeout")
            if b"\n" in self.buffer:
                line, self.buffer = self.buffer.split(b"\n", 1)
                try:
                    value = json.loads(line)
                except (ValueError, UnicodeDecodeError):
                    raise ActionError("Codex 返回了无法解析的协议数据，请检查 CLI 版本。", 502)
                if not isinstance(value, dict):
                    raise ActionError("Codex 返回了不兼容的协议数据。", 502)
                if "method" in value and "id" in value:
                    self.send({"id": value["id"], "error": {"code": -32601, "message": "Tools are disabled"}})
                    raise ActionError("Codex 请求了额外权限或工具，已停止本次操作。", 502)
                method = value.get("method")
                if method in {"turn/started", "turn/completed", "item/started", "item/completed",
                              "item/agentMessage/delta", "error"}:
                    self.diagnostics.update(last_event=method, last_event_at=now())
                    if method == "error":
                        self.diagnostics["last_error"] = turn_failure((value.get("params") or {}).get("error"))
                return value
            if len(self.buffer) > 4 * 1024 * 1024:
                raise ActionError("Codex 响应超过限制，操作已停止。", 502)
            if not self.selector.select(min(.25, max(0, deadline - time.monotonic()))):
                continue
            chunk = os.read(self.process.stdout.fileno(), 65536)
            if not chunk:
                raise ActionError("Codex 进程提前退出，请检查 CLI 与网络。", 502, "processExited")
            self.buffer += chunk

    def request(self, method, params, deadline):
        if method != "turn/interrupt":
            self.diagnostics.update(last_request=method, last_request_at=now())
        self.sequence += 1
        request_id = self.sequence
        self.send({"id": request_id, "method": method, "params": params})
        while True:
            value = self.receive(deadline)
            if value.get("id") == request_id:
                if "error" in value:
                    error = value["error"]
                    if method == "account/read":
                        code = error.get("code") if isinstance(error, dict) else None
                        message = error.get("message") if isinstance(error, dict) else None
                        if type(code) is int:
                            self.diagnostics["rpc_error_code"] = code
                        reason, detail = "unrecognized", "具体原因未识别；上游原文未保存，以免泄露凭据。请保留本次诊断记录再排查。"
                        if type(code) is int and code == -32603 and isinstance(message, str):
                            reason, detail = ACCOUNT_READ_ERRORS.get(message, (reason, detail))
                        self.diagnostics["rpc_error_reason"] = reason
                        suffix = "（错误码 %s）" % code if type(code) is int else "（错误码未知）"
                        raise ActionError("Codex 身份读取失败（account/read）%s：%s" % (suffix, detail), 502)
                    code = error.get("code")
                    if type(code) is int:
                        self.diagnostics["rpc_error_code"] = code
                    # Upstream messages can contain tokens, URLs or configuration.
                    if (method == "account/login/start" and value["error"].get("message") ==
                            "failed to request device code: device code request failed with status 429 Too Many Requests"):
                        raise LoginRateLimited()
                    raise ActionError("Codex 拒绝了操作（%s），请检查账号设置和 CLI 版本。" % method, 502)
                result = value.get("result")
                if not isinstance(result, dict):
                    raise ActionError("Codex 返回了不兼容的结果。", 502)
                return result
            self.pending.append(value)
            if len(self.pending) > 2000:
                raise ActionError("Codex 返回过多事件，操作已停止。", 502)

    def event(self, method, predicate, deadline):
        while True:
            for index, value in enumerate(self.pending):
                if value.get("method") == method and predicate(value.get("params", {})):
                    return self.pending.pop(index)["params"]
            value = self.receive(deadline)
            if value.get("method") == method and predicate(value.get("params", {})):
                return value["params"]
            self.pending.append(value)
            if len(self.pending) > 2000:
                raise ActionError("Codex 返回过多事件，操作已停止。", 502)

    def initialize(self):
        self.request("initialize", {"clientInfo": {"name": "agent_monitor_accounts", "version": "1"},
                                   "capabilities": {"experimentalApi": True}}, time.monotonic() + 20)
        self.send({"method": "initialized"})

    def rejections(self, thread_id, turn_id):
        """Keep terminal refusal evidence even when turn/completed never arrives."""
        if not thread_id or not turn_id:
            return []
        failures = {}
        for value in self.pending:
            params = value.get("params")
            if (value.get("method") != "error" or not isinstance(params, dict)
                    or params.get("threadId") != thread_id or params.get("turnId") != turn_id
                    or params.get("willRetry") is not False):
                continue
            failure = turn_failure(params.get("error"))
            key = restriction_key(failure)
            if key:
                failures[key] = failure
        return list(failures.values())

    def interrupt(self, force=False):
        # The worker retains pipe/selector ownership. Only signal its child here
        # so a blocked protocol write can return and reach that worker's finally.
        with self.process_guard:
            if self.process.poll() is None:
                try:
                    os.killpg(self.process.pid, signal.SIGKILL if force else signal.SIGTERM)
                except ProcessLookupError:
                    pass

    def close(self):
        self.selector.close()
        with self.process_guard:
            if self.process.poll() is None:
                try:
                    os.killpg(self.process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                try:
                    self.process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    try:
                        os.killpg(self.process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    self.process.wait(timeout=2)
        for stream in (self.process.stdin, self.process.stdout):
            try:
                stream.close()
            except OSError:
                pass  # the peer may have died with an unfinished buffered write


def quota_reading(result, account_id):
    if result.get("accountId") is None:
        raise ActionError("配额响应未提供账号，无法核验读数归属；请稍后重试。", 502)
    if result.get("accountId") != account_id:
        raise ActionError("配额响应的账号与所选账号不匹配。", 502)
    limit = select_codex_rate_limit(result) or {}
    reading = {"observed_at": now(), "codex_quota_available": bool(limit)}
    allowed = result.get("ordinaryUsageAllowed")
    reading["ordinary_usage_allowed"] = allowed if type(allowed) is bool else None
    spent = limit.get("spendControlReached")
    reading["spend_control_reached"] = spent if type(spent) is bool else None
    reached = limit.get("rateLimitReachedType")
    reading["rate_limit_reached_type"] = (reached if isinstance(reached, str) and reached in REACHED_LIMITS
                                          else "unrecognized" if reached is not None else None)
    for minutes, prefix in ((300, "five_hour"), (10080, "seven_day")):
        window = next((limit[key] for key in ("primary", "secondary")
                       if isinstance(limit.get(key), dict) and limit[key].get("windowDurationMins") == minutes), {})
        for source, target in (("usedPercent", "used_pct"), ("resetsAt", "resets_at")):
            value = window.get(source)
            reading[prefix + "_" + target] = value if type(value) in (int, float) and math.isfinite(value) else None
    return reading


def retain_quota(record):
    readings = [(record.get(key) or {}).get(phase)
                for key in ("operation", "batch_result") for phase in ("after", "before")]
    readings.append(record.get("last_quota"))
    readings = [reading for reading in readings if reading is not None]
    if readings:
        record["last_quota"] = max(readings, key=lambda reading: datetime.fromisoformat(reading["observed_at"]))


class Manager:
    def __init__(self, root, rpc_factory=Rpc):
        self.root = Path(root)
        self.rpc_factory = rpc_factory
        self.guard = threading.RLock()
        self.jobs = {}
        self.stopping = False
        self.slots = threading.BoundedSemaphore(1)
        self.controls = Controls(self.root.parent / "upstream_controls")
        self.login_slot = threading.BoundedSemaphore(1)
        self.login_generation = 0
        self.start_interval = START_INTERVAL_SECONDS
        self.next_start = 0
        self.start_guard = threading.Lock()
        self.batch_routes = {}

    def open_rpc(self, directory, job):
        # All initial and recovery peers share this lane. Do not hold the state
        # guard while waiting, so cancel/status/release remain responsive.
        while not job["cancelled"].is_set():
            if self.start_guard.acquire(timeout=.1):
                try:
                    delay = max(0, self.next_start - time.monotonic())
                    if job["cancelled"].wait(delay):
                        raise Cancelled("操作已取消。")
                    with self.guard:
                        if job["cancelled"].is_set():
                            raise Cancelled("操作已取消。")
                        self.next_start = time.monotonic() + self.start_interval
                        route = job.get("network_route")
                        try:
                            with network_policy.using_route(route.endpoint if route else None):
                                rpc = self.rpc_factory(directory, job["cancelled"])
                        except client_network.NetworkError as exc:
                            raise ActionError(str(exc), exc.status, "clientNetworkDisconnected") from None
                        job["rpc"] = rpc
                        return rpc
                finally:
                    self.start_guard.release()
        raise Cancelled("操作已取消。")

    def restrict(self, directory, record, failure):
        key = restriction_key(failure)
        if key:
            record.setdefault("restrictions", {}).setdefault(key, {
                "category": failure["category"], "created_at": now()})
            self.save(directory, record)

    def observe_permission(self, directory, record, reading, *, explicit=False):
        if quota_denied(reading):
            self.restrict(directory, record, {"category": "usageLimitExceeded"})
        elif explicit and reading.get("codex_quota_available") and reading.get("ordinary_usage_allowed") is True:
            record.get("restrictions", {}).pop("quota", None)
            self.save(directory, record)

    @staticmethod
    def check_restrictions(record):
        restrictions = record.get("restrictions", {})
        for key in RESTRICTION_DETAILS:
            if key in restrictions and not (key == "quota" and (record["operation"]["refresh_only"] or record["operation"].get("login_only"))):
                raise ActionError(RESTRICTION_DETAILS[key], 409, "locallyPaused")

    def directory(self, profile_id):
        try:
            if str(uuid.UUID(profile_id)) != profile_id:
                raise ValueError()
        except (ValueError, TypeError, AttributeError):
            raise ActionError("账号标识无效。")
        directory = self.root / profile_id
        if directory.is_symlink() or not (directory / "profile.json").is_file():
            raise ActionError("未找到这个账号。", 404)
        return directory

    def read(self, directory):
        try:
            return json.loads((directory / "profile.json").read_text())
        except (OSError, ValueError):
            raise ActionError("账号记录无法读取，请检查服务主机的状态目录。", 500)

    def save(self, directory, record):
        retain_quota(record)
        write_record(directory / "profile.json", record)

    def add(self, email, account_id=None):
        if not isinstance(email, str) or len(email) > 254 or email != email.strip() or "@" not in email or any(c.isspace() for c in email):
            raise ActionError("请输入有效的账号邮箱。")
        if account_id is not None and (not isinstance(account_id, str) or not account_id or len(account_id) > 256):
            raise ActionError("工作区标识无效。")
        with self.guard:
            private_dir(self.root)
            with lock_file(self.root / ".catalog.lock"):
                matches = []
                for path in self.root.glob("*/profile.json"):
                    record = self.read(path.parent)
                    if record["email"].casefold() == email.casefold() and (account_id is None or record.get("account_id") in (None, account_id)):
                        matches.append((path.parent, record))
                if len(matches) > 1:
                    raise ActionError("这个邮箱有多个工作区，请在下方对应账号卡片上操作。", 409)
                if matches:
                    directory, record = matches[0]
                    if account_id is not None and record.get("account_id") is None:
                        with lock_file(directory / ".lock"):
                            record["account_id"] = account_id
                            self.save(directory, record)
                    return self.view(directory)
                profile_id = str(uuid.uuid4())
                directory = private_dir(self.root / profile_id)
                self.save(directory, {"id": profile_id, "email": email, "account_id": account_id, "operation": None})
                return self.view(directory)

    def view(self, directory):
        record = self.read(directory)
        operation = record.get("operation")
        job = self.jobs.get(record["id"])
        if operation and operation["stage"] in ACTIVE and not job:
            try:
                with lock_file(directory / ".lock"):
                    # The worker may have completed between the first read and lock acquisition.
                    record = self.read(directory)
                    operation = record.get("operation")
                    if operation and operation["stage"] in ACTIVE:
                        operation.update(stage="interrupted", detail="上次操作已中断；不会自动重发，请查看消息结果。")
                        self.save(directory, record)
            except ActionError as exc:
                if exc.status != 409:
                    raise
        result = copy.deepcopy(record)
        result["restriction_details"] = [text for key, text in RESTRICTION_DETAILS.items()
                                         if key in record.get("restrictions", {})]
        result["has_credentials"] = (directory / "codex/auth.json").is_file()
        result["busy"] = bool(job) or bool(operation and operation["stage"] in ACTIVE)
        if job and operation and operation["stage"] == "login":
            result["operation"].update(job.get("authorization", {}))
        return result

    def discover(self, entries):
        """Materialize private profiles from metadata, without authorizing or sending."""
        ids, unavailable = set(), []
        for entry in entries:
            if entry.get("account_state") != "known":
                continue
            email, account_id = entry.get("account_label"), entry.get("account_id")
            if not account_id:
                continue
            try:
                account = self.add(email, account_id)
            except ActionError as exc:
                if exc.status != 400:
                    raise
                unavailable.append({"account_id": account_id,
                                    "detail": "登录记录缺少有效邮箱或工作区标识，请在来源机器登录并刷新账号记录。"})
                continue
            ids.add(account["id"])
        return ids, unavailable

    def list(self, eligible_ids=None):
        with self.guard:
            accounts = [self.view(path.parent) for path in sorted(self.root.glob("*/profile.json"))]
            batch = self.read_batch()
            if batch:
                by_id = {account["id"]: account for account in accounts}
                batch["items"] = [{"profile_id": pid, "operation": self.batch_operation(by_id[pid], batch["id"])}
                                  for pid in batch.pop("profile_ids")]
            if eligible_ids is not None:
                retained = {item["profile_id"] for item in batch["items"]} if batch else set()
                accounts = [account for account in accounts
                            if account["id"] in eligible_ids | retained or account["busy"]]
                for account in accounts:
                    account["eligible"] = account["id"] in eligible_ids
            return {"accounts": accounts, "message": MESSAGE, "batch": batch,
                    "upstream_pause": self.controls.read("codex").get("pause")}

    def read_batch(self):
        path = self.root / "batch.json"
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text())
        except (OSError, ValueError):
            raise ActionError("批次记录无法读取，请检查服务主机的状态目录。", 500)

    @staticmethod
    def batch_operation(record, batch_id):
        for key in ("operation", "batch_result"):
            operation = record.get(key)
            if operation and operation.get("batch_id") == batch_id:
                return operation
        return None

    def prepare(self, profile_id, *, published_id=None, batch_id=None, refresh_only=False, login_only=False):
        if self.stopping:
            raise ActionError("服务正在停止，请稍后重试；本次未启动账号操作。", 503)
        directory = self.directory(profile_id)
        if profile_id in self.jobs:
            raise ActionError("这个账号已有操作正在进行。", 409)
        lock = lock_file(directory / ".lock")
        try:
            record = self.read(directory)
            retain_quota(record)
            previous = record.get("operation")
            if previous and previous.get("failure") and previous["failure"].get("category") != "locallyPaused":
                record.setdefault("last_failure", copy.deepcopy(previous))
            if (previous and previous.get("message_status") == "unknown"
                    and record.get("reviewed_operation_id") != previous.get("id")):
                record.setdefault("restrictions", {}).setdefault("unknown", {
                    "category": "unknown", "created_at": now()})
                record.setdefault("last_failure", copy.deepcopy(previous))
            if previous and published_id and previous.get("batch_id") == published_id:
                record["batch_result"] = previous
            record["operation"] = {"id": str(uuid.uuid4()), "stage": "queued", "started_at": now(),
                                   "refresh_only": refresh_only, "login_only": login_only, "message_status": "not_sent",
                                   "quota_status": "not_read", "detail": "已排队，等待处理。"}
            if batch_id:
                record["operation"]["batch_id"] = batch_id
                # A retry replaces the result of that same batch; keep one authority.
                if (record.get("batch_result") or {}).get("batch_id") == batch_id:
                    record.pop("batch_result")
            self.save(directory, record)
            return directory, record, {"cancelled": threading.Event(), "done": threading.Event(), "authorization": {},
                                       "login_generation": self.login_generation}, lock
        except Exception:
            lock.close()
            raise

    def launch(self, prepared):
        directory, record, job, lock = prepared
        profile_id = record["id"]
        self.jobs[profile_id] = job
        thread = threading.Thread(target=self.run, args=prepared, daemon=True, name="codex-account-" + profile_id)
        job["thread"] = thread
        try:
            thread.start()
        except Exception:
            self.jobs.pop(profile_id, None)
            try:
                self.update(directory, record, stage="failed", detail="未能启动操作；消息未发送，可重试。")
            finally:
                lock.close()
                job["done"].set()

    def start(self, profile_id, *, refresh_only=False, login_only=False):
        with self.guard:
            directory = self.directory(profile_id)
            with lock_file(self.root / ".catalog.lock"):
                batch = self.read_batch()
                if not refresh_only and not login_only and batch and profile_id in batch["profile_ids"]:
                    raise ActionError("这个账号已纳入当前批次。请使用「继续未发送账号」或明确开始新一轮，避免重复发送。", 409)
                self.launch(self.prepare(profile_id, refresh_only=refresh_only, login_only=login_only, published_id=batch["id"] if batch else None))
                return self.view(directory)

    def start_batch(self, expected_batch_id, eligible_ids=None, *, network_route=None):
        if expected_batch_id is not None and not isinstance(expected_batch_id, str):
            raise ActionError("批次标识无效。")
        with self.guard:
            private_dir(self.root)
            with lock_file(self.root / ".catalog.lock"):
                current = self.read_batch()
                published_id = current["id"] if current else None
                if expected_batch_id != published_id:
                    return self.list()
                accounts = sorted(self.list()["accounts"], key=lambda a: (a["email"].casefold(), a["id"]))
                if any(account["busy"] for account in accounts):
                    raise ActionError("还有账号正在操作，请等待完成或取消后再开始新一轮。", 409)
                if eligible_ids is not None:
                    accounts = [account for account in accounts if account["id"] in eligible_ids]
                if not accounts:
                    raise ActionError("尚未发现可发送的 Codex 登录账号，请刷新来源机器的账号记录。")
                batch = {"id": str(uuid.uuid4()), "created_at": now(), "profile_ids": [a["id"] for a in accounts]}
                if network_route:
                    network_route.require()
                    batch["network_device"] = {"id": network_route.id, "label": network_route.label}
                prepared = []
                try:
                    # A subset round can leave a former member outside the new
                    # manifest. Preserve its published result before quota-only
                    # refresh later replaces that member's active operation.
                    if current:
                        for pid in set(current["profile_ids"]) - set(batch["profile_ids"]):
                            directory = self.directory(pid)
                            with lock_file(directory / ".lock"):
                                record = self.read(directory)
                                previous = record.get("operation")
                                if previous and previous.get("batch_id") == published_id:
                                    record["batch_result"] = previous
                                    self.save(directory, record)
                    for pid in batch["profile_ids"]:
                        prepared.append(self.prepare(pid, published_id=published_id, batch_id=batch["id"]))
                        if network_route:
                            prepared[-1][2]["network_route"] = network_route
                    write_record(self.root / "batch.json", batch)
                except Exception:
                    for *_, lock in prepared:
                        lock.close()
                    raise
                self.batch_routes = {batch["id"]: network_route} if network_route else {}
                self.launch_sequence(prepared)
                return self.list()

    def retry_batch(self, batch_id, eligible_ids=None):
        with self.guard:
            private_dir(self.root)
            with lock_file(self.root / ".catalog.lock"):
                batch = self.read_batch()
                if not batch or batch["id"] != batch_id:
                    raise ActionError("批次已变化，请刷新后查看。", 409)
                network_route = self.batch_routes.get(batch_id)
                if batch.get("network_device"):
                    if not network_route:
                        raise ActionError("本轮设备连接已失效，请连接本机网络后明确开始新一轮；不会切换出口重发。", 409)
                    network_route.require()
                if any(account["busy"] for account in self.list()["accounts"]):
                    raise ActionError("还有账号正在操作，请等待本轮结束再继续。", 409)
                prepared = []
                try:
                    for pid in batch["profile_ids"]:
                        if eligible_ids is not None and pid not in eligible_ids:
                            continue
                        account = self.view(self.directory(pid))
                        operation = self.batch_operation(account, batch_id)
                        if account["busy"] or (operation and operation["message_status"] != "not_sent"):
                            continue
                        prepared.append(self.prepare(pid, published_id=batch_id, batch_id=batch_id))
                        if network_route:
                            prepared[-1][2]["network_route"] = network_route
                except Exception:
                    for *_, lock in prepared:
                        lock.close()
                    raise
                self.launch_sequence(prepared)
                return self.list()

    def launch_sequence(self, prepared):
        predecessor = None
        stopped = threading.Event()
        for item in prepared:
            job = item[2]
            job["predecessor"] = predecessor
            job["batch_stopped"] = stopped
            predecessor = job["done"]
            self.launch(item)

    def cancel(self, profile_id):
        with self.guard:
            self.directory(profile_id)
            job = self.jobs.get(profile_id)
            if not job:
                raise ActionError("本进程没有正在运行的这项操作；请刷新查看结果。", 409)
            job["cancelled"].set()
            return {"cancelling": True}

    def forget_login(self, profile_id):
        with self.guard:
            directory = self.directory(profile_id)
            with lock_file(directory / ".lock"):
                (directory / "codex/auth.json").unlink(missing_ok=True)
                record = self.read(directory)
                self.save(directory, record)
                return self.view(directory)

    def resume(self, profile_id, operation_id, pause_id=None):
        """An explicit acknowledgement; never schedules a provider operation."""
        with self.guard:
            directory = self.directory(profile_id)
            with lock_file(directory / ".lock"):
                record = self.read(directory)
                if (record.get("operation") or {}).get("id") != operation_id:
                    raise ActionError("账号结果已变化，请刷新并重新核对。", 409)
                try:
                    if pause_id is not None:
                        self.controls.resume("codex", pause_id)
                except (Paused, Busy) as exc:
                    raise ActionError(str(exc), 409, "locallyPaused") from None
                for key in ("authentication", "unknown", "rate_limit", "login_rate_limit"):
                    record.get("restrictions", {}).pop(key, None)
                # Retain the failed operation, and remember explicit acknowledgement
                # so prepare does not recreate an already reviewed unknown hold.
                record["reviewed_operation_id"] = operation_id
                self.save(directory, record)
                return self.view(directory)

    def release_rate_limit(self, profile_id):
        """Explicit local acknowledgement only; no provider call or send."""
        with self.guard:
            directory = self.directory(profile_id)
            with lock_file(directory / ".lock"):
                record = self.read(directory)
                for key in ("rate_limit", "login_rate_limit"):
                    record.get("restrictions", {}).pop(key, None)
                self.save(directory, record)
                return self.view(directory)

    def update(self, directory, record, **fields):
        with self.guard:
            record["operation"].update(fields)
            self.save(directory, record)

    def identity(self, rpc, directory, record):
        account = rpc.request("account/read", {"refreshToken": False}, time.monotonic() + 20).get("account")
        if account is None:
            return False
        credential = codex_account(directory / "codex/auth.json")
        if (not isinstance(account, dict) or account.get("type") != "chatgpt" or not credential
                or str(account.get("email", "")).casefold() != record["email"].casefold()
                or str(credential.label).casefold() != record["email"].casefold()
                or (record.get("account_id") and record["account_id"] != credential.account_id)):
            raise ActionError("登录的邮箱或工作区与所选账号不一致，未发送消息。请清除登录态后登录正确账号。", 409)
        record["account_id"] = credential.account_id
        (directory / "codex/auth.json").chmod(0o600)
        self.save(directory, record)
        return True

    def run(self, directory, record, job, lock):
        rpc = None
        lease = None
        thread_id = turn_id = None
        slot = False
        login_slot = False
        def acquire_slot():
            while not job["cancelled"].is_set():
                if self.slots.acquire(timeout=.25):
                    return
            raise Cancelled("操作已取消。")
        def check_login_queue():
            if job["cancelled"].is_set():
                raise Cancelled("操作已取消。")
            with self.guard:
                if job["login_generation"] != self.login_generation:
                    raise ActionError("OpenAI 登录服务刚刚限流，本账号尚未申请设备码。请稍后点击继续未发送账号，或重试单账号操作。", 429)
        try:
            predecessor = job.get("predecessor")
            while predecessor and not predecessor.wait(.1):
                if job["cancelled"].is_set():
                    raise Cancelled("操作已取消。")
            if job.get("batch_stopped") and job["batch_stopped"].is_set():
                raise ActionError("本轮前序账号出现异常，已停止后续账号；本账号未发出请求。", 409, "locallyPaused")
            if any(key in record.get("restrictions", {}) for key in ("unknown", "authentication", "rate_limit", "login_rate_limit")):
                # Migrate pre-control account holds without any provider call.
                try:
                    migration = self.controls.acquire("codex")
                except (Paused, Busy):
                    pass
                else:
                    try:
                        migration.pause("previousAccountFailure")
                    finally:
                        migration.close()
            self.check_restrictions(record)
            if not record["operation"].get("login_only") and not (directory / "codex/auth.json").is_file():
                raise ActionError("本页尚未保存登录态。请单独登录；授权完成后需重新点击发送或刷新。", 409, "needsLogin")
            acquire_slot()
            slot = True
            try:
                lease = self.controls.acquire("codex", cancelled=job["cancelled"], timeout=0,
                                              interval=self.start_interval)
            except (Paused, Busy) as exc:
                if job["cancelled"].is_set():
                    raise Cancelled("操作已取消。") from None
                raise ActionError(str(exc), 409, "locallyPaused") from None
            self.update(directory, record, stage="checking", detail="正在检查登录状态。")
            rpc = self.open_rpc(directory, job)
            rpc.initialize()
            if not self.identity(rpc, directory, record):
                if not record["operation"].get("login_only"):
                    raise ActionError("登录态不可用，请单独登录；本次未发送消息。", 409, "needsLogin")
                self.update(directory, record, stage="login_queued", detail="等待逐个授权；轮到此账号后显示设备码，已有登录态的账号会继续处理。")
                while not login_slot:
                    check_login_queue()
                    login_slot = self.login_slot.acquire(timeout=.25)
                # A preceding login may have hit its rate limit while we waited.
                check_login_queue()
                login = rpc.request("account/login/start", {"type": "chatgptDeviceCode"}, time.monotonic() + 30)
                url = login.get("verificationUrl", "")
                parsed = urlparse(url)
                if parsed.scheme != "https" or parsed.netloc != "auth.openai.com" or not login.get("userCode") or not login.get("loginId"):
                    raise ActionError("Codex 未返回有效的官方设备授权信息。请更新 CLI。", 502)
                job["authorization"] = {"verification_url": url, "user_code": login["userCode"]}
                self.update(directory, record, stage="login", detail="请在 OpenAI 官方页面登录所选邮箱，再输入下方设备码。邮箱验证码也在官方页面填写。")
                completed = rpc.event("account/login/completed", lambda p: p.get("loginId") == login["loginId"], time.monotonic() + LOGIN_SECONDS)
                job["authorization"] = {}
                if not completed.get("success"):
                    if completed.get("error") == "device auth failed with status 429 Too Many Requests":
                        raise LoginRateLimited()
                    raise ActionError("官方授权未完成，本次设备码已停止使用。请重新发起操作获取新码；本次未发送消息。", 502)
                self.login_slot.release()
                login_slot = False
                if not self.identity(rpc, directory, record):
                    raise ActionError("授权后仍未取得登录态，请重试。", 502)
            if record["operation"].get("login_only"):
                self.update(directory, record, stage="succeeded", detail="已完成登录，本次未发送消息或查询配额。请重新选择后续操作。")
                return
            if record["operation"]["refresh_only"]:
                self.update(directory, record, stage="reading", detail="正在查询配额，本次不发送消息。")
                after = quota_reading(rpc.request("account/rateLimits/read", {}, time.monotonic() + 20), record["account_id"])
                self.observe_permission(directory, record, after, explicit=True)
                self.update(directory, record, stage="succeeded", quota_status="observed", after=after,
                            detail="已读取服务端配额，本次未发送消息。")
                return
            deadline = time.monotonic() + SEND_SECONDS
            before = quota_reading(rpc.request("account/rateLimits/read", {}, min(deadline, time.monotonic() + 15)), record["account_id"])
            self.update(directory, record, before=before)
            self.observe_permission(directory, record, before)
            if not before["codex_quota_available"]:
                raise ActionError("发送前未取得目标 Codex 配额，未发送消息。可仅刷新配额重查。", 502)
            if quota_denied(before):
                raise ActionError("发送前服务端明确限制了额度使用；本次未发送消息，可仅刷新配额查看。", 409, "usageLimitExceeded")
            if any(before.get(key) is not None and before[key] >= 100 for key in ("five_hour_used_pct", "seven_day_used_pct")):
                raise ActionError("发送前目标窗口已显示 100%，本次保守跳过发送；这不是服务端明确拒绝。可稍后仅刷新配额。", 409, "quotaAtCapacity")
            configuration = rpc.request("config/read", {"includeLayers": True, "cwd": str(directory / "work")},
                                        min(deadline, time.monotonic() + 20))
            options, profile = restricted_thread_options(configuration)
            thread = rpc.request("thread/start", {**options, "ephemeral": True, "cwd": str(directory / "work"),
                                 "approvalPolicy": "never",
                                 "baseInstructions": "Respond briefly to the user's message.",
                                 "developerInstructions": "This is a connectivity check. Reply with OK."}, deadline)
            verify_restricted_thread(thread, profile)
            thread_id = thread["thread"]["id"]
            identifiers = {"thread_id": safe_protocol_id(thread_id)} if safe_protocol_id(thread_id) else {}
            # Commit unknown before transmission: lost acknowledgement is not proof of no usage.
            self.update(directory, record, stage="sending", message_status="unknown", diagnostics=identifiers,
                        message=secrets.choice(MESSAGES),
                        detail="正在发送一条短消息；中断后不会自动重发。")
            turn = rpc.request("turn/start", {"threadId": thread_id, "input": [{"type": "text", "text": record["operation"]["message"]}],
                                              "environments": []}, deadline)
            turn_id = turn["turn"]["id"]
            if safe_protocol_id(turn_id):
                identifiers["turn_id"] = safe_protocol_id(turn_id)
                self.update(directory, record, diagnostics=dict(identifiers))
            completed = rpc.event("turn/completed", lambda p: p.get("threadId") == thread_id and p.get("turn", {}).get("id") == turn_id, deadline)
            if completed["turn"].get("status") != "completed":
                failure = turn_failure(completed["turn"].get("error"))
                self.update(directory, record, message_status="failed", failure=failure)
                reason = TURN_FAILURES.get(failure["category"], "服务端未提供可识别的错误类别。")
                if "http_status" in failure:
                    reason += " HTTP %s。" % failure["http_status"]
                raise ActionError("消息发送阶段：" + reason + "可能已消耗额度；不会自动重发。", 502)
            self.update(directory, record, stage="reading", message_status="succeeded", detail="消息已完成，正在查询服务端配额。")
            try:
                after = quota_reading(rpc.request("account/rateLimits/read", {}, time.monotonic() + 20), record["account_id"])
                self.observe_permission(directory, record, after)
                has_reset = after["seven_day_resets_at"] is not None
                self.update(directory, record, stage="succeeded" if has_reset else "partial", quota_status="observed", after=after,
                            detail="消息已完成，已读取服务端重置时间；这不保证窗口已固定或重置。" if has_reset else "消息已完成，但服务端未返回七天窗口重置时间。")
            except ActionError as exc:
                failure = {"category": exc.category, "stage": "reading"}
                self.restrict(directory, record, failure)
                if lease:
                    lease.pause("quotaReadFailed")
                if job.get("batch_stopped") is not None:
                    job["batch_stopped"].set()
                self.update(directory, record, stage="partial", quota_status="failed", failure=failure,
                            detail="消息已完成，但配额查询未成功，已暂停后续请求。不要为了查询结果重复发送。")
                record["last_failure"] = copy.deepcopy(record["operation"])
                self.save(directory, record)
        except Cancelled:
            if rpc:
                record.setdefault("restrictions", {})["unknown"] = {"category": "cancelled", "created_at": now()}
                if lease:
                    lease.pause("cancelled")
                if job.get("batch_stopped") is not None:
                    job["batch_stopped"].set()
            self.update(directory, record, stage="cancelled", detail="操作已取消。若消息已发出，额度消耗不会撤回；发送结果见下方。")
            if rpc:
                record["last_failure"] = copy.deepcopy(record["operation"])
                self.save(directory, record)
        except Exception as exc:
            if isinstance(exc, LoginRateLimited):
                with self.guard:
                    self.login_generation += 1
            detail = str(exc) if isinstance(exc, ActionError) else "操作未完成，请检查 Codex CLI 或服务状态目录。"
            if record["operation"]["message_status"] == "unknown":
                detail += " 发送结果未知，可能已消耗额度，已暂停后续请求；请先核对原始结果。"
            failure = record["operation"].get("failure") or {
                "stage": record["operation"]["stage"],
                "category": exc.category if isinstance(exc, ActionError) else "unknown",
            }
            self.restrict(directory, record, failure)
            if record["operation"]["message_status"] == "unknown":
                record.setdefault("restrictions", {})["unknown"] = {"category": failure["category"], "created_at": now()}
            benign = failure["category"] in ("needsLogin", "missingCli", "networkPolicyUnavailable", "usageLimitExceeded", "quotaAtCapacity", "locallyPaused")
            if failure["category"] == "clientNetworkDisconnected" and rpc is None:
                # A missing device before RPC creation is a local prerequisite,
                # not evidence of an upstream failure. Stop only this batch.
                benign = True
                if job.get("batch_stopped") is not None:
                    job["batch_stopped"].set()
            if not benign:
                if lease:
                    lease.pause("operationFailed")
                if job.get("batch_stopped") is not None:
                    job["batch_stopped"].set()
            diagnostics = copy.deepcopy(getattr(rpc, "diagnostics", {}))
            route = getattr(rpc, "network_route", None)
            if isinstance(route, str):
                diagnostics["network"] = network_policy.observations(route)
            rejections = rpc.rejections(thread_id, turn_id) if rpc else []
            for rejection in rejections:
                self.restrict(directory, record, rejection)
                if restriction_key(rejection) != "quota":
                    if lease:
                        lease.pause("upstreamRejected")
                    if job.get("batch_stopped") is not None:
                        job["batch_stopped"].set()
            if rejections:
                diagnostics["rejections"] = rejections
            for name, value in (("thread_id", thread_id), ("turn_id", turn_id)):
                if safe_protocol_id(value) is not None:
                    diagnostics[name] = safe_protocol_id(value)
            if failure["category"] == "timeout" and thread_id and turn_id and not job["cancelled"].is_set():
                try:
                    rpc.request("turn/interrupt", {"threadId": thread_id, "turnId": turn_id}, time.monotonic() + INTERRUPT_SECONDS)
                    diagnostics["interrupt"] = "acknowledged"
                except Exception:
                    diagnostics["interrupt"] = "unconfirmed"
            self.update(directory, record, diagnostics=diagnostics)
            self.update(directory, record, stage="failed", detail=detail, failure=failure)
            if failure["category"] != "locallyPaused":
                record["last_failure"] = copy.deepcopy(record["operation"])
                self.save(directory, record)
        finally:
            try:
                if rpc:
                    rpc.close()
            except Exception as exc:
                job["cleanup_error"] = exc
                raise
            finally:
                try:
                    if lease:
                        lease.close(completed="cleanup_error" not in job)
                except Exception as exc:
                    job["cleanup_error"] = exc
                    raise
                finally:
                    if login_slot:
                        self.login_slot.release()
                    if slot:
                        self.slots.release()
                    with self.guard:
                        job["authorization"] = {}
                        if "cleanup_error" not in job:
                            self.jobs.pop(record["id"], None)
                        elif job.get("batch_stopped") is not None:
                            job["batch_stopped"].set()
                        lock.close()
                        job["done"].set()

    def close(self):
        with self.guard:
            self.stopping = True
            jobs = list(self.jobs.values())
            for job in jobs:
                job["cancelled"].set()
        # One shared deadline per phase, not three seconds per queued account.
        for force in (False, True):
            for job in jobs:
                if job["thread"].is_alive() and job.get("rpc"):
                    job["rpc"].interrupt(force=force)
            deadline = time.monotonic() + 3
            for job in jobs:
                job["thread"].join(timeout=max(0, deadline - time.monotonic()))
            if not any(job["thread"].is_alive() for job in jobs):
                if any("cleanup_error" in job for job in jobs):
                    raise RuntimeError("账号子进程回收失败，已拒绝重启；请检查服务日志。")
                return
        raise RuntimeError("账号子进程清理未完成，已拒绝重启；请检查服务日志。")


from project_paths import state_path

manager = Manager(state_path("codex-accounts"))
atexit.register(manager.close)
