import json
from pathlib import Path
import signal
import subprocess
import sys
import unittest
from unittest import mock

import server
import sync_process


ROOT = Path(__file__).resolve().parents[1]


class SyncProcessTests(unittest.TestCase):
    def setUp(self):
        self.popen = subprocess.Popen
        server._reset_sync_state_for_tests()
        self.addCleanup(server._reset_sync_state_for_tests)

    def launch_fixture(self, body, *args, **kwargs):
        script = "import sys;sys.path.insert(0,%r)\n" % str(ROOT) + body
        return self.popen([sys.executable, "-c", script], **kwargs)

    def test_real_worker_progress_and_results_keep_generation_leases_in_child(self):
        body = '''
from types import SimpleNamespace
import sync, sync_process
def collect(**kwargs):
    first = sync.SyncResult(generation=SimpleNamespace(close=lambda: None))
    last = sync.SyncResult(error="source unavailable")
    kwargs['on_statistics']('fast', first)
    kwargs['on_complete']('fast', first)
    kwargs['on_complete']('slow', last)
    return {'fast':first, 'slow':last}
sync.sync_all = collect
sync_process._worker()
'''
        events = []
        with mock.patch("sync_process.subprocess.Popen", side_effect=lambda *a, **k: self.launch_fixture(body, *a, **k)):
            results = sync_process.sync_all(
                on_statistics=lambda name, result: events.append(("statistics", name, result)),
                on_complete=lambda name, result: events.append(("complete", name, result)))
        self.assertEqual([(kind, name) for kind, name, _ in events],
                         [("statistics", "fast"), ("complete", "fast"), ("complete", "slow")])
        self.assertEqual(results, {"fast": sync_process.Outcome(True),
                                   "slow": sync_process.Outcome(False, "source unavailable")})
        self.assertEqual(server._normalize_sync_outcomes(results, ("fast", "slow"))["fast"],
                         {"kind": "success", "generation": None, "reason": None})
        self.assertFalse(sync_process._active)

    def test_protocol_failures_never_become_success(self):
        for output, code in (([], 0), ([{"phase": "result", "results": {}}], 7),
                             ([{"phase": "result", "results": {"lost": {"success": True, "error": None}}}], 0),
                             ([{"phase": "complete", "machine": "bad", "outcome": {"success": True, "error": "failure"}}], 0)):
            with self.subTest(output=output, exit=code):
                body = "import json\nsys.stdin.readline()\n"
                body += "\n".join("print(%r,flush=True)" % json.dumps(item) for item in output)
                body += "\nsys.exit(%d)\n" % code
                with mock.patch("sync_process.subprocess.Popen", side_effect=lambda *a, **k: self.launch_fixture(body, *a, **k)):
                    with self.assertRaises((ValueError, RuntimeError)):
                        sync_process.sync_all()
                self.assertFalse(sync_process._active)

    def test_parent_eof_stops_worker_when_parent_instance_disappears(self):
        # A separate watchdog thread cannot run while a C extension holds GIL.
        # Use a normal sleeping worker here; the supervisor test below exercises
        # explicit shutdown for the GIL-holding case.
        body = '''
import sync, sync_process, time
def collect(**kwargs):
    print('ready', file=sys.stderr, flush=True)
    time.sleep(30)
sync.sync_all=collect
sync_process._worker()
'''
        p = self.launch_fixture(body, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, start_new_session=True)
        try:
            p.stdin.write('{}\n'); p.stdin.flush()
            self.assertEqual(p.stderr.readline().strip(), 'ready')
            p.stdin.close()
            self.assertEqual(p.wait(timeout=3), -signal.SIGTERM)
        finally:
            if p.poll() is None:
                sync_process._stop(p)
            p.stdout.close(); p.stderr.close()

    def test_shutdown_reaps_worker_and_prevents_a_followup_spawn(self):
        body = "import ctypes\nsys.stdin.readline()\nprint('ready',flush=True)\nctypes.PyDLL(None).sleep(30)\n"
        p = self.launch_fixture(body, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                text=True, start_new_session=True)
        try:
            p.stdin.write('{}\n'); p.stdin.flush()
            self.assertEqual(p.stdout.readline().strip(), 'ready')
            with sync_process._lock:
                sync_process._active.add(p)
            sync_process.shutdown()
            self.assertIsNotNone(p.poll())
            with self.assertRaisesRegex(RuntimeError, 'shutting down'):
                sync_process.sync_all()
        finally:
            if p.poll() is None:
                sync_process._stop(p)
            p.stdin.close(); p.stdout.close()
            with sync_process._lock:
                sync_process._active.discard(p)
                sync_process._stopping = False

    def test_hub_progress_and_account_memory_remain_in_parent(self):
        server._SYNC_STATE.update(running=True, round_quota_refresh=True, round_refresh=1,
                                 refresh_requested=1, pending_machines=("fast", "slow"),
                                 statistics_pending=("fast", "slow"), phase="statistics")
        def collect(**kwargs):
            self.assertTrue(kwargs['progressive'])
            ok, fail = sync_process.Outcome(True), sync_process.Outcome(False, 'offline')
            kwargs['on_statistics']('fast', ok)
            self.assertIn('fast', server._SYNC_STATE['pending_machines'])
            self.assertNotIn('fast', server._SYNC_STATE['statistics_pending'])
            kwargs['on_complete']('fast', ok)
            kwargs['on_complete']('slow', fail)
            return {'fast':ok, 'slow':fail}
        with mock.patch('server.hub.enabled', return_value=True), \
             mock.patch('server.sync_process.sync_all', side_effect=collect), \
             mock.patch('server._remember_accounts_after_sync_publish') as remember:
            server._run_sync_round(('fast', 'slow'))
        remember.assert_called_once_with()
        self.assertEqual(server._SYNC_STATE['refresh_completed'], 1)
        self.assertFalse(server._SYNC_STATE['running'])
        self.assertEqual(server._SYNC_STATE['observations']['fast']['last_attempt_outcome'], 'success')
        self.assertEqual(server._SYNC_STATE['observations']['slow']['last_attempt_outcome'], 'failure')
        self.assertEqual(server._SYNC_STATE['observations']['slow']['reason'], 'offline')

    def test_reexec_waits_for_sync_shutdown(self):
        events = []
        with mock.patch('server.threading.Timer') as timer, \
             mock.patch('server.sync_process.shutdown', side_effect=lambda: events.append('shutdown')), \
             mock.patch('server.os.execv', side_effect=lambda *args: events.append('exec')):
            server._schedule_reexec()
            timer.call_args.args[1]()
        self.assertEqual(events, ['shutdown', 'exec'])
