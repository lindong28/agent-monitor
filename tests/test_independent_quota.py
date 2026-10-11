import copy
import tempfile
import threading
import unittest
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import quota_sync
import server
from machine_config import Machine


class IndependentQuotaTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "quota.json"
        self.machine = Machine("mini", "mini-alias", False)
        self.block = {"account_id": "workspace", "account_label": "one@example.test",
                      "updated_at": "2026-10-11T00:00:00Z", "seven_day_pct": 20,
                      "refresh_error": None, "signed_out": False}
        self.current = SimpleNamespace(host="mini", meta={"source_host_identity": "source-a",
            "rate_limits": {p: copy.deepcopy(self.block) for p in ("claude", "codex")}})

    def payload(self, **updates):
        block = {**self.block, "updated_at": "2026-10-11T01:00:00Z", "seven_day_pct": 80, **updates}
        return {"schema_version": 1, "source_host_identity": "source-a",
                "exporter_commit": "a" * 40, "rate_limits": {"claude": block, "codex": block}}

    def run_query(self, manager, payload=None, error=None):
        def run(machines, complete):
            complete(self.machine.name, payload, error)
        with mock.patch.object(quota_sync, "run_queries", side_effect=run):
            ticket = manager.request([self.machine])
            self.assertTrue(manager.wait(ticket, timeout=2))

    def test_new_reading_survives_old_statistics_and_restart(self):
        manager = quota_sync.Manager(self.path)
        self.run_query(manager, self.payload())
        for source in (manager, quota_sync.Manager(self.path)):
            result = source.merge(self.current, self.machine)
            self.assertEqual(result["codex"]["seven_day_pct"], 80)
            self.assertEqual(result["codex"]["updated_at"], "2026-10-11T01:00:00Z")
        self.assertEqual(self.current.meta["rate_limits"]["codex"]["seven_day_pct"], 20)

    def test_signed_out_outcome_does_not_depend_on_fallback_reading_age(self):
        for observed_at in ("2026-10-11T00:00:00Z", "2026-10-10T23:00:00Z"):
            with self.subTest(observed_at=observed_at):
                self.path.unlink(missing_ok=True)
                manager = quota_sync.Manager(self.path)
                self.run_query(manager, self.payload(updated_at=observed_at, signed_out=True, seven_day_pct=10))
                result = manager.merge(self.current, self.machine)["codex"]
                self.assertTrue(result["signed_out"])
                self.assertEqual(result["updated_at"], self.block["updated_at"])
                self.assertEqual(result["seven_day_pct"], 20 if observed_at < self.block["updated_at"] else 10)

    def test_failure_retains_observation_and_success_clears_failure(self):
        manager = quota_sync.Manager(self.path)
        self.run_query(manager, self.payload())
        self.run_query(manager, error="query timed out")
        result = manager.merge(self.current, self.machine)["codex"]
        self.assertEqual(result["seven_day_pct"], 80)
        self.assertEqual(result["updated_at"], "2026-10-11T01:00:00Z")
        self.assertIn("query timed out", result["refresh_error"])
        self.run_query(manager, self.payload())
        self.assertIsNone(manager.merge(self.current, self.machine)["codex"]["refresh_error"])

    def test_provider_failure_with_older_fallback_retains_latest_whole_reading(self):
        manager = quota_sync.Manager(self.path)
        self.run_query(manager, self.payload(five_hour_pct=None))
        self.run_query(manager, self.payload(updated_at="2026-10-10T00:00:00Z",
            seven_day_pct=10, five_hour_pct=50, refresh_error="provider timeout"))
        result = manager.merge(self.current, self.machine)["codex"]
        self.assertEqual(result["seven_day_pct"], 80)
        self.assertIsNone(result["five_hour_pct"])
        self.assertEqual(result["updated_at"], "2026-10-11T01:00:00Z")
        self.assertEqual(result["refresh_error"], "provider timeout")

    def test_later_snapshot_success_supersedes_old_failure(self):
        manager = quota_sync.Manager(self.path)
        self.run_query(manager, self.payload())
        self.run_query(manager, error="old timeout")
        manager.records["mini"]["attempted_at"] = "2026-10-11T01:30:00Z"
        self.current.meta["rate_limits"]["codex"].update(updated_at="2026-10-11T02:00:00Z", seven_day_pct=90)
        result = manager.merge(self.current, self.machine)["codex"]
        self.assertEqual(result["seven_day_pct"], 90)
        self.assertIsNone(result["refresh_error"])

    def test_repeated_requests_coalesce_and_wait_for_their_own_round(self):
        manager = quota_sync.Manager(self.path)
        entered, release = threading.Event(), threading.Event()
        calls = []
        def run(machines, complete):
            calls.append(machines)
            if len(calls) == 1:
                entered.set()
                self.assertTrue(release.wait(2))
            complete("mini", self.payload(), None)
        with mock.patch.object(quota_sync, "run_queries", side_effect=run):
            first = manager.request([self.machine])
            self.assertTrue(entered.wait(1))
            manager.request([self.machine])
            last = manager.request([self.machine])
            self.assertFalse(manager.wait(last, timeout=.01))
            release.set()
            self.assertTrue(manager.wait(last, timeout=2))
        self.assertEqual(len(calls), 2)
        self.assertEqual(manager.status()["completed"], last)

    def test_shutdown_closes_admission_and_drains_owned_worker(self):
        manager = quota_sync.Manager(self.path)
        entered, release = threading.Event(), threading.Event()
        def run(machines, complete):
            entered.set()
            release.wait(2)
            raise RuntimeError("terminated")
        with mock.patch.object(quota_sync, "run_queries", side_effect=run), \
                mock.patch.object(quota_sync.sync_process, "shutdown", side_effect=release.set):
            manager.request([self.machine])
            self.assertTrue(entered.wait(1))
            manager.shutdown()
        self.assertFalse(manager.thread.is_alive())
        with self.assertRaises(RuntimeError):
            manager.request([self.machine])

    def test_source_alias_and_account_mismatch_do_not_reassign_readings(self):
        for changed in ("source", "alias", "account"):
            with self.subTest(changed=changed):
                manager = quota_sync.Manager(self.path)
                payload = self.payload()
                machine = self.machine
                if changed == "source": payload["source_host_identity"] = "source-b"
                if changed == "account":
                    payload = self.payload(account_label="two@example.test")
                self.run_query(manager, payload)
                if changed == "alias": machine = Machine("mini", "new-host", False)
                result = manager.merge(self.current, machine)["codex"]
                self.assertEqual(result["account_label"], "one@example.test")
                self.assertEqual(result["seven_day_pct"], 20)

    def test_query_starts_while_existing_statistics_round_is_busy(self):
        server._reset_sync_state_for_tests()
        manager = quota_sync.Manager(self.path)
        entered, release = threading.Event(), threading.Event()
        def run(machines, complete):
            entered.set()
            release.wait(2)
            complete("mini", self.payload(), None)
        try:
            with mock.patch.object(server, "QUOTA_REFRESH", manager), \
                    mock.patch.object(server.hub, "enabled", return_value=True), \
                    mock.patch.object(server.sync, "load_machine_config", return_value=SimpleNamespace(machines=[self.machine])), \
                    mock.patch.object(quota_sync, "run_queries", side_effect=run):
                server._SYNC_STATE["running"] = True
                receipt = server.refresh_endpoint({"ack": ["1"]})
                self.assertTrue(entered.wait(1), "quota waited for the active statistics round")
                self.assertTrue(receipt["queued_refresh"])
                self.assertTrue(manager.status()["running"])
                release.set()
                self.assertTrue(manager.wait(1, timeout=2))
        finally:
            release.set()
            server._reset_sync_state_for_tests()

    def test_supervised_protocol_streams_results_and_rejects_incomplete_exit(self):
        real_popen = subprocess.Popen
        valid = {"machine": "mini", "payload": self.payload(), "error": None}
        for messages, succeeds in (([valid, {"done": True}], True),
                                   ([valid], False), ([valid, valid, {"done": True}], False)):
            with self.subTest(messages=len(messages), succeeds=succeeds):
                code = "import sys;sys.stdin.readline();" + ";".join(
                    "print(%r,flush=True)" % json.dumps(m) for m in messages)
                def launch(*args, **kwargs):
                    return real_popen([sys.executable, "-c", code], **kwargs)
                events = []
                with mock.patch.object(quota_sync.sync_process.subprocess, "Popen", side_effect=launch):
                    if succeeds:
                        quota_sync.run_queries([self.machine], lambda *args: events.append(args))
                        self.assertEqual(events, [("mini", self.payload(), None)])
                    else:
                        with self.assertRaises((RuntimeError, ValueError)):
                            quota_sync.run_queries([self.machine], lambda *args: events.append(args))
                self.assertFalse(quota_sync.sync_process._active)
