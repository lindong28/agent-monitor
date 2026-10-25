"""Bounded reuse of fully computed Sessions results from admitted generations."""
import json
import threading
from collections import OrderedDict
from datetime import timezone


class SessionCache:
    def __init__(self, *, max_entries=8, max_bytes=16 * 1024 * 1024):
        self.max_entries = max_entries
        self.max_bytes = max_bytes
        self._items = OrderedDict()
        self._bytes = 0
        self._lock = threading.Lock()

    def _key(self, current, query, window):
        signature = getattr(current, "_signature", None)
        lease = getattr(current, "_lease", None)
        if not signature or lease is None or lease.closed:
            return None
        moving = window is not None and (query.get("range") or ["30d"])[0] != "custom"
        bounds = None if window is None else (window[0],
            window[1].astimezone(timezone.utc).date() if moving else window[1])
        dimensions = tuple(sorted((key, tuple(values)) for key, values in query.items()
                                  if key not in {"sync", "force", "sort", "order"}))
        return (current.host, str(current.db_path), signature, bounds, dimensions)

    def get(self, current, query, window):
        key = self._key(current, query, window)
        if key is None:
            return None
        with self._lock:
            cached = self._items.get(key)
            if cached is None:
                return None
            end, payload = cached
            if end is not None and window[1] < end:
                return None
            self._items.move_to_end(key)
        # Each caller owns its response, including nested lists and mappings.
        return json.loads(payload)

    def put(self, current, query, window, entries, result):
        key = self._key(current, query, window)
        if key is None:
            return
        moving = window is not None and (query.get("range") or ["30d"])[0] != "custom"
        end = window[1] if moving else None
        # Check the reader's candidates, before exact time/dimension filtering.
        # On the same UTC end date its widened SQL candidate range is unchanged.
        if moving and any(entry.timestamp > end for entry in entries):
            return
        payload = json.dumps(result, ensure_ascii=True, separators=(",", ":"))
        if len(payload) > self.max_bytes:
            return
        with self._lock:
            previous = self._items.pop(key, None)
            if previous is not None:
                self._bytes -= len(previous[1])
            self._items[key] = (end, payload)
            self._bytes += len(payload)
            while len(self._items) > self.max_entries or self._bytes > self.max_bytes:
                _, (_, removed) = self._items.popitem(last=False)
                self._bytes -= len(removed)
