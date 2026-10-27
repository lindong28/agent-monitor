"""Failure -> persisted state -> operator readback, with synthetic inputs only."""
import contextlib
import io
import json
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

import diagnostics
import exporter
from agent_monitor import quota_service
import hub
import quota_refresh
import server
import sync
import upstream_controls
from parsers.accounts import Account
from parsers import RateLimits


class FailureEvidenceTests(unittest.TestCase):
    def setUp(self):
        # Exercise failure persistence without booting the real proxy runtime.
        # Child operations are mocked below; this endpoint is never contacted.
        route = mock.patch.object(quota_refresh.network_policy, "endpoint",
                                  return_value="http://agent-monitor:synthetic-test-capability@127.0.0.1:9")
        route.start()
        self.addCleanup(route.stop)

    def test_original_quota_failure_survives_rejection_restart_and_resume(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            payload = {"error": "fixture-secret-token", "failure": {
                "stage": "quota_read", "kind": "rpc_error", "error_type": "RefreshError",
                "rpc_code": -32000, "http_status": 401, "body": "fixture-secret-token"}}
            child = SimpleNamespace(returncode=1, pid=0,
                                    communicate=mock.Mock(return_value=(json.dumps(payload), "")))
            with mock.patch.object(quota_refresh, "ROOT", root), \
                    mock.patch.object(quota_refresh.subprocess, "Popen", return_value=child) as spawn:
                with self.assertRaises(quota_refresh.RefreshError):
                    quota_refresh.load_rate_limits("codex", Account("synthetic"))
                with self.assertRaises(quota_refresh.RefreshError) as rejected:
                    quota_refresh.load_rate_limits("codex", Account("synthetic"))
                self.assertEqual(spawn.call_count, 1)
            controls = upstream_controls.Controls(root / "state/upstream_controls")
            pause = controls.read("codex")["pause"]
            self.assertEqual(pause["failure"]["http_status"], 401)
            self.assertEqual(pause["failure"]["stage"], "quota_read")
            self.assertNotIn("fixture-secret-token", json.dumps(pause))
            self.assertIn("401", str(rejected.exception))
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                upstream_controls.main(["status", "codex", "--state-root", str(controls.root)])
            self.assertIn("401", output.getvalue())
            controls.resume("codex", pause["id"])
            self.assertEqual(controls.read("codex")["last_pause"], pause)

    def test_legacy_pause_explicitly_reports_missing_original_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            controls = upstream_controls.Controls(Path(directory))
            lease = controls.acquire("codex")
            lease.pause("quotaRefreshFailed")
            lease.close()
            with self.assertRaises(upstream_controls.Paused) as caught:
                controls.acquire("codex")
            self.assertIn("未记录", str(caught.exception))

    def test_pause_write_failure_does_not_hide_original_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            child = SimpleNamespace(returncode=1, pid=0, communicate=mock.Mock(return_value=(json.dumps({
                "error": "受控原始错误", "failure": {"stage": "quota_read", "http_status": 403}}), "")))
            save = upstream_controls.Controls._save
            def fail_pause(controls, provider, value):
                if value.get("pause"):
                    raise OSError(28, "fixture-secret-token")
                return save(controls, provider, value)
            with mock.patch.object(quota_refresh, "ROOT", root), \
                    mock.patch.object(quota_refresh.subprocess, "Popen", return_value=child), \
                    mock.patch.object(upstream_controls.Controls, "_save", fail_pause), \
                    self.assertLogs("diagnostics", level="ERROR") as logs:
                with self.assertRaises(quota_refresh.RefreshError) as caught:
                    quota_refresh.load_rate_limits("codex", Account("synthetic"))
            self.assertIn("403", str(caught.exception))
            self.assertIn("保存失败", str(caught.exception))
            self.assertIn("errno=28", str(caught.exception))
            self.assertNotIn("fixture-secret-token", str(caught.exception) + str(logs.output))
            self.assertIn("active", upstream_controls.Controls(root / "state/upstream_controls").read("codex"))

    def test_failure_transport_rejects_arbitrary_values(self):
        self.assertEqual(diagnostics.safe_failure({"stage": ["secret"], "kind": "secret",
            "error_type": "secret", "http_status": "secret", "rpc_code": True,
            "timeout_seconds": float("nan"), "frames": [{"file": [], "line": 1}],
            "body": "secret", "reader_sha256": "secret"}), {"frames": []})

    def test_parent_launch_errors_reach_cached_export_without_pause(self):
        messages = []
        for number in (13, 24):
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                with mock.patch.object(quota_refresh, "ROOT", root), \
                        mock.patch.object(exporter, "ROOT", root), \
                        mock.patch.object(hub, "enabled", return_value=True), \
                        mock.patch.object(quota_service.accounts, "codex_account", return_value=Account("synthetic")), \
                        mock.patch.object(quota_service.accounts, "claude_account", return_value=None), \
                        mock.patch.object(quota_service.codex, "load_rate_limits", return_value=RateLimits(1, None, 2, None)), \
                        mock.patch.object(quota_service.claude_status, "load_rate_limits", return_value=None), \
                        mock.patch.object(quota_refresh.subprocess, "Popen", side_effect=OSError(number, "secret-fixture")):
                    result = exporter._rate_limits(refresh=True)
                message = result["codex"]["refresh_error"]
                messages.append(message)
                self.assertIn("errno=" + str(number), message)
                self.assertIn("stage=child_start", message)
                self.assertNotIn("secret-fixture", message)
                self.assertNotIn("pause", upstream_controls.Controls(root / "state/upstream_controls").read("codex"))
                cached = json.loads((root / "state/quota_snapshot.json").read_text())
                self.assertEqual(cached["codex"]["refresh_error"], message)
        self.assertNotEqual(*messages)

    def test_safe_exception_event_is_persisted_without_exception_values(self):
        with tempfile.TemporaryDirectory() as directory:
            monitor = diagnostics.Monitor()
            monitor.path = Path(directory)
            self.addCleanup(monitor.close)
            try:
                raise PermissionError(13, "Bearer fixture-secret-token", "/secret/account")
            except OSError as exc:
                event_id = monitor.record_failure("account_api", exc)
            monitor.tick()
            raw = (monitor.path / "events.jsonl").read_text()
            event = next(json.loads(line) for line in raw.splitlines() if json.loads(line).get("failure_id") == event_id)
            self.assertEqual(event["failure"]["errno"], 13)
            self.assertEqual(event["failure"]["error_type"], "PermissionError")
            self.assertNotIn("fixture-secret-token", raw)
            self.assertNotIn("/secret/account", raw)

    def test_network_start_failure_preserves_errno_without_sensitive_text(self):
        results = []
        for number in (13, 8):
            with mock.patch.object(subprocess, "run", side_effect=OSError(number, "fixture-secret-token")):
                result = server.NetworkReport().probe()
            results.append(result["error"])
            self.assertIn("errno=" + str(number), result["error"])
            self.assertNotIn("fixture-secret-token", json.dumps(result))
        self.assertNotEqual(*results)

    def test_sync_timeout_reports_callsite_stage_without_command(self):
        def timed_out(args, **kwargs):
            raise subprocess.TimeoutExpired(["secret-command"], kwargs["timeout"], stderr="fixture-secret-token")
        for stage in ("remote_export", "transfer"):
            with self.assertRaises(subprocess.TimeoutExpired) as caught:
                sync._run(timed_out, ["secret-command"], sync.time.monotonic() + 10, stage=stage)
            reason = sync._timeout_reason(caught.exception)
            self.assertIn(stage, reason)
            self.assertNotIn("secret", reason)


if __name__ == "__main__":
    unittest.main()
