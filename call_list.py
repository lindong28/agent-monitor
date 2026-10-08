"""Request-page reads over immutable admitted snapshots; analysis stays on demand."""

from contextlib import ExitStack
from datetime import datetime, timezone
from itertools import groupby
import json

import llm_attempts as audit
import statistics_snapshot as snapshots


SELECTORS = ("machine", "project", "session_ref", "logical_model", "request_outcome",
             "provider", "account_profile", "route", "actual_model", "attempt_outcome",
             "cost_basis", "credential_source_kind", "credential_source_ref")
REQUEST_TIME = "julianday(json_extract(payload, '$.request_timestamp'))"
ATTEMPT_TIME = "julianday(json_extract(payload, '$.attempt_timestamp'))"


def _window_sql(expression, observation):
    # Inclusive coarse bounds; Python below applies the exact half-open window.
    clauses, args = [], []
    for op, key in ((">=", "start_at"), ("<=", "end_at")):
        if observation[key] is not None:
            clauses.append(expression + " " + op + " julianday(?)")
            args.append(observation[key].isoformat())
    return "(" + " AND ".join(clauses or ["1"]) + ")", args


def _inside(raw, domain, observation):
    return audit._in_window(audit._parse_timestamp(raw[domain + "_timestamp"]), observation)


def _children(conn, rows):
    result = []
    ids = [row["id"] for row in rows]
    for start in range(0, len(ids), 400):
        part = ids[start:start + 400]
        result.extend(json.loads(row[0]) for row in conn.execute(
            "SELECT payload FROM statistics_attempts WHERE json_extract(payload, '$.logical_request_fk') IN ("
            + ",".join("?" for _ in part) + ") ORDER BY position", part))
    return result


def _option_exists(conn, machine, name, value, observation):
    request = name in audit.REQUEST_FILTERS
    domain = "request" if request else "attempt"
    if name == "machine" and value != machine:
        return False
    field = (audit.REQUEST_FILTERS if request else audit.ATTEMPT_FILTERS)[name]
    if name == "machine":
        match, args = "1", []
    elif name in ("credential_source_kind", "credential_source_ref"):
        match = "json_extract(json_extract(payload, '$.credential_source_json'), '$.%s') = ?" % field
        args = [value]
    else:
        match, args = "json_extract(payload, '$.%s') = ?" % field, [value]
    expression = REQUEST_TIME if request else ATTEMPT_TIME
    window, times = _window_sql(expression, observation)
    # A request option remains valid when only its child is in this window.
    if request:
        child_window, child_times = _window_sql(ATTEMPT_TIME.replace("payload", "a.payload"), observation)
        window += " OR EXISTS (SELECT 1 FROM statistics_attempts a WHERE json_extract(a.payload, '$.logical_request_fk') = json_extract(r.payload, '$.id') AND (" + child_window + " OR " + ATTEMPT_TIME.replace("payload", "a.payload") + " IS NULL))"
        times += child_times
    sql = "SELECT payload FROM statistics_%ss r WHERE (%s) AND (%s OR %s IS NULL)" % (domain, match, window, expression)
    for (payload,) in conn.execute(sql, args + times):
        raw = json.loads(payload)
        if _inside(raw, domain, observation):
            return True
        if request and any(_inside(child, "attempt", observation) for child in _children(conn, [raw])):
            return True
    return False


def _selected_options(connections, metadata, values, observation):
    options = {"request_dimensions": {}, "attempt_dimensions": {}}
    for name in SELECTORS:
        scope, field = audit._OPTION_FILTER_FIELDS[name]
        retained = []
        for value in values.get(name, []):
            if any(metadata[g.db_path]["gateway"]["state"] == "available"
                   and _option_exists(conn, g.host, name, value, observation)
                   for g, conn in connections):
                retained.append(value)
        options[scope][field] = ([{"id": value, "display_name": None,
                                  "display_name_source": "source_registry_not_collected"}
                                 for value in retained] if name == "account_profile" else retained)
    audit._normalize_option_filters(values, options, SELECTORS)
    return options


def _source_page(current, conn, gateway, values, observation, now):
    empty = {"gateway": {**gateway, "requests": [], "attempts": []}}
    if gateway["state"] != "available" or (values.get("machine") and current.host not in values["machine"]):
        return empty
    clauses, args = [], []
    for name, field in audit.REQUEST_FILTERS.items():
        if name != "machine" and values.get(name):
            clauses.append("json_extract(payload, '$.%s') IN (%s)" % (field, ",".join("?" for _ in values[name])))
            args.extend(values[name])
    window, times = _window_sql(REQUEST_TIME, observation)
    clauses.append(window)
    args += times
    boundary = audit._decode_cursor(values["request_cursor"], "request") if values.get("request_cursor") else None
    if boundary:
        clauses.append(REQUEST_TIME + " <= julianday(?)")
        args.append(boundary[0].astimezone(timezone.utc).isoformat())
    limit = values["page_size"] + 1

    def select(rows):
        children = _children(conn, rows)
        source = {"gateway": {**gateway, "requests": rows, "attempts": children}}
        selected = audit._load_observation(values, now=now, sources=[(current.host, current.db_path)],
                                           snapshot_reader=lambda _: source)
        requests = audit._selected_items(selected, values)[0]
        requests.sort(key=audit._request_sort_key, reverse=True)
        ids = {row["request_ingest_sequence"][1] for row in requests
               if boundary is None or audit._request_sort_key(row) < boundary}
        return [row for row in rows if row["id"] in ids]

    # SQLite accepts fewer timestamp forms than the authoritative Python reader.
    # NULL buckets cannot be discarded. They are usually empty and index-seekable.
    unusual = [json.loads(row[0]) for row in conn.execute(
        "SELECT payload FROM statistics_requests WHERE " + REQUEST_TIME + " IS NULL")]
    chosen = select(unusual) if unusual else []
    normal, pending = [], []
    cursor = conn.execute("SELECT " + REQUEST_TIME + ", payload FROM statistics_requests WHERE "
                          + " AND ".join(clauses) + " ORDER BY " + REQUEST_TIME + " DESC", args)
    # Consume the complete last bucket before sorting at Python precision.
    for _, bucket in groupby(cursor, key=lambda row: row[0]):
        pending.extend(json.loads(row[1]) for row in bucket)
        # Older exporters lack child lookup indexes. Batch complete buckets so
        # they do not scan the attempt table separately for each request.
        if len(pending) < limit:
            continue
        normal.extend(select(pending))
        pending = []
        if len(normal) >= limit:
            break
    if pending:
        normal.extend(select(pending))
    chosen.extend(normal)
    chosen.sort(key=lambda row: (audit._parse_timestamp(row["request_timestamp"]), row["id"]), reverse=True)
    chosen = chosen[:limit]
    return {"gateway": {**gateway, "requests": chosen, "attempts": _children(conn, chosen)}}


def without_analysis(payload):
    """Do not label page-local counts or subtotals as full-range statistics."""
    payload["requests"]["matching_count"] = None
    for key in ("request_summary", "attempt_summary", "cost_summary", "attempts"):
        payload[key] = None
    return payload


def page(query, admitted, *, now=None):
    values = audit._validated_query(query, audit.CALL_PARAMS - {"attempt_cursor"})
    now = now or datetime.now(timezone.utc)
    sources = [(g.host, g.db_path) for g in admitted]
    with ExitStack() as stack:
        connections = [(g, stack.enter_context(snapshots._admitted_connection(g))) for g in admitted]
        metadata = {}
        for g, conn in connections:
            meta = snapshots._admitted_meta(conn)
            gateway = dict(meta["gateway"]) if meta else {"state": "not_collected", "observed_at": None}
            metadata[g.db_path] = {"gateway": {**gateway, "requests": [], "attempts": []}}
        observation = audit._load_observation(values, now=now, sources=sources, snapshot_reader=metadata.__getitem__)
        options = _selected_options(connections, metadata, values, observation)
        pages = {g.db_path: _source_page(g, conn, metadata[g.db_path]["gateway"], values, observation, now)
                 for g, conn in connections}
        observation = audit._load_observation(values, now=now, sources=sources, snapshot_reader=pages.__getitem__)
        return {"selection_options": options, "calls": without_analysis(audit._project_calls(observation, values))}
