"""Quota failure evidence and recovery use fabricated accounts and isolated state."""
import json
import tempfile
import io
import http.client
import threading
import subprocess
import unittest
from pathlib import Path
from unittest import mock
from types import SimpleNamespace

import diagnostics
import exporter
import quota_refresh
import quota_recovery
import quota_sync
import hub
import server
from parsers import RateLimits
from parsers import accounts, codex


class EvidenceTests(unittest.TestCase):
    def test_header_schemes_and_multi_cookie_values_never_survive_projection(self):
        for message, secrets in (("Authorization: Basic dXNlcjpwYXNz", ["dXNlcjpwYXNz"]),
                ("Cookie: first=one; session=fixture-secret", ["one", "fixture-secret"]),
                ('{"refreshToken":"fixture-token"}', ["fixture-token"]),
                ("Proxy-Authorization: Digest username=fixture-user, response=fixture-response", ["fixture-user", "fixture-response"])):
            with self.subTest(message=message):
                facts = diagnostics.safe_failure({"detail": "Request rejected. " + message})
                self.assertIn("Request rejected", facts["detail"])
                for secret in secrets:
                    self.assertNotIn(secret, json.dumps(facts))

    def test_gui_timeout_cleanup_cannot_drop_captured_exclusions(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(hub, "ROOT", Path(tmp)), \
                mock.patch.object(hub, "configuration", return_value={"quota_interval_seconds": 3600}):
            state = Path(tmp) / "state"
            state.mkdir()
            import time
            (state / "quota-collection-at").write_text(str(time.time()))
            request_path = state / "quota-refresh-request-v2.json"
            exclusions = {"codex": ["a" * 64]}
            hub._write_quota_message(request_path, {"nonce": "b" * 32, "blocked_accounts": exclusions})
            original = hub._pending_quota_request
            def capture_then_delete():
                request = original()
                request_path.unlink(missing_ok=True)
                return request
            done = threading.Event()
            def query(**kwargs):
                self.assertEqual(kwargs["blocked_accounts"], exclusions)
                done.set()
                return {"codex": {}, "claude": {}}
            with mock.patch.object(hub, "_pending_quota_request", side_effect=capture_then_delete), \
                    mock.patch("agent_monitor.quota_service.rate_limits", side_effect=query) as read, \
                    mock.patch("rollup.run", side_effect=lambda: done.wait(2)):
                hub.collect()
            read.assert_called_once()
            receipt = json.loads((state / "quota-refresh-receipt-v2.json").read_text())
            self.assertEqual(receipt["blocked_accounts"], exclusions)

    def test_passive_pause_without_reading_still_exposes_recovery_target(self):
        from upstream_controls import Controls
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(exporter, "ROOT", Path(tmp)), \
                mock.patch("hub.enabled", return_value=False), \
                mock.patch("agent_monitor.quota_service.accounts.codex_account", return_value=accounts.Account("a", "a@example.test")), \
                mock.patch("agent_monitor.quota_service.accounts.claude_account", return_value=None), \
                mock.patch("agent_monitor.quota_service.codex.load_rate_limits", return_value=None), \
                mock.patch("agent_monitor.quota_service.claude_status.load_rate_limits", return_value=None), \
                mock.patch("agent_monitor.quota_service.quota_refresh.load_rate_limits") as query:
            controls = Controls(Path(tmp) / "state/upstream_controls")
            lease = controls.acquire("codex")
            lease.pause("fixture")
            lease.close()
            block = exporter._rate_limits(refresh=False)["codex"]
            self.assertEqual(block["account_id"], "a")
            self.assertEqual(block["query_state"], "paused")
            self.assertEqual(block["refresh_pause"]["id"], controls.read("codex")["pause"]["id"])
            query.assert_not_called()

    def test_recovery_preflight_binds_id_as_well_as_label_and_rejects_stale_window(self):
        from upstream_controls import Controls
        account = accounts.Account("a", "fixture@example.test")
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(exporter, "ROOT", Path(tmp)), \
                mock.patch("agent_monitor.quota_service.accounts.codex_account", return_value=account), \
                mock.patch("agent_monitor.quota_service.quota_refresh.load_rate_limits", return_value=RateLimits(5, 1, None, None)) as query:
            controls = Controls(Path(tmp) / "state/upstream_controls")
            lease = controls.acquire("codex")
            lease.pause("fixture")
            lease.close()
            pause_id = controls.read("codex")["pause"]["id"]
            with self.assertRaises(quota_refresh.RefreshError):
                exporter.recover_codex_quota(account.label, pause_id, expected_id="wrong")
            query.assert_not_called()
            with self.assertRaises(quota_refresh.RefreshError):
                exporter.recover_codex_quota(account.label, pause_id, expected_id="a")
            self.assertIn("pause", controls.read("codex"))
            self.assertFalse((Path(tmp) / "state/quota_snapshot.json").exists())

    def test_round_unknown_dispatch_stops_later_provider_calls(self):
        machines = [{"name": n, "ssh_host": n, "self": False} for n in ("one", "two")]
        output = io.StringIO()
        # The remote may have called the provider before this timeout.
        with mock.patch("quota_sync.sys.stdin", io.StringIO(json.dumps(machines))), \
                mock.patch("quota_sync.sys.stdout", output), \
                mock.patch("quota_sync.sync_process.watch_parent"), \
                mock.patch("quota_sync.subprocess.run", side_effect=subprocess.TimeoutExpired("fixture", 1)) as remote:
            quota_sync._worker()
        self.assertEqual(remote.call_count, 1)
        rows = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertIn("尚未尝试", rows[1]["error"])

    def test_exclusions_scope_known_unknown_and_local_failures(self):
        fingerprint = "a" * 64
        for state, evidence, expected in (("paused", fingerprint, {"codex": [fingerprint]}),
                ("failed", None, {"codex": ["*"]}), ("local_unavailable", None, {}),
                ("unknown", fingerprint, {"codex": ["*"]})):
            with self.subTest(state=state):
                blocked = {}
                quota_sync.accumulate_exclusions(blocked, {"error": None, "payload": {"rate_limits": {
                    "claude": {}, "codex": {"refresh_error": "fixture", "query_state": state,
                    "refresh_failure": {"account_fingerprint": evidence}}}}})
                self.assertEqual(blocked, expected)

    def test_restricted_gui_request_requires_matching_acknowledgement(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(hub, "ROOT", Path(tmp)), \
                mock.patch.object(hub, "QUOTA_POLL_INTERVAL", .001):
            state = Path(tmp) / "state"
            exclusions = {"codex": ["a" * 64]}
            def respond(_):
                request = json.loads((state / "quota-refresh-request-v2.json").read_text())
                self.assertFalse((state / "quota-refresh-request.json").exists())
                self.assertEqual(request["blocked_accounts"], exclusions)
                hub._write_quota_message(state / "quota-refresh-receipt-v2.json", {
                    **request, "rate_limits": {"codex": {}, "claude": {}}})
            with mock.patch.object(hub, "_kick_quota_collector", side_effect=respond):
                self.assertEqual(hub.request_quota_refresh(timeout=.1, blocked_accounts=exclusions), {"codex": {}, "claude": {}})

    def test_rpc_reason_survives_failure_projection_without_credentials(self):
        error = {"code": -32603, "message": 'request failed body=' + json.dumps({
            "status": 401, "error": {"code": "token_revoked", "message":
            "Session revoked. Authorization: Bearer secret-fixture-token https://host.test/?token=secret-fixture-url"}})}
        facts = diagnostics.safe_failure(quota_refresh._rpc_failure(error, 2))
        self.assertEqual(facts.get("upstream_code"), "token_revoked")
        self.assertIn("Session revoked", facts.get("detail", ""))
        self.assertNotIn("secret-fixture", json.dumps(facts))
        unknown = quota_refresh._rpc_failure({"code": -32603, "message": "connection closed before response"}, 2)
        self.assertIn("connection closed", unknown.get("detail", ""))

    def test_session_reading_identifies_its_source(self):
        stamp = "2026-10-26T06:00:00Z"
        value = codex._build_rate_limits({"secondary": {"used_percent": 2}}, stamp, "fixture", {})
        self.assertEqual(value.quota_observation, {
            "source": "codex_session", "observed_at": stamp, "session_id": "fixture"})

    def test_scoped_failure_does_not_query_same_account_or_block_independent_provider(self):
        with tempfile.TemporaryDirectory() as tmp:
            a = accounts.Account("fixture", "one@example.test")
            b = accounts.Account("other", "two@example.test")
            blocked = {"codex": [quota_refresh.account_fingerprint("codex", a)]}
            with mock.patch.object(exporter, "ROOT", Path(tmp)), mock.patch("hub.enabled", return_value=False), \
                 mock.patch("agent_monitor.quota_service.accounts.codex_account", return_value=a), \
                 mock.patch("agent_monitor.quota_service.accounts.claude_account", return_value=b), \
                 mock.patch("agent_monitor.quota_service.codex.load_rate_limits", return_value=None), \
                 mock.patch("agent_monitor.quota_service.claude_status.load_rate_limits", return_value=None), \
                 mock.patch("agent_monitor.quota_service.quota_refresh.load_rate_limits", side_effect=quota_refresh.NotSignedIn("fixture")) as query:
                result = exporter._rate_limits(refresh=True, blocked_accounts=blocked)
            self.assertEqual([c.args[0] for c in query.call_args_list], ["claude"])
            self.assertEqual(result["codex"]["query_state"], "blocked")


class RecoveryHttpTests(unittest.TestCase):
    def test_origin_identity_busy_and_success_receipt(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager = quota_sync.Manager(Path(tmp) / "receipts.json")
            machine = SimpleNamespace(name="fixture", ssh_host="fixture", is_self=True)
            request = dict(machine="fixture", account_id="a", account_label="a@example.test", pause_id="a" * 32)
            block = {"account_id": "a", "account_label": "a@example.test", "refresh_error": "paused"}
            current = SimpleNamespace(host="fixture", meta={"source_host_identity": "host-fixture", "rate_limits": {"claude": None, "codex": block}})
            admitted = mock.MagicMock()
            admitted.__enter__.return_value = SimpleNamespace(admitted=[current])
            network = {"policy": "chromium-system", "coverage": "recent_connections_only", "events": []}
            receipt = {"ok": True, "block": {**block, "refresh_error": None}, "source_host_identity": "host-fixture", "exporter_commit": "a" * 40, "network": network}
            with mock.patch.object(server, "QUOTA_REFRESH", manager), \
                    mock.patch("server.sync.load_machine_config", return_value=SimpleNamespace(by_name={"fixture": machine})), \
                    mock.patch("server.generation.generation_admission_snapshot", return_value=admitted), \
                    mock.patch("quota_recovery.run", return_value=receipt) as run:
                request_finished = threading.Event()

                class Handler(server.Handler):
                    def finish(self):
                        try:
                            super().finish()
                        finally:
                            request_finished.set()

                httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
                thread = threading.Thread(target=httpd.serve_forever)
                thread.start()
                def post(value=request, origin=None):
                    request_finished.clear()
                    connection = http.client.HTTPConnection("127.0.0.1", httpd.server_port, timeout=3)
                    try:
                        connection.request("POST", "/api/quota-recovery", json.dumps(value), {"Content-Type": "application/json", "X-Agent-Monitor": "action", "Origin": origin or f"http://127.0.0.1:{httpd.server_port}"})
                        response = connection.getresponse()
                        body = json.loads(response.read())
                        # A response can arrive before the handler's finally
                        # clears recovering; finish one scenario before the next.
                        self.assertTrue(request_finished.wait(3), "HTTP handler did not finish")
                        if response.status == 200:
                            self.assertEqual(body["network"], network)
                        return response.status
                    finally:
                        connection.close()
                try:
                    self.assertEqual(post(origin="https://untrusted.test"), 403)
                    self.assertEqual(post({**request, "machine": "unknown"}), 400)
                    self.assertEqual(post({**request, "account_id": "other"}), 409)
                    manager.recovering = True
                    self.assertEqual(post(), 409)
                    manager.recovering = False
                    run.assert_not_called()
                    self.assertEqual(post(), 200)
                    run.assert_called_once()
                    stored = json.loads(manager.path.read_text())
                    self.assertIsNone(stored["machines"]["fixture"]["payload"]["rate_limits"]["codex"]["refresh_error"])
                finally:
                    httpd.shutdown()
                    httpd.server_close()
                    thread.join(2)


if __name__ == "__main__":
    unittest.main()
