"""Collection scheduling, admission and per-round progress ownership."""
import logging
import threading
from contextlib import contextmanager
from datetime import datetime, timezone

import diagnostics
import generation
import hub
import query_view
import sync
import sync_process

logger = logging.getLogger("agent-monitor")
SYNC_DUE_AFTER_SECONDS = 600
STALE_AFTER_SECONDS = 6 * 60 * 60

def normalize_sync_outcomes(results, round_machines):
    if not isinstance(results, dict):
        raise TypeError("sync_all must return a machine-to-SyncResult mapping")
    outcomes = {}
    for name in sorted(set(round_machines) | set(results)):
        result = results.get(name)
        if isinstance(result, sync_process.Outcome):
            outcomes[name] = {
                "kind": "success" if result.success else "failure",
                "generation": None,
                "reason": result.error,
            }
            continue
        if not isinstance(result, sync.SyncResult):
            outcomes[name] = {
                "kind": "malformed",
                "generation": None,
                "reason": "同步结果格式错误：期望 SyncResult 且只有一个明确结论。",
            }
            continue
        has_generation = result.generation is not None
        has_error = result.error is not None
        if has_generation and not has_error:
            outcomes[name] = {
                "kind": "success",
                "generation": result.generation,
                "reason": None,
            }
        elif has_error and not has_generation:
            outcomes[name] = {
                "kind": "failure",
                "generation": None,
                "reason": str(result.error),
            }
        else:
            outcomes[name] = {
                "kind": "malformed",
                "generation": None,
                "reason": "同步结果没有明确结论，或结论互相冲突。",
            }
    return outcomes


def failed_observation(previous, attempted_at, reason):
    previous = previous or {}
    return {
        "contact_status": "unreachable",
        "last_attempt_outcome": "failure",
        "last_attempt_ts": attempted_at,
        "last_successful_contact_ts": previous.get("last_successful_contact_ts"),
        "reason": reason,
    }


def attempt_failure_observation(previous, attempted_at, reason, *, outcome="failure"):
    previous = previous or {}
    return {
        "contact_status": previous.get("contact_status", "unknown"),
        "last_attempt_outcome": outcome,
        "last_attempt_ts": attempted_at,
        "last_successful_contact_ts": previous.get("last_successful_contact_ts"),
        "reason": reason,
    }


def runtime_observation(runtime, name, meta):
    observation = runtime.get("observations", {}).get(name)
    if observation is not None:
        merged = dict(observation)
        if not merged.get("last_successful_contact_ts") and meta:
            merged["last_successful_contact_ts"] = meta.get("published_at")
        return merged
    error = runtime.get("errors", {}).get(name) or runtime.get("errors", {}).get("*")
    if error:
        return {
            "contact_status": "unreachable",
            "last_attempt_outcome": "failure",
            "last_attempt_ts": runtime.get("completed_at"),
            "last_successful_contact_ts": meta.get("published_at") if meta else None,
            "reason": error,
        }
    return {
        "contact_status": "unknown",
        "last_attempt_outcome": "unknown_since_restart",
        "last_attempt_ts": None,
        "last_successful_contact_ts": meta.get("published_at") if meta else None,
        "reason": "服务重启后尚未联系过；本进程还没有完成过同步。",
    }


def generation_age_seconds(meta, now):
    published = parse_timestamp(meta.get("published_at"))
    if published is None:
        return float("inf")
    return max((now.astimezone(timezone.utc) - published).total_seconds(), 0.0)


def aware_now(value=None):
    current = value() if callable(value) else value
    current = current or datetime.now(timezone.utc)
    if current.tzinfo is None or current.utcoffset() is None:
        raise ValueError("sync status time must be timezone-aware")
    return current


def utc_timestamp():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_timestamp(value):
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def first(query, key, default):
    values = query.get(key)
    return values[0] if values else default


class CollectionCoordinator:
    """Own collection threads, query-view lifetime and synchronized progress."""

    def __init__(self, quota_refresh, remember_accounts, instance_id):
        self.quota_refresh = quota_refresh
        self.remember_accounts = remember_accounts
        self.instance_id = instance_id
        self.query_view = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._scheduler = None
        self._state = {
            "running": False,
            "started_at": None,
            "completed_at": None,
            "round_machines": (),
            "last_completed_round_machines": (),
            "known_machines": None,
            "observations": {},
            "refresh_requested": 0,
            "refresh_completed": 0,
            "round_refresh": 0,
            "queued_refresh": False,
            "round_quota_refresh": True,
            "phase": None,
            "pending_machines": (),
            "statistics_pending": (),
        }

    def start(self):
        self.capture_startup_config()
        if hub.enabled():
            self.query_view = query_view.QueryView()
            self.query_view.start(interval=min(10, hub.interval()))

    def start_schedule(self):
        if hub.is_hub():
            self._scheduler = threading.Thread(target=self._scheduled_sync, name="agent-monitor-schedule", daemon=True)
            self._scheduler.start()

    def _scheduled_sync(self):
        while not self._stop.is_set():
            try:
                self.request({})
            except Exception:
                logger.exception("scheduled statistics sync failed; next interval will retry")
            if self._stop.wait(min(10, hub.interval())):
                break

    def stop(self):
        self._stop.set()

    def close(self):
        self.stop()
        self.quota_refresh.shutdown()
        if self.query_view is not None:
            self.query_view.close()

    def request(self, query, *, queued=False, request_receipt=None):
        if first(query, "sync", "1") == "0":
            return False
        force = first(query, "force", "0") == "1"
        if not force and not self.automatic_due():
            return False
        if force and hub.enabled() and not queued:
            # Explicit quota reads start even if an older statistics round is busy.
            self.quota_refresh.request(sync.load_machine_config().machines)
        # The Hub's prepared-view path schedules from configuration only. Keep the
        # standalone admission path for its existing direct-query lifecycle.
        if self.query_view is not None or request_receipt is not None:
            round_machines = tuple(machine.name for machine in sync.load_machine_config().machines)
        else:
            with generation.generation_admission_snapshot() as admission:
                round_machines = tuple(record.machine.name for record in admission.records)
        with self._lock:
            self._reconcile_machine_config_locked(set(round_machines))
            if force and not queued:
                self._state["refresh_requested"] += 1
                if request_receipt is not None:
                    request_receipt["refresh_request"] = self._state["refresh_requested"]
            if self._state["running"]:
                if force:
                    self._state["queued_refresh"] = True
                return False
            self._state.update(
                running=True,
                started_at=utc_timestamp(),
                completed_at=None,
                round_machines=round_machines,
                last_completed_round_machines=(),
                round_refresh=self._state["refresh_requested"],
                round_quota_refresh=force or not hub.enabled(),
                phase="statistics",
                pending_machines=round_machines,
                statistics_pending=round_machines,
            )
        try:
            thread = threading.Thread(
                target=self.run_round,
                args=(round_machines,),
                name="agent-monitor-sync",
                daemon=True,
            )
            thread.start()
        except Exception as exc:
            reason = "Failed to start sync worker: %s: %s" % (type(exc).__name__, exc)
            logger.exception("%s", reason)
            completed_at = utc_timestamp()
            with self._lock:
                observations = dict(self._state["observations"])
                for name in round_machines:
                    observations[name] = attempt_failure_observation(
                        observations.get(name), completed_at, reason
                    )
                self._state.update(
                    running=False,
                    completed_at=completed_at,
                    last_completed_round_machines=round_machines,
                    observations=observations,
                    phase=None,
                    pending_machines=(),
                )
            return False
        return True


    def automatic_due(self, now=None):
        current_time = aware_now(now)
        runtime = self.snapshot()
        if runtime["syncing"]:
            return False
        # Hub cadence is start-to-start. Waiting an interval after completion and
        # checking only once per interval used to skip the next scheduled tick.
        if hub.enabled():
            started_at = parse_timestamp(runtime["started_at"])
            if started_at is not None:
                return (current_time.astimezone(timezone.utc) - started_at).total_seconds() >= hub.interval()
        completed_at = parse_timestamp(runtime["completed_at"])
        if completed_at is not None:
            attempt_age = max(
                (current_time.astimezone(timezone.utc) - completed_at).total_seconds(),
                0.0,
            )
            if attempt_age < (hub.interval() if hub.enabled() else SYNC_DUE_AFTER_SECONDS):
                return False
        return self.due(now=current_time)


    def due(self, now=None):
        current_time = aware_now(now)
        with self.admission() as admission:
            for record in admission.records:
                if not record.admitted:
                    return True
                if generation_age_seconds(record.current.meta, current_time) >= (hub.interval() if hub.enabled() else SYNC_DUE_AFTER_SECONDS):
                    return True
        return False


    def machine_completed(self, name, result, *, statistics_only=False):
        if self.query_view is not None:
            # Keep terminal/progress notifications behind query-view publication.
            try:
                self.query_view.refresh()
            except Exception:
                logger.exception("statistics completed but query view is unavailable")
        outcome = normalize_sync_outcomes({name: result}, (name,))[name]
        attempted_at = utc_timestamp()
        with self._lock:
            previous = self._state["observations"].get(name)
            if outcome["kind"] == "success":
                observation = {
                    "contact_status": "reachable", "last_attempt_outcome": "success",
                    "last_attempt_ts": attempted_at, "last_successful_contact_ts": attempted_at,
                    "reason": None,
                }
            elif outcome["kind"] == "failure":
                observation = failed_observation(previous, attempted_at, outcome["reason"])
            else:
                observation = attempt_failure_observation(
                    previous, attempted_at, outcome["reason"], outcome="malformed_result"
                )
            self._state["observations"][name] = observation
            self._state["statistics_pending"] = tuple(
                item for item in self._state.get("statistics_pending", ()) if item != name
            )
            if not statistics_only:
                self._state["pending_machines"] = tuple(
                    item for item in self._state["pending_machines"] if item != name
                )
            if not self._state["statistics_pending"] and self._state["round_quota_refresh"]:
                self._state["phase"] = "quota"


    @diagnostics.measured("statistics.sync_round")
    def run_round(self, round_machines=()):
        results = {}
        outcomes = {}
        completed_round_machines = tuple(round_machines)
        completed_at = None
        observations = None
        quota_ticket = self.quota_refresh.status()["requested"] if hub.enabled() else 0
        try:
            with self._lock:
                quota = self._state["round_quota_refresh"]
            # Hub quota has its own worker and does not publish a statistics DB.
            collect = sync_process.sync_all if hub.enabled() else sync.sync_all
            results = collect(
                quota_refresh=quota and not hub.enabled(), progressive=False,
                on_statistics=lambda name, result: self.machine_completed(name, result, statistics_only=True),
                on_complete=self.machine_completed,
            )
            outcomes = normalize_sync_outcomes(results, round_machines)
            if any(outcome["kind"] != "success" for outcome in outcomes.values()):
                diagnostics.monitor.outcome("source_failure")
            completed_round_machines = tuple(sorted(outcomes))
            completed_at = utc_timestamp()
            with self._lock:
                observations = dict(self._state["observations"])
            for name, outcome in outcomes.items():
                if outcome["kind"] == "failure":
                    observations[name] = failed_observation(
                        observations.get(name), completed_at, outcome["reason"]
                    )
                elif outcome["kind"] == "success":
                    observations[name] = {
                        "contact_status": "reachable",
                        "last_attempt_outcome": "success",
                        "last_attempt_ts": completed_at,
                        "last_successful_contact_ts": completed_at,
                        "reason": None,
                    }
                else:
                    observations[name] = attempt_failure_observation(
                        observations.get(name),
                        completed_at,
                        outcome["reason"],
                        outcome="malformed_result",
                    )
        except Exception as exc:
            logger.exception("cross-machine sync failed: %s", exc)
            diagnostics.monitor.outcome("error")
            completed_at = utc_timestamp()
            reason = "%s: %s" % (type(exc).__name__, exc)
            with self._lock:
                observations = dict(self._state["observations"])
            for name in round_machines:
                observations[name] = attempt_failure_observation(
                    observations.get(name), completed_at, reason
                )
        finally:
            if quota_ticket:
                with self._lock:
                    self._state["phase"] = "quota"
                self.quota_refresh.wait(quota_ticket)
            self.remember_accounts()
            completed_at = completed_at or utc_timestamp()
            if observations is None:
                with self._lock:
                    observations = dict(self._state["observations"])
            for name, outcome in outcomes.items():
                if outcome["kind"] != "success":
                    continue
                current = outcome["generation"]
                if current is not None and hasattr(current, "close"):
                    try:
                        current.close()
                    except Exception as exc:
                        logger.exception("generation cleanup failed for %s: %s", name, exc)
                        previous = observations.get(name) or {}
                        observations[name] = {
                            "contact_status": (
                                "reachable"
                                if outcome["kind"] == "success"
                                else previous.get("contact_status", "unknown")
                            ),
                            "last_attempt_outcome": "cleanup_failed",
                            "last_attempt_ts": completed_at,
                            "last_successful_contact_ts": (
                                completed_at
                                if outcome["kind"] == "success"
                                else previous.get("last_successful_contact_ts")
                            ),
                            "reason": "数据版本清理失败：%s：%s"
                            % (type(exc).__name__, exc),
                        }
            with self._lock:
                followup = self._state["queued_refresh"]
                self._state.update(
                    running=False,
                    completed_at=completed_at,
                    last_completed_round_machines=completed_round_machines,
                    observations=observations,
                    refresh_completed=self._state["round_refresh"],
                    queued_refresh=False,
                    phase=None,
                    pending_machines=(),
                    statistics_pending=(),
                )
            if followup:
                self.request({"force": ["1"]}, queued=True)


    def snapshot(self, current_machines=None):
        with self._lock:
            if current_machines is not None:
                self._reconcile_machine_config_locked(set(current_machines))
            snapshot = dict(self._state)
            snapshot["round_machines"] = tuple(self._state["round_machines"])
            snapshot["last_completed_round_machines"] = tuple(
                self._state["last_completed_round_machines"]
            )
            snapshot["known_machines"] = (
                None
                if self._state["known_machines"] is None
                else frozenset(self._state["known_machines"])
            )
            snapshot["observations"] = {
                name: dict(observation)
                for name, observation in self._state["observations"].items()
            }
        snapshot["errors"] = {
            name: observation["reason"]
            for name, observation in snapshot["observations"].items()
            if observation.get("reason")
        }
        snapshot["syncing"] = snapshot.pop("running")
        snapshot["terminal"] = not snapshot["syncing"]
        return snapshot


    def _reconcile_machine_config_locked(self, current_machines):
        known = self._state["known_machines"]
        if known is None:
            self._state["known_machines"] = set(current_machines)
            return
        observations = dict(self._state["observations"])
        for name in current_machines - known:
            observations[name] = {
                "contact_status": "unknown",
                "last_attempt_outcome": "not_attempted_since_added",
                "last_attempt_ts": None,
                "last_successful_contact_ts": None,
                "reason": "这台机器是本服务启动后加入的，还没进过同步。",
            }
        self._state["known_machines"] = set(current_machines)
        self._state["observations"] = observations


    def capture_startup_config(self):
        """Anchor which machines existed when this process started.

        Tests and direct function callers can still establish the anchor lazily via
        self.status(); the real server captures it before accepting requests so a
        machine added before the first status request is not mislabeled as a
        restart-unknown machine.
        """
        with generation.generation_admission_snapshot() as admission:
            current_machines = {record.machine.name for record in admission.records}
        with self._lock:
            if self._state["known_machines"] is None:
                self._state["known_machines"] = current_machines


    def status(self, now=None, admission=None):
        current_time = aware_now(now)
        if admission is None:
            with self.admission() as loaded:
                runtime = self.snapshot(
                    record.machine.name for record in loaded.records
                )
                return self._status_from_admission(loaded, current_time, runtime)
        runtime = self.snapshot(
            record.machine.name for record in admission.records
        )
        return self._status_from_admission(admission, current_time, runtime)


    def _status_from_admission(self, admission, current_time, runtime):
        machine_rows = []
        admitted_names = {current.host for current in admission.admitted}
        for record in admission.records:
            name = record.machine.name
            meta = record.current.meta if record.admitted else None
            observation = runtime_observation(runtime, name, meta)
            if record.never:
                availability = "never"
            elif not record.admitted:
                availability = None
            else:
                availability = observation["contact_status"]
            stale = bool(
                meta
                and generation_age_seconds(meta, current_time)
                >= STALE_AFTER_SECONDS
            )
            machine_rows.append(
                {
                    "name": name,
                    "declared": True,
                    "this_machine": record.machine.is_self,
                    "admitted": record.admitted,
                    "availability": availability,
                    "stale": stale,
                    "syncing": runtime["syncing"] and name in runtime.get("pending_machines", ()),
                    "statistics_syncing": runtime["syncing"] and name in runtime.get("statistics_pending", ()),
                    "reason": observation["reason"],
                    "exclusion_reason": record.exclusion_reason,
                    "last_sync_ts": meta.get("published_at") if meta else None,
                    "generated_at": meta.get("generated_at") if meta else None,
                    "data_start_date": meta.get("data_start_date") if meta else None,
                    "generation_id": meta.get("generation_id") if meta else None,
                    "totals_schema_version": meta.get("schema_version") if meta else None,
                    "generation_totals_basis": (
                        meta.get("metric_totals_basis")
                        if meta and meta.get("schema_version") == 2
                        else ("legacy_project_derived" if meta else None)
                    ),
                    "generation_legacy_fallback_bucket_count": (
                        meta.get("legacy_fallback_bucket_count")
                        if meta and meta.get("schema_version") == 2
                        else None
                    ),
                    "generation_legacy_fallback_bucket_dimensions": (
                        meta.get("legacy_fallback_bucket_dimensions")
                        if meta and meta.get("schema_version") == 2
                        else None
                    ),
                    "last_attempt_outcome": observation["last_attempt_outcome"],
                    "last_attempt_ts": observation["last_attempt_ts"],
                    "last_successful_contact_ts": observation["last_successful_contact_ts"],
                }
            )
        declared = len(admission.config.machines)
        if hub.enabled():
            import statistics_snapshot
            details = {current.host: statistics_snapshot.read_metadata(current.db_path) for current in admission.admitted}
            for row in machine_rows:
                detail = details.get(row["name"], {"state": "not_collected"})
                row["statistics"] = {key: detail.get(key) for key in (
                    "state", "observed_at", "history_authoritative_from", "scan_complete",
                    "source_error_count", "blocked_source_count", "usage_quality_counts",
                )}
        return {
            "instance_id": self.instance_id,
            "quota_refresh": self.quota_refresh.status() if hub.enabled() else None,
            "refresh_requested": runtime.get("refresh_requested", 0),
            "refresh_completed": runtime.get("refresh_completed", 0),
            "queued_refresh": runtime.get("queued_refresh", False),
            "phase": runtime.get("phase"),
            "coverage": {"admitted": len(admitted_names), "declared": declared},
            "all_machines": sorted(admitted_names),
            "machines": machine_rows,
            "syncing": runtime["syncing"],
            "terminal": runtime["terminal"],
            "started_at": runtime["started_at"],
            "completed_at": runtime["completed_at"],
        }


    @contextmanager
    def admission(self):
        context = self.query_view.snapshot() if self.query_view is not None else generation.generation_admission_snapshot()
        with context as admission:
            yield admission

