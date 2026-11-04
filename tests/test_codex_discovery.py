"""Known metadata reaches real HTTP actions without manual additions or provider access."""
from agent_monitor.quota import account_memory
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

from agent_monitor.accounts import codex_accounts as ca
from agent_monitor.network import client_network
from agent_monitor import http_server as server
from tests.test_codex_accounts import seed_auth


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        for patch in (mock.patch.object(ca, "START_INTERVAL_SECONDS", 0),
                      mock.patch.object(ca.shutil, "which", return_value=sys.executable)):
            patch.start()
            self.addCleanup(patch.stop)
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        home_patch = mock.patch.object(ca.Path, "home", return_value=self.root / "home")
        home_patch.start()
        self.addCleanup(home_patch.stop)
        self.manager = ca.Manager(self.root / "profiles")
        self.hub = client_network.Hub()
        self.addCleanup(self.hub.close)
        self.route = self.hub.register("fixture-browser-device")
        self.connection = {"id": self.route.id, "capability": self.route.browser}
        self.memory = self.root / "agent_monitor.account_memory.json"
        self.currents = []
        self.scenarios = {}
        original = subprocess.Popen

        def spawn(command, **kwargs):
            self.assertEqual(command[:4], [sys.executable, "app-server", "--listen", "stdio://"])
            directory = Path(kwargs["env"]["CODEX_HOME"]).parent
            record = self.manager.read(directory)
            kwargs["env"].update(FAKE_EMAIL=record["email"], FAKE_ACCOUNT=record["account_id"],
                                 FAKE_SCENARIO=self.scenarios.get(record["email"], "success"))
            return original([sys.executable, str(Path(__file__).parent / "fixtures/codex_account_server.py")], **kwargs)

        @contextlib.contextmanager
        def admission():
            yield SimpleNamespace(admitted=self.currents, records=(SimpleNamespace(machine=SimpleNamespace(name="local")),))

        for patch in (mock.patch.object(ca, "manager", self.manager),
                      mock.patch.object(client_network, "hub", self.hub),
                      mock.patch.object(ca.subprocess, "Popen", side_effect=spawn),
                      mock.patch.object(server.ACCOUNT_MEMORY, "path", self.memory),
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
                      **{name: None for name in account_memory.NUMERIC_FIELDS}}
            records[account_memory.account_key("codex", record)] = record
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

    def batch_payload(self, expected_batch_id, profile_ids=None):
        payload = {"expected_batch_id": expected_batch_id, "connection": self.connection}
        if profile_ids is None:
            status, data = self.request()
            self.assertEqual(status, 200)
            profile_ids = [a["id"] for a in data["accounts"] if a["eligible"]]
        else:
            payload["profile_ids"] = profile_ids
        payload["confirmation"] = self.route.issue(profile_ids, expected_batch_id)
        return payload

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

    def methods(self, account):
        path = self.manager.directory(account["id"]) / "calls.jsonl"
        return [json.loads(line).get("method") for line in path.read_text().splitlines()] if path.exists() else []

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
        # Discovery only imports identity metadata. Sending requires separately
        # saved credentials; these are synthetic fixture tokens, never real login.
        for account in data["accounts"]:
            seed_auth(self.manager, account)
        self.assertEqual(self.request("/batch-start", self.batch_payload(None))[0], 200)
        self.wait()
        self.assertEqual(self.request("/batch-start", self.batch_payload(None))[0], 200)
        self.request()
        self.assertTrue(all(self.calls(a) == 1 for a in data["accounts"]))

    def test_selected_rounds_send_only_selected_accounts_and_reject_invalid_selection(self):
        self.source("live", "live@example.com")
        self.history([("past", "past@example.com")])
        accounts = self.request()[1]["accounts"]
        for account in accounts:
            seed_auth(self.manager, account)
        first, second = accounts
        status, data = self.request("/batch-start", self.batch_payload(None, [first["id"]]))
        self.assertEqual(status, 200)
        self.wait()
        self.assertEqual(self.calls(first), 1)
        self.assertEqual(self.calls(second), 0)
        batch_id = data["batch"]["id"]
        self.assertEqual([item["profile_id"] for item in data["batch"]["items"]], [first["id"]])
        # A lost-response replay cannot widen or repeat the accepted round.
        self.assertEqual(self.request("/batch-start", self.batch_payload(None, [second["id"]]))[0], 200)
        self.assertEqual(self.calls(second), 0)
        for selection in ([], None, "all", [first["id"], first["id"]], [123], [{}], ["missing"], [first["id"], "missing"]):
            with self.subTest(selection=selection):
                status, _ = self.request("/batch-start", {"expected_batch_id": batch_id, "profile_ids": selection})
                self.assertIn(status, (400, 409))
                self.assertEqual(self.manager.read_batch()["id"], batch_id)
                self.assertEqual(self.calls(first), 1)
                self.assertEqual(self.calls(second), 0)
        status, _ = self.request("/batch-start", self.batch_payload(batch_id, [first["id"], second["id"]]))
        self.assertEqual(status, 200)
        self.wait()
        self.assertEqual(self.calls(first), 2)
        self.assertEqual(self.calls(second), 1)

    def test_refresh_all_rejects_stale_or_partial_scope_without_provider_call(self):
        self.source("one-workspace", "one@example.com")
        self.source("two-workspace", "two@example.com")
        accounts = self.request()[1]["accounts"]
        for account in accounts:
            seed_auth(self.manager, account)
        ids = [account["id"] for account in accounts]
        payload = {"profile_ids": ids[:1], "connection": self.connection, "approval_expected": None,
                   "confirmation": self.route.issue(ids[:1], None, "refresh-all")}
        status, _ = self.request("/refresh-all", payload)
        self.assertEqual(status, 409)
        self.assertTrue(all(not self.methods(account) for account in accounts))

    def test_quota_subset_and_retry_bind_scope_revision_and_disabled_state(self):
        for i in range(3):
            self.source(f"workspace-{i}", f"account{i}@example.test")
        rows = sorted(self.request()[1]["accounts"], key=lambda a: a["id"])
        for row in rows:
            seed_auth(self.manager, row)
        ids = [row["id"] for row in rows]
        self.scenarios[rows[1]["email"]] = "account_read_timeout"
        payload = {"profile_ids": ids, "connection": self.connection, "approval_expected": None,
                   "confirmation": self.route.issue(ids, None, "refresh-selected")}
        self.assertEqual(self.request("/refresh-selected", payload)[0], 200)
        self.wait()
        data = self.request()[1]
        failed = next(a for a in data["accounts"] if a["id"] == ids[1])
        local = {"id": ids[1], "operation_id": failed["operation"]["id"], "disabled": True}
        self.assertEqual(self.request("/set-disabled", local)[0], 200)
        self.assertEqual(self.request("/attribute-account-pause", {**local, "pause_id": data["upstream_pause"]["id"]})[0], 200)
        revision = data["quota_batch"]["revision"]
        retry = {"profile_ids": ids[2:], "connection": self.connection, "approval_expected": revision,
                 "confirmation": self.route.issue(ids[2:], revision, "refresh-retry")}
        self.assertEqual(self.request("/refresh-retry", {**retry, "profile_ids": ids})[0], 409)
        self.assertEqual(self.request("/refresh-retry", retry)[0], 200)
        self.wait()
        self.assertEqual(self.request("/refresh-retry", retry)[0], 409)
        methods = [self.methods(row) for row in rows]
        self.assertEqual([m.count("account/rateLimits/read") for m in methods], [1, 0, 1])
        self.assertTrue(all("turn/start" not in m for m in methods))
    def test_refresh_all_http_action_consumes_route_and_dispatches_current_scope(self):
        self.source("one-workspace", "one@example.com")
        self.source("two-workspace", "two@example.com")
        status, data = self.request()
        self.assertEqual(status, 200)
        for account in data["accounts"]:
            seed_auth(self.manager, account)
        ids = [account["id"] for account in data["accounts"]]
        expected = {"accounts": [], "message": "fixture"}
        with mock.patch.object(self.manager, "start_refresh_batch", return_value=expected) as start:
            payload = {"profile_ids": ids, "connection": self.connection, "approval_expected": None,
                       "confirmation": self.route.issue(ids, None, "refresh-all")}
            status, result = self.request("/refresh-all", payload)
        self.assertEqual(status, 200)
        self.assertEqual(result, {**expected, "unavailable_accounts": []})
        start.assert_called_once_with(sorted(ids), network_route=self.route, retry_revision=None, expected_revision=None)

    def test_refresh_all_uses_current_login_ready_scope_and_rejects_stale_selection(self):
        self.source("ready-workspace", "ready@example.com")
        self.source("expired-workspace", "expired@example.com")
        accounts = self.request()[1]["accounts"]
        for account in accounts:
            seed_auth(self.manager, account)
        expired = next(account for account in accounts if account["email"] == "expired@example.com")
        ready = next(account for account in accounts if account["email"] == "ready@example.com")
        directory = self.manager.directory(expired["id"])
        record = self.manager.read(directory)
        record["login_required"] = True
        self.manager.save(directory, record)
        payload = {"profile_ids": [ready["id"]], "connection": self.connection, "approval_expected": None,
                   "confirmation": self.route.issue([ready["id"]], None, "refresh-all")}
        with mock.patch.object(self.manager, "start_refresh_batch", return_value={}) as start:
            self.assertEqual(self.request("/refresh-all", payload)[0], 200)
            start.assert_called_once_with([ready["id"]], network_route=self.route, retry_revision=None, expected_revision=None)
        record.pop("login_required")
        self.manager.save(directory, record)
        payload["confirmation"] = self.route.issue([ready["id"]], None, "refresh-all")
        with mock.patch.object(self.manager, "start_refresh_batch") as start:
            self.assertEqual(self.request("/refresh-all", payload)[0], 409)
            start.assert_not_called()
        self.assertTrue(all(not self.methods(account) for account in accounts))

    def test_removal_excludes_next_round_and_retry_but_keeps_results(self):
        self.source("live", "live@example.com")
        self.history([("past", "past@example.com")])
        data = self.request()[1]
        past = next(a for a in data["accounts"] if a["email"] == "past@example.com")
        live = next(a for a in data["accounts"] if a["email"] == "live@example.com")
        seed_auth(self.manager, live)
        batch = self.request("/batch-start", self.batch_payload(None))[1]["batch"]
        self.wait()
        # The unsigned historical account remains definitely unsent, eligible
        # for retry until its source identity is removed.
        result = self.manager.view(self.manager.directory(past["id"]))
        self.assertEqual(result["operation"]["failure"]["category"], "needsLogin")
        self.assertEqual(self.calls(live), 1)
        self.history([])
        data = self.request()[1]
        self.assertFalse(next(a for a in data["accounts"] if a["id"] == past["id"])["eligible"])
        self.assertEqual(len(data["batch"]["items"]), 2)
        self.assertEqual(self.request("/batch-retry", {"batch_id": batch["id"], "connection": self.connection,
                                                         "confirmation": self.route.issue([past["id"]], batch["id"], "batch-retry")})[0], 200)
        self.assertEqual(self.calls(past), 0)
        self.assertEqual(self.request("/start", {"id": past["id"]})[0], 409)
        next_batch = self.request("/batch-start", self.batch_payload(batch["id"]))[1]["batch"]
        self.wait()
        self.assertEqual(len(next_batch["items"]), 1)
        self.assertEqual(self.calls(past), 0)
        self.assertEqual(self.calls(live), 2)

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
        status, data = self.request("/batch-start", self.batch_payload(None))
        self.assertEqual(status, 200)
        self.wait()
        batch_id = data["batch"]["id"]
        start_payload = self.batch_payload(batch_id)
        self.memory.write_text("not json")
        for action, payload in (("", None), ("/batch-start", start_payload),
                                ("/batch-retry", {"batch_id": batch_id, "connection": self.connection})):
            self.assertEqual(self.request(action, payload)[0], 503)
        self.assertEqual(self.request("/forget-login", {"id": account["id"]})[0], 200)
        self.assertEqual(self.calls(account), 0)


if __name__ == "__main__":
    unittest.main()
