import dataclasses
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import exporter
import quota_refresh
import refresh
import server
from parsers import RateLimits, accounts


class ProviderTests(unittest.TestCase):
    def test_network_prerequisite_failure_is_local_and_never_starts_child(self):
        from upstream_controls import Controls
        account = accounts.Account('workspace', 'fixture@example.invalid')
        with mock.patch.object(quota_refresh.network_policy, 'endpoint', side_effect=quota_refresh.network_policy.NetworkPolicyError('runtime_missing')), \
                mock.patch('quota_refresh.subprocess.Popen') as spawn:
            with self.assertRaises(quota_refresh.LocalUnavailable):
                quota_refresh.load_rate_limits('codex', account)
        spawn.assert_not_called()
        self.assertNotIn('pause', Controls(self.root / 'state/upstream_controls').read('codex'))

    def setUp(self):
        route = mock.patch.object(quota_refresh.network_policy, "endpoint", return_value="http://agent-monitor:synthetic-test-capability@127.0.0.1:9")
        route.start()
        self.addCleanup(route.stop)
        root = tempfile.TemporaryDirectory()
        self.addCleanup(root.cleanup)
        self.root = Path(root.name)
        for module in (quota_refresh, exporter):
            patcher = mock.patch.object(module, "ROOT", self.root)
            patcher.start()
            self.addCleanup(patcher.stop)
        sleeper = mock.patch("exporter.time.sleep")
        self.sleep = sleeper.start()
        self.addCleanup(sleeper.stop)

    def test_recovery_keeps_pause_until_cache_saved_and_reads_only_codex(self):
        from upstream_controls import Controls, Paused
        controls = Controls(self.root / "state/upstream_controls")
        lease = controls.acquire("codex")
        lease.pause("original")
        lease.close()
        pause_id = controls.read("codex")["pause"]["id"]
        account = accounts.Account("workspace", "expected@example.test")
        reading = RateLimits(5, 4102444800, 20, 4102444800, updated_at="2026-10-22T00:00:00Z")
        payload = json.dumps({"reading": dataclasses.asdict(reading)})
        child = SimpleNamespace(pid=0, returncode=0, communicate=lambda *a, **k: (payload, ""))
        cache = self.root / "state/quota_snapshot.json"
        cache.write_text(json.dumps({"claude": {"account_id": "other", "refresh_error": "retained"}}))
        save = exporter._save_quota_cache

        def save_while_paused(path, result):
            self.assertEqual(set(result), {"codex"})
            with self.assertRaises(Paused):
                controls.acquire("codex")
            save(path, result)

        with mock.patch("exporter.accounts.codex_account", return_value=account), \
                mock.patch("quota_refresh.subprocess.Popen", return_value=child) as spawn, \
                mock.patch("exporter._save_quota_cache", side_effect=save_while_paused):
            exporter.recover_codex_quota(account.label, pause_id)
        self.assertEqual(spawn.call_count, 1)
        state = controls.read("codex")
        self.assertNotIn("pause", state)
        self.assertEqual(state["last_pause"]["id"], pause_id)
        self.assertIn("probe_attempt", state["last_pause"])
        stored = json.loads(cache.read_text())
        self.assertEqual(stored["claude"]["refresh_error"], "retained")
        self.assertIsNone(stored["codex"]["refresh_error"])
        self.assertEqual(stored["codex"]["account_label"], account.label)

    def test_recovery_mismatch_failure_and_cache_failure_keep_pause(self):
        from upstream_controls import Controls
        account = accounts.Account("workspace", "expected@example.test")
        other = accounts.Account("workspace", "other@example.test")
        reading = RateLimits(5, 4102444800, 20, 4102444800, updated_at="2026-10-22T00:00:00Z")
        for mode in ("before", "after", "upstream", "cache"):
            with self.subTest(mode=mode):
                controls = Controls(self.root / "state/upstream_controls")
                controls.root.mkdir(parents=True, exist_ok=True)
                controls._save("codex", {"version": 1, "pause": {"id": mode, "reason": "original"}})
                payload = {"error": "fixture failure"} if mode == "upstream" else {"reading": dataclasses.asdict(reading)}
                child = SimpleNamespace(pid=0, returncode=0, communicate=lambda *a, **k: (json.dumps(payload), ""))
                identities = [other] if mode == "before" else [account, other if mode == "after" else account]
                with mock.patch("exporter.accounts.codex_account", side_effect=identities), \
                        mock.patch("quota_refresh.subprocess.Popen", return_value=child) as spawn, \
                        mock.patch("exporter._save_quota_cache", side_effect=OSError("fixture cache failure")) as save:
                    with self.assertRaises((quota_refresh.RefreshError, OSError)):
                        exporter.recover_codex_quota(account.label, mode)
                self.assertEqual(spawn.call_count, 0 if mode == "before" else 1)
                self.assertEqual(save.call_count, 1 if mode == "cache" else 0)
                state = controls.read("codex")
                self.assertIn("pause", state)
                if mode == "upstream":
                    self.assertEqual(state["last_pause"]["id"], mode)
                elif mode != "before":
                    self.assertIn("probe_attempt", state["pause"])

    def test_one_attempt_per_provider_and_success_clears_persisted_error(self):
        old = RateLimits(3, None, 20, None, updated_at="2026-08-01T00:00:00Z")
        fresh = dataclasses.replace(old, seven_day_pct=0, updated_at="2026-09-29T00:00:00Z")
        for outcome in (fresh, quota_refresh.RefreshError("still offline"),
                        quota_refresh.NotSignedIn("signed out")):
            with self.subTest(outcome=type(outcome).__name__), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                cache = root / "state/quota_snapshot.json"
                cache.parent.mkdir()
                account = accounts.Account("a")
                previous = exporter._rate_limit_block(old, account)
                previous["refresh_error"] = "previous failure"
                cache.write_text(json.dumps({"claude": previous}))
                calls = {"claude": 0, "codex": 0}

                def live(provider, account):
                    calls[provider] += 1
                    if provider == "codex":
                        return fresh
                    if isinstance(outcome, quota_refresh.NotSignedIn):
                        raise outcome
                    if isinstance(outcome, Exception):
                        raise outcome
                    return outcome

                self.sleep.reset_mock()
                with mock.patch.object(exporter, "ROOT", root), \
                        mock.patch("hub.enabled", return_value=True), \
                        mock.patch("exporter.quota_refresh.load_rate_limits", side_effect=live), \
                        mock.patch("exporter.claude_status.load_rate_limits", return_value=old), \
                        mock.patch("exporter.codex.load_rate_limits", return_value=old), \
                        mock.patch("exporter.accounts.claude_account", return_value=account), \
                        mock.patch("exporter.accounts.codex_account", return_value=account):
                    result = exporter._rate_limits(refresh=True)["claude"]
                    passive = exporter._rate_limits(refresh=False)["claude"]
                signed_out = isinstance(outcome, quota_refresh.NotSignedIn)
                self.assertEqual(calls, {"claude": 1, "codex": 1})
                self.sleep.assert_not_called()
                written = json.loads(cache.read_text())["claude"]
                if isinstance(outcome, RateLimits):
                    for block in (result, written, passive):
                        self.assertIsNone(block["refresh_error"])
                        self.assertEqual(block["seven_day_pct"], 0)
                        self.assertEqual(block["updated_at"], fresh.updated_at)
                else:
                    self.assertEqual(result["updated_at"], old.updated_at)
                    self.assertEqual(result["seven_day_pct"], 20)
                    self.assertEqual(result["signed_out"], signed_out)
                    self.assertEqual(result["refresh_error"], None if signed_out else "still offline")

    def codex_cli(self, response, account_id="account-a", error=None, error_id=2):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            shim = home / ".local/libexec/agent-shims/codex"
            shim.parent.mkdir(parents=True)
            shim.write_text("#!%s\nimport sys,json\nresponse=%r\nerror=%r\nerror_id=%r\n" %
                            (sys.executable, response, error, error_id) +
                            "for line in sys.stdin:\n m=json.loads(line)\n if 'id' in m:\n"
                            "  reply={'error':error} if error is not None and m['id']==error_id else {'result':{} if m['id']==1 else response}\n"
                            "  print(json.dumps({'id':m['id'],**reply}),flush=True)\n")
            shim.chmod(0o700)
            with mock.patch("quota_refresh.Path.home", return_value=home), \
                    mock.patch.object(quota_refresh.network_policy, "endpoint", return_value="http://agent-monitor:synthetic-test-capability@127.0.0.1:9"):
                return quota_refresh._codex({"account_id": account_id})

    def test_codex_uses_window_duration_and_matching_account(self):
        reading = self.codex_cli({"accountId": "account-a", "rateLimits": {
            "primary": {"usedPercent": 62, "windowDurationMins": 10080, "resetsAt": 1900000000},
            "secondary": {"usedPercent": 9, "windowDurationMins": 300, "resetsAt": 1800000000},
            "planType": "pro"}})
        self.assertEqual((reading.five_hour_pct, reading.seven_day_pct), (9, 62))
        self.assertEqual(reading.plan, "pro")
        self.assertTrue(reading.updated_at)

    def test_codex_revoked_token_requires_login_without_exposing_upstream_text(self):
        # Shape captured from the node-c CLI's failed quota RPC, with no secrets.
        error = {"code": -32603, "message":
                 'failed to fetch codex rate limits: GET https://example.test/private failed: '
                 '401 Unauthorized; content-type=text/plain; body=' + json.dumps({
                     "error": {"message": "private upstream detail", "code": "token_revoked"},
                     "status": 401})}
        with self.assertRaises(quota_refresh.RefreshError) as caught:
            self.codex_cli({}, error=error)
        message = str(caught.exception)
        self.assertIn("重新登录", message)
        self.assertNotIn("更新", message)
        self.assertNotIn("private", message)
        self.assertNotIn("example.test", message)
        self.assertNotIsInstance(caught.exception, quota_refresh.NotSignedIn)
        self.assertEqual(caught.exception.failure["stage"], "quota_read")
        self.assertEqual(caught.exception.failure["http_status"], 401)
        self.assertEqual(caught.exception.failure["rpc_code"], -32603)

    def test_codex_only_unsupported_rpc_recommends_cli_update(self):
        for request_id in (1, 2):
            for error, upgrade in (({"code": -32601}, True),
                                   ({"code": -32603, "message": "private upstream detail"}, False),
                                   ({"code": -32603, "message": "body=not JSON token_revoked"}, False),
                                   ({"code": -32603, "message": 'body={"error": []}'}, False)):
                with self.subTest(request_id=request_id, error=error):
                    with self.assertRaises(quota_refresh.RefreshError) as caught:
                        self.codex_cli({}, error=error, error_id=request_id)
                    self.assertEqual("更新" in str(caught.exception), upgrade)
                    self.assertNotIn("private", str(caught.exception))
                    self.assertNotIn("重新登录", str(caught.exception))
                    self.assertNotIsInstance(caught.exception, quota_refresh.NotSignedIn)
                    self.assertEqual(caught.exception.failure["stage"], "initialize" if request_id == 1 else "quota_read")

    def test_codex_rejects_other_account_and_missing_identity(self):
        for identity in ("other", None):
            with self.subTest(identity=identity), self.assertRaises(quota_refresh.RefreshError):
                self.codex_cli({"accountId": identity, "rateLimits": {"primary": {
                    "usedPercent": 62, "windowDurationMins": 10080}}})

    def test_live_failure_preserves_cached_value_and_timestamp(self):
        old = RateLimits(3, None, 20, None, updated_at="2026-08-01T00:00:00Z")
        fresh = RateLimits(None, None, 55, None, updated_at="2026-09-08T00:00:00Z")

        def live(provider, account):
            if provider == "claude":
                raise quota_refresh.RefreshError("request failed")
            return fresh

        with mock.patch("exporter.quota_refresh.load_rate_limits", side_effect=live), \
                mock.patch("exporter.claude_status.load_rate_limits", return_value=old), \
                mock.patch("exporter.accounts.claude_account", return_value=accounts.Account("a")), \
                mock.patch("exporter.accounts.codex_account", return_value=accounts.Account("b")):
            result = exporter._rate_limits(refresh=True)
        self.assertEqual(result["claude"]["updated_at"], old.updated_at)
        self.assertEqual(result["claude"]["seven_day_pct"], 20)
        self.assertEqual(result["claude"]["refresh_error"], "request failed")
        self.assertEqual(result["codex"]["updated_at"], fresh.updated_at)
        self.assertIsNone(result["codex"]["refresh_error"])

    def claude_with_tokens(self, responses, account_id="acct-1"):
        """Drive `_claude` against a stubbed read-only OAuth helper.

        `responses` maps token -> either an HTTPError to raise or the profile
        dict that token's credential resolves to.
        """
        import io, types, urllib.error

        class _Response(io.StringIO):
            def __enter__(self): return self
            def __exit__(self, *exc): return False

        helper = types.ModuleType("agent_monitor_usage")
        helper.OAUTH_BETA = "beta"
        helper.TIMEOUT_S = 5
        helper.read_tokens = lambda: iter(responses)
        helper.fetch = lambda token: {"five_hour": {"utilization": 11},
                                      "seven_day": {"utilization": 22}}
        helper.to_epoch = lambda value: None

        class _Opener:
            def open(self, request, timeout=None):
                token = request.headers["Authorization"].split(" ", 1)[1]
                outcome = responses[token]
                if isinstance(outcome, urllib.error.HTTPError):
                    raise outcome
                return _Response(json.dumps(outcome))

        helper._OPENER = _Opener()
        helper._OPENER.open = mock.Mock(wraps=helper._OPENER.open)
        self.claude_requests = helper._OPENER.open
        with mock.patch("quota_refresh.claude_oauth", helper):
            return quota_refresh._claude({"account_id": account_id})

    def test_missing_claude_credentials_and_mismatched_identity_remain_distinct(self):
        with self.assertRaises(quota_refresh.NotSignedIn):
            self.claude_with_tokens({})
        with self.assertRaises(quota_refresh.RefreshError) as caught:
            self.claude_with_tokens({"tok-a": {"account": {"uuid": "someone-else"}}})
        self.assertNotIsInstance(caught.exception, quota_refresh.NotSignedIn)
        reading = self.claude_with_tokens({"tok-a": {"account": {"uuid": "someone-else"}},
                                          "tok-b": {"account": {"uuid": "acct-1"}}})
        self.assertEqual((reading.five_hour_pct, reading.seven_day_pct), (11, 22))

    def test_claude_refusal_never_rotates_credentials(self):
        import urllib.error
        for status in (401, 403, 429):
            with self.subTest(status=status):
                refusal = urllib.error.HTTPError("u", status, "refused", {}, None)
                with self.assertRaises(quota_refresh.RefreshError) as caught:
                    self.claude_with_tokens({"tok-a": refusal,
                                             "tok-b": {"account": {"uuid": "acct-1"}}})
                self.assertNotIsInstance(caught.exception, quota_refresh.NotSignedIn)
                self.assertEqual(self.claude_requests.call_count, 1)
                self.assertEqual(caught.exception.failure["stage"], "profile_read")
                self.assertEqual(caught.exception.failure["http_status"], status)

    def test_a_claude_http_error_that_is_not_an_auth_refusal_stays_a_failure(self):
        """A 500 from a reachable endpoint is not a sign-out."""
        import urllib.error
        server_error = urllib.error.HTTPError("u", 500, "Server Error", {}, None)

        with self.assertRaises(quota_refresh.RefreshError) as caught:
            self.claude_with_tokens({"tok-a": server_error})
        self.assertNotIsInstance(caught.exception, quota_refresh.NotSignedIn)

    def test_a_codex_cli_too_old_to_answer_is_a_failure_not_a_sign_out(self):
        """The carve-out the requirement names, and the easiest one to lose.

        `receive()` also carries the `initialize` handshake, so classifying any
        JSON-RPC error as "not signed in" retires the alarm for an outdated CLI
        on a machine that is signed in — and retires it silently, because
        signed-out machines are reported as a state rather than a failure.
        """
        cases = {
            "error on the initialize handshake": {"error": {"code": -32601}},
            "error on the quota read": {"error": {"code": -32601}},
            "a result with no account field": {"rateLimits": {}},
            "a result naming another account": {"accountId": "someone-else"},
        }
        for label, response in cases.items():
            with self.subTest(label):
                with self.assertRaises(quota_refresh.RefreshError) as caught:
                    self.codex_cli(response)
                self.assertNotIsInstance(caught.exception, quota_refresh.NotSignedIn)

    def test_a_cached_signed_out_verdict_clears_as_soon_as_the_machine_is_used(self):
        """Bounds how long a stale "not signed in" label can survive.

        A passive export cannot tell a lapsed credential from a working one —
        that takes a query — so it republishes the last verdict an active
        refresh reached. After a re-sign-in that verdict is wrong, and the only
        two things that end it are the hourly active refresh and the first
        newer local reading. The second is what keeps the window short, and it
        is a property of which block wins rather than anything explicit, so it
        would regress without a sound.
        """
        account = accounts.Account("acct-1", "me@example.test", "pro")
        cached = exporter._rate_limit_block(
            RateLimits(8, None, 41, None, updated_at="2026-09-14T00:00:00+00:00"), account
        )
        cached["refresh_error"] = None
        cached["signed_out"] = True

        def passive(local_reading):
            import hub
            with mock.patch.object(hub, "enabled", return_value=True), \
                    mock.patch("exporter._read_quota_cache",
                               return_value={"claude": cached, "codex": cached}), \
                    mock.patch("exporter.claude_status.load_rate_limits", return_value=local_reading), \
                    mock.patch("exporter.codex.load_rate_limits", return_value=None), \
                    mock.patch("exporter.accounts.claude_account", return_value=account), \
                    mock.patch("exporter.accounts.codex_account", return_value=None):
                return exporter._rate_limits(refresh=False)["claude"]

        # Signed back in but not used yet: the old verdict is all there is.
        self.assertIs(passive(None)["signed_out"], True)
        # Used once, so there is a newer reading than the cache: verdict gone.
        used = passive(RateLimits(3, None, 12, None, updated_at="2026-09-15T12:00:00+00:00"))
        self.assertIs(used["signed_out"], False)

    def test_a_restored_cache_never_publishes_an_error_beside_a_signed_out_flag(self):
        """Both producer halves of that invariant, which mutation showed untested.

        The consumer resolves the contradiction toward reporting the error, so a
        regression here is not silent — it is the *opposite* bug: a genuinely
        signed-out machine whose cached block still carries a stale error string
        goes back to being reported as a failure, which is the complaint this
        change exists to remove.
        """
        import hub
        account = accounts.Account("acct-1", "me@example.test", "pro")
        lapsed = exporter._rate_limit_block(
            RateLimits(8, None, 41, None, updated_at="2026-09-14T00:00:00+00:00"), account
        )
        lapsed["refresh_error"] = None
        lapsed["signed_out"] = True

        # (a) the hub is unreachable, so the cached block is restored and stamped.
        with mock.patch("exporter._read_quota_cache",
                        return_value={"claude": lapsed, "codex": lapsed}), \
                mock.patch("exporter.accounts.claude_account", return_value=account), \
                mock.patch("exporter.accounts.codex_account", return_value=account), \
                mock.patch.object(hub, "request_quota_refresh",
                                  side_effect=hub.QuotaRequestError("Hub is unreachable")):
            restored = exporter._gui_rate_limits()
        for provider in ("claude", "codex"):
            self.assertTrue(restored[provider]["refresh_error"])
            self.assertIs(restored[provider]["signed_out"], False)

        # (b) an active refresh fails; the cache keeps the newer reading but must
        # take this query's `signed_out` along with its error, not the old one.
        newer = dict(lapsed, updated_at="2026-09-16T00:00:00+00:00")
        written = {}
        with mock.patch.object(hub, "enabled", return_value=True), \
                mock.patch("exporter._read_quota_cache",
                           return_value={"claude": newer, "codex": newer}), \
                mock.patch("exporter.claude_status.load_rate_limits", return_value=None), \
                mock.patch("exporter.codex.load_rate_limits", return_value=None), \
                mock.patch("exporter.accounts.claude_account", return_value=account), \
                mock.patch("exporter.accounts.codex_account", return_value=account), \
                mock.patch("exporter.quota_refresh.load_rate_limits",
                           side_effect=quota_refresh.RefreshError("Quota request timed out")), \
                mock.patch("exporter.json.dump", side_effect=lambda obj, *a, **k: written.update(obj)), \
                mock.patch("exporter.os.fsync"), mock.patch("exporter.os.chmod"):
            exporter._rate_limits(refresh=True)
        for provider in ("claude", "codex"):
            self.assertEqual(written[provider]["refresh_error"], "Quota request timed out")
            self.assertIs(written[provider]["signed_out"], False)

    def test_no_account_is_a_state_rather_than_an_error(self):
        """Not signing a machine into a provider is a choice, not a fault.

        It used to raise the same RefreshError as a timeout, so the one
        distinction that matters downstream — is there anything to fix — was
        gone by the time the exporter saw it.
        """
        with self.assertRaises(quota_refresh.NotSignedIn):
            quota_refresh.load_rate_limits("claude", None)
        # Still a RefreshError, so callers that only care that no reading was
        # taken keep working unchanged.
        self.assertTrue(issubclass(quota_refresh.NotSignedIn, quota_refresh.RefreshError))

    def test_the_reader_child_reports_which_of_the_two_it_hit(self):
        """The class has to survive a process boundary, so it travels as data.

        Recovering it by matching the message text instead would make every
        future rewording a silent reclassification.
        """
        payload = json.dumps({"error": "not signed in", "signed_out": True})
        child = SimpleNamespace(
            pid=0, returncode=1, communicate=lambda _input, timeout=None: (payload, "")
        )
        with mock.patch("quota_refresh.subprocess.Popen", return_value=child):
            with self.assertRaises(quota_refresh.NotSignedIn):
                quota_refresh.load_rate_limits("claude", accounts.Account("a"))

        payload = json.dumps({"error": "timed out", "signed_out": False})
        with mock.patch("quota_refresh.subprocess.Popen", return_value=child):
            with self.assertRaises(quota_refresh.RefreshError) as caught:
                quota_refresh.load_rate_limits("claude", accounts.Account("a"))
        self.assertNotIsInstance(caught.exception, quota_refresh.NotSignedIn)

    def test_exporter_publishes_a_signed_out_block_with_no_error(self):
        with mock.patch("exporter.quota_refresh.load_rate_limits",
                        side_effect=quota_refresh.NotSignedIn("no account")), \
                mock.patch("exporter.claude_status.load_rate_limits", return_value=None), \
                mock.patch("exporter.codex.load_rate_limits", return_value=None), \
                mock.patch("exporter.accounts.claude_account", return_value=None), \
                mock.patch("exporter.accounts.codex_account", return_value=None):
            result = exporter._rate_limits(refresh=True)
        for provider in ("claude", "codex"):
            self.assertIsNone(result[provider]["refresh_error"])
            self.assertIs(result[provider]["signed_out"], True)

    def test_without_an_active_query_a_missing_credential_still_publishes_a_block(self):
        """The bug this closes: no block at all, read as "your exporter is old".

        A machine that has never signed in has no reading and no account, so
        this path used to publish nothing for the provider — and an absent block
        cannot say which of the two it is. The server's only reading of it was
        that the exporter was too old to confirm quota refresh, which named a
        remedy the reader did not need.
        """
        with mock.patch("exporter.claude_status.load_rate_limits", return_value=None), \
                mock.patch("exporter.codex.load_rate_limits", return_value=None), \
                mock.patch("exporter.accounts.claude_account", return_value=None), \
                mock.patch("exporter.accounts.codex_account", return_value=None):
            result = exporter._rate_limits(refresh=False)
        for provider in ("claude", "codex"):
            self.assertIsInstance(result[provider], dict)
            self.assertIs(result[provider]["signed_out"], True)
            # Absent, not null. This export ran no active query, and null is the
            # value that claims one ran and succeeded.
            self.assertNotIn("refresh_error", result[provider])

    def test_a_signed_in_machine_with_no_reading_yet_is_left_exactly_as_it_was(self):
        """The new block is for the signed-out case only.

        A machine that is signed in but has not read a quota yet publishes None,
        the same as before — it has no fact the server lacks, and other readers
        rely on that shape. Narrowing it this way is what keeps the change to
        the one case that was being misreported.
        """
        with mock.patch("exporter.claude_status.load_rate_limits", return_value=None), \
                mock.patch("exporter.codex.load_rate_limits", return_value=None), \
                mock.patch("exporter.accounts.claude_account", return_value=accounts.Account("a")), \
                mock.patch("exporter.accounts.codex_account", return_value=accounts.Account("b")):
            result = exporter._rate_limits(refresh=False)
        for provider in ("claude", "codex"):
            self.assertIsNone(result[provider])

    def test_no_cached_reading_still_reports_refresh_failure(self):
        with mock.patch("exporter.quota_refresh.load_rate_limits", side_effect=quota_refresh.RefreshError("failed")), \
                mock.patch("exporter.claude_status.load_rate_limits", return_value=None), \
                mock.patch("exporter.codex.load_rate_limits", return_value=None):
            result = exporter._rate_limits(refresh=True)
        self.assertEqual(result["claude"]["updated_at"], "")
        self.assertEqual(result["codex"]["refresh_error"], "failed")

    def test_failure_without_timestamp_survives_server_projection(self):
        admission = SimpleNamespace(admitted=[SimpleNamespace(host="node-d", meta={
            "rate_limits": {"claude": {"updated_at": "", "refresh_error": "failed"}}})])
        with mock.patch("server._self_machine_name", return_value="node-b"), \
                mock.patch("server._quota_unavailable_reason", return_value="No reading"):
            result = server._live_rate_limits_from_admission(admission)
        self.assertEqual(result["claude"]["accounts"], [])
        self.assertEqual(result["claude"]["refresh_errors"], [{"machine": "node-d", "reason": "failed"}])
        self.assertIn("无法确认", result["codex"]["refresh_errors"][0]["reason"])

    def test_a_just_switched_account_is_not_reported_as_an_old_exporter(self):
        """The bug this closes: switching accounts blamed the exporter.

        A passive export omits `refresh_error` and carries the key over from its
        quota cache only while the cache names the account now signed in. The
        first passive round after a switch therefore publishes a block without
        it — a current exporter behaving correctly, which the server read as one
        too old to confirm a refresh and told the reader to update. The other
        provider, whose account did not change, kept the key in the same export,
        which is what made the machine's own two rows disagree.
        """
        signed_in = accounts.Account("account-new", "new@example.test", "max")
        reading = RateLimits(24, None, 34, None, updated_at="2026-09-19T03:03:47Z")
        # Same machine, one provider switched and one not, as the hub saw it.
        stale_cache = {"claude": {**exporter._rate_limit_block(
            RateLimits(22, None, 18, None, updated_at="2026-09-19T02:40:43Z"),
            accounts.Account("account-old", "old@example.test", "max")),
            "refresh_error": None, "signed_out": False}}
        with mock.patch("hub.enabled", return_value=True), \
                mock.patch("exporter._read_quota_cache", return_value=stale_cache), \
                mock.patch("exporter.claude_status.load_rate_limits", return_value=reading), \
                mock.patch("exporter.codex.load_rate_limits", return_value=None), \
                mock.patch("exporter.accounts.claude_account", return_value=signed_in), \
                mock.patch("exporter.accounts.codex_account", return_value=None):
            blocks = exporter._rate_limits(refresh=False)
        self.assertNotIn("refresh_error", blocks["claude"])

        admission = SimpleNamespace(admitted=[
            SimpleNamespace(host="node-c", meta={"rate_limits": blocks})])
        with mock.patch("server._self_machine_name", return_value="node-b"), \
                mock.patch("server._quota_unavailable_reason", return_value="No reading"):
            result = server._live_rate_limits_from_admission(admission)
        self.assertEqual(result["claude"]["refresh_errors"], [])
        # The reading it did publish is still the one on the row, with its own
        # age left to say how current it is.
        entry = result["claude"]["accounts"][0]
        self.assertEqual(entry["seven_day_used_pct"], 34)
        self.assertEqual(entry["account_id"], "account-new")

    def test_an_exporter_older_than_both_keys_is_still_named(self):
        """The discriminator must not silence the case it was built to keep.

        An exporter predating `signed_out` predates `refresh_error` too, so a
        block carrying neither is the one that genuinely cannot confirm that an
        active refresh ran.
        """
        block = exporter._rate_limit_block(
            RateLimits(8, None, 55, None, updated_at="2026-09-08T12:00:00Z"),
            accounts.Account("account-a"))
        block.pop("refresh_error", None)
        block.pop("signed_out", None)
        admission = SimpleNamespace(admitted=[
            SimpleNamespace(host="node-a", meta={"rate_limits": {"claude": block}})])
        with mock.patch("server._self_machine_name", return_value="node-b"), \
                mock.patch("server._quota_unavailable_reason", return_value="No reading"):
            result = server._live_rate_limits_from_admission(admission)
        self.assertEqual(result["claude"]["refresh_errors"][0]["machine"], "node-a")
        self.assertIn("无法确认", result["claude"]["refresh_errors"][0]["reason"])

    def test_success_then_failure_keeps_latest_reading_and_current_presence(self):
        account = accounts.Account("account-a", "a@example.test", "pro")
        newest = exporter._rate_limit_block(
            RateLimits(8, None, 55, None, updated_at="2026-09-08T12:00:00Z"), account)
        newest["refresh_error"] = None
        latest_entry = server._account_entry(newest, ["node-d"], "node-d")
        key, record = server._account_memory_entry("claude", latest_entry)
        memory = {"version": 1, "accounts": {key: record}}
        old = RateLimits(2, None, 20, None, updated_at="2026-09-08T08:00:00Z")
        for cached in (old, None):
            with self.subTest(cache_present=cached is not None), \
                    mock.patch("exporter.quota_refresh.load_rate_limits", side_effect=quota_refresh.RefreshError("offline")), \
                    mock.patch("exporter.claude_status.load_rate_limits", return_value=cached), \
                    mock.patch("exporter.codex.load_rate_limits", return_value=None), \
                    mock.patch("exporter.accounts.claude_account", return_value=account), \
                    mock.patch("exporter.accounts.codex_account", return_value=None), \
                    mock.patch("server._load_account_memory", return_value=("valid", memory)), \
                    mock.patch("server._self_machine_name", return_value="node-d"), \
                    mock.patch("server._quota_unavailable_reason", return_value="No reading"):
                blocks = exporter._rate_limits(refresh=True)
                admission = SimpleNamespace(admitted=[SimpleNamespace(host="node-d", meta={"rate_limits": blocks})])
                result = server._live_rate_limits_from_admission(admission)
                server._merge_remembered_rate_limit_accounts(result)
                entry = result["claude"]["accounts"][0]
                self.assertEqual(entry["seven_day_used_pct"], 55)
                self.assertEqual(entry["updated_at"], record["observed_at"])
                self.assertEqual(entry["presence"], "in_use")
                self.assertEqual(entry["machines"], ["node-d"])
                self.assertEqual(entry["account_plan"], "pro")
                self.assertEqual(result["claude"]["refresh_errors"][0]["reason"], "offline")

    def test_refresh_status_is_normalized_at_consumer_boundary(self):
        for value in (None, "offline", "", " ", False, {}, json.loads("1e400")):
            with self.subTest(value=value):
                block = exporter._rate_limit_block(
                    RateLimits(8, None, 55, None, updated_at="2026-09-08T12:00:00Z"),
                    accounts.Account("account-a"))
                block["refresh_error"] = value
                admission = SimpleNamespace(admitted=[SimpleNamespace(host="node-d", meta={
                    "rate_limits": {"claude": block}})])
                with mock.patch("server._self_machine_name", return_value="node-d"), \
                        mock.patch("server._quota_unavailable_reason", return_value="No reading"):
                    result = server._live_rate_limits_from_admission(admission)
                json.dumps(result, allow_nan=False)
                self.assertEqual(result["claude"]["accounts"][0]["seven_day_used_pct"], 55)
                errors = result["claude"]["refresh_errors"]
                if value is None:
                    self.assertEqual(errors, [])
                else:
                    self.assertEqual(len(errors), 1)
                    self.assertEqual(errors[0]["reason"], "offline") if value == "offline" else self.assertIn("无效", errors[0]["reason"])

    def test_memory_replacement_does_not_borrow_a_healthy_machine_source(self):
        account = accounts.Account("a", "a@example.test")
        old = exporter._rate_limit_block(RateLimits(2, None, 20, None, updated_at="2026-09-29T01:00:00Z"), account)
        old["refresh_error"] = None
        failed = {**old, "refresh_error": "offline"}
        latest = {**old, "updated_at": "2026-09-29T07:00:00Z", "seven_day_pct": 80}
        key, record = server._account_memory_entry("claude", server._account_entry(latest, ["node-b"], "node-b"))
        for remembered in (False, True):
            admission = SimpleNamespace(admitted=[
                SimpleNamespace(host="node-d", meta={"rate_limits": {"claude": old}}),
                SimpleNamespace(host="node-b", meta={"rate_limits": {"claude": failed}}),
            ])
            with self.subTest(remembered=remembered), \
                    mock.patch("server._load_account_memory", return_value=("valid", {"version": 1, "accounts": {key: record} if remembered else {}})), \
                    mock.patch("server._self_machine_name", return_value="node-d"), \
                    mock.patch("server._quota_unavailable_reason", return_value="No reading"):
                result = server._live_rate_limits_from_admission(admission)
                server._merge_remembered_rate_limit_accounts(result)
                entry = result["claude"]["accounts"][0]
                self.assertEqual(entry["reading_from"], None if remembered else "node-d")
                self.assertEqual(entry["seven_day_used_pct"], 80 if remembered else 20)
                self.assertEqual(entry["updated_at"], record["observed_at"] if remembered else old["updated_at"])


class RefreshRoundTests(unittest.TestCase):
    def status(self, started="new", completed="done", syncing=False, success=True):
        return {"instance_id": "server-a", "started_at": started, "completed_at": completed,
                "syncing": syncing, "terminal": not syncing,
                "machines": [{"name": "node-d", "admitted": success,
                              "last_attempt_outcome": "success" if success else "failure",
                              "last_attempt_ts": completed}]}

    def run_round(self, before=None, initial=None, polled=None, final=None, errors=None, timeout=2, clock=None):
        initial = initial or self.status()
        final = final or initial
        calls = []

        def get(path, method="GET"):
            calls.append(path)
            if path == "/api/sync-status":
                return (before or self.status(started="old")) if len(calls) == 1 else (polled or initial)
            if path == "/api/refresh":
                self.assertEqual(method, "POST")
                return initial
            return {"sync": final,
                    "rate_limits": {p: {"refresh_errors": errors or []} for p in ("claude", "codex")}}

        kwargs = {"clock": clock} if clock else {}
        return refresh.refresh(get, {"node-d"}, timeout=timeout, sleep=lambda _: None, **kwargs)

    def test_success_is_the_requested_round(self):
        self.assertEqual(self.run_round(), [])

    def test_join_running_round(self):
        busy = self.status(completed=None, syncing=True)
        self.assertEqual(self.run_round(before=busy, initial=busy, polled=self.status(), final=self.status()), [])

    def test_terminal_failed_machine_is_partial(self):
        self.assertIn("data refresh failed", self.run_round(initial=self.status(success=False))[0])

    def test_provider_failure_is_partial(self):
        errors = [{"machine": "node-d", "reason": "login expired"}]
        self.assertEqual(len(self.run_round(errors=errors)), 2)

    def test_old_completed_round_is_not_success(self):
        with self.assertRaises(refresh.RefreshError):
            self.run_round(initial=self.status(started="old"))

    def test_restarted_server_and_superseding_round_are_not_success(self):
        for field, value in (("instance_id", "server-b"), ("started_at", "next")):
            final = self.status()
            final[field] = value
            with self.subTest(field=field), self.assertRaises(refresh.RefreshError):
                self.run_round(final=final)

    def test_wait_is_bounded(self):
        busy = self.status(completed=None, syncing=True)
        ticks = iter([0, 3])
        with self.assertRaisesRegex(refresh.RefreshError, "wait limit"):
            self.run_round(initial=busy, clock=lambda: next(ticks))


if __name__ == "__main__":
    unittest.main()
