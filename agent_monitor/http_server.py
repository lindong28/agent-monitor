from pathlib import Path as _LogPath
from agent_monitor.runtime import service_logs
service_logs.initialize(_LogPath(__file__).resolve().parents[1])

import argparse
import ipaddress
import socket
import hashlib
import json
import logging
import mimetypes
import os
import posixpath
import re
import signal
import sys
import threading
import time
import uuid
from datetime import date, datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer as _ThreadingHTTPServer
from socketserver import TCPServer
from agent_monitor.accounts import codex_accounts
from agent_monitor.network import account_egress
from agent_monitor.network import client_network
from urllib.parse import parse_qs, unquote, urlparse
from zoneinfo import ZoneInfo

from agent_monitor.accounting import rollup
from agent_monitor.snapshots import generation
from agent_monitor import hub_config as hub
from agent_monitor.snapshots import sync
from agent_monitor.runtime import process_identity
import agent_monitor.views.query_view as query_view
from agent_monitor.quota import quota_sync
from agent_monitor import diagnostics
import agent_monitor.asset_delivery as asset_delivery
from agent_monitor.paths import PROJECT_ROOT
from agent_monitor import source_inventory
from agent_monitor.quota.account_memory import AccountMemory; from agent_monitor.quota.account_memory import UNSPECIFIED_ACCOUNT_LABEL; from agent_monitor.quota.account_memory import parse_observed_at
from agent_monitor.collection import CollectionCoordinator
from agent_monitor.quota.quota_view import QuotaView
from agent_monitor import ingestion
from agent_monitor.views import ViewServices
from agent_monitor.views.overview import OverviewView
from agent_monitor.views.sessions import SessionsView
from agent_monitor.views.network import NetworkReport
from agent_monitor.views import hub_reads
from agent_monitor.network.local_network import LocalNetwork
from functools import partial


ROOT = PROJECT_ROOT
WEB_ROOT = ROOT / "web"
GUIDE_PAGES = {
    "/guide/sources": "guide.html",
    "/guide/collection": "guide-collection.html",
    "/guide/setup": "guide-setup.html",
    "/guide/questions": "guide-questions.html",
}
logger = logging.getLogger("agent-monitor")
SERVER_INSTANCE_ID = uuid.uuid4().hex
ACCOUNT_MEMORY = AccountMemory(ROOT / "state" / "account_memory.json")
QUOTA_REFRESH = quota_sync.Manager(ROOT / "state" / "machine_quota.json")
QUOTA_VIEW = QuotaView(ACCOUNT_MEMORY, QUOTA_REFRESH)
QUOTA_REFRESH.on_complete = QUOTA_VIEW.remember_published
COLLECTION = CollectionCoordinator(QUOTA_REFRESH, QUOTA_VIEW.remember_published, SERVER_INSTANCE_ID)

# Host/port this process is serving on, stashed by main() so /api/restart can
# re-exec with identical arguments.
_BIND_HOST = "127.0.0.1"
_BIND_PORT = 39001


def _web_files():
    return sorted(asset_delivery.files(WEB_ROOT))


def _source_signature():
    return source_inventory.source_signature(ROOT)


def _web_signature():
    return asset_delivery.signature(WEB_ROOT)


# Signature of the code this process actually loaded. health() compares it to a
# fresh signature each call, so the server self-reports when its on-disk source
# has changed since boot (the symptom: a long-lived daemon serving old code).
BOOT_SIGNATURE = _source_signature()
from agent_monitor.runtime import service_runtime
from agent_monitor.runtime import deployment_guard
BOOT_DEPLOYMENT_SIGNATURE = service_runtime.signature(ROOT)


def diagnostics_endpoint(_query):
    return diagnostics.monitor.snapshot()


def overview(query, *, views):
    return views.overview.read(query)


def _pivot_window(query):
    if _first(query, "range", "30d") != "custom":
        return rollup.range_window(_first(query, "range", "30d"))
    try:
        start = date.fromisoformat(_first(query, "start", ""))
        end = date.fromisoformat(_first(query, "end", ""))
    except ValueError as exc:
        raise ValueError("请选择有效的起始和终止日期") from exc
    if start > end:
        raise ValueError("起始日期不能晚于终止日期")
    return start, end


def pivot_endpoint(query):
    window = _pivot_window(query)
    COLLECTION.request(query)
    if COLLECTION.query_view is None:
        return _pivot_from_view(query, window)
    with COLLECTION.admission() as admission:
        with rollup.use_admitted_generations(admission.admitted):
            return _pivot_from_view(query, window)


def _pivot_from_view(query, window):
    if "group_by" in query:
        group_by = _first(query, "group_by", "none")
        return rollup.query_grouped(
            [] if group_by == "none" else group_by.split(","),
            _first(query, "metric", "cost"),
            agents=set(query.get("agent", [])) or None,
            projects=set(query.get("project", [])) or None,
            models=set(query.get("model", [])) or None,
            machines=set(query.get("machine", [])) or None,
            time_range=window,
        )
    return rollup.query_pivot(
        _first(query, "x", "day"),
        _first(query, "group", "none"),
        _first(query, "metric", "cost"),
        agents=set(query.get("agent", [])) or None,
        projects=set(query.get("project", [])) or None,
        models=set(query.get("model", [])) or None,
        machines=set(query.get("machine", [])) or None,
        time_range=window,
    )


def pivot_filters_endpoint(query):
    window = _pivot_window(query)
    COLLECTION.request(query)
    if COLLECTION.query_view is None:
        return rollup.filter_options(time_range=window)
    with COLLECTION.admission() as admission:
        with rollup.use_admitted_generations(admission.admitted):
            return rollup.filter_options(time_range=window)


def sync_status_endpoint(_query):
    return COLLECTION.status()


def collection_endpoint(_query):
    """Read existing collection and quota observations without usage aggregation."""
    with COLLECTION.admission() as admission:
        status = COLLECTION.status(admission=admission)
        limits = QUOTA_VIEW.live_from_admission(admission, sync_status=status)
        QUOTA_VIEW.apply_applicability(limits)
        return {"sync": status, "rate_limits": limits}


def cost_diagnostics_endpoint(query, *, views):
    return views.sessions.cost_diagnostics(query)


def refresh_endpoint(query):
    receipt = {}
    COLLECTION.request({"force": ["1"]}, request_receipt=receipt)
    if _first(query, "ack", "0") == "1":
        runtime = COLLECTION.snapshot()
        return dict(receipt, instance_id=SERVER_INSTANCE_ID,
                    syncing=runtime["syncing"], queued_refresh=runtime["queued_refresh"],
                    refresh_requested=runtime["refresh_requested"],
                    refresh_completed=runtime["refresh_completed"])
    return dict(COLLECTION.status(), **receipt)


def sessions_endpoint(query, *, views):
    return views.sessions.list(query)


def session_detail(session_id, machine=None, query=None, *, views):
    return views.sessions.detail(session_id, machine, query)


def health(query):
    return {
        "ok": True,
        "instance_id": SERVER_INSTANCE_ID,
        "signature": BOOT_SIGNATURE,
        "source_root": str(ROOT),
        "deployment_signature": BOOT_DEPLOYMENT_SIGNATURE,
        "web_signature": _web_signature(),
        "stale": _source_signature() != BOOT_SIGNATURE or _first(query, "asset_watch", "") != "1",
    }


def _compile_check():
    """py_compile every source file; return a list of error strings (empty when
    all compile). Guards /api/restart from re-exec'ing into broken code — without
    an external supervisor, a crash on boot would take the dashboard down."""
    import py_compile

    errors = []
    for path in source_inventory.python_sources(ROOT):
        try:
            py_compile.compile(str(path), doraise=True)
        except py_compile.PyCompileError as exc:
            errors.append(str(exc))
    return errors


def _schedule_reexec(guard):
    def _reexec():
        # Keep observation alive if cleanup/exec fails; successful exec replaces
        # all threads and the observer's non-inheritable file descriptors.
        try:
            codex_accounts.manager.close()
            client_network.hub.close()
            COLLECTION.close()
            previous_path = os.environ.get("PYTHONPATH")
            previous_directory = os.open(".", os.O_RDONLY)
            os.environ["PYTHONPATH"] = str(ROOT) + (os.pathsep + previous_path if previous_path else "")
            try:
                os.chdir(ROOT)
                os.execv(
                    sys.executable,
                    [sys.executable, "-m", "agent_monitor.http_server", "--host", _BIND_HOST, "--port", str(_BIND_PORT)],
                )
            finally:
                os.fchdir(previous_directory)
                os.close(previous_directory)
                if previous_path is None:
                    os.environ.pop("PYTHONPATH", None)
                else:
                    os.environ["PYTHONPATH"] = previous_path
        finally:
            # On successful exec the non-inheritable FD closes atomically;
            # on cleanup/exec failure release it here so recovery can proceed.
            guard.__exit__(None, None, None)

    threading.Timer(0.4, _reexec).start()


def _valid_zone(name):
    if not name:
        return None
    try:
        ZoneInfo(name)
    except Exception:
        return None
    return name


def local_timezone():
    """IANA name of the machine's current timezone, resolved live so it can't
    drift from the system configuration. Prefers /etc/localtime (the OS-level
    setting the system clock uses, re-read on every call and updated whenever the
    user changes timezone) over the TZ env var, which is frozen per-process at
    launch and would otherwise pin a stale zone. Returns None when unresolved."""
    try:
        link = os.readlink("/etc/localtime")
    except OSError:
        link = ""
    marker = "zoneinfo/"
    idx = link.rfind(marker)
    zone = _valid_zone(link[idx + len(marker):]) if idx != -1 else None
    return zone or _valid_zone(os.environ.get("TZ"))


def timezone_endpoint(_query):
    return {"timezone": local_timezone()}


def network(query, *, views):
    return views.network.read()


ROUTES = {
    "/api/health": health,
    "/api/timezone": timezone_endpoint,
    "/api/overview": overview,
    "/api/pivot": pivot_endpoint,
    "/api/pivot-filters": pivot_filters_endpoint,
    "/api/sync-status": sync_status_endpoint,
    "/api/collection": collection_endpoint,
    "/api/cost-diagnostics": cost_diagnostics_endpoint,
    "/api/sessions": sessions_endpoint,
    "/api/diagnostics": diagnostics_endpoint,
    "/api/network": network,
}


def create_views(*, usage_source=None):
    """Build fresh view/cache owners without replacing process-wide services."""
    return ViewServices(OverviewView(COLLECTION, QUOTA_VIEW, rollup),
                        SessionsView(COLLECTION, ingestion if usage_source is None else usage_source),
                        NetworkReport())


def create_routes(views):
    routes = dict(ROUTES)
    for path in ("/api/overview", "/api/sessions", "/api/cost-diagnostics", "/api/network"):
        routes[path] = partial(routes[path], views=views)
    return routes


class ThreadingHTTPServer(_ThreadingHTTPServer):
    def __init__(self, *args, views=None, **kwargs):
        self.views = views if views is not None else create_views()
        self.routes = create_routes(self.views)
        self.local_network = LocalNetwork()
        super().__init__(*args, **kwargs)

    def service_actions(self):
        service_logs.maintain()
        super().service_actions()

    def server_bind(self):
        # No CGI consumer needs a reverse-resolved server_name. Bind/listen
        # must still work when the host's reverse DNS service is unavailable.
        TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address[:2]


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    timeout = 30

    def handle_one_request(self):
        self._request_started = None
        self._diagnostic_token = None
        self._response_status = None
        self._response_complete = False
        try:
            return super().handle_one_request()
        finally:
            if self._diagnostic_token is not None:
                status = self._response_status
                diagnostics.monitor.finish(self._diagnostic_token,
                    "http_error" if status is not None and status >= 400 else
                    "success" if self._response_complete else "incomplete")
                diagnostics.monitor.local.token = None

    def parse_request(self):
        accepted = super().parse_request()
        self._request_started = time.monotonic()
        if accepted:
            path = urlparse(self.path).path
            if path.startswith("/api/"):
                label = path if path in ROUTES else "/api/session/:id" if path.startswith("/api/session/") else "/api/other"
                self._diagnostic_token = diagnostics.monitor.begin(self.command + " " + label)
                diagnostics.monitor.local.token = self._diagnostic_token
        return accepted

    def send_response(self, code, message=None):
        self._response_status = code
        return super().send_response(code, message)

    def end_headers(self):
        started = getattr(self, "_request_started", None)
        if started is not None:
            self.send_header("Server-Timing", "app;dur=%.2f" % ((time.monotonic() - started) * 1000))
        if self.close_connection and not any(header.lower().startswith(b"connection:")
                                             for header in getattr(self, "_headers_buffer", ())):
            self.send_header("Connection", "close")
        super().end_headers()

    def do_HEAD(self):
        if self.headers.get("Content-Length") or self.headers.get("Transfer-Encoding"):
            self.close_connection = True
        self._handle_request(send_body=False)

    def do_GET(self):
        if self.headers.get("Content-Length") or self.headers.get("Transfer-Encoding"):
            self.close_connection = True
        self._handle_request(send_body=True)

    def do_POST(self):
        # Some actions have no body; never reuse their unread input as a request.
        self.close_connection = True
        parsed = urlparse(self.path)
        if parsed.path == '/api/local-network/probe':
            if not self._check_local_network_boundary(post=True):
                return
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 2048 or self.headers.get_content_type() != 'application/json':
                    raise ValueError()
                payload = json.loads(self.rfile.read(length))
                pid, instance, kind = payload.get('pid'), payload.get('instance'), payload.get('probe_kind')
                if (type(pid) is not int or not 0 < pid < 2**31 or not isinstance(instance, str)
                        or not re.fullmatch(r'[0-9]+:[0-9.]{1,60}', instance)
                        or kind not in ('provider', 'third_party')):
                    raise ValueError()
            except (ValueError, TypeError, AttributeError):
                self._send_json({'error': '诊断参数无效；请重新读取本机。'}, status=400)
                return
            self._send_json(self.server.local_network.probe(pid, instance, kind))
            return
        if self._hub_action_path(parsed.path) and hub_reads.origin():
            self._send_json({'error': '此页面展示 Hub 数据；请到 Hub 执行此操作。',
                             'hub_url': hub_reads.origin()}, status=409)
            return
        if client_network.handle(self):
            return
        if (parsed.path.startswith("/api/codex-accounts/") or parsed.path in
                ("/api/account-memory/remove", "/api/restart", "/api/refresh", "/api/network", "/api/quota-recovery")):
            header = "codex-accounts" if parsed.path.startswith("/api/codex-accounts/") else "action"
            if not self._check_browser_boundary(header, require_origin=True):
                return
        if parsed.path.startswith("/api/codex-accounts/"):
            self._handle_codex_accounts(parsed.path, post=True)
            return
        if parsed.path == "/api/account-memory/remove":
            self._handle_account_memory_remove()
            return
        if parsed.path == "/api/quota-recovery":
            self._handle_quota_recovery()
            return
        if parsed.path == "/api/restart":
            self._handle_restart()
            return
        if parsed.path in ("/api/refresh", "/api/network"):
            try:
                query = parse_qs(parsed.query)
                action = refresh_endpoint if parsed.path == "/api/refresh" else self.server.routes["/api/network"]
                self._send_json(action(query))
            except Exception:
                logger.exception("refresh action failed")
                self._send_json({"error": "刷新失败，请检查服务状态。"}, status=500)
            return
        self.send_error(404)

    def do_CONNECT(self):
        if not client_network.handle(self):
            self.send_error(404)

    @staticmethod
    def _hub_action_path(path):
        return path.startswith('/api/codex-accounts') or path in (
            '/api/account-memory/remove', '/api/quota-recovery', '/api/refresh')

    def _local_network_address(self):
        try:
            address = urlparse('http://' + self.headers.get('Host', ''))
            return (ipaddress.ip_address(self.client_address[0]).is_loopback
                    and address.hostname in ('127.0.0.1', 'localhost', '::1')
                    and (address.port or 80) == self.server.server_port
                    and not address.username and not address.password)
        except ValueError:
            return False

    def _check_local_network_boundary(self, *, post=False, send_body=True):
        if not self._local_network_address():
            self._send_json({'error': '本机网络仅能从本机回环网址读取。请在这台电脑运行 agent-monitor open。'},
                            status=403, send_body=send_body)
            return False
        return self._check_browser_boundary('local-network', require_origin=post, send_body=send_body)

    def _check_browser_boundary(self, header, *, require_origin=False, send_body=True):
        # This remains a trusted personal-network service. These checks stop
        # browser cross-site requests; they do not authenticate LAN clients.
        host = self.headers.get("Host", "")
        try:
            hostname = urlparse("//" + host).hostname
        except ValueError:
            hostname = None
        try:
            ipaddress.ip_address(hostname or "")
            valid_host = True
        except ValueError:
            allowed = {"localhost", socket.gethostname().lower()}
            allowed.update(name.split(".")[0] for name in tuple(allowed))
            valid_host = (hostname or "").lower() in allowed
            if not valid_host:
                fqdn = socket.getfqdn().lower()
                valid_host = (hostname or "").lower() in {fqdn, fqdn.split(".")[0]}
        origin = self.headers.get("Origin")
        if (not valid_host or self.headers.get("X-Agent-Monitor") != header
                or self.headers.get("Sec-Fetch-Site") == "cross-site"
                or (origin is not None and origin not in ("http://" + host, "https://" + host))
                or (require_origin and origin is None)):
            self._send_json({"error": "请从本机地址或服务主机名打开页面执行操作。"}, status=403, send_body=send_body)
            return False
        return True

    def _handle_codex_accounts(self, path, post=False, send_body=True):
        if not post and not self._check_browser_boundary("codex-accounts", send_body=send_body):
            return
        try:
            manager = codex_accounts.manager
            if not post and path == "/api/codex-accounts":
                ids, unavailable = manager.discover(QUOTA_VIEW.action_catalog())
                self._send_json({**manager.list(ids), "unavailable_accounts": unavailable}, send_body=send_body)
                return
            if not post:
                raise codex_accounts.ActionError("未找到此接口。", 404)
            if self.headers.get_content_type() != "application/json":
                raise codex_accounts.ActionError("请求必须使用 JSON。")
            try:
                length = int(self.headers.get("Content-Length", ""))
                if not 0 < length <= 4096:
                    raise ValueError()
                payload = json.loads(self.rfile.read(length))
                if not isinstance(payload, dict):
                    raise ValueError()
            except (ValueError, UnicodeDecodeError):
                raise codex_accounts.ActionError("请求内容无效或超过大小限制。")
            if path == "/api/codex-accounts/egress-preview":
                route = client_network.hub.browser_route(payload.get("connection"))
                selected = payload.get("profile_ids", [])
                if (not isinstance(selected, list) or len(selected) > 200
                        or any(not isinstance(pid, str) or len(pid) > 128 for pid in selected)):
                    raise codex_accounts.ActionError("预览账号选择无效。")
                result = account_egress.preview(route)
                if selected:
                    result["confirmation"] = route.issue(selected, payload.get("expected_batch_id"))
            elif path == "/api/codex-accounts/network-status":
                route = client_network.hub.browser_route(payload.get("connection"))
                result = {"label": route.label, "connected": True}
            elif path == "/api/codex-accounts/add":
                result = manager.add(payload.get("email"), payload.get("account_id"))
            elif path == "/api/codex-accounts/batch-start":
                if "expected_batch_id" not in payload:
                    raise codex_accounts.ActionError("请先读取当前批次再操作。")
                ids, unavailable = manager.discover(QUOTA_VIEW.action_catalog())
                selected = ids
                if "profile_ids" in payload:
                    requested = payload["profile_ids"]
                    if (not isinstance(requested, list) or not requested
                            or any(not isinstance(pid, str) or not pid for pid in requested)
                            or len(set(requested)) != len(requested)):
                        raise codex_accounts.ActionError("请选择一个或多个不同的账号。")
                    if not set(requested).issubset(ids):
                        raise codex_accounts.ActionError("所选账号已不在当前登录记录中，请刷新后重新选择。", 409)
                    selected = set(requested)
                if not selected:
                    raise codex_accounts.ActionError("尚未发现可发送的 Codex 登录账号。")
                route = client_network.hub.browser_route(payload.get("connection"))
                route.consume(payload.get("confirmation"), selected, payload["expected_batch_id"])
                manager.start_batch(payload["expected_batch_id"], selected, network_route=route)
                result = {**manager.list(ids), "unavailable_accounts": unavailable}
            elif path == "/api/codex-accounts/batch-retry":
                batch = manager.read_batch()
                route = client_network.hub.browser_route(payload.get("connection"))
                if not batch or (batch.get("network_device") or {}).get("id") != route.id:
                    raise codex_accounts.ActionError("请连接本轮原设备；旧版批次需重新查看出口并明确开始新一轮。", 409)
                ids, unavailable = manager.discover(QUOTA_VIEW.action_catalog())
                manager.retry_batch(payload.get("batch_id"), ids)
                result = {**manager.list(ids), "unavailable_accounts": unavailable}
            elif path == "/api/codex-accounts/start":
                manager.directory(payload.get("id"))
                raise codex_accounts.ActionError("请通过开始新一轮查看设备出口并确认后发送。", 409)
            elif path == "/api/codex-accounts/refresh":
                result = manager.start(payload.get("id"), refresh_only=True)
            elif path == "/api/codex-accounts/login":
                result = manager.start(payload.get("id"), login_only=True)
            elif path == "/api/codex-accounts/resume":
                result = manager.resume(payload.get("id"), payload.get("operation_id"), payload.get("pause_id"))
            elif path == "/api/codex-accounts/cancel":
                result = manager.cancel(payload.get("id"))
            elif path == "/api/codex-accounts/forget-login":
                result = manager.forget_login(payload.get("id"))
            elif path == "/api/codex-accounts/release-rate-limit":
                result = manager.release_rate_limit(payload.get("id"))
            else:
                raise codex_accounts.ActionError("未找到此接口。", 404)
            self._send_json(result)
        except (codex_accounts.ActionError, client_network.NetworkError) as exc:
            self._send_json({"error": str(exc)}, status=exc.status, send_body=send_body)
        except Exception as exc:
            failure_id = diagnostics.monitor.record_failure("account_api", exc)
            self._send_json({"error": "账号操作失败，请检查服务主机的诊断事件；诊断标识：" + failure_id},
                            status=500, send_body=send_body)

    def _handle_quota_recovery(self):
        from agent_monitor.quota import quota_recovery
        try:
            length = int(self.headers.get("Content-Length", ""))
            if not 0 < length <= 4096 or self.headers.get_content_type() != "application/json":
                raise ValueError()
            value = quota_recovery.validate_request(json.loads(self.rfile.read(length)))
            machine = sync.load_machine_config().by_name.get(value["machine"])
            if machine is None:
                raise ValueError()
        except (ValueError, UnicodeDecodeError):
            self._send_json({"error": "恢复目标或请求格式无效。"}, status=400)
            return
        with QUOTA_REFRESH.recovery_scope() as available:
            if not available:
                self._send_json({"error": "已有配额操作，请等待结束后再核对暂停状态。"}, status=409)
                return
            try:
                receipt, status = QUOTA_REFRESH.recover(machine, value)
                self._send_json(receipt, status=status)
            except Exception:
                self._send_json({"error": "恢复回执未能确认或保存，请核对目标机器状态；不要重复探测。"}, status=500)


    def _handle_account_memory_remove(self):
        # Browser admission is shared by every mutation in do_POST.
        if self.headers.get_content_type() != "application/json":
            self._send_json(
                {"error": "Content-Type 必须是 application/json"}, status=400
            )
            return
        try:
            content_length = int(self.headers.get("Content-Length", ""))
            if content_length < 0:
                raise ValueError("negative Content-Length")
            payload = json.loads(self.rfile.read(content_length))
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
            self._send_json({"error": "请求体必须是合法的 JSON"}, status=400)
            return
        provider = payload.get("provider") if isinstance(payload, dict) else None
        account_id = payload.get("account_id") if isinstance(payload, dict) else None
        observed_at = payload.get("observed_at") if isinstance(payload, dict) else None
        account_label = payload.get("account_label", UNSPECIFIED_ACCOUNT_LABEL) if isinstance(payload, dict) else UNSPECIFIED_ACCOUNT_LABEL
        if account_label is not UNSPECIFIED_ACCOUNT_LABEL and account_label is not None and (
            not isinstance(account_label, str) or not account_label
        ):
            self._send_json({"error": "account_label must be null or a non-empty string"}, status=400)
            return
        if provider not in ("claude", "codex") or not isinstance(
            account_id, str
        ) or not account_id or not isinstance(observed_at, str) or not observed_at or (
            parse_observed_at(observed_at) is None
        ):
            self._send_json(
                {
                    "error": (
                        "provider must be claude or codex and account_id must be "
                        "a non-empty string; observed_at must be a non-empty "
                        "ISO-8601 string"
                    )
                },
                status=400,
            )
            return
        try:
            with generation.generation_admission_snapshot() as admission:
                live_rate_limits = QUOTA_VIEW.live_from_admission(admission)
            status, receipt = ACCOUNT_MEMORY.remove(
                provider, account_id, observed_at, live_rate_limits, account_label=account_label
            )
        except Exception as exc:
            logger.exception(
                "account memory removal failed: %s: %s",
                type(exc).__name__,
                exc,
            )
            self._send_json(
                {"error": "账号记录的移除没有写入成功"},
                status=500,
            )
            return
        self._send_json(receipt, status=status)

    def _handle_restart(self):
        # HTTP threads must open independent locks rather than sharing the
        # command-line parent's inherited descriptor through process env.
        guard = deployment_guard.deployment_lock(ROOT, inherit=False)
        try:
            guard.__enter__()
        except (OSError, ValueError):
            self._send_json({"restarting": False, "error": "Installation or service change is busy or its lock is unavailable; retry after it finishes."}, status=409)
            return
        scheduled = False
        try:
            errors = _compile_check()
            if errors:
                self._send_json({"restarting": False, "error": errors[0]})
                return
            self._send_json({"restarting": True})
            try:
                self.wfile.flush()
            except OSError:
                pass
            logger.info("restart requested via /api/restart; re-executing")
            _schedule_reexec(guard)
            scheduled = True
        finally:
            if not scheduled:
                guard.__exit__(None, None, None)

    def _handle_request(self, send_body=True):
        parsed = urlparse(self.path)
        if parsed.path == '/api/runtime-context':
            target = hub_reads.origin()
            self._send_json({'host': socket.gethostname(), 'subject_id': self.server.local_network.subject_id,
                             'local_network': self._local_network_address(), 'hub_url': target,
                             'statistics_source': 'hub' if target else 'local'}, send_body=send_body)
            return
        if parsed.path == '/api/local-network':
            if self._check_local_network_boundary(send_body=send_body):
                try:
                    self._send_json(self.server.local_network.read(), send_body=send_body)
                except Exception as exc:
                    logger.warning('Local network read failed: %s', type(exc).__name__)
                    self._send_json({'error': '本机读取未完成；上次快照仍可查看，请稍后更新本机。'}, status=503, send_body=send_body)
            return
        if parsed.path == '/api/local-network/probe':
            self._send_json({'error': '主动诊断必须明确点击并使用 POST。'}, status=405, send_body=send_body)
            return
        if parsed.path == '/api/network/cache':
            self._send_json(self.server.views.network.cached(), send_body=send_body)
            return
        target = hub_reads.origin()
        if target and parsed.path.startswith('/api/session/') and not hub_reads.supports(parsed.path):
            self._send_json({'error': '会话标识无效；未读取本机统计。'}, status=400, send_body=send_body)
            return
        if target and self._hub_action_path(parsed.path):
            self._send_json({'error': '请到 Hub 查看和操作该账号。', 'hub_url': target}, status=409, send_body=send_body)
            return
        if target and hub_reads.supports(parsed.path):
            if self.headers.get('X-Agent-Monitor-Read-Hop'):
                self._send_json({'error': 'Hub 配置形成循环；请核对机器角色与 Hub 地址。'}, status=502, send_body=send_body)
                return
            try:
                self._send_json(hub_reads.read(parsed.path, parse_qs(parsed.query), target=target),
                                send_body=send_body, compress=True)
            except Exception as exc:
                logger.warning('Hub read failed: %s', type(exc).__name__)
                self._send_json({'error': 'Hub 数据暂不可用；本机网络仍可查看，统计未替换为本机数据。',
                                 'source': target, 'failure_type': type(exc).__name__}, status=502, send_body=send_body)
            return
        if parsed.path == "/api/quota-recovery":
            query = parse_qs(parsed.query)
            config = sync.load_machine_config()
            name = _first(query, "machine", "")
            machine = next((m for m in config.by_name.values() if m.is_self), None) if name == "self" else config.by_name.get(name)
            if machine is None:
                self._send_json({"error": "请选择已配置的机器。"}, status=400, send_body=send_body)
                return
            try:
                self._send_json(QUOTA_REFRESH.recovery_status(machine), send_body=send_body)
            except Exception:
                self._send_json({"error": "未取得可核验的当前状态；暂停未解除，未发出上游请求。"}, status=503, send_body=send_body)
            return
        if parsed.path == "/api/codex-accounts":
            self._handle_codex_accounts(parsed.path, send_body=send_body)
            return
        query = parse_qs(parsed.query)
        if parsed.path == "/api/refresh" or (
                parsed.path.startswith("/api/") and "1" in query.get("force", ())):
            self._send_json({"error": "显式刷新请使用 POST。"}, status=405, send_body=send_body)
            return
        if self._local_network_address() and hub_reads.supports(parsed.path):
            # Opening a local dashboard is a read, including standalone installs.
            # Collection/quota updates remain behind the explicit POST refresh.
            query['sync'] = ['0']
        try:
            if parsed.path.startswith("/api/session/"):
                session_id = unquote(parsed.path[len("/api/session/") :])
                self._send_json(session_detail(session_id, _first(query, "machine", ""), query, views=self.server.views), send_body=send_body, compress=True)
                return
            route = self.server.routes.get(parsed.path)
            if route:
                self._send_json(route(query), send_body=send_body, compress=True)
                return
            if parsed.path in ("/", "/explore", "/sessions", "/network", "/guide", "/collection", "/ip-check-docs") or parsed.path in GUIDE_PAGES or parsed.path.startswith("/web/"):
                self._serve_static(parsed.path, send_body=send_body)
                return
            self.send_error(404)
        except query_view.ViewUnavailable as exc:
            self._send_json({"error": str(exc)}, status=503, send_body=send_body)
        except Exception as exc:
            logger.exception("Request failed: %s", exc)
            self.send_error(500, str(exc))

    def log_message(self, fmt, *args):
        logger.info("%s - %s", self.address_string(), fmt % args)

    def _send_json(self, payload, status=200, send_body=True, compress=False):
        with diagnostics.monitor.stage("json_encode"):
            data = json.dumps(payload, default=_json_default).encode("utf-8")
        self._send_json_bytes(data, status=status, send_body=send_body, compress=compress)

    def _send_json_bytes(self, data, status=200, send_body=True, compress=False):
        encoded = False
        if compress:
            data, encoded = asset_delivery.encode(data, self.headers)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        if compress:
            self.send_header("Vary", "Accept-Encoding")
        if encoded:
            self.send_header("Content-Encoding", "gzip")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if send_body:
            with diagnostics.monitor.stage("response_body_write"):
                self.wfile.write(data)
        self._response_complete = True

    def _serve_static(self, path, send_body=True):
        if path == "/":
            file_path = WEB_ROOT / "index.html"
        elif path == "/explore":
            file_path = WEB_ROOT / "explore.html"
        elif path == "/sessions":
            file_path = WEB_ROOT / "sessions.html"
        elif path == "/network":
            file_path = WEB_ROOT / "network.html"
        elif path == "/collection":
            file_path = WEB_ROOT / "collection.html"
        elif path == "/guide":
            file_path = WEB_ROOT / "guide.html"
        elif path in GUIDE_PAGES:
            file_path = WEB_ROOT / GUIDE_PAGES[path]
        elif path == "/ip-check-docs":
            file_path = ROOT / "ip_check" / "README.md"
        elif path.startswith("/web/"):
            # Containment is judged on the requested path, not on where it
            # resolves: hub deployments symlink web/vendor to the checkout that
            # ran install.sh, and judging the resolved target sent every asset
            # behind that link to 404 — three blank charts and system fonts,
            # with no message. Traversal is a property of the request itself.
            normalized = posixpath.normpath(path)
            if not normalized.startswith("/web/"):
                self.send_error(404)
                return
            file_path = WEB_ROOT / normalized[len("/web/"):]
        else:
            self.send_error(404)
            return

        try:
            resolved = file_path.resolve()
            if not resolved.is_file():
                self.send_error(404)
                return
            version, assets = asset_delivery.snapshot(WEB_ROOT)
            if file_path.is_relative_to(WEB_ROOT):
                data = assets[file_path.relative_to(WEB_ROOT).as_posix()]
            else:
                data = asset_delivery.read(resolved)
        except (OSError, KeyError):
            self.send_error(404)
            return

        content_type = mimetypes.guess_type(str(resolved))[0]
        if content_type is None and resolved.suffix == ".md":
            content_type = "text/markdown"
        if content_type is None:
            content_type = "application/octet-stream"
        cacheable = path.startswith("/web/") and resolved.suffix != ".html"
        if resolved.suffix == ".html":
            data = asset_delivery.html(data, version)
        request_version = parse_qs(urlparse(getattr(self, "path", "")).query).get("v", [])
        cache_control = ("private, max-age=31536000, immutable"
                         if cacheable and request_version == [version]
                         else "no-cache" if cacheable else "no-store")
        headers = getattr(self, "headers", {})
        compressible = resolved.suffix in {".html", ".css", ".js", ".svg", ".md"}
        encoded = False
        if compressible:
            data, encoded = asset_delivery.encode(data, headers)
        etag = '"%s"' % hashlib.sha256(data).hexdigest()
        if cacheable and etag in [part.strip().removeprefix("W/") for part in
                                 getattr(self, "headers", {}).get("If-None-Match", "").split(",")]:
            self.send_response(304)
            self.send_header("ETag", etag)
            self.send_header("Cache-Control", cache_control)
            if compressible:
                self.send_header("Vary", "Accept-Encoding")
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", cache_control)
        if compressible:
            self.send_header("Vary", "Accept-Encoding")
        if encoded:
            self.send_header("Content-Encoding", "gzip")
        if cacheable:
            self.send_header("ETag", etag)
        self.end_headers()
        if send_body:
            self.wfile.write(data)


def _first(query, key, default):
    values = query.get(key)
    return values[0] if values else default


def _json_default(value):
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError("Unsupported JSON value: %r" % (value,))


def main():
    global _BIND_HOST, _BIND_PORT
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=39001)
    args = parser.parse_args()
    _BIND_HOST, _BIND_PORT = args.host, args.port

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    diagnostics.monitor.start(ROOT / "state" / "diagnostics")
    COLLECTION.start()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    logger.info("agent-monitor listening on http://%s:%s", args.host, args.port)
    def terminate(signum, frame):
        raise SystemExit(0)
    signal.signal(signal.SIGTERM, terminate)
    if hub.is_hub():
        state = ROOT / "state"
        state.mkdir(parents=True, exist_ok=True)
        # launchd bypasses the standalone launcher; register this exact instance.
        process_identity.record(state, ROOT, os.getpid())
        (state / "port").write_text(str(args.port))
        (state / "pid").write_text(str(os.getpid()))

    COLLECTION.start_schedule()
    try:
        server.serve_forever()
    finally:
        COLLECTION.stop()
        diagnostics.monitor.close()
        try:
            codex_accounts.manager.close()
        finally:
            client_network.hub.close()
            COLLECTION.close()
            server.server_close()


if __name__ == "__main__":
    main()
