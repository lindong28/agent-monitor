"""Versioned statistics carried inside the admitted, digest-bound snapshot.db.

Gateway rows are captured in one read transaction, never copied from the live
SQLite file. Only exact v3-v10 gateway audit schemas are accepted (no call content).
"""

from contextlib import closing, contextmanager
from dataclasses import asdict, fields
from datetime import date, datetime, timedelta, timezone
import json
import math
from pathlib import Path
import sqlite3

from parsers import UsageEntry
import llm_attempts


VERSION = 1
TABLES = {"statistics_meta", "statistics_usage", "statistics_requests", "statistics_attempts"}
META_FIELDS = {"version", "observed_at", "history_authoritative_from", "scan_complete",
               "blocked_source_count", "source_error_count", "gateway"}
GATEWAY_FIELDS = {"state", "schema_version", "observed_at", "request_high", "attempt_high"}
# Versioned columns: unknown columns cannot travel as arbitrary JSON payload.
REQUEST_FIELDS = frozenset("id request_timestamp canonical_project_id logical_request_id session_ref logical_model requested_capabilities_json admission_revision requested_route_id route_selection_source preselection_candidates_json request_outcome request_reject_reason active_attempt_id".split())
REQUEST_FIELDS_V5 = REQUEST_FIELDS | {"requested_mode", "resolved_route_id"}
# Ledger schema 6 (llm-gateway c087c1c) stamps the calling OS user on each request.
REQUEST_FIELDS_V6 = REQUEST_FIELDS_V5 | {"caller_username"}
# Schema 7 adds resource tables; its request/attempt projections remain v6.
REQUEST_FIELDS_V8 = REQUEST_FIELDS_V6 | {"caller_route_constraint_json"}
REQUEST_FIELDS_BY_SCHEMA = {5: REQUEST_FIELDS_V5, 6: REQUEST_FIELDS_V6,
                            7: REQUEST_FIELDS_V6, 8: REQUEST_FIELDS_V8,
                            9: REQUEST_FIELDS_V8, 10: REQUEST_FIELDS_V8}
ATTEMPT_FIELDS = frozenset("id attempt_id logical_request_fk attempt_no parent_attempt_id run_id attempt_timestamp routing_revision route_selection_source route_id actual_model provider_id credential_profile_id credential_source_json transport route_verified_capabilities_json generation_controls_json dispatch_boundary outcome latency_ms usage_state usage_json cost_state cost_value cost_basis cost_currency cost_provenance pricing_state pricing_value pricing_currency pricing_basis pricing_source pricing_checked_at pricing_effective_at pricing_valid_until error_class http_status".split())
USAGE_FIELDS = {field.name for field in fields(UsageEntry)}
ATTEMPT_FIELDS_BY_SCHEMA = {10: ATTEMPT_FIELDS | {"usage_missing_reason"}}
SUPPORTED_GATEWAY_SCHEMAS = frozenset(range(3, 11))


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
    if llm_attempts._parse_timestamp(candidate["observed_at"]) < llm_attempts._parse_timestamp(previous["observed_at"]):
        raise StatisticsSnapshotError("statistics observation moved backwards; last successful data retained")
    before_usage = {entry.dedup_key for entry in previous["usage_entries"]}
    after_usage = {entry.dedup_key for entry in candidate["usage_entries"]}
    if not before_usage <= after_usage:
        raise StatisticsSnapshotError("source usage history shrank; restore its archive before retrying")
    before, after = previous["gateway"], candidate["gateway"]
    if before["state"] == "available":
        if after["state"] != "available":
            raise StatisticsSnapshotError("source gateway ledger disappeared; last successful data retained")
        requests = lambda data: {(row["canonical_project_id"], row["logical_request_id"]) for row in data["requests"]}
        attempts = lambda data: {row["attempt_id"] for row in data["attempts"]}
        if not requests(before) <= requests(after) or not attempts(before) <= attempts(after):
            raise StatisticsSnapshotError("source gateway history shrank; restore its ledger before retrying")


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
        return {"state": "available", **meta}


def _timestamp(value=None):
    value = datetime.now(timezone.utc) if value is None else value
    if isinstance(value, str):
        value = llm_attempts._parse_timestamp(value)
    llm_attempts._aware(value)
    return value.astimezone(timezone.utc).isoformat()


def _gateway_snapshot(path, observed_at):
    path = Path(path)
    meta = {"state": "missing", "schema_version": None, "observed_at": observed_at,
            "request_high": None, "attempt_high": None}
    if not path.exists() and not path.is_symlink():
        return meta, [], []
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=10)) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("BEGIN")
        schema_version = llm_attempts._validate_schema(conn)
        requests = [dict(row) for row in conn.execute("SELECT * FROM logical_requests")]
        attempts = [dict(row) for row in conn.execute("SELECT * FROM attempts")]
        meta.update(state="available", schema_version=schema_version,
                    request_high=max((row["id"] for row in requests), default=None),
                    attempt_high=max((row["id"] for row in attempts), default=None))
        _validate_gateway(meta, requests, attempts)
    return meta, requests, attempts


def write_snapshot(snapshot_path, loaded_entries, *, ledger_path=None, observed_at=None):
    """Write to an unpublished snapshot copy; the caller owns publish/fsync/digest."""
    observed_at = _timestamp(observed_at)
    gateway, requests, attempts = _gateway_snapshot(
        llm_attempts.DEFAULT_LEDGER_PATH if ledger_path is None else ledger_path, observed_at
    )
    meta = {"version": VERSION, "observed_at": observed_at,
            "history_authoritative_from": getattr(loaded_entries, "history_authoritative_from", None),
            "scan_complete": bool(getattr(loaded_entries, "scan_complete", True)),
            "blocked_source_count": len(getattr(loaded_entries, "blocked_sources", ())),
            "source_error_count": len(getattr(loaded_entries, "source_errors", ())),
            "gateway": gateway}
    usage = []
    for entry in list(loaded_entries) + list(getattr(loaded_entries, "unattributed_entries", ())):
        row = asdict(entry)
        row["timestamp"] = _timestamp(entry.timestamp)
        _usage_entry(row)
        usage.append(row)
    _validate_meta(meta)
    with closing(sqlite3.connect(str(snapshot_path))) as conn, conn:
        for name in sorted(TABLES):
            conn.execute(f"CREATE TABLE IF NOT EXISTS {name} (position INTEGER PRIMARY KEY, payload TEXT NOT NULL)")
            conn.execute(f"DELETE FROM {name}")
        for name, rows in (("statistics_meta", [meta]), ("statistics_usage", usage),
                           ("statistics_requests", requests), ("statistics_attempts", attempts)):
            conn.executemany(f"INSERT INTO {name} VALUES (?, ?)",
                             [(index, json.dumps(row, allow_nan=False, sort_keys=True))
                              for index, row in enumerate(rows)])
        _create_detail_indexes(conn)
    return read_snapshot(snapshot_path)


def _create_detail_indexes(conn):
    """Build lookup indexes once at export, before the snapshot digest is bound.

    The versioned tables/payloads stay unchanged; older readers ignore these
    optional indexes and older snapshots still support scan-based lookup.
    """
    for table, field in (("requests", "logical_request_id"), ("requests", "id"),
                         ("attempts", "logical_request_fk"), ("attempts", "attempt_id")):
        conn.execute("CREATE INDEX IF NOT EXISTS am_detail_%s_%s ON statistics_%s "
                     "(json_extract(payload, '$.%s'))" % (table, field, table, field))


def _validate_meta(meta):
    if not isinstance(meta, dict) or set(meta) != META_FIELDS or type(meta["version"]) is not int or meta["version"] != VERSION:
        raise ValueError("unsupported statistics extension")
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


def _usage_entry(row):
    if not isinstance(row, dict) or set(row) != USAGE_FIELDS:
        raise ValueError("invalid usage entry fields")
    row = dict(row)
    row["timestamp"] = llm_attempts._parse_timestamp(row["timestamp"])
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
    return UsageEntry(**row)


def _validate_gateway(meta, requests, attempts):
    if not isinstance(meta, dict) or set(meta) != GATEWAY_FIELDS:
        raise ValueError("invalid gateway metadata")
    if not isinstance(meta["observed_at"], str):
        raise ValueError("invalid gateway observation time")
    _timestamp(meta["observed_at"])
    if meta["state"] == "missing":
        if requests or attempts or any(meta[key] is not None for key in ("schema_version", "request_high", "attempt_high")):
            raise ValueError("missing gateway carries rows")
        return
    if (meta["state"] != "available" or type(meta["schema_version"]) is not int
            or meta["schema_version"] not in SUPPORTED_GATEWAY_SCHEMAS):
        raise ValueError("unsupported gateway snapshot")
    request_fields = REQUEST_FIELDS_BY_SCHEMA.get(meta["schema_version"], REQUEST_FIELDS)
    attempt_fields = ATTEMPT_FIELDS_BY_SCHEMA.get(meta["schema_version"], ATTEMPT_FIELDS)
    for rows, expected, high in ((requests, request_fields, "request_high"), (attempts, attempt_fields, "attempt_high")):
        if meta[high] is not None and (type(meta[high]) is not int or meta[high] < 1):
            raise ValueError("invalid gateway high watermark")
        if any(not isinstance(row, dict) or set(row) != expected for row in rows):
            raise ValueError("invalid gateway row fields")
        ids = [row["id"] for row in rows]
        if len(set(ids)) != len(ids) or max(ids, default=None) != meta[high]:
            raise ValueError("invalid gateway high watermark")
    if len({(row["canonical_project_id"], row["logical_request_id"]) for row in requests}) != len(requests):
        raise ValueError("duplicate gateway request")
    if len({row["attempt_id"] for row in attempts}) != len(attempts):
        raise ValueError("duplicate gateway attempt")
    llm_attempts._validate_observation([llm_attempts._request_row(row) for row in requests],
                                      [llm_attempts._attempt_row(row, meta["schema_version"]) for row in attempts])


def read_snapshot(snapshot_path):
    """Validate and return statistics; legacy snapshots explicitly lack coverage."""
    try:
        with closing(sqlite3.connect(Path(snapshot_path).resolve().as_uri() + "?mode=ro", uri=True)) as conn:
            conn.execute("BEGIN")
            objects = conn.execute("SELECT name, type FROM sqlite_master WHERE name LIKE 'statistics_%'").fetchall()
            if not objects:
                return {"state": "not_collected", "observed_at": None, "usage_entries": [],
                        "history_authoritative_from": None, "gateway": {"state": "not_collected", "observed_at": None}}
            if {name for name, _ in objects} != TABLES or any(kind != "table" for _, kind in objects):
                raise ValueError("incomplete statistics extension")
            rows = {}
            for name in sorted(TABLES):
                columns = conn.execute(f"PRAGMA table_info({name})").fetchall()
                if [(row[1], row[2], row[3], row[5]) for row in columns] != [("position", "INTEGER", 0, 1), ("payload", "TEXT", 1, 0)]:
                    raise ValueError("unsupported statistics table")
                rows[name] = [json.loads(row[0]) for row in conn.execute(f"SELECT payload FROM {name} ORDER BY position")]
            if len(rows["statistics_meta"]) != 1:
                raise ValueError("invalid statistics metadata")
            meta = rows["statistics_meta"][0]
            _validate_meta(meta)
            requests, attempts = rows["statistics_requests"], rows["statistics_attempts"]
            _validate_gateway(meta["gateway"], requests, attempts)
            return {**meta, "state": "available",
                    "usage_entries": [_usage_entry(row) for row in rows["statistics_usage"]],
                    "gateway": {**meta["gateway"], "requests": requests, "attempts": attempts}}
    except (sqlite3.Error, ValueError, TypeError, KeyError, llm_attempts.ProjectionError) as exc:
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
    except (sqlite3.Error, ValueError, TypeError, KeyError, llm_attempts.ProjectionError) as exc:
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


def read_admitted_usage(current, *, time_range=None, query=None, session_id=None):
    """Decode only usage needed by Sessions; callers retain exact time filtering."""
    with _admitted_connection(current) as conn:
        if _admitted_meta(conn) is None:
            return []
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
        if session_id is not None:
            clauses.append("json_extract(payload, '$.session_id') = ?")
            parameters.append(session_id)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        return [_usage_entry(json.loads(row[0])) for row in conn.execute(
            "SELECT payload FROM statistics_usage" + where + " ORDER BY position", parameters)]


def read_admitted_gateway(current, *, request_query=None):
    """Read history, or one detail's complete chain from a leased generation.

    The audit reader validates the detail query before invoking this callback.
    Metadata for every source is retained even when its rows are not selected.
    """
    with _admitted_connection(current) as conn:
        meta = _admitted_meta(conn)
        if meta is None:
            return {"gateway": {"state": "not_collected", "observed_at": None}}
        gateway = dict(meta["gateway"])
        if request_query is not None:
            requests, attempts = _gateway_detail_rows(conn, current.host, request_query)
            return {"gateway": {**gateway, "requests": requests, "attempts": attempts}}
        for domain in ("requests", "attempts"):
            gateway[domain] = [json.loads(row[0]) for row in conn.execute(
                f"SELECT payload FROM statistics_{domain} ORDER BY position")]
        return {"gateway": gateway}


def _gateway_detail_rows(conn, machine, query):
    if query.get("machine") != [machine]:
        return [], []
    if "attempt_id" in query:
        where = "json_extract(payload, '$.id') IN (SELECT json_extract(payload, '$.logical_request_fk') FROM statistics_attempts WHERE json_extract(payload, '$.attempt_id') = ?)"
        parameters = [query["attempt_id"][0]]
    else:
        where = "json_extract(payload, '$.logical_request_id') = ?"
        parameters = [query["logical_request_id"][0]]
        if "project" in query:
            where += " AND json_extract(payload, '$.canonical_project_id') = ?"
            parameters.append(query["project"][0])
    requests = [json.loads(row[0]) for row in conn.execute(
        "SELECT payload FROM statistics_requests WHERE " + where + " ORDER BY position", parameters)]
    if len(requests) != 1:
        # The shared reader reports absent/ambiguous identity before using children.
        return requests, []
    ids = [row["id"] for row in requests]
    attempts = [json.loads(row[0]) for row in conn.execute(
        "SELECT payload FROM statistics_attempts WHERE json_extract(payload, '$.logical_request_fk') IN ("
        + ",".join("?" for _ in ids) + ") ORDER BY position", ids)]
    return requests, attempts
