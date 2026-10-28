import base64
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
from agent_monitor.accounts import codex_accounts as ca


def seed_auth(manager, account):
    """Write a synthetic identity only; no real credential store is consulted."""
    directory = manager.directory(account["id"])
    ca.private_dir(directory / "codex")
    claims = {"email": account["email"], "https://api.openai.com/auth": {"chatgpt_account_id": account["account_id"]}}
    token = "fixture." + base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=") + ".fixture"
    ca.write_record(directory / "codex/auth.json", {"tokens": {"account_id": account["account_id"],
        "id_token": token, "access_token": "SECRET_FIXTURE_TOKEN"}})


class AccountActionsTests(unittest.TestCase):
    def setUp(self):
        for patch in (mock.patch.object(ca, "START_INTERVAL_SECONDS", 0),
                      mock.patch.object(ca.network_policy, "endpoint", return_value="http://agent-monitor:synthetic-test-capability@127.0.0.1:9"),
                      mock.patch.object(ca.shutil, "which", return_value=sys.executable)):
            patch.start()
            self.addCleanup(patch.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.scenario = "success"
        self.spawned = []
        real_popen = subprocess.Popen
        self.real_popen = real_popen

        def spawn(command, **kwargs):
            self.spawned.append((command, dict(kwargs["env"])))
            directory = Path(kwargs["env"]["CODEX_HOME"]).parent
            record = self.manager.read(directory)
            kwargs["env"].update(FAKE_SCENARIO=self.scenario, FAKE_EMAIL=record["email"],
                                 FAKE_ACCOUNT=record.get("account_id") or "workspace-one")
            return real_popen([sys.executable, str(ROOT / "tests/fixtures/codex_account_server.py")], **kwargs)

        self.patch = mock.patch.object(ca.subprocess, "Popen", side_effect=spawn)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.manager = ca.Manager(self.root / "profiles")
        self.addCleanup(self.manager.close)

    def account(self, email="one@example.com", account_id="workspace-one", signed_in=True):
        account = self.manager.add(email, account_id)
        if signed_in:
            seed_auth(self.manager, account)
        return account

    def acknowledge(self, account):
        pause = self.manager.list().get("upstream_pause")
        return self.manager.resume(account["id"], account["operation"]["id"], pause["id"] if pause else None)

    def test_shutdown_refuses_new_account_work(self):
        account = self.account()
        self.manager.close()
        with self.assertRaises(ca.ActionError) as raised:
            self.manager.start(account["id"], refresh_only=True)
        self.assertEqual(raised.exception.status, 503)
        self.assertEqual(self.spawned, [])

    def test_device_loss_before_rpc_stops_batch_without_provider_pause(self):
        from agent_monitor.network import client_network
        route = client_network.Route('fixture device')
        self.addCleanup(route.close)
        accounts = [self.account(email=f'{i}@example.com', account_id=f'workspace-{i}') for i in range(2)]
        original = self.manager.open_rpc
        def disconnected(directory, job):
            route.close()
            return original(directory, job)
        with mock.patch.object(self.manager, 'open_rpc', side_effect=disconnected):
            self.manager.start_batch(None, [a['id'] for a in accounts], network_route=route)
            finished = [self.wait(a['id']) for a in accounts]
        self.assertEqual(self.spawned, [])
        self.assertEqual([a['operation']['failure']['category'] for a in finished],
                         ['clientNetworkDisconnected', 'locallyPaused'])
        self.assertIsNone(self.manager.list()['upstream_pause'])

    def test_device_error_after_rpc_start_still_pauses_provider(self):
        account = self.account()
        peer = mock.Mock(diagnostics={}, network_route=None)
        peer.initialize.side_effect = ca.ActionError('fixture disconnected', 409, 'clientNetworkDisconnected')
        peer.rejections.return_value = []
        self.manager.rpc_factory = mock.Mock(return_value=peer)
        self.manager.start(account['id'])
        self.wait(account['id'])
        self.assertIsNotNone(self.manager.list()['upstream_pause'])

    def test_shutdown_reaps_a_peer_that_blocks_protocol_writes(self):
        account = self.account()
        ready = threading.Event()
        peers = []
        real_popen = self.real_popen
        # The peer deliberately never reads stdin and ignores TERM. No provider
        # or real credentials are involved; the watchdog finally reaps it.
        def factory(directory, cancelled):
            with mock.patch.object(ca.shutil, "which", return_value=sys.executable),\
                 mock.patch.object(ca.subprocess, "Popen", side_effect=lambda *a, **kw: real_popen(
                     [sys.executable, "-c", "import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); print('ready',flush=True); time.sleep(30)"], **kw)):
                rpc = ca.Rpc(directory, cancelled)
            rpc.process.stdout.readline()
            peers.append(rpc.process)
            def blocked_initialize():
                ready.set()
                rpc.send({"payload": "x" * 1000000})
            rpc.initialize = blocked_initialize
            return rpc
        self.manager.rpc_factory = factory
        try:
            self.manager.start(account["id"], refresh_only=True)
            self.assertTrue(ready.wait(3))
            self.manager.close()
            self.assertFalse(self.manager.jobs)
            with self.assertRaises(ChildProcessError):
                os.waitpid(peers[0].pid, os.WNOHANG)
        finally:
            for peer in peers:
                if peer.poll() is None:
                    peer.kill()
                peer.wait(timeout=3)
            self.manager.close()

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

    def run_case(self, scenario, *, login_only=False):
        # Independent cases have independent persisted provider controls. Tests
        # of restart and acknowledgement explicitly reuse this same manager root.
        self.manager.close()
        self.manager = ca.Manager(self.root / ("case-" + str(len(self.spawned))) / "profiles")
        self.addCleanup(self.manager.close)
        self.scenario = scenario
        account = self.account(signed_in=not login_only)
        self.manager.start(account["id"], login_only=login_only)
        return self.wait(account["id"])

    def test_login_send_and_reuse_are_separate_from_default_home(self):
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "DONT_INHERIT", "CODEX_AUTH_TOKEN": "DONT_INHERIT"}):
            account = self.run_case("success", login_only=True)
            self.assertEqual(account["operation"]["message_status"], "not_sent")
            self.assertNotIn("account/rateLimits/read", [c.get("method") for c in self.calls(account)])
            self.manager.start(account["id"])
            account = self.wait(account["id"])
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
        self.assertIn('model_provider="agent-monitor-http"', command)
        self.assertIn('model_providers.agent-monitor-http.supports_websockets=false', command)
        self.assertIn('model_providers.agent-monitor-http.request_max_retries=0', command)
        self.assertIn('model_providers.agent-monitor-http.stream_max_retries=0', command)
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

    def test_message_pool_has_100_unique_short_messages(self):
        self.assertEqual(len(ca.MESSAGES), 100)
        self.assertEqual(len(set(ca.MESSAGES)), 100)
        for message in ca.MESSAGES:
            self.assertTrue(message.strip())
            self.assertNotIn("\n", message)
        for example in ("你好！", "现在几点了？", "今天天气如何？"):
            self.assertIn(example, ca.MESSAGES)

    def test_selected_message_is_saved_before_send_and_survives_outcomes(self):
        real_request = ca.Rpc.request
        seen = []

        def observe(rpc, method, params, deadline):
            if method == "turn/start":
                record = self.manager.read(directory)
                seen.append(record["operation"])
                self.assertEqual(record["operation"]["message_status"], "unknown")
                self.assertEqual(params["input"], [{"type": "text", "text": record["operation"]["message"]}])
            return real_request(rpc, method, params, deadline)

        for index, scenario in enumerate(("success", "disconnect")):
            with self.subTest(scenario=scenario):
                account = self.account(f"message{index}@example.com", f"message-workspace-{index}")
                directory = self.manager.directory(account["id"])
                self.scenario = scenario
                selected = ca.MESSAGES[index * 99]
                with mock.patch.object(ca.secrets, "choice", return_value=selected) as choose,\
                     mock.patch.object(ca.Rpc, "request", observe):
                    self.manager.start(account["id"])
                    result = self.wait(account["id"])
                choose.assert_called_once_with(ca.MESSAGES)
                self.assertEqual(result["operation"]["message"], selected)
                self.assertEqual(result["operation"]["message_status"], "succeeded" if scenario == "success" else "unknown")
                turns = [c for c in self.calls(account) if c.get("method") == "turn/start"]
                self.assertEqual(len(turns), 1)
                self.assertEqual(turns[0]["params"]["input"][0]["text"], selected)
                observer = ca.Manager(self.manager.root)
                try:
                    restored = observer.view(directory)
                    self.assertEqual(restored["operation"]["message"], selected)
                finally:
                    observer.close()
        self.assertEqual(len(seen), 2)

    def test_quota_and_login_only_do_not_draw_messages(self):
        account = self.account()
        with mock.patch.object(ca.secrets, "choice") as choose:
            for mode in ({"refresh_only": True}, {"login_only": True}):
                self.manager.start(account["id"], **mode)
                result = self.wait(account["id"])
                self.assertNotIn("message", result["operation"])
            choose.assert_not_called()
        self.assertFalse(any(c.get("method") == "turn/start" for c in self.calls(account)))

    def test_configuration_is_verified_before_any_turn(self):
        for scenario in ("unsafe_config", "unsafe_sandbox", "wrong_profile", "custom_profile"):
            with self.subTest(scenario=scenario):
                result = self.run_case(scenario)
                methods = [c.get("method") for c in self.calls(result)]
                expected = 1 if scenario == "custom_profile" else 0
                self.assertEqual(methods.count("turn/start"), expected)
                self.assertEqual(result["operation"]["message_status"], "succeeded" if expected else "not_sent")

    def test_wrong_email_and_workspace_never_send(self):
        for scenario in ("wrong_email", "wrong_workspace"):
            with self.subTest(scenario=scenario):
                result = self.run_case(scenario, login_only=True)
                account = result
                self.assertEqual(result["operation"]["message_status"], "not_sent")
                self.assertIn("不一致", result["operation"]["detail"])
                self.assertNotIn("turn/start", [c.get("method") for c in self.calls(account)])

    def test_login_rate_limits_are_visible_without_misdiagnosing_settings(self):
        for scenario in ("login_start_rate_limit", "login_poll_rate_limit", "login_unknown_failure"):
            with self.subTest(scenario=scenario):
                result = self.run_case(scenario, login_only=True)
                op = result["operation"]
                self.assertEqual(op["stage"], "failed")
                self.assertEqual(op["message_status"], "not_sent")
                if scenario != "login_unknown_failure":
                    self.assertIn("429", op["detail"])
                    self.assertIn("限流", op["detail"])
                self.assertNotIn("已启用设备码", op["detail"])
                self.assertNotIn("SECRET_FIXTURE_TOKEN", json.dumps(result))
                self.assertNotIn("user_code", op)
                self.assertNotIn("turn/start", [c.get("method") for c in self.calls(result)])

    def test_send_success_is_not_lost_on_quota_failure(self):
        result = self.run_case("quota_failure")
        self.assertEqual(result["operation"]["stage"], "partial")
        self.assertEqual(result["operation"]["message_status"], "succeeded")
        self.assertEqual(result["operation"]["quota_status"], "failed")
        self.assertNotIn("SECRET_FIXTURE_TOKEN", json.dumps(result))
        self.scenario = "success"
        self.acknowledge(result)
        self.manager.start(result["id"], refresh_only=True)
        refreshed = self.wait(result["id"])
        self.assertEqual(refreshed["operation"]["quota_status"], "observed")
        self.assertEqual([c.get("method") for c in self.calls(result)].count("turn/start"), 1)

    def test_missing_reset_is_unknown_not_locally_calculated(self):
        result = self.run_case("no_reset")
        self.assertEqual(result["operation"]["stage"], "partial")
        self.assertIsNone(result["operation"]["after"]["seven_day_resets_at"])

    def test_post_send_quota_survives_failed_refresh_and_restart(self):
        account = self.run_case("success")
        reading = account["operation"]["after"]
        methods = [call.get("method") for call in self.calls(account)]
        self.assertLess(methods.index("turn/start"), len(methods) - 1)
        self.assertEqual(methods[-1], "account/rateLimits/read")
        with mock.patch.object(self.manager, "rpc_factory", side_effect=ca.ActionError("fixture authorization failed")):
            self.manager.start(account["id"], refresh_only=True)
            failed = self.wait(account["id"])
        self.assertEqual(failed["operation"]["quota_status"], "not_read")
        self.assertEqual(failed.get("last_quota"), reading)
        restarted = ca.Manager(self.manager.root)
        self.addCleanup(restarted.close)
        self.assertEqual(restarted.list()["accounts"][0].get("last_quota"), reading)
        self.assertEqual([c.get("method") for c in self.calls(account)].count("turn/start"), 1)

    def test_legacy_reading_is_retained_before_replacing_operation(self):
        for source in ("operation", "batch_result"):
            with self.subTest(source=source):
                account = self.account(email=source + "@example.com")
                directory = self.manager.directory(account["id"])
                record = self.manager.read(directory)
                reading = {"observed_at": "2026-10-08T01:00:00+00:00",
                           "seven_day_used_pct": 8, "seven_day_resets_at": 1900000000}
                record[source] = {"stage": "succeeded", "after": reading}
                ca.write_record(directory / "profile.json", record)
                self.assertNotIn("last_quota", self.manager.read(directory))
                with mock.patch.object(self.manager, "rpc_factory", side_effect=ca.ActionError("fixture query failed")):
                    self.manager.start(account["id"], refresh_only=True)
                    failed = self.wait(account["id"])
                self.assertEqual(failed.get("last_quota"), reading)

    def test_new_successful_query_replaces_whole_retained_reading(self):
        account = self.run_case("success")
        previous = account["operation"]["after"]
        self.scenario = "no_reset"
        self.manager.start(account["id"], refresh_only=True)
        refreshed = self.wait(account["id"])
        reading = refreshed["operation"]["after"]
        self.assertGreater(reading["observed_at"], previous["observed_at"])
        self.assertEqual(refreshed.get("last_quota"), reading)
        self.assertIsNone(reading["seven_day_resets_at"])
        self.assertEqual([c.get("method") for c in self.calls(account)].count("turn/start"), 1)

    def test_disconnect_keeps_unknown_and_does_not_retry(self):
        result = self.run_case("disconnect")
        self.assertEqual(result["operation"]["message_status"], "unknown")
        self.assertEqual([c.get("method") for c in self.calls(result)].count("turn/start"), 1)

    def test_failed_turn_is_not_success(self):
        result = self.run_case("failed_turn")
        self.assertEqual(result["operation"]["message_status"], "failed")

    def test_failed_send_retains_before_without_recovery_or_replay(self):
        for scenario, status in (("failed_turn", "failed"), ("disconnect", "unknown")):
            with self.subTest(scenario=scenario):
                result = self.run_case(scenario)
                op = result["operation"]
                self.assertEqual(op["message_status"], status)
                self.assertEqual(op["stage"], "failed")
                self.assertEqual(op["quota_status"], "not_read")
                self.assertNotIn("after", op)
                self.assertEqual(result["last_quota"], op["before"])
                methods = [c.get("method") for c in self.calls(result)]
                self.assertEqual(methods.count("turn/start"), 1)
                self.assertEqual(methods.count("account/rateLimits/read"), 1)
                self.assertEqual(methods.count("account/read"), 1)
                self.assertEqual(methods.count("account/login/start"), 0)

    def test_structured_failure_is_safe_and_survives_recovery_failure(self):
        for scenario, category in (("usage_limit", "usageLimitExceeded"),
                                   ("stream_error", "responseStreamDisconnected"),
                                   ("unsafe_error", "unknown"),
                                   ("recovery_failure", "responseStreamDisconnected")):
            with self.subTest(scenario=scenario):
                self.manager.root = self.root / scenario
                result = self.run_case(scenario)
                failure = result["operation"].get("failure", {})
                self.assertEqual(failure.get("category"), category)
                self.assertEqual(failure.get("stage"), "sending")
                self.assertNotIn("SECRET_FIXTURE_TOKEN", json.dumps(result))
                if scenario == "stream_error":
                    self.assertEqual(failure.get("http_status"), 503)
                if scenario == "recovery_failure":
                    self.assertEqual(result["operation"]["quota_status"], "not_read")
                    self.assertEqual(result["last_quota"], result["operation"]["before"])

    def test_failure_does_not_open_recovery_identity_or_quota_requests(self):
        result = self.run_case("recovery_identity")
        self.assertEqual(result["operation"]["quota_status"], "not_read")
        methods = [c.get("method") for c in self.calls(result)]
        self.assertEqual(methods.count("turn/start"), 1)
        self.assertEqual(methods.count("account/login/start"), 0)
        self.assertEqual(methods.count("account/read"), 1)
        self.assertEqual(methods.count("account/rateLimits/read"), 1)
        self.assertEqual(result["last_quota"], result["operation"]["before"])

    def test_before_is_retained_across_replacement_and_restart(self):
        for source in ("operation", "batch_result"):
            account = self.account(email=source + "@example.com")
            directory = self.manager.directory(account["id"])
            record = self.manager.read(directory)
            old = {"observed_at": "2026-10-08T01:00:00+00:00", "seven_day_used_pct": 16}
            latest = {"observed_at": "2026-10-08T02:00:00+00:00", "seven_day_used_pct": 100, "seven_day_resets_at": None}
            record[source] = {"stage": "failed", "before": latest, "after": old}
            ca.write_record(directory / "profile.json", record)
            with mock.patch.object(self.manager, "rpc_factory", side_effect=ca.ActionError("fixture failure")):
                self.manager.start(account["id"], refresh_only=True)
                result = self.wait(account["id"])
            self.assertEqual(result["last_quota"], latest)
            restarted = ca.Manager(self.manager.root)
            self.addCleanup(restarted.close)
            self.assertEqual(restarted.view(directory)["last_quota"], latest)

    def test_tools_are_rejected(self):
        result = self.run_case("tool_request")
        self.assertEqual(result["operation"]["stage"], "failed")
        self.assertIn("工具", result["operation"]["detail"])

    def test_official_link_validation_and_redacted_error(self):
        for scenario in ("bad_url", "login_error"):
            with self.subTest(scenario=scenario):
                result = self.run_case(scenario, login_only=True)
                self.assertEqual(result["operation"]["message_status"], "not_sent")
                self.assertNotIn("SECRET_FIXTURE_TOKEN", json.dumps(result))
                self.assertNotIn("evil.example", json.dumps(result))

    def test_pending_login_polling_duplicate_lock_and_cancellation(self):
        self.scenario = "wait_login"
        account = self.account(signed_in=False)
        self.manager.start(account["id"], login_only=True)
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
        self.assertEqual(result["operation"]["failure"]["category"], "timeout")
        self.assertEqual(result["operation"]["quota_status"], "not_read")
        self.assertEqual([c.get("method") for c in self.calls(result)].count("turn/start"), 1)

    def test_cancel_unknown_send_keeps_result_and_reaps_only_peer(self):
        self.scenario = "wait_completion"
        account = self.account()
        self.manager.start(account["id"])
        self.wait(account["id"], "sending")
        self.manager.cancel(account["id"])
        result = self.wait(account["id"])
        self.assertEqual(result["operation"]["message_status"], "unknown")
        self.assertEqual(result["operation"]["quota_status"], "not_read")
        self.assertEqual(result["operation"]["stage"], "cancelled")
        self.assertIn("unknown", result["restrictions"])
        self.assertEqual(len(self.spawned), 1)
        self.assertFalse(self.manager.jobs)
        self.assertEqual([c.get("method") for c in self.calls(result)].count("account/rateLimits/read"), 1)

    def test_missing_login_send_and_refresh_never_start_a_peer(self):
        account = self.account(signed_in=False)
        for refresh_only in (False, True):
            self.manager.start(account["id"], refresh_only=refresh_only)
            result = self.wait(account["id"])
            self.assertEqual(result["operation"]["failure"]["category"], "needsLogin")
            self.assertEqual(result["operation"]["message_status"], "not_sent")
        self.assertEqual(self.spawned, [])

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
        self.manager.start(account["id"], login_only=True)
        self.wait(account["id"])
        self.assertEqual(self.manager.add("ONE@example.com")["id"], account["id"])
        self.assertEqual(self.account()["id"], account["id"])


if __name__ == "__main__":
    unittest.main()
