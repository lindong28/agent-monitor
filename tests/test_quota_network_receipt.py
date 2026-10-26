"""Recovery receipt observations never choose a new upstream route."""
import dataclasses
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
from urllib.parse import urlsplit

import exporter
import network_policy
import quota_recovery
import quota_refresh
from parsers import RateLimits, accounts
from upstream_controls import Controls


class RecoveryNetworkTests(unittest.TestCase):
    def test_reader_inherits_product_route_for_success_failure_and_missing_evidence(self):
        route = "http://agent-monitor:synthetic-capability@127.0.0.1:45678"
        account = accounts.Account("fixture-account", "fixture@example.test")
        reading = RateLimits(5, 4102444800, 20, 4102444800, updated_at="2026-10-26T00:00:00Z")
        for mode in ("success", "upstream_failure", "unavailable"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as tmp, \
                    mock.patch.dict(os.environ, {"HOME": tmp, "AGENT_MONITOR_PROXY_INTERNAL": route,
                        "HTTPS_PROXY": "http://127.0.0.1:49999", "NO_PROXY": "*"}, clear=True), \
                    mock.patch.object(exporter, "ROOT", Path(tmp)), \
                    mock.patch.object(quota_refresh, "ROOT", Path(tmp)), \
                    mock.patch("exporter.accounts.codex_account", return_value=account), \
                    mock.patch("exporter.exporter_version", return_value="a" * 40), \
                    mock.patch("generation.self_certified_host_identity", return_value="fixture-host"):
                controls = Controls(Path(tmp) / "state/upstream_controls")
                lease = controls.acquire("codex")
                lease.pause("fixture")
                lease.close()
                pause_id = controls.read("codex")["pause"]["id"]
                request = dict(machine="fixture", account_id=account.account_id,
                               account_label=account.label, pause_id=pause_id)
                payload = {"error": "fixture rejection"} if mode == "upstream_failure" else {
                    "reading": dataclasses.asdict(reading)}
                child = SimpleNamespace(pid=0, returncode=0,
                                        communicate=lambda *a, **k: (json.dumps(payload), ""))
                facts = {"policy": "chromium-system", "coverage": "unavailable", "events": []}
                if mode != "unavailable":
                    facts = {"policy": "chromium-system", "coverage": "recent_connections_only",
                             "events": [{"target": "https://upstream.example.test",
                                         "route": "PROXY 127.0.0.1:12345", "result": "connected"}]}
                normal_env = network_policy.child_environment(os.environ)
                with mock.patch("quota_refresh.subprocess.Popen", return_value=child) as spawn, \
                        mock.patch("network_policy.observations", return_value=facts) as observations:
                    receipt = quota_recovery.local_recovery(request)
                self.assertEqual(receipt["ok"], mode != "upstream_failure")
                self.assertEqual(receipt["network"], facts)
                spawn.assert_called_once()
                env = spawn.call_args.kwargs["env"]
                scoped = observations.call_args.args[0]
                self.assertEqual(env["HTTPS_PROXY"], scoped)
                self.assertEqual(env["NO_PROXY"], "")
                before, after = urlsplit(normal_env["HTTPS_PROXY"]), urlsplit(scoped)
                self.assertEqual((before.hostname, before.port, before.password),
                                 (after.hostname, after.port, after.password))
                self.assertRegex(after.username, r"^agent-monitor\.[a-f0-9]{32}$")
                self.assertEqual(network_policy.endpoint(), route, "operation scope must not leak")
                self.assertNotIn("synthetic-capability", json.dumps(receipt))
                self.assertEqual("pause" in controls.read("codex"), mode == "upstream_failure")

    def test_missing_runtime_does_not_start_reader_or_consume_probe(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(exporter, "ROOT", Path(tmp)), \
                mock.patch("exporter.exporter_version", return_value="a" * 40), \
                mock.patch("network_policy.endpoint", side_effect=network_policy.NetworkPolicyError("runtime_missing")), \
                mock.patch("exporter.recover_codex_quota") as reader:
            receipt = quota_recovery.local_recovery(dict(machine="fixture", account_id="fixture",
                account_label="fixture@example.test", pause_id="a" * 32))
            reader.assert_not_called()
            self.assertFalse(receipt["ok"])
            self.assertEqual(receipt["network"]["coverage"], "unavailable")


if __name__ == "__main__":
    unittest.main()
