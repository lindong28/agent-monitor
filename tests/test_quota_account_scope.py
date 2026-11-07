"""Account-scope admission exercised through the real isolated stdio peer."""
import json
import copy
import unittest
from unittest import mock
from contextlib import nullcontext, contextmanager
import test_codex_accounts
import test_quota_auth_recovery
from agent_monitor.accounts import codex_accounts as ca, account_holds


class AccountScopeTests(unittest.TestCase):
    setUp = test_codex_accounts.AccountActionsTests.setUp
    account = test_codex_accounts.AccountActionsTests.account
    wait = test_codex_accounts.AccountActionsTests.wait
    calls = test_codex_accounts.AccountActionsTests.calls

    def test_scope_parser_requires_actual_transport_and_rpc_context(self):
        from agent_monitor.quota.codex_quota import revoked_quota_transport
        message = 'failed to fetch codex rate limits: GET https://chatgpt.com/backend-api/wham/usage failed: 401 Unauthorized; content-type=application/json; body='
        body = {"error": {"code": "token_revoked"}}
        error = {"code": -32603, "message": message + json.dumps(body)}
        self.assertTrue(revoked_quota_transport(error, "account/rateLimits/read"))
        self.assertTrue(revoked_quota_transport({**error, "message": error["message"].replace("application/json;", "application/json; charset=utf-8;")}, "account/rateLimits/read"))
        for candidate, method in ((error, "account/read"), (error, "other"),
                ({**error, "code": -32601}, "account/rateLimits/read"),
                ({**error, "message": message + json.dumps({**body, "status": 500})}, "account/rateLimits/read"),
                ({**error, "message": message + "not-json"}, "account/rateLimits/read")):
            with self.subTest(candidate=candidate, method=method):
                self.assertFalse(revoked_quota_transport(candidate, method))

    def test_unreadable_rejected_credential_history_blocks_alias_and_login(self):
        rows = self.accounts()
        local = account_holds.controls(self.root, rows[0]["account_id"], rows[0]["email"])
        local.root.mkdir(parents=True)
        for content in ("{broken", json.dumps({"version": 1, "rejected_credentials": []})):
            with self.subTest(content=content):
                (local.root / "codex.json").write_text(content)
                self.assertTrue(account_holds.blocked(self.root, rows[1]["account_id"], rows[1]["email"], allow_rejected_login=True))
                self.assertFalse(self.manager.view(self.manager.directory(rows[1]["id"]))["login_required"])
                self.assertIn("无法核实", self.manager.view(self.manager.directory(rows[1]["id"]))["restriction_messages"]["credential_protection"])
                self.manager.start(rows[1]["id"], login_only=True)
                result = self.wait(rows[1]["id"])
                self.assertEqual(result["operation"]["failure"]["category"], "locallyPaused")
                self.assertEqual(self.spawned, [])

    def accounts(self):
        rows = [self.account(f"{name}@example.test", f"workspace-{name}") for name in "abc"]
        for row in rows:
            path = self.manager.directory(row["id"]) / "codex/auth.json"
            auth = json.loads(path.read_text())
            auth["tokens"]["access_token"] = row["email"] + "-fixture"
            path.write_text(json.dumps(auth))
        return rows

    def test_paused_operation_survives_ordinary_prepare_and_partial_batch(self):
        rows = self.accounts()
        self.scenario = "scoped_revoked"
        self.manager.start(rows[1]["id"], refresh_only=True)
        failed = self.wait(rows[1]["id"])
        self.assertEqual(failed["operation"]["failure"]["category"], "needsReauth")
        originals = {row["id"]: self.manager.read(self.manager.directory(row["id"])) for row in rows}
        for options in ({}, {"refresh_only": True}, {"login_only": True}):
            with self.subTest(options=options), self.assertRaises(ca.ActionError):
                self.manager.start(rows[1]["id"], **options)
            self.assertEqual(self.manager.read(self.manager.directory(rows[1]["id"])), originals[rows[1]["id"]])
        with self.assertRaises(ca.ActionError):
            self.manager.start_batch(None, {row["id"] for row in rows[:2]})
        self.assertIsNone(self.manager.read_batch())
        # Model a hold appearing after the batch's initial list snapshot.
        stale = copy.deepcopy(self.manager.list())
        for row in stale["accounts"]:
            row["account_quota_pause"] = None
        with mock.patch.object(self.manager, "list", return_value=stale), self.assertRaises(ca.ActionError):
            self.manager.start_batch(None, {row["id"] for row in rows[:2]})
        for row in rows:
            self.assertEqual(self.manager.read(self.manager.directory(row["id"])), originals[row["id"]])
        self.assertIsNone(self.manager.read_batch())
        self.assertFalse(self.manager.jobs)

        batch = {"id": "fixture-send-batch", "profile_ids": [row["id"] for row in rows[:2]]}
        ca.write_record(self.manager.root / "batch.json", batch)
        with self.assertRaises(ca.ActionError):
            self.manager.retry_batch(batch["id"])
        for row in rows:
            self.assertEqual(self.manager.read(self.manager.directory(row["id"])), originals[row["id"]])
        self.assertEqual(self.manager.read_batch(), batch)
        self.assertFalse(self.manager.jobs)

    def test_batch_rollback_preserves_update_before_profile_lock(self):
        rows = self.accounts()[:2]
        ids = [row["id"] for row in rows]
        first = self.manager.directory(ids[0])
        baseline = self.manager.read(first)
        prepare = self.manager.prepare
        for action in ("start", "retry", "refresh"):
            with self.subTest(action=action):
                ca.write_record(first / "profile.json", baseline)
                batch = {"id": "fixture-send-batch", "profile_ids": ids}
                ca.write_record(self.manager.root / "batch.json", batch)
                expected = copy.deepcopy(baseline)
                expected["disabled"] = True
                expected["reviewed_operation_id"] = "concurrent-review"

                def prepare_after_update(profile_id, **kwargs):
                    if profile_id == ids[1]:
                        raise ca.ActionError("fixture second member became unavailable", 409)
                    # A writer completes after listing but before prepare takes its lock.
                    with ca.lock_file(first / ".lock"):
                        ca.write_record(first / "profile.json", expected)
                    return prepare(profile_id, **kwargs)

                with mock.patch.object(self.manager, "prepare", side_effect=prepare_after_update), \
                     mock.patch.object(self.manager, "launch_sequence") as launch, \
                     self.assertRaises(ca.ActionError):
                    if action == "start":
                        self.manager.start_batch(batch["id"], set(ids))
                    elif action == "retry":
                        self.manager.retry_batch(batch["id"], set(ids))
                    else:
                        self.manager.start_refresh_batch(ids)
                launch.assert_not_called()
                self.assertEqual(self.manager.read(first), expected)
                self.assertEqual(self.manager.read_batch(), batch)
                with ca.lock_file(first / ".lock"):
                    pass  # The failed transaction releases every prepared profile lock.

    def test_layered_failure_exposes_manual_shared_acknowledgement_then_account_probe(self):
        from agent_monitor.quota import quota_service, quota_refresh, quota_recovery
        from agent_monitor.snapshots import exporter
        from agent_monitor.accounts import upstream_controls
        from parsers.codex_profiles import SessionLinks
        self.manager.close()
        self.manager = ca.Manager(self.root / "state/codex-accounts")
        self.addCleanup(self.manager.close)
        row = self.accounts()[0]
        self.scenario = "scoped_revoked"
        self.manager.start(row["id"], refresh_only=True)
        original = self.wait(row["id"])
        profile = "monitor-" + row["id"]
        auth_path = self.manager.directory(row["id"]) / "codex/auth.json"
        with mock.patch.object(exporter, "ROOT", self.root), mock.patch('agent_monitor.hub_config.is_hub', return_value=True), \
             mock.patch('agent_monitor.snapshots.generation.self_certified_host_identity', return_value='fixture'), \
             mock.patch('parsers.codex_profiles.session_links', return_value=SessionLinks()), \
             mock.patch('parsers.codex_profiles.discover', return_value=[]), \
             mock.patch.object(quota_refresh.network_policy, 'child_environment', return_value={}):
            child = mock.Mock(returncode=1)
            def fail_after_new_credentials(*args, **kwargs):
                auth = json.loads(auth_path.read_text())
                auth["tokens"]["access_token"] = "new-fixture-credential"
                auth_path.write_text(json.dumps(auth))
                return json.dumps({"error": "fixture quota unavailable", "failure": {
                    "stage": "quota_read", "kind": "rpc_error", "http_status": 500},
                    "authentication_recovery": {"outcome": "quota_failed", "initial_failure": original["account_quota_pause"]["failure"]}}), ""
            child.communicate.side_effect = fail_after_new_credentials
            with mock.patch.object(quota_refresh.subprocess, "Popen", return_value=child) as spawn:
                with self.assertRaises(quota_refresh.RefreshError):
                    quota_service.recover_codex_quota(row["email"], original["account_quota_pause"]["id"],
                        expected_id=row["account_id"], root=self.root, profile=profile, renew_auth=True, pause_scope="account", admitted_probe={})
                local = quota_recovery.local_status(profile=profile, pause_scope="account")
                shared = quota_recovery.local_status()
                self.assertIsNotNone(local["shared_pause"])
                self.assertTrue(local["account"]["blocked_reason"])
                self.assertEqual(shared["account"]["account_quota_pause"], local["pause"])
                # Existing explicit operator action, after investigating the shared cause.
                with mock.patch('builtins.print'):
                    self.assertEqual(upstream_controls.main(["resume", "codex", "--pause-id", shared["pause"]["id"],
                        "--state-root", str(self.manager.controls.root)]), 0)
                self.assertEqual(spawn.call_count, 1, "manual acknowledgement never calls provider")
            after = quota_recovery.local_status(profile=profile, pause_scope="account")
            self.assertIsNone(after["shared_pause"])
            self.assertIsNone(after["account"]["blocked_reason"])
            self.assertEqual(after["pause"], local["pause"], "manual shared acknowledgement must retain account protection")

    def test_revoked_accounts_do_not_stop_independent_account_or_replay_next_batch(self):
        rows = self.accounts()
        def peer(directory, cancelled):
            email = self.manager.read(directory)["email"]
            self.scenario = "success" if email.startswith("c@") else "scoped_no_status" if email.startswith("b@") else "scoped_revoked"
            return ca.Rpc(directory, cancelled)
        self.manager.rpc_factory = peer
        self.manager.start_refresh_batch([r["id"] for r in rows])
        done = [self.wait(r["id"]) for r in rows]
        self.assertEqual([r["operation"]["stage"] for r in done], ["failed", "failed", "succeeded"])
        self.assertIsNone(self.manager.controls.read("codex").get("pause"))
        self.assertTrue(all(r["account_quota_pause"] for r in done[:2]))
        self.assertNotEqual(done[0]["account_quota_pause"]["id"], done[1]["account_quota_pause"]["id"])
        auth = self.manager.directory(rows[0]["id"]) / "codex/auth.json"
        before = auth.read_bytes()
        with self.assertRaises(ca.ActionError):
            self.manager.forget_login(rows[0]["id"])
        self.assertEqual(auth.read_bytes(), before)
        self.assertEqual(self.manager.view(auth.parent.parent)["account_quota_pause"], done[0]["account_quota_pause"])
        self.assertEqual(len(self.spawned), 3)
        self.manager.close()
        self.manager = ca.Manager(self.root / "profiles", rpc_factory=peer)
        self.addCleanup(self.manager.close)
        self.manager.start_refresh_batch([r["id"] for r in rows])
        again = [self.wait(r["id"]) for r in rows]
        self.assertEqual(len(self.spawned), 4)
        self.assertEqual(again[2]["operation"]["stage"], "succeeded")
        for index in range(2):
            self.assertEqual(again[index]["operation"], done[index]["operation"])

    def test_body_only_or_other_status_or_changed_credential_remains_shared(self):
        for scenario in ("scoped_body", "scoped_500", "scoped_403", "scoped_generic", "scoped_changed"):
            with self.subTest(scenario=scenario):
                self.manager.close()
                self.manager = ca.Manager(self.root / scenario / "profiles")
                self.addCleanup(self.manager.close)
                rows = self.accounts()
                self.scenario = scenario
                self.manager.start_refresh_batch([r["id"] for r in rows])
                done = [self.wait(r["id"]) for r in rows]
                self.assertIsNotNone(self.manager.controls.read("codex").get("pause"))
                self.assertEqual(done[1]["operation"]["failure"]["category"], "locallyPaused")

    def test_rejected_digest_alias_survives_pause_clear_and_forces_explicit_login(self):
        rows = self.accounts()
        original = self.manager.directory(rows[0]["id"]) / "codex/auth.json"
        alias = self.manager.directory(rows[1]["id"]) / "codex/auth.json"
        auth = json.loads(alias.read_text())
        auth["tokens"]["access_token"] = json.loads(original.read_text())["tokens"]["access_token"]
        alias.write_text(json.dumps(auth))
        self.scenario = "scoped_revoked"
        self.manager.start_refresh_batch([r["id"] for r in rows[:2]])
        done = [self.wait(r["id"]) for r in rows[:2]]
        self.assertEqual(len(self.spawned), 1)
        self.assertEqual(done[1]["operation"]["failure"]["category"], "locallyPaused")
        controls = account_holds.controls(self.root, rows[0]["account_id"], rows[0]["email"])
        controls.resume("codex", controls.read("codex")["pause"]["id"])
        self.assertTrue(account_holds.blocked(self.root, rows[1]["account_id"], rows[1]["email"], auth_path=alias))
        self.assertTrue(self.manager.view(alias.parent.parent)["login_required"])
        self.scenario = "success"
        self.manager.start(rows[1]["id"], login_only=True)
        result = self.wait(rows[1]["id"])
        self.assertEqual(result["operation"]["stage"], "succeeded")
        self.assertIn("account/login/start", [x["method"] for x in self.calls(rows[1])])
        self.assertTrue(account_holds.blocked(self.root, rows[0]["account_id"], rows[0]["email"], auth_path=original))

    def test_scoped_renew_status_and_failed_no_new_credentials_keep_independent_receipts(self):
        from datetime import datetime, timezone
        from dataclasses import asdict
        from agent_monitor.contracts.usage import RateLimits
        from agent_monitor.quota import quota_service, quota_refresh, quota_recovery
        from agent_monitor.snapshots import exporter
        from parsers.codex_profiles import SessionLinks
        self.manager.close()
        self.manager = ca.Manager(self.root / "state/codex-accounts")
        self.addCleanup(self.manager.close)
        rows = self.accounts()
        self.scenario = "scoped_revoked"
        self.manager.start_refresh_batch([r["id"] for r in rows[:2]])
        done = [self.wait(r["id"]) for r in rows[:2]]
        local_a = account_holds.controls(self.root / "state", rows[0]["account_id"], rows[0]["email"])
        local_b = account_holds.controls(self.root / "state", rows[1]["account_id"], rows[1]["email"])
        before_b = local_b.read("codex")
        from parsers.accounts import Account
        with mock.patch.object(quota_refresh.subprocess, "Popen") as spawn:
            for row in rows[:2]:
                with self.assertRaises(quota_refresh.LocalUnavailable):
                    quota_refresh.load_rate_limits("codex", Account(row["account_id"], row["email"]), root=self.root,
                        codex_home=self.manager.directory(row["id"]) / "codex")
            spawn.assert_not_called()
        selected = "monitor-" + rows[0]["id"]
        with mock.patch.object(exporter, "ROOT", self.root), mock.patch('agent_monitor.hub_config.is_hub', return_value=True), \
             mock.patch('agent_monitor.snapshots.generation.self_certified_host_identity', return_value='fixture'), \
             mock.patch('parsers.codex_profiles.session_links', return_value=SessionLinks()), \
             mock.patch('parsers.codex_profiles.discover', return_value=[]), \
             mock.patch.object(quota_refresh.network_policy, 'child_environment', return_value={}):
            status = quota_recovery.local_status(profile=selected, pause_scope="account")
            self.assertTrue(status["account"]["auth_recovery_available"])
            self.assertIsNone(status["account"]["blocked_reason"])
            self.assertEqual(status["account"]["quota_profile"], selected)
            self.assertNotIn("rejected_credentials", json.dumps(status))
            child = mock.Mock(returncode=1)
            child.communicate.return_value = (json.dumps({"error": "fixture no new credentials",
                "failure": {"stage": "credentials", "kind": "invalid_result"},
                "authentication_recovery": {"outcome": "no_new_credentials", "initial_failure": status["pause"]["failure"]}}), "")
            carrier = {}
            with mock.patch.object(quota_refresh.subprocess, "Popen", return_value=child):
                with self.assertRaises(quota_refresh.RefreshError):
                    quota_service.recover_codex_quota(rows[0]["email"], status["pause"]["id"], expected_id=rows[0]["account_id"],
                        root=self.root, admitted_probe=carrier, profile=selected, renew_auth=True, pause_scope="account")
            self.assertEqual(child.communicate.call_count, 1)
            self.assertEqual(carrier["authentication_recovery"]["outcome"], "no_new_credentials")
            self.assertNotIn("pause", self.manager.controls.read("codex"))
            self.assertEqual(local_b.read("codex"), before_b)
            # B has its own untouched recovery opportunity and receipt.
            reading = RateLimits(17, 1900000000, None, None, updated_at=datetime.now(timezone.utc).isoformat())
            child = mock.Mock(returncode=0)
            child.communicate.return_value = (json.dumps({"reading": asdict(reading),
                "authentication_recovery": {"outcome": "quota_confirmed", "initial_failure": before_b["pause"]["failure"]}}), "")
            with mock.patch.object(quota_refresh.subprocess, "Popen", return_value=child):
                quota_service.recover_codex_quota(rows[1]["email"], before_b["pause"]["id"], expected_id=rows[1]["account_id"],
                    root=self.root, admitted_probe={}, profile="monitor-" + rows[1]["id"], renew_auth=True, pause_scope="account")
            self.assertIsNone(local_b.read("codex").get("pause"))
            self.assertIsNotNone(local_a.read("codex").get("pause"))
            self.assertIn("rejected_credentials", local_b.read("codex"))
            self.assertNotIn("active", self.manager.controls.read("codex"))


class ScopedReauthorizationTests(unittest.TestCase):
    _Cases = test_codex_accounts.AccountActionsTests
    setUp = test_quota_auth_recovery.ReauthorizationTests.setUp
    account = _Cases.account
    wait = _Cases.wait
    calls = _Cases.calls

    def seed(self):
        account, binding = test_quota_auth_recovery.ReauthorizationTests.seed(self)
        self.local = account_holds.controls(self.root, account["account_id"], account["email"])
        self.local.root.mkdir(parents=True)
        state = self.manager.controls.read("codex")
        state["last_recovery"]["request"]["pause_scope"] = "account"
        state["rejected_credentials"] = {"a" * 64: "fixture"}
        self.local._save("codex", state)
        self.manager.controls._save("codex", {"version": 1})
        binding["pause_scope"] = "account"
        return account, binding

    def test_scoped_reauthorization_closes_both_leases_and_preserves_other_account(self):
        account, binding = self.seed()
        other = account_holds.controls(self.root, "other", "other@example.test")
        lease = other.acquire("codex")
        lease.pause("fixture")
        lease.close()
        other_before = other.read("codex")
        self.manager.start_reauthorization(account["id"], binding, publication=lambda block: nullcontext())
        result = self.wait(account["id"])
        self.assertEqual(result["operation"]["stage"], "succeeded")
        self.assertNotIn("pause", self.local.read("codex"))
        self.assertNotIn("active", self.manager.controls.read("codex"))
        self.assertNotIn("pause", self.manager.controls.read("codex"))
        self.assertEqual(other.read("codex"), other_before)
        self.assertIn("a" * 64, self.local.read("codex")["rejected_credentials"])

    def test_scoped_retained_candidate_commit_is_local_and_releases_shared_lane(self):
        account, binding = self.seed()
        @contextmanager
        def broken(block):
            raise OSError("fixture commit failure")
            yield
        self.manager.start_reauthorization(account["id"], binding, publication=broken)
        failed = self.wait(account["id"])
        self.assertEqual(failed["operation"]["stage"], "failed")
        state = self.local.read("codex")
        self.assertEqual(state["pause"]["reason"], "localRecoveryCommitFailed")
        self.assertNotIn("pause", self.manager.controls.read("codex"))
        count = len(self.spawned)
        done = self.manager.commit_saved_recovery(account["id"], failed["operation"]["id"], state["pause"]["id"],
            pause_scope="account", publication=lambda block: nullcontext())
        self.assertEqual(done["operation"]["stage"], "succeeded")
        self.assertEqual(len(self.spawned), count)
        self.assertNotIn("active", self.manager.controls.read("codex"))
        self.assertNotIn("active", self.local.read("codex"))

    def test_shared_pause_blocks_scoped_reauthorization_before_rpc(self):
        account, binding = self.seed()
        lease = self.manager.controls.acquire("codex")
        lease.pause("fixture-shared")
        lease.close()
        before = self.manager.controls.read("codex")
        self.manager.start_reauthorization(account["id"], binding, publication=lambda block: nullcontext())
        result = self.wait(account["id"])
        self.assertEqual(result["operation"]["failure"]["category"], "locallyPaused")
        self.assertEqual(self.spawned, [])
        self.assertEqual(self.manager.controls.read("codex"), before)


class ScopedHttpTests(unittest.TestCase):
    def test_http_selection_and_approval_cannot_reuse_global_scope(self):
        import http.client
        import threading
        from types import SimpleNamespace
        from agent_monitor import http_server as server
        from agent_monitor.quota import quota_recovery
        from agent_monitor.network.client_network import ApprovalStore
        profile = "monitor-11111111-1111-4111-8111-111111111111"
        machine = SimpleNamespace(name="fixture", is_self=True)
        request = {"machine": "fixture", "account_id": "workspace", "account_label": "one@example.test",
            "profile": profile, "pause_id": "a" * 32, "mode": "renew_auth", "pause_scope": "account"}
        manager = mock.MagicMock()
        manager.recovery_scope.return_value.__enter__.return_value = True
        manager.recovery_status.return_value = {"pause_scope": "account", "account": {"quota_profile": profile}}
        manager.recover.return_value = ({"ok": True}, 200)
        approvals = ApprovalStore()
        with mock.patch.object(server, "UPSTREAM_APPROVALS", approvals), mock.patch.object(server, "QUOTA_REFRESH", manager), \
             mock.patch.object(server.hub, "enabled", return_value=False), \
             mock.patch.object(server.sync, "load_machine_config", return_value=SimpleNamespace(by_name={"fixture": machine})):
            httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
            thread = threading.Thread(target=httpd.serve_forever)
            thread.start()
            def call(method, path, body=None):
                conn = http.client.HTTPConnection("127.0.0.1", httpd.server_port, timeout=3)
                try:
                    conn.request(method, path, json.dumps(body) if body else None, {"Content-Type": "application/json",
                        "X-Agent-Monitor": "action", "Origin": f"http://127.0.0.1:{httpd.server_port}"})
                    response = conn.getresponse()
                    return response.status, json.loads(response.read())
                finally:
                    conn.close()
            try:
                self.assertEqual(call("GET", f"/api/quota-recovery?machine=self&profile={profile}&pause_scope=account")[0], 200)
                manager.recovery_status.assert_called_once_with(machine, profile=profile, pause_scope="account")
                scope = quota_recovery.approval_scope(request)
                old = approvals.issue("quota-auth-recovery", scope[:-1], request["pause_id"])
                self.assertEqual(call("POST", "/api/quota-recovery", {**request, "confirmation": old})[0], 409)
                manager.recover.assert_not_called()
                token = approvals.issue("quota-auth-recovery", scope, request["pause_id"])
                self.assertEqual(call("POST", "/api/quota-recovery", {**request, "confirmation": token})[0], 200)
                manager.recover.assert_called_once_with(machine, request)
                self.assertEqual(call("POST", "/api/quota-recovery", {**request, "confirmation": token})[0], 409)
            finally:
                httpd.shutdown()
                httpd.server_close()
                thread.join(3)


if __name__ == "__main__":
    unittest.main()
