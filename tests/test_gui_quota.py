import contextlib
import fcntl
import json
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import exporter
import hub
import rollup
import sync
from parsers import RateLimits, accounts


class GuiQuotaTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.state = self.root / "state"
        self.state.mkdir()
        self.request = self.state / "quota-refresh-request.json"
        self.receipt = self.state / "quota-refresh-receipt.json"
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        for patch in (
            mock.patch.object(hub, "ROOT", self.root),
            mock.patch.object(exporter, "ROOT", self.root),
            mock.patch.object(hub, "enabled", return_value=True),
            mock.patch.object(hub, "QUOTA_POLL_INTERVAL", 0.002),
            mock.patch.object(hub, "QUOTA_KICK_INTERVAL", 0.01),
            mock.patch.object(exporter.accounts, "claude_account", return_value=accounts.Account("a")),
            mock.patch.object(exporter.accounts, "codex_account", return_value=accounts.Account("a")),
            mock.patch.object(exporter.claude_status, "load_rate_limits", return_value=None),
            mock.patch.object(exporter.codex, "load_rate_limits", return_value=None),
            mock.patch.object(exporter.quota_refresh, "load_rate_limits", side_effect=AssertionError("no real provider calls")),
        ):
            self.stack.enter_context(patch)
        self.loaded = "program = %s\narguments = {\n%s\ncollect\n}\n" % ((self.root / "agent-monitor",) * 2)
        self.limits = {provider: {**exporter._rate_limit_block(
            RateLimits(12, None, 34, None, updated_at="2026-09-09T00:00:00Z"), accounts.Account("a")),
            "refresh_error": None} for provider in ("claude", "codex")}

    def run_collector(self):
        with mock.patch.object(rollup, "run", return_value={}), mock.patch.object(exporter, "_rate_limits", return_value=self.limits):
            hub.collect()

    def test_idle_collector_receipt_bypasses_hourly_schedule_and_uses_original_result(self):
        (self.state / "quota-collection-at").write_text(str(time.time()))
        calls = []
        def launch(args, **kwargs):
            calls.append(args)
            if args[1] == "kickstart":
                self.run_collector()
                (self.state / "quota_snapshot.json").write_text(json.dumps({"unrelated": "later cache"}))
            return SimpleNamespace(returncode=0, stdout=self.loaded)
        with mock.patch.object(hub.subprocess, "run", side_effect=launch):
            result = hub.request_quota_refresh(timeout=1)
        self.assertEqual(result, self.limits)
        self.assertEqual([args[1] for args in calls], ["print", "kickstart"])
        self.assertTrue(all("-k" not in args for args in calls))
        self.assertFalse(self.request.exists())
        self.assertEqual(self.receipt.stat().st_mode & 0o777, 0o600)
        with mock.patch.object(rollup, "run", return_value={}), mock.patch.object(exporter, "_rate_limits") as read:
            hub.collect()
        read.assert_not_called()

    def test_request_arriving_during_running_collector_query_is_only_acked_by_next_query(self):
        entered, release = threading.Event(), threading.Event()
        def old_read(**kwargs):
            entered.set()
            self.assertTrue(release.wait(2))
            return self.limits
        with mock.patch.object(rollup, "run", return_value={}), mock.patch.object(exporter, "_rate_limits", side_effect=old_read):
            collector = threading.Thread(target=hub.collect)
            collector.start()
            self.assertTrue(entered.wait(1))
            hub._write_quota_message(self.request, {"nonce": "a" * 32})
            release.set()
            collector.join(2)
        self.assertFalse(collector.is_alive())
        self.assertFalse(self.receipt.exists())
        self.run_collector()
        self.assertEqual(json.loads(self.receipt.read_text())["nonce"], "a" * 32)

    def test_retry_kick_covers_request_at_end_of_old_collector(self):
        kicks = []
        def launch(args, **kwargs):
            if args[1] == "kickstart":
                kicks.append(args)
                # First kick reaches an old process that already passed quota.
                if len(kicks) == 2:
                    self.run_collector()
            return SimpleNamespace(returncode=0, stdout=self.loaded)
        with mock.patch.object(hub.subprocess, "run", side_effect=launch):
            self.assertEqual(hub.request_quota_refresh(timeout=1), self.limits)
        self.assertEqual(len(kicks), 2)

    def test_concurrent_requests_have_distinct_receipts_and_collector_does_not_take_request_lock(self):
        results, nonces, errors = [], [], []
        def launch(args, **kwargs):
            if args[1] == "kickstart":
                nonce = json.loads(self.request.read_text())["nonce"]
                nonces.append(nonce)
                self.run_collector()
                limits = {provider: {**block, "refresh_error": nonce} for provider, block in self.limits.items()}
                hub._write_quota_message(self.receipt, {"nonce": nonce, "rate_limits": limits})
            return SimpleNamespace(returncode=0, stdout=self.loaded)
        def request():
            try:
                results.append(hub.request_quota_refresh(timeout=2))
            except BaseException as exc:
                errors.append(exc)
        with mock.patch.object(hub.subprocess, "run", side_effect=launch):
            workers = [threading.Thread(target=request) for _ in range(2)]
            for worker in workers:
                worker.start()
            for worker in workers:
                worker.join(3)
        self.assertFalse(any(worker.is_alive() for worker in workers))
        self.assertEqual(errors, [])
        self.assertEqual(len(set(nonces)), 2)
        self.assertEqual({result["claude"]["refresh_error"] for result in results}, set(nonces))

    def test_stale_wrong_and_late_ack_are_not_success(self):
        hub._write_quota_message(self.receipt, {"nonce": "0" * 32, "rate_limits": self.limits})
        with mock.patch.object(hub.subprocess, "run", return_value=SimpleNamespace(returncode=0, stdout=self.loaded)):
            with self.assertRaisesRegex(hub.QuotaRequestError, "超时"):
                hub.request_quota_refresh(timeout=0.025)
        self.assertFalse(self.request.exists())
        def late(args, **kwargs):
            if args[1] == "kickstart":
                nonce = json.loads(self.request.read_text())["nonce"]
                time.sleep(0.03)
                hub._write_quota_message(self.receipt, {"nonce": nonce, "rate_limits": self.limits})
            return SimpleNamespace(returncode=0, stdout=self.loaded)
        with mock.patch.object(hub.subprocess, "run", side_effect=late):
            with self.assertRaisesRegex(hub.QuotaRequestError, "超时"):
                hub.request_quota_refresh(timeout=0.015)
        self.assertFalse(self.request.exists())

    def test_queue_wait_is_bounded_without_overwriting_pending_nonce(self):
        hub._write_quota_message(self.request, {"nonce": "b" * 32})
        with (self.state / "quota-refresh-request.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            with mock.patch.object(hub.subprocess, "run") as launch:
                with self.assertRaisesRegex(hub.QuotaRequestError, "另一个请求"):
                    hub.request_quota_refresh(timeout=0.02)
            launch.assert_not_called()
            self.assertEqual(json.loads(self.request.read_text())["nonce"], "b" * 32)

    def test_missing_foreign_and_rejected_job_do_not_claim_success(self):
        for output, code, kick_code in ((self.loaded, 113, 0), (self.loaded.replace("collect", "rollup"), 0, 0),
                                       (self.loaded.replace(str(self.root), "/foreign"), 0, 0), (self.loaded, 0, 1)):
            with self.subTest(output=output, code=code, kick_code=kick_code):
                def launch(args, **kwargs):
                    return SimpleNamespace(returncode=code if args[1] == "print" else kick_code, stdout=output)
                with mock.patch.object(hub.subprocess, "run", side_effect=launch) as run:
                    with self.assertRaises(hub.QuotaRequestError):
                        hub.request_quota_refresh(timeout=1)
                self.assertEqual(run.call_count, 2 if kick_code else 1)
                self.assertFalse(self.request.exists())

    def test_failed_atomic_receipt_write_preserves_complete_previous_receipt(self):
        previous = {"nonce": "a" * 32, "rate_limits": self.limits}
        hub._write_quota_message(self.receipt, previous)
        with mock.patch.object(Path, "replace", side_effect=OSError("interrupted")):
            with self.assertRaises(OSError):
                hub._write_quota_message(self.receipt, {"nonce": "b" * 32, "rate_limits": self.limits})
        self.assertEqual(json.loads(self.receipt.read_text()), previous)
        self.assertEqual(list(self.state.iterdir()), [self.receipt])

    def test_incomplete_receipt_and_launchctl_timeout_do_not_ack(self):
        def launch(args, **kwargs):
            if args[1] == "kickstart":
                nonce = json.loads(self.request.read_text())["nonce"]
                hub._write_quota_message(self.receipt, {"nonce": nonce, "rate_limits": {"claude": self.limits["claude"]}})
            return SimpleNamespace(returncode=0, stdout=self.loaded)
        with mock.patch.object(hub.subprocess, "run", side_effect=launch):
            with self.assertRaisesRegex(hub.QuotaRequestError, "超时"):
                hub.request_quota_refresh(timeout=0.02)
        with mock.patch.object(hub.subprocess, "run", side_effect=subprocess.TimeoutExpired("launchctl", 0.01)):
            with self.assertRaises(hub.QuotaRequestError):
                hub.request_quota_refresh(timeout=0.02)
        self.assertFalse(self.request.exists())

    def test_collector_ack_keeps_partial_provider_error_and_captured_nonce(self):
        hub._write_quota_message(self.request, {"nonce": "a" * 32})
        limits = {**self.limits, "codex": {**self.limits["codex"], "refresh_error": "provider failed"}}
        def read(**kwargs):
            hub._write_quota_message(self.request, {"nonce": "b" * 32})
            return limits
        with mock.patch.object(rollup, "run", return_value={}), mock.patch.object(exporter, "_rate_limits", side_effect=read):
            hub.collect()
        self.assertEqual(json.loads(self.receipt.read_text()), {"nonce": "a" * 32, "rate_limits": limits})
        self.assertEqual(hub._pending_quota_nonce(), "b" * 32)

    def test_gui_failure_retains_matching_account_only_without_new_timestamp(self):
        (self.state / "quota_snapshot.json").write_text(json.dumps(self.limits))
        for account in (accounts.Account("a"), accounts.Account("b"), None):
            with self.subTest(account=account), mock.patch.object(hub, "request_quota_refresh", side_effect=hub.QuotaRequestError("GUI unavailable")), \
                    mock.patch.object(exporter.accounts, "claude_account", return_value=account):
                result = exporter._gui_rate_limits()
            self.assertEqual(result["codex"]["updated_at"], self.limits["codex"]["updated_at"])
            self.assertEqual(result["claude"]["updated_at"], self.limits["claude"]["updated_at"] if account and account.account_id == "a" else "")
            self.assertEqual(result["claude"]["five_hour_pct"], 12 if account and account.account_id == "a" else None)
            self.assertEqual(result["claude"]["refresh_error"], "GUI unavailable")
        (self.state / "quota_snapshot.json").unlink()
        with mock.patch.object(hub, "request_quota_refresh", side_effect=hub.QuotaRequestError("timeout")):
            result = exporter._gui_rate_limits()
        self.assertTrue(all(block["updated_at"] == "" for block in result.values()))

    def test_remote_only_hub_manual_exports_request_gui_quota(self):
        for enabled, refresh, expected in ((True, True, " --gui-quota"), (True, False, " --cached-quota"),
                                            (False, True, ""), (False, False, " --cached-quota")):
            commands = []
            def runner(args, **kwargs):
                commands.append(args[-1])
                output = "/tmp/agent-monitor-export.ABC12345\n" if "mktemp" in args[-1] else "{}"
                return subprocess.CompletedProcess(args, 0, output, "")
            with self.subTest(enabled=enabled, refresh=refresh), mock.patch.object(hub, "enabled", return_value=enabled):
                sync._pull_remote_export("fixture", self.root / "bundle", timeout=1, runner=runner, quota_refresh=refresh)
            command = next(command for command in commands if "agent-monitor export" in command)
            self.assertEqual(command, "~/.local/bin/agent-monitor export --out /tmp/agent-monitor-export.ABC12345/bundle" + expected)

    def test_cli_forwards_gui_and_cached_quota_flags(self):
        cli = self.root / "agent-monitor"
        shutil.copyfile(Path(__file__).resolve().parents[1] / "agent-monitor", cli)
        python = self.root / ".venv/bin/python"
        python.parent.mkdir(parents=True)
        python.symlink_to(sys.executable)
        (self.root / "exporter.py").write_text("class ExportError(Exception): pass\ndef export_bundle(**kwargs): return kwargs\n")
        for flags, gui, refresh in (([], False, True), (["--gui-quota"], True, True),
                                     (["--gui-quota", "--cached-quota"], True, False)):
            with self.subTest(flags=flags):
                result = subprocess.run(["bash", str(cli), "export", "--out", "fixture", *flags],
                                        cwd=self.root, text=True, capture_output=True, timeout=5)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(result.stdout), {"output_path": "fixture", "gui_quota": gui, "quota_refresh": refresh})

    def test_gui_failure_is_visible_through_existing_provider_error_projection(self):
        import server
        with mock.patch.object(hub, "request_quota_refresh", side_effect=hub.QuotaRequestError("GUI unavailable")):
            blocks = exporter._gui_rate_limits()
        admission = SimpleNamespace(admitted=[SimpleNamespace(host="fixture", meta={"rate_limits": blocks})])
        with mock.patch.object(server, "_self_machine_name", return_value="another"), \
                mock.patch.object(server, "_quota_unavailable_reason", return_value="No reading"):
            projected = server._live_rate_limits_from_admission(admission)
        for provider in ("claude", "codex"):
            self.assertEqual(projected[provider]["refresh_errors"], [{"machine": "fixture", "reason": "GUI unavailable"}])


if __name__ == "__main__":
    unittest.main()
