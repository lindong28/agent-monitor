"""HTTP and process regression fixtures; no real state/providers."""
import http.client
import json
import os
from pathlib import Path
import signal
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock
import server
import sync_process
import process_identity
import network_report
ROOT = Path(__file__).resolve().parents[1]

class HttpMutationTests(unittest.TestCase):
    def setUp(self):
        dns = mock.patch.object(server.socket, 'getfqdn', return_value='fixture.example')
        dns.start()
        self.addCleanup(dns.stop)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        root_patch = mock.patch.object(server, 'ROOT', self.root)
        root_patch.start()
        self.addCleanup(root_patch.stop)
        self.http = server.ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.close)
        self.origin = 'http://127.0.0.1:%d' % self.http.server_port
    def close(self):
        self.http.shutdown(); self.http.server_close(); self.thread.join(2)
    def request(self, method, path, headers=None):
        conn = http.client.HTTPConnection('127.0.0.1', self.http.server_port, timeout=3)
        try:
            values = {'Origin': self.origin, 'X-Agent-Monitor': 'action'}
            values.update(headers or {})
            conn.request(method,path,headers={k:v for k,v in values.items() if v is not None})
            response=conn.getresponse(); response.read(); return response.status
        finally: conn.close()
    def test_all_mutations_share_browser_boundary(self):
        with mock.patch.object(server,'_compile_check',return_value=[]), mock.patch.object(server,'_schedule_reexec') as restart, mock.patch.object(server,'_remove_account_memory') as remove, mock.patch.object(server,'_maybe_sync_remotes') as refresh, mock.patch.object(server,'_sync_status',return_value={}):
            for path in ('/api/restart','/api/refresh','/api/account-memory/remove'):
                for headers in ({'Origin':'https://other.example'},{'Origin':None},{'X-Agent-Monitor':None},{'Sec-Fetch-Site':'cross-site'},{'Host':'attacker.example','Origin':'http://attacker.example'}):
                    with self.subTest(path=path,headers=headers): self.assertEqual(self.request('POST',path,headers),403)
            restart.assert_not_called(); remove.assert_not_called(); refresh.assert_not_called()
    def test_safe_methods_cannot_request_explicit_refresh(self):
        with mock.patch.object(server,'_maybe_sync_remotes') as sync:
            for method in ('GET','HEAD'):
                for path in ('/api/refresh','/api/overview?force=1','/api/pivot?force=1','/api/pivot-filters?force=1','/api/sessions?force=1','/api/network?force=1'):
                    with self.subTest(method=method,path=path): self.assertEqual(self.request(method,path),405)
            sync.assert_not_called()
    def test_same_origin_refresh_reaches_action_once(self):
        with mock.patch.object(server,'refresh_endpoint',return_value={'refresh_requested':1}) as refresh:
            self.assertEqual(self.request('POST','/api/refresh?ack=1'),200)
            refresh.assert_called_once_with({'ack':['1']})

    def test_network_cli_forced_post_and_cached_get_use_actual_http_boundary(self):
        with mock.patch.object(network_report,'_live_server_port',return_value=self.http.server_port), \
             mock.patch.object(server,'network',return_value={'fixture':'fresh'}) as refresh, \
             mock.patch.dict(server.ROUTES,{'/api/network':lambda query:{'fixture':'cached'}}):
            self.assertEqual(network_report.collect(force=True)[0],{'fixture':'fresh'})
            refresh.assert_called_once_with({'force':['1']})
            self.assertEqual(network_report.collect(force=False)[0],{'fixture':'cached'})

    def test_restart_and_remove_same_origin_reach_handlers(self):
        def response(handler): handler._send_json({'ok':True})
        for path,method in (('/api/restart','_handle_restart'),('/api/account-memory/remove','_handle_account_memory_remove')):
            with mock.patch.object(server.Handler,method,autospec=True,side_effect=response) as action:
                self.assertEqual(self.request('POST',path),200)
                action.assert_called_once()

    def test_restart_lock_covers_admission_delay_and_failed_reexec(self):
        guard = server.deployment_guard
        scheduled = threading.Event()
        with mock.patch.object(server, '_compile_check', return_value=[]), mock.patch.object(server.threading, 'Timer') as timer:
            timer.return_value.start.side_effect = scheduled.set
            with guard.deployment_lock(self.root, inherit=False):
                self.assertEqual(self.request('POST', '/api/restart'), 409)
            timer.assert_not_called()
            self.assertEqual(self.request('POST', '/api/restart'), 200)
            self.assertTrue(scheduled.wait(2))
            self.assertEqual(self.request('POST', '/api/restart'), 409)
            self.assertEqual(timer.call_count, 1)
            with self.assertRaises(guard.GuardError), guard.deployment_lock(self.root, inherit=False):
                pass
            callback = timer.call_args.args[1]
            with mock.patch.object(server.codex_accounts.manager, 'close'), mock.patch.object(server.client_network.hub, 'close'), mock.patch.object(server.QUOTA_REFRESH, 'shutdown'), mock.patch.object(server, 'QUERY_VIEW', None), mock.patch.object(server.os, 'execv', side_effect=RuntimeError('fixture exec failure')):
                with self.assertRaisesRegex(RuntimeError, 'fixture exec failure'):
                    callback()
            with guard.deployment_lock(self.root, inherit=False) as fd:
                self.assertFalse(os.get_inheritable(fd))

class ProcessGroupTests(unittest.TestCase):
    def test_live_identity_survives_missing_or_dead_pid_alias(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            for name in ('agent-monitor','process_identity.py','deployment_guard.py'):
                shutil.copy2(ROOT/name,root/name)
            (root/'hub.py').write_text('print("standalone")\n')
            state=root/'state';state.mkdir()
            (state/'port').write_text('39123')
            child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(20)'])
            dead=subprocess.Popen([sys.executable,'-c','pass']);dead.wait(timeout=3)
            environment=dict(os.environ,AGENT_MONITOR_PYTHON=sys.executable)
            try:
                process_identity.record(state,root,child.pid)
                for alias in (None,dead.pid):
                    with self.subTest(alias=alias):
                        if alias is None: (state/'pid').unlink(missing_ok=True)
                        else: (state/'pid').write_text(str(alias))
                        self.assertEqual(process_identity.read_owned(state,root)[0],child.pid)
                        for command in ('status','start'):
                            result=subprocess.run([str(root/'agent-monitor'),command],env=environment,
                                                  capture_output=True,text=True,timeout=5)
                            self.assertEqual(result.returncode,0,result.stderr)
                            self.assertIn(str(child.pid),result.stdout)
                (state/'pid').unlink(missing_ok=True)
                result=subprocess.run([str(root/'agent-monitor'),'stop'],env=environment,
                                      capture_output=True,text=True,timeout=13)
                self.assertEqual(result.returncode,0,result.stderr)
                self.assertEqual(child.wait(timeout=3),-signal.SIGTERM)
                self.assertFalse((state/'pid-identity.json').exists())
            finally:
                if child.poll() is None:child.kill()
                child.wait(timeout=3)

    def test_live_pid_conflict_never_overrides_the_identity_record(self):
        with tempfile.TemporaryDirectory() as directory:
            state=Path(directory)
            child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(20)'])
            try:
                process_identity.record(state,ROOT,child.pid)
                (state/'pid').write_text(str(os.getpid()))
                with self.assertRaises(process_identity.IdentityUnknown):
                    process_identity.stop(state,ROOT,grace=.05)
                self.assertIsNone(child.poll())
                (state/'pid').unlink()
                saved=json.loads((state/'pid-identity.json').read_text())
                saved['identity']['start']='wrong-instance'
                (state/'pid-identity.json').write_text(json.dumps(saved))
                with self.assertRaises(process_identity.IdentityUnknown):
                    process_identity.read_owned(state,ROOT)
            finally:
                if child.poll() is None:child.kill()
                child.wait(timeout=3)

    def test_hub_main_registers_its_own_instance_for_cli_status(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            for name in ('agent-monitor','process_identity.py','deployment_guard.py'):
                shutil.copy2(ROOT/name,root/name)
            (root/'hub.py').write_text('print("standalone")\n')
            with mock.patch.object(server,'ROOT',root), \
                 mock.patch.object(server,'_capture_startup_machine_config'), \
                 mock.patch.object(server.hub,'enabled',return_value=False), \
                 mock.patch.object(server.hub,'is_hub',return_value=True), \
                 mock.patch.object(server.hub,'interval',return_value=10), \
                 mock.patch.object(server,'_maybe_sync_remotes'), \
                 mock.patch.object(server,'QUERY_VIEW',None), \
                 mock.patch.object(server.diagnostics.monitor,'start'), \
                 mock.patch.object(server.diagnostics.monitor,'close'), \
                 mock.patch.object(server.codex_accounts.manager,'close'), \
                 mock.patch.object(server.QUOTA_REFRESH,'shutdown'), \
                 mock.patch.object(server.signal,'signal'), \
                 mock.patch.object(server.ThreadingHTTPServer,'serve_forever'), \
                 mock.patch.object(sys,'argv',['server','--host','127.0.0.1','--port','0']):
                server.main()
            self.assertTrue((root/'state/pid-identity.json').exists())
            self.assertEqual(process_identity.read_owned(root/'state',root)[0],os.getpid())
            result=subprocess.run([str(root/'agent-monitor'),'status'],
                                  env=dict(os.environ,AGENT_MONITOR_PYTHON=sys.executable),
                                  capture_output=True,text=True,timeout=5)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertIn('pid=%s'%os.getpid(),result.stdout)

    def test_real_cli_start_records_the_instance_and_stop_releases_it(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            for name in ('agent-monitor','process_identity.py','deployment_guard.py'):
                shutil.copy2(ROOT/name,root/name)
            (root/'hub.py').write_text('print("standalone")\n')
            (root/'server.py').write_text('''import argparse,json,threading
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
p=argparse.ArgumentParser();p.add_argument('--host');p.add_argument('--port',type=int);a=p.parse_args()
class Handler(BaseHTTPRequestHandler):
 def do_GET(self):
  self.send_response(200);self.send_header('Content-Length','2');self.end_headers();self.wfile.write(b'{}')
 def log_message(self,*args):pass
s=ThreadingHTTPServer((a.host,a.port),Handler)
timer=threading.Timer(15,s.shutdown);timer.daemon=True;timer.start()
s.serve_forever()
''')
            with socket.socket() as probe:
                probe.bind(('127.0.0.1',0));port=probe.getsockname()[1]
            environment=dict(os.environ,AGENT_MONITOR_PYTHON=sys.executable,
                             AGENT_MONITOR_BIND='127.0.0.1',AGENT_MONITOR_PORT=str(port))
            try:
                for role, prefix in (('standalone', []), ('client', ['local'])):
                    (root/'hub.py').write_text('print(' + repr(role) + ')\n')
                    started=subprocess.run([str(root/'agent-monitor'), *prefix, 'start'],env=environment,capture_output=True,text=True,timeout=8)
                    self.assertEqual(started.returncode,0,started.stderr)
                    owned=process_identity.read_owned(root/'state',root)
                    self.assertIsNotNone(owned)
                    stopped=subprocess.run([str(root/'agent-monitor'), *prefix, 'stop'],env=environment,capture_output=True,text=True,timeout=13)
                    self.assertEqual(stopped.returncode,0,stopped.stderr)
                    self.assertIsNone(process_identity.identity(owned[0]))
                    self.assertFalse((root/'state/pid-identity.json').exists())
            finally:
                process_identity.stop(root/'state',root,grace=.05)

    def test_malformed_saved_identity_is_unknown_not_stopped(self):
        with tempfile.TemporaryDirectory() as directory:
            state=Path(directory)
            (state/'pid').write_text(str(os.getpid()))
            for saved in (None,[],{},'invalid'):
                (state/'pid-identity.json').write_text(json.dumps(saved))
                with self.assertRaises(process_identity.IdentityUnknown):
                    process_identity.read_owned(state,ROOT)

    def test_owned_instance_stops_but_mismatched_identity_never_signals(self):
        with tempfile.TemporaryDirectory() as directory:
            state=Path(directory)
            for ignores_term in (False, True):
                body='import time,signal;'
                if ignores_term: body+='signal.signal(signal.SIGTERM,signal.SIG_IGN);'
                body+='print("ready",flush=True);time.sleep(20)'
                process=subprocess.Popen([sys.executable,'-c',body],stdout=subprocess.PIPE,text=True)
                try:
                    self.assertEqual(process.stdout.readline().strip(),'ready')
                    (state/'pid').write_text(str(process.pid))
                    process_identity.record(state,ROOT,process.pid)
                    saved=json.loads((state/'pid-identity.json').read_text())
                    saved['identity']['start']='wrong-instance'
                    (state/'pid-identity.json').write_text(json.dumps(saved))
                    with self.assertRaises(process_identity.IdentityUnknown):
                        process_identity.stop(state,ROOT,grace=.05)
                    self.assertIsNone(process.poll())
                    process_identity.record(state,ROOT,process.pid)
                    process_identity.stop(state,ROOT,grace=.05)
                    self.assertEqual(process.wait(timeout=2),-signal.SIGKILL if ignores_term else -signal.SIGTERM)
                finally:
                    if process.poll() is None: process.kill()
                    process.wait(timeout=2);process.stdout.close()

    def test_stopping_after_normal_reap_never_signals_old_group(self):
        with tempfile.TemporaryDirectory() as directory:
            worker=Path(directory)/'worker.py';worker.write_text('pass\n')
            with sync_process.supervised_worker(worker) as process:
                self.assertEqual(process.wait(timeout=3),0)
                with mock.patch.object(sync_process.os,'killpg') as signal_group:
                    sync_process._stop(process)
                signal_group.assert_not_called()

    def test_shutdown_and_exit_observer_can_finish_together(self):
        with tempfile.TemporaryDirectory() as directory:
            worker=Path(directory)/'worker.py'
            worker.write_text('import time\nprint("ready",flush=True)\ntime.sleep(.05)\n')
            with sync_process.supervised_worker(worker) as process:
                self.assertEqual(process.stdout.readline().strip(),'ready')
                peer=threading.Thread(target=lambda:sync_process._stop(process))
                peer.start()
                self.assertIn(process.wait(timeout=3),(0,-signal.SIGTERM))
                peer.join(3);self.assertFalse(peer.is_alive())

    def test_finished_leader_descendants_are_cleaned_before_reap(self):
        with tempfile.TemporaryDirectory() as directory:
            worker=Path(directory)/'worker.py'
            worker.write_text('import subprocess,sys\np=subprocess.Popen([sys.executable,"-c","import time;time.sleep(20)"],stdin=subprocess.DEVNULL,stderr=subprocess.DEVNULL)\nprint(p.pid,flush=True)\n')
            child=None
            try:
                with sync_process.supervised_worker(worker) as process:
                    child=int(process.stdout.readline())
                    eof=threading.Event()
                    reader=threading.Thread(target=lambda: (process.stdout.read(),eof.set()),daemon=True)
                    reader.start()
                    self.assertTrue(eof.wait(3),'inherited stdout kept the consumer blocked')
                    self.assertEqual(process.wait(timeout=3),0)
                    reader.join(1)
                deadline=time.monotonic()+3
                while time.monotonic()<deadline:
                    state=subprocess.run(['ps','-o','stat=','-p',str(child)],capture_output=True,text=True).stdout.strip()
                    if not state or state.startswith('Z'): break
                    time.sleep(.02)
                self.assertTrue(not state or state.startswith('Z'),state)
                with self.assertRaises(ChildProcessError): os.waitpid(process.pid,os.WNOHANG)
            finally:
                if child:
                    try: os.kill(child,signal.SIGKILL)
                    except ProcessLookupError: pass
    def test_cli_unknown_pid_does_not_signal_unrelated_process(self):
        with tempfile.TemporaryDirectory() as directory:
            base=Path(directory)
            (base/'cli.sh').write_text((ROOT/'agent-monitor').read_text().split('\ncase "${1:-}" in\n',1)[0])
            (base/'hub.py').write_text('print("standalone")\n')
            if (ROOT/'process_identity.py').exists(): (base/'process_identity.py').write_bytes((ROOT/'process_identity.py').read_bytes())
            (base/'state').mkdir()
            sleeper=subprocess.Popen([sys.executable,'-c','import time;time.sleep(20)'])
            try:
                (base/'state/pid').write_text(str(sleeper.pid))
                result=subprocess.run(['bash','-c','source "$1"; stop','fixture',str(base/'cli.sh')],env=dict(os.environ,AGENT_MONITOR_PYTHON=sys.executable),capture_output=True,text=True,timeout=12)
                self.assertIsNone(sleeper.poll(),result.stdout+result.stderr)
                self.assertNotEqual(result.returncode,0)
                self.assertTrue((base/'state/pid').exists())
            finally:
                if sleeper.poll() is None: sleeper.kill()
                sleeper.wait(timeout=3)
