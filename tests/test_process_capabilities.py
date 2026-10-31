"""Capability composition uses synthetic process/controller/proxy endpoints."""
import copy
import http.server
import json
from pathlib import Path
import socketserver
import tempfile
import threading
import time
import unittest
from unittest import mock

from agent_monitor.network import process_observer as po, process_route as pr, process_probe as pp


class CapabilityTests(unittest.TestCase):
    def sample(self):
        conn = {'local': '127.0.0.1:42000', 'peer': '127.0.0.1:7890', 'state': 'ESTABLISHED'}
        row = {'metadata': {'network': 'tcp', 'sourceIP': '127.0.0.1', 'sourcePort': '42000',
                           'inboundIP': '127.0.0.1', 'inboundPort': '7890',
                           'processPath': '/bin/codex', 'host': 'model.example', 'destinationPort': '443'},
               'chains': ['node', 'group'], 'rule': 'DomainSuffix'}
        return conn, row

    def test_association_requires_unique_complete_tuple_and_process_if_present(self):
        conn, row = self.sample()
        self.assertEqual(po.associate([row], [conn], '/bin/codex', 'now')[0]['host'], 'model.example')
        for field, value in [('sourceIP', '127.0.0.2'), ('sourcePort', '42001'), ('inboundIP', '127.0.0.2'),
                             ('inboundPort', '7891'), ('network', 'udp'), ('processPath', '/bin/other')]:
            changed = copy.deepcopy(row); changed['metadata'][field] = value
            with self.subTest(field=field):
                self.assertEqual(po.associate([changed], [conn], '/bin/codex', 'now'), [])
        del row['metadata']['inboundPort']
        self.assertEqual(po.associate([row], [conn], '/bin/codex', 'now'), [])
        conn, row = self.sample()
        self.assertEqual(po.associate([row, row], [conn], '/bin/codex', 'now'), [])

    def test_ipv6_and_unrelated_controller_content_not_exported(self):
        conn, row = self.sample()
        conn.update(local='[::1]:42000', peer='[::1]:7890')
        row['metadata'].update(sourceIP='::1', inboundIP='::1', inboundUser='secret')
        row.update(secret='never export', unrelated={'token': 'secret'})
        out = po.associate([row], [conn], '/bin/codex', 'now')
        self.assertEqual(len(out), 1); self.assertNotIn('secret', json.dumps(out))

    def test_real_unix_read_is_get_only_and_reused_once_per_scan(self):
        conn, row = self.sample(); calls = []
        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_GET(self):
                calls.append(self.path)
                body = json.dumps({'connections': [row]}).encode()
                self.send_response(200); self.send_header('Content-Length', str(len(body))); self.end_headers(); self.wfile.write(body)
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / 'controller.sock')
            server = socketserver.UnixStreamServer(path, Handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
            try:
                with mock.patch.dict('os.environ', {'AGENT_MONITOR_MIHOMO_SOCKET': path}):
                    observer = po.MihomoObserver()
                    self.assertEqual(observer.observe([conn], '/bin/codex')['status'], 'observed')
                    self.assertEqual(observer.observe([conn], '/bin/codex')['status'], 'observed')
                self.assertEqual(calls, ['/connections'])
            finally:
                server.shutdown(); server.server_close(); thread.join(2)

    def test_optional_absence_failure_and_no_matching_connection(self):
        conn, row = self.sample()
        with mock.patch.dict('os.environ', {}, clear=True), mock.patch.object(po, 'discover', return_value=[]):
            self.assertEqual(po.MihomoObserver().observe([conn], '/bin/codex')['status'], 'not_configured')
        with mock.patch.dict('os.environ', {'AGENT_MONITOR_MIHOMO_SOCKET': '/fixture'}):
            with mock.patch.object(po, 'read_connections', side_effect=PermissionError()):
                self.assertEqual(po.MihomoObserver().observe([conn], '/bin/codex')['status'], 'unavailable')
            with mock.patch.object(po, 'read_connections', return_value=[]):
                self.assertEqual(po.MihomoObserver().observe([conn], '/bin/codex')['status'], 'unmatched')

    def test_preview_and_request_share_admission_for_both_kinds(self):
        conn, _ = self.sample()
        for env, connections in [({'HTTPS_PROXY': 'http://127.0.0.1:7890'}, [conn]),
                                 ({'HTTPS_PROXY': 'http://127.0.0.1:7890'}, []),
                                 ({'HTTPS_PROXY': 'http://127.0.0.1:7890', 'NO_PROXY': 'example.test'}, [conn]),
                                 (None, [conn])]:
            options = pr.options('codex', env, connections, '12:1')
            for kind in pr.PROBE_KINDS:
                with self.subTest(env=env, kind=kind):
                    if options[kind]['status'] == 'available':
                        self.assertEqual(pr.from_snapshot('codex', env, connections, '12:1', kind)['target'], options[kind]['target'])
                    else:
                        with self.assertRaises(pr.RouteUnavailable): pr.from_snapshot('codex', env, connections, '12:1', kind)

    def test_third_party_wire_response_and_target_wide_cooldown(self):
        calls = []
        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_GET(self):
                calls.append((self.path, dict(self.headers)))
                body = json.dumps({'status': 'success', 'query': '8.8.8.8', 'countryCode': 'US',
                                   'country': 'United States', 'regionName': 'California', 'city': 'Example'}).encode()
                self.send_response(200); self.send_header('Content-Length', str(len(body)))
                self.send_header('X-Rl', '0'); self.send_header('X-Ttl', '120'); self.end_headers(); self.wfile.write(body)
        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        try:
            target = pr.target_for('codex', 'third_party')
            route = {'host': 'ip-api.com', 'target': target, 'probe_kind': 'third_party', 'instance': '12:1',
                     'proxy': f'http://127.0.0.1:{server.server_port}', 'proxy_address': f'http://127.0.0.1:{server.server_port}'}
            receipt = pp.request_trace(route)
            self.assertEqual(receipt['ip'], '8.8.8.8'); self.assertEqual(receipt['country_code'], 'US')
            self.assertEqual(calls[0][0], target); self.assertEqual(len(calls), 1)
            self.assertFalse(any(k.lower() in ('authorization', 'cookie') for k in calls[0][1]))
            cache = pp.ProbeCache()
            with mock.patch.object(pp, 'route_for', return_value=route), mock.patch.object(pp.subprocess, 'run', return_value=mock.Mock(stdout=json.dumps(receipt))) as run:
                first = cache.read(12, '12:1', 'third_party')
                self.assertGreaterEqual(first['retry_after_seconds'], 120)
                self.assertTrue(cache.read(12, '12:1', 'third_party')['cached'])
                route['instance'] = '13:2'
                blocked = cache.read(13, '13:2', 'third_party')
                self.assertIn('共享冷却', blocked['error']); run.assert_called_once()
                route.update(host='chatgpt.com', probe_kind='provider', target=pr.target_for('codex', 'provider'))
                cache.read(13, '13:2', 'provider')
                self.assertEqual(run.call_count, 2)  # third-party pause never silently becomes or blocks a provider request
        finally:
            server.shutdown(); server.server_close(); thread.join(2)

    def test_invalid_third_party_result_does_not_become_ip(self):
        for value in ({'status': 'fail'}, {'status': 'success', 'query': '127.0.0.1'}, {'status': 'success', 'query': 'invalid'}):
            with self.assertRaises((ValueError, KeyError)):
                pp.parse_public(json.dumps(value).encode())


if __name__ == '__main__': unittest.main()
