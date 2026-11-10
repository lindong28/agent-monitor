"""Network lifecycle uses fixture state and never contacts an upstream."""
import json
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from agent_monitor.views.network import NetworkReport


class NetworkCacheLifecycleTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / 'network.json'
        self.now, self.wall = [100.0], [1000.0]
        self.good = {'timestamp': '2026-11-10T00:00:00Z', 'verdict': 'low',
                     'public': {'ok': True, 'ip': '192.0.2.1'}}

    def result(self, data):
        return subprocess.CompletedProcess([], 0, json.dumps(data), '')

    def view(self, runner=None, host='fixture'):
        return NetworkReport(runner=runner or mock.Mock(return_value=self.result(self.good)),
            child_environment=lambda env: {}, clock=lambda: self.now[0],
            wall_clock=lambda: self.wall[0], hostname=lambda: host, state_path=self.path)

    def test_restart_retains_result_and_cooldown(self):
        self.view().read()
        runner = mock.Mock(side_effect=AssertionError('must reuse persisted result'))
        restarted = self.view(runner)
        self.assertEqual(restarted.cached()['public']['ip'], '192.0.2.1')
        self.assertFalse(restarted.cached()['stale'])
        self.assertTrue(restarted.read()['refresh']['reused'])
        runner.assert_not_called()
        self.now[0] += 61
        self.wall[0] += 61
        self.assertTrue(restarted.cached()['stale'])

    def test_failure_retains_old_result_and_error_after_restart(self):
        runner = mock.Mock(side_effect=[self.result(self.good), self.result({'error': 'fixture timeout'})])
        report = self.view(runner)
        report.read()
        self.now[0] += 61
        self.wall[0] += 61
        failed = report.read()
        self.assertEqual(failed['timestamp'], self.good['timestamp'])
        self.assertTrue(failed['stale'])
        self.assertEqual(failed['last_attempt']['error'], 'fixture timeout')
        self.assertEqual(self.view().cached()['last_attempt']['error'], 'fixture timeout')
        self.assertEqual(self.view().cached()['public']['ip'], '192.0.2.1')

    def test_cache_does_not_wait_for_probe_and_two_requests_share_probe(self):
        entered, release = threading.Event(), threading.Event()
        def run(*args, **kwargs):
            entered.set()
            if not release.wait(3):
                raise AssertionError('probe not released')
            return self.result(self.good)
        runner = mock.Mock(side_effect=run)
        report, results, cached = self.view(runner), [], []
        threads = [threading.Thread(target=lambda: results.append(report.read())) for _ in range(2)]
        for thread in threads:
            thread.start()
        self.assertTrue(entered.wait(1))
        reader = threading.Thread(target=lambda: cached.append(report.cached()))
        reader.start()
        reader.join(0.5)
        try:
            self.assertFalse(reader.is_alive(), 'cache read waited for public probe')
            self.assertFalse(cached[0]['available'])
        finally:
            release.set()
            for thread in threads + [reader]:
                thread.join(4)
        self.assertEqual(len(results), 2)
        self.assertEqual(runner.call_count, 1)

    def test_host_mismatch_and_invalid_file_do_not_restore_wrong_result(self):
        self.view().read()
        self.assertFalse(self.view(host='other-host').cached()['available'])
        self.path.write_text('{broken')
        self.assertFalse(self.view().cached()['available'])

    def test_failure_without_old_result_preserves_retry_after_restart(self):
        self.view(mock.Mock(return_value=self.result({'error': 'limited', 'retry_after_seconds': 180}))).read()
        report = self.view(mock.Mock(side_effect=AssertionError('must respect cooldown')))
        self.assertEqual(report.cached()['refresh']['retry_in_seconds'], 180)
        self.assertEqual(report.read()['error'], 'limited')

    def test_write_failure_keeps_result_and_reports_problem(self):
        report = self.view()
        with mock.patch('os.replace', side_effect=OSError('fixture disk failure')):
            result = report.read()
        self.assertEqual(result['public']['ip'], '192.0.2.1')
        self.assertIn('persistence_error', result)

    def test_separate_process_restores_saved_result_without_probe(self):
        self.view().read()
        source = ('import json,sys; from agent_monitor.views.network import NetworkReport; '
                  'print(json.dumps(NetworkReport(state_path=sys.argv[1], hostname=lambda:"fixture").cached()))')
        result = subprocess.run([sys.executable, '-c', source, str(self.path)],
                                capture_output=True, text=True, check=True, timeout=10)
        self.assertEqual(json.loads(result.stdout)['public']['ip'], '192.0.2.1')

    def test_partial_public_failure_retains_old_reading_and_recovers(self):
        runner = mock.Mock(side_effect=[self.result(self.good), self.result({
            'public': {'ok': False, 'error': 'public timeout'}, 'verdict': 'unknown'}), self.result(self.good)])
        report = self.view(runner)
        report.read()
        for outcome in ('failed', 'recovered'):
            self.now[0] += 61
            self.wall[0] += 61
            result = report.read()
            self.assertEqual(result['public']['ip'], '192.0.2.1')
            self.assertEqual(result['stale'], outcome == 'failed')
            if outcome == 'failed':
                self.assertEqual(result['last_attempt']['error'], 'public timeout')
            else:
                self.assertNotIn('last_attempt', result)

    def test_credentials_are_removed_before_persistence(self):
        report = self.view(mock.Mock(return_value=self.result({'error':
            'proxy failed https://user:fixture-password@example.test/ token=fixture-token'})))
        report.read()
        raw = self.path.read_text()
        self.assertNotIn('fixture-password', raw)
        self.assertNotIn('fixture-token', raw)
        self.assertIn('proxy failed', raw)

    def test_construction_does_not_read_state_and_explicit_view_is_isolated(self):
        from agent_monitor import http_server as server
        with mock.patch.object(Path, 'read_text', side_effect=AssertionError('unexpected state read')):
            report = self.view()
            views = server.create_views(network=report)
            self.assertIs(views.network, report)
            self.assertIsNone(server.create_views().network.state_path)
        self.assertFalse(report.cached()['available'])
