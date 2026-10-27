"""Agent usage statistics inside the admitted, digest-bound snapshot.db."""

from contextlib import closing, contextmanager
from copy import deepcopy
from dataclasses import fields
from datetime import date, datetime, timedelta, timezone
import json
import math
from pathlib import Path
import sqlite3
import diagnostics

from parsers import UsageEntry, USAGE_QUALITIES, usage_quality_counts, validate_usage_metadata
from session_identity import SessionIndex, validate as validate_session


VERSION = 5
V3_TABLES = {"statistics_meta", "statistics_usage"}
TABLES = V3_TABLES | {"statistics_sessions"}
LEGACY_TABLES = V3_TABLES | {"statistics_requests", "statistics_attempts"}
META_FIELDS = {"version", "observed_at", "history_authoritative_from", "scan_complete",
               "blocked_source_count", "source_error_count"}
USAGE_FIELDS = {field.name for field in fields(UsageEntry)}
EVIDENCE_FIELDS = {"usage_quality", "service_context", "cost_provenance"}
LEGACY_USAGE_FIELDS = USAGE_FIELDS - EVIDENCE_FIELDS



class StatisticsSnapshotError(ValueError):
    pass


def ensure_retained(previous_path, candidate_path):
    """A source reset or exporter downgrade must not erase centrally saved events."""
    previous = read_snapshot(previous_path)
    if previous["state"] != "available":
        return
    candidate = read_snapshot(candidate_path)
    if candidate["state"] != "available":
        raise StatisticsSnapshotError("exporter omitted previously collected statistics; last successful data retained")
    if _parse_timestamp(candidate["observed_at"]) < _parse_timestamp(previous["observed_at"]):
        raise StatisticsSnapshotError("statistics observation moved backwards; last successful data retained")
    before_usage = {entry.dedup_key for entry in previous["usage_entries"]}
    after_usage = {entry.dedup_key for entry in candidate["usage_entries"]}
    if not before_usage <= after_usage:
        raise StatisticsSnapshotError("source usage history shrank; restore its archive before retrying")
    before_sessions = {(r["agent_id"], r["session_id"]) for r in previous.get("session_metadata", ())}
    after_sessions = {(r["agent_id"], r["session_id"]) for r in candidate.get("session_metadata", ())}
    if not before_sessions <= after_sessions:
        raise StatisticsSnapshotError("source session metadata shrank; restore its archive before retrying")
    after_metadata = {(r["agent_id"], r["session_id"]): r for r in candidate.get("session_metadata", ())}
    for row in previous.get("session_metadata", ()):
        new = after_metadata[(row["agent_id"], row["session_id"])]
        if row["relation"] in ("root", "child", "conflict") and new["relation"] == "unknown":
            raise StatisticsSnapshotError("source lost recorded session ancestry; restore its archive before retrying")


def read_metadata(path):
    """Read coverage from an already admitted immutable snapshot without decoding events."""
    with closing(sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)) as conn:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='statistics_meta'").fetchone():
            return {"state": "not_collected"}
        rows = conn.execute("SELECT payload FROM statistics_meta").fetchall()
        if len(rows) != 1:
            raise StatisticsSnapshotError("invalid statistics metadata")
        meta = json.loads(rows[0][0])
        _validate_meta(meta)
        meta.pop("gateway", None)
        meta.setdefault("usage_quality_counts", None)
        return {"state": "available", **meta}


def _timestamp(value=None):
    value = datetime.now(timezone.utc) if value is None else value
    if isinstance(value, str):
        value = _parse_timestamp(value)
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp requires a timezone")
    return value.astimezone(timezone.utc).isoformat()


def write_snapshot(snapshot_path, loaded_entries, *, observed_at=None):
    """Write to an unpublished snapshot copy; the caller owns publish/fsync/digest."""
    observed_at = _timestamp(observed_at)
    meta = {"version": VERSION, "observed_at": observed_at,
            "history_authoritative_from": getattr(loaded_entries, "history_authoritative_from", None),
            "scan_complete": bool(getattr(loaded_entries, "scan_complete", True)),
            "blocked_source_count": len(getattr(loaded_entries, "blocked_sources", ())),
            "source_error_count": len(getattr(loaded_entries, "source_errors", ()))}
    usage = []
    entries = list(loaded_entries) + list(getattr(loaded_entries, "unattributed_entries", ()))
    meta["usage_quality_counts"] = usage_quality_counts(entries)
    sessions = [validate_session(row) for row in getattr(loaded_entries, "session_metadata", ())]
    for entry in entries:
        # Scalar fields are immutable; copying each datetime just before
        # formatting it dominates large exports. Only metadata needs a copy.
        row = {name: getattr(entry, name) for name in USAGE_FIELDS}
        for name in ("service_context", "cost_provenance"):
            row[name] = deepcopy(row[name])
        row["timestamp"] = _timestamp(entry.timestamp)
        _usage_entry(row)
        # Validate/encode before touching the destination, but retain only the
        # encoded form rather than all dictionaries plus a second JSON batch.
        usage.append(json.dumps(row, allow_nan=False, sort_keys=True))
    _validate_meta(meta)
    with closing(sqlite3.connect(str(snapshot_path))) as conn, conn:
        for name in sorted(LEGACY_TABLES - TABLES):
            conn.execute(f"DROP TABLE IF EXISTS {name}")
        for name in sorted(TABLES):
            conn.execute(f"CREATE TABLE IF NOT EXISTS {name} (position INTEGER PRIMARY KEY, payload TEXT NOT NULL)")
            conn.execute(f"DELETE FROM {name}")
        for name, rows in (("statistics_meta", [meta]), ("statistics_usage", usage), ("statistics_sessions", sessions)):
            conn.executemany(f"INSERT INTO {name} VALUES (?, ?)",
                             ((index, row if name == "statistics_usage" else
                               json.dumps(row, allow_nan=False, sort_keys=True))
                              for index, row in enumerate(rows)))
        _create_usage_indexes(conn)
    return read_snapshot(snapshot_path)


def _create_usage_indexes(conn):
    # Optional access paths: old snapshots remain readable, with identical
    # predicates and position ordering. Build only on the unpublished copy.
    conn.execute("CREATE INDEX IF NOT EXISTS idx_usage_date ON statistics_usage "
                 "(substr(json_extract(payload, '$.timestamp'), 1, 10))")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_usage_session ON statistics_usage "
                 "(json_extract(payload, '$.session_id'))")


def _validate_meta(meta):
    if not isinstance(meta, dict) or type(meta.get("version")) is not int or meta["version"] not in (1, 2, 3, 4, VERSION):
        raise ValueError("unsupported statistics extension")
    expected = META_FIELDS | ({"gateway"} if meta["version"] == 1 else set())
    if meta["version"] >= 3:
        expected |= {"usage_quality_counts"}
    if set(meta) != expected:
        raise ValueError("invalid statistics metadata fields")
    if not isinstance(meta["observed_at"], str):
        raise ValueError("invalid observation time")
    _timestamp(meta["observed_at"])
    if meta["history_authoritative_from"] is not None:
        date.fromisoformat(meta["history_authoritative_from"])
    if type(meta["scan_complete"]) is not bool:
        raise ValueError("invalid scan state")
    for key in ("blocked_source_count", "source_error_count"):
        if type(meta[key]) is not int or meta[key] < 0:
            raise ValueError("invalid source count")
    if meta["version"] >= 3:
        counts = meta["usage_quality_counts"]
        if (not isinstance(counts, dict) or set(counts) != set(USAGE_QUALITIES)
                or any(type(value) is not int or value < 0 for value in counts.values())):
            raise ValueError("invalid usage quality counts")


def _usage_entry(row, version=VERSION, *, metadata_validator=None):
    expected = LEGACY_USAGE_FIELDS if version in (1, 2) else USAGE_FIELDS
    if not isinstance(row, dict) or set(row) != expected:
        raise ValueError("invalid usage entry fields")
    row = dict(row)
    row["timestamp"] = _parse_timestamp(row["timestamp"])
    for key in ("session_id", "message_id", "request_id", "model", "project", "agent_id"):
        if not isinstance(row[key], str):
            raise ValueError("invalid usage identity")
    for key in ("input_tokens", "output_tokens", "cache_creation_tokens", "cache_read_tokens", "message_count", "cache_creation_1h_tokens"):
        if type(row[key]) is not int or row[key] < 0:
            raise ValueError("invalid token count")
    if row["cache_creation_1h_tokens"] > row["cache_creation_tokens"]:
        raise ValueError("one-hour cache tokens exceed total cache creation tokens")
    if row["cost_usd"] is not None and (type(row["cost_usd"]) not in (float, int) or not math.isfinite(row["cost_usd"]) or row["cost_usd"] < 0):
        raise ValueError("invalid usage cost")
    (metadata_validator or validate_usage_metadata)(row)
    return UsageEntry(**row)


def _parse_timestamp(value):
    if not isinstance(value, str):
        raise ValueError("invalid timestamp")
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("timestamp requires a timezone")
    return result


def read_snapshot(snapshot_path):
    """Validate and return statistics; legacy snapshots explicitly lack coverage."""
    try:
        with closing(sqlite3.connect(Path(snapshot_path).resolve().as_uri() + "?mode=ro", uri=True)) as conn:
            conn.execute("BEGIN")
            objects = conn.execute("SELECT name, type FROM sqlite_master WHERE name LIKE 'statistics_%'").fetchall()
            if not objects:
                return {"state": "not_collected", "observed_at": None, "usage_entries": [],
                        "history_authoritative_from": None}
            present = {name for name, _ in objects}
            if present not in (TABLES, V3_TABLES, LEGACY_TABLES) or any(kind != "table" for _, kind in objects):
                raise ValueError("incomplete statistics extension")
            rows = {}
            for name in sorted(present & TABLES):
                columns = conn.execute(f"PRAGMA table_info({name})").fetchall()
                if [(row[1], row[2], row[3], row[5]) for row in columns] != [("position", "INTEGER", 0, 1), ("payload", "TEXT", 1, 0)]:
                    raise ValueError("unsupported statistics table")
                rows[name] = [json.loads(row[0]) for row in conn.execute(f"SELECT payload FROM {name} ORDER BY position")]
            if len(rows["statistics_meta"]) != 1:
                raise ValueError("invalid statistics metadata")
            meta = rows["statistics_meta"][0]
            _validate_meta(meta)
            expected_tables = LEGACY_TABLES if meta["version"] == 1 else (TABLES if meta["version"] >= 4 else V3_TABLES)
            if {name for name, _ in objects} != expected_tables:
                raise ValueError("statistics version/table mismatch")
            meta.pop("gateway", None)  # v1 raw Gateway rows are retired; outer digest still binds them.
            entries = [_usage_entry(row, version=meta["version"]) for row in rows["statistics_usage"]]
            if meta["version"] >= 3 and meta["usage_quality_counts"] != usage_quality_counts(entries):
                raise ValueError("usage quality counts disagree with retained observations")
            meta.setdefault("usage_quality_counts", None)
            sessions = [validate_session(row) for row in rows.get("statistics_sessions", [])]
            if len({(r["agent_id"], r["session_id"]) for r in sessions}) != len(sessions):
                raise ValueError("duplicate session metadata")
            return {**meta, "state": "available", "usage_entries": entries, "session_metadata": sessions}
    except (sqlite3.Error, ValueError, TypeError, KeyError) as exc:
        raise StatisticsSnapshotError("Statistics snapshot is invalid or unavailable") from exc


def validate_snapshot(snapshot_path):
    return read_snapshot(snapshot_path)


@contextmanager
def _admitted_connection(current):
    """Only consume an immutable generation while its admission lease is held.

    Export/import validates every domain; admission verifies the complete digest.
    These readers do not replace either boundary with partial validation.
    """
    from generation import CurrentGeneration
    if (not isinstance(current, CurrentGeneration) or current._lease is None
            or current._lease.closed):
        raise StatisticsSnapshotError("An active generation lease is required")
    try:
        with closing(sqlite3.connect(current.db_path.resolve().as_uri() + "?mode=ro", uri=True)) as conn:
            conn.execute("BEGIN")
            yield conn
    except (sqlite3.Error, ValueError, TypeError, KeyError) as exc:
        raise StatisticsSnapshotError("Statistics snapshot is invalid or unavailable") from exc


def _admitted_meta(conn):
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='statistics_meta'").fetchone():
        return None
    rows = conn.execute("SELECT payload FROM statistics_meta").fetchall()
    if len(rows) != 1:
        raise ValueError("invalid statistics metadata")
    meta = json.loads(rows[0][0])
    _validate_meta(meta)
    return meta


@contextmanager
def admitted_usage_rows(current, *, time_range=None, query=None, session_id=None, session_agent=None, identities=False):
    """Select the existing candidates while retaining the admission transaction."""
    with _admitted_connection(current) as conn:
        meta = _admitted_meta(conn)
        if meta is None:
            yield None, [], iter(())
            return
        sessions = [json.loads(row[0]) for row in conn.execute("SELECT payload FROM statistics_sessions ORDER BY position")] if meta["version"] >= 4 else []
        clauses, parameters = [], []
        if time_range is not None:
            start, end = time_range
            # Exported timestamps are UTC. Widen by a day to also retain offset
            # timestamps and subsecond boundaries accepted by older imports.
            clauses.append("substr(json_extract(payload, '$.timestamp'), 1, 10) BETWEEN ? AND ?")
            parameters.extend(((start.astimezone(timezone.utc) - timedelta(days=1)).date().isoformat(),
                               (end.astimezone(timezone.utc) + timedelta(days=1)).date().isoformat()))
        for key, field in (("agent", "agent_id"), ("project", "project"), ("model", "model")):
            values = (query or {}).get(key, [])
            if values:
                clauses.append(f"json_extract(payload, '$.{field}') IN ({','.join('?' for _ in values)})")
                parameters.extend(values)
        if (query or {}).get("cost_status") == ["unknown"]:
            clauses.append("json_extract(payload, '$.cost_usd') IS NULL")
        if session_id is not None:
            index = SessionIndex(sessions)
            keys = [key for key in index.metadata if key[1] == session_id and (not session_agent or key[0] == session_agent)]
            members = set().union(*(index.members(key) for key in keys)) if keys else set()
            ids = sorted({key[1] for key in members} | {session_id})
            clauses.append("json_extract(payload, '$.session_id') IN (SELECT value FROM json_each(?))")
            parameters.append(json.dumps(ids))
            if session_agent:
                clauses.append("json_extract(payload, '$.agent_id') = ?")
                parameters.append(session_agent)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        # Sort only row identifiers for indexed selections. Sorting complete
        # payloads spills large JSON records into SQLite's temporary B-tree.
        # The outer primary-key lookup retains the original position order.
        selection = (" WHERE position IN (SELECT position FROM statistics_usage" + where + ")") if clauses else ""
        columns = ("position, payload, json_extract(payload, '$.agent_id', '$.session_id')"
                   if identities else "payload")
        yield meta, sessions, conn.execute(
            "SELECT " + columns + " FROM statistics_usage" + selection + " ORDER BY position", parameters)


def decode_usage_payloads(payloads, version):
    """Fully validate selected payloads, retaining only this read's metadata memo."""
    previous_metadata = None

    def validate_metadata(row):
        nonlocal previous_metadata
        # JSON metadata contains only strings, null, lists and dictionaries
        # after validation. Reuse only an identical successful validation,
        # including the amount's nullness; scalar fields are still checked
        # for every row. Nothing is retained across reads or generations.
        metadata = (row.get("usage_quality", "unknown"), row.get("service_context"),
                    row.get("cost_provenance"), row["cost_usd"] is None)
        if metadata != previous_metadata:
            validate_usage_metadata(row)
            previous_metadata = metadata

    return [_usage_entry(json.loads(payload), version=version, metadata_validator=validate_metadata)
            for payload in payloads]


@diagnostics.measured("statistics.read_usage")
def read_admitted_usage(current, *, time_range=None, query=None, session_id=None, session_agent=None):
    """Decode only usage needed by Sessions; callers retain exact time filtering."""
    from aggregators import LoadedEntries
    with admitted_usage_rows(current, time_range=time_range, query=query,
                             session_id=session_id, session_agent=session_agent) as (meta, sessions, rows):
        if meta is None:
            return []
        return LoadedEntries(decode_usage_payloads((row[0] for row in rows), meta["version"]),
                             session_metadata=sessions)
