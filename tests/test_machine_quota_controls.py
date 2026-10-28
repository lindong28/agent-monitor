"""Local-only quota admission and round scheduling; no provider clients."""

import dataclasses
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from agent_monitor.quota import quota_refresh
from agent_monitor.quota import quota_sync
from agent_monitor.accounts import upstream_controls
from agent_monitor.snapshots.machine_config import Machine
from parsers import RateLimits
from parsers.accounts import Account


class QuotaAdmissionTests(unittest.TestCase):
    def setUp(self):
        # Supply the mocked provider child with a route without starting Electron.
        route = mock.patch.object(quota_refresh.network_policy, "endpoint",
                                  return_value="http://agent-monitor:synthetic-test-capability@127.0.0.1:9")
        route.start()
        self.addCleanup(route.stop)
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        patcher = mock.patch.object(quota_refresh, "ROOT", self.root)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.controls = upstream_controls.Controls(self.root / "state/upstream_controls")
        self.account = Account("synthetic")

    def child(self, payload, code=0):
        return SimpleNamespace(returncode=code, pid=0,
                               communicate=mock.Mock(return_value=(json.dumps(payload), "")))

    def test_success_releases_provider_lane_and_does_not_pause(self):
        child = self.child({"reading": dataclasses.asdict(RateLimits(1, None, 2, None))})
        with mock.patch.object(quota_refresh.subprocess, "Popen", return_value=child) as spawn:
            for _ in range(2):
                self.assertEqual(quota_refresh.load_rate_limits("codex", self.account).five_hour_pct, 1)
        self.assertEqual(spawn.call_count, 2)
        self.assertNotIn("active", self.controls.read("codex"))
        self.assertNotIn("pause", self.controls.read("codex"))

    def test_failure_persists_pause_and_prevents_second_child(self):
        for payload in ({"error": "refused", "signed_out": False}, ["invalid"]):
            with self.subTest(payload=type(payload).__name__):
                child = self.child(payload, code=1)
                with mock.patch.object(quota_refresh.subprocess, "Popen", return_value=child) as spawn:
                    with self.assertRaises(quota_refresh.RefreshError):
                        quota_refresh.load_rate_limits("codex", self.account)
                    state = upstream_controls.Controls(self.controls.root).read("codex")
                    self.assertEqual(state["pause"]["reason"], "quotaRefreshFailed")
                    self.assertNotIn("active", state)
                    with self.assertRaises(quota_refresh.RefreshError):
                        quota_refresh.load_rate_limits("codex", self.account)
                    self.assertEqual(spawn.call_count, 1)
                self.controls.resume("codex", state["pause"]["id"])

    def test_local_unavailable_and_missing_sign_in_do_not_pause(self):
        with mock.patch.object(quota_refresh.subprocess, "Popen") as spawn:
            with self.assertRaises(quota_refresh.NotSignedIn):
                quota_refresh.load_rate_limits("codex", None)
            spawn.assert_not_called()
        self.assertFalse(self.controls.root.exists())
        for flag, exception in (("signed_out", quota_refresh.NotSignedIn),
                                ("local_unavailable", quota_refresh.LocalUnavailable)):
            child = self.child({"error": "local prerequisite", flag: True}, code=1)
            with mock.patch.object(quota_refresh.subprocess, "Popen", return_value=child):
                with self.assertRaises(exception):
                    quota_refresh.load_rate_limits("codex", self.account)
            self.assertNotIn("pause", self.controls.read("codex"))

    def test_existing_lane_prevents_spawn_and_other_provider_is_independent(self):
        held = self.controls.acquire("codex")
        try:
            child = self.child({"reading": dataclasses.asdict(RateLimits(1, None, 2, None))})
            with mock.patch.object(quota_refresh.subprocess, "Popen", return_value=child) as spawn:
                with self.assertRaises(quota_refresh.RefreshError):
                    quota_refresh.load_rate_limits("codex", self.account)
                spawn.assert_not_called()
                quota_refresh.load_rate_limits("claude", self.account)
                self.assertEqual(spawn.call_count, 1)
        finally:
            held.close()

    def test_cleanup_failure_retains_active_marker(self):
        child = SimpleNamespace(returncode=None, pid=123,
                                communicate=mock.Mock(side_effect=OSError("cleanup failed")))
        with mock.patch.object(quota_refresh.subprocess, "Popen", return_value=child),\
                mock.patch.object(quota_refresh.os, "killpg"):
            with self.assertRaises(OSError):
                quota_refresh.load_rate_limits("codex", self.account)
        state = self.controls.read("codex")
        self.assertIn("active", state)
        self.assertIn("pause", state)


class SerialQuotaRoundTests(unittest.TestCase):
    def payload(self, failure=False):
        return {"schema_version": 1, "source_host_identity": "source", "exporter_commit": "a" * 40,
                "rate_limits": {"codex": {"refresh_error": "refused" if failure else None}, "claude": {}}}

    def run_round(self, failure):
        machines = [Machine(str(i), "fake-" + str(i), False) for i in range(3)]
        inputs = io.StringIO(json.dumps([m.as_config_dict() for m in machines]) + "\n")
        output = io.StringIO()
        calls = []
        def query(command, **kwargs):
            calls.append(command[-2])
            # A previous result must be emitted before admitting another machine.
            self.assertEqual(len(output.getvalue().splitlines()), len(calls) - 1)
            self.assertEqual(kwargs["timeout"], quota_sync.MACHINE_TIMEOUT)
            if failure and len(calls) == 3:
                import shlex
                argv = shlex.split(command[-1])
                self.assertEqual(json.loads(argv[argv.index("--quota-exclusions") + 1]), {"codex": ["*"]})
            return SimpleNamespace(stdout=json.dumps(self.payload(failure and len(calls) == 2)))
        with mock.patch.object(quota_sync.sys, "stdin", inputs),\
                mock.patch.object(quota_sync.sys, "stdout", output),\
                mock.patch.object(quota_sync.sync_process, "watch_parent"),\
                mock.patch.object(quota_sync.subprocess, "run", side_effect=query):
            quota_sync._worker()
        return calls, [json.loads(line) for line in output.getvalue().splitlines()]

    def test_serial_success_and_provider_scoped_failure(self):
        calls, results = self.run_round(False)
        self.assertEqual(calls, ["fake-0", "fake-1", "fake-2"])
        self.assertTrue(all(result["payload"] for result in results[:-1]))
        calls, results = self.run_round(True)
        self.assertEqual(calls, ["fake-0", "fake-1", "fake-2"])
        self.assertIsNotNone(results[2]["payload"])
        self.assertEqual(results[-1], {"done": True})

    def test_parent_wait_budget_tracks_number_of_selected_machines(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = quota_sync.Manager(Path(directory) / "quota.json")
            for count in (1, 3, 9):
                manager.machines = tuple(Machine(str(i), str(i), False) for i in range(count))
                with mock.patch.object(manager.condition, "wait_for", return_value=True) as wait:
                    self.assertTrue(manager.wait(1))
                    self.assertGreaterEqual(wait.call_args.kwargs["timeout"], count * 140 + 20)

    def test_changed_machine_scope_is_not_queued_behind_active_round(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = quota_sync.Manager(Path(directory) / "quota.json")
            manager.running = True
            manager.machines = (Machine("one", "one", False),)
            manager.requested = 1
            self.assertEqual(manager.request(manager.machines), 1)
            with self.assertRaises(RuntimeError):
                manager.request([Machine("two", "two", False)])
            self.assertEqual(manager.requested, 1)
