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
from contextlib import contextmanager
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
import sync_process
import process_identity
import query_view
import quota_sync
import diagnostics
import asset_delivery
import quota_applicability
from aggregators import extract_metric, load_all_entries
from parsers import claude_status, codex
from parsers.accounts import quota_identity
from pricing_fetcher import is_estimated_pricing_model


ROOT = Path(__file__).resolve().parent
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
_SYNC_DUE_AFTER_SECONDS = 600
_STALE_AFTER_SECONDS = 6 * 60 * 60
RANGE_DAYS = {"7d": 7, "30d": 30, "90d": 90, "6m": 180, "1y": 365, "2y": 730}
_SYNC_LOCK = threading.Lock()
_ACCOUNT_MEMORY_LOCK = threading.Lock()
_ACCOUNT_MEMORY_DELETE_EPOCH = 0
_ACCOUNT_MEMORY_ACTIVE_UPSERT_EPOCHS = defaultdict(int)
_ACCOUNT_MEMORY_DELETED_AT_EPOCH = {}
_ACCOUNT_MEMORY_VERSION = 2
_UNSPECIFIED_ACCOUNT_LABEL = object()
_ACCOUNT_MEMORY_PATH = ROOT / "state" / "account_memory.json"
QUOTA_REFRESH = quota_sync.Manager(ROOT / "state" / "machine_quota.json",
                                  on_complete=lambda: _remember_accounts_after_sync_publish())
_ACCOUNT_MEMORY_ENTRY_FIELDS = frozenset(
    {
        "provider",
        "account_id",
        "account_label",
        "account_plan",
        "five_hour_used_pct",
        "five_hour_resets_at",
        "seven_day_used_pct",
        "seven_day_resets_at",
        "observed_at",
    }
)
_ACCOUNT_MEMORY_NUMERIC_FIELDS = (
    "five_hour_used_pct",
    "five_hour_resets_at",
    "seven_day_used_pct",
    "seven_day_resets_at",
)
_SYNC_STATE = {
    "running": False,
    "started_at": None,
    "completed_at": None,
    "round_machines": (),
    "last_completed_round_machines": (),
    "known_machines": None,
    "observations": {},
    "refresh_requested": 0,
    "refresh_completed": 0,
    "round_refresh": 0,
    "queued_refresh": False,
    "round_quota_refresh": True,
    "phase": None,
    "pending_machines": (),
    "statistics_pending": (),
}
SERVER_INSTANCE_ID = uuid.uuid4().hex
QUERY_VIEW = None

# Host/port this process is serving on, stashed by main() so /api/restart can
# re-exec with identical arguments.
_BIND_HOST = "127.0.0.1"
_BIND_PORT = 39001


def _source_files():
    """Python modules whose code is frozen into this running process. Static
    assets (web/*) are re-read from disk per request and never go stale, so only
    the imported .py files matter for detecting code drift."""
    files = sorted(ROOT.glob("*.py"))
    files += sorted((ROOT / "parsers").glob("**/*.py"))
    return files


def _web_files():
    return sorted(asset_delivery.files(WEB_ROOT))


def _source_signature():
    digest = hashlib.sha256()
    for path in _source_files():
        try:
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


@contextmanager
def _query_admission():
    context = QUERY_VIEW.snapshot() if QUERY_VIEW is not None else generation.generation_admission_snapshot()
    with context as admission:
        yield admission


def diagnostics_endpoint(_query):
    return diagnostics.monitor.snapshot()


def overview(query):
    completed_before = _sync_runtime_snapshot()["completed_at"]
    sync_started = _maybe_sync_remotes(query)
    with _query_admission() as admission:
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

    sync_status = dict(_sync_status(now=now, admission=admission))
    sync_status["refresh_pending"] = bool(
        sync_started
        or sync_status["syncing"]
        or sync_status.get("completed_at") != completed_before
    )
    result = {
        "rate_limits": _rate_limits(
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
    _maybe_sync_remotes(query)
    if QUERY_VIEW is None:
        return _pivot_from_view(query, window)
    with _query_admission() as admission:
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
    _maybe_sync_remotes(query)
    if QUERY_VIEW is None:
        return rollup.filter_options(time_range=window)
    with _query_admission() as admission:
        with rollup.use_admitted_generations(admission.admitted):
            return rollup.filter_options(time_range=window)


def sync_status_endpoint(_query):
    return _sync_status()


def collection_endpoint(_query):
    """Read existing collection and quota observations without usage aggregation."""
    with _query_admission() as admission:
        status = _sync_status(admission=admission)
        limits = _live_rate_limits_from_admission(admission, sync_status=status)
        _apply_quota_applicability(limits)
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
    _maybe_sync_remotes({"force": ["1"]}, request_receipt=receipt)
    if _first(query, "ack", "0") == "1":
        runtime = _sync_runtime_snapshot()
        return dict(receipt, instance_id=SERVER_INSTANCE_ID,
                    syncing=runtime["syncing"], queued_refresh=runtime["queued_refresh"],
                    refresh_requested=runtime["refresh_requested"],
                    refresh_completed=runtime["refresh_completed"])
    return dict(_sync_status(), **receipt)


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
        _maybe_sync_remotes(query)
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
            QUOTA_REFRESH.shutdown()
            if QUERY_VIEW is not None:
                QUERY_VIEW.close()
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
                ids, unavailable = manager.discover(_codex_action_catalog())
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
                ids, unavailable = manager.discover(_codex_action_catalog())
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
                ids, unavailable = manager.discover(_codex_action_catalog())
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
        account_label = payload.get("account_label", _UNSPECIFIED_ACCOUNT_LABEL) if isinstance(payload, dict) else _UNSPECIFIED_ACCOUNT_LABEL
        if account_label is not _UNSPECIFIED_ACCOUNT_LABEL and account_label is not None and (
            not isinstance(account_label, str) or not account_label
        ):
            self._send_json({"error": "account_label must be null or a non-empty string"}, status=400)
            return
        if provider not in ("claude", "codex") or not isinstance(
            account_id, str
        ) or not account_id or not isinstance(observed_at, str) or not observed_at or (
            _observed_at(observed_at) is None
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
                live_rate_limits = _live_rate_limits_from_admission(admission)
            status, receipt = _remove_account_memory(
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


def _maybe_sync_remotes(query, *, queued=False, request_receipt=None):
    if _first(query, "sync", "1") == "0":
        return False
    force = _first(query, "force", "0") == "1"
    if not force and not _automatic_sync_due():
        return False
    if force and hub.enabled() and not queued:
        # Explicit quota reads start even if an older statistics round is busy.
        QUOTA_REFRESH.request(sync.load_machine_config().machines)
    # The Hub's prepared-view path schedules from configuration only. Keep the
    # standalone admission path for its existing direct-query lifecycle.
    if QUERY_VIEW is not None or request_receipt is not None:
        round_machines = tuple(machine.name for machine in sync.load_machine_config().machines)
    else:
        with generation.generation_admission_snapshot() as admission:
            round_machines = tuple(record.machine.name for record in admission.records)
    with _SYNC_LOCK:
        _reconcile_machine_config_locked(set(round_machines))
        if force and not queued:
            _SYNC_STATE["refresh_requested"] += 1
            if request_receipt is not None:
                request_receipt["refresh_request"] = _SYNC_STATE["refresh_requested"]
        if _SYNC_STATE["running"]:
            if force:
                _SYNC_STATE["queued_refresh"] = True
            return False
        _SYNC_STATE.update(
            running=True,
            started_at=_utc_timestamp(),
            completed_at=None,
            round_machines=round_machines,
            last_completed_round_machines=(),
            round_refresh=_SYNC_STATE["refresh_requested"],
            round_quota_refresh=force or not hub.enabled(),
            phase="statistics",
            pending_machines=round_machines,
            statistics_pending=round_machines,
        )
    try:
        thread = threading.Thread(
            target=_run_sync_round,
            args=(round_machines,),
            name="agent-monitor-sync",
            daemon=True,
        )
        thread.start()
    except Exception as exc:
        reason = "Failed to start sync worker: %s: %s" % (type(exc).__name__, exc)
        logger.exception("%s", reason)
        completed_at = _utc_timestamp()
        with _SYNC_LOCK:
            observations = dict(_SYNC_STATE["observations"])
            for name in round_machines:
                observations[name] = _attempt_failure_observation(
                    observations.get(name), completed_at, reason
                )
            _SYNC_STATE.update(
                running=False,
                completed_at=completed_at,
                last_completed_round_machines=round_machines,
                observations=observations,
                phase=None,
                pending_machines=(),
            )
        return False
    return True


def _automatic_sync_due(now=None):
    current_time = _aware_now(now)
    runtime = _sync_runtime_snapshot()
    if runtime["syncing"]:
        return False
    # Hub cadence is start-to-start. Waiting an interval after completion and
    # checking only once per interval used to skip the next scheduled tick.
    if hub.enabled():
        started_at = _parse_timestamp(runtime["started_at"])
        if started_at is not None:
            return (current_time.astimezone(timezone.utc) - started_at).total_seconds() >= hub.interval()
    completed_at = _parse_timestamp(runtime["completed_at"])
    if completed_at is not None:
        attempt_age = max(
            (current_time.astimezone(timezone.utc) - completed_at).total_seconds(),
            0.0,
        )
        if attempt_age < (hub.interval() if hub.enabled() else _SYNC_DUE_AFTER_SECONDS):
            return False
    return _sync_due(now=current_time)


def _sync_due(now=None):
    current_time = _aware_now(now)
    with _query_admission() as admission:
        for record in admission.records:
            if not record.admitted:
                return True
            if _generation_age_seconds(record.current.meta, current_time) >= (hub.interval() if hub.enabled() else _SYNC_DUE_AFTER_SECONDS):
                return True
    return False


def _sync_machine_completed(name, result, *, statistics_only=False):
    if QUERY_VIEW is not None:
        # Keep terminal/progress notifications behind query-view publication.
        try:
            QUERY_VIEW.refresh()
        except Exception:
            logger.exception("statistics completed but query view is unavailable")
    outcome = _normalize_sync_outcomes({name: result}, (name,))[name]
    attempted_at = _utc_timestamp()
    with _SYNC_LOCK:
        previous = _SYNC_STATE["observations"].get(name)
        if outcome["kind"] == "success":
            observation = {
                "contact_status": "reachable", "last_attempt_outcome": "success",
                "last_attempt_ts": attempted_at, "last_successful_contact_ts": attempted_at,
                "reason": None,
            }
        elif outcome["kind"] == "failure":
            observation = _failed_observation(previous, attempted_at, outcome["reason"])
        else:
            observation = _attempt_failure_observation(
                previous, attempted_at, outcome["reason"], outcome="malformed_result"
            )
        _SYNC_STATE["observations"][name] = observation
        _SYNC_STATE["statistics_pending"] = tuple(
            item for item in _SYNC_STATE.get("statistics_pending", ()) if item != name
        )
        if not statistics_only:
            _SYNC_STATE["pending_machines"] = tuple(
                item for item in _SYNC_STATE["pending_machines"] if item != name
            )
        if not _SYNC_STATE["statistics_pending"] and _SYNC_STATE["round_quota_refresh"]:
            _SYNC_STATE["phase"] = "quota"


@diagnostics.measured("statistics.sync_round")
def _run_sync_round(round_machines=()):
    results = {}
    outcomes = {}
    completed_round_machines = tuple(round_machines)
    completed_at = None
    observations = None
    quota_ticket = QUOTA_REFRESH.status()["requested"] if hub.enabled() else 0
    try:
        with _SYNC_LOCK:
            quota = _SYNC_STATE["round_quota_refresh"]
        # Hub quota has its own worker and does not publish a statistics DB.
        collect = sync_process.sync_all if hub.enabled() else sync.sync_all
        results = collect(
            quota_refresh=quota and not hub.enabled(), progressive=False,
            on_statistics=lambda name, result: _sync_machine_completed(name, result, statistics_only=True),
            on_complete=_sync_machine_completed,
        )
        outcomes = _normalize_sync_outcomes(results, round_machines)
        if any(outcome["kind"] != "success" for outcome in outcomes.values()):
            diagnostics.monitor.outcome("source_failure")
        completed_round_machines = tuple(sorted(outcomes))
        completed_at = _utc_timestamp()
        with _SYNC_LOCK:
            observations = dict(_SYNC_STATE["observations"])
        for name, outcome in outcomes.items():
            if outcome["kind"] == "failure":
                observations[name] = _failed_observation(
                    observations.get(name), completed_at, outcome["reason"]
                )
            elif outcome["kind"] == "success":
                observations[name] = {
                    "contact_status": "reachable",
                    "last_attempt_outcome": "success",
                    "last_attempt_ts": completed_at,
                    "last_successful_contact_ts": completed_at,
                    "reason": None,
                }
            else:
                observations[name] = _attempt_failure_observation(
                    observations.get(name),
                    completed_at,
                    outcome["reason"],
                    outcome="malformed_result",
                )
    except Exception as exc:
        logger.exception("cross-machine sync failed: %s", exc)
        diagnostics.monitor.outcome("error")
        completed_at = _utc_timestamp()
        reason = "%s: %s" % (type(exc).__name__, exc)
        with _SYNC_LOCK:
            observations = dict(_SYNC_STATE["observations"])
        for name in round_machines:
            observations[name] = _attempt_failure_observation(
                observations.get(name), completed_at, reason
            )
    finally:
        if quota_ticket:
            with _SYNC_LOCK:
                _SYNC_STATE["phase"] = "quota"
            QUOTA_REFRESH.wait(quota_ticket)
        _remember_accounts_after_sync_publish()
        completed_at = completed_at or _utc_timestamp()
        if observations is None:
            with _SYNC_LOCK:
                observations = dict(_SYNC_STATE["observations"])
        for name, outcome in outcomes.items():
            if outcome["kind"] != "success":
                continue
            current = outcome["generation"]
            if current is not None and hasattr(current, "close"):
                try:
                    current.close()
                except Exception as exc:
                    logger.exception("generation cleanup failed for %s: %s", name, exc)
                    previous = observations.get(name) or {}
                    observations[name] = {
                        "contact_status": (
                            "reachable"
                            if outcome["kind"] == "success"
                            else previous.get("contact_status", "unknown")
                        ),
                        "last_attempt_outcome": "cleanup_failed",
                        "last_attempt_ts": completed_at,
                        "last_successful_contact_ts": (
                            completed_at
                            if outcome["kind"] == "success"
                            else previous.get("last_successful_contact_ts")
                        ),
                        "reason": "数据版本清理失败：%s：%s"
                        % (type(exc).__name__, exc),
                    }
        with _SYNC_LOCK:
            followup = _SYNC_STATE["queued_refresh"]
            _SYNC_STATE.update(
                running=False,
                completed_at=completed_at,
                last_completed_round_machines=completed_round_machines,
                observations=observations,
                refresh_completed=_SYNC_STATE["round_refresh"],
                queued_refresh=False,
                phase=None,
                pending_machines=(),
                statistics_pending=(),
            )
        if followup:
            _maybe_sync_remotes({"force": ["1"]}, queued=True)


def _normalize_sync_outcomes(results, round_machines):
    if not isinstance(results, dict):
        raise TypeError("sync_all must return a machine-to-SyncResult mapping")
    outcomes = {}
    for name in sorted(set(round_machines) | set(results)):
        result = results.get(name)
        if isinstance(result, sync_process.Outcome):
            outcomes[name] = {
                "kind": "success" if result.success else "failure",
                "generation": None,
                "reason": result.error,
            }
            continue
        if not isinstance(result, sync.SyncResult):
            outcomes[name] = {
                "kind": "malformed",
                "generation": None,
                "reason": "同步结果格式错误：期望 SyncResult 且只有一个明确结论。",
            }
            continue
        has_generation = result.generation is not None
        has_error = result.error is not None
        if has_generation and not has_error:
            outcomes[name] = {
                "kind": "success",
                "generation": result.generation,
                "reason": None,
            }
        elif has_error and not has_generation:
            outcomes[name] = {
                "kind": "failure",
                "generation": None,
                "reason": str(result.error),
            }
        else:
            outcomes[name] = {
                "kind": "malformed",
                "generation": None,
                "reason": "同步结果没有明确结论，或结论互相冲突。",
            }
    return outcomes


def _failed_observation(previous, attempted_at, reason):
    previous = previous or {}
    return {
        "contact_status": "unreachable",
        "last_attempt_outcome": "failure",
        "last_attempt_ts": attempted_at,
        "last_successful_contact_ts": previous.get("last_successful_contact_ts"),
        "reason": reason,
    }


def _attempt_failure_observation(previous, attempted_at, reason, *, outcome="failure"):
    previous = previous or {}
    return {
        "contact_status": previous.get("contact_status", "unknown"),
        "last_attempt_outcome": outcome,
        "last_attempt_ts": attempted_at,
        "last_successful_contact_ts": previous.get("last_successful_contact_ts"),
        "reason": reason,
    }


def _sync_runtime_snapshot(current_machines=None):
    with _SYNC_LOCK:
        if current_machines is not None:
            _reconcile_machine_config_locked(set(current_machines))
        snapshot = dict(_SYNC_STATE)
        snapshot["round_machines"] = tuple(_SYNC_STATE["round_machines"])
        snapshot["last_completed_round_machines"] = tuple(
            _SYNC_STATE["last_completed_round_machines"]
        )
        snapshot["known_machines"] = (
            None
            if _SYNC_STATE["known_machines"] is None
            else frozenset(_SYNC_STATE["known_machines"])
        )
        snapshot["observations"] = {
            name: dict(observation)
            for name, observation in _SYNC_STATE["observations"].items()
        }
    snapshot["errors"] = {
        name: observation["reason"]
        for name, observation in snapshot["observations"].items()
        if observation.get("reason")
    }
    snapshot["syncing"] = snapshot.pop("running")
    snapshot["terminal"] = not snapshot["syncing"]
    return snapshot


def _reset_sync_state_for_tests():
    with _SYNC_LOCK:
        _SYNC_STATE.update(
            running=False,
            started_at=None,
            completed_at=None,
            round_machines=(),
            last_completed_round_machines=(),
            known_machines=None,
            observations={},
            refresh_requested=0,
            refresh_completed=0,
            round_refresh=0,
            queued_refresh=False,
            round_quota_refresh=True,
            phase=None,
            pending_machines=(),
            statistics_pending=(),
        )


def _reconcile_machine_config_locked(current_machines):
    known = _SYNC_STATE["known_machines"]
    if known is None:
        _SYNC_STATE["known_machines"] = set(current_machines)
        return
    observations = dict(_SYNC_STATE["observations"])
    for name in current_machines - known:
        observations[name] = {
            "contact_status": "unknown",
            "last_attempt_outcome": "not_attempted_since_added",
            "last_attempt_ts": None,
            "last_successful_contact_ts": None,
            "reason": "这台机器是本服务启动后加入的，还没进过同步。",
        }
    _SYNC_STATE["known_machines"] = set(current_machines)
    _SYNC_STATE["observations"] = observations


def _capture_startup_machine_config():
    """Anchor which machines existed when this process started.

    Tests and direct function callers can still establish the anchor lazily via
    _sync_status(); the real server captures it before accepting requests so a
    machine added before the first status request is not mislabeled as a
    restart-unknown machine.
    """
    with generation.generation_admission_snapshot() as admission:
        current_machines = {record.machine.name for record in admission.records}
    with _SYNC_LOCK:
        if _SYNC_STATE["known_machines"] is None:
            _SYNC_STATE["known_machines"] = current_machines


def _sync_status(now=None, admission=None):
    current_time = _aware_now(now)
    if admission is None:
        with _query_admission() as loaded:
            runtime = _sync_runtime_snapshot(
                record.machine.name for record in loaded.records
            )
            return _sync_status_from_admission(loaded, current_time, runtime)
    runtime = _sync_runtime_snapshot(
        record.machine.name for record in admission.records
    )
    return _sync_status_from_admission(admission, current_time, runtime)


def _sync_status_from_admission(admission, current_time, runtime):
    machine_rows = []
    admitted_names = {current.host for current in admission.admitted}
    for record in admission.records:
        name = record.machine.name
        meta = record.current.meta if record.admitted else None
        observation = _runtime_observation(runtime, name, meta)
        if record.never:
            availability = "never"
        elif not record.admitted:
            availability = None
        else:
            availability = observation["contact_status"]
        stale = bool(
            meta
            and _generation_age_seconds(meta, current_time)
            >= _STALE_AFTER_SECONDS
        )
        machine_rows.append(
            {
                "name": name,
                "declared": True,
                "this_machine": record.machine.is_self,
                "admitted": record.admitted,
                "availability": availability,
                "stale": stale,
                "syncing": runtime["syncing"] and name in runtime.get("pending_machines", ()),
                "statistics_syncing": runtime["syncing"] and name in runtime.get("statistics_pending", ()),
                "reason": observation["reason"],
                "exclusion_reason": record.exclusion_reason,
                "last_sync_ts": meta.get("published_at") if meta else None,
                "generated_at": meta.get("generated_at") if meta else None,
                "data_start_date": meta.get("data_start_date") if meta else None,
                "generation_id": meta.get("generation_id") if meta else None,
                "totals_schema_version": meta.get("schema_version") if meta else None,
                "generation_totals_basis": (
                    meta.get("metric_totals_basis")
                    if meta and meta.get("schema_version") == 2
                    else ("legacy_project_derived" if meta else None)
                ),
                "generation_legacy_fallback_bucket_count": (
                    meta.get("legacy_fallback_bucket_count")
                    if meta and meta.get("schema_version") == 2
                    else None
                ),
                "generation_legacy_fallback_bucket_dimensions": (
                    meta.get("legacy_fallback_bucket_dimensions")
                    if meta and meta.get("schema_version") == 2
                    else None
                ),
                "last_attempt_outcome": observation["last_attempt_outcome"],
                "last_attempt_ts": observation["last_attempt_ts"],
                "last_successful_contact_ts": observation["last_successful_contact_ts"],
            }
        )
    declared = len(admission.config.machines)
    if hub.enabled():
        import statistics_snapshot
        details = {current.host: statistics_snapshot.read_metadata(current.db_path) for current in admission.admitted}
        for row in machine_rows:
            detail = details.get(row["name"], {"state": "not_collected"})
            row["statistics"] = {key: detail.get(key) for key in (
                "state", "observed_at", "history_authoritative_from", "scan_complete",
                "source_error_count", "blocked_source_count", "usage_quality_counts",
            )}
    return {
        "instance_id": SERVER_INSTANCE_ID,
        "quota_refresh": QUOTA_REFRESH.status() if hub.enabled() else None,
        "refresh_requested": runtime.get("refresh_requested", 0),
        "refresh_completed": runtime.get("refresh_completed", 0),
        "queued_refresh": runtime.get("queued_refresh", False),
        "phase": runtime.get("phase"),
        "coverage": {"admitted": len(admitted_names), "declared": declared},
        "all_machines": sorted(admitted_names),
        "machines": machine_rows,
        "syncing": runtime["syncing"],
        "terminal": runtime["terminal"],
        "started_at": runtime["started_at"],
        "completed_at": runtime["completed_at"],
    }


def _runtime_observation(runtime, name, meta):
    observation = runtime.get("observations", {}).get(name)
    if observation is not None:
        merged = dict(observation)
        if not merged.get("last_successful_contact_ts") and meta:
            merged["last_successful_contact_ts"] = meta.get("published_at")
        return merged
    error = runtime.get("errors", {}).get(name) or runtime.get("errors", {}).get("*")
    if error:
        return {
            "contact_status": "unreachable",
            "last_attempt_outcome": "failure",
            "last_attempt_ts": runtime.get("completed_at"),
            "last_successful_contact_ts": meta.get("published_at") if meta else None,
            "reason": error,
        }
    return {
        "contact_status": "unknown",
        "last_attempt_outcome": "unknown_since_restart",
        "last_attempt_ts": None,
        "last_successful_contact_ts": meta.get("published_at") if meta else None,
        "reason": "服务重启后尚未联系过；本进程还没有完成过同步。",
    }


def _generation_age_seconds(meta, now):
    published = _parse_timestamp(meta.get("published_at"))
    if published is None:
        return float("inf")
    return max((now.astimezone(timezone.utc) - published).total_seconds(), 0.0)


def _aware_now(value=None):
    current = value() if callable(value) else value
    current = current or datetime.now(timezone.utc)
    if current.tzinfo is None or current.utcoffset() is None:
        raise ValueError("sync status time must be timezone-aware")
    return current


def _utc_timestamp():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_timestamp(value):
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


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
                  created_at=identity.get("created_at"),
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


def _codex_action_catalog():
    """Read the known identity directory, without refreshing or rewriting it."""
    with generation.generation_admission_snapshot() as admission:
        entries = []
        for current in admission.admitted:
            limits = current.meta.get("rate_limits")
            block = limits.get("codex") if isinstance(limits, dict) else None
            if isinstance(block, dict):
                # Identity remains usable when no quota timestamp was observed.
                entries.append(_account_entry(block, [current.host]))
    with _ACCOUNT_MEMORY_LOCK:
        state, payload = _load_account_memory()
    if state == "unreadable_or_unsupported":
        raise codex_accounts.ActionError("历史登录账号记录无法读取，暂不能确认完整账号列表。请检查服务主机的 account_memory.json。", 503)
    if state == "valid":
        entries.extend(_remembered_rate_limit_entry(record) for record in payload["accounts"].values()
                       if record["provider"] == "codex")
    return entries


def _rate_limits(admission=None, sync_status=None, expected_delete_epoch=None):
    if admission is None:
        with generation.generation_admission_snapshot() as loaded:
            return _rate_limits_from_admission(
                loaded,
                sync_status=sync_status,
                expected_delete_epoch=expected_delete_epoch,
            )
    return _rate_limits_from_admission(
        admission,
        sync_status=sync_status,
        expected_delete_epoch=expected_delete_epoch,
    )


def _rate_limits_from_admission(
    admission, sync_status=None, *, expected_delete_epoch=None
):
    if expected_delete_epoch is None:
        with _account_memory_upsert_epoch() as registered_epoch:
            result = _live_rate_limits_from_admission(
                admission, sync_status=sync_status
            )
            _remember_rate_limit_accounts(
                result, expected_delete_epoch=registered_epoch
            )
    else:
        result = _live_rate_limits_from_admission(
            admission, sync_status=sync_status
        )
        _remember_rate_limit_accounts(
            result, expected_delete_epoch=expected_delete_epoch
        )
    _merge_remembered_rate_limit_accounts(result)
    _apply_quota_applicability(result)
    return result


def _live_rate_limits_from_admission(admission, sync_status=None):
    """Quota grouped by account, because quota is metered per account.

    Machines are not the unit: three machines signed into one Claude account
    report one counter three times, and picking the freshest of them is right.
    Two machines signed into different Codex accounts report two independent
    pools, and picking the freshest is a coin toss between them — the bug this
    replaces. Grouping by account gets both cases right.

    A machine whose exporter predates account stamping groups under its own key
    rather than merging with anything: an unknown account is not evidence of a
    shared one, and merging unknowns would recreate the same bug one level down.
    """
    grouped = {"claude": {}, "codex": {}}
    refresh_errors = {"claude": [], "codex": []}
    signed_out = {"claude": [], "codex": []}
    for current in admission.admitted:
        rate_limits = current.meta.get("rate_limits")
        if hub.enabled():
            config = getattr(admission, "config", None)
            machine = getattr(config, "by_name", {}).get(current.host)
            rate_limits = QUOTA_REFRESH.merge(current, machine)
        if not isinstance(rate_limits, dict):
            rate_limits = {}
        for provider, buckets in grouped.items():
            block = rate_limits.get(provider)
            # Not every machine signs into every provider, and one that does not
            # has nothing to fix. It is recorded as a state so the page can say
            # why its name is absent from the figures, and kept out of
            # `refresh_errors` so nothing downstream counts it as a failure.
            #
            # It suppresses the error, not the row. A machine whose sign-in
            # lapsed usually still holds a reading from before it did, and that
            # reading with its own timestamp is the only remaining cue that the
            # machine has stopped reporting. Dropping the row here would take
            # that away too, and leave no observable difference between a
            # machine that never used the provider and one that quietly fell off.
            refresh_error = None
            # An error present in the block wins over the signed-out state, and
            # is read first for that reason. The two are not supposed to appear
            # together — but a block can carry both (a cached block from a lapsed
            # sign-in, restamped with this round's error), and if the signed-out
            # branch ran first that error would be dropped on every sync round
            # for as long as the cache held. A failure that stops being reported
            # is the one outcome this whole change must not produce, so the
            # consumer resolves the contradiction toward reporting it.
            if (
                isinstance(block, dict)
                and block.get("signed_out") is True
                and block.get("refresh_error") is None
            ):
                signed_out[provider].append(current.host)
            # A missing `refresh_error` is not by itself evidence of an old
            # exporter. A current one omits the key on every passive export —
            # absent means "this export ran no active query", which the 120-second
            # statistics round legitimately is. It normally carries the key over
            # from its own quota cache, but only while the cached block names the
            # account the machine is signed into now; switch accounts and the
            # carry-over stops, correctly, because republishing the old account's
            # "query succeeded" under a new one would be a false claim. Reading
            # absence as age then sent the reader to update an exporter that was
            # already current, once per account switch, until that machine's next
            # hourly refresh.
            #
            # `signed_out` is the discriminator: it was added after
            # `refresh_error`, and every exporter that publishes it publishes the
            # error key too whenever it runs an active query. Neither key means
            # the exporter predates both and genuinely cannot confirm a refresh.
            elif not isinstance(block, dict) or (
                "refresh_error" not in block and "signed_out" not in block
            ):
                refresh_error = "无法确认这台机器的配额刷新已生效，请更新它的 agent-monitor exporter 后重试。"
            elif block.get("refresh_error") is not None:
                value = block["refresh_error"]
                refresh_error = value if isinstance(value, str) and value.strip() else "配额刷新状态无效，请更新这台机器的 agent-monitor exporter 后重试。"
            if refresh_error is not None:
                failure_entry = {"machine": current.host, "reason": refresh_error}
                if isinstance(block, dict) and (diagnostics.safe_failure(block.get("refresh_failure")) or block.get("refresh_pause")):
                    failure_entry.update(account_id=_account_id(block), account_label=block.get("account_label"),
                        failure=diagnostics.safe_failure(block.get("refresh_failure")),
                        pause=block.get("refresh_pause"), query_state=block.get("query_state"))
                refresh_errors[provider].append(failure_entry)
            if not isinstance(block, dict):
                continue
            evidence = quota_applicability.observation(block.get("quota_observation"))
            updated_at = _observed_at(evidence["observed_at"] if evidence and evidence["observed_at"] else block.get("updated_at"))
            if updated_at is None:
                if not (_account_id(block) and (refresh_error or provider == "claude")):
                    continue
                # A failed quota request does not sign the current account out.
                # Keep its presence so account memory can supply an older value.
                updated_at = datetime.min.replace(tzinfo=timezone.utc)
            account_id = _account_id(block)
            key = _account_key(provider, block) if account_id else ("unattributed", current.host)
            bucket = buckets.get(key)
            if bucket is None:
                buckets[key] = {
                    "latest": (updated_at, block),
                    # Which machine the shown figure came from. One account is
                    # commonly signed in on several machines and the row shows
                    # the freshest of them, so "this account's reading" and "this
                    # machine's reading" are different claims — and a consumer
                    # that only has the machine *list* cannot tell which of them
                    # the number in front of the reader belongs to.
                    "latest_host": current.host,
                    "machines": [current.host],
                    "confirmations": [],
                    "failures": [],
                }
            else:
                bucket["machines"].append(current.host)
                old_observation = quota_applicability.observation(bucket["latest"][1].get("quota_observation"))
                new_observation = quota_applicability.observation(block.get("quota_observation"))
                old_trusted = bool(provider == "claude" and old_observation and old_observation["observed_at"])
                new_trusted = bool(provider == "claude" and new_observation and new_observation["observed_at"])
                if (new_trusted, updated_at) > (old_trusted, bucket["latest"][0]):
                    bucket["latest"] = (updated_at, block)
                    bucket["latest_host"] = current.host
            bucket = buckets[key]
            note = quota_applicability.confirmation(block.get("subscription_confirmation"))
            if note:
                bucket["confirmations"].append(note)
            if block.get("refresh_failure"):
                bucket["failures"].append(block["refresh_failure"])

    self_machine = _self_machine_name(admission)
    result = {}
    for provider, buckets in grouped.items():
        # Freshest account first; it is the one the reader is most likely using.
        ordered = sorted(buckets.values(), key=lambda b: b["latest"][0], reverse=True)
        entries = [
            _account_entry(
                bucket["latest"][1],
                bucket["machines"],
                self_machine,
                reading_from=bucket["latest_host"],
            )
            for bucket in ordered
        ]
        for entry, bucket in zip(entries, ordered):
            if bucket["confirmations"]:
                entry["subscription_confirmation"] = max(bucket["confirmations"], key=quota_applicability.confirmation_time)
            if any(failure.get("http_status") == 403 for failure in bucket["failures"] if isinstance(failure, dict)):
                entry["quota_access_refused"] = True
        result[provider] = {
            "accounts": entries,
            "refresh_errors": refresh_errors[provider],
            # Machines that are not signed in to this provider. Published beside
            # the errors rather than inside them: the page tells the reader why a
            # machine is absent from the figures without implying there is
            # anything to fix.
            "signed_out_machines": sorted(signed_out[provider]),
            "unavailable_reason": None
            if entries
            else _quota_unavailable_reason(
                provider, sync_status, signed_out[provider], refresh_errors[provider]
            ),
        }
    return result


def _remember_accounts_after_sync_publish():
    """Remember the current admission snapshot while this sync round is still running."""
    try:
        with generation.generation_admission_snapshot() as admission:
            with _account_memory_upsert_epoch() as expected_delete_epoch:
                rate_limits = _live_rate_limits_from_admission(admission)
                _remember_rate_limit_accounts(
                    rate_limits, expected_delete_epoch=expected_delete_epoch
                )
    except Exception as exc:
        logger.warning(
            "account memory upsert after sync publish failed: %s: %s",
            type(exc).__name__,
            exc,
            exc_info=True,
        )


def _remember_rate_limit_accounts(rate_limits, *, expected_delete_epoch):
    try:
        return _upsert_account_memory(
            rate_limits, expected_delete_epoch=expected_delete_epoch
        )
    except Exception as exc:
        logger.warning(
            "account memory upsert failed: %s: %s",
            type(exc).__name__,
            exc,
            exc_info=True,
        )
        return False


def _merge_remembered_rate_limit_accounts(rate_limits):
    for provider in ("claude", "codex"):
        provider_limits = rate_limits.get(provider)
        if not isinstance(provider_limits, dict):
            continue
        for entry in provider_limits.get("accounts", ()):
            entry["presence"] = "in_use"

    with _ACCOUNT_MEMORY_LOCK:
        state, payload = _load_account_memory()
    if state != "valid":
        return

    remembered = sorted(
        payload["accounts"].values(),
        key=lambda record: _observed_at(record["observed_at"]),
        reverse=True,
    )
    for provider in ("claude", "codex"):
        provider_limits = rate_limits.get(provider)
        if not isinstance(provider_limits, dict):
            continue
        entries = provider_limits.get("accounts")
        if not isinstance(entries, list):
            continue
        # Machines whose live reading is not known to be current. A lapsed
        # sign-in belongs here alongside an outright failure: the question this
        # set answers is "may account memory supply a newer value for this
        # entry", and the answer turns on whether the machine still refreshes,
        # not on whether its inability to do so is classed as a fault.
        failed_machines = {
            failure["machine"] for failure in provider_limits.get("refresh_errors", ())
        } | set(provider_limits.get("signed_out_machines", ()))
        for entry in entries:
            record = payload["accounts"].get(_account_key(provider, entry))
            if record is None or not failed_machines.intersection(entry.get("machines", ())):
                continue
            live_time = _observed_at(entry.get("updated_at"))
            live_evidence = quota_applicability.observation(entry.get("quota_observation"))
            stored_evidence = quota_applicability.observation(record.get("quota_observation"))
            live_rank = (bool(provider == "claude" and live_evidence and live_evidence["observed_at"]), live_time or datetime.min.replace(tzinfo=timezone.utc))
            stored_rank = (bool(provider == "claude" and stored_evidence and stored_evidence["observed_at"]), _observed_at(record["observed_at"]))
            if live_rank >= stored_rank:
                continue
            for field in _ACCOUNT_MEMORY_NUMERIC_FIELDS:
                entry[field] = _quota_number(record[field])
            entry["updated_at"] = record["observed_at"]
            entry["quota_observation"] = stored_evidence
            if record.get("legacy_observation"):
                entry["legacy_observation"] = record["legacy_observation"]
            # Account memory does not retain a machine source. The replaced
            # live reading's host cannot vouch for these remembered numbers.
            entry["reading_from"] = record.get("reading_from")
            # Memory has no reading-plan provenance. Keep today's credential
            # plan separate rather than attach a stale cache's plan to its quota.
            entry["reading_plan"] = None
            entry["account_plan"] = entry.get("credential_plan")
        live_account_ids = {
            _account_key(provider, entry)
            for entry in entries
            if entry.get("account_state") == "known"
        }
        entries.extend(
            _remembered_rate_limit_entry(record)
            for record in remembered
            if record["provider"] == provider
            and _account_key(provider, record) not in live_account_ids
        )


def _apply_quota_applicability(rate_limits):
    """Separate current quota from retained facts after whole-reading selection."""
    block = rate_limits.get("claude")
    if not isinstance(block, dict):
        return
    try:
        applicability_store = quota_applicability.Store(_ACCOUNT_MEMORY_PATH.parent)
        notes = applicability_store.read()
        state_error = False
    except (OSError, ValueError):
        notes, state_error = {}, True
        logger.warning("quota applicability state unreadable; current Claude quotas withheld")
    now = time.time()
    for account in block.get("accounts", ()):
        evidence = quota_applicability.observation(account.get("quota_observation"))
        observed = evidence.get("observed_at") if evidence else None
        candidates = [quota_applicability.confirmation(account.get("subscription_confirmation")),
                      notes.get("claude:" + str(account.get("account_id")))]
        candidates = [item for item in candidates if item]
        note = max(candidates, key=quota_applicability.confirmation_time) if candidates else None
        if note and not state_error:
            # Only the explicit recovery path produces restored_at. A normal
            # request already in flight when the user confirms cannot retire it.
            try:
                note = applicability_store.adopt("claude", account["account_id"], note)
            except (OSError, ValueError):
                state_error = True
                logger.warning("quota applicability evidence could not be persisted")
            account["subscription_confirmation"] = note
        historical = {key: account.get(key) for key in (*_ACCOUNT_MEMORY_NUMERIC_FIELDS, "reading_from")}
        historical.update(observed_at=observed, recorded_at=account.get("updated_at"),
                          source=evidence["source"] if evidence else "legacy_unknown",
                          account_plan=account.get("account_plan"))
        account["last_observation"] = historical
        if state_error:
            status, reason = "unverified", "订阅确认记录无法读取，当前配额未核实；请检查服务主机状态文件。"
        elif quota_applicability.unavailable(note):
            status, reason = "no_subscription", "无有效 Claude Code 订阅（用户已确认）；历史配额不适用于当前账号。"
            account["subscription_confirmation"] = note
        elif account.get("quota_access_refused") and account.get("refresh_error"):
            status, reason = "access_denied", "配额访问被拒绝（HTTP 403），原因未确认；历史读数不能代表当前额度。"
        elif not observed:
            status, reason = "unverified", "配额观测时间未核实；旧记录接收时间不能证明额度已更新。"
        elif not quota_applicability.current_window(account, now):
            status, reason = "historical", "没有当前窗口的配额读数，历史记录仅供参考。"
        else:
            status, reason = "current", None
        if status == "no_subscription":
            expected = [failure for failure in block.get("refresh_errors", ())
                        if failure.get("account_id") == account.get("account_id")
                        and (failure.get("failure") or {}).get("http_status") == 403]
            account["historical_query_failures"] = expected
            block["refresh_errors"] = [failure for failure in block.get("refresh_errors", ()) if failure not in expected]
        account["quota_status"] = status
        account["quota_status_reason"] = reason
        # A credential tier never proves a current paid entitlement.
        account["plan_verification"] = "unverified"
        account["updated_at"] = observed if status == "current" else None
        for prefix in ("five_hour", "seven_day"):
            reset = account.get(prefix + "_resets_at")
            if status != "current" or type(reset) not in (int, float) or not math.isfinite(reset) or reset <= now:
                account[prefix + "_used_pct"] = None
                account[prefix + "_resets_at"] = None


def _remembered_rate_limit_entry(record):
    return {
        "account_id": record["account_id"],
        "account_label": record["account_label"],
        "account_plan": record["account_plan"],
        # Memory stores neither, and must not start: its record shape is
        # validated by exact set equality, so one extra field would make the
        # whole file unreadable and drop every remembered account (ADR
        # 20260822-586a). Null is also the honest value — a remembered row
        # states a past observation, not what any machine is signed into now.
        "reading_plan": None,
        "credential_plan": None,
        "account_state": "known",
        "presence": "remembered",
        "five_hour_used_pct": _quota_number(record["five_hour_used_pct"]),
        "five_hour_resets_at": _quota_number(record["five_hour_resets_at"]),
        "seven_day_used_pct": _quota_number(record["seven_day_used_pct"]),
        "seven_day_resets_at": _quota_number(record["seven_day_resets_at"]),
        "updated_at": record["observed_at"],
        "machines": [],
        # Null, and present: a remembered row has no machine behind it at all,
        # which is a different thing from a live row whose source this server is
        # too old to name. Omitting the key would conflate them at the consumer.
        "reading_from": record.get("reading_from"),
        "quota_observation": quota_applicability.observation(record.get("quota_observation")),
        "legacy_observation": record.get("legacy_observation"),
        "this_machine": None,
    }


@contextmanager
def _account_memory_upsert_epoch():
    with _ACCOUNT_MEMORY_LOCK:
        epoch = _ACCOUNT_MEMORY_DELETE_EPOCH
        _ACCOUNT_MEMORY_ACTIVE_UPSERT_EPOCHS[epoch] += 1
    try:
        yield epoch
    finally:
        with _ACCOUNT_MEMORY_LOCK:
            remaining = _ACCOUNT_MEMORY_ACTIVE_UPSERT_EPOCHS[epoch] - 1
            if remaining:
                _ACCOUNT_MEMORY_ACTIVE_UPSERT_EPOCHS[epoch] = remaining
            else:
                del _ACCOUNT_MEMORY_ACTIVE_UPSERT_EPOCHS[epoch]
            _prune_account_memory_deletions_locked()


def _prune_account_memory_deletions_locked():
    if not _ACCOUNT_MEMORY_ACTIVE_UPSERT_EPOCHS:
        _ACCOUNT_MEMORY_DELETED_AT_EPOCH.clear()
        return
    oldest_active_epoch = min(_ACCOUNT_MEMORY_ACTIVE_UPSERT_EPOCHS)
    for key, deleted_at_epoch in tuple(_ACCOUNT_MEMORY_DELETED_AT_EPOCH.items()):
        if deleted_at_epoch <= oldest_active_epoch:
            del _ACCOUNT_MEMORY_DELETED_AT_EPOCH[key]


def _upsert_account_memory(rate_limits, *, expected_delete_epoch):
    candidates = []
    for provider in ("claude", "codex"):
        provider_limits = rate_limits.get(provider)
        if not isinstance(provider_limits, dict):
            continue
        for entry in provider_limits.get("accounts", ()):
            candidate = _account_memory_entry(provider, entry)
            if candidate is not None:
                candidates.append(candidate)
    if not candidates:
        return False

    with _ACCOUNT_MEMORY_LOCK:
        state, payload = _load_account_memory()
        if state == "unreadable_or_unsupported":
            return False
        accounts = {
            key: _normalized_account_memory_record(record)
            for key, record in payload["accounts"].items()
        }
        changed = False
        for key, candidate in candidates:
            if (
                _ACCOUNT_MEMORY_DELETED_AT_EPOCH.get(key, 0)
                > expected_delete_epoch
            ):
                continue
            existing = accounts.get(key)
            if existing is not None:
                candidate_time = _observed_at(candidate["observed_at"])
                existing_time = _observed_at(existing["observed_at"])
                old = quota_applicability.observation(existing.get("quota_observation"))
                new = quota_applicability.observation(candidate.get("quota_observation"))
                old_trusted, new_trusted = bool(candidate["provider"] == "claude" and old and old["observed_at"]), bool(candidate["provider"] == "claude" and new and new["observed_at"])
                if (new_trusted, candidate_time) <= (old_trusted, existing_time):
                    continue
                if new_trusted and not old_trusted:
                    candidate["legacy_observation"] = {field: existing[field] for field in _ACCOUNT_MEMORY_ENTRY_FIELDS}
                elif existing.get("legacy_observation"):
                    candidate["legacy_observation"] = existing["legacy_observation"]
            accounts[key] = candidate
            changed = True
        if not changed:
            return False
        _write_account_memory(
            {
                "version": _ACCOUNT_MEMORY_VERSION,
                "accounts": accounts,
            }
        )
        return True


def _remove_account_memory(provider, account_id, observed_at, live_rate_limits, *,
                           account_label=_UNSPECIFIED_ACCOUNT_LABEL):
    global _ACCOUNT_MEMORY_DELETE_EPOCH
    with _ACCOUNT_MEMORY_LOCK:
        provider_limits = live_rate_limits.get(provider)
        entries = (
            provider_limits.get("accounts", ())
            if isinstance(provider_limits, dict)
            else ()
        )
        state, payload = _load_account_memory()
        if state == "unreadable_or_unsupported":
            return 500, {"error": "账号记录不可读或不受支持"}
        if account_label is _UNSPECIFIED_ACCOUNT_LABEL:
            candidates = {
                _account_key(provider, record)
                for record in list(payload["accounts"].values()) + list(entries)
                if record.get("account_id") == account_id
                and record.get("provider", provider) == provider
            }
            if len(candidates) > 1:
                return 409, {"error": "同一工作区有多个账号，请刷新页面后按邮箱移除。"}
            key = next(iter(candidates), quota_identity(provider, account_id))
        else:
            key = quota_identity(provider, account_id, account_label)
        in_use_entries = [
            entry
            for entry in entries
            if entry.get("account_state") == "known"
            and _account_key(provider, entry) == key
        ]
        in_use_machines = sorted(
            {
                machine
                for entry in in_use_entries
                for machine in entry.get("machines", ())
            }
        )
        if in_use_entries:
            return 409, {
                "error": "该账号仍在使用中：%s"
                % ", ".join(in_use_machines),
                "machines": in_use_machines,
            }
        unstamped_entries = [
            entry for entry in entries if entry.get("account_state") == "unstamped"
        ]
        unstamped_machines = sorted(
            {
                machine
                for entry in unstamped_entries
                for machine in entry.get("machines", ())
            }
        )
        if unstamped_entries:
            return 409, {
                "error": (
                    "无法确认账号归属，请更新这些机器上的 agent-monitor：%s"
                    % ", ".join(unstamped_machines)
                ),
                "machines": unstamped_machines,
            }

        accounts = dict(payload["accounts"])
        record = accounts.get(key)
        if record is None:
            return 404, {"error": "找不到该历史账号"}
        if record["observed_at"] != observed_at:
            return 409, {
                "error": (
                    "这条历史账号在你确认之后发生了变化；"
                    "请刷新页面、看过最新读数再移除"
                )
            }
        del accounts[key]
        _write_account_memory(
            {
                "version": _ACCOUNT_MEMORY_VERSION,
                "accounts": accounts,
            }
        )
        _ACCOUNT_MEMORY_DELETE_EPOCH += 1
        _ACCOUNT_MEMORY_DELETED_AT_EPOCH[key] = _ACCOUNT_MEMORY_DELETE_EPOCH
        _prune_account_memory_deletions_locked()
        return 200, {
            "account_label": record["account_label"],
            "observed_at": record["observed_at"],
        }


def _account_memory_entry(provider, entry):
    if not isinstance(entry, dict) or entry.get("account_state") != "known":
        return None
    account_id = entry.get("account_id")
    evidence = quota_applicability.observation(entry.get("quota_observation"))
    observed_at = _observed_at(evidence["observed_at"] if evidence and evidence["observed_at"] else entry.get("updated_at"))
    if (
        provider not in ("claude", "codex")
        or not isinstance(account_id, str)
        or not account_id
    ):
        return None
    if observed_at is None:
        return None
    record = {
        "provider": provider,
        "account_id": account_id,
        "account_label": entry.get("account_label"),
        "account_plan": entry.get("account_plan"),
        "five_hour_used_pct": _quota_number(entry.get("five_hour_used_pct")),
        "five_hour_resets_at": _quota_number(entry.get("five_hour_resets_at")),
        "seven_day_used_pct": _quota_number(entry.get("seven_day_used_pct")),
        "seven_day_resets_at": _quota_number(entry.get("seven_day_resets_at")),
        "observed_at": observed_at.astimezone(timezone.utc).isoformat(),
    }
    evidence = quota_applicability.observation(entry.get("quota_observation"))
    if evidence and evidence["observed_at"]:
        record["quota_observation"] = evidence
        record["reading_from"] = _text(entry.get("reading_from"))
    return _account_key(provider, record), record


def _normalized_account_memory_record(record):
    normalized = dict(record)
    for field in _ACCOUNT_MEMORY_NUMERIC_FIELDS:
        normalized[field] = _quota_number(normalized.get(field))
    return normalized


def _load_account_memory():
    try:
        raw = _ACCOUNT_MEMORY_PATH.read_bytes()
    except FileNotFoundError:
        return "missing", {"version": _ACCOUNT_MEMORY_VERSION, "accounts": {}}
    except OSError as exc:
        logger.warning("account memory is unreadable: %s: %s", type(exc).__name__, exc)
        return "unreadable_or_unsupported", None
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        logger.warning("account memory is unreadable: %s: %s", type(exc).__name__, exc)
        return "unreadable_or_unsupported", None
    if not _valid_account_memory(payload):
        logger.warning("account memory has an unreadable shape or unsupported version")
        return "unreadable_or_unsupported", None
    if payload["version"] == 1:
        payload = {"version": _ACCOUNT_MEMORY_VERSION, "accounts": {
            _account_key(record["provider"], record): record
            for record in payload["accounts"].values()
        }}
    return "valid", payload


def _valid_account_memory(payload):
    if not isinstance(payload, dict) or set(payload) != {"version", "accounts"}:
        return False
    if payload.get("version") not in (1, _ACCOUNT_MEMORY_VERSION):
        return False
    accounts = payload.get("accounts")
    if not isinstance(accounts, dict):
        return False
    for key, record in accounts.items():
        if not isinstance(key, str) or not isinstance(record, dict):
            return False
        if not _ACCOUNT_MEMORY_ENTRY_FIELDS <= set(record) or not set(record) <= _ACCOUNT_MEMORY_ENTRY_FIELDS | {"quota_observation", "reading_from", "legacy_observation"}:
            return False
        if "quota_observation" in record and quota_applicability.observation(record["quota_observation"]) is None:
            return False
        if "reading_from" in record and record["reading_from"] is not None and not isinstance(record["reading_from"], str):
            return False
        if "legacy_observation" in record and (not isinstance(record["legacy_observation"], dict)
                or set(record["legacy_observation"]) != _ACCOUNT_MEMORY_ENTRY_FIELDS):
            return False
        provider = record.get("provider")
        account_id = record.get("account_id")
        if provider not in ("claude", "codex"):
            return False
        if not isinstance(account_id, str) or not account_id:
            return False
        expected_key = ("%s:%s" % (provider, account_id) if payload["version"] == 1
                        else _account_key(provider, record))
        if key != expected_key:
            return False
        if record.get("account_label") is not None and not isinstance(
            record["account_label"], str
        ):
            return False
        if record.get("account_plan") is not None and not isinstance(
            record["account_plan"], str
        ):
            return False
        if _observed_at(record.get("observed_at")) is None:
            return False
    return True


def _write_account_memory(payload):
    parent = _ACCOUNT_MEMORY_PATH.parent
    parent_created = not parent.exists()
    parent.mkdir(parents=True, exist_ok=True)
    if parent_created:
        generation._fsync_directory(parent.parent)
    temporary = parent / (".%s-%s" % (_ACCOUNT_MEMORY_PATH.name, uuid.uuid4().hex))
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(
                payload,
                handle,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
                allow_nan=False,
            )
            handle.write("\n")
            handle.flush()
        generation._fsync_file(temporary)
        os.replace(temporary, _ACCOUNT_MEMORY_PATH)
        generation._fsync_directory(parent)
    finally:
        if temporary.exists():
            temporary.unlink()


def _observed_at(value):
    """Observation time, always tz-aware, so readings stay comparable.

    Codex rollouts carry tz-naive timestamps (`parsers/codex.py` normalizes them
    for its own `max()`, and `tests/test_codex_rate_limits.py` pins that they
    occur), while Claude's are offset-aware. Comparing one of each raises, and
    both comparisons here span machines — so one machine with a naive timestamp
    would take out the Overview for all of them. Naive is read as UTC, which is
    what the exporters mean by it.
    """
    parsed = _parse_timestamp(value)
    if parsed is None:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _account_id(block):
    """The account id, or None. Anything that is not a usable id reads as None.

    The generation schema validates `rate_limits` only as "an object" — nothing
    checks what is inside it — and this value is now a dict key and a rendered
    string. A machine publishing a malformed field must cost its own quota row,
    not the whole Overview payload it happens to travel in.
    """
    account_id = block.get("account_id")
    return account_id if isinstance(account_id, str) and account_id else None


def _account_key(provider, block):
    return quota_identity(provider, _account_id(block), block.get("account_label"))


def _quota_number(value):
    """A standard-JSON number, or None when one malformed field is unusable.

    `rate_limits` blocks intentionally have no inner wire schema. Normalizing at
    each server-side boundary keeps one machine or remembered record's bad
    field from turning the whole Overview response into JSON the browser cannot
    parse, without rejecting the other rows in the same memory file.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    return None


def _plan_to_show(block):
    """The plan a row displays, derived here rather than taken on trust.

    An exporter that predates the two raw fields sends neither key, and its
    `account_plan` is then the only plan available. Once a block carries the
    keys, that value is a claim by one writer among several — every machine
    runs its own exporter, at its own version, and nothing between them checks
    that it agrees with the pair beside it. Past that point the pair is the
    whole answer, including when the answer is "neither source reported one".
    """
    reading = _text(block.get("reading_plan"))
    credential = _text(block.get("credential_plan"))
    if reading or credential:
        return reading or credential
    if "reading_plan" in block or "credential_plan" in block:
        return None
    return _text(block.get("account_plan"))


def _account_entry(block, machines, self_machine=None, reading_from=None):
    account_id = _account_id(block)
    if account_id:
        account_state = "known"
    elif block.get("account_id") is None and "account_id" in block:
        account_state = "signed_out"
    else:
        account_state = "unstamped"
    return {
        "account_id": account_id,
        "account_label": _text(block.get("account_label")),
        # Derived here, not taken from the block. The exporter derives the same
        # value, but it is one of several writers — every other machine runs its
        # own, at its own version, and nothing between them validates that a
        # published `account_plan` agrees with the pair beside it. Trusting it
        # would let a block claim a plan neither source reported, and that value
        # does not stop at the screen: it is what the account memory persists,
        # and that file cannot be rebuilt. Recomputing costs nothing and makes
        # the contradiction unrepresentable downstream.
        #
        # The block's own value is the last resort, and only for exporters older
        # than the pair — where it is the one plan on offer. That is a question
        # about whether the keys are there, not about whether they hold
        # anything: a current exporter that read neither source publishes both
        # keys as null, and falling back on falsiness would hand that block the
        # very value this recomputation exists to distrust. `account_id` two
        # fields up already draws the line this way, for the same reason.
        "account_plan": _plan_to_show(block),
        # The two facts behind it, passed through unmerged so that having
        # fewer than two comparable sources stays tellable apart from two that
        # agree — the derived value alone cannot express the difference. An
        # exporter that predates ADR 20260822-586a sends neither key, so such a
        # row has zero sources, not one; either way nothing is compared, and
        # that is not the same as comparing and finding them equal.
        "reading_plan": _text(block.get("reading_plan")),
        "credential_plan": _text(block.get("credential_plan")),
        # Three states, not two. "Signed out" and "reported by an exporter too
        # old to stamp accounts" both leave no account, but only the second is
        # fixed by updating that machine — telling a current machine to update
        # itself sends the reader after a problem they do not have.
        "account_state": account_state,
        # The machine whose reading this row is showing, which is one of
        # `machines` and not necessarily all of them. Published because a reader
        # told "this machine stopped reporting" needs to know whether the figure
        # beside that sentence is the one that stopped, or another machine's
        # that is still advancing.
        "reading_from": reading_from,
        # `*_used_pct`, not `*_pct`: the page shows headroom, and a name that
        # does not say which direction it runs is one rename away from being
        # rendered upside down. The wire field it comes from keeps its old name
        # — that one is written by other machines and cannot be renamed without
        # a flag day.
        "five_hour_used_pct": _quota_number(block.get("five_hour_pct")),
        "five_hour_resets_at": _quota_number(block.get("five_hour_resets_at")),
        "seven_day_used_pct": _quota_number(block.get("seven_day_pct")),
        "seven_day_resets_at": _quota_number(block.get("seven_day_resets_at")),
        "updated_at": (quota_applicability.observation(block.get("quota_observation")) or {}).get("observed_at") or block.get("updated_at"),
        "quota_observation": quota_applicability.observation(block.get("quota_observation")),
        "refresh_error": _text(block.get("refresh_error")),
        "machines": sorted(machines),
        # Marked so a row can say which account the session in front of the
        # reader is spending. Three e-mail addresses do not tell them that; the
        # machine list does, but only once one of the names is singled out.
        "this_machine": self_machine if self_machine in machines else None,
    }


def _self_machine_name(admission):
    """Which declared machine is this one, via the config's own accessor.

    `load_machine_config` already refuses a config that does not name exactly
    one, so this does not re-derive that rule — it only tolerates an admission
    built without a config, which happens in tests.
    """
    config = getattr(admission, "config", None)
    if config is None:
        return None
    try:
        return config.self_machine.name
    except (AttributeError, StopIteration):
        return None


def _text(value):
    return value if isinstance(value, str) and value else None


def _quota_unavailable_reason(provider, sync_status, signed_out=(), refresh_errors=()):
    # Nobody is signed in anywhere, and nothing failed. That is a fleet that does
    # not use this provider, not a fleet waiting on a refresh — and the sentence
    # below about needing a successful refresh would send the reader to retry
    # something that is already working. Checked before the failure branches so a
    # single unrelated failure elsewhere cannot restore the misleading wording.
    if signed_out and not refresh_errors:
        return "没有机器登录 %s，没有配额可报。" % provider
    machine_errors = [
        "%s: %s" % (machine["name"], machine["reason"])
        for machine in (sync_status or {}).get("machines", ())
        if machine.get("reason")
        and machine.get("last_attempt_outcome")
        in {"failure", "cleanup_failed", "malformed_result"}
    ]
    if machine_errors:
        return (
            "最近一次同步在拿到 %s 配额数据前就失败了：%s。"
            % (provider, "；".join(machine_errors))
        )
    if (sync_status or {}).get("syncing"):
        return (
            "已纳入的数据里还没有 %s 配额，同步进行中。"
            % provider
        )
    return (
        "已纳入的数据里还没有 %s 配额，需要一次成功的刷新。"
        % provider
    )


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
    global _BIND_HOST, _BIND_PORT, QUERY_VIEW
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=39001)
    args = parser.parse_args()
    _BIND_HOST, _BIND_PORT = args.host, args.port

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    diagnostics.monitor.start(ROOT / "state" / "diagnostics")
    _capture_startup_machine_config()
    if hub.enabled():
        QUERY_VIEW = query_view.QueryView()
        QUERY_VIEW.start(interval=min(10, hub.interval()))
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    logger.info("agent-monitor listening on http://%s:%s", args.host, args.port)
    stop = threading.Event()
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

        def scheduled_sync():
            while not stop.is_set():
                try:
                    _maybe_sync_remotes({})
                except Exception:
                    logger.exception("scheduled statistics sync failed; next interval will retry")
                if stop.wait(min(10, hub.interval())):
                    break

        # Ends with the server; every SSH export has its existing per-machine deadline.
        threading.Thread(target=scheduled_sync, name="agent-monitor-schedule", daemon=True).start()
    try:
        server.serve_forever()
    finally:
        stop.set()
        diagnostics.monitor.close()
        try:
            codex_accounts.manager.close()
        finally:
            client_network.hub.close()
            QUOTA_REFRESH.shutdown()
            if QUERY_VIEW is not None:
                QUERY_VIEW.close()
            server.server_close()


if __name__ == "__main__":
    main()
