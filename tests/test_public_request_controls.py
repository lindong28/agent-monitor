import concurrent.futures
import datetime
import json
import ssl
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from ip_check import cli
import pricing_fetcher
import network_policy
import server
from agent_monitor.views.network import NetworkReport


class PublicRequestControlsTests(unittest.TestCase):
    def setUp(self):
        # Requests and probe children below are mocked; no Electron is needed.
        route = mock.patch.object(network_policy, "endpoint",
                                  return_value="http://agent-monitor:synthetic-test-capability@127.0.0.1:9")
        route.start()
        self.addCleanup(route.stop)

    def test_verified_tls_success_keeps_upstream_pricing(self):
        response = mock.MagicMock()
        response.__enter__.return_value.read.return_value = b'{"fixture": {"rate": 1}}'
        with mock.patch.object(pricing_fetcher.network_policy, 'opener', return_value=mock.Mock(open=mock.Mock(return_value=response))) as request:
            self.assertEqual(pricing_fetcher._fetch_litellm_pricing(), {'fixture': {'rate': 1}})
        request.assert_called_once()
        self.assertTrue(request.call_args.args[0]._context.check_hostname)

    def test_certificate_failure_uses_fallback_without_an_unverified_retry(self):
        error = ssl.SSLCertVerificationError('fixture certificate rejected')
        with tempfile.TemporaryDirectory() as directory, \
                mock.patch.object(pricing_fetcher.network_policy, 'opener', return_value=mock.Mock(open=mock.Mock(side_effect=error))) as request, \
                mock.patch.object(pricing_fetcher, '_fallback_pricing', return_value={'fixture': {}}):
            result = pricing_fetcher.get_pricing(Path(directory) / 'cache.json', persist=False)
        self.assertIn('fixture', result)
        self.assertEqual(request.call_count, 1)
        context = request.call_args.args[0]._context
        self.assertTrue(context.check_hostname)
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)

    def test_non_success_http_response_never_parses_success_shaped_json(self):
        for status in (302, 401, 429, 503):
            for probe in (cli.get_public_info, cli.get_ip_risk, cli.get_stopforumspam):
                with self.subTest(status=status, probe=probe.__name__):
                    response = mock.Mock(status_code=status, headers={'Retry-After': '120'})
                    response.json.return_value = {'status': 'success', 'success': 1,
                                                 'ip': {'appears': 0}, '203.0.113.9': {'risk': 0}}
                    with mock.patch.object(cli, '_egress_session') as session:
                        session.return_value.get.return_value = response
                        value = probe() if probe == cli.get_public_info else probe('203.0.113.9')
                    response.json.assert_not_called()
                    self.assertIn(str(status), str(value))
                    self.assertIn('Retry-After', str(value))
                    session.return_value.get.assert_called_once()
                    self.assertFalse(session.return_value.get.call_args.kwargs['allow_redirects'])

    def test_success_http_response_still_parses_json(self):
        for probe, payload in ((cli.get_public_info, {'status': 'success'}),
                               (cli.get_ip_risk, {'203.0.113.9': {'risk': 0}}),
                               (cli.get_stopforumspam, {'success': 1, 'ip': {'appears': 0}})):
            with self.subTest(probe=probe.__name__):
                response = mock.Mock(status_code=200, headers={})
                response.json.return_value = payload
                with mock.patch.object(cli, '_egress_session') as session:
                    session.return_value.get.return_value = response
                    value = probe() if probe == cli.get_public_info else probe('203.0.113.9')
                response.json.assert_called_once()
                self.assertNotIn('查询失败', str(value))

    def test_retry_after_dates_seconds_and_invalid_headers(self):
        date = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=180)
        for header, expected in [('120', 120), (date.strftime('%a, %d %b %Y %H:%M:%S GMT'), 180),
                                 ('-1', None), ('NaN', None), ('', None)]:
            delays = []
            response = mock.Mock(status_code=429, headers={'Retry-After': header})
            with self.subTest(header=header), self.assertRaisesRegex(ValueError, 'HTTP 429'):
                cli._require_success(response, delays.append)
            if expected is None:
                self.assertEqual(delays, [])
            else:
                self.assertTrue(expected - 1 <= delays[0] <= expected)

    def test_collect_all_preserves_longest_endpoint_wait(self):
        from tests import test_ip_check_json as fixtures
        from contextlib import ExitStack
        def risk(ip, route=None, on_retry_after=None):
            on_retry_after(180)
            return '查询失败（HTTP 429）', None
        def spam(ip, route=None, on_retry_after=None):
            on_retry_after(90)
            return ['查询失败（HTTP 503）']
        with ExitStack() as stack:
            for name, value in fixtures.DEFAULT_RETURNS.items():
                stack.enter_context(mock.patch.object(cli, name, return_value=value))
            stack.enter_context(mock.patch.object(cli, 'resolve_route', return_value={
                'address': '', 'source': 'fixture', 'reason': '', 'path': '/fixture'}))
            stack.enter_context(mock.patch.object(cli, 'get_public_info', return_value=fixtures.PUBLIC_PROXY_OK))
            stack.enter_context(mock.patch.object(cli, 'get_ip_risk', side_effect=risk))
            stack.enter_context(mock.patch.object(cli, 'get_stopforumspam', side_effect=spam))
            result = cli.collect_all()
        self.assertEqual(result['retry_after_seconds'], 180)

    def test_retry_after_extends_cache_but_expiry_allows_new_explicit_request(self):
        for delay, expiry in ((180, 180), (5, 60), ('invalid', 60), (True, 60)):
            clock = [1000.0]
            response = subprocess.CompletedProcess([], 0, json.dumps({'retry_after_seconds': delay}), '')
            with self.subTest(delay=delay), \
                    mock.patch.object(subprocess, 'run', return_value=response) as request:
                report = NetworkReport(runner=request, clock=lambda: clock[0])
                report.report()
                clock[0] += expiry - 1
                report.report()
                self.assertEqual(request.call_count, 1)
                clock[0] += 1
                report.report()
                self.assertEqual(request.call_count, 2)

    def test_process_failures_are_cached(self):
        for failure in (FileNotFoundError(), OSError(), subprocess.TimeoutExpired('fixture', 30)):
            with self.subTest(failure=type(failure).__name__), \
                    mock.patch.object(subprocess, 'run', side_effect=failure) as request:
                report = NetworkReport(runner=request)
                self.assertEqual(report.report()['verdict'], 'unknown')
                report.report()
                request.assert_called_once()

    def test_force_cannot_bypass_success_or_failure_cache(self):
        for response in (subprocess.CompletedProcess([], 0, '{"verdict":"low"}', ''),
                         subprocess.CompletedProcess([], 1, '', 'fixture failure')):
            with self.subTest(returncode=response.returncode), \
                    mock.patch.object(subprocess, 'run', return_value=response) as request:
                report = NetworkReport(runner=request)
                first = report.report()
                second = report.report()
                self.assertEqual(first, second)
                request.assert_called_once()

    def test_concurrent_requests_share_one_probe(self):
        entered = threading.Event()
        release = threading.Event()

        def run(*args, **kwargs):
            entered.set()
            self.assertTrue(release.wait(2))
            return subprocess.CompletedProcess([], 0, '{"verdict":"low"}', '')

        with mock.patch.object(subprocess, 'run', side_effect=run) as request, \
                concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
            report = NetworkReport(runner=request)
            futures = [pool.submit(report.report) for _ in range(3)]
            self.assertTrue(entered.wait(1))
            release.set()
            results = [future.result(3) for future in futures]
        self.assertEqual(results, [{'verdict': 'low'}] * 3)
        request.assert_called_once()


if __name__ == '__main__':
    unittest.main()
