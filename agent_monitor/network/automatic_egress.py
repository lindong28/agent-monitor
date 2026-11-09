"""Lazy, machine-local admission for one automatic model-service quota batch."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import os
import math
import threading
import time
import uuid

from agent_monitor.network import account_egress, network_policy

# Evidence is reusable only on the same helper and generation. No credentials or
# durable files: each target is observed at most once a minute in this process.
_lock = threading.Lock()
_cache = None


class Skipped(RuntimeError):
    def __init__(self, reason, targets=()):
        self.details = {"reason": reason, "checked_at": datetime.now(timezone.utc).isoformat(),
                        "targets": list(targets)}
        message = {
            "china": "模型服务出口地区为中国，本批自动查询已跳过。",
            "unknown": "无法确认模型服务出口地区，本批自动查询已跳过。",
            "route_changed": "模型服务出口线路或检查有效期已变化，本批剩余自动查询已停止。",
        }[reason]
        super().__init__(message)


def _observed_probe(host, route):
    with network_policy.using_route(route):
        env = network_policy.child_environment(os.environ, scope=uuid.uuid4().hex)
    result = account_egress._run_probe(host, env)
    status = network_policy.observations(env[network_policy.INTERNAL])
    events = [row for row in status.get("events", []) if row.get("target") == "https://" + host
              and row.get("result") == "connected"]
    event = events[-1] if events else {}
    return result, event


def _evidence(route):
    global _cache
    with _lock:
        before = network_policy.observations(route)
        generation = before.get("generation")
        if type(generation) is not int:
            raise Skipped("unknown")
        key = (route, generation)
        if not _cache or _cache[0] != key:
            _cache = (key, {})
        cached = _cache[1]
        due = [host for host in account_egress.TARGETS
               if host not in cached or cached[host][0] <= time.monotonic()]
        if due:
            with ThreadPoolExecutor(max_workers=len(due)) as pool:
                results = list(pool.map(lambda host: _observed_probe(host, route), due))
            for host, (result, event) in zip(due, results):
                delay = result.get("retry_after_seconds")
                if not isinstance(delay, (int, float)) or isinstance(delay, bool) or not math.isfinite(delay):
                    delay = 0
                # A failed target's backoff must never extend another target's
                # successful geography evidence. Successful evidence lasts 60s.
                ttl = 60 if result.get("country_code") and not result.get("error") else max(60, delay)
                cached[host] = (time.monotonic() + ttl, result, event)
        readings = [cached[host][1:] for host in account_egress.TARGETS]
        targets = [{key: result.get(key) for key in ("host", "country_code", "observed_at", "error")}
                   for result, _event in readings]
        after = network_policy.observations(route)
        routes = sorted({event["route"] for result, event in readings
                         if result.get("country_code") and not result.get("error") and event.get("route")
                         and event.get("generation") == generation})
        reason = ("china" if any(row.get("country_code") == "CN" for row in targets) else
                  "unknown" if not routes else
                  "route_changed" if after.get("generation") != generation else None)
        return generation, routes, targets, reason


class Batch:
    def __init__(self):
        self.lock = threading.Lock()
        self.env = None
        self.skipped = None
        self.targets = []

    def environment(self):
        with self.lock:
            if self.skipped:
                raise self.skipped
            if self.env is None:
                try:
                    route = network_policy.endpoint()
                    generation, routes, self.targets, reason = _evidence(route)
                    if reason:
                        raise Skipped(reason, self.targets)
                    scope = "auto-" + uuid.uuid4().hex
                    network_policy.admit_automatic(route, scope, generation, routes, 120)
                    with network_policy.using_route(route):
                        self.env = network_policy.child_environment(os.environ, scope=scope)
                except network_policy.NetworkPolicyError:
                    self.skipped = Skipped("unknown", self.targets)
                    raise self.skipped from None
                except Skipped as exc:
                    self.skipped = exc
                    raise
            self.check()
            return dict(self.env)

    def check(self):
        """Bridge rejection is local admission, never a provider failure/pause."""
        if self.skipped:
            raise self.skipped
        if self.env:
            status = network_policy.observations(self.env[network_policy.INTERNAL])
            if type(status.get("generation")) is not int:
                self.skipped = Skipped("unknown", self.targets)
                raise self.skipped
            if status.get("automatic_rejection"):
                self.skipped = Skipped("route_changed", self.targets)
                raise self.skipped
