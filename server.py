import argparse
import ipaddress
import socket
import hashlib
import json
import logging
import math
import mimetypes
import os
import posixpath
import signal
import subprocess
import sys
import threading
import time
import uuid
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from dataclasses import replace
from project_alias import canonical_project
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer as _ThreadingHTTPServer
from socketserver import TCPServer
from pathlib import Path
import codex_accounts
import network_policy
import account_egress
import client_network
from urllib.parse import parse_qs, unquote, urlparse
from zoneinfo import ZoneInfo

import rollup
import generation
import statistics_snapshot
from session_identity import SessionIndex
from session_cache import SessionCache
from conversation_cache import ConversationCache
import hub
import sync
import process_identity
import query_view
import quota_sync
import diagnostics
import asset_delivery
import quota_applicability
from project_paths import PROJECT_ROOT
from agent_monitor import source_inventory
from agent_monitor.account_memory import AccountMemory, UNSPECIFIED_ACCOUNT_LABEL, parse_observed_at
from agent_monitor.collection import CollectionCoordinator
from agent_monitor.quota_view import QuotaView
from aggregators import extract_metric, load_all_entries
from pricing_fetcher import is_estimated_pricing_model


ROOT = PROJECT_ROOT
WEB_ROOT = ROOT / "web"
GUIDE_PAGES = {
    "/guide/sources": "guide.html",
    "/guide/collection": "guide-collection.html",
    "/guide/setup": "guide-setup.html",
    "/guide/questions": "guide-questions.html",
}
logger = logging.getLogger("agent-monitor")
_NETWORK_CACHE = {"ts": 0.0, "data": None}
_NETWORK_TTL = 60.0
_NETWORK_LOCK = threading.Lock()
_SESSION_CACHE = SessionCache()
_CONVERSATION_CACHE = ConversationCache()
RANGE_DAYS = {"7d": 7, "30d": 30, "90d": 90, "6m": 180, "1y": 365, "2y": 730}
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


def _source_files():
    """Python modules whose code is frozen into this running process. Static
    assets (web/*) are re-read from disk per request and never go stale, so only
    the imported .py files matter for detecting code drift."""
    return source_inventory.python_sources(ROOT)


def _web_files():
    return sorted(asset_delivery.files(WEB_ROOT))


def _source_signature():
    digest = hashlib.sha256()
    for path in _source_files():
        try:
            digest.update(str(path.relative_to(ROOT)).encode())
            digest.update(path.read_bytes())
        except OSError:
            continue
    return digest.hexdigest()[:16]


def _web_signature():
    return asset_delivery.signature(WEB_ROOT)


# Signature of the code this process actually loaded. health() compares it to a
# fresh signature each call, so the server self-reports when its on-disk source
# has changed since boot (the symptom: a long-lived daemon serving old code).
BOOT_SIGNATURE = _source_signature()
import service_runtime
import deployment_guard
BOOT_DEPLOYMENT_SIGNATURE = service_runtime.signature(ROOT)


def diagnostics_endpoint(_query):
    return diagnostics.monitor.snapshot()


def overview(query):
    completed_before = COLLECTION.snapshot()["completed_at"]
    sync_started = COLLECTION.request(query)
    with COLLECTION.admission() as admission:
        with rollup.use_admitted_generations(admission.admitted):
            return _overview_from_admission(
                query,
                admission,
                completed_before=completed_before,
                sync_started=sync_started,
            )


def _overview_from_admission(
    query,
    admission,
    *,
    completed_before,
    sync_started,
):
    now = datetime.now().astimezone()
    range_value = _first(query, "range", "30d")
    rollup_range_window = rollup.range_window(range_value, now)
    cost_granularity = _auto_time_dim(range_value)
    cost_over_time = rollup.query_pivot(
        cost_granularity, "agent", "cost", time_range=rollup_range_window
    )
    today = now.astimezone(rollup.BUCKET_TIMEZONE).date()
    today_window = (today, today)
    week_window = (today - timedelta(days=today.weekday()), today)
    month_window = (today.replace(day=1), today)
    week_summary = _rollup_summary(week_window)
    week_summary["window"] = {
        "start": _start_of_week(now).isoformat(),
        "end": now.isoformat(),
    }

    sync_status = dict(COLLECTION.status(now=now, admission=admission))
    sync_status["refresh_pending"] = bool(
        sync_started
        or sync_status["syncing"]
        or sync_status.get("completed_at") != completed_before
    )
    result = {
        "rate_limits": QUOTA_VIEW.rate_limits(
            admission=admission,
            sync_status=sync_status,
        ),
        "sync": sync_status,
        "today": _rollup_summary(today_window),
        "week": week_summary,
        "range": _rollup_summary(rollup_range_window),
        "daily_cost_30d": _daily_cost_from_pivot(
            rollup.query_pivot(
                "day", "agent", "cost", time_range=rollup.range_window("30d", now)
            )
        ),
        "cost_over_time": cost_over_time,
        "cost_over_time_granularity": cost_granularity,
        "rollup_coverage": _rollup_coverage(rollup_range_window),
        "top_projects_week": _top_projects_from_rollup(week_window, limit=5),
        "model_mix_month": _model_mix_from_rollup(month_window),
        "attribution_windows": {
            "week": {"start": week_window[0].isoformat(), "end": today.isoformat()},
            "month": {"start": month_window[0].isoformat(), "end": today.isoformat()},
        },
        "history_gap": None,
        "codex_cost_estimated": True,
    }
    if _first(query, "compare", "") == "1":
        result["comparison"] = _period_comparison(range_value, now)
    return result


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


def cost_diagnostics_endpoint(query):
    """Group unpriced observations without starting remote collection."""
    time_range = _session_window(query, datetime.now().astimezone())
    counts = defaultdict(int)

    def collect(entries, machine, aliases=None):
        projects = [canonical_project(value, aliases) for value in query.get("project", [])]
        for entry in _filter_time(entries, time_range):
            if entry.cost_usd is not None:
                continue
            if projects and canonical_project(entry.project, aliases) not in projects:
                continue
            if query.get("agent") and entry.agent_id not in query["agent"]:
                continue
            if query.get("model") and entry.model not in query["model"]:
                continue
            reason = (entry.cost_provenance or {}).get("reason") or "unrecorded"
            counts[(machine, entry.agent_id, entry.model, reason)] += 1

    if hub.enabled():
        with generation.generation_admission_snapshot() as admission:
            for current in admission.admitted:
                if query.get("machine") and current.host not in query["machine"]:
                    continue
                selection = {key: query[key] for key in ("agent", "model") if key in query}
                selection["cost_status"] = ["unknown"]
                entries = statistics_snapshot.read_admitted_usage(current, time_range=time_range, query=selection)
                collect(entries, current.host, (current.meta or {}).get("aliases"))
    else:
        machine = sync.load_machine_config().self_machine.name
        if not query.get("machine") or machine in query["machine"]:
            collect(load_all_entries(read_only=True, diagnostic=True), machine)
    return {"groups": [dict(zip(("machine", "agent", "model", "reason"), key), entries=count)
                       for key, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))]}


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


def sessions_endpoint(query):
    cost_status = _first(query, "cost_status", "")
    if cost_status not in ("", "unknown"):
        raise ValueError("Unsupported cost_status: %s" % cost_status)
    now = datetime.now().astimezone()
    time_range = _session_window(query, now)
    sessions = []
    options = {key: set() for key in ("machine", "agent", "project", "model")}
    with_filters = _first(query, "with_filters", "") == "1"

    def collect(entries, machine=None, aliases=None, *, index=None, projects=None, with_offset=False):
        source_sessions = []
        source_options = {key: set() for key in options}
        index = index if index is not None else SessionIndex(getattr(entries, "session_metadata", ()))
        # Match Explore display identities without rewriting stored usage.
        projects = projects if projects is not None else {}
        in_window = []
        offsets = {}
        for offset, entry in enumerate(entries):
            if time_range is not None and not time_range[0] <= entry.timestamp.astimezone() <= time_range[1]:
                continue
            if entry.project not in projects:
                projects[entry.project] = canonical_project(entry.project, aliases)
            project = projects[entry.project]
            entry = entry if project == entry.project else replace(entry, project=project)
            in_window.append(entry)
            if with_offset:
                offsets[id(entry)] = offset
        display_query = dict(query)
        if query.get("project"):
            display_query["project"] = [canonical_project(value, aliases)
                                        for value in query["project"]]
        if with_filters:
            if machine and in_window:
                source_options["machine"].add(machine)
            for entry in in_window:
                source_options["agent"].add(entry.agent_id)
                source_options["project"].add(entry.project)
                source_options["model"].add(entry.model)
        source_options = {key: sorted(values) for key, values in source_options.items()}
        if _first(query, "machine", "") not in ("", machine):
            return source_sessions, source_options, None
        visible = _filter_values(in_window, display_query)
        if cost_status == "unknown":
            visible = [entry for entry in visible if entry.cost_usd is None]
        for key, items in index.group(visible).items():
            row = _conversation_stats(items, key, index, aliases)
            if machine:
                row["machine"] = machine
            source_sessions.append(row)
        return source_sessions, source_options, offsets[id(visible[0])] if with_offset and visible else None

    def merge(result):
        rows, filters = result[:2]
        sessions.extend(rows)
        for key, values in filters.items():
            options[key].update(values)

    if hub.enabled():
        COLLECTION.request(query)
        with generation.generation_admission_snapshot() as admission:
            for current in admission.admitted:
                if not with_filters and _first(query, "machine", "") not in ("", current.host):
                    continue
                result = _SESSION_CACHE.get(current, query, time_range)
                if result is None:
                    aliases = (getattr(current, "meta", None) or {}).get("aliases")
                    grouped = _CONVERSATION_CACHE.read(current, query, time_range,
                        lambda entries, index, projects: collect(entries, current.host, aliases,
                            index=index, projects=projects, with_offset=True))
                    if grouped is None:
                        entries = statistics_snapshot.read_admitted_usage(
                            current, time_range=time_range, query=None if with_filters else
                            {key: value for key, value in query.items() if key != "project"})
                        result = collect(entries, current.host, aliases)
                        _SESSION_CACHE.put(current, query, time_range, entries, result)
                    else:
                        result, reusable = grouped
                        if reusable:
                            # Every bucket checked all candidate timestamps;
                            # no retained event objects are needed for this guard.
                            _SESSION_CACHE.put(current, query, time_range, (), result)
                merge(result)
    else:
        merge(collect(load_all_entries(), sync.load_machine_config().self_machine.name))
    sort = _first(query, "sort", "time")
    sessions.sort(key=lambda row: _session_sort_key(row, sort),
                  reverse=_first(query, "order", "desc") != "asc")
    if with_filters:
        return {"sessions": sessions, "filters": {key: sorted(v for v in values if v)
                                                 for key, values in options.items()}}
    return sessions


def session_detail(session_id, machine=None, query=None):
    # Direct Python callers retain the old all-records default; the HTTP/UI
    # passes its actual list context, including the ordinary 30-day default.
    query = {"scope": ["all"]} if query is None else query
    scope = _first(query, "scope", "filtered")
    if scope not in ("all", "filtered"):
        raise ValueError("Unsupported session scope")
    if _first(query, "cost_status", "") not in ("", "unknown"):
        raise ValueError("Unsupported cost_status")
    agent = _first(query, "session_agent", "")
    window = None if scope == "all" else _session_window(query, datetime.now().astimezone())
    aliases = None
    if hub.enabled():
        with generation.generation_admission_snapshot() as admission:
            selected = [current for current in admission.admitted if current.host == machine]
            if not selected:
                return {"meta": None, "entries": [], "scope": scope}
            current = selected[0]
            entries = statistics_snapshot.read_admitted_usage(current, session_id=session_id, session_agent=agent,
                time_range=window, query=None if scope == "all" else
                {key: value for key, value in query.items() if key != "project"})
            aliases = (getattr(current, "meta", None) or {}).get("aliases")
    else:
        entries = load_all_entries()
    index = SessionIndex(getattr(entries, "session_metadata", ()))
    keys = {(entry.agent_id, entry.session_id) for entry in entries if entry.session_id == session_id and (not agent or entry.agent_id == agent)}
    keys.update(key for key in index.metadata if key[1] == session_id and (not agent or key[0] == agent))
    roots = {index.root(key) for key in keys}
    if len(roots) > 1:
        raise ValueError("Session ID is ambiguous; specify session_agent")
    if not roots:
        return {"meta": None, "entries": [], "scope": scope}
    root = next(iter(roots))
    entries = [replace(entry, project=canonical_project(entry.project, aliases)) for entry in entries
               if index.root((entry.agent_id, entry.session_id)) == root]
    if scope != "all":
        display_query = dict(query)
        if query.get("project"):
            display_query["project"] = [canonical_project(value, aliases) for value in query["project"]]
        entries = _filter_values(_filter_time(entries, window), display_query)
        if _first(query, "cost_status", "") == "unknown":
            entries = [entry for entry in entries if entry.cost_usd is None]
    entries.sort(key=lambda entry: entry.timestamp)
    if not entries:
        return {"meta": None, "entries": [], "scope": scope}
    meta = _conversation_stats(entries, root, index, aliases)
    meta["machine"] = machine
    meta["executions"] = []
    for key, items in _group_execution_sessions(entries).items():
        identity = index.metadata.get(key, {})
        meta["executions"].append({**_session_stats(items), "title": identity.get("title"),
                                   "parent_session_id": identity.get("parent_session_id"), "is_main": key == root})
    return {"meta": meta, "entries": [_entry_json(entry) for entry in entries], "scope": scope}


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
    for path in _source_files():
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
            os.execv(
                sys.executable,
                [sys.executable, str(ROOT / "server.py"), "--host", _BIND_HOST, "--port", str(_BIND_PORT)],
            )
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


def network(query):
    return {**_network_report(query), "checked_host": socket.gethostname()}


def _network_report(query):
    # A refresh click shares the same request budget as polling. Hold the lock
    # through the bounded child call so concurrent clients share failures too.
    with _NETWORK_LOCK:
        now = time.monotonic()
        if (_NETWORK_CACHE["data"] is not None
                and now - _NETWORK_CACHE["ts"] < _NETWORK_CACHE.get("ttl", _NETWORK_TTL)):
            return _NETWORK_CACHE["data"]
        data = _run_network_report()
        delay = data.get("retry_after_seconds")
        ttl = _NETWORK_TTL
        if (not isinstance(delay, bool) and isinstance(delay, (int, float))
                and math.isfinite(delay) and delay >= 0):
            ttl = max(ttl, delay)
        _NETWORK_CACHE.update(ts=time.monotonic(), data=data, ttl=ttl)
        return data


def _run_network_report():
    try:
        result = subprocess.run(["ip-check", "--json"], capture_output=True, text=True, timeout=30,
                                env=network_policy.child_environment(os.environ))
        if result.returncode != 0:
            return {
                "error": result.stderr or "ip-check exited non-zero",
                "installed": True,
                "verdict": "unknown",
            }
        data = json.loads(result.stdout)
        if not isinstance(data, dict):
            return {"error": "ip-check returned an invalid report", "installed": True, "verdict": "unknown"}
        return data
    except network_policy.NetworkPolicyError as exc:
        return {"error": str(exc), "installed": True, "verdict": "unknown"}
    except FileNotFoundError:
        return {
            "error": "PATH 里找不到 ip-check 命令",
            "installed": False,
            "verdict": "unknown",
            "hint": "运行 ./install.sh 安装",
        }
    except subprocess.TimeoutExpired:
        return {
            "error": "ip-check 超时（>30s），外部 API 可能较慢",
            "installed": True,
            "verdict": "unknown",
        }
    except json.JSONDecodeError as exc:
        return {
            "error": f"ip-check returned invalid JSON: {exc}",
            "installed": True,
            "verdict": "unknown",
        }
    except OSError as exc:
        return {"error": "无法启动 ip-check：" + diagnostics.failure_summary(
            diagnostics.failure_details(exc, stage="network_start")), "installed": True, "verdict": "unknown"}


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


class ThreadingHTTPServer(_ThreadingHTTPServer):
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
                action = refresh_endpoint if parsed.path == "/api/refresh" else network
                self._send_json(action(query))
            except Exception:
                logger.exception("refresh action failed")
                self._send_json({"error": "刷新失败，请检查服务状态。"}, status=500)
            return
        self.send_error(404)

    def do_CONNECT(self):
        if not client_network.handle(self):
            self.send_error(404)

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
        import quota_recovery
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
        with QUOTA_REFRESH.condition:
            if QUOTA_REFRESH.running or QUOTA_REFRESH.recovering:
                self._send_json({"error": "已有配额操作，请等待结束后再核对暂停状态。"}, status=409)
                return
            QUOTA_REFRESH.recovering = True
        try:
            with generation.generation_admission_snapshot() as admission:
                current = next((item for item in admission.admitted if item.host == machine.name), None)
                if current is None:
                    self._send_json({"error": "目标机器尚未通过来源确认。"}, status=409)
                    return
                meta = dict(current.meta)
                limits = QUOTA_REFRESH.merge(current, machine)
            before = limits.get("codex") or {}
            if (before.get("account_id"), before.get("account_label")) != (value["account_id"], value["account_label"]):
                self._send_json({"error": "页面账号已变化，请重新核对。"}, status=409)
                return
            receipt = quota_recovery.run(machine, value)
            if receipt.get("ok"):
                block = receipt.get("block") or {}
                if (receipt.get("source_host_identity") != meta["source_host_identity"]
                        or (block.get("account_id"), block.get("account_label")) != (value["account_id"], value["account_label"])):
                    raise ValueError("recovery identity mismatch")
                # A signed-in independent provider may have no observation yet.
                recovered_limits = {p: dict(limits.get(p) or {}) for p in ("claude", "codex")}
                recovered_limits["codex"] = block
                payload = {"schema_version": 1, "source_host_identity": receipt["source_host_identity"],
                           "exporter_commit": receipt["exporter_commit"], "rate_limits": recovered_limits}
                quota_sync._validate(payload)
                with QUOTA_REFRESH.condition:
                    QUOTA_REFRESH.records[machine.name] = {"ssh_host": machine.ssh_host, "payload": payload,
                        "attempted_at": datetime.now(timezone.utc).isoformat(), "error": None}
                    hub._write_quota_message(QUOTA_REFRESH.path, {"version": 1, "machines": QUOTA_REFRESH.records})
                    QUOTA_REFRESH.revision += 1
                receipt = {"ok": True, "message": "有效读数已保存，目标机器的 Codex 自动查询已恢复。",
                           "network": receipt.get("network", {"coverage": "unavailable", "events": []})}
            self._send_json(receipt, status=200 if receipt.get("ok") else 409)
        except Exception:
            self._send_json({"error": "恢复回执未能确认或保存，请核对目标机器状态；不要重复探测。"}, status=500)
        finally:
            with QUOTA_REFRESH.condition:
                QUOTA_REFRESH.recovering = False

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
        if parsed.path == "/api/codex-accounts":
            self._handle_codex_accounts(parsed.path, send_body=send_body)
            return
        query = parse_qs(parsed.query)
        if parsed.path == "/api/refresh" or (
                parsed.path.startswith("/api/") and "1" in query.get("force", ())):
            self._send_json({"error": "显式刷新请使用 POST。"}, status=405, send_body=send_body)
            return
        try:
            if parsed.path.startswith("/api/session/"):
                session_id = unquote(parsed.path[len("/api/session/") :])
                self._send_json(session_detail(session_id, _first(query, "machine", ""), query), send_body=send_body, compress=True)
                return
            route = ROUTES.get(parsed.path)
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


def _session_window(query, now):
    if _first(query, "range", "30d") != "custom":
        return _range_window(_first(query, "range", "30d"), now)
    start, end = _pivot_window(query)
    return (datetime.combine(start, datetime.min.time(), tzinfo=rollup.BUCKET_TIMEZONE),
            datetime.combine(end, datetime.max.time(), tzinfo=rollup.BUCKET_TIMEZONE))


def _period_comparison(range_value, now):
    if range_value == "all":
        return {"available": False, "reason": "全部历史没有等长的前一期"}
    days = RANGE_DAYS.get(range_value, 30)
    end = now.astimezone(rollup.BUCKET_TIMEZONE).date() - timedelta(days=1)
    start = end - timedelta(days=days - 1)
    previous = (start - timedelta(days=days), start - timedelta(days=1))
    def period(window):
        return {"start": window[0].isoformat(), "end": window[1].isoformat(),
                **rollup.period_summary(window)}
    current, prior = period((start, end)), period(previous)
    known = current["known_cost_usd"] is not None and prior["known_cost_usd"] is not None
    return {"available": True, "timezone": str(rollup.BUCKET_TIMEZONE), "days": days,
            "current": current, "previous": prior,
            "known_cost_delta_usd": current["known_cost_usd"] - prior["known_cost_usd"] if known else None,
            "coverage_complete": None}


def _range_window(value, now):
    calendar_window = rollup.range_window(value, now)
    if calendar_window is None:
        return None
    start_date, _ = calendar_window
    start = datetime.combine(
        start_date, datetime.min.time(), tzinfo=rollup.BUCKET_TIMEZONE
    )
    return start, now


def _auto_time_dim(range_value):
    if range_value == "all":
        return "month"
    days = RANGE_DAYS.get(range_value, 30)
    if days <= 90:
        return "day"
    if days <= 365:
        return "week"
    return "month"


def _rollup_coverage(range_window):
    earliest = rollup.earliest_rollup_date()
    range_start = None
    partial = False
    if earliest:
        if range_window is None:
            partial = True
        else:
            range_start = rollup._calendar_date_bounds(range_window)[0].isoformat()
            partial = range_start < earliest
    return {
        "earliest_date": earliest,
        "range_start": range_start,
        "partial_before_range": partial,
    }


def _filter_time(entries, time_range):
    if time_range is None:
        return list(entries)
    start, end = time_range
    return [entry for entry in entries if start <= entry.timestamp.astimezone() <= end]


def _filter_values(entries, query):
    agents = set(query.get("agent", []))
    projects = set(query.get("project", []))
    models = set(query.get("model", []))
    result = []
    for entry in entries:
        if agents and entry.agent_id not in agents:
            continue
        if projects and entry.project not in projects:
            continue
        if models and entry.model not in models:
            continue
        result.append(entry)
    return result


def _summary(entries):
    by_agent = defaultdict(float)
    total_cost = 0.0
    for entry in entries:
        if entry.cost_usd is not None:
            total_cost += entry.cost_usd
            by_agent[entry.agent_id] += entry.cost_usd
    return {
        "cost_usd": total_cost,
        "tokens": sum(extract_metric(entry, "total") for entry in entries),
        "by_agent": {"claude-code": by_agent["claude-code"], "codex": by_agent["codex"]},
    }


def _rollup_summary(time_range):
    cost_pivot = rollup.query_pivot(
        "agent", "none", "cost", time_range=time_range
    )
    cost_rows = cost_pivot["rows"]
    token_rows = rollup.query_pivot(
        "agent", "none", "total", time_range=time_range
    )["rows"]
    by_agent = {"claude-code": None, "codex": None}
    for row in cost_rows:
        value = row["values"].get("value")
        if value is not None:
            by_agent[row["x"]] = value
    return {
        "cost_usd": (sum(value for value in by_agent.values() if value is not None)
                     if any(value is not None for value in by_agent.values()) else None),
        "tokens": sum(
            row["values"].get("value") or 0
            for row in token_rows
        ),
        "by_agent": by_agent,
        "cost_coverage": cost_pivot.get("cost_coverage"),
    }


def _daily_cost_from_pivot(pivot):
    return [
        {
            "date": row["x"],
            "claude_cost": row["values"].get("claude-code"),
            "codex_cost": row["values"].get("codex"),
        }
        for row in pivot["rows"]
    ]


def _top_projects_from_rollup(time_range, limit):
    pivot = rollup.query_pivot(
        "project", "none", "cost", time_range=time_range
    )
    return [
        {"project": row["x"], "cost_usd": row["values"].get("value"),
         "cost_coverage": row.get("cost_coverage", {}).get("value")}
        for row in pivot["rows"]
        if row["values"].get("value") is not None
    ][:limit]


def _model_mix_from_rollup(time_range):
    pivot = rollup.query_pivot(
        "model", "none", "total", time_range=time_range
    )
    rows = [
        {"model": row["x"], "tokens": row["values"].get("value") or 0}
        for row in pivot["rows"]
    ]
    grand_total = sum(row["tokens"] for row in rows) or 1
    for row in rows:
        row["pct"] = row["tokens"] / grand_total
    return rows


def _daily_cost(entries, now):
    days = []
    by_day = defaultdict(lambda: {"claude_cost": 0.0, "codex_cost": 0.0})
    for entry in entries:
        day = entry.timestamp.astimezone(rollup.BUCKET_TIMEZONE).date().isoformat()
        if entry.cost_usd is None:
            continue
        key = "codex_cost" if entry.agent_id == "codex" else "claude_cost"
        by_day[day][key] += entry.cost_usd
    start = now.astimezone(rollup.BUCKET_TIMEZONE).date() - timedelta(days=29)
    for offset in range(30):
        day = (start + timedelta(days=offset)).isoformat()
        row = {"date": day}
        row.update(by_day[day])
        days.append(row)
    return days


def _top_projects(entries, limit):
    totals = defaultdict(float)
    for entry in entries:
        if entry.cost_usd is not None:
            totals[entry.project] += entry.cost_usd
    return [
        {"project": project, "cost_usd": cost}
        for project, cost in sorted(totals.items(), key=lambda item: item[1], reverse=True)[:limit]
    ]


def _model_mix(entries):
    totals = defaultdict(int)
    for entry in entries:
        totals[entry.model] += extract_metric(entry, "total")
    grand_total = sum(totals.values()) or 1
    return [
        {"model": model, "tokens": tokens, "pct": tokens / grand_total}
        for model, tokens in sorted(totals.items(), key=lambda item: item[1], reverse=True)
    ]


def _group_sessions(entries):
    sessions = defaultdict(list)
    for entry in entries:
        sessions[entry.session_id].append(entry)
    return sessions


def _group_execution_sessions(entries):
    sessions = defaultdict(list)
    for entry in entries:
        sessions[(entry.agent_id, entry.session_id)].append(entry)
    return sessions


def _conversation_stats(entries, key, index, aliases=None):
    result = _session_stats(entries)
    identity = index.metadata.get(key, {})
    own = next((entry for entry in entries if (entry.agent_id, entry.session_id) == key), None)
    result.update(session_id=key[1], agent_id=key[0], title=identity.get("title"),
                  created_at=identity.get("created_at"), account=identity.get("account"),
                  relationship_status=index.status(key),
                  subagent_count=len({(e.agent_id, e.session_id) for e in entries} - {key}),
                  project=own.project if own else canonical_project(identity.get("project") or result["project"], aliases))
    return result


def _session_stats(entries):
    entries = sorted(entries, key=lambda entry: entry.timestamp)
    known_costs = [entry.cost_usd for entry in entries if entry.cost_usd is not None]
    cost = sum(known_costs) if known_costs else None
    start = entries[0].timestamp
    end = entries[-1].timestamp
    usage_events = sum(entry.message_count for entry in entries)
    return {
        "session_id": entries[0].session_id,
        "agent_id": entries[0].agent_id,
        "project": entries[0].project,
        "model": entries[0].model,
        "started_at": start.isoformat(),
        "ended_at": end.isoformat(),
        "duration_seconds": max(int((end - start).total_seconds()), 0),
        "cost_usd": cost,
        "unpriced_records": len(entries) - len(known_costs),
        "tokens": sum(extract_metric(entry, "total") for entry in entries),
        "usage_events": usage_events,
        "estimated": any(e.agent_id == "codex" or is_estimated_pricing_model(e.model) for e in entries),
    }


def _entry_json(entry):
    return {
        "timestamp": entry.timestamp.isoformat(),
        "session_id": entry.session_id,
        "message_id": entry.message_id,
        "request_id": entry.request_id,
        "model": entry.model,
        "input_tokens": entry.input_tokens,
        "output_tokens": entry.output_tokens,
        "cache_creation_tokens": entry.cache_creation_tokens,
        "cache_read_tokens": entry.cache_read_tokens,
        "cost_usd": entry.cost_usd,
        "project": entry.project,
        "agent_id": entry.agent_id,
        "usage_event_count": entry.message_count,
        "usage_quality": entry.usage_quality,
        "service_context": entry.service_context,
        "cost_provenance": entry.cost_provenance,
    }


def _session_sort_key(row, sort):
    if sort == "cost":
        return row["cost_usd"] if row["cost_usd"] is not None else -1
    if sort == "tokens":
        return row["tokens"]
    if sort == "duration":
        return row["duration_seconds"]
    return row["ended_at"]


def _first(query, key, default):
    values = query.get(key)
    return values[0] if values else default


def _start_of_day(value):
    return value.astimezone(rollup.BUCKET_TIMEZONE).replace(
        hour=0, minute=0, second=0, microsecond=0
    )


def _start_of_week(value):
    start = _start_of_day(value)
    return start - timedelta(days=start.weekday())


def _start_of_month(value):
    return value.astimezone(rollup.BUCKET_TIMEZONE).replace(
        day=1, hour=0, minute=0, second=0, microsecond=0
    )


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
