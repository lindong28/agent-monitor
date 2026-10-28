"""Transactional derived usage and buckets over the retained raw archive.

Raw observations remain authoritative. The archive's dirty-key triggers also
cover writes by older application versions; consumption commits with projection.
"""
import hashlib
import json
from dataclasses import replace
from pathlib import Path

from agent_monitor.contracts.usage import LoadedEntries
from agent_monitor.ingestion.usage_archive import _decode, _encode
from agent_monitor.ingestion import usage_models
from . import pricing_fetcher, usage_buckets


class PreparedRollup:
    """Complete bucket snapshot, deliberately not an empty event collection."""

    def __init__(self, project_buckets, usage_buckets, metadata):
        self.project_buckets = project_buckets
        self.usage_buckets = usage_buckets
        self.__dict__.update(metadata)


def _signature():
    digest = hashlib.sha256()
    import token_cost
    from agent_monitor.contracts import usage, calendar
    for module in (pricing_fetcher, usage_buckets, token_cost, usage, calendar):
        digest.update(Path(module.__file__).read_bytes())
    digest.update(Path(__file__).read_bytes())
    return digest.hexdigest()


def _initialize(conn):
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name IN ('prepared_usage', 'prepared_buckets', 'usage_model_counts')")}
    conn.execute("CREATE TABLE IF NOT EXISTS prepared_usage (dedup_key TEXT PRIMARY KEY, entry TEXT NOT NULL, attributed INTEGER NOT NULL)")
    conn.execute("CREATE TABLE IF NOT EXISTS prepared_buckets (kind TEXT NOT NULL, bucket_key TEXT NOT NULL, payload TEXT NOT NULL, cost_units TEXT NOT NULL, PRIMARY KEY(kind, bucket_key))")
    conn.execute("CREATE INDEX IF NOT EXISTS usage_events_project ON usage_events(json_quote(json_extract(entry, '$.project')))")
    usage_models.initialize(conn)
    return len(tables) != 3


def _price_dependencies(conn, pricing):
    result = {}
    for (model,) in conn.execute("SELECT model FROM usage_model_counts WHERE event_count > 0"):
        key = pricing_fetcher.resolve_model_key(json.loads(model), pricing)
        # Member order can affect equal numeric threshold spellings in token_cost.
        # Keep the complete record, including nested mode/geo/provenance metadata.
        value = [key, pricing.get(key), getattr(pricing, "sources", {}).get(key, "configured")]
        result[model] = hashlib.sha256(json.dumps(value).encode()).hexdigest()
    return result


_COUNTERS = ("input_tokens", "output_tokens", "cache_creation_tokens",
             "cache_read_tokens", "message_count")


def _compact(raw, entry):
    """Old contribution survives raw correction/deletion; overlay serves detail."""
    before, after = _encode(raw), _encode(entry)
    return {
        "b": [usage_buckets._bucket_date(entry.timestamp).isoformat(),
              entry.agent_id or "unknown", entry.model or "unknown",
              entry.project or "unknown"],
        "v": [getattr(entry, field) for field in _COUNTERS] + [entry.cost_usd],
        "o": {key: value for key, value in after.items() if value != before[key]},
    }


def _update_bucket(conn, kind, key, values, sign):
    key = json.dumps(key)
    row = conn.execute("SELECT payload, cost_units FROM prepared_buckets WHERE kind=? AND bucket_key=?",
                       (kind, key)).fetchone()
    bucket, units = (json.loads(row[0]), int(row[1])) if row else (usage_buckets._empty_bucket(), 0)
    cost = values[-1]
    contribution = dict(zip(_COUNTERS, values[:-1]))
    contribution.update(entry_count=1, cost_known_count=int(cost is not None))
    for field in bucket:
        if field != "cost_usd":
            bucket[field] += sign * contribution[field]
    if cost is not None:
        units += sign * usage_buckets._cost_units(cost)
    if bucket["entry_count"] == 0:
        conn.execute("DELETE FROM prepared_buckets WHERE kind=? AND bucket_key=?", (kind, key))
        return
    bucket["cost_usd"] = units / (1 << 1074)
    conn.execute("INSERT INTO prepared_buckets VALUES (?, ?, ?, ?) ON CONFLICT(kind, bucket_key) DO UPDATE SET payload=excluded.payload, cost_units=excluded.cost_units",
                 (kind, key, json.dumps(bucket), str(units)))


def _contribute(conn, contribution, attributed, sign):
    day, agent, model, project = contribution["b"]
    _update_bucket(conn, "usage", (day, agent, model), contribution["v"], sign)
    if attributed:
        _update_bucket(conn, "project", (day, agent, project, model), contribution["v"], sign)


def prepare_usage(conn, pricing, context, source_errors, *, materialize):
    """Called inside archive collection's transaction and identity/rollup lock."""
    missing_tables = _initialize(conn)
    signature = _signature()
    dependencies = _price_dependencies(conn, pricing)
    previous_prices = conn.execute("SELECT value FROM archive_meta WHERE key='usage_projection_prices'").fetchone()
    previous = conn.execute("SELECT value FROM archive_meta WHERE key='usage_projection_signature'").fetchone()
    previous_context = conn.execute("SELECT value FROM archive_meta WHERE key='usage_projection_context'").fetchone()
    context_value = json.dumps([context["projects"], sorted(context["blocked_paths"])], sort_keys=True)
    rebuild = missing_tables or previous is None or previous[0] != signature or previous_context is None or previous_prices is None
    if rebuild:
        conn.execute("DELETE FROM prepared_usage")
        conn.execute("DELETE FROM prepared_buckets")
        rows = conn.execute("SELECT dedup_key, entry FROM usage_events ORDER BY dedup_key")
    else:
        old_prices = json.loads(previous_prices[0])
        for model, dependency in dependencies.items():
            if old_prices.get(model) != dependency:
                conn.execute("INSERT INTO archive_dirty SELECT dedup_key FROM usage_events WHERE json_quote(json_extract(entry, '$.model'))=? ON CONFLICT(dedup_key) DO NOTHING", (model,))
        if previous_context[0] != context_value:
            old_projects, old_blocked = json.loads(previous_context[0])
            changed = set(old_blocked) ^ context["blocked_paths"]
            changed.update(path for path in old_projects.keys() | context["projects"].keys()
                           if old_projects.get(path, path) != context["projects"].get(path, path))
            for path in changed:
                conn.execute("INSERT INTO archive_dirty SELECT dedup_key FROM usage_events WHERE json_quote(json_extract(entry, '$.project'))=? ON CONFLICT(dedup_key) DO NOTHING", (json.dumps(path, ensure_ascii=False),))
        rows = conn.execute("SELECT dirty.dedup_key, raw.entry FROM archive_dirty AS dirty LEFT JOIN usage_events AS raw ON raw.dedup_key=dirty.dedup_key ORDER BY dirty.dedup_key")
    for key, payload in rows:
        old = conn.execute("SELECT entry, attributed FROM prepared_usage WHERE dedup_key=?", (key,)).fetchone()
        if old:
            _contribute(conn, json.loads(old[0]), bool(old[1]), -1)
        if payload is None:
            conn.execute("DELETE FROM prepared_usage WHERE dedup_key=?", (key,))
            continue
        raw = _decode(json.loads(payload))
        entry = pricing_fetcher.with_calculated_cost(raw, pricing=pricing)
        attributed = raw.project not in context["blocked_paths"]
        if attributed:
            entry = replace(entry, project=context["projects"].get(raw.project, raw.project))
        contribution = _compact(raw, entry)
        _contribute(conn, contribution, attributed, 1)
        conn.execute("INSERT INTO prepared_usage VALUES (?, ?, ?) ON CONFLICT(dedup_key) DO UPDATE SET entry=excluded.entry, attributed=excluded.attributed",
                     (key, json.dumps(contribution, separators=(",", ":")), int(attributed)))
    if conn.execute("SELECT 1 FROM archive_dirty LIMIT 1").fetchone():
        conn.execute("DELETE FROM archive_dirty")
    conn.execute("INSERT INTO archive_meta VALUES ('usage_projection_signature', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value WHERE value != excluded.value", (signature,))
    conn.execute("INSERT INTO archive_meta VALUES ('usage_projection_context', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value WHERE value != excluded.value", (context_value,))
    conn.execute("INSERT INTO archive_meta VALUES ('usage_projection_prices', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value WHERE value != excluded.value", (json.dumps(dependencies, sort_keys=True),))
    metadata = dict(context["metadata"], source_errors=list(source_errors), scan_complete=not source_errors)
    if materialize:
        entries, unattributed = [], []
        for raw, prepared, attributed in conn.execute(
                "SELECT raw.entry, prepared.entry, prepared.attributed FROM prepared_usage AS prepared "
                "JOIN usage_events AS raw USING(dedup_key) ORDER BY prepared.dedup_key"):
            payload = json.loads(raw)
            payload.update(json.loads(prepared)["o"])
            (entries if attributed else unattributed).append(_decode(payload))
        entries.sort(key=lambda entry: entry.timestamp)
        unattributed.sort(key=lambda entry: entry.timestamp)
        return LoadedEntries(entries, unattributed_entries=unattributed, **metadata)
    buckets = {"project": {}, "usage": {}}
    for kind, key, payload in conn.execute("SELECT kind, bucket_key, payload FROM prepared_buckets"):
        buckets[kind][tuple(json.loads(key))] = json.loads(payload)
    return PreparedRollup(buckets["project"], buckets["usage"], metadata)
