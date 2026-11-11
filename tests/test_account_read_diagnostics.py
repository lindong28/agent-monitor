"""Read failures through a synthetic stdio peer, with persisted/API readback."""
import http.client
import json
import threading
import time
import unittest
from unittest import mock

from agent_monitor.accounts import codex_accounts as ca
from agent_monitor import http_server as server
from tests import test_codex_accounts as actions


class AccountReadDiagnosticsTests(unittest.TestCase):
    setUp = actions.AccountActionsTests.setUp
    account = actions.AccountActionsTests.account
    wait = actions.AccountActionsTests.wait
    calls = actions.AccountActionsTests.calls
    run_case = actions.AccountActionsTests.run_case
    acknowledge = actions.AccountActionsTests.acknowledge

    def test_quota_rpc_failure_retains_safe_facts_without_widening_attribution(self):
        self.scenario = "quota_rpc_diagnostic"
        account = self.account()
        self.manager.start(account["id"], refresh_only=True)
        result = self.wait(account["id"])
        facts = result["operation"]["diagnostics"].get("rpc_failure", {})
        self.assertEqual(facts.get("rpc_code"), -32603)
        self.assertEqual(facts.get("http_status"), 401)
        self.assertEqual(facts.get("upstream_code"), "token_revoked")
        self.assertIn("session expired", facts.get("detail", ""))
        self.assertNotIn("SECRET_FIXTURE_TOKEN", json.dumps(result))
        self.assertNotIn("CLI 版本", result["operation"]["detail"])
        pause = self.manager.list()["upstream_pause"]
        self.assertEqual(pause["failure"].get("upstream_code"), "token_revoked")
        self.assertNotIn("authentication", result.get("restrictions", {}))
        self.assertNotIn("turn/start", [c.get("method") for c in self.calls(result)])
        self.manager.close()
        self.manager = ca.Manager(self.manager.root)
        self.addCleanup(self.manager.close)
        self.assertEqual(self.manager.view(self.manager.directory(account["id"]))["last_failure"]["diagnostics"]["rpc_failure"], facts)

    def test_local_network_failure_does_not_pause_provider_or_start_cli(self):
        with mock.patch.object(ca.network_policy, 'endpoint', side_effect=ca.network_policy.NetworkPolicyError('runtime_missing')):
            result = self.run_case('success')
        self.assertEqual(result['operation']['failure']['category'], 'networkPolicyUnavailable')
        self.assertEqual(result['operation']['message_status'], 'not_sent')
        self.assertEqual(len(self.spawned), 0)
        self.assertIsNone(self.manager.list()['upstream_pause'])

    def test_network_connection_facts_survive_restart_with_original_failure(self):
        facts = {"policy": "chromium-system", "coverage": "recent_connections_only", "events": [
            {"target": "https://fixture.invalid", "route": "PROXY 127.0.0.1:9", "result": "proxy_connect_failed"}]}
        with mock.patch.object(ca.network_policy, "observations", return_value=facts):
            result = self.run_case("account_read_transport")
        self.manager.close()
        self.manager = ca.Manager(self.manager.root)
        self.addCleanup(self.manager.close)
        restored = self.manager.view(self.manager.directory(result["id"]))
        self.assertEqual(restored["last_failure"]["diagnostics"]["network"], facts)

    def test_known_reason_survives_restart_resume_and_replacement(self):
        for scenario, reason, wording in (
            ("account_read_auth", "discoveryUnauthorized", "认证"),
            ("account_read_workspace", "missingWorkspace", "工作区"),
            ("account_read_transport", "discoveryFailed", "具体原因"),
            ("account_read_timeout", "discoveryTimeout", "超时"),
        ):
            with self.subTest(scenario=scenario):
                spawned_before = len(self.spawned)
                result = self.run_case(scenario)
                op = result["operation"]
                self.assertEqual(op["diagnostics"].get("rpc_error_reason"), reason)
                self.assertEqual(op["diagnostics"]["rpc_error_code"], -32603)
                self.assertIn(wording, op["detail"])
                self.assertIn("-32603", op["detail"])
                self.assertEqual(op["message_status"], "not_sent")
                self.assertEqual(op["failure"], {"stage": "checking", "category": "unknown"})
                methods = [c.get("method") for c in self.calls(result)]
                self.assertEqual(methods.count("account/read"), 1)
                self.assertNotIn("account/rateLimits/read", methods)
                self.assertNotIn("turn/start", methods)
                self.assertIsNotNone(self.manager.list()["upstream_pause"])
                self.assertEqual(len(self.spawned), spawned_before + 1)
                self.manager.close()
                self.manager = ca.Manager(self.manager.root)
                self.addCleanup(self.manager.close)
                restored = self.manager.view(self.manager.directory(result["id"]))
                self.assertEqual(restored["operation"], op)
                self.acknowledge(restored)
                self.scenario = "success"
                self.manager.start(result["id"], refresh_only=True)
                current = self.wait(result["id"])
                self.assertEqual(current["operation"]["stage"], "succeeded")
                self.assertEqual(current["last_failure"], op)

    def test_unknown_and_malformed_errors_keep_secrets_out_of_persisted_results(self):
        for scenario in ("account_read_unsafe", "account_read_malformed"):
            with self.subTest(scenario=scenario):
                result = self.run_case(scenario)
                op = result["operation"]
                self.assertEqual(op["diagnostics"].get("rpc_error_reason"), "unrecognized")
                self.assertIn("具体原因未识别", op["detail"])
                self.assertNotIn("CLI 版本", op["detail"])
                persisted = (self.manager.directory(result["id"]) / "profile.json").read_text()
                self.assertNotIn("SECRET_FIXTURE_TOKEN", persisted + json.dumps(self.manager.list()))
                if scenario == "account_read_unsafe":
                    # A URL without credentials is useful diagnostic context.
                    self.assertIn("https://secret.example/path", op["diagnostics"]["rpc_failure"]["detail"])

    def test_failure_reason_is_readable_through_account_http_api(self):
        result = self.run_case("account_read_workspace")
        catalog = [{"account_state": "known", "account_label": result["email"], "account_id": result["account_id"]}]
        with mock.patch.object(ca, "manager", self.manager), mock.patch.object(server.QUOTA_VIEW, "action_catalog", return_value=catalog):
            http_server = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
            thread = threading.Thread(target=http_server.serve_forever, daemon=True)
            thread.start()
            connection = http.client.HTTPConnection("127.0.0.1", http_server.server_port, timeout=3)
            try:
                connection.request("GET", "/api/codex-accounts", headers={"X-Agent-Monitor": "codex-accounts"})
                response = connection.getresponse()
                body = json.loads(response.read())
                self.assertEqual(response.status, 200)
                account = next(a for a in body["accounts"] if a["id"] == result["id"])
                self.assertEqual(account["operation"]["diagnostics"].get("rpc_error_reason"), "missingWorkspace")
                self.assertEqual(account["last_failure"], account["operation"])
            finally:
                connection.close()
                http_server.shutdown()
                http_server.server_close()
                thread.join(2)

    def test_only_exact_message_and_integer_internal_error_are_classified(self):
        text = "selected workspace missing from routing discovery"
        errors = (
            {"code": -32601, "message": text},
            {"code": -32603.0, "message": text},
            {"code": "-32603", "message": text},
            {"code": -32603, "message": text + " SECRET_FIXTURE_TOKEN"},
            {"code": -32603, "message": {"secret": "SECRET_FIXTURE_TOKEN"}},
            None,
        )
        for error in errors:
            with self.subTest(error=error):
                rpc = ca.Rpc.__new__(ca.Rpc)
                rpc.sequence, rpc.pending, rpc.diagnostics = 0, [], {}
                rpc.auth_path = self.root / "synthetic-auth-not-present.json"
                rpc.redaction_values = ("SECRET_FIXTURE_TOKEN",)
                rpc.send = mock.Mock()
                rpc.receive = mock.Mock(return_value={"id": 1, "error": error})
                with self.assertRaises(ca.ActionError) as raised:
                    rpc.request("account/read", {"refreshToken": False}, time.monotonic() + 5)
                self.assertEqual(rpc.diagnostics["rpc_error_reason"], "unrecognized")
                self.assertNotIn("SECRET_FIXTURE_TOKEN", str(raised.exception) + json.dumps(rpc.diagnostics))

    def test_decoded_errors_redact_old_and_rotated_credentials(self):
        old, new = "SECRET_FIXTURE_TOKEN", "NEXT_FIXTURE_TOKEN"
        for method in ("account/read", "account/rateLimits/read"):
            for encoded in (False, True):
                with self.subTest(method=method, encoded=encoded):
                    body = json.dumps({"status": 401, "error": {
                        "code": old, "message": "rotation failed " + old + " " + new}})
                    if encoded:
                        for secret in (old, new):
                            body = body.replace(secret, "".join("\\u%04x" % ord(c) for c in secret))
                    rpc = ca.Rpc.__new__(ca.Rpc)
                    rpc.sequence, rpc.pending, rpc.diagnostics = 0, [], {}
                    rpc.auth_path = self.root / "synthetic-auth-not-present.json"
                    rpc.redaction_values = (old,)
                    rpc.send = mock.Mock()
                    rpc.receive = mock.Mock(return_value={"id": 1, "error": {
                        "code": -32603, "message": "request failed body=" + body}})
                    with mock.patch.object(ca.codex_quota, "credential_secrets", return_value=(new,)):
                        with self.assertRaises(ca.ActionError):
                            rpc.request(method, {}, time.monotonic() + 5)
                    rpc.send.assert_called_once()
                    facts = rpc.diagnostics["rpc_failure"]
                    self.assertIn("rotation failed", facts["detail"])
                    for secret in (old, new):
                        self.assertNotIn(secret, json.dumps(facts))


if __name__ == "__main__":
    unittest.main()
