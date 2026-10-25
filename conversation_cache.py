"""Reuse fully validated conversation results by their complete input content."""
from collections import OrderedDict
from datetime import timezone
import hashlib
import json
import threading
import diagnostics

from session_identity import SessionIndex
import statistics_snapshot


class ConversationCache:
    def __init__(self, *, max_entries=8192, max_bytes=16 * 1024 * 1024):
        self.max_entries = max_entries
        self.max_bytes = max_bytes
        self._items = OrderedDict()
        self._bytes = 0
        self._lock = threading.Lock()

    def _get(self, key, window):
        with self._lock:
            cached = self._items.get(key)
            if cached is None:
                return None
            end, payload = cached
            if end is not None and window[1] < end:
                return None
            self._items.move_to_end(key)
        return json.loads(payload)

    def _put(self, key, end, result):
        payload = json.dumps(result, ensure_ascii=True, separators=(",", ":"))
        size = len(key) + len(payload)
        if size > self.max_bytes:
            return
        with self._lock:
            old = self._items.pop(key, None)
            if old is not None:
                self._bytes -= len(key) + len(old[1])
            self._items[key] = (end, payload)
            self._bytes += size
            while len(self._items) > self.max_entries or self._bytes > self.max_bytes:
                removed_key, (_, removed) = self._items.popitem(last=False)
                self._bytes -= len(removed_key) + len(removed)

    @diagnostics.measured("statistics.read_usage")
    def read(self, current, query, window, collect):
        """Return (source result, safe for source caching), or request fallback.

        collect receives fully validated events and the current complete ancestry.
        Its third result is the first visible event's ordinal within this bucket.
        """
        moving = window is not None and (query.get("range") or ["30d"])[0] != "custom"
        bounds = None if window is None else (window[0].isoformat(),
            window[1].astimezone(timezone.utc).date().isoformat() if moving else window[1].isoformat())
        dimensions = {k: v for k, v in query.items() if k not in {"sync", "force", "sort", "order"}}
        reader_query = None if (query.get("with_filters") or [""])[0] == "1" else {
            k: v for k, v in query.items() if k != "project"}
        with statistics_snapshot.admitted_usage_rows(current, time_range=window,
                query=reader_query, identities=True) as (meta, sessions, rows):
            if meta is None:
                return ([], {key: [] for key in ("machine", "agent", "project", "model")}), True
            index = SessionIndex(sessions)
            identities, buckets = {}, {}
            for position, payload, identity_json in rows:
                identity = identities.get(identity_json)
                if identity is None:
                    values = json.loads(identity_json)
                    if not all(isinstance(value, str) for value in values):
                        return None
                    identity = identities[identity_json] = tuple(values)
                root = index.root(identity)
                if root not in buckets:
                    buckets[root] = (hashlib.sha256(), [])
                fingerprint, records = buckets[root]
                raw = payload.encode("utf-8")
                fingerprint.update(len(raw).to_bytes(8, "big"))
                fingerprint.update(raw)
                records.append((position, payload, identity))

            source_rows, options, projects = [], {}, {}
            reusable = True
            for root, (fingerprint, records) in buckets.items():
                context = (current.host, meta["version"], root, index.metadata.get(root),
                    index.status(root), current.meta.get("aliases"), bounds, dimensions, fingerprint.hexdigest())
                key = hashlib.sha256(json.dumps(context, sort_keys=True, ensure_ascii=True).encode()).hexdigest()
                result = self._get(key, window)
                if result is None:
                    entries = statistics_snapshot.decode_usage_payloads((r[1] for r in records), meta["version"])
                    # Exact identities are necessary: two duplicate-key identities
                    # can share a root today, then diverge in a later generation.
                    if any((entry.agent_id, entry.session_id) != record[2]
                           for entry, record in zip(entries, records)):
                        return None
                    result = collect(entries, index, projects)
                    safe = not moving or all(entry.timestamp <= window[1] for entry in entries)
                    reusable = reusable and safe
                    if safe:
                        self._put(key, window[1] if moving else None, result)
                bucket_rows, filters, first = result
                if bucket_rows:
                    source_rows.extend((records[first][0], row) for row in bucket_rows)
                for name, values in filters.items():
                    options.setdefault(name, set()).update(values)
            # A group's first candidate may be outside the visible filters. Use
            # its first *visible* ordinal at this generation's current positions.
            source_rows.sort(key=lambda item: item[0])
            return ([row for _, row in source_rows],
                    {key: sorted(values) for key, values in options.items()}), reusable
