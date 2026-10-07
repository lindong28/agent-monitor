import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import codex_accounts as ca


class AccountActionsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.scenario = "success"
        self.spawned = []
        real_popen = subprocess.Popen

        def spawn(command, **kwargs):
            self.spawned.append((command, dict(kwargs["env"])))
            kwargs["env"]["FAKE_SCENARIO"] = self.scenario
            return real_popen([sys.executable, str(ROOT / "tests/fixtures/codex_account_server.py")], **kwargs)

        self.patch = mock.patch.object(ca.subprocess, "Popen", side_effect=spawn)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.manager = ca.Manager(self.root / "profiles")
        self.addCleanup(self.manager.close)

    def account(self, email="one@example.com", account_id="workspace-one"):
        return self.manager.add(email, account_id)

    def wait(self, profile_id, stage=None):
        deadline = time.monotonic() + 6
        while time.monotonic() < deadline:
            account = next(a for a in self.manager.list()["accounts"] if a["id"] == profile_id)
            if (stage and account["operation"]["stage"] == stage) or (not stage and not account["busy"]):
                return account
            time.sleep(.015)
        self.fail("operation did not reach expected state")

    def calls(self, account):
        path = self.manager.directory(account["id"]) / "calls.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()]

    def run_case(self, scenario):
        self.scenario = scenario
        account = self.account()
        self.manager.start(account["id"])
        return self.wait(account["id"])

    def test_login_send_and_reuse_are_separate_from_default_home(self):
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "DONT_INHERIT", "CODEX_AUTH_TOKEN": "DONT_INHERIT"}):
            account = self.run_case("success")
        self.assertEqual(account["operation"]["message_status"], "succeeded")
        self.assertEqual(account["operation"]["after"]["seven_day_resets_at"], 1900000002)
        self.assertEqual(account["operation"]["after"]["seven_day_used_pct"], 0)
        serialized = json.dumps(self.manager.list())
        self.assertNotIn("SECRET_FIXTURE_TOKEN", serialized)
        self.assertNotIn("TEST-CODE", serialized)
        command, env = self.spawned[0]
        self.assertNotIn("OPENAI_API_KEY", env)
        self.assertNotIn("CODEX_AUTH_TOKEN", env)
        self.assertTrue(env["HOME"].startswith(str(self.root)))
        self.assertIn('features.shell_tool=false', command)
        self.assertIn('project_doc_max_bytes=0', command)
        path = self.manager.directory(account["id"])
        self.assertEqual((path / "codex/auth.json").stat().st_mode & 0o777, 0o600)
        self.assertEqual(path.stat().st_mode & 0o777, 0o700)
        self.assertEqual((path / "profile.json").stat().st_mode & 0o777, 0o600)
        self.manager.start(account["id"])
        self.wait(account["id"])
        methods = [call.get("method") for call in self.calls(account)]
        self.assertEqual(methods.count("account/login/start"), 1)
        self.assertEqual(methods.count("turn/start"), 2)

    def test_wrong_email_and_workspace_never_send(self):
        for scenario in ("wrong_email", "wrong_workspace"):
            with self.subTest(scenario=scenario):
                self.scenario = scenario
                account = self.account()
                self.manager.forget_login(account["id"])
                self.manager.start(account["id"])
                result = self.wait(account["id"])
                self.assertEqual(result["operation"]["message_status"], "not_sent")
                self.assertIn("不一致", result["operation"]["detail"])
                self.assertNotIn("turn/start", [c.get("method") for c in self.calls(account)])

    def test_send_success_is_not_lost_on_quota_failure(self):
        result = self.run_case("quota_failure")
        self.assertEqual(result["operation"]["stage"], "partial")
        self.assertEqual(result["operation"]["message_status"], "succeeded")
        self.assertEqual(result["operation"]["quota_status"], "failed")
        self.assertNotIn("SECRET_FIXTURE_TOKEN", json.dumps(result))
        self.scenario = "success"
        self.manager.start(result["id"], refresh_only=True)
        refreshed = self.wait(result["id"])
        self.assertEqual(refreshed["operation"]["quota_status"], "observed")
        self.assertEqual([c.get("method") for c in self.calls(result)].count("turn/start"), 1)

    def test_missing_reset_is_unknown_not_locally_calculated(self):
        result = self.run_case("no_reset")
        self.assertEqual(result["operation"]["stage"], "partial")
        self.assertIsNone(result["operation"]["after"]["seven_day_resets_at"])

    def test_disconnect_keeps_unknown_and_does_not_retry(self):
        result = self.run_case("disconnect")
        self.assertEqual(result["operation"]["message_status"], "unknown")
        self.assertEqual([c.get("method") for c in self.calls(result)].count("turn/start"), 1)

    def test_failed_turn_is_not_success(self):
        result = self.run_case("failed_turn")
        self.assertEqual(result["operation"]["message_status"], "failed")

    def test_tools_are_rejected(self):
        result = self.run_case("tool_request")
        self.assertEqual(result["operation"]["stage"], "failed")
        self.assertIn("工具", result["operation"]["detail"])

    def test_official_link_validation_and_redacted_error(self):
        for scenario in ("bad_url", "login_error"):
            with self.subTest(scenario=scenario):
                result = self.run_case(scenario)
                self.assertEqual(result["operation"]["message_status"], "not_sent")
                self.assertNotIn("SECRET_FIXTURE_TOKEN", json.dumps(result))
                self.assertNotIn("evil.example", json.dumps(result))

    def test_pending_login_polling_duplicate_lock_and_cancellation(self):
        self.scenario = "wait_login"
        account = self.account()
        self.manager.start(account["id"])
        pending = self.wait(account["id"], "login")
        self.assertEqual(pending["operation"]["user_code"], "TEST-CODE")
        directory = self.manager.directory(account["id"])
        self.assertNotIn("TEST-CODE", (directory / "profile.json").read_text())
        second_manager = ca.Manager(self.manager.root)
        self.assertTrue(second_manager.list()["accounts"][0]["busy"])
        with self.assertRaises(ca.ActionError) as error:
            second_manager.start(account["id"])
        self.assertEqual(error.exception.status, 409)
        with self.assertRaises(ca.ActionError):
            self.manager.forget_login(account["id"])
        self.manager.cancel(account["id"])
        cancelled = self.wait(account["id"])
        self.assertEqual(cancelled["operation"]["stage"], "cancelled")
        self.assertNotIn("user_code", cancelled["operation"])

    def test_deadline_cleans_up_child_without_replaying_send(self):
        with mock.patch.object(ca, "SEND_SECONDS", .2):
            result = self.run_case("wait_turn")
        self.assertEqual(result["operation"]["stage"], "failed")
        self.assertEqual(result["operation"]["message_status"], "unknown")
        self.assertFalse(self.manager.jobs)

    def test_restart_is_visible_without_new_work(self):
        account = self.account()
        path = self.manager.directory(account["id"])
        record = self.manager.read(path)
        record["operation"] = {"stage": "sending", "message_status": "unknown"}
        self.manager.save(path, record)
        recovered = ca.Manager(self.manager.root).list()["accounts"][0]
        self.assertEqual(recovered["operation"]["stage"], "interrupted")
        self.assertEqual(recovered["operation"]["message_status"], "unknown")
        self.assertFalse(self.spawned)

    def test_profile_identity_and_path_validation(self):
        first = self.account()
        self.assertEqual(self.account()["id"], first["id"])
        other = self.account("two@example.com")
        self.assertNotEqual(first["id"], other["id"])
        another_workspace = self.account(account_id="workspace-two")
        self.assertNotEqual(first["id"], another_workspace["id"])
        with self.assertRaises(ca.ActionError):
            self.manager.add("one@example.com")
        for bad in ("../../outside", None, "anything"):
            with self.assertRaises(ca.ActionError):
                self.manager.start(bad)
        for bad in ("invalid", " a@b.c", "a@b\nc"):
            with self.assertRaises(ca.ActionError):
                self.manager.add(bad)

    def test_email_only_form_reuses_profile_after_workspace_is_bound(self):
        account = self.manager.add("one@example.com")
        self.manager.start(account["id"])
        self.wait(account["id"])
        self.assertEqual(self.manager.add("ONE@example.com")["id"], account["id"])
        self.assertEqual(self.account()["id"], account["id"])


if __name__ == "__main__":
    unittest.main()
