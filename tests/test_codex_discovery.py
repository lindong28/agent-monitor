"""Known metadata reaches real HTTP actions without manual additions or provider access."""
import contextlib
import http.client
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest import mock

import codex_accounts as ca
import server


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.manager = ca.Manager(self.root / "profiles")
        self.memory = self.root / "account_memory.json"
        self.currents = []
        self.scenarios = {}
        original = subprocess.Popen

        def spawn(command, **kwargs):
            directory = Path(kwargs["env"]["CODEX_HOME"]).parent
            record = self.manager.read(directory)
            kwargs["env"].update(FAKE_EMAIL=record["email"], FAKE_ACCOUNT=record["account_id"],
                                 FAKE_SCENARIO=self.scenarios.get(record["email"], "success"))
            return original([sys.executable, str(Path(__file__).parent / "fixtures/codex_account_server.py")], **kwargs)

        @contextlib.contextmanager
        def admission():
            yield SimpleNamespace(admitted=self.currents, records=(SimpleNamespace(machine=SimpleNamespace(name="local")),))

        for patch in (mock.patch.object(ca, "manager", self.manager),
                      mock.patch.object(ca.subprocess, "Popen", side_effect=spawn),
                      mock.patch.object(server, "_ACCOUNT_MEMORY_PATH", self.memory),
                      mock.patch.object(server.generation, "generation_admission_snapshot", admission)):
            patch.start()
            self.addCleanup(patch.stop)
        self.http = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        thread = threading.Thread(target=self.http.serve_forever, daemon=True)
        thread.start()
        def close():
            self.http.shutdown()
            self.http.server_close()
            thread.join(2)
            self.manager.close()
        self.addCleanup(close)

    def source(self, workspace, email, host="local"):
        self.currents.append(SimpleNamespace(host=host, meta={"rate_limits": {"codex": {
            "account_id": workspace, "account_label": email, "account_state": "known",
            "updated_at": "2026-10-07T00:00:00Z", "signed_out": False}}}))

    def history(self, identities):
        records = {}
        for workspace, email in identities:
            record = {"provider": "codex", "account_id": workspace, "account_label": email,
                      "account_plan": None, "observed_at": "2026-10-06T00:00:00+00:00",
                      **{name: None for name in server._ACCOUNT_MEMORY_NUMERIC_FIELDS}}
            records[server._account_key("codex", record)] = record
        self.memory.write_text(json.dumps({"version": 2, "accounts": records}))

    def request(self, action="", payload=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.http.server_port, timeout=3)
        try:
            conn.request("POST" if payload is not None else "GET", "/api/codex-accounts" + action,
                         body=json.dumps(payload) if payload is not None else None,
                         headers={"X-Agent-Monitor": "codex-accounts", "Content-Type": "application/json",
                                  "Origin": f"http://127.0.0.1:{self.http.server_port}"})
            response = conn.getresponse()
            return response.status, json.loads(response.read())
        finally:
            conn.close()

    def wait(self):
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            if not any(a["busy"] for a in self.manager.list()["accounts"]):
                return
            time.sleep(.02)
        self.fail("operations did not finish")

    def calls(self, account):
        path = self.manager.directory(account["id"]) / "calls.jsonl"
        return sum(json.loads(line).get("method") == "turn/start" for line in path.read_text().splitlines()) if path.exists() else 0

    def test_history_and_current_need_zero_manual_additions_and_send_once(self):
        self.source("shared", "one@example.com")
        self.source("shared", "ONE@example.com", "second-machine")
        self.source("shared", "member@example.com", "third-machine")
        self.history([("shared", "one@example.com"), ("other", "one@example.com"), ("past", "past@example.com")])
        before = self.memory.read_bytes()
        code, data = self.request()
        self.assertEqual(code, 200)
        self.assertEqual(len(data["accounts"]), 4)
        self.assertTrue(all(a["eligible"] for a in data["accounts"]))
        self.assertEqual(self.memory.read_bytes(), before)
        self.assertTrue(all(self.calls(a) == 0 for a in data["accounts"]))
        self.assertEqual(self.request("/batch-start", {"expected_batch_id": None})[0], 200)
        self.wait()
        self.request("/batch-start", {"expected_batch_id": None})
        self.request()
        self.assertTrue(all(self.calls(a) == 1 for a in data["accounts"]))

    def test_removal_excludes_next_round_and_retry_but_keeps_results(self):
        self.source("live", "live@example.com")
        self.history([("past", "past@example.com")])
        self.scenarios["past@example.com"] = "wait_login"
        data = self.request()[1]
        past = next(a for a in data["accounts"] if a["email"] == "past@example.com")
        batch = self.request("/batch-start", {"expected_batch_id": None})[1]["batch"]
        deadline = time.monotonic() + 5
        while self.manager.view(self.manager.directory(past["id"]))["operation"]["stage"] != "login":
            self.assertLess(time.monotonic(), deadline)
            time.sleep(.02)
        self.request("/cancel", {"id": past["id"]})
        self.wait()
        self.history([])
        data = self.request()[1]
        self.assertFalse(next(a for a in data["accounts"] if a["id"] == past["id"])["eligible"])
        self.assertEqual(len(data["batch"]["items"]), 2)
        self.request("/batch-retry", {"batch_id": batch["id"]})
        self.assertEqual(self.calls(past), 0)
        self.assertEqual(self.request("/start", {"id": past["id"]})[0], 409)
        next_batch = self.request("/batch-start", {"expected_batch_id": batch["id"]})[1]["batch"]
        self.wait()
        self.assertEqual(len(next_batch["items"]), 1)
        self.assertEqual(self.calls(past), 0)

    def test_missing_email_is_visible_and_unknown_machine_is_not_an_account(self):
        self.source("no-email", None)
        self.source(None, None, "unknown-machine")
        code, data = self.request()
        self.assertEqual(code, 200)
        self.assertEqual(data["accounts"], [])
        self.assertEqual([a["account_id"] for a in data["unavailable_accounts"]], ["no-email"])
        self.assertEqual(self.request("/batch-start", {"expected_batch_id": None})[0], 400)

    def test_identity_does_not_require_a_quota_timestamp(self):
        self.source("fresh-login", "fresh@example.com")
        block = self.currents[0].meta["rate_limits"]["codex"]
        for observed in (None, "not-a-timestamp", "2026-10-07T00:00:00Z"):
            block["updated_at"] = observed
            code, data = self.request()
            self.assertEqual(code, 200)
            self.assertEqual([a["account_id"] for a in data["accounts"]], ["fresh-login"])
        block.pop("updated_at")
        self.assertEqual(len(self.request()[1]["accounts"]), 1)

    def test_unreadable_history_fails_visibly_without_dispatch(self):
        self.source("live", "live@example.com")
        account = self.request()[1]["accounts"][0]
        self.memory.write_text("not json")
        for action, payload in (("", None), ("/batch-start", {"expected_batch_id": None}),
                                ("/batch-retry", {"batch_id": "old"})):
            self.assertEqual(self.request(action, payload)[0], 503)
        self.assertEqual(self.request("/forget-login", {"id": account["id"]})[0], 200)
        self.assertEqual(self.calls(account), 0)


if __name__ == "__main__":
    unittest.main()
