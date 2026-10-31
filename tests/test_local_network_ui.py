"""Local network admission, passive reads and fixed Hub adapter; all synthetic."""
import contextlib
import http.client
import json
import os
import time
from pathlib import Path
import subprocess
import threading
import unittest
from unittest import mock
from urllib.parse import urlsplit, parse_qs

from agent_monitor import http_server as server
from agent_monitor.network import host_network
from agent_monitor.network.local_network import LocalNetwork
from agent_monitor.views import hub_reads
from agent_monitor.views.network import NetworkReport


class PassiveFactsTests(unittest.TestCase):
    def test_system_timezone_offset_ignores_process_timezone_override(self):
        try:
            with mock.patch.dict(os.environ, {'TZ': 'UTC'}), mock.patch.object(host_network.os, 'readlink', return_value='/usr/share/zoneinfo/Asia/Singapore'):
                time.tzset()
                data = host_network.capture(platform='linux', run=mock.Mock(return_value=mock.Mock(stdout='[]')), read_text=lambda p: '', environment={})
                self.assertEqual(data['timezone']['name'], 'Asia/Singapore')
                self.assertEqual(data['timezone']['offset'], '+0800')
        finally:
            time.tzset()

    def test_os_facts_are_passive_and_secrets_are_removed(self):
        outputs = {'ifconfig': 'en0: flags\n\tinet 192.0.2.2 netmask ff\n\tinet6 fe80::1%en0 prefixlen 64\n',
                   '--dns': 'nameserver[0] : 192.0.2.53\nnameserver[1] : 192.0.2.53',
                   '--proxy': 'HTTPSEnable : 1\nHTTPSProxy : localhost\nHTTPSPort : 8080\nProxyAutoConfigEnable : 1'}
        commands = []
        def run(argv, **kwargs):
            commands.append(argv)
            return mock.Mock(stdout=outputs[argv[0] if argv[0] == 'ifconfig' else argv[1]])
        data = host_network.capture(platform='darwin', run=run, environment={'HTTPS_PROXY': 'http://user:secret@localhost:9/path?token=private'})
        self.assertEqual(data['dns'], ['192.0.2.53'])
        self.assertEqual(data['addresses'][0]['address'], '192.0.2.2')
        self.assertTrue(data['system_proxy']['pac_enabled'])
        self.assertNotIn('secret', json.dumps(data)); self.assertNotIn('private', json.dumps(data))
        self.assertEqual(commands, [['ifconfig', '-a'], ['scutil', '--dns'], ['scutil', '--proxy']])

    def test_linux_and_partial_failures_keep_independent_facts(self):
        for failed in (False, True):
            run = mock.Mock(side_effect=FileNotFoundError() if failed else None,
                            return_value=mock.Mock(stdout='[{"ifname":"eth0","addr_info":[{"local":"192.0.2.3"}]}]'))
            data = host_network.capture(platform='linux', run=run, read_text=lambda p: 'nameserver 192.0.2.54', environment={})
            self.assertEqual(data['dns'], ['192.0.2.54']); self.assertTrue(data['timezone']['name'])
            self.assertEqual(len(data['addresses']), 0 if failed else 1)
            self.assertEqual(data['system_proxy']['status'], 'unavailable')

    def test_cached_server_evidence_never_launches_probe_after_expiry(self):
        runner = mock.Mock(side_effect=AssertionError('public request forbidden'))
        report = NetworkReport(runner=runner, clock=lambda: 1000, hostname=lambda: 'old-server', ttl=60)
        self.assertFalse(report.cached()['available'])
        report._cache.update(ts=1, data={'timestamp': '2026-01-01', 'public': {'ip': '192.0.2.8'}})
        self.assertEqual(report.cached()['public']['ip'], '192.0.2.8')
        self.assertEqual(report.cached()['refresh']['retry_in_seconds'], 0)
        runner.assert_not_called()

    def test_process_failure_does_not_erase_os_facts_and_adjacent_reads_share_work(self):
        local = LocalNetwork()
        with mock.patch('agent_monitor.network.local_network.host_network.capture', return_value={'dns': ['192.0.2.53']}) as host, \
             mock.patch('agent_monitor.network.local_network.process_network.read_report', return_value={'schema_version': 1, 'processes': [], 'errors': [{'stage': 'collector'}]}):
            self.assertEqual(local.read()['host_network']['dns'], ['192.0.2.53'])
            local.read(); host.assert_called_once()


class HubReadTests(unittest.TestCase):
    def test_session_navigation_and_usage_filters_do_not_break_hub_detail(self):
        session = mock.Mock(get=mock.Mock(return_value=mock.Mock(status_code=200, json=lambda: {'meta': {}, 'entries': []})))
        # sessions.js carries list navigation into the detail URL; usage filters are local UI state.
        query = parse_qs('range=30d&page=2&sort=cost&session=s1&session_machine=A&session_agent=codex&machine=A&scope=all&usage_page=1&usage_model=m1&model=m2&return=%2Fcollection%3Frange%3D30d%23cost-diagnostics')
        result = hub_reads.read('/api/session/s1', query, target='http://hub.test', session_factory=lambda: contextlib.nullcontext(session))
        self.assertEqual(result['entries'], [])
        sent = parse_qs(urlsplit(session.get.call_args.args[0]).query)
        self.assertEqual(sent, {k: query[k] for k in ('range', 'sort', 'session_agent', 'machine', 'scope', 'model')} | {'sync': ['0']})

    def test_fixed_target_query_and_headers(self):
        session = mock.MagicMock()
        session.get.return_value = mock.Mock(status_code=200, json=lambda: {'hub': 'fixture'})
        factory = lambda: contextlib.nullcontext(session)
        for path in sorted(hub_reads.PATHS) + ['/api/session/session-1']:
            self.assertEqual(hub_reads.read(path, {'sync': ['1'], 'range': ['7d'], 'machine': ['A', 'B']}, target='http://hub.test:39001', session_factory=factory), {'hub': 'fixture'})
            args, kwargs = session.get.call_args
            url = urlsplit(args[0])
            self.assertEqual(url.netloc, 'hub.test:39001'); self.assertEqual(url.path, path)
            self.assertEqual(parse_qs(url.query)['sync'], ['0'])
            self.assertFalse(kwargs['allow_redirects'])
            self.assertEqual(kwargs['headers'], {'X-Agent-Monitor-Read-Hop': '1'})
        for path in ('/api/refresh', '/api/codex-accounts', '/api/session/../refresh', '/api/session/%2e%2e', '/api/session/%2f..', '/api/session/%252f'):
            with self.subTest(path=path), self.assertRaises(ValueError):
                hub_reads.read(path, {}, target='http://hub.test', session_factory=factory)
        for query in ({'force': ['1']}, {'url': ['http://other.test']}):
            with self.assertRaises(ValueError): hub_reads.read('/api/overview', query, target='http://hub.test', session_factory=factory)

    def test_redirect_error_and_wrong_shape_are_not_accepted(self):
        for status, value in ((302, {}), (503, {}), (200, [])):
            session = mock.Mock(get=mock.Mock(return_value=mock.Mock(status_code=status, json=lambda: value)))
            with self.assertRaises(ValueError):
                hub_reads.read('/api/overview', {}, target='http://hub.test', session_factory=lambda: contextlib.nullcontext(session))


class LocalHTTPTests(unittest.TestCase):
    def setUp(self):
        self.origin_patch = mock.patch.object(hub_reads, 'origin', return_value=None)
        self.origin_patch.start(); self.addCleanup(self.origin_patch.stop)
        self.http = server.ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        self.http.local_network.read = mock.Mock(return_value={'subject_id': 'A', 'host_network': {'dns': ['192.0.2.53']}})
        self.http.local_network.probe = mock.Mock(return_value={'probe_kind': 'third_party'})
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True); self.thread.start()
        self.addCleanup(self.close)
        self.origin = f'http://127.0.0.1:{self.http.server_port}'

    def close(self):
        self.http.shutdown(); self.http.server_close(); self.thread.join(3)

    def request(self, path, *, method='GET', headers=None, body=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.http.server_port, timeout=3)
        try:
            values = {'X-Agent-Monitor': 'local-network', 'Origin': self.origin}
            values.update(headers or {})
            connection.request(method, path, body=body, headers={k: v for k, v in values.items() if v is not None})
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally: connection.close()

    def test_read_and_post_admission_reject_wrong_origin_host_peer_and_method(self):
        self.assertEqual(self.request('/api/local-network')[0], 200)
        for header in ({'Origin': 'http://evil.test'}, {'Host': 'evil.test'}, {'X-Agent-Monitor': None}, {'Sec-Fetch-Site': 'cross-site'}):
            self.assertEqual(self.request('/api/local-network', headers=header)[0], 403)
        with mock.patch.object(server.Handler, '_local_network_address', return_value=False):
            self.assertEqual(self.request('/api/local-network')[0], 403)
        self.assertEqual(self.request('/api/local-network/probe')[0], 405)
        body = json.dumps({'pid': 101, 'instance': '101:123.001', 'probe_kind': 'third_party'})
        self.assertEqual(self.request('/api/local-network/probe', method='POST', headers={'Content-Type': 'application/json'}, body=body)[0], 200)
        self.assertEqual(self.request('/api/local-network/probe', method='POST', headers={'Content-Type': 'application/json', 'Origin': None}, body=body)[0], 403)
        self.http.local_network.probe.assert_called_once_with(101, '101:123.001', 'third_party')

    def test_hub_failures_never_read_local_statistics_and_unknown_actions_are_rejected(self):
        with mock.patch.object(hub_reads, 'origin', return_value='http://hub.test'), \
             mock.patch.object(hub_reads, 'read', side_effect=ValueError('offline')) as read, \
             mock.patch.object(self.http.views.overview, 'read', side_effect=AssertionError('local fallback')):
            status, result = self.request('/api/overview')
            self.assertEqual(status, 502); self.assertIn('统计服务', result['error'])
            self.assertEqual(self.request('/api/local-network')[0], 200)
            for path in ('/api/refresh', '/api/quota-recovery', '/api/account-memory/remove'):
                self.assertEqual(self.request(path, method='POST')[0], 403)
            self.assertEqual(self.request('/api/codex-accounts/send', method='POST')[0], 405)
            self.assertEqual(self.request('/api/session/%2e%2e')[0], 400)
            self.assertEqual(read.call_count, 1)

    def test_standalone_normal_read_keeps_existing_data_without_triggering_collection(self):
        route = mock.Mock(return_value={'existing_cost': 42})
        with mock.patch.dict(self.http.routes, {'/api/overview': route}):
            self.assertEqual(self.request('/api/overview?sync=1')[1]['existing_cost'], 42)
            route.assert_called_once_with({'sync': ['0']})
        self.http.local_network.read.side_effect = RuntimeError('failed')
        self.assertEqual(self.request('/api/local-network')[0], 503)


if __name__ == '__main__': unittest.main()
