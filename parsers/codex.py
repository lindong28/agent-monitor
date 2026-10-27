import contextlib
import hashlib
import json
import logging
import os
import shutil
import sqlite3
import tempfile
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from agent_monitor.contracts.usage import RateLimits, UsageEntry
from agent_monitor.contracts.session import codex_metadata, merge_metadata


# Ported and dependency-stripped from token-tracker src/adapters/codex.py.
CODEX_DIR = os.path.expanduser("~/.codex")
SESSIONS_DIR = os.path.join(CODEX_DIR, "sessions")
STATE_DB = os.path.join(CODEX_DIR, "state_5.sqlite")
DEFAULT_CODEX_MODEL = "gpt-5"
# Source-pinned spawn/filter/counter contracts: docs/references/metrics.md.
_PAGINATED_ZERO_BASELINE_VERSIONS = {"0.147.0", "0.152.0", "0.153.4", "0.154.0", "0.158.0", "0.162.1"}

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

    paths = list(sessions_path.rglob("*.jsonl"))
    history = HistoryResolver(paths)
    for jsonl_path in paths:
        try:
            for entry in parse_file(jsonl_path, models=models, history=history):
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


class HistoryResolver:
    """Resolve byte-bounded ancestry, caching only stable source observations."""

    def __init__(self, paths):
        self.paths = list(paths)
        self.headers = {}
        self.prefixes = {}
        self.index = None
        self.fork_index = None
        self.copied_prefixes = {}

    @classmethod
    def for_path(cls, path):
        path = Path(path)
        root = Path(SESSIONS_DIR)
        if not path.is_relative_to(root):
            root = path.parent
        return cls(root.rglob("*.jsonl"))

    def header(self, path):
        path = Path(path)
        stamp = _file_signature(path)
        cached = self.headers.get(path)
        if cached and cached[0] == stamp:
            return cached[1]
        with path.open("rb") as handle:
            data = json.loads(handle.readline())
        payload = data.get("payload") if isinstance(data, dict) and data.get("type") == "session_meta" else None
        if not isinstance(payload, dict) or not payload.get("id"):
            raise ValueError("history source has no owner metadata")
        self.headers[path] = (stamp, payload)
        return payload

    def _parents(self, path, base):
        if self.index is None:
            self.index = {}
            for candidate in self.paths:
                # Paginated files preserve the logical session id in metadata;
                # history_base addresses the physical thread suffix instead.
                identity = Path(candidate).stem[-36:]
                try:
                    if str(uuid.UUID(identity)) != identity:
                        raise ValueError
                except ValueError:
                    try:
                        identity = self.header(candidate)["id"]
                    except (OSError, ValueError, UnicodeError):
                        continue
                self.index.setdefault(identity, []).append(candidate)
        seen = {Path(path)}
        while base is not None:
            if not isinstance(base, dict):
                raise ValueError("invalid history_base")
            limit = base.get("end_byte_offset")
            identity = base.get("thread_id")
            if not isinstance(identity, str) or type(limit) is not int or limit < 0:
                raise ValueError("invalid history_base identity or byte boundary")
            candidates = self.index.get(identity, [])
            if len(candidates) != 1:
                raise ValueError("history_base parent missing or ambiguous")
            parent = Path(candidates[0])
            if parent in seen:
                raise ValueError("cyclic history_base")
            seen.add(parent)
            stamp = _file_signature(parent)
            if limit > stamp[2]:
                raise ValueError("history_base byte boundary exceeds parent")
            yield parent, stamp, limit
            header = self.header(parent)
            base = header.get("history_base")

    def resolve(self, path, base):
        dependencies = []
        for parent, stamp, limit in self._parents(path, base):
            dependencies.append((str(parent), stamp, limit))
            key = (parent, stamp, limit)
            if key not in self.prefixes:
                if limit > stamp[2]:
                    raise ValueError("history_base byte boundary exceeds parent")
                latest = None
                with parent.open("rb") as handle:
                    while handle.tell() < limit:
                        line = handle.readline(limit - handle.tell())
                        if not line.endswith(b"\n"):
                            raise ValueError("history_base boundary is not a complete record")
                        data = json.loads(line)
                        if not isinstance(data, dict):
                            continue
                        payload = data.get("payload")
                        if data.get("type") == "event_msg" and isinstance(payload, dict) and payload.get("type") == "token_count":
                            info = payload.get("info")
                            total = info.get("total_token_usage") if isinstance(info, dict) else None
                            if isinstance(total, dict):
                                if not _valid_fork_usage(total, total):
                                    raise ValueError("history_base has invalid cumulative usage")
                                latest = total
                if _file_signature(parent) != stamp:
                    raise ValueError("history_base changed during read")
                self.prefixes[key] = latest
            latest = self.prefixes[key]
            if latest is not None:
                return latest, dependencies
            header = self.header(parent)
            if header.get("history_base") is None and header.get("forked_from_id"):
                raise ValueError("history_base ancestor has no measurable counter")
        return {"input_tokens": 0, "output_tokens": 0}, dependencies

    def fork_parent(self, path, identity):
        if self.fork_index is None:
            self.fork_index = {}
            for candidate in self.paths:
                try:
                    owner = self.header(candidate)["id"]
                except (OSError, ValueError, UnicodeError):
                    continue
                self.fork_index.setdefault(owner, []).append(Path(candidate))
        candidates = self.fork_index.get(identity, [])
        if len(candidates) != 1 or candidates[0] == Path(path):
            raise ValueError("copied fork parent missing or ambiguous")
        parent = candidates[0]
        return parent, _file_signature(parent)

    def copied_boundary(self, path, header):
        """Match an explicitly bounded original prefix, not the parent's EOF.

        RolloutLine timestamps are regenerated when copied (Codex recorder);
        RolloutItem type/payload are the preserved facts. Cache parent digests
        once per source version, rather than rereading it for every child.
        """
        cutoff = header.get("forked_from_ordinal_exclusive")
        if cutoff is None:
            raise ValueError("copied fork prefix has no explicit inherited boundary")
        if type(cutoff) is not int or cutoff < 0:
            raise ValueError("invalid copied fork ordinal boundary")
        parent, stamp = self.fork_parent(path, header["forked_from_id"])
        key = (parent, stamp, cutoff)
        if key not in self.copied_prefixes:
            records = []
            with parent.open(encoding="utf-8") as handle:
                for number, line in enumerate(handle):
                    row = json.loads(line)
                    if not isinstance(row, dict):
                        raise ValueError("invalid copied fork parent record")
                    ordinal = row.get("ordinal", number)
                    if type(ordinal) is not int or ordinal != number:
                        raise ValueError("copied fork parent ordinal sequence is incomplete")
                    if cutoff is not None and ordinal >= cutoff:
                        break
                    records.append(_rollout_item_digest(row))
            if cutoff is not None and len(records) < cutoff:
                raise ValueError("copied fork ordinal boundary exceeds available parent")
            if _file_signature(parent) != stamp:
                raise ValueError("copied fork parent changed during read")
            self.copied_prefixes[key] = records
        records = self.copied_prefixes[key]
        if not records:
            raise ValueError("copied fork parent has no verifiable prefix")
        position = 0
        with Path(path).open(encoding="utf-8") as handle:
            next(handle)  # This rollout's owner, not part of the copied prefix.
            for line_number, line in enumerate(handle, start=2):
                row = json.loads(line)
                if not isinstance(row, dict):
                    raise ValueError("invalid copied fork record")
                # Some writers omit the parent's SessionMeta when copying.
                if position == 0 and row.get("type") != "session_meta":
                    position = 1
                if position >= len(records) or _rollout_item_digest(row) != records[position]:
                    raise ValueError("copied fork prefix cannot be verified")
                position += 1
                if position == len(records):
                    return line_number
        raise ValueError("copied fork prefix is incomplete")

    def signature(self, path):
        try:
            header = self.header(path)
            base = header.get("history_base")
            if base is None and header.get("forked_from_id") and header.get("subagent_history_start_ordinal") is None:
                if header.get("forked_from_ordinal_exclusive") is None:
                    # The local owner checkpoint, not a mutable parent tail,
                    # determines which later deltas can be attributed.
                    return []
                parent, stamp = self.fork_parent(path, header["forked_from_id"])
                return [(str(parent), stamp, header.get("forked_from_ordinal_exclusive"))]
            # Dependency discovery reads headers/stat only; a warm archive hit
            # must not rescan all ancestor transcript bodies just to key a cache.
            return [(str(parent), stamp, limit) for parent, stamp, limit in self._parents(path, base)]
        except (OSError, ValueError, UnicodeError):
            return None


def _rollout_item_digest(row):
    item = {key: value for key, value in row.items() if key not in ("timestamp", "ordinal")}
    return hashlib.sha256(json.dumps(item, sort_keys=True, separators=(",", ":")).encode()).digest()


def parse_file(path, models=None, source_errors=None, history=None):
    models = models or {}
    session_id = ""
    session_ts = ""
    project = "unknown"
    model = "unknown"
    own_model = "unknown"
    inherited_ordinal = None
    copied_end_line = None
    copied_unknown = False
    was_inherited = False
    previous_usage = None
    inherited_pending = False
    inherited_unknown = False
    fresh_paginated_subagent = False
    producer_version = None
    prefix_decoded = True
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
                    prefix_decoded = False
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
                    producer_version = payload.get("cli_version")
                    fresh_paginated_subagent = (
                        isinstance(producer_version, str)
                        and producer_version in _PAGINATED_ZERO_BASELINE_VERSIONS
                        and payload.get("history_mode") == "paginated"
                        and payload.get("thread_source") == "subagent"
                        and payload.get("history_base") is None
                    )
                    inherited_ordinal = payload.get("subagent_history_start_ordinal")
                    if inherited_ordinal is not None and (type(inherited_ordinal) is not int or inherited_ordinal < 0):
                        _record_source_error(source_errors, path, line_number,
                                             ValueError("invalid subagent history ordinal"))
                        inherited_ordinal = None
                        copied_unknown = True
                    if payload.get("forked_from_id") and payload.get("history_base") is None and inherited_ordinal is None:
                        try:
                            history = history or HistoryResolver.for_path(path)
                            copied_end_line = history.copied_boundary(path, payload)
                        except (OSError, ValueError, UnicodeError, StopIteration) as exc:
                            _record_source_error(source_errors, path, line_number, exc)
                            copied_unknown = True
                    if payload.get("history_base") is not None:
                        inherited_pending = True
                        try:
                            history = history or HistoryResolver.for_path(path)
                            previous_usage, _dependencies = history.resolve(path, payload["history_base"])
                        except (OSError, ValueError, UnicodeError) as exc:
                            _record_source_error(source_errors, path, line_number, exc)
                            inherited_unknown = True

                    session_ts = payload.get("timestamp", "") or data.get("timestamp", "")
                    cwd = payload.get("cwd", "")
                    if cwd:
                        project = _project_from_cwd(cwd)
                    # threads.model is mutable current state, not historical attribution.
                    own_model = payload.get("model") or "unknown"
                    model = own_model
                    continue

                ordinal = data.get("ordinal", line_number - 1)
                inherited = ((inherited_ordinal is not None and type(ordinal) is int and ordinal < inherited_ordinal)
                             or (copied_end_line is not None and line_number <= copied_end_line))
                if was_inherited and not inherited:
                    model = own_model
                    inherited_pending = True
                    inherited_unknown = False
                was_inherited = inherited
                if row_type == "turn_context":
                    context = data.get("payload")
                    if not inherited:
                        model = context.get("model") or "unknown" if isinstance(context, dict) else "unknown"
                    continue
                if row_type != "event_msg":
                    continue

                payload = data.get("payload", {})
                if not isinstance(payload, dict):
                    continue
                if payload.get("type") == "thread_settings_applied":
                    # Copied snapshots retain their original thread_id. Both
                    # fork startup and later resumes persist an own checkpoint,
                    # so it proves future ownership, not the earlier prefix.
                    own_checkpoint = payload.get("thread_id") == session_id
                    zero_baseline = (
                        fresh_paginated_subagent and prefix_decoded and previous_usage is None
                        and type(inherited_ordinal) is int and inherited_ordinal > 0
                        and type(data.get("ordinal")) is int and data["ordinal"] == inherited_ordinal
                        and (own_checkpoint or (producer_version == "0.147.0" and "thread_id" not in payload))
                    )
                    if not inherited and (own_checkpoint or zero_baseline):
                        settings = payload.get("thread_settings")
                        model = (settings.get("model") or "unknown") if isinstance(settings, dict) else "unknown"
                        if zero_baseline:
                            # These paginated producers filter inherited TokenCount
                            # before seeding usage. 0.147.0's startup checkpoint
                            # predates thread_id; its exact boundary proves ownership.
                            previous_usage = {}
                        if copied_unknown:
                            copied_unknown = False
                            inherited_pending = True
                            inherited_unknown = previous_usage is None
                    continue
                if payload.get("type") != "token_count":
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
                if inherited:
                    # Keep the old event identity so archive upsert repairs it.
                    delta = {"input_tokens": 0, "output_tokens": 0,
                             "cache_creation_tokens": 0, "cache_read_tokens": 0}
                elif copied_unknown:
                    # No proven boundary: later copied events are not new calls.
                    # Emit nothing, so retained archive facts remain with source_errors.
                    # Keep the observed counter for a later thread-owned checkpoint;
                    # that checkpoint can recover future deltas without guessing here.
                    previous_usage = total_usage
                    continue
                elif inherited_ordinal is not None and inherited_ordinal > 1 and previous_usage is None:
                    _record_source_error(source_errors, path, line_number,
                                         ValueError("inherited subagent history has no measurable counter"))
                    previous_usage = total_usage
                    inherited_pending = False
                    continue
                elif inherited_pending:
                    inherited_pending = False
                    if inherited_unknown:
                        previous_usage = total_usage
                        continue
                    delta = _usage_delta(previous_usage, total_usage, info.get("last_token_usage")) or {
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
                        cost_usd=None if any(delta.values()) else 0.0,
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


def load_rate_limits(sessions_dir=None, state_db=None, *, session_links=None, source_errors=None):
    from . import codex_profiles
    links = session_links if session_links is not None else codex_profiles.session_links(source_errors=source_errors)
    if links.unavailable:
        return None
    sessions_path = Path(sessions_dir or SESSIONS_DIR)
    if not sessions_path.is_dir():
        return None

    state_path = state_db or STATE_DB
    jsonl_files = sorted(sessions_path.rglob("*.jsonl"))
    signature = (_files_signature(jsonl_files, state_path), links.signature)
    if signature == _RATE_LIMITS_CACHE["signature"]:
        return _RATE_LIMITS_CACHE["value"]

    models = _load_thread_models(state_path)
    candidates = filter(None, (_extract_latest_rate_limits(path, models, session_links=links) for path in jsonl_files))
    latest = max(candidates, key=lambda limits: _timestamp_key(limits.updated_at), default=None)
    _RATE_LIMITS_CACHE.update(signature=signature, value=latest)
    return latest


def load_session_rate_limits(session_id, sessions_dir=None):
    """Read a session's stored weekly quota; no provider or account lookup."""
    try:
        if str(uuid.UUID(session_id)) != session_id:
            raise ValueError
    except (ValueError, AttributeError, TypeError):
        raise ValueError("use the full canonical session UUID; session not queried") from None
    from . import codex_profiles
    links = codex_profiles.session_links()
    if links.excludes(session_id):
        raise ValueError("session account is mixed or unverified; stored quota excluded")
    sessions_path = Path(SESSIONS_DIR if sessions_dir is None else sessions_dir)
    if not sessions_path.is_dir():
        raise ValueError("session directory unavailable; session not queried")
    paths = list(sessions_path.rglob("rollout-*-" + session_id + ".jsonl"))
    result = {"session_id": session_id, "status": "no_rollout",
              "seven_day_pct": None, "seven_day_resets_at": None,
              "updated_at": None, "account_identity": "unknown"}
    if not paths:
        return result
    if len(paths) != 1:
        raise ValueError("multiple rollout files for this session; quota unverified")
    path = paths[0]
    if _read_session_id(path) != session_id:
        raise ValueError("rollout identity unreadable or mismatched; quota unverified")
    limits = _extract_latest_rate_limits(path, {}, expire=False, session_links=links)
    if limits is None or limits.seven_day_pct is None:
        result["status"] = "no_weekly_reading"
        return result
    result.update(status="observed", seven_day_pct=limits.seven_day_pct,
                  seven_day_resets_at=limits.seven_day_resets_at,
                  updated_at=limits.updated_at)
    return result


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


class ThreadModels(dict):
    session_metadata = ()


def _query_thread_models(state_path, immutable, read_only=True):
    query = "mode=ro&immutable=1" if immutable else ("mode=ro" if read_only else "mode=rw")
    uri = state_path.resolve().as_uri() + "?" + query
    with contextlib.closing(sqlite3.connect(uri, uri=True)) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(threads)")}
        selected = ["id", "model"] + [key for key in
                    ("source", "title", "name", "created_at", "created_at_ms", "cwd") if key in columns]
        rows = [dict(zip(selected, row)) for row in conn.execute("SELECT " + ", ".join(selected) + " FROM threads")]
    models = ThreadModels({row["id"]: row["model"] for row in rows if row["id"] and row["model"]})
    models.session_metadata = [codex_metadata(row["id"], row.get("source"),
        title=row.get("name") or row.get("title"), project=row.get("cwd"),
        created_at=row["created_at_ms"] / 1000 if type(row.get("created_at_ms")) is int else row.get("created_at"))
        for row in rows if isinstance(row["id"], str) and row["id"]]
    return models


def session_metadata(history, models):
    headers = []
    for path in history.paths:
        try:
            header = history.header(path)
        except (OSError, ValueError, UnicodeError):
            # The usage parser already reports unreadable sources. Metadata
            # absence never changes a retained usage event or invents a link.
            continue
        row = codex_metadata(header["id"], header.get("source"),
                             created_at=header.get("timestamp"), project=header.get("cwd"))
        if row:
            headers.append(row)
    # DB titles are mutable; headers supply owner IDs even for paginated files.
    return merge_metadata(headers, getattr(models, "session_metadata", ()))


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


def _is_codex_limit(snapshot, identity_key="limit_id"):
    # The fixed producer defaults absent/null IDs to codex; explicit other or
    # malformed IDs never acquire that legacy default.
    return isinstance(snapshot, dict) and snapshot.get(identity_key) in (None, "codex")


def select_codex_rate_limit(result):
    """Select the ordinary Codex bucket from the app-server multi-bucket API."""
    buckets = result.get("rateLimitsByLimitId")
    if buckets is not None:
        selected = buckets.get("codex") if isinstance(buckets, dict) else None
    else:
        selected = result.get("rateLimits")
    return selected if _is_codex_limit(selected, "limitId") else None


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
                if rate_limits and _is_codex_limit(rate_limits):
                    last_rate_limits = (rate_limits, data.get("timestamp", ""), session_id)
    except (OSError, PermissionError):
        return None

    if not last_rate_limits:
        return None

    rate_limits, timestamp, session_id = last_rate_limits
    return _build_rate_limits(rate_limits, timestamp, session_id, models)


def _extract_latest_rate_limits(path, models, *, expire=True, session_links=None):
    session_id = _read_session_id(path)
    if session_links is not None and session_links.excludes(session_id):
        return None
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
            if rate_limits and _is_codex_limit(rate_limits):
                return _build_rate_limits(rate_limits, data.get("timestamp", ""), session_id, models, expire=expire)
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


def _build_rate_limits(rate_limits, timestamp, session_id, models, *, expire=True):
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

    # `expire` remains accepted for callers; an elapsed reset is not a new
    # provider observation. Consumers can mark the retained reset as expired.

    if five_pct is None and seven_pct is None:
        return None

    return RateLimits(
        five_hour_pct=five_pct,
        five_hour_resets_at=five_reset,
        seven_day_pct=seven_pct,
        seven_day_resets_at=seven_reset,
        model=models.get(session_id, DEFAULT_CODEX_MODEL),
        updated_at=timestamp,
        # Plan and percentages belong to this same stored observation.
        plan=_plan_type(rate_limits),
        quota_observation={"source": "codex_session", "observed_at": timestamp, "session_id": session_id},
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
