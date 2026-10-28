"""Shared event-to-daily-bucket accounting, without storage dependencies."""
from collections import defaultdict
from agent_monitor.contracts.calendar import BUCKET_TIMEZONE


def _cost_units(value):
    # All finite floats are integer multiples of 2**-1074. Use the same
    # summation for full and incremental buckets, without subtraction drift.
    numerator, denominator = float(value).as_integer_ratio()
    return numerator << (1074 - (denominator.bit_length() - 1))


def _bucket_date(value):
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("usage timestamps must be timezone-aware")
    return value.astimezone(BUCKET_TIMEZONE).date()


def _bucket_entries(entries):
    buckets = defaultdict(_empty_bucket)
    costs = defaultdict(int)
    for entry in entries:
        day = _bucket_date(entry.timestamp)
        key = (
            day.isoformat(),
            entry.agent_id or "unknown",
            entry.project or "unknown",
            entry.model or "unknown",
        )
        _add_entry_to_bucket(buckets[key], entry)
        if entry.cost_usd is not None:
            costs[key] += _cost_units(entry.cost_usd)
    for key, units in costs.items():
        buckets[key]["cost_usd"] = units / (1 << 1074)
    return buckets


def _bucket_usage_entries(entries):
    buckets = defaultdict(_empty_bucket)
    costs = defaultdict(int)
    for entry in entries:
        day = _bucket_date(entry.timestamp)
        key = (
            day.isoformat(),
            entry.agent_id or "unknown",
            entry.model or "unknown",
        )
        _add_entry_to_bucket(buckets[key], entry)
        if entry.cost_usd is not None:
            costs[key] += _cost_units(entry.cost_usd)
    for key, units in costs.items():
        buckets[key]["cost_usd"] = units / (1 << 1074)
    return buckets


def _add_entry_to_bucket(bucket, entry):
    bucket["input_tokens"] += entry.input_tokens
    bucket["output_tokens"] += entry.output_tokens
    bucket["cache_creation_tokens"] += entry.cache_creation_tokens
    bucket["cache_read_tokens"] += entry.cache_read_tokens
    if entry.cost_usd is not None:
        bucket["cost_usd"] += entry.cost_usd
        bucket["cost_known_count"] += 1
    bucket["entry_count"] += 1
    bucket["message_count"] += entry.message_count


def _empty_bucket():
    return {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_creation_tokens": 0,
        "cache_read_tokens": 0,
        "cost_usd": 0.0,
        "cost_known_count": 0,
        "entry_count": 0,
        "message_count": 0,
    }


