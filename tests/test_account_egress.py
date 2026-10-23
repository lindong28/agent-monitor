import subprocess
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest import mock

import requests
import account_egress as egress


class EgressTests(unittest.TestCase):
    def setUp(self):
        egress._cache.clear()
        self.addCleanup(egress._cache.clear)

    def test_trace_keeps_target_ip_and_country_separate(self):
        for ip, country in [('1.1.1.1', 'US'), ('8.8.8.8', 'CN'), ('2606:4700:4700::1111', 'SG')]:
            with self.subTest(ip=ip):
                result = egress.parse_trace(f'h=chatgpt.com\nip={ip}\nloc={country}\n'.encode(), 'chatgpt.com')
                self.assertEqual((result['ip'], result['country_code']), (ip, country))
        for body in [b'<html>challenge</html>', b'h=other.example\nip=1.1.1.1\nloc=CN',
                     b'h=chatgpt.com\nip=127.0.0.1\nloc=CN', b'h=chatgpt.com\nip=broken\nloc=CN',
                     b'h=chatgpt.com\nip=1.1.1.1\nip=8.8.8.8\nloc=CN']:
            with self.assertRaises((ValueError, KeyError)):
                egress.parse_trace(body, 'chatgpt.com')
        self.assertIsNone(egress.parse_trace(b'h=chatgpt.com\nip=1.1.1.1\nloc=XX', 'chatgpt.com')['country_code'])

    def test_probe_uses_shared_policy_without_credentials_or_redirects(self):
        response = mock.MagicMock(status_code=200, headers={})
        response.raw.read.return_value = b'h=chatgpt.com\nip=1.1.1.1\nloc=CN\n'
        response.__enter__.return_value = response
        client = mock.MagicMock()
        client.get.return_value = response
        client.__enter__.return_value = client
        with mock.patch.object(egress.network_policy, 'session', return_value=client):
            result = egress.probe('chatgpt.com')
        self.assertEqual(result['country_code'], 'CN')
        args, kw = client.get.call_args
        self.assertEqual(args, ('https://chatgpt.com/cdn-cgi/trace',))
        self.assertFalse(kw['allow_redirects'])
        self.assertEqual(kw['headers'], {'Accept-Encoding': 'identity'})
        self.assertNotIn('auth', kw)
        self.assertEqual(client.get.call_count, 1)

    def test_http_refusal_retry_after_and_timeout_remain_unknown(self):
        for code, delay in [(302, '75'), (429, '120'), (403, 'invalid')]:
            response = mock.MagicMock(status_code=code, headers={'Retry-After': delay})
            response.__enter__.return_value = response
            client = mock.MagicMock(); client.__enter__.return_value = client; client.get.return_value = response
            with mock.patch.object(egress.network_policy, 'session', return_value=client):
                result = egress.probe('chatgpt.com')
            self.assertIsNone(result['ip']); self.assertIn(str(code), result['error'])
            self.assertEqual(result.get('retry_after_seconds'), int(delay) if delay.isdecimal() else None)
            response.raw.read.assert_not_called()
        client.get.side_effect = requests.Timeout('SECRET')
        with mock.patch.object(egress.network_policy, 'session', return_value=client):
            self.assertNotIn('SECRET', str(egress.probe('chatgpt.com')))

    def test_child_budget_and_internal_failure_do_not_leak(self):
        with mock.patch.object(egress.subprocess, 'run', side_effect=subprocess.TimeoutExpired('SECRET', 15)) as run:
            result = egress._run_probe('chatgpt.com', {'SAFE': 'env'})
        self.assertIn('15秒', result['error']); self.assertNotIn('SECRET', str(result))
        self.assertEqual(run.call_args.kwargs['timeout'], 15)

    def test_concurrent_previews_share_success_and_failure_cache(self):
        calls = []
        def probe(host, env):
            calls.append(host); time.sleep(.02)
            result = egress.unknown(host, 'fixture failure')
            if host == 'chatgpt.com':
                result.update(ip='1.1.1.1', country_code='US', error=None)
            else:
                result['retry_after_seconds'] = 120
            return result
        with mock.patch.object(egress.network_policy, 'child_environment', return_value={}), \
                mock.patch.object(egress, '_run_probe', side_effect=probe):
            with ThreadPoolExecutor(max_workers=2) as pool:
                reports = list(pool.map(lambda _: egress.preview(), range(2)))
            self.assertCountEqual(calls, list(egress.TARGETS))
            self.assertEqual(reports[0]['targets'][0]['observed_at'], reports[1]['targets'][0]['observed_at'])
            self.assertCountEqual([all(x['cached'] for x in r['targets']) for r in reports], [False, True])
            self.assertIsNone(reports[1]['targets'][1]['ip'])
            deadline, data = egress._cache['chatgpt.com']
            egress._cache['chatgpt.com'] = (time.monotonic() - 1, data)
            report = egress.preview()
            self.assertEqual(calls.count('chatgpt.com'), 2)
            self.assertEqual(calls.count('auth.openai.com'), 1)
            self.assertTrue(report['targets'][1]['cached'])
            self.assertGreater(report['targets'][1]['retry_after_seconds'], 60)
