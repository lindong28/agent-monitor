"""Bounded, process-local observations. Never acquire a business lock here."""

from collections import deque
from contextlib import contextmanager
from datetime import datetime, timezone
import functools
import json
import math
import logging
from logging.handlers import RotatingFileHandler
import os
import re
from pathlib import Path
import threading
import time
import uuid


# Only protocol facts and bounded, redacted upstream reasons cross this boundary.
# Arbitrary exception text, argv and full response bodies remain excluded.
_STAGES = {"unknown", "account_api", "admission", "child_start", "child_result",
           "credentials", "profile_read", "quota_read", "initialize", "account_check",
           "quota_parse", "cleanup", "remote_prepare", "remote_export", "transfer",
           "network_start", "quota_worker", "pause_save"}
_KINDS = {"exception", "rpc_error", "http_error", "timeout", "process_exit",
          "invalid_result", "account_missing", "account_mismatch", "missing_windows"}
_ERROR_TYPES = {"Exception", "RuntimeError", "RefreshError", "NotSignedIn", "LocalUnavailable",
                "OSError", "PermissionError", "FileNotFoundError", "TimeoutError", "TimeoutExpired",
                "CalledProcessError", "HTTPError", "URLError", "JSONDecodeError", "ValueError",
                "KeyError", "TypeError", "BrokenPipeError", "ConnectionError", "ConnectionResetError"}
_SOURCE_FILES = {"server.py", "codex_accounts.py", "upstream_controls.py", "quota_refresh.py",
                 "quota_sync.py", "sync.py", "diagnostics.py"}


def safe_upstream_detail(value):
    """Redact credential syntax before retaining a bounded upstream reason."""
    if not isinstance(value, str):
        return None
    # Headers have scheme/value and multi-cookie syntax: discard their complete
    # value, rather than only the first space- or semicolon-delimited token.
    value = re.sub(r'''(?im)\b(?:proxy-authorization|authorization|set-cookie|cookie)["']?\s*[:=]\s*[^\r\n]*''', "[credential header redacted]", value)
    value = re.sub(r"https?://[^\s\"<>]+", "[url redacted]", value)
    value = re.sub(r"(?i)\bBearer\s+[^\s,;\"}]+", "Bearer [redacted]", value)
    value = re.sub(r'''(?ix)(["']?(?:password|access[_-]?token|refresh[_-]?token|id[_-]?token|api[_-]?key|client[_-]?secret|token|secret)["']?\s*[:=]\s*)(?:"[^"\n]*"|'[^'\n]*'|[^\s,;}]+)''', r"\1[redacted]", value)
    value = re.sub(r"[A-Za-z0-9_+/=-]{32,}", "[opaque value redacted]", value)
    return " ".join(value.split())[:512]


def safe_failure(value):
    if not isinstance(value, dict):
        return {}
    result = {}
    for key in ("detail", "upstream_code"):
        item = safe_upstream_detail(value.get(key))
        if item:
            result[key] = item
    for key, allowed in (("stage", _STAGES), ("kind", _KINDS), ("error_type", _ERROR_TYPES)):
        item = value.get(key)
        if isinstance(item, str) and item in allowed:
            result[key] = item
    for key in ("errno", "http_status", "rpc_code", "returncode"):
        item = value.get(key)
        if type(item) is int and abs(item) <= 2**31:
            result[key] = item
    item = value.get("timeout_seconds")
    if type(item) in (int, float) and math.isfinite(item) and 0 <= item <= 86400:
        result["timeout_seconds"] = round(item, 3)
    for key in ("account_fingerprint", "reader_sha256"):
        item = value.get(key)
        if isinstance(item, str) and len(item) == 64 and all(c in "0123456789abcdef" for c in item):
            result[key] = item
    frames = value.get("frames")
    if isinstance(frames, list):
        result["frames"] = [{"file": f["file"], "line": f["line"]} for f in frames[-5:]
                            if isinstance(f, dict) and isinstance(f.get("file"), str) and f["file"] in _SOURCE_FILES
                            and type(f.get("line")) is int and 0 < f["line"] < 100000]
    return result


def failure_details(exc, *, stage="unknown", kind="exception", **facts):
    value = {"stage": stage, "kind": kind, "error_type": type(exc).__name__}
    if value["error_type"] not in _ERROR_TYPES:
        value["error_type"] = "Exception"
    for attribute, key in (("errno", "errno"), ("code", "http_status"),
                           ("returncode", "returncode"), ("timeout", "timeout_seconds")):
        value[key] = getattr(exc, attribute, None)
    value.update(facts)
    frames = []
    trace = exc.__traceback__
    while trace is not None:
        path = Path(trace.tb_frame.f_code.co_filename)
        if path.parent == Path(__file__).parent and path.name in _SOURCE_FILES:
            frames.append({"file": path.name, "line": trace.tb_lineno})
        trace = trace.tb_next
    if frames:
        value["frames"] = frames[-5:]
    value.update(safe_failure(getattr(exc, "failure", None)))
    return safe_failure(value)


def failure_summary(value):
    facts = safe_failure(value)
    return "，".join(f"{key}={item}" for key, item in facts.items()
                    if key not in {"account_fingerprint", "reader_sha256", "frames"}) or "原始诊断未记录"


def timestamp(value):
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except (AttributeError, TypeError, ValueError, OverflowError):
        return None


class Monitor:
    def __init__(self, *, limit=256, slow_seconds=1., long_seconds=30., clock=time.monotonic):
        self.limit, self.slow_seconds, self.long_seconds = limit, slow_seconds, long_seconds
        self.clock = clock
        self.instance_id = uuid.uuid4().hex
        self.started_at = time.time()
        self.lock = threading.Lock()
        self.local = threading.local()
        self.active = {}
        self.recent = deque(maxlen=limit)
        self.events = deque(maxlen=limit)
        self.started = self.completed = self.failed = self.slow = 0
        self.active_dropped = self.samples_evicted = self.events_dropped = 0
        self.sources = {}
        self.sources_checked_at = None
        self.sources_error = "not_observed"
        self.interval = None
        self.stale_seconds = None
        self.persistence_error = None
        self.last_written_at = None
        self.last_tick_at = None
        self.path = None
        self.stop = threading.Event()
        self.thread = None
        self.event_handler = None
        self.incidents = set()

    def begin(self, operation):
        with self.lock:
            self.started += 1
            token = {"id": self.started, "operation": operation[:80],
                     "started_at": time.time(), "start": self.clock(), "phase": "running"}
            if len(self.active) < self.limit:
                self.active[token["id"]] = token
            else:
                self.active_dropped += 1
            return token

    def phase(self, token, phase):
        with self.lock:
            token["phase"] = phase[:80]

    def outcome(self, value):
        token = getattr(self.local, "token", None)
        if token:
            token["outcome"] = value

    def _event(self, event):
        if len(self.events) == self.limit:
            self.events_dropped += 1
        self.events.append({"instance_id": self.instance_id, "at": time.time(), **event})

    def record_failure(self, operation, exc):
        failure_id = uuid.uuid4().hex
        event = {"kind": "failure", "failure_id": failure_id,
                 "failure": failure_details(exc, stage=operation)}
        with self.lock:
            self._event(event)
        # Also leave a safe immediate record if the observer cannot persist.
        try:
            logging.getLogger(__name__).error("diagnostic_failure %s", json.dumps(event))
        except Exception:
            # A broken logging sink must not replace the business exception.
            with self.lock:
                self.persistence_error = "failure_log_write_failed; inspect diagnostic sinks"
        return failure_id

    def finish(self, token, outcome="success"):
        with self.lock:
            self.active.pop(token["id"], None)
            elapsed = max(0., self.clock() - token["start"])
            row = {k: v for k, v in token.items() if k != "start"}
            outcome = token.get("outcome", outcome) if outcome == "success" else outcome
            row.update(duration_seconds=elapsed, outcome=outcome)
            self.completed += 1
            self.failed += outcome != "success"
            self.slow += elapsed >= self.slow_seconds
            if len(self.recent) == self.limit:
                self.samples_evicted += 1
            self.recent.append(row)
            if elapsed >= self.slow_seconds or outcome != "success":
                self._event({"kind": "operation_completed", **row})

    @contextmanager
    def operation(self, name):
        token = self.begin(name)
        previous = getattr(self.local, "token", None)
        self.local.token = token
        outcome = "success"
        try:
            yield token
        except BaseException:
            outcome = "error"
            raise
        finally:
            self.finish(token, outcome)
            self.local.token = previous

    @contextmanager
    def stage(self, name):
        token = getattr(self.local, "token", None)
        previous = token["phase"] if token else None
        if token:
            self.phase(token, name)
        try:
            yield
        finally:
            if token:
                self.phase(token, previous)

    def set_sources(self, sources, *, interval, stale_seconds, error=None):
        # Caller supplies only configured machine names and observation times.
        with self.lock:
            self.sources = sources
            self.interval, self.stale_seconds = interval, stale_seconds
            self.sources_checked_at = time.time()
            self.sources_error = error

    def source_error(self, error):
        with self.lock:
            self.sources_error = error

    def snapshot(self):
        now, mono = time.time(), self.clock()
        with self.lock:
            active = [{**{k: v for k, v in row.items() if k != "start"},
                       "elapsed_seconds": max(0., mono - row["start"])} for row in self.active.values()]
            sources = []
            for name, item in self.sources.items():
                row = {"machine": name, **item}
                for field in ("generated_at", "observed_at"):
                    stamp = timestamp(row.get(field))
                    row[field.removesuffix("_at") + "_age_seconds"] = max(0., now - stamp) if stamp is not None else None
                age = row["observed_age_seconds"]
                row["age_intervals"] = age / self.interval if age is not None and self.interval else None
                row["stale"] = age >= self.stale_seconds if age is not None and self.stale_seconds else None
                sources.append(row)
            active_count = self.started - self.completed
            return {"instance_id": self.instance_id, "pid": os.getpid(), "started_at": self.started_at,
                    "sampled_at": now, "last_observer_tick_at": self.last_tick_at,
                    "last_written_at": self.last_written_at, "persistence_error": self.persistence_error,
                    "observer_running": self.thread is not None and self.thread.is_alive(),
                    "thresholds_seconds": {"slow_operation": self.slow_seconds, "long_running": self.long_seconds,
                                           "source_stale": self.stale_seconds, "collection_interval": self.interval},
                    "counts_since_start": {"started": self.started, "completed": self.completed,
                                           "failed": self.failed, "slow": self.slow, "active": active_count},
                    "coverage": {"active_details_omitted": active_count - len(active),
                                 "active_details_dropped_total": self.active_dropped,
                                 "completed_samples_evicted": self.samples_evicted,
                                 "events_dropped": self.events_dropped,
                                 "detail_limit": self.limit},
                    "active": active, "recent": list(self.recent), "sources": sources,
                    "sources_checked_at": self.sources_checked_at, "sources_error": self.sources_error,
                    "scope": "this_process; completed durations include body writes; age_intervals is not missed rounds",
                    "next_action": "Inspect active phases and source ages; use docs/operations/services.md runtime diagnostics."}

    def tick(self):
        report = self.snapshot()
        current = set()
        for row in report["active"]:
            if row["elapsed_seconds"] >= self.long_seconds:
                current.add(("long_running", str(row["id"])))
        for row in report["sources"]:
            if row["stale"]:
                current.add(("source_stale", row["machine"]))
            elif row["observed_age_seconds"] is None:
                current.add(("source_unknown", row["machine"]))
        if report["sources_error"]:
            current.add(("sources_unknown", "view"))
        with self.lock:
            for kind, subject in current - self.incidents:
                self._event({"kind": kind, "subject": subject, "transition": "observed"})
            for kind, subject in self.incidents - current:
                # Source read failure cannot prove that a previous stale source recovered.
                if kind == "source_stale" and (report["sources_error"] or
                        any(r["machine"] == subject and r["stale"] is None for r in report["sources"])):
                    current.add((kind, subject))
                    continue
                retired = kind.startswith("source_") and subject not in {r["machine"] for r in report["sources"]}
                self._event({"kind": kind, "subject": subject,
                             "transition": "retired" if retired else "cleared"})
            self.incidents = current
            self.last_tick_at = time.time()
        if self.path is None:
            return
        pending_events = 0
        try:
            self.path.mkdir(parents=True, exist_ok=True)
            if self.event_handler is None:
                self.event_handler = RotatingFileHandler(self.path / "events.jsonl", maxBytes=1024*1024, backupCount=2)
                # logging's default emit hides I/O failures; preserve them as monitor state.
                def failed(record):
                    raise OSError("diagnostic event write failed")
                self.event_handler.handleError = failed
                self.event_handler.setFormatter(logging.Formatter("%(message)s"))
            with self.lock:
                events = list(self.events)
                self.events.clear()
                pending_events = len(events)
            for event in events:
                self.event_handler.emit(logging.LogRecord("diagnostics", logging.INFO, "", 0,
                                                         json.dumps(event), (), None))
                pending_events -= 1
            report = self.snapshot()
            report["persistence_error"] = None
            report["last_written_at"] = time.time()
            temporary = self.path / ("current.%s.tmp" % self.instance_id)
            temporary.write_text(json.dumps(report, allow_nan=False), encoding="utf-8")
            temporary.replace(self.path / "current.json")
            with self.lock:
                self.persistence_error = None
                self.last_written_at = report["last_written_at"]
        except (OSError, ValueError, TypeError):
            with self.lock:
                self.persistence_error = "write_failed; inspect state/diagnostics permissions and disk space"
                # Completed emits are not lost when a later emit or snapshot
                # write fails. This counts unconfirmed emits, not fsync durability.
                self.events_dropped += pending_events

    def start(self, path, interval=5):
        self.path = Path(path)
        def observe():
            while not self.stop.is_set():
                try:
                    self.tick()
                except Exception:
                    self.source_error("observer_failed")
                if self.stop.wait(interval):
                    break
        self.thread = threading.Thread(target=observe, name="agent-monitor-diagnostics", daemon=True)
        self.thread.start()

    def close(self):
        self.stop.set()
        if self.thread is not None:
            self.thread.join(timeout=2)
        if self.event_handler is not None and not (self.thread and self.thread.is_alive()):
            self.event_handler.close()


monitor = Monitor()


def measured(name):
    def decorate(fn):
        @functools.wraps(fn)
        def invoke(*args, **kwargs):
            with monitor.operation(name):
                return fn(*args, **kwargs)
        return invoke
    return decorate
