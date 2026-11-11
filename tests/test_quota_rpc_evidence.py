"""RPC diagnostic projection: synthetic input only, no provider discovery."""
import json
import unittest
from agent_monitor import diagnostics
from agent_monitor.quota.codex_quota import rpc_failure


class RpcEvidenceTests(unittest.TestCase):
    def test_message_and_unknown_data_survive_failure_persistence(self):
        error = {"code": -32603,
                 "message": 'failed to fetch codex rate limits: GET https://example.test/usage failed; body={"status":503,"error":{"message":"upstream busy"}}',
                 "data": {"request_id": "fixture-request", "unknown_cause": {"region": "example"},
                          "access_token": "fixture-opaque", "escaped": "fixture-opaque"}}
        facts = rpc_failure(error, secrets=("fixture-opaque",))
        self.assertIn("GET https://example.test/usage failed", facts.get("rpc_message", ""))
        self.assertIn("fixture-request", facts.get("rpc_data", ""))
        self.assertIn("unknown_cause", facts["rpc_data"])
        self.assertNotIn("fixture-opaque", json.dumps(facts))
        self.assertFalse(facts["rpc_message_truncated"])
        self.assertFalse(facts["rpc_data_truncated"])
        restored = diagnostics.safe_failure(json.loads(json.dumps(facts)))
        self.assertEqual(restored, facts)
        self.assertIn("fixture-request", diagnostics.failure_summary(restored))

    def test_absent_null_and_truncation_are_distinguishable(self):
        absent = rpc_failure({"code": -32603, "message": "short"})
        null = rpc_failure({"code": -32603, "message": "x" * 3000, "data": None})
        self.assertNotIn("rpc_data", absent)
        self.assertEqual(null.get("rpc_data"), "null")
        self.assertTrue(null.get("rpc_message_truncated"))
        self.assertLessEqual(len(null["rpc_message"]), 2048)
        large = rpc_failure({"code": -32603, "data": {"body": "v" * 3000}})
        self.assertTrue(large.get("rpc_data_truncated"))
        self.assertLessEqual(len(large["rpc_data"]), 2048)

    def test_malformed_body_and_syntax_credentials_keep_context(self):
        facts = rpc_failure({"code": -32603, "message": "context body=broken", "data": {
            "Authorization": "Bearer secret-header", "url": "https://host.test/?access_token=secret-url",
            "unexpected": "retain me"}})
        encoded = json.dumps(facts)
        self.assertIn("body=broken", facts.get("rpc_message", ""))
        self.assertIn("retain me", facts.get("rpc_data", ""))
        self.assertNotIn("secret-header", encoded)
        self.assertNotIn("secret-url", encoded)


if __name__ == "__main__":
    unittest.main()
