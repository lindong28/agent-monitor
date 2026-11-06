"""Recurring quota actions through the real Manager and isolated stdio peer."""
import json
import unittest
from unittest import mock

from agent_monitor.accounts import codex_accounts as ca
from tests import test_codex_accounts as actions


class QuotaRecurrenceTests(unittest.TestCase):
    setUp = actions.AccountActionsTests.setUp
    account = actions.AccountActionsTests.account
    wait = actions.AccountActionsTests.wait
    calls = actions.AccountActionsTests.calls
    run_case = actions.AccountActionsTests.run_case

    def test_quota_refresh_does_not_depend_on_account_discovery_even_on_recurrence(self):
        self.scenario = "account_read_transport"
        account = self.account()
        for _ in range(2):
            self.manager.start(account["id"], refresh_only=True)
            result = self.wait(account["id"])
            self.assertEqual(result["operation"]["stage"], "succeeded")
            self.assertEqual(result["operation"]["message_status"], "not_sent")
            self.assertIsNone(self.manager.list()["upstream_pause"])
        methods = [c.get("method") for c in self.calls(result)]
        self.assertNotIn("account/read", methods)
        self.assertNotIn("turn/start", methods)
        self.assertEqual(methods.count("account/rateLimits/read"), 2)

    def test_unknown_identity_error_keeps_safe_context_and_remains_paused(self):
        result = self.run_case("account_read_unsafe")
        facts = result["operation"]["diagnostics"].get("rpc_failure", {})
        self.assertEqual(facts.get("stage"), "account_check")
        self.assertIn("workspace routing discovery failed", facts.get("detail", ""))
        self.assertNotIn("SECRET_FIXTURE_TOKEN", json.dumps(result))
        self.assertEqual(self.manager.list()["upstream_pause"]["failure"].get("detail"), facts["detail"])
        self.manager.close()
        self.manager = ca.Manager(self.manager.root)
        self.addCleanup(self.manager.close)
        self.assertEqual(self.manager.view(self.manager.directory(result["id"]))["last_failure"]["diagnostics"]["rpc_failure"], facts)

    def test_post_send_quota_failure_preserves_original_cause_without_replay(self):
        result = self.run_case("post_quota_diagnostic")
        self.assertEqual(result["operation"]["message_status"], "succeeded")
        self.assertEqual(result["operation"]["quota_status"], "failed")
        pause = self.manager.list()["upstream_pause"]
        self.assertEqual(pause["failure"]["upstream_code"], "token_revoked")
        self.assertEqual(pause["source"]["operation_id"], result["operation"]["id"])
        self.assertEqual(result["last_failure"]["diagnostics"]["rpc_failure"]["http_status"], 401)
        for operation in (result["operation"], result["last_failure"]):
            self.assertEqual(operation["diagnostics"]["thread_id"], "11111111-1111-4111-8111-111111111111")
            self.assertEqual(operation["diagnostics"]["turn_id"], "1")
        self.assertNotIn("SECRET_FIXTURE_TOKEN", json.dumps(result))
        self.assertEqual([c.get("method") for c in self.calls(result)].count("turn/start"), 1)

    def test_local_identity_mismatch_refuses_before_starting_cli(self):
        account = self.account()
        wrong = dict(account, account_id="different-workspace")
        actions.seed_auth(self.manager, wrong)
        self.manager.start(account["id"], refresh_only=True)
        result = self.wait(account["id"])
        self.assertEqual(result["operation"]["stage"], "failed")
        self.assertEqual(len(self.spawned), 0)

    def test_email_case_remains_equivalent_but_another_member_is_rejected(self):
        for label, succeeds in (("ONE@EXAMPLE.COM", True), ("other@example.com", False)):
            with self.subTest(label=label):
                account = self.account(email=label)
                actions.seed_auth(self.manager, dict(account, email="one@example.com"))
                self.manager.start(account["id"], refresh_only=True)
                result = self.wait(account["id"])
                self.assertEqual(result["operation"]["stage"], "succeeded" if succeeds else "failed")

    def test_credential_change_during_read_rejects_result(self):
        account = self.account()
        original = ca.Rpc.request
        def changed(rpc, method, params, deadline):
            result = original(rpc, method, params, deadline)
            if method == "account/rateLimits/read":
                actions.seed_auth(self.manager, dict(account, email="different@example.com"))
            return result
        with mock.patch.object(ca.Rpc, "request", changed):
            self.manager.start(account["id"], refresh_only=True)
            result = self.wait(account["id"])
        self.assertEqual(result["operation"]["stage"], "failed")
        self.assertNotIn("after", result["operation"])
        self.assertIsNotNone(self.manager.list()["upstream_pause"])

    def test_rotated_credentials_are_redacted_from_unknown_errors(self):
        result = self.run_case("account_read_rotated")
        detail = result["operation"]["diagnostics"].get("rpc_failure", {}).get("detail", "")
        self.assertIn("rotation failed", detail)
        persisted = (self.manager.directory(result["id"]) / "profile.json").read_text()
        for secret in ("SECRET_FIXTURE_TOKEN", "NEXT_FIXTURE_TOKEN"):
            self.assertNotIn(secret, persisted + json.dumps(self.manager.list()))

    def test_missing_windows_do_not_replace_the_last_usable_reading(self):
        account = self.account()
        self.manager.start(account["id"], refresh_only=True)
        before = self.wait(account["id"])["last_quota"]
        self.scenario = "missing_windows"
        self.manager.start(account["id"], refresh_only=True)
        result = self.wait(account["id"])
        self.assertEqual(result["operation"]["stage"], "failed")
        self.assertEqual(result["last_quota"], before)
        self.assertEqual(self.manager.list()["upstream_pause"]["failure"]["kind"], "missing_windows")


if __name__ == "__main__":
    unittest.main()
