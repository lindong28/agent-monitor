"""Finite quota-only reread policy; only owned synthetic credential files."""
import base64
import json
import tempfile
import time
import unittest
from pathlib import Path
import os
import sys
from unittest import mock
from agent_monitor.quota import codex_quota as quota

TRANSPORT = {"code": -32603, "message": "failed to fetch codex rate limits: error sending request for url (https://chatgpt.com/backend-api/wham/usage)"}
RESULT = {"accountId": "fixture-workspace", "rateLimits": {"secondary": {"usedPercent": 17, "windowDurationMins": 10080, "resetsAt": 4102444800}}}

class Failure(RuntimeError):
    def __init__(self, error):
        self.failure = quota.rpc_failure(error)
        self.quota_transport_failure = quota.quota_transport_failure(error, "account/rateLimits/read")

class TransportRetryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.auth = Path(self.temp.name) / "auth.json"
        self.seed()
        self.context = {"original_digest": quota.credential_digest(self.auth), "deadline": time.monotonic() + 20,
                        "client_info": {"userAgent": "agent_monitor/0.167.0 (fixture)"}}
        self.calls = []

    def seed(self, expiry=4102444800, suffix="fixture"):
        def jwt(payload):
            return "fixture." + base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=") + "." + suffix
        claims = {"email": "fixture@example.test", "https://api.openai.com/auth": {"chatgpt_account_id": "fixture-workspace"}}
        self.auth.write_text(json.dumps({"auth_mode": "chatgpt", "tokens": {"account_id": "fixture-workspace",
            "id_token": jwt(claims), "access_token": jwt({"exp": expiry}) if expiry is not None else "opaque-fixture-token",
            "refresh_token": "fixture-refresh"}}))

    def read(self, failures=1, *, error=TRANSPORT, change=None, retry=True):
        def request(method, params):
            self.calls.append(method)
            if len(self.calls) <= failures:
                if change: change()
                raise Failure(error)
            return RESULT
        return quota.read_bound(request, self.auth, "fixture-workspace", "fixture@example.test",
                                **({"retry": self.context} if retry else {}))

    def test_one_transport_failure_then_success_preserves_first_failure(self):
        result = self.read()
        self.assertEqual(self.calls, ["account/rateLimits/read"] * 2)
        self.assertEqual(result["seven_day_used_pct"], 17)
        self.assertEqual(self.context["receipt"]["outcome"], "quota_confirmed")
        self.assertEqual(self.context["receipt"]["quota_rpc_budget_used"], 2)
        self.assertEqual(self.context["receipt"]["first_failure"]["rpc_code"], -32603)

    def test_persistent_transport_stops_after_two_and_retains_both(self):
        with self.assertRaises(Failure) as raised: self.read(failures=100)
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(raised.exception.failure["quota_retry"]["first_failure"]["rpc_code"], -32603)
        self.assertEqual(raised.exception.failure["quota_retry"]["outcome"], "quota_failed")
        with self.assertRaises(quota.ReadingError): self.read(failures=100)
        self.assertEqual(len(self.calls), 2)

    def test_identity_change_preserves_transport_evidence_without_reread(self):
        def change():
            value = json.loads(self.auth.read_text())
            value['tokens']['account_id'] = 'another-workspace'
            self.auth.write_text(json.dumps(value))
        with self.assertRaises(quota.ReadingError) as raised:
            self.read(change=change)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(raised.exception.failure['kind'], 'account_mismatch')
        self.assertEqual(raised.exception.failure['rpc_message'], TRANSPORT['message'])
        self.assertEqual(raised.exception.failure['rpc_code'], -32603)
        self.assertNotIn('receipt', self.context)

    def test_unknown_auth_status_or_data_does_not_get_another_call(self):
        for error in ({**TRANSPORT, "code": -32603.0},
                      {**TRANSPORT, "code": "-32603"},
                      {"code": -32603, "message": "internal error"},
                      {**TRANSPORT, "data": {"unknown": "retain evidence"}},
                      {"code": -32603, "message": "failed: 401 Unauthorized"},
                      {"code": -32603, "message": "failed: 429 Too Many Requests"},
                      {"code": -32603, "message": "failed: 500 Internal Server Error"}):
            with self.subTest(error=error):
                self.calls = []
                with self.assertRaises(Failure): self.read(error=error)
                self.assertEqual(len(self.calls), 1)

    def test_auth_refresh_risk_or_unknown_client_blocks_reread(self):
        for expiry in (None, time.time() + 60, time.time() + 310):
            with self.subTest(expiry=expiry):
                self.seed(expiry)
                self.context["original_digest"] = quota.credential_digest(self.auth)
                self.calls = []
                with self.assertRaises(Failure): self.read()
                self.assertEqual(len(self.calls), 1)
        self.seed()
        self.context.update(original_digest=quota.credential_digest(self.auth), client_info={"userAgent": "agent_monitor/0.168.0 (fixture)"})
        self.calls = []
        with self.assertRaises(Failure): self.read()
        self.assertEqual(len(self.calls), 1)

    def test_changed_generation_and_expired_deadline_block_reread(self):
        with self.assertRaises(Failure): self.read(change=lambda: self.seed(suffix="rotated"))
        self.assertEqual(len(self.calls), 1)
        self.context.update(original_digest=quota.credential_digest(self.auth), deadline=time.monotonic() - 1)
        self.calls = []
        with self.assertRaises(Failure): self.read()
        self.assertEqual(len(self.calls), 1)

    def test_non_sdk_jwt_expiry_representations_do_not_prove_refresh_safety(self):
        for expiry in (4102444800.5, 10 ** 30):
            with self.subTest(expiry=expiry):
                self.seed(expiry)
                self.context.update(original_digest=quota.credential_digest(self.auth), used=False)
                self.calls = []
                with self.assertRaises(Failure): self.read()
                self.assertEqual(len(self.calls), 1)

    def test_default_recovery_contract_is_still_one_call(self):
        with self.assertRaises(Failure): self.read(retry=False)
        self.assertEqual(len(self.calls), 1)


def seed_fresh(auth, *, expires=4102444800):
    value = json.loads(auth.read_text())
    body = base64.urlsafe_b64encode(json.dumps({"exp": expires}).encode()).decode().rstrip("=")
    value["tokens"].update(access_token="fixture." + body + ".fixture", refresh_token="fixture-refresh")
    auth.write_text(json.dumps(value))


from tests import test_codex_accounts as actions
from tests import test_quota_refresh as native
from agent_monitor.accounts import codex_accounts as ca
from agent_monitor.accounts.upstream_controls import Controls
from parsers.accounts import Account


class ManagedTransportTests(unittest.TestCase):
    setUp = actions.AccountActionsTests.setUp
    account = actions.AccountActionsTests.account
    wait = actions.AccountActionsTests.wait
    calls = actions.AccountActionsTests.calls

    def start_case(self, scenario, *, expires=4102444800):
        self.scenario = scenario
        account = self.account()
        seed_fresh(self.manager.directory(account["id"]) / "codex/auth.json", expires=expires)
        self.manager.start(account["id"], refresh_only=True)
        return self.wait(account["id"])

    def test_normal_managed_entry_recovers_and_receipt_survives_new_manager(self):
        result = self.start_case("bounded_once")
        self.assertEqual(result["operation"]["stage"], "succeeded")
        self.assertEqual([c["method"] for c in self.calls(result)].count("account/rateLimits/read"), 2)
        self.assertNotIn("turn/start", [c["method"] for c in self.calls(result)])
        root = self.manager.root
        self.manager.close()
        self.manager = ca.Manager(root)
        self.addCleanup(self.manager.close)
        receipt = self.manager.controls.read("codex")["last_quota_retry"]
        self.assertEqual(receipt["outcome"], "quota_confirmed")
        self.assertEqual(receipt["first_failure"]["rpc_code"], -32603)
        self.assertIsNone(self.manager.controls.read("codex").get("pause"))

    def test_both_requests_keep_same_deadline_and_provider_lease(self):
        from agent_monitor.accounts.upstream_controls import Busy
        original = ca.Rpc.request
        deadlines = []
        owner = self
        def request(rpc, method, params, deadline):
            if method == "account/rateLimits/read":
                deadlines.append(deadline)
                with owner.assertRaises(Busy): owner.manager.controls.acquire("codex")
            return original(rpc, method, params, deadline)
        with mock.patch.object(ca.Rpc, "request", request):
            result = self.start_case("bounded_once")
        self.assertEqual(result["operation"]["stage"], "succeeded")
        self.assertEqual(len(deadlines), 2)
        self.assertEqual(deadlines[0], deadlines[1])

    def test_cancellation_before_reread_sends_no_second_quota_rpc(self):
        original = ca.Rpc.request
        def request(rpc, method, params, deadline):
            try:
                return original(rpc, method, params, deadline)
            except ca.ActionError:
                if method == "account/rateLimits/read": rpc.cancelled.set()
                raise
        with mock.patch.object(ca.Rpc, "request", request):
            result = self.start_case("bounded_once")
        self.assertEqual(result["operation"]["stage"], "cancelled")
        self.assertEqual([c["method"] for c in self.calls(result)].count("account/rateLimits/read"), 1)
        self.assertIsNotNone(self.manager.controls.read("codex").get("pause"))

    def test_persistent_failure_stops_shared_lane_and_restart_does_not_replay(self):
        result = self.start_case("bounded_persistent")
        self.assertEqual(result["operation"]["stage"], "failed")
        pause = self.manager.controls.read("codex")["pause"]
        self.assertEqual(pause["failure"]["quota_retry"]["quota_rpc_budget_used"], 2)
        self.assertEqual([c["method"] for c in self.calls(result)].count("account/rateLimits/read"), 2)
        root = self.manager.root
        self.manager.close()
        self.manager = ca.Manager(root)
        self.addCleanup(self.manager.close)
        try:
            self.manager.start(result["id"], refresh_only=True)
        except ca.ActionError as exc:
            self.assertEqual(exc.category, "locallyPaused")
        else:
            stopped = self.wait(result["id"])
            self.assertEqual(stopped["operation"]["failure"]["category"], "locallyPaused")
        self.assertEqual(len(self.spawned), 1)
        self.assertEqual(self.manager.controls.read("codex")["pause"]["id"], pause["id"])

    def test_unknown_data_and_proactive_auth_window_keep_single_rpc(self):
        for scenario, expiry in (("bounded_unknown", 4102444800), ("bounded_expiring", time.time() + 60)):
            with self.subTest(scenario=scenario):
                self.manager.close()
                self.manager = ca.Manager(self.root / scenario / "profiles")
                self.addCleanup(self.manager.close)
                result = self.start_case(scenario, expires=expiry)
                self.assertEqual(result["operation"]["stage"], "failed")
                self.assertEqual([c["method"] for c in self.calls(result)].count("account/rateLimits/read"), 1)
                if scenario == "bounded_unknown":
                    self.assertIn("fixture-data", result["last_failure"]["diagnostics"]["rpc_failure"]["rpc_data"])


class NativeTransportTests(unittest.TestCase):
    setUp = native.ProviderTests.setUp

    def run_native(self, scenario, *, recovery=False):
        from agent_monitor.quota import quota_refresh
        import base64
        home = self.root / "fixture-home"
        auth = home / ".codex/auth.json"
        auth.parent.mkdir(parents=True, exist_ok=True)
        claims = {"email": "fixture@example.test", "https://api.openai.com/auth": {"chatgpt_account_id": "fixture-workspace"}}
        body = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
        auth.write_text(json.dumps({"tokens": {"account_id": "fixture-workspace", "id_token": "fixture." + body + ".fixture"}}))
        seed_fresh(auth)
        shim = home / ".local/libexec/agent-shims/codex"
        shim.parent.mkdir(parents=True, exist_ok=True)
        peer = Path(__file__).parent / "fixtures/codex_account_server.py"
        shim.write_text("#!" + sys.executable + "\nimport os,sys,json,runpy,pathlib\n" +
            "pathlib.Path(os.environ['HOME'],'launch.json').write_text(json.dumps({'file_store': 'cli_auth_credentials_store=\"file\"' in sys.argv, 'alternate_auth': 'OPENAI_API_KEY' in os.environ}))\n" +
            "os.environ.update(FAKE_SCENARIO=" + repr(scenario) + ",FAKE_EMAIL='fixture@example.test',FAKE_ACCOUNT='fixture-workspace')\n" +
            "runpy.run_path(" + repr(str(peer)) + ",run_name='__main__')\n")
        shim.chmod(0o700)
        controls = Controls(self.root / "state/upstream_controls")
        options = {}
        if recovery:
            lease = controls.acquire("codex")
            lease.pause("quotaRefreshFailed")
            options["probe_pause_id"] = lease.state["pause"]["id"]
            lease.close()
        account = Account("fixture-workspace", "fixture@example.test")
        with mock.patch.dict(os.environ, {"HOME": str(home), "CODEX_HOME": str(auth.parent), "OPENAI_API_KEY": "synthetic-alternate-auth"}):
            try:
                result = quota_refresh.load_rate_limits("codex", account, root=self.root, codex_home=auth.parent, **options)
            except quota_refresh.RefreshError:
                result = None
        calls = [json.loads(line) for line in (home / "calls.jsonl").read_text().splitlines()]
        launch = json.loads((home / "launch.json").read_text())
        self.assertEqual(launch, {"file_store": True, "alternate_auth": False})
        return result, calls, controls

    def test_normal_native_child_recovers_through_owning_controls_and_cache_carrier(self):
        result, calls, controls = self.run_native("bounded_once")
        self.assertIsNotNone(result)
        self.assertEqual([c["method"] for c in calls].count("account/rateLimits/read"), 2)
        self.assertEqual(result.quota_observation["quota_retry"]["outcome"], "quota_confirmed")
        self.assertEqual(controls.read("codex")["last_quota_retry"]["first_failure"]["rpc_code"], -32603)
        self.assertIsNone(controls.read("codex").get("pause"))

    def test_native_persistent_failure_and_later_collection_keep_the_same_pause(self):
        first, calls, controls = self.run_native("bounded_persistent")
        self.assertIsNone(first)
        self.assertEqual([c["method"] for c in calls].count("account/rateLimits/read"), 2)
        original = controls.read("codex")["pause"]
        self.assertEqual(original["failure"]["quota_retry"]["quota_rpc_budget_used"], 2)
        second, calls, controls = self.run_native("bounded_persistent")
        self.assertIsNone(second)
        self.assertEqual([c["method"] for c in calls].count("account/rateLimits/read"), 2)
        self.assertEqual(controls.read("codex")["pause"]["id"], original["id"])

    def test_explicit_recovery_keeps_its_single_probe(self):
        result, calls, controls = self.run_native("bounded_once", recovery=True)
        self.assertIsNone(result)
        self.assertEqual([c["method"] for c in calls].count("account/rateLimits/read"), 1)
        self.assertIsNotNone(controls.read("codex").get("pause"))


if __name__ == "__main__": unittest.main()
