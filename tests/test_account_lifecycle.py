"""Real exec/SIGTERM boundaries with isolated state and a local stdio peer."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]

SCRIPT = r'''
import faulthandler
faulthandler.dump_traceback_later(5)
import os, signal, subprocess, sys, threading, time
from pathlib import Path
from unittest import mock
import codex_accounts as ca
import server

root = Path(os.environ['HOME'])
original_popen = subprocess.Popen
peers = []
ready = threading.Event()
def factory(directory, cancelled):
    def spawn(*args, **kwargs):
        return original_popen([sys.executable, '-c',
            "import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); print('ready',flush=True); time.sleep(20)"], **kwargs)
    with mock.patch.object(ca.shutil, 'which', return_value=sys.executable), mock.patch.object(ca.subprocess, 'Popen', side_effect=spawn), mock.patch.object(ca.network_policy, 'endpoint', return_value='http://agent-monitor:fixture@127.0.0.1:1'):
        rpc = ca.Rpc(directory, cancelled)
    rpc.process.stdout.readline()
    peers.append(rpc.process)
    def initialize():
        ready.set()
        rpc.process.stdin.write(b'x' * 1000000)
        rpc.process.stdin.flush()
    rpc.initialize = initialize
    return rpc
manager = ca.Manager(root / 'profiles', rpc_factory=factory)
server.codex_accounts.manager = manager
account = manager.add('fixture@example.com', 'fixture-workspace')
from tests.test_codex_accounts import seed_auth
seed_auth(manager, account)
manager.start(account['id'], refresh_only=True)
assert ready.wait(3)

def assert_reaped():
    try:
        result = os.waitpid(peers[0].pid, os.WNOHANG)
    except ChildProcessError:
        return
    raise AssertionError('child not reaped before lifecycle boundary: %r' % (result,))

if sys.argv[1] == 'exec':
    original_exec = os.execv
    def replace(*args):
        assert_reaped()
        original_exec(sys.executable, [sys.executable, '-c', "import os; print('EXEC-REAPED',os.getpid(),flush=True)"])
    with mock.patch.object(server.os, 'execv', side_effect=replace), mock.patch.object(server.threading, 'Timer') as timer:
        print('BEFORE', os.getpid(), flush=True)
        guard = server.deployment_guard.deployment_lock(root, inherit=False)
        guard.__enter__()
        server._schedule_reexec(guard)
        timer.call_args.args[1]()
else:
    server._capture_startup_machine_config = lambda: None
    server.hub.is_hub = lambda: False
    original_server = server.ThreadingHTTPServer
    class ReadyServer(original_server):
        def serve_forever(self, *args, **kwargs):
            faulthandler.cancel_dump_traceback_later()
            print('READY', os.getpid(), flush=True)
            return super().serve_forever(*args, **kwargs)
    server.ThreadingHTTPServer = ReadyServer
    sys.argv = ['server', '--host', '127.0.0.1', '--port', '0']
    try:
        server.main()
    except SystemExit:
        assert_reaped()
        print('TERM-REAPED', flush=True)
'''


class AccountLifecycleTests(unittest.TestCase):
    def run_lifecycle(self, mode):
        with tempfile.TemporaryDirectory() as home:
            env = dict(os.environ, HOME=home, PYTHONPATH=str(ROOT))
            peer = subprocess.Popen([sys.executable, '-c', SCRIPT, mode], env=env,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                    start_new_session=True)
            try:
                if mode == 'term':
                    # A bounded selector wait avoids hanging if startup fails.
                    import selectors
                    with selectors.DefaultSelector() as selector:
                        selector.register(peer.stdout, selectors.EVENT_READ)
                        if not selector.select(6):
                            import signal
                            try:
                                os.killpg(peer.pid, signal.SIGKILL)
                            except ProcessLookupError:
                                pass
                            _, err = peer.communicate(timeout=3)
                            self.fail('fixture did not start; isolated child diagnostics:\n' + err)
                        self.assertTrue(peer.stdout.readline().startswith('READY'))
                    peer.terminate()
                out, err = peer.communicate(timeout=12)
                self.assertEqual(peer.returncode, 0, err)
                return out
            finally:
                if peer.poll() is None:
                    import signal
                    os.killpg(peer.pid, signal.SIGKILL)
                    peer.wait(timeout=3)
                peer.stdout.close()
                peer.stderr.close()

    def test_real_reexec_reaps_child_and_preserves_server_pid(self):
        lines = self.run_lifecycle('exec').splitlines()
        self.assertEqual(lines[0].split()[1], lines[1].split()[1])
        self.assertTrue(lines[1].startswith('EXEC-REAPED'))

    def test_real_sigterm_drains_child_before_server_exit(self):
        self.assertIn('TERM-REAPED', self.run_lifecycle('term'))
