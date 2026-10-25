"""Account controls through the local stdio peer; never use a real CLI."""
import json
import time
import unittest
from unittest import mock

import codex_accounts as ca
from tests import test_codex_accounts as actions


class RequestControlsTests(unittest.TestCase):
    account = actions.AccountActionsTests.account
    wait = actions.AccountActionsTests.wait
    calls = actions.AccountActionsTests.calls
    acknowledge = actions.AccountActionsTests.acknowledge
    run_case = actions.AccountActionsTests.run_case

    def setUp(self):
        patch = mock.patch.object(ca, "START_INTERVAL_SECONDS", 0, create=True)
        patch.start()
        self.addCleanup(patch.stop)
        actions.AccountActionsTests.setUp(self)

    def test_identity_reads_never_force_refresh(self):
        result = self.run_case("disconnect")
        reads = [c for c in self.calls(result) if c.get("method") == "account/read"]
        self.assertEqual(len(reads), 1)  # signed-in initial identity only; no recovery
        self.assertTrue(all(c["params"] == {"refreshToken": False} for c in reads))

    def test_prequota_failure_or_explicit_refusal_never_sends(self):
        for scenario in ("prequota_failure", "quota_denied", "spend_denied", "reached_limit"):
            with self.subTest(scenario=scenario):
                self.manager.root = self.root / scenario
                result = self.run_case(scenario)
                self.assertEqual(result["operation"]["message_status"], "not_sent")
                self.assertNotIn("turn/start", [c.get("method") for c in self.calls(result)])

    def test_full_percentage_is_local_preventive_skip_not_provider_refusal(self):
        result = self.run_case("rounded_full")
        self.assertEqual(result["operation"]["message_status"], "not_sent")
        self.assertEqual(result["operation"]["failure"]["category"], "quotaAtCapacity")
        self.assertNotIn("turn/start", [c.get("method") for c in self.calls(result)])
        self.assertIsNone(self.manager.list()["upstream_pause"])

    def test_refusals_do_not_recover_or_start_again_after_restart(self):
        for scenario, key in (("usage_limit", "quota"), ("unauthorized", "authentication"),
                              ("rate_limit", "rate_limit"), ("http_429", "rate_limit")):
            with self.subTest(scenario=scenario):
                self.manager.root = self.root / scenario
                result = self.run_case(scenario)
                self.assertEqual([c.get("method") for c in self.calls(result)].count("account/rateLimits/read"), 1)
                self.assertIn(key, result.get("restrictions", {}))
                count = len(self.spawned)
                self.manager.close()
                self.manager = ca.Manager(self.manager.root)
                self.addCleanup(self.manager.close)
                self.manager.start(result["id"])
                self.wait(result["id"])
                self.assertEqual(len(self.spawned), count)
                if key != "quota":
                    self.manager.start(result["id"], refresh_only=True)
                    self.wait(result["id"])
                    self.assertEqual(len(self.spawned), count)
                self.assertNotIn("SECRET_FIXTURE_TOKEN", json.dumps(self.manager.list()))

    def test_quota_pause_needs_affirmative_permission_not_unknown_or_percent(self):
        result = self.run_case("usage_limit")
        for scenario in ("success", "rounded_full", "quota_allowed"):
            self.scenario = scenario
            self.manager.start(result["id"], refresh_only=True)
            current = self.wait(result["id"])
            self.assertEqual("quota" in current.get("restrictions", {}), scenario != "quota_allowed")
        self.assertEqual([c.get("method") for c in self.calls(result)].count("turn/start"), 1)

    def test_local_release_preserves_other_reasons_and_sends_nothing(self):
        result = self.run_case("rate_limit")
        directory = self.manager.directory(result["id"])
        record = self.manager.read(directory)
        record.setdefault("restrictions", {}).update({"quota": {"category": "usageLimitExceeded", "created_at": ca.now()},
            "authentication": {"category": "unauthorized", "created_at": ca.now()}})
        self.manager.save(directory, record)
        calls = self.calls(result)
        self.manager.forget_login(result["id"])
        self.assertEqual(set(self.manager.view(directory)["restrictions"]), {"quota", "authentication", "rate_limit"})
        self.acknowledge(result)
        updated = self.manager.view(directory)
        self.assertEqual(set(updated["restrictions"]), {"quota"})
        self.assertEqual(updated["operation"], record["operation"])
        self.assertEqual(self.calls(result), calls)
        self.manager.forget_login(result["id"])
        self.assertEqual(set(self.manager.view(directory)["restrictions"]), {"quota"})

    def test_timeout_retains_progress_and_attempts_interrupt_without_replay(self):
        with mock.patch.object(ca, "SEND_SECONDS", .25):
            result = self.run_case("wait_completion")
        op = result["operation"]
        self.assertEqual(op["message_status"], "unknown")
        diagnostics = op.get("diagnostics", {})
        self.assertEqual(diagnostics.get("last_event"), "turn/started")
        self.assertEqual(diagnostics.get("interrupt"), "acknowledged")
        self.assertEqual(diagnostics.get("thread_id"), "11111111-1111-4111-8111-111111111111")
        self.assertEqual(diagnostics.get("turn_id"), "1")
        methods = [c.get("method") for c in self.calls(result)]
        self.assertEqual(methods.count("turn/start"), 1)
        self.assertEqual(methods.count("turn/interrupt"), 1)

    def test_start_gate_spaces_actual_peers_and_cancelled_queue_never_spawns(self):
        self.manager.start_interval = .08
        original = self.manager.rpc_factory
        starts = []
        def factory(*args):
            starts.append(time.monotonic())
            return original(*args)
        self.manager.rpc_factory = factory
        accounts = [self.account(email=f"test{i}@example.com", account_id=f"workspace{i}") for i in range(3)]
        self.manager.start_batch(None)
        for account in accounts:
            self.wait(account["id"])
        self.assertEqual(len(starts), 3)
        self.assertTrue(all(b - a >= .075 for a, b in zip(starts, starts[1:])), starts)
        self.manager.start_interval = 2
        self.manager.next_start = time.monotonic() + 2
        account = self.account(email="cancel@example.com")
        self.manager.start(account["id"], refresh_only=True)
        self.manager.cancel(account["id"])
        self.assertEqual(self.wait(account["id"])["operation"]["stage"], "cancelled")
        self.assertEqual(len(starts), 3)

    def test_unknown_failure_never_starts_a_recovery_peer(self):
        result = self.run_case("disconnect")
        self.assertEqual(result["operation"]["quota_status"], "not_read")
        self.assertEqual(len(self.spawned), 1)
        self.assertIn("unknown", result["restrictions"])
        self.assertIsNotNone(self.manager.list()["upstream_pause"])

    def test_quota_clear_requires_target_bucket_and_cannot_clear_rate_pause(self):
        result = self.run_case("usage_limit")
        directory = self.manager.directory(result["id"])
        record = self.manager.read(directory)
        reading = ca.quota_reading({"accountId": "workspace-one", "ordinaryUsageAllowed": True,
            "rateLimitsByLimitId": {"other": {"limitId": "other"}}}, "workspace-one")
        self.manager.observe_permission(directory, record, reading, explicit=True)
        self.assertIn("quota", self.manager.view(directory)["restrictions"])
        record["restrictions"]["rate_limit"] = {"category": "rateLimitExceeded", "created_at": ca.now()}
        self.manager.save(directory, record)
        reading = ca.quota_reading({"accountId": "workspace-one", "ordinaryUsageAllowed": True,
            "rateLimits": {"limitId": "codex"}}, "workspace-one")
        self.manager.observe_permission(directory, record, reading, explicit=True)
        self.assertEqual(set(self.manager.view(directory)["restrictions"]), {"rate_limit"})

    def test_rejection_notification_survives_missing_completion(self):
        for scenario, key in (("notify_unauthorized", "authentication"), ("notify_usage_limit", "quota"),
                              ("notify_http_429", "rate_limit"), ("notify_other_thread", None)):
            with self.subTest(scenario=scenario):
                self.manager.root = self.root / scenario
                result = self.run_case(scenario)
                self.assertEqual(result["operation"]["message_status"], "unknown")
                methods = [c.get("method") for c in self.calls(result)]
                self.assertEqual(methods.count("turn/start"), 1)
                self.assertEqual(methods.count("account/rateLimits/read"), 1)
                self.assertEqual(set(result.get("restrictions", {})), {"unknown", key} if key else {"unknown"})
                self.assertNotIn("SECRET_FIXTURE_TOKEN", json.dumps(result))
