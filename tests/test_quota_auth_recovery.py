"""Explicit auth recovery with synthetic credentials and an in-memory RPC peer."""
import base64
import json
import os
from pathlib import Path
import tempfile
import unittest
import subprocess
import sys
from unittest import mock

from agent_monitor.quota import codex_quota as quota, quota_recovery
from agent_monitor.quota.quota_refresh import RefreshError


class AuthRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.auth = Path(self.temp.name) / "auth.json"
        self.write_auth("before")
        self.calls = []
        self.recovery = {}
        self.changed = True
        self.wrong_identity = False
        self.refresh_error = False
        self.quota_error = False

    def write_auth(self, access, email="one@example.test", refresh=True):
        claim = base64.urlsafe_b64encode(json.dumps({"email": email}).encode()).decode().rstrip("=")
        tokens = {"account_id": "workspace", "id_token": "fixture." + claim + ".fixture", "access_token": access}
        if refresh:
            tokens["refresh_token"] = "synthetic-refresh"
        self.auth.write_text(json.dumps({"auth_mode": "chatgpt", "tokens": tokens}))

    def request(self, method, params):
        self.calls.append((method, params))
        if method == "account/read":
            if self.changed:
                self.write_auth("after", "other@example.test" if self.wrong_identity else "one@example.test")
            if self.refresh_error:
                raise RefreshError("routing failed", failure={"stage": "account_check", "kind": "rpc_error", "rpc_code": -32603})
            return {"account": {"type": "chatgpt", "email": "one@example.test"}}
        if self.quota_error:
            raise RefreshError("still refused", failure={"stage": "quota_read", "kind": "rpc_error", "http_status": 401, "upstream_code": "token_revoked"})
        return {"accountId": "workspace", "rateLimits": {"primary": {"windowDurationMins": 300, "usedPercent": 17}}}

    def read(self):
        return quota.read_bound(self.request, self.auth, "workspace", "one@example.test",
                                renew_auth=True, recovery=self.recovery)

    def test_two_separate_repairs_use_new_credentials_then_one_quota_each(self):
        for _ in range(2):
            self.write_auth("before")
            self.assertEqual(self.read()["five_hour_used_pct"], 17)
        self.assertEqual([method for method, _ in self.calls], ["account/read", "account/rateLimits/read"] * 2)
        self.assertTrue(all(params == {"refreshToken": True} for method, params in self.calls if method == "account/read"))
        self.assertEqual(self.recovery["outcome"], "quota_confirmed")
        self.assertNotIn("synthetic-refresh", json.dumps(self.recovery))

    def test_returned_account_without_new_credentials_never_queries_quota(self):
        self.changed = False
        with self.assertRaises(quota.ReadingError):
            self.read()
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.recovery["outcome"], "no_new_credentials")

    def test_routing_rpc_failure_after_credential_change_preserves_progress(self):
        self.refresh_error = True
        self.assertEqual(self.read()["five_hour_used_pct"], 17)
        self.assertEqual(self.recovery["refresh_failure"]["rpc_code"], -32603)
        self.assertEqual(len(self.calls), 2)

    def test_changed_identity_and_missing_refresh_credentials_refuse_quota(self):
        self.wrong_identity = True
        with self.assertRaises(quota.ReadingError):
            self.read()
        self.assertEqual(len(self.calls), 1)
        self.calls.clear()
        self.write_auth("before", refresh=False)
        with self.assertRaises(quota.ReadingError):
            self.read()
        self.assertEqual(self.calls, [])

    def test_refused_quota_does_not_refresh_again(self):
        self.quota_error = True
        with self.assertRaises(RefreshError):
            self.read()
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(self.recovery["outcome"], "quota_failed")

    def test_normal_read_keeps_one_rpc(self):
        quota.read_bound(self.request, self.auth, "workspace", "one@example.test")
        self.assertEqual(self.calls, [("account/rateLimits/read", {})])

    def test_new_mode_is_distinct_from_old_approval_scope(self):
        old = {"machine": "fixture", "account_id": "workspace", "account_label": "one@example.test",
               "pause_id": "a" * 32, "profile": "monitor-fixture"}
        new = {**old, "mode": "renew_auth"}
        self.assertEqual(quota_recovery.validate_request(new), new)
        self.assertNotEqual(quota_recovery.approval_scope(old), quota_recovery.approval_scope(new))
        for mode in (True, "unknown", None):
            with self.assertRaises(ValueError):
                quota_recovery.validate_request({**old, "mode": mode})


class NativeAuthRecoveryTests(unittest.TestCase):
    setUp = AuthRecoveryTests.setUp
    write_auth = AuthRecoveryTests.write_auth

    def run_native(self, *, slow=False, noisy=False, fail_initialize=False, refusal=None, managed=True):
        from agent_monitor.quota import quota_refresh
        self.script = Path(self.temp.name) / "fixture-codex"
        self.script.write_text("#!" + sys.executable + "\n" + '''import json,os,sys,time
from pathlib import Path
home=Path(os.environ['CODEX_HOME'])
if os.environ.get('NOISY_AUTH_FIXTURE'):
 sys.stderr.write('fixture refresh detail: '+json.loads((home/'auth.json').read_text())['tokens']['access_token']+'\\n'+'x'*200000);sys.stderr.flush()
for line in sys.stdin:
 m=json.loads(line)
 with (home/'calls.jsonl').open('a') as f:f.write(json.dumps(m)+'\\n')
 if 'id' not in m:continue
 method=m.get('method')
 if method=='initialize' and os.environ.get('FAIL_AUTH_INITIALIZE'):
  print(json.dumps({'id':m['id'],'error':{'code':-32603,'message':'fixture initialize failed'}}),flush=True);continue
 if method=='account/read':
  auth=json.loads((home/'auth.json').read_text());auth['tokens']['access_token']='after';(home/'auth.json').write_text(json.dumps(auth))
  if os.environ.get('SLOW_AUTH_FIXTURE'):time.sleep(.3)
  result={'account':{'type':'chatgpt','email':'one@example.test'}}
 elif method=='account/rateLimits/read':
  if os.environ.get('AUTH_REFUSAL'):
   code=os.environ['AUTH_REFUSAL']
   body=json.dumps({'error':{'code':code}})
   prefix='body=' if code=='body_only' else 'failed to fetch codex rate limits: GET https://chatgpt.com/backend-api/wham/usage failed: 401 Unauthorized; content-type=application/json; body='
   print(json.dumps({'id':m['id'],'error':{'code':-32603,'message':prefix+body}}),flush=True);continue
  if os.environ.get('SLOW_AUTH_FIXTURE'):time.sleep(.3)
  result={'accountId':'workspace','rateLimits':{'primary':{'windowDurationMins':300,'usedPercent':17}}}
 else:result={}
 print(json.dumps({'id':m['id'],'result':result}),flush=True)
''')
        self.script.chmod(0o700)
        real_popen = subprocess.Popen
        self.processes = []
        def spawn(command, **kwargs):
            self.command = command
            self.child_env = kwargs['env'].copy()
            if slow:
                kwargs['env']['SLOW_AUTH_FIXTURE'] = '1'
            if noisy:
                kwargs['env']['NOISY_AUTH_FIXTURE'] = '1'
            if refusal:
                kwargs['env']['AUTH_REFUSAL'] = refusal
            if fail_initialize:
                kwargs['env']['FAIL_AUTH_INITIALIZE'] = '1'
            process = real_popen(command, **kwargs)
            self.processes.append(process)
            return process
        with mock.patch.object(quota_refresh.Path, "home", return_value=Path(self.temp.name)), \
                mock.patch.object(quota_refresh.shutil, "which", return_value=str(self.script)), \
                mock.patch.object(quota_refresh.subprocess, "Popen", side_effect=spawn), \
                mock.patch.object(quota_refresh.network_policy, "endpoint", return_value="http://agent-monitor:fixture@127.0.0.1:9"), \
                mock.patch.object(quota_refresh, "CODEX_RPC_SECONDS", .5 if slow else 28):
            return quota_refresh._codex({"account_id": "workspace", "label": "one@example.test",
                "codex_home": str(self.auth.parent), "renew_auth": True,
                "managed_auth": managed, "source_digest": quota.recovery_source_digest(self.auth),
                "auth_failure": {"kind": "rpc_error", "stage": "quota_read", "http_status": 401, "upstream_code": "token_revoked"}}, recovery=self.recovery)

    def test_native_refusal_proof_requires_exact_transport_and_bound_credential(self):
        for code in ("token_expired", "token_revoked", "body_only", "unknown"):
            with self.subTest(code=code):
                self.write_auth("before")
                with self.assertRaises(RefreshError) as raised:
                    self.run_native(refusal=code)
                proof = getattr(raised.exception, "account_quota_rejection", None)
                if code in ("token_expired", "token_revoked"):
                    self.assertEqual(proof, quota.credential_digest(self.auth))
                    self.assertEqual(raised.exception.failure["http_status"], 401)
                    self.assertEqual(raised.exception.failure["upstream_code"], code)
                else:
                    self.assertIsNone(proof)

    def test_native_transport_uses_unique_ids_and_file_credentials(self):
        self.assertEqual(self.run_native().five_hour_pct, 17)
        calls = [json.loads(line) for line in (self.auth.parent / "calls.jsonl").read_text().splitlines()]
        requests = [call for call in calls if "id" in call]
        self.assertEqual([call['method'] for call in requests], ['initialize', 'account/read', 'account/rateLimits/read'])
        self.assertEqual(len({call['id'] for call in requests}), 3)
        self.assertIn('cli_auth_credentials_store="file"', self.command)
        self.assertIn('forced_login_method="chatgpt"', self.command)
        self.assertNotIn('client_diagnostics', self.recovery)
        self.assertEqual(requests[-1]['params'], {'excludeResetCreditDetails': True})
        self.assertEqual(self.recovery['initial_failure']['http_status'], 401)
        self.assertTrue(all(process.poll() is not None for process in self.processes))

    def test_native_managed_recovery_excludes_ambient_auth_and_keeps_route(self):
        names = ('CODEX_ACCESS_TOKEN', 'CODEX_API_KEY', 'OPENAI_API_KEY',
                 'OPENAI_BASE_URL', 'CODEX_HOME')
        for present in (False, True):
            with self.subTest(ambient_auth=present), mock.patch.dict(os.environ):
                for name in names:
                    os.environ.pop(name, None)
                if present:
                    os.environ.update({name: 'synthetic-alternate' for name in names})
                self.write_auth('before')
                self.assertEqual(self.run_native().five_hour_pct, 17)
                for name in names[:-1]:
                    self.assertNotIn(name, self.child_env)
                self.assertEqual(self.child_env['CODEX_HOME'], str(self.auth.parent))
                self.assertEqual(self.child_env['HOME'], str(self.auth.parent.parent / 'home'))
                self.assertEqual(self.child_env['AGENT_MONITOR_PROXY_INTERNAL'],
                                 'http://agent-monitor:fixture@127.0.0.1:9')

    def test_existing_file_recovery_keeps_owning_home_and_excludes_oauth_overrides(self):
        names = ('CODEX_ACCESS_TOKEN', 'CODEX_API_KEY', 'OPENAI_API_KEY',
                 'CODEX_REFRESH_TOKEN_URL_OVERRIDE', 'CODEX_APP_SERVER_LOGIN_CLIENT_ID')
        for present in (False, True):
            with self.subTest(ambient_auth=present), mock.patch.dict(os.environ):
                for name in names:
                    os.environ.pop(name, None)
                if present:
                    os.environ.update({name: 'synthetic-alternate' for name in names})
                self.write_auth('before')
                self.assertEqual(self.run_native(managed=False).five_hour_pct, 17)
                self.assertEqual(self.child_env['HOME'], self.temp.name)
                self.assertEqual(self.child_env['CODEX_HOME'], str(self.auth.parent))
                self.assertNotIn('forced_login_method="chatgpt"', self.command)
                self.assertTrue(all(name not in self.child_env for name in names))
                self.assertEqual(self.child_env['AGENT_MONITOR_PROXY_INTERNAL'],
                                 'http://agent-monitor:fixture@127.0.0.1:9')

    def test_file_recovery_refuses_nonstandard_provenance_before_spawn(self):
        from agent_monitor.quota import quota_refresh
        cases = [None, {'issuer': 'https://auth.openai.com', 'client_id': 'app_EMoamEEZ73f0CkXaXp7hrann'},
                 {'issuer': 'https://other.test', 'client_id': 'app_EMoamEEZ73f0CkXaXp7hrann'},
                 {'issuer': 'https://auth.openai.com', 'client_id': 'other'}, {}, 'invalid']
        for index, provenance in enumerate(cases):
            with self.subTest(provenance=index):
                self.write_auth('before')
                auth = json.loads(self.auth.read_text())
                auth['tokens']['oauth_provenance'] = provenance
                self.auth.write_text(json.dumps(auth))
                if index < 2:
                    quota.file_auth_recovery_preflight(self.auth)
                else:
                    with mock.patch.object(quota_refresh.subprocess, 'Popen') as spawn, \
                            mock.patch.object(quota_refresh.network_policy, 'child_environment', return_value={}):
                        with self.assertRaises(quota_refresh.LocalUnavailable):
                            quota_refresh._codex({'account_id': 'workspace', 'label': 'one@example.test',
                                'codex_home': str(self.auth.parent), 'renew_auth': True,
                                'source_digest': quota.recovery_source_digest(self.auth)}, recovery={})
                    spawn.assert_not_called()


    def test_native_phases_share_deadline_and_child_is_reaped(self):
        with self.assertRaises(RefreshError) as caught:
            self.run_native(slow=True)
        self.assertEqual(caught.exception.failure['kind'], 'timeout')
        self.assertTrue(all(process.poll() is not None for process in self.processes))

    def test_bounded_client_diagnostics_do_not_block_or_expose_credentials(self):
        self.assertEqual(self.run_native(noisy=True).five_hour_pct, 17)
        excerpt = self.recovery['client_diagnostics']
        self.assertIn('fixture refresh detail:', excerpt)
        self.assertNotIn('before', excerpt)
        self.assertLessEqual(len(excerpt), 512)
        self.assertEqual(quota.safe_auth_recovery(self.recovery)['client_diagnostics'], excerpt)
        self.assertTrue(all(process.poll() is not None for process in self.processes))

    def test_client_excerpt_redacts_cross_chunk_and_truncation_boundaries(self):
        from agent_monitor.quota.quota_refresh import _safe_client_excerpt
        secret = 'synthetic-secret-' + 'z' * 100
        for size in (len(secret), 30):
            with self.subTest(retained_secret_bytes=size):
                self.assertNotIn('synthetic-secret', _safe_client_excerpt(
                    'refresh detail: ' + secret[:size], [secret], truncated=size < len(secret)))

    def test_initialize_failure_and_noisy_timeout_keep_first_cause_and_cleanup(self):
        for initialize in (True, False):
            with self.subTest(initialize_failure=initialize), self.assertRaises(RefreshError):
                self.run_native(noisy=True, fail_initialize=initialize, slow=not initialize)
            self.assertEqual(self.recovery['initial_failure']['http_status'], 401)
            self.assertIn('fixture refresh detail:', self.recovery['client_diagnostics'])
            self.assertNotIn('before', self.recovery['client_diagnostics'])
            self.assertTrue(all(process.poll() is not None for process in self.processes))


class RecoveryApprovalTests(unittest.TestCase):
    def test_existing_source_digest_survives_http_adapter_and_one_time_approval(self):
        import io
        from email.message import Message
        from types import SimpleNamespace
        from agent_monitor import http_server as server
        from agent_monitor.network.client_network import ApprovalStore
        machine = SimpleNamespace(name='fixture-owner')
        for profile in ('', 'existing'):
            with self.subTest(profile=profile):
                request = dict(machine=machine.name, account_id='workspace', account_label='one@example.test',
                    profile=profile, pause_id='a' * 32, mode='renew_auth', source_digest='b' * 64)
                approvals = ApprovalStore()
                manager = mock.MagicMock()
                manager.recovery_scope.return_value.__enter__.return_value = True
                manager.recover.return_value = ({'ok': True}, 200)
                def post(payload):
                    raw = json.dumps(payload).encode()
                    handler = object.__new__(server.Handler)
                    handler.headers = Message()
                    handler.headers['Content-Length'] = str(len(raw))
                    handler.headers['Content-Type'] = 'application/json'
                    handler.rfile = io.BytesIO(raw)
                    handler._send_json = mock.Mock()
                    handler._handle_quota_recovery()
                    return handler._send_json.call_args.kwargs['status']
                scope = quota_recovery.approval_scope(request)
                with mock.patch.object(server, 'UPSTREAM_APPROVALS', approvals), \
                        mock.patch.object(server, 'QUOTA_REFRESH', manager), \
                        mock.patch.object(server.sync, 'load_machine_config', return_value=SimpleNamespace(by_name={machine.name: machine})):
                    valid = approvals.issue('quota-auth-recovery', scope, request['pause_id'])
                    missing = dict(request)
                    del missing['source_digest']
                    self.assertEqual(post({**missing, 'confirmation': valid}), 400)
                    self.assertEqual(post({**request, 'source_digest': 'c' * 64, 'confirmation': valid}), 409)
                    old = approvals.issue('quota-recovery', scope[:-2], request['pause_id'])
                    self.assertEqual(post({**request, 'confirmation': old}), 409)
                    manager.recover.assert_not_called()
                    valid = approvals.issue('quota-auth-recovery', scope, request['pause_id'])
                    self.assertEqual(post({**request, 'confirmation': valid}), 200)
                    manager.recover.assert_called_once_with(machine, request)
                    self.assertEqual(post({**request, 'confirmation': valid}), 409)

    def test_reauthorization_has_distinct_confirmation_and_original_task_scope(self):
        import http.client
        import threading
        from types import SimpleNamespace
        from agent_monitor import http_server as server
        from agent_monitor.accounts import codex_accounts as ca
        from agent_monitor.network.client_network import ApprovalStore, Route
        machine=SimpleNamespace(name='fixture-owner',is_self=True)
        request={'machine':'fixture-owner','account_id':'workspace','account_label':'One@Example.test',
            'profile':'monitor-11111111-1111-4111-8111-111111111111','pause_id':'f'*32,
            'mode':'reauthorize','operation_id':'22222222-2222-4222-8222-222222222222'}
        binding={k:request[k] for k in ('pause_id','operation_id','account_id','account_label')}
        binding['account_label']=binding['account_label'].casefold()
        quota_manager=mock.MagicMock()
        quota_manager.recovery_scope.return_value.__enter__.return_value=True
        quota_manager.recovery_status.return_value={'source_host_identity':'fixture-host','account':{'login_recovery':binding,'quota_profile':request['profile']}}
        manager=mock.Mock();manager.start_reauthorization.return_value={'operation':{'stage':'queued'}}
        approvals=ApprovalStore();scope=quota_recovery.approval_scope(request)
        context={"operation_id":request["operation_id"],"device":None,"quota_batch_id":None,"preview":None}
        route=Route("fixture device")
        self.addCleanup(route.close)
        connection={"id":route.id,"capability":route.browser}
        expected={"pause_id":request["pause_id"],"network_context":context}
        with mock.patch.object(server,'UPSTREAM_APPROVALS',approvals), \
             mock.patch.object(server,'QUOTA_REFRESH',quota_manager), \
             mock.patch.object(ca,'manager',manager), \
             mock.patch.object(quota_recovery,'managed_network_context',return_value=context), \
             mock.patch.object(server.client_network.hub,'browser_route',return_value=route), \
             mock.patch.object(server.hub,'enabled',return_value=False), \
             mock.patch.object(server.hub,'is_hub',return_value=True), \
             mock.patch.object(server.sync,'load_machine_config',return_value=SimpleNamespace(by_name={'fixture-owner':machine})):
            httpd=server.ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
            thread=threading.Thread(target=httpd.serve_forever);thread.start()
            def post(token):
                conn=http.client.HTTPConnection('127.0.0.1',httpd.server_port,timeout=3)
                try:
                    conn.request('POST','/api/quota-recovery',json.dumps({**request,'confirmation':token,'network_context':context,'connection':connection}),
                        {'Content-Type':'application/json','X-Agent-Monitor':'action','Origin':f'http://127.0.0.1:{httpd.server_port}'})
                    response=conn.getresponse();return response.status,json.loads(response.read())
                finally:conn.close()
            try:
                old=approvals.issue('quota-auth-recovery',scope,request['pause_id'])
                self.assertEqual(post(old)[0],409)
                wrong=route.issue([*scope[:-1],'wrong-operation'],expected,'quota-login-recovery')
                self.assertEqual(post(wrong)[0],409)
                manager.start_reauthorization.assert_not_called()
                valid=route.issue(scope,expected,'quota-login-recovery')
                status,receipt=post(valid)
                self.assertEqual(status,202);self.assertTrue(receipt['login_pending'])
                self.assertEqual(post(valid)[0],409)
                manager.start_reauthorization.assert_called_once_with('11111111-1111-4111-8111-111111111111',binding,publication=mock.ANY,network_route=mock.ANY)
                quota_manager.recover.assert_not_called()
            finally:
                httpd.shutdown();httpd.server_close();thread.join(3)

    def test_old_action_wrong_scope_and_replay_cannot_approve_auth_repair(self):
        import http.client
        import threading
        from types import SimpleNamespace
        from agent_monitor import http_server as server
        from agent_monitor.network.client_network import ApprovalStore
        machine = SimpleNamespace(name="fixture-owner")
        manager = mock.MagicMock()
        manager.recovery_scope.return_value.__enter__.return_value = True
        manager.recover.return_value = ({"ok": True}, 200)
        approvals = ApprovalStore()
        request = {"machine": "fixture-owner", "account_id": "workspace", "account_label": "one@example.test",
                   "profile": "existing", "pause_id": "a" * 32, "mode": "renew_auth", "source_digest": "b" * 64}
        scope = quota_recovery.approval_scope(request)
        with mock.patch.object(server, "UPSTREAM_APPROVALS", approvals), \
                mock.patch.object(server, "QUOTA_REFRESH", manager), \
                mock.patch.object(server.hub, "enabled", return_value=False), \
                mock.patch.object(server.sync, "load_machine_config", return_value=SimpleNamespace(by_name={"fixture-owner": machine})):
            httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
            thread = threading.Thread(target=httpd.serve_forever)
            thread.start()
            def post(token):
                conn = http.client.HTTPConnection("127.0.0.1", httpd.server_port, timeout=3)
                try:
                    conn.request("POST", "/api/quota-recovery", json.dumps({**request, "confirmation": token}),
                                 {"Content-Type": "application/json", "X-Agent-Monitor": "action", "Origin": f"http://127.0.0.1:{httpd.server_port}"})
                    response = conn.getresponse()
                    response.read()
                    return response.status
                finally:
                    conn.close()
            try:
                old = approvals.issue("quota-recovery", scope[:-1], request["pause_id"])
                wrong = approvals.issue("quota-auth-recovery", [*scope[:-1], "wrong-mode"], request["pause_id"])
                self.assertEqual(post(old), 409)
                self.assertEqual(post(wrong), 409)
                manager.recover.assert_not_called()
                valid = approvals.issue("quota-auth-recovery", scope, request["pause_id"])
                self.assertEqual(post(valid), 200)
                self.assertEqual(post(valid), 409)
                manager.recover.assert_called_once_with(machine, request)
            finally:
                httpd.shutdown()
                httpd.server_close()
                thread.join(3)


class PersistedRecoveryTests(unittest.TestCase):
    def setUp(self):
        from parsers.codex_profiles import SessionLinks
        for patch in (mock.patch('parsers.codex_profiles.discover', return_value=[]),
                      mock.patch('parsers.codex_profiles.session_links', return_value=SessionLinks())):
            patch.start()
            self.addCleanup(patch.stop)

    def test_bound_child_receipt_updates_profile_cache_and_retains_original_failure(self):
        from datetime import datetime, timezone
        from agent_monitor.accounts.codex_accounts import Manager
        from agent_monitor.accounts.upstream_controls import Controls
        from agent_monitor.quota import quota_service, quota_refresh
        from parsers.accounts import Account
        from agent_monitor.contracts.usage import RateLimits
        from dataclasses import asdict
        from tests.test_codex_accounts import seed_auth
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manager = Manager(root / "state/codex-accounts")
            self.addCleanup(manager.close)
            account = manager.add("one@example.test", "workspace")
            seed_auth(manager, account)
            directory = manager.directory(account['id'])
            record = manager.read(directory)
            record['operation'] = {"id": "failed-quota", "stage": "failed", "refresh_only": True,
                "message_status": "not_sent", "failure": {"category": "unknown", "stage": "reading"}, "detail": "original failure"}
            manager.save(directory, record)
            profile = "monitor-" + account['id']
            failure = {"kind": "rpc_error", "stage": "quota_read", "http_status": 401, "upstream_code": "token_revoked",
                "account_profile": profile, "account_fingerprint": quota_refresh.account_fingerprint("codex", Account("workspace", "one@example.test"))}
            controls = Controls(root / "state/upstream_controls")
            lease = controls.acquire('codex')
            lease.pause('quotaRefreshFailed', failure=failure)
            lease.close()
            pause_id = controls.read('codex')['pause']['id']
            reading = RateLimits(17, 1900000000, None, None, updated_at=datetime.now(timezone.utc).isoformat())
            auth_result = {"outcome": "quota_confirmed", "initial_failure": failure}
            child = mock.Mock(returncode=0)
            child.communicate.return_value = (json.dumps({"reading": asdict(reading), "authentication_recovery": auth_result}), "")
            carrier = {}
            with mock.patch.object(quota_refresh.subprocess, 'Popen', return_value=child), \
                    mock.patch.object(quota_refresh.network_policy, 'child_environment', return_value={}):
                block = quota_service.recover_codex_quota("one@example.test", pause_id, expected_id="workspace",
                    root=root, admitted_probe=carrier, profile=profile, renew_auth=True)
            target = json.loads(child.communicate.call_args.args[0])
            self.assertTrue(target['renew_auth'])
            self.assertEqual(target['codex_home'], str(directory / 'codex'))
            self.assertEqual(carrier['authentication_recovery'], auth_result)
            self.assertEqual(block['five_hour_pct'], 17)
            self.assertIsNone(controls.read('codex').get('pause'))
            saved = Manager(root / 'state/codex-accounts').view(directory)
            self.assertEqual(saved['operation']['stage'], 'succeeded')
            self.assertEqual(saved['operation']['after']['five_hour_used_pct'], 17)
            self.assertEqual(saved['last_failure']['detail'], 'original failure')
            self.assertTrue((root / 'state/quota_snapshot.json').is_file())

    def test_unknown_failure_does_not_admit_auth_repair(self):
        from agent_monitor.quota.codex_quota import authentication_quota_failure
        known = {"kind": "rpc_error", "stage": "quota_read", "http_status": 401, "upstream_code": "token_revoked"}
        self.assertTrue(authentication_quota_failure(known))
        self.assertTrue(authentication_quota_failure({**known, "upstream_code": "token_expired"}))
        for changed in ({"stage": "account_check"}, {"http_status": 403}, {"http_status": None}, {"upstream_code": "unknown"}, {"kind": "timeout"}):
            self.assertFalse(authentication_quota_failure({**known, **changed}))


class ExistingSourceRecoveryTests(unittest.TestCase):
    # Includes a positive guard rejecting every real provider discovery root.
    from tests.test_quota_refresh import ProviderTests as _Isolation
    setUp = _Isolation.setUp
    write_auth = AuthRecoveryTests.write_auth

    def source(self, kind):
        from contextlib import ExitStack
        from types import SimpleNamespace
        from agent_monitor.accounts.upstream_controls import Controls
        from agent_monitor.quota import quota_refresh
        from parsers import accounts, codex_profiles
        root = self.root / kind
        home = root / ('.codex' if kind == 'native' else 'custom/codex')
        home.mkdir(parents=True)
        self.auth = home / 'auth.json'
        self.write_auth('before')
        profile = 'existing' if kind == 'profile' else ''
        account = accounts.Account('workspace', 'one@example.test')
        controls = Controls(root / 'state/upstream_controls')
        lease = controls.acquire('codex')
        failure = dict(stage='quota_read', kind='rpc_error', http_status=401, upstream_code='token_expired',
                       account_fingerprint=quota_refresh.account_fingerprint('codex', account))
        if profile:
            failure['account_profile'] = profile
        lease.pause('quotaRefreshFailed', failure=failure)
        lease.close()
        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(mock.patch.object(accounts, 'CODEX_AUTH', self.auth if not profile else root / 'absent/auth.json'))
        stack.enter_context(mock.patch.object(codex_profiles, 'discover', return_value=[SimpleNamespace(home=home,
            account=dict(name=profile, account_id=account.account_id, email=account.label))] if profile else []))
        stack.enter_context(mock.patch('agent_monitor.snapshots.exporter.ROOT', root))
        stack.enter_context(mock.patch('agent_monitor.snapshots.generation.self_certified_host_identity', return_value='fixture-host'))
        request = dict(machine='fixture', account_id=account.account_id, account_label=account.label,
            profile=profile, pause_id=controls.read('codex')['pause']['id'], mode='renew_auth',
            source_digest=quota.recovery_source_digest(self.auth))
        return root, controls, request

    def test_existing_sources_recover_into_normal_cache_without_monitor_enrollment(self):
        from datetime import datetime, timezone
        from dataclasses import asdict
        from agent_monitor.contracts.usage import RateLimits
        from agent_monitor.quota import quota_service, quota_refresh
        for kind in ('native', 'configured', 'profile'):
            with self.subTest(source=kind):
                root, controls, request = self.source(kind)
                status = quota_recovery.local_status()
                self.assertTrue(status['account']['auth_recovery_available'])
                self.assertEqual(status['account']['source_digest'], request['source_digest'])
                quota_recovery.validate_request(request)
                reading = RateLimits(17, 4102444800, None, None, updated_at=datetime.now(timezone.utc).isoformat())
                child = mock.Mock(returncode=0)
                child.communicate.return_value = (json.dumps(dict(reading=asdict(reading),
                    authentication_recovery=dict(outcome='quota_confirmed'))), '')
                carrier = {}
                with mock.patch.object(quota_refresh.subprocess, 'Popen', return_value=child):
                    block = quota_service.recover_codex_quota(request['account_label'], request['pause_id'],
                        expected_id=request['account_id'], root=root, profile=request['profile'], renew_auth=True,
                        source_digest=request['source_digest'], admitted_probe=carrier)
                target = json.loads(child.communicate.call_args.args[0])
                self.assertFalse(target['managed_auth'])
                self.assertEqual(target['codex_home'], str(self.auth.parent))
                self.assertEqual(target['source_digest'], request['source_digest'])
                saved = json.loads((root / 'state/quota_snapshot.json').read_text())['codex']
                observed = saved['managed_accounts'][0] if kind == 'profile' else saved
                self.assertEqual(observed['five_hour_pct'], 17)
                self.assertEqual(observed['quota_observation']['source'], 'active_query')
                self.assertEqual(observed['account_id'], request['account_id'])
                self.assertIsNone(controls.read('codex').get('pause'))
                self.assertEqual((self.auth.parent.parent / '.lock').exists(), kind == 'profile')
                self.assertFalse((root / 'state/codex-accounts').exists())
                self.assertEqual(block['five_hour_pct'], 17)

    def test_changed_source_or_missing_binding_never_starts_provider(self):
        from agent_monitor.quota import quota_service, quota_refresh
        for kind in ('native', 'profile'):
            with self.subTest(source=kind):
                root, controls, request = self.source(kind)
                for digest in (None, '0' * 64):
                    before = controls.read('codex')
                    with mock.patch.object(quota_refresh.subprocess, 'Popen') as child:
                        with self.assertRaises(quota_refresh.LocalUnavailable):
                            quota_service.recover_codex_quota(request['account_label'], request['pause_id'],
                                expected_id=request['account_id'], root=root, profile=request['profile'], renew_auth=True,
                                source_digest=digest, admitted_probe={})
                    child.assert_not_called()
                    self.assertEqual(controls.read('codex'), before)
                missing = dict(request)
                del missing['source_digest']
                with self.assertRaises(ValueError):
                    quota_recovery.validate_request(missing)
                changed = {**request, 'source_digest': '0' * 64}
                self.assertNotEqual(quota_recovery.approval_scope(request), quota_recovery.approval_scope(changed))

    def test_save_failure_retains_pause_after_valid_reading(self):
        from datetime import datetime, timezone
        from agent_monitor.contracts.usage import RateLimits
        from agent_monitor.quota import quota_service, quota_refresh
        root, controls, request = self.source('native')
        reading = RateLimits(17, 4102444800, None, None, updated_at=datetime.now(timezone.utc).isoformat())
        with mock.patch.object(quota_refresh, 'load_rate_limits', return_value=reading), \
                mock.patch.object(quota_service, 'save_cache', side_effect=OSError('fixture disk failure')):
            with self.assertRaises(OSError):
                quota_service.recover_codex_quota(request['account_label'], request['pause_id'],
                    expected_id=request['account_id'], root=root, renew_auth=True,
                    source_digest=request['source_digest'], admitted_probe={})
        self.assertEqual(controls.read('codex')['pause']['id'], request['pause_id'])


class ReauthorizationTests(unittest.TestCase):
    from tests.test_codex_accounts import AccountActionsTests as _Cases
    def setUp(self):
        from contextlib import nullcontext
        self._Cases.setUp(self)
        from agent_monitor.accounts import codex_accounts as ca
        self.manager.close()
        self.manager = ca.Manager(self.root / "codex-accounts")
        self.addCleanup(self.manager.close)
        identity = mock.patch('agent_monitor.snapshots.generation.self_certified_host_identity', return_value='fixture-host')
        identity.start()
        self.addCleanup(identity.stop)
        self.publication = lambda block: nullcontext()
    account = _Cases.account
    wait = _Cases.wait
    calls = _Cases.calls

    def start(self, account, binding):
        return self.manager.start_reauthorization(account['id'],binding,publication=self.publication)

    def seed(self, *, cached=True):
        import uuid
        from agent_monitor.accounts import codex_accounts as ca
        from agent_monitor.quota.quota_refresh import account_fingerprint
        from parsers.accounts import Account
        account = self.account()
        directory = self.manager.directory(account['id'])
        path = directory / 'codex/auth.json'
        auth = json.loads(path.read_text()); auth['tokens']['access_token'] = 'synthetic-old-access'
        path.write_text(json.dumps(auth))
        record = self.manager.read(directory)
        self.original_id = str(uuid.uuid4())
        record['operation'] = {'id':self.original_id,'stage':'failed','refresh_only':True,
            'login_only':False,'message_status':'not_sent','quota_status':'failed',
            'quota_batch_id':'original-quota-batch','failure':{'category':'unknown'}}
        self.manager.save(directory, record)
        profile = 'monitor-' + account['id']
        if cached:
            ca.write_record(self.manager.root.parent/'quota_snapshot.json',{'codex':{'managed_accounts':[
                {'account_profile':profile,'account_id':account['account_id'],'account_label':account['email']} ]}})
        failure = {'stage':'credentials','kind':'invalid_result','account_profile':profile,
            'account_fingerprint':account_fingerprint('codex', Account(account['account_id'],account['email'],None))}
        state = {'version':1,'pause':{'id':'d'*32,'reason':'quotaRefreshFailed','failure':failure},
            'last_recovery':{'ok':False,'pause_id':'d'*32,'request':{
                'machine':'fixture-owner','account_id':account['account_id'],'account_label':account['email'],
                'pause_id':'e'*32,'profile':profile,'mode':'renew_auth'},
                'authentication_recovery':{'outcome':'no_new_credentials','initial_failure':{
                    'stage':'quota_read','kind':'rpc_error','http_status':401,'upstream_code':'token_revoked'}}}}
        self.manager.controls.root.mkdir(parents=True,exist_ok=True)
        self.manager.controls._save('codex',state)
        return account, quota.reauthorization_context(record,state)

    def test_same_identity_login_and_one_quota_restore_original_task_and_pause(self):
        account,binding = self.seed()
        self.start(account,binding)
        result = self.wait(account['id'])
        methods = [c['method'] for c in self.calls(account) if 'id' in c]
        self.assertEqual(methods,['initialize','account/login/start','account/read','account/rateLimits/read'])
        self.assertEqual(result['operation']['id'],self.original_id)
        self.assertEqual(result['operation']['stage'],'succeeded')
        self.assertEqual(result['last_failure']['id'],self.original_id)
        self.assertEqual(result['last_failure']['stage'],'failed')
        self.assertEqual(result['quota_batch_result']['stage'],'succeeded')
        state = self.manager.controls.read('codex')
        self.assertIsNone(state.get('pause'))
        self.assertIsNone(state.get('active'))
        self.assertTrue(state['last_recovery']['ok'])
        self.assertEqual(state['last_recovery']['authentication_recovery']['initial_failure']['http_status'],401)
        self.assertTrue((self.root/'quota_snapshot.json').exists())

    def test_ordinary_login_stays_paused_and_wrong_binding_never_starts(self):
        from agent_monitor.accounts.codex_accounts import ActionError
        account,binding = self.seed()
        with self.assertRaises(ActionError):
            self.manager.start_reauthorization(account['id'],{**binding,'account_label':'other@example.test'},publication=self.publication)
        self.assertEqual(self.spawned,[])
        self.manager.start(account['id'],login_only=True)
        result = self.wait(account['id'])
        self.assertEqual(result['operation']['failure']['category'],'locallyPaused')
        self.assertEqual(self.spawned,[])

    def test_changed_pause_before_admission_starts_no_peer(self):
        account,binding=self.seed()
        with mock.patch.object(self.manager,'launch') as launch:
            self.start(account,binding)
            prepared=launch.call_args.args[0]
        state=self.manager.controls.read('codex');state['pause']['id']='a'*32
        self.manager.controls._save('codex',state)
        self.manager.launch(prepared)
        result=self.wait(account['id'])
        self.assertEqual(result['operation']['failure']['category'],'locallyPaused')
        self.assertEqual(self.spawned,[])

    def test_cancel_and_manager_restart_retain_original_quota_binding(self):
        from agent_monitor.accounts import codex_accounts as ca
        account,binding=self.seed();self.scenario='wait_login'
        self.start(account,binding)
        pending=self.wait(account['id'],'login')
        self.assertEqual(pending['operation']['recovery']['original_operation']['id'],self.original_id)
        self.manager.cancel(account['id']);self.wait(account['id'])
        root=self.manager.root;self.manager.close()
        self.manager=ca.Manager(root);self.addCleanup(self.manager.close)
        result=self.manager.view(self.manager.directory(account['id']))
        self.assertEqual(result['operation']['stage'],'cancelled')
        self.assertEqual(result['operation']['recovery']['original_operation']['id'],self.original_id)
        self.assertIsNone(quota.reauthorization_context(result,self.manager.controls.read('codex')))
        self.assertNotIn('account/rateLimits/read',[c['method'] for c in self.calls(account)])

    def test_wrong_identity_does_not_query_quota_or_clear_pause(self):
        account,binding = self.seed();self.scenario='wrong_workspace'
        self.start(account,binding)
        result=self.wait(account['id'])
        self.assertEqual(result['operation']['stage'],'failed')
        self.assertNotIn('account/rateLimits/read',[c['method'] for c in self.calls(account)])
        self.assertIsNotNone(self.manager.controls.read('codex').get('pause'))
        self.assertEqual(result['operation']['recovery']['original_operation']['id'],self.original_id)

    def test_false_completion_and_save_failure_preserve_recovery_chain(self):
        from agent_monitor.quota import quota_service
        for scenario in ('login_unknown_failure','login_false_saved','login_poll_rate_limit','no_reset','prequota_failure','save_failure'):
            with self.subTest(scenario=scenario):
                account,binding=self.seed();self.scenario='success' if scenario=='save_failure' else scenario
                context = mock.patch.object(quota_service,'record_codex_reading',side_effect=OSError('fixture save failed')) if scenario=='save_failure' else mock.patch.object(quota_service,'record_codex_reading',wraps=quota_service.record_codex_reading)
                with context:
                    self.start(account,binding)
                    result=self.wait(account['id'])
                self.assertEqual(result['operation']['stage'],'failed')
                self.assertEqual(result['operation']['recovery']['original_operation']['id'],self.original_id)
                self.assertIsNotNone(self.manager.controls.read('codex').get('pause'))
                if scenario in ('login_unknown_failure','login_false_saved','login_poll_rate_limit'):
                    self.assertNotIn('account/rateLimits/read',[c['method'] for c in self.calls(account)])
                    self.assertIn('login_completion',result['operation']['diagnostics'])
                    self.assertNotIn('SECRET_FIXTURE_TOKEN',json.dumps(result['operation']['diagnostics']))
                    self.assertNotIn('synthetic-old-access',json.dumps(result['operation']['diagnostics']))
                if scenario=='login_false_saved':
                    saved=json.loads((self.manager.directory(account['id'])/'codex/auth.json').read_text())
                    self.assertEqual(saved['tokens']['access_token'],'SECRET_FIXTURE_TOKEN')
                if scenario=='login_poll_rate_limit':
                    self.assertEqual(result['operation']['failure']['category'],'loginRateLimited')
                    self.assertEqual(self.manager.login_generation,1)
                # Separate synthetic identity/profile per failure case.
                self.manager.close()
                from agent_monitor.accounts import codex_accounts as ca
                self.manager=ca.Manager(self.root/(scenario+'-next')/'codex-accounts')
                self.addCleanup(self.manager.close)

    def test_normal_quota_view_updates_and_commit_failures_restore_publication(self):
        from contextlib import contextmanager
        from types import SimpleNamespace
        from agent_monitor.quota import quota_sync
        from agent_monitor.quota.quota_view import QuotaView
        from agent_monitor.snapshots import generation, exporter
        for fault in ('none','publication','final_save','close_save'):
            with self.subTest(fault=fault):
                account,binding=self.seed();self.scenario='success'
                profile='monitor-'+account['id']
                block={'account_id':account['account_id'],'account_label':account['email'],
                    'account_profile':profile,'seven_day_pct':91,'seven_day_resets_at':1900000000,
                    'updated_at':'2026-11-01T00:00:00Z','quota_observation':{'source':'active_query','observed_at':'2026-11-01T00:00:00Z'}}
                machine=SimpleNamespace(name='fixture-owner',ssh_host='fixture-owner',is_self=True)
                current=SimpleNamespace(host=machine.name,meta={'rate_limits':{'codex':{'managed_accounts':[block]},'claude':{}},
                    'source_host_identity':'fixture-host','generated_at':'2026-11-01T00:00:00Z'})
                admission=SimpleNamespace(admitted=[current],config=SimpleNamespace(by_name={machine.name:machine},self_machine=machine))
                publisher=quota_sync.Manager(self.manager.root.parent/('machine-quota-'+fault+'.json'))
                view=QuotaView(mock.Mock(),publisher)
                @contextmanager
                def snapshot():
                    yield admission
                self.publication=lambda fresh:publisher.managed_recovery_publication(machine,profile,fresh,
                    expected_source='wrong-source' if fault=='publication' else 'fixture-host')
                save=self.manager.controls._save;injected=[];terminal_saves=[]
                def save_state(provider,state):
                    if not state.get('pause') and (state.get('last_recovery') or {}).get('ok') is True:
                        terminal_saves.append(True)
                        if fault=='close_save' and len(terminal_saves)>1:
                            raise OSError('fixture redundant terminal save failed')
                    if fault=='final_save' and not injected and not state.get('pause') and (state.get('last_recovery') or {}).get('ok') is True:
                        injected.append(True);raise OSError('fixture final state save failed')
                    return save(provider,state)
                with mock.patch.object(generation,'generation_admission_snapshot',snapshot), \
                     mock.patch.object(exporter,'exporter_version',return_value='0'*40), \
                     mock.patch('agent_monitor.quota.quota_view.hub.enabled',return_value=True), \
                     mock.patch.object(self.manager.controls,'_save',side_effect=save_state):
                    before=view.live_from_admission(admission)['codex']['accounts'][0]['seven_day_used_pct']
                    self.start(account,binding);result=self.wait(account['id'])
                    after=view.live_from_admission(admission)['codex']['accounts'][0]['seven_day_used_pct']
                    reopened=QuotaView(mock.Mock(),quota_sync.Manager(publisher.path))
                    self.assertEqual(reopened.live_from_admission(admission)['codex']['accounts'][0]['seven_day_used_pct'],after)
                self.assertEqual(before,91)
                state=self.manager.controls.read('codex')
                if fault in ('none','close_save'):
                    self.assertEqual(after,0)
                    self.assertEqual(result['operation']['id'],self.original_id)
                    self.assertEqual(result['operation']['stage'],'succeeded')
                    self.assertIsNone(state.get('pause'))
                    self.assertIsNone(state.get('active'))
                    self.assertEqual(len(terminal_saves),1)
                    self.assertNotIn(account['id'],self.manager.jobs)
                    if fault=='close_save':
                        local=self.manager.controls.acquire('codex')
                        local.close()
                    self.assertEqual(state['last_recovery']['previous_recovery']['authentication_recovery']['outcome'],'no_new_credentials')
                else:
                    self.assertEqual(after,91)
                    self.assertIsNotNone(state.get('pause'))
                    self.assertFalse(state['last_recovery']['ok'])
                    self.assertEqual(result['operation']['stage'],'failed')
                    self.assertEqual(result['operation']['failure']['stage'],'saving')
                    self.assertEqual(result['operation']['recovery']['original_operation']['id'],self.original_id)
                    self.assertEqual(result['operation']['diagnostics']['local_commit']['stage'],'recovery_commit')
                    self.assertEqual(state['pause']['failure']['stage'],'recovery_commit')


    def test_first_monitor_observation_reaches_cache_publication_and_view(self):
        from contextlib import contextmanager
        from types import SimpleNamespace
        from agent_monitor.quota import quota_sync, quota_service
        from agent_monitor.quota.quota_view import QuotaView
        from agent_monitor.snapshots import generation, exporter
        account, binding = self.seed(cached=False)
        profile = 'monitor-' + account['id']
        machine = SimpleNamespace(name='fixture-owner', ssh_host='fixture-owner', is_self=True)
        current = SimpleNamespace(host=machine.name, meta={'rate_limits': {'codex': {}, 'claude': {}},
            'source_host_identity': 'fixture-host', 'generated_at': '2026-11-01T00:00:00Z'})
        admission = SimpleNamespace(admitted=[current], config=SimpleNamespace(by_name={machine.name: machine}, self_machine=machine))
        publisher = quota_sync.Manager(self.root / 'machine_quota.json')
        @contextmanager
        def snapshot():
            yield admission
        self.publication = lambda fresh: publisher.managed_recovery_publication(machine, profile, fresh, expected_source='fixture-host')
        with mock.patch.object(generation, 'generation_admission_snapshot', snapshot), mock.patch.object(exporter, 'exporter_version', return_value='0'*40), mock.patch('agent_monitor.quota.quota_view.hub.enabled', return_value=True):
            self.start(account, binding)
            result = self.wait(account['id'])
            self.assertEqual(result['operation']['stage'], 'succeeded')
            view = QuotaView(mock.Mock(), publisher)
            rows = view.live_from_admission(admission)['codex']['accounts']
            self.assertEqual(rows[0]['seven_day_used_pct'], 0)
            reopened = quota_sync.Manager(publisher.path)
            self.assertEqual(QuotaView(mock.Mock(), reopened).live_from_admission(admission)['codex']['accounts'][0]['seven_day_used_pct'], 0)
            from datetime import datetime, timezone, timedelta
            after = {**result['operation']['after'], 'seven_day_used_pct':42,
                'observed_at':(datetime.now(timezone.utc)+timedelta(seconds=1)).isoformat()}
            with mock.patch.object(quota, 'read_bound', return_value=after):
                self.manager.start(account['id'], refresh_only=True)
                refreshed = self.wait(account['id'])
            self.assertEqual(refreshed['operation']['after']['seven_day_used_pct'],42)
            normal = view.live_from_admission(admission)['codex']['accounts'][0]
            self.assertEqual(normal['seven_day_used_pct'],42)
            self.assertEqual(normal['updated_at'],after['observed_at'])
            self.assertEqual(QuotaView(mock.Mock(),quota_sync.Manager(publisher.path)).live_from_admission(admission)['codex']['accounts'][0]['seven_day_used_pct'],42)
            with mock.patch.object(quota_service, 'read_cache', side_effect=PermissionError('synthetic unreadable local quota cache')), mock.patch('agent_monitor.quota.quota_sync.diagnostics.monitor.record_failure') as diagnostic:
                self.assertEqual(view.live_from_admission(admission)['codex']['accounts'][0]['seven_day_used_pct'],0)
                self.assertTrue(publisher.status()['cache_error'])
                diagnostic.assert_called()
            self.assertEqual(view.live_from_admission(admission)['codex']['accounts'][0]['seven_day_used_pct'],42)
            self.assertIsNone(publisher.status()['cache_error'])
            current.meta['source_host_identity'] = 'different-host'
            self.assertFalse(publisher.merge(current, machine)['codex'].get('managed_accounts'))
            current.meta['source_host_identity'] = 'fixture-host'
            cold = quota_sync.Manager(self.root/'cold_machine_quota.json')
            with mock.patch.object(generation,'self_certified_host_identity',return_value='different-host'):
                self.assertFalse(cold.merge(current,machine)['codex'].get('managed_accounts'))
            self.assertEqual(cold.merge(current,machine)['codex']['managed_accounts'][0]['seven_day_pct'],42)
            unavailable = generation.GenerationError('synthetic missing host identity')
            with mock.patch.object(generation,'self_certified_host_identity',side_effect=unavailable), mock.patch('agent_monitor.quota.quota_sync.diagnostics.monitor.record_failure') as diagnostic:
                self.assertEqual(view.live_from_admission(admission)['codex']['accounts'][0]['seven_day_used_pct'],0)
                self.assertTrue(publisher.status()['cache_error'])
                diagnostic.assert_called()
                self.assertEqual(quota_sync.diagnostics.failure_details(unavailable)['error_type'],'GenerationError')
            record = self.manager.read(self.manager.directory(account['id']))
            record['email'] = 'changed@example.test'
            self.manager.save(self.manager.directory(account['id']), record)
            self.assertFalse(publisher.merge(current, machine)['codex']['managed_accounts'])
            record['email'] = account['email']
            self.manager.save(self.manager.directory(account['id']), record)
            remote = SimpleNamespace(name=machine.name, ssh_host=machine.ssh_host, is_self=False)
            self.assertFalse(publisher.merge(current, remote)['codex']['managed_accounts'])
            (self.manager.directory(account['id']) / 'profile.json').unlink()
            self.assertFalse(publisher.merge(current, machine)['codex']['managed_accounts'])
        cache = quota_service.read_cache(self.root / 'quota_snapshot.json')['codex']['managed_accounts']
        self.assertEqual(cache[0]['account_profile'], profile)
        self.assertIn('network', self.manager.controls.read('codex')['last_recovery'])

    def failed_commit(self):
        from contextlib import contextmanager
        account, binding = self.seed(cached=False)
        @contextmanager
        def broken(block):
            raise OSError('synthetic local publication failure')
            yield
        self.publication = broken
        self.start(account, binding)
        result = self.wait(account['id'])
        self.assertEqual(result['operation']['stage'], 'failed')
        pause = self.manager.controls.read('codex')['pause']
        self.assertIsNotNone(quota.local_commit_context(result, self.manager.controls.read('codex')))
        return account, result, pause

    def test_local_commit_uses_retained_result_and_rejects_replay(self):
        from contextlib import nullcontext
        from agent_monitor.accounts.codex_accounts import ActionError
        account, result, pause = self.failed_commit()
        count = len(self.spawned)
        with mock.patch.object(self.manager, 'open_rpc') as rpc:
            saved = self.manager.commit_saved_recovery(account['id'], result['operation']['id'], pause['id'], publication=lambda block: nullcontext())
            rpc.assert_not_called()
        self.assertEqual(len(self.spawned), count)
        self.assertEqual(saved['operation']['id'], self.original_id)
        self.assertEqual(saved['operation']['stage'], 'succeeded')
        state = self.manager.controls.read('codex')
        self.assertNotIn('pause', state)
        self.assertNotIn('active', state)
        self.assertEqual(state['last_pause']['id'], pause['id'])
        with self.assertRaises(ActionError):
            self.manager.commit_saved_recovery(account['id'], result['operation']['id'], pause['id'], publication=lambda block: nullcontext())

    def test_local_commit_failure_keeps_candidate_and_releases_only_own_probe(self):
        from contextlib import contextmanager
        from agent_monitor.accounts.codex_accounts import ActionError
        account, result, pause = self.failed_commit()
        @contextmanager
        def broken(block):
            raise OSError('synthetic local write still broken')
            yield
        with self.assertRaises(ActionError), mock.patch.object(self.manager, 'open_rpc') as rpc:
            self.manager.commit_saved_recovery(account['id'], result['operation']['id'], pause['id'], publication=broken)
        rpc.assert_not_called()
        current = self.manager.controls.read('codex')
        self.assertEqual(current['pause']['id'], pause['id'])
        self.assertNotIn('active', current)
        self.assertNotIn('probe_attempt', current['pause'])
        record = self.manager.read(self.manager.directory(account['id']))
        self.assertEqual(record['operation']['candidate_quota'], result['operation']['candidate_quota'])
        self.assertEqual(record['operation']['recovery'], result['operation']['recovery'])
        for changed in ({'active': {'id':'other'}}, {'pause': {**current['pause'], 'id':'different', 'probe_attempt':{'id':'other'}}}):
            self.assertIsNone(quota.local_commit_context(record, {**current, **changed}))
        record['operation']['candidate_quota'].update(five_hour_resets_at=1, seven_day_resets_at=1)
        self.assertIsNone(quota.local_commit_context(record, current))

    def test_local_final_save_failure_can_continue_without_reauthorizing(self):
        from contextlib import nullcontext
        from agent_monitor.accounts.codex_accounts import ActionError
        account, result, pause = self.failed_commit()
        save = self.manager.controls._save
        failures = []
        def fail_terminal(provider, state):
            if not state.get('pause') and not failures:
                failures.append(True)
                raise OSError('synthetic final local state failure')
            return save(provider, state)
        with mock.patch.object(self.manager.controls, '_save', side_effect=fail_terminal), mock.patch.object(self.manager, 'open_rpc') as rpc:
            with self.assertRaises(ActionError):
                self.manager.commit_saved_recovery(account['id'], result['operation']['id'], pause['id'], publication=lambda block:nullcontext())
            rpc.assert_not_called()
        state = self.manager.controls.read('codex')
        self.assertEqual(state['pause']['id'], pause['id'])
        self.assertNotIn('probe_attempt', state['pause'])
        self.assertNotIn('active', state)
        failed = self.manager.read(self.manager.directory(account['id']))
        self.assertEqual(failed['operation']['stage'], 'failed')
        self.assertEqual(failed['operation']['candidate_quota'], result['operation']['candidate_quota'])
        with mock.patch.object(self.manager, 'open_rpc') as rpc:
            saved = self.manager.commit_saved_recovery(account['id'], result['operation']['id'], pause['id'], publication=lambda block:nullcontext())
            rpc.assert_not_called()
        self.assertEqual(saved['operation']['id'], self.original_id)
        self.assertEqual(saved['operation']['stage'], 'succeeded')

    def test_monitor_cache_projection_preserves_explicit_permission_and_unknown_values(self):
        from agent_monitor.quota import quota_service
        from parsers.accounts import Account
        account = self.account()
        for allowed in (True, False, None):
            reading = {'observed_at':'2026-11-07T00:00:00Z', 'seven_day_used_pct':80,
                'seven_day_resets_at':1900000000, 'codex_quota_available':allowed,
                'ordinary_usage_allowed':allowed, 'spend_control_reached':allowed,
                'rate_limit_reached_type':'credits' if allowed is not None else None}
            quota_service.record_codex_reading(self.root/'quota_snapshot.json',
                Account(account['account_id'],account['email'],None),reading,
                account_profile='monitor-'+account['id'],profile_root=self.manager.root)
            row=quota_service.read_cache(self.root/'quota_snapshot.json')['codex']['managed_accounts'][0]
            for key in ('codex_quota_available','ordinary_usage_allowed','spend_control_reached','rate_limit_reached_type'):
                self.assertEqual(row[key],reading[key])
            self.assertIsNone(row['five_hour_pct'])

    def test_local_commit_http_scope_origin_and_real_first_member_publication(self):
        import http.client
        import threading
        from contextlib import contextmanager
        from types import SimpleNamespace
        from agent_monitor import http_server as server
        from agent_monitor.accounts import codex_accounts as ca
        from agent_monitor.quota import quota_sync
        from agent_monitor.quota.quota_view import QuotaView
        from agent_monitor.snapshots import generation, exporter
        account, result, pause = self.failed_commit()
        profile = 'monitor-' + account['id']
        machine = SimpleNamespace(name='fixture-owner', ssh_host='fixture-owner', is_self=True)
        current = SimpleNamespace(host=machine.name, meta={'rate_limits': {'codex': {}, 'claude': {}},
            'source_host_identity':'fixture-host', 'generated_at':'2026-11-01T00:00:00Z'})
        admission = SimpleNamespace(admitted=[current],config=SimpleNamespace(by_name={machine.name:machine},self_machine=machine))
        publisher = quota_sync.Manager(self.root/'machine_quota.json')
        binding = quota.local_commit_context(result, self.manager.controls.read('codex'))
        @contextmanager
        def snapshot():
            yield admission
        status = {'source_host_identity':'fixture-host','account':{'local_commit':binding,'quota_profile':profile}}
        with mock.patch.object(server,'QUOTA_REFRESH',publisher), mock.patch.object(ca,'manager',self.manager), \
                mock.patch.object(server.hub,'enabled',return_value=False), mock.patch.object(server.hub,'is_hub',return_value=True), \
                mock.patch.object(server.sync,'load_machine_config',return_value=admission.config), \
                mock.patch.object(publisher,'recovery_status',return_value=status), \
                mock.patch.object(generation,'generation_admission_snapshot',snapshot), \
                mock.patch.object(exporter,'exporter_version',return_value='0'*40), \
                mock.patch.object(self.manager,'open_rpc') as rpc:
            httpd=server.ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
            thread=threading.Thread(target=httpd.serve_forever);thread.start()
            body={'id':account['id'],'machine':machine.name,'operation_id':result['operation']['id'],'pause_id':pause['id']}
            def post(value, *, trusted=True):
                conn=http.client.HTTPConnection('127.0.0.1',httpd.server_port,timeout=3)
                try:
                    headers={'Content-Type':'application/json','X-Agent-Monitor':'codex-accounts',
                        'Origin':f'http://127.0.0.1:{httpd.server_port}' if trusted else 'http://untrusted.example'}
                    conn.request('POST','/api/codex-accounts/commit-recovery',json.dumps(value),headers)
                    response=conn.getresponse();return response.status,json.loads(response.read())
                finally:conn.close()
            try:
                self.assertEqual(post(body,trusted=False)[0],403)
                self.assertEqual(post({**body,'pause_id':'wrong'})[0],409)
                self.assertEqual(post({**body,'operation_id':'wrong'})[0],409)
                code,saved=post(body)
                self.assertEqual(code,200)
                self.assertEqual(saved['operation']['id'],self.original_id)
                self.assertEqual(saved['operation']['stage'],'succeeded')
                self.assertEqual(post(body)[0],409)
                rpc.assert_not_called()
                with mock.patch('agent_monitor.quota.quota_view.hub.enabled',return_value=True):
                    self.assertEqual(QuotaView(mock.Mock(),publisher).live_from_admission(admission)['codex']['accounts'][0]['seven_day_used_pct'],0)
            finally:
                httpd.shutdown();httpd.server_close();thread.join(timeout=3)


if __name__ == "__main__":
    unittest.main()
