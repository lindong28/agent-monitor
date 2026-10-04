"""Refresh through the running dashboard, which owns sync and account memory."""

import json
import sys
import time
import urllib.request
from pathlib import Path

import machine_config

ROOT = Path(__file__).resolve().parent


class RefreshError(RuntimeError):
    pass


def refresh(get, expected_names, *, timeout, clock=time.monotonic, sleep=time.sleep):
    before = get("/api/sync-status")
    deadline = clock() + timeout
    initial = get("/api/overview?force=1")["sync"]
    instance = initial.get("instance_id")
    started = initial.get("started_at")
    target = initial.get("refresh_requested")
    if not started or instance != before.get("instance_id") or (
        not before.get("syncing") and started == before.get("started_at")
    ):
        raise RefreshError("The dashboard did not confirm a new refresh round; restart agent-monitor and retry.")

    def check_round(status):
        if status.get("instance_id") != instance or (target is None and status.get("started_at") != started):
            raise RefreshError("The dashboard or refresh round changed while waiting; retry Refresh.")

    status = initial
    while True:
        check_round(status)
        if status.get("terminal") and not status.get("syncing") and status.get("completed_at") and (
            target is None or status.get("refresh_completed", 0) >= target
        ):
            break
        if clock() >= deadline:
            raise RefreshError("Refresh is still running after the wait limit; inspect agent-monitor status and retry later.")
        sleep(min(1, max(0, deadline - clock())))
        status = get("/api/sync-status")
    overview = get("/api/overview?sync=0")
    final = overview["sync"]
    check_round(final)
    if final.get("syncing") or final.get("completed_at") != status.get("completed_at"):
        raise RefreshError("Refresh completion changed while reading the result; retry Refresh.")
    failures = []
    rows = {row["name"]: row for row in final.get("machines", [])}
    for name in sorted(expected_names):
        row = rows.get(name, {})
        if (not row.get("admitted") or row.get("last_attempt_outcome") != "success"
                or row.get("last_attempt_ts") != final["completed_at"]):
            # Server-side reasons may include raw SSH output. Keep the daemon
            # log free of it; the dashboard already presents contact details.
            failures.append("%s: data refresh failed or was not included; inspect the dashboard's machine status." % name)
    for provider in ("claude", "codex"):
        block = overview.get("rate_limits", {}).get(provider, {})
        errors = block.get("refresh_errors")
        if errors is None:
            failures.append("%s: active Quota refresh is unconfirmed; update the dashboard and exporters." % provider)
        else:
            for error in errors:
                failures.append("%s / %s Quota: %s" % (error["machine"], provider, error["reason"]))
    return failures


def main():
    try:
        import hub
        origin = hub.origin()
        if origin is None:
            port = int((ROOT / "state/port").read_text().strip())
            if not 1 <= port <= 65535:
                raise ValueError("invalid port")
            origin = "http://127.0.0.1:%d" % port
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

        def get(path):
            with opener.open(origin + path, timeout=30) as response:
                return json.load(response)

        import server

        health = get("/api/health?asset_watch=1")
        if health.get("stale") or health.get("signature") != server._source_signature():
            raise RefreshError("The dashboard is running different code; run 'agent-monitor restart', then 'agent-monitor refresh'.")
        names = set(machine_config.load_machine_config().by_name)
        print("agent-monitor refresh: refreshing %d machines and their current-account Quota…" % len(names), flush=True)
        failures = refresh(get, names, timeout=210 * len(names) + 60)
        if failures:
            print("agent-monitor refresh: partial — some data or Quota could not be refreshed.")
            for failure in failures:
                print("  " + failure)
            return 1
        print("agent-monitor refresh: completed — %d machines' data and current-account Quota refreshed." % len(names))
        return 0
    except RefreshError as exc:
        print("agent-monitor refresh: incomplete — " + str(exc), file=sys.stderr)
    except (OSError, ValueError, KeyError, TypeError):
        print("agent-monitor refresh: incomplete — dashboard unavailable or response invalid. Run 'agent-monitor status', then 'agent-monitor restart' and 'agent-monitor refresh'.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
