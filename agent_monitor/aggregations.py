"""In-memory aggregation over already loaded usage entries."""
from collections import OrderedDict, defaultdict
from datetime import datetime, timedelta
from agent_monitor.contracts.calendar import BUCKET_TIMEZONE as USAGE_TIMEZONE

TIME_DIMS = {"day", "week", "month"}
ALL_DIMS = TIME_DIMS | {"project", "model", "agent"}
METRICS = {"cost", "input", "output", "cache_read", "cache_creation", "total", "messages"}


def pivot(
    entries,
    x_dim,
    group_dim,
    metric,
    agents=None,
    projects=None,
    models=None,
    time_range=None,
):
    _validate(x_dim, group_dim, metric)
    filtered = [
        entry
        for entry in entries
        if _included(entry, agents=agents, projects=projects, models=models, time_range=time_range)
    ]

    columns = ["value"] if group_dim == "none" else _ordered_unique(extract_dim(entry, group_dim) for entry in filtered)
    buckets = defaultdict(lambda: {"value": 0, "known": 0})
    row_keys = OrderedDict()

    for entry in filtered:
        x_value = extract_dim(entry, x_dim)
        column = "value" if group_dim == "none" else extract_dim(entry, group_dim)
        row_keys.setdefault(x_value, None)
        metric_value = extract_metric(entry, metric)
        bucket = buckets[(x_value, column)]
        if metric == "cost":
            if metric_value is None:
                continue
            bucket["known"] += 1
        bucket["value"] += metric_value or 0

    rows = []
    for x_value in row_keys:
        values = {}
        for column in columns:
            bucket = buckets.get((x_value, column))
            if metric == "cost" and (not bucket or bucket["known"] == 0):
                values[column] = None
            else:
                values[column] = bucket["value"] if bucket else 0
        rows.append({"x": x_value, "values": values})

    if x_dim in TIME_DIMS:
        rows.sort(key=lambda row: row["x"])
    else:
        rows.sort(key=lambda row: _row_total(row), reverse=True)

    return {"columns": columns, "rows": rows}


def extract_dim(entry, dim):
    if dim == "day":
        return entry.timestamp.astimezone(USAGE_TIMEZONE).date().isoformat()
    if dim == "week":
        local = entry.timestamp.astimezone(USAGE_TIMEZONE)
        monday = local.date() - timedelta(days=local.weekday())
        return monday.isoformat()
    if dim == "month":
        return entry.timestamp.astimezone(USAGE_TIMEZONE).strftime("%Y-%m")
    if dim == "project":
        return entry.project
    if dim == "model":
        return entry.model
    if dim == "agent":
        return entry.agent_id
    raise ValueError("Unsupported dimension: %s" % dim)


def extract_metric(entry, metric):
    if metric == "cost":
        return entry.cost_usd
    if metric == "input":
        return entry.input_tokens
    if metric == "output":
        return entry.output_tokens
    if metric == "cache_read":
        return entry.cache_read_tokens
    if metric == "cache_creation":
        return entry.cache_creation_tokens
    if metric == "total":
        return (
            entry.input_tokens
            + entry.output_tokens
            + entry.cache_read_tokens
            + entry.cache_creation_tokens
        )
    if metric == "messages":
        return entry.message_count
    raise ValueError("Unsupported metric: %s" % metric)


def _included(entry, agents=None, projects=None, models=None, time_range=None):
    if agents is not None and entry.agent_id not in agents:
        return False
    if projects is not None and entry.project not in projects:
        return False
    if models is not None and entry.model not in models:
        return False
    if time_range is not None:
        start, end = time_range
        if entry.timestamp < start or entry.timestamp >= end:
            return False
    return True


def _ordered_unique(values):
    seen = OrderedDict()
    for value in values:
        seen.setdefault(value, None)
    return sorted(seen.keys())


def _row_total(row):
    total = 0
    for value in row["values"].values():
        total += value or 0
    return total


def _validate(x_dim, group_dim, metric):
    if x_dim not in ALL_DIMS:
        raise ValueError("Unsupported x_dim: %s" % x_dim)
    if group_dim != "none" and group_dim not in ALL_DIMS:
        raise ValueError("Unsupported group_dim: %s" % group_dim)
    if metric not in METRICS:
        raise ValueError("Unsupported metric: %s" % metric)
