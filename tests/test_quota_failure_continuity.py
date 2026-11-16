"""Normal reader failure -> durable pause -> recovery history; no provider access."""
import dataclasses
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from agent_monitor.accounts.upstream_controls import Controls, recovery_receipt
from agent_monitor.quota import quota_refresh
from agent_monitor.network import network_policy
from parsers import accounts, RateLimits


class FailureContinuityTests(unittest.TestCase):
    def test_batch_facts_survive_pause_but_are_not_account_observations(self):
        for mode in ("failed", "unavailable", "success", "unscoped"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                route = "http://agent-monitor.auto-" + "a" * 32 + ":SECRET_ROUTE@127.0.0.1:9"
                env = {network_policy.INTERNAL: route}
                batch = SimpleNamespace(environment=lambda: env, check=lambda: None)
                failure = {"stage": "quota_read", "rpc_code": -32603, "kind": "rpc_error",
                           "client_diagnostics": "synthetic TLS failure", "client_version": "fixture/0.167.0"}
                payload = {"error": "fixture transport failure", "failure": failure}
                if mode == "success":
                    payload = {"reading": dataclasses.asdict(RateLimits(5, None, 10, None))}
                peer = SimpleNamespace(returncode=0, pid=0, communicate=lambda *a, **k: (json.dumps(payload), ""))
                observations = {"coverage": "recent_connections_only", "events": [
                    {"target": "https://user:SECRET_URL@fixture.test/path?token=SECRET_QUERY",
                     "route": "PROXY 127.0.0.1:8000", "result": "connected"}]}
                if mode == "unavailable":
                    observations = {"coverage": "unavailable"}
                if mode == "unscoped":
                    batch = None
                    env[network_policy.INTERNAL] = "http://agent-monitor:SECRET_ROUTE@127.0.0.1:9"
                with mock.patch.object(accounts, "CODEX_AUTH", root / "auth.json"), \
                     mock.patch.object(quota_refresh.subprocess, "Popen", return_value=peer), \
                     mock.patch.object(network_policy, "child_environment", return_value=env), \
                     mock.patch.object(network_policy, "observations", return_value=observations) as observe:
                    if mode == "success":
                        value = quota_refresh.load_rate_limits("codex", accounts.Account("fixture", "f@example.test"), root=root, automatic_batch=batch)
                        self.assertEqual(value.five_hour_pct, 5)
                        observe.assert_not_called()
                        self.assertNotIn("pause", Controls(root / "state/upstream_controls").read("codex"))
                        continue
                    with self.assertRaises(quota_refresh.RefreshError):
                        quota_refresh.load_rate_limits("codex", accounts.Account("fixture", "f@example.test"), root=root, automatic_batch=batch)
                facts = Controls(root / "state/upstream_controls").read("codex")["pause"]["failure"]
                self.assertEqual(facts["client_diagnostics"], "synthetic TLS failure")
                self.assertEqual(facts["client_version"], "fixture/0.167.0")
                self.assertNotIn("SECRET", json.dumps(facts))
                if mode == "unscoped":
                    observe.assert_not_called()
                    self.assertEqual(facts["network"]["coverage"], "unavailable")
                else:
                    observe.assert_called_once_with(route)
                    self.assertEqual(facts["network"]["scope"], "automatic_batch")
                    self.assertEqual(facts["network"]["coverage"], observations["coverage"])
                if mode == "failed":
                    self.assertEqual(facts["network"]["events"][0]["target"], "https://fixture.test")

    def test_old_receipt_only_recovers_matching_original_pause_without_writes(self):
        receipt = {"ok": True, "request": {"pause_id": "original"}, "failure": {}}
        for pause_id in ("original", "later", None):
            state = {"last_recovery": receipt, "last_pause": {"id": pause_id,
                     "failure": {"detail": "old cause"}, "at": 123}}
            before = json.dumps(state)
            result = recovery_receipt(state)
            self.assertEqual("original_pause" in result, pause_id == "original")
            self.assertEqual(json.dumps(state), before)
            self.assertEqual(result["failure"], {})


if __name__ == "__main__":
    unittest.main()
