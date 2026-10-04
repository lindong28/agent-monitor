import contextlib
import json
import logging
import os
import shutil
import sqlite3
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import RateLimits, UsageEntry


# Ported and dependency-stripped from token-tracker src/adapters/codex.py.
CODEX_DIR = os.path.expanduser("~/.codex")
SESSIONS_DIR = os.path.join(CODEX_DIR, "sessions")
STATE_DB = os.path.join(CODEX_DIR, "state_5.sqlite")
DEFAULT_CODEX_MODEL = "gpt-5"

logger = logging.getLogger(__name__)
_RATE_LIMITS_CACHE = {"signature": None, "value": None}


def load_entries(hours_back=0, sessions_dir=None, state_db=None):
    entries = []
    seen = set()
    cutoff = None
    if hours_back > 0:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours_back)

    models = _load_thread_models(state_db or STATE_DB)
    sessions_path = Path(sessions_dir or SESSIONS_DIR)
    if not sessions_path.is_dir():
        return entries

    for jsonl_path in sessions_path.rglob("*.jsonl"):
        try:
            for entry in parse_file(jsonl_path, models=models):
                if cutoff and entry.timestamp < cutoff:
                    continue
                if entry.dedup_key in seen:
                    continue
                seen.add(entry.dedup_key)
                entries.append(entry)
        except Exception as exc:
            logger.warning("Skipping Codex session %s: %s", jsonl_path, exc)

    entries.sort(key=lambda entry: entry.timestamp)
    return entries


def parse_file(path, models=None, source_errors=None):
    models = models or {}
    session_id = ""
    session_ts = ""
    project = "unknown"
    model = DEFAULT_CODEX_MODEL
    is_fork = False
    previous_usage = None
    entries = []

    try:
        with open(path, "r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError as exc:
                    _record_source_error(source_errors, path, line_number, exc)
                    continue
                if not isinstance(data, dict):
                    continue

                row_type = data.get("type")
                if row_type == "session_meta":
                    payload = data.get("payload", {})
                    if session_id or not isinstance(payload, dict) or not payload.get("id"):
                        continue
                    # Copied parent metadata can follow the real file owner.
                    session_id = payload.get("id", "") or ""
                    is_fork = bool(payload.get("forked_from_id"))
                    session_ts = payload.get("timestamp", "") or data.get("timestamp", "")
                    cwd = payload.get("cwd", "")
                    if cwd:
                        project = _project_from_cwd(cwd)
                    model = models.get(session_id) or payload.get("model") or DEFAULT_CODEX_MODEL
                    continue

                if row_type != "event_msg":
                    continue

                payload = data.get("payload", {})
                if not isinstance(payload, dict) or payload.get("type") != "token_count":
                    continue
                info = payload.get("info")
                if not isinstance(info, dict):
                    continue
                total_usage = info.get("total_token_usage")
                if not isinstance(total_usage, dict) or not session_id:
                    continue
                timestamp = _parse_usage_timestamp(
                    data.get("timestamp") or session_ts,
                    path,
                    line_number,
                    source_errors,
                )
                if timestamp is None:
                    continue
                if is_fork and previous_usage is None:
                    last = info.get("last_token_usage")
                    if not _valid_fork_usage(last, total_usage):
                        _record_source_error(source_errors, path, line_number,
                                             ValueError("fork initial token_count has no valid last_token_usage"))
                        previous_usage = total_usage
                        continue
                    # total includes the ancestor's billed history; last is the
                    # first request made by this fork. Preserve a zero-valued
                    # key so archive upserts also repair an inherited-only row.
                    if _usage_total(total_usage) == 0:
                        previous_usage = total_usage
                        continue
                    delta = _usage_delta(None, last, last) or {
                        "input_tokens": 0, "output_tokens": 0,
                        "cache_creation_tokens": 0, "cache_read_tokens": 0,
                    }
                else:
                    delta = _usage_delta(previous_usage, total_usage, info.get("last_token_usage"))
                previous_usage = total_usage
                if delta is None:
                    continue
                entries.append(
                    UsageEntry(
                        timestamp=timestamp,
                        session_id=session_id,
                        message_id=Path(path).stem,
                        request_id="token-count:%d" % line_number,
                        model=model,
                        input_tokens=delta["input_tokens"],
                        output_tokens=delta["output_tokens"],
                        cache_creation_tokens=delta["cache_creation_tokens"],
                        cache_read_tokens=delta["cache_read_tokens"],
                        cost_usd=None,
                        project=project,
                        agent_id="codex",
                        message_count=int(any(delta.values())),
                    )
                )
    except (OSError, PermissionError, UnicodeError) as exc:
        _record_source_error(source_errors, path, None, exc)
        return []

    return entries


def _valid_fork_usage(last, total):
    if not isinstance(last, dict):
        return False
    for key in ("input_tokens", "output_tokens", "cached_input_tokens", "cache_write_input_tokens"):
        value = last.get(key, 0 if key in ("cached_input_tokens", "cache_write_input_tokens") else None)
        if type(value) is not int or value < 0 or value > _int(total.get(key)):
            return False
    return (last.get("cached_input_tokens", 0) + last.get("cache_write_input_tokens", 0)
            <= last["input_tokens"])


def _usage_delta(previous, current, last):
    current_total = _usage_total(current)
    previous_total = _usage_total(previous) if isinstance(previous, dict) else None
    if previous_total is None or current_total < previous_total:
        delta_total = current_total
        fallback = current
    else:
        delta_total = current_total - previous_total
        fallback = {
            key: _int(current.get(key)) - _int(previous.get(key))
            for key in (
                "input_tokens",
                "cached_input_tokens",
                "cache_write_input_tokens",
                "output_tokens",
            )
        }
    if delta_total <= 0:
        return None

    usage = last if isinstance(last, dict) and _usage_total(last) == delta_total else fallback
    total_input = _int(usage.get("input_tokens"))
    output_tokens = _int(usage.get("output_tokens"))
    if total_input < 0 or output_tokens < 0 or total_input + output_tokens != delta_total:
        output_tokens = max(min(output_tokens, delta_total), 0)
        total_input = delta_total - output_tokens
    cache_read = max(min(_int(usage.get("cached_input_tokens")), total_input), 0)
    cache_creation = max(
        min(_int(usage.get("cache_write_input_tokens")), total_input - cache_read),
        0,
    )
    return {
        "input_tokens": total_input - cache_read - cache_creation,
        "output_tokens": output_tokens,
        "cache_creation_tokens": cache_creation,
        "cache_read_tokens": cache_read,
    }


def _usage_total(usage):
    return _int(usage.get("input_tokens")) + _int(usage.get("output_tokens"))


def _parse_usage_timestamp(value, path, line_number, source_errors):
    try:
        timestamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        _record_source_error(
            source_errors,
            path,
            line_number,
            ValueError("token_count event has no valid timestamp"),
        )
        return None
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    return timestamp


def _record_source_error(source_errors, path, line_number, exc):
    if source_errors is None:
        return
    detail = "%s: %s" % (type(exc).__name__, exc)
    if line_number is not None:
        detail = "line %d: %s" % (line_number, detail)
    source_errors.append({"path": str(path), "stage": "parse", "error": detail})


def load_rate_limits(sessions_dir=None, state_db=None):
    sessions_path = Path(sessions_dir or SESSIONS_DIR)
    if not sessions_path.is_dir():
        return None

    state_path = state_db or STATE_DB
    jsonl_files = sorted(sessions_path.rglob("*.jsonl"))
    signature = _files_signature(jsonl_files, state_path)
    if signature == _RATE_LIMITS_CACHE["signature"]:
        return _RATE_LIMITS_CACHE["value"]

    models = _load_thread_models(state_path)
    candidates = filter(None, (_extract_latest_rate_limits(path, models) for path in jsonl_files))
    latest = max(candidates, key=lambda limits: _timestamp_key(limits.updated_at), default=None)
    _RATE_LIMITS_CACHE.update(signature=signature, value=latest)
    return latest


def _files_signature(paths, state_db):
    signature = []
    for path in paths:
        try:
            stat = path.stat()
        except OSError:
            continue
        signature.append((str(path), stat.st_mtime_ns, stat.st_size))
    if state_db:
        try:
            stat = Path(state_db).stat()
            signature.append((str(state_db), stat.st_mtime_ns, stat.st_size))
        except OSError:
            pass
    return tuple(signature)


def _load_thread_models(state_db, immutable=False, source_errors=None):
    if not state_db:
        return {}
    try:
        state_path = Path(state_db)
        if _optional_file_signature(state_path) is None:
            return {}
        if immutable:
            return _load_thread_models_from_snapshot(state_path)
        return _query_thread_models(state_path, immutable=immutable)
    except (sqlite3.Error, OSError) as exc:
        _record_metadata_error(source_errors, state_db, exc)
        return {}


def _load_thread_models_from_snapshot(state_path):
    with tempfile.TemporaryDirectory(prefix="agent-monitor-codex-metadata-") as tmp:
        for attempt in range(3):
            snapshot_dir = Path(tmp) / str(attempt)
            snapshot_dir.mkdir()
            snapshot_path = snapshot_dir / state_path.name
            try:
                before = _metadata_signature(state_path)
                shutil.copyfile(state_path, snapshot_path)
                if before[1] is not None:
                    shutil.copyfile(
                        Path(str(state_path) + "-wal"),
                        Path(str(snapshot_path) + "-wal"),
                    )
                after = _metadata_signature(state_path)
            except FileNotFoundError:
                continue
            if before == after:
                # This private copy may need WAL/SHM initialization even when
                # the source has no sidecars. Never open the live DB for writes.
                return _query_thread_models(snapshot_path, immutable=False, read_only=False)
    raise sqlite3.OperationalError("Codex metadata changed while taking a read-only snapshot")


def _metadata_signature(state_path):
    wal_path = Path(str(state_path) + "-wal")
    return (_file_signature(state_path), _optional_file_signature(wal_path))


def _optional_file_signature(path):
    try:
        return _file_signature(path)
    except FileNotFoundError:
        return None


def _file_signature(path):
    file_stat = path.stat()
    return (file_stat.st_dev, file_stat.st_ino, file_stat.st_size, file_stat.st_mtime_ns)


def _query_thread_models(state_path, immutable, read_only=True):
    query = "mode=ro&immutable=1" if immutable else ("mode=ro" if read_only else "mode=rw")
    uri = state_path.resolve().as_uri() + "?" + query
    with contextlib.closing(sqlite3.connect(uri, uri=True)) as conn:
        rows = conn.execute("SELECT id, model FROM threads WHERE model IS NOT NULL").fetchall()
    return {row[0]: row[1] for row in rows if row[0] and row[1]}


def _record_metadata_error(source_errors, path, exc):
    if source_errors is None:
        return
    source_errors.append(
        {
            "path": str(path),
            "stage": "metadata",
            "error": "%s: %s" % (type(exc).__name__, exc),
        }
    )


def _extract_rate_limits(path, models):
    session_id = ""
    last_rate_limits = None
    try:
        with open(path, "r", encoding="utf-8") as handle:
            for line in handle:
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(data, dict):
                    continue
                if data.get("type") == "session_meta":
                    session_id = data.get("payload", {}).get("id", "")
                if data.get("type") != "event_msg":
                    continue
                payload = data.get("payload", {})
                if payload.get("type") != "token_count":
                    continue
                rate_limits = payload.get("rate_limits")
                if rate_limits:
                    last_rate_limits = (rate_limits, data.get("timestamp", ""), session_id)
    except (OSError, PermissionError):
        return None

    if not last_rate_limits:
        return None

    rate_limits, timestamp, session_id = last_rate_limits
    return _build_rate_limits(rate_limits, timestamp, session_id, models)


def _extract_latest_rate_limits(path, models):
    session_id = _read_session_id(path)
    try:
        for line in _reverse_lines(path):
            if '"rate_limits"' not in line:
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(data, dict) or data.get("type") != "event_msg":
                continue
            payload = data.get("payload", {})
            if not isinstance(payload, dict) or payload.get("type") != "token_count":
                continue
            rate_limits = payload.get("rate_limits")
            if rate_limits:
                return _build_rate_limits(rate_limits, data.get("timestamp", ""), session_id, models)
    except (OSError, PermissionError, UnicodeDecodeError):
        return None
    return None


def _read_session_id(path):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            for line in handle:
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(data, dict) and data.get("type") == "session_meta":
                    payload = data.get("payload", {})
                    return payload.get("id", "") if isinstance(payload, dict) else ""
    except (OSError, PermissionError, UnicodeDecodeError):
        pass
    return ""


def _reverse_lines(path, chunk_size=65536):
    with open(path, "rb") as handle:
        handle.seek(0, os.SEEK_END)
        position = handle.tell()
        buffer = b""
        while position > 0:
            size = min(chunk_size, position)
            position -= size
            handle.seek(position)
            buffer = handle.read(size) + buffer
            lines = buffer.split(b"\n")
            if position > 0:
                buffer = lines.pop(0)
            else:
                buffer = b""
            for raw in reversed(lines):
                if raw.strip():
                    yield raw.decode("utf-8")


def _build_rate_limits(rate_limits, timestamp, session_id, models):
    primary = rate_limits.get("primary") or {}
    secondary = rate_limits.get("secondary") or {}
    windows = [window for window in (primary, secondary) if isinstance(window, dict)]
    if any(window.get("window_minutes") is not None for window in windows):
        five_hour = next(
            (window for window in windows if _int(window.get("window_minutes")) == 300),
            primary if primary.get("window_minutes") is None else {},
        )
        seven_day = next(
            (window for window in windows if _int(window.get("window_minutes")) == 10080),
            secondary if secondary.get("window_minutes") is None else {},
        )
    else:
        five_hour, seven_day = primary, secondary

    five_pct = five_hour.get("used_percent")
    five_reset = five_hour.get("resets_at")
    seven_pct = seven_day.get("used_percent")
    seven_reset = seven_day.get("resets_at")

    now_ts = datetime.now(timezone.utc).timestamp()
    if five_reset and five_reset < now_ts:
        five_pct = 0.0
    if seven_reset and seven_reset < now_ts:
        seven_pct = 0.0

    if five_pct is None and seven_pct is None:
        return None

    return RateLimits(
        five_hour_pct=five_pct,
        five_hour_resets_at=five_reset,
        seven_day_pct=seven_pct,
        seven_day_resets_at=seven_reset,
        model=models.get(session_id, DEFAULT_CODEX_MODEL),
        updated_at=timestamp,
        # Same event as the percentages above, which is what makes it the one
        # plan that can be shown beside them without pairing two clocks.
        #
        # "Same event" is the honest claim, not "same observation": the two
        # branches above rewrite a percentage to 0.0 once its own reset time
        # has passed, so after a reset the figure is derived here and was
        # never reported at `updated_at`. A plan that changed during that
        # window would then sit next to a zero it did not produce — the pair
        # the credential comparison downstream is there to expose, since a
        # changed plan is exactly what makes the two sources differ.
        plan=_plan_type(rate_limits),
    )


def _plan_type(rate_limits):
    """The reading's own plan, or None when it does not usably state one.

    Non-empty string or nothing: `rate_limits` has no wire schema, and a
    truthy non-string here would travel all the way to a rendered label and to
    the equality that decides whether the two plan sources disagree.
    """
    plan = rate_limits.get("plan_type")
    return plan if isinstance(plan, str) and plan else None


def _timestamp_key(value):
    try:
        parsed = datetime.fromisoformat((value or "").replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return datetime.min.replace(tzinfo=timezone.utc)


def _project_from_cwd(cwd):
    return cwd or "unknown"


def _int(value):
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0
