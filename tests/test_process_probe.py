"""Probe admission, wire behavior and receipts, with no public network calls."""
import http.client
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import socket
import ssl
import subprocess
import tempfile
import threading
import unittest
from unittest import mock

from agent_monitor.network import process_probe as pp
from agent_monitor.network.client_connector import Connector


class ProcessProbeTests(unittest.TestCase):
    def backend(self, env=None):
        backend = mock.Mock()
        backend.candidates.return_value = [(12, '/bin/codex')]
        backend.identity.return_value = '123'
        backend.executable.return_value = '/bin/codex'
        backend.arguments.return_value = ['codex']
        backend.environment.return_value = env or {'HTTPS_PROXY': 'http://127.0.0.1:8000'}
        backend.connections.return_value = [{'local': '127.0.0.1:4000', 'peer': '127.0.0.1:8000'}]
        return backend

    def test_route_requires_identity_unique_proxy_and_current_socket(self):
        backend = self.backend()
        self.assertEqual(pp.route_for(12, '12:123', backend)['proxy'], 'http://127.0.0.1:8000')
        for env in ({'HTTPS_PROXY': 'http://127.0.0.1:9000'},
                    {'HTTPS_PROXY': 'http://127.0.0.1:8000', 'ALL_PROXY': 'http://127.0.0.1:9000'},
                    {'NO_PROXY': '*', 'HTTPS_PROXY': 'http://127.0.0.1:8000'},
                    {'OPENAI_BASE_URL': 'https://custom.test', 'HTTPS_PROXY': 'http://127.0.0.1:8000'},
                    {'HTTPS_PROXY': 'http://unresolved.test:8000'}):
            with self.subTest(env=env), self.assertRaises(ValueError): pp.route_for(12, '12:123', self.backend(env))
        with self.assertRaises(ValueError): pp.route_for(12, '12:999', backend)
        backend.identity.side_effect = ['123', '999']
        with self.assertRaises(ValueError): pp.route_for(12, '12:123', backend)
        secret = self.backend({'HTTPS_PROXY': 'http://name:secret@localhost:8000'})
        route = pp.route_for(12, '12:123', secret)
        self.assertEqual(route['proxy'], 'http://name:secret@127.0.0.1:8000')
        self.assertNotIn('secret', route['proxy_address'])

    def test_cache_revalidates_route_and_honors_failure_retry_after(self):
        route = {'host': 'chatgpt.com', 'proxy': 'http://private:secret@127.0.0.1:8000',
                 'proxy_address': 'http://127.0.0.1:8000', 'instance': '12:123'}
        child = mock.Mock(stdout=json.dumps({'host': 'chatgpt.com', 'ip': None, 'error': 'HTTP 429', 'retry_after_seconds': 120}))
        cache = pp.ProbeCache()
        with mock.patch.object(pp, 'route_for', return_value=route) as read, mock.patch.object(pp.subprocess, 'run', return_value=child) as run:
            first, second = cache.read(12, '12:123'), cache.read(12, '12:123')
            self.assertFalse(first['cached']); self.assertTrue(second['cached'])
            self.assertGreaterEqual(second['retry_after_seconds'], 119)
            run.assert_called_once(); self.assertEqual(read.call_count, 3)
            self.assertNotIn('secret', json.dumps(first))
            read.side_effect = ValueError('raw-secret')
            self.assertNotIn('raw-secret', json.dumps(cache.read(12, '12:123')))
            run.assert_called_once()

    def test_probe_get_does_not_request_and_post_requires_origin_one_use_ticket(self):
        connector = Connector.__new__(Connector)
        connector.server = 'http://hub.test'; connector.stopped = threading.Event()
        connector.registration = {'connection': {}}
        connector.guard = threading.Lock(); connector.process_report_lock = threading.Lock()
        connector.process_probe_tickets = {}; connector.process_probe_cache = mock.Mock()
        connector.process_probe_cache.read.return_value = {'instance': '12:123', 'ip': '8.8.8.8'}
        server = ThreadingHTTPServer(('127.0.0.1', 0), connector.pair_handler())
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close); self.addCleanup(server.shutdown)
        origin = f'http://127.0.0.1:{server.server_port}'
        def call(method, path, body=None, request_origin=origin):
            client = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=3)
            try:
                client.request(method, path, body, {'Origin': request_origin, 'Content-Type': 'application/json'})
                r = client.getresponse(); return r.status, r.read().decode()
            finally: client.close()
        status, page = call('GET', '/process-network-probe?origin=http://hub.test&nonce='+'a'*32+'&pid=12&instance=12:123')
        self.assertEqual(status, 200); connector.process_probe_cache.read.assert_not_called()
        config, _ = json.JSONDecoder().raw_decode(page.split('const c=', 1)[1])
        payload = json.dumps({'ticket': config['ticket']})
        self.assertEqual(call('POST', '/process-network-probe', payload, 'http://evil.test')[0], 403)
        connector.process_probe_cache.read.assert_not_called()
        self.assertEqual(call('POST', '/process-network-probe', payload)[0], 200)
        self.assertEqual(call('POST', '/process-network-probe', payload)[0], 403)
        connector.process_probe_cache.read.assert_called_once_with(12, '12:123', 'provider')

    def test_real_proxy_tls_wire_and_bounded_receipt(self):
        import requests
        # TLS terminates on our local synthetic proxy after CONNECT. Neither DNS
        # nor a request to the public target is performed by this fixture.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); cert, key = root/'cert.pem', root/'key.pem'
            subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1',
                            '-subj', '/CN=chatgpt.com', '-addext', 'subjectAltName=DNS:chatgpt.com',
                            '-keyout', str(key), '-out', str(cert)], capture_output=True, check=True, timeout=10)
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER); context.load_cert_chain(cert, key)
            server = socket.socket(); server.bind(('127.0.0.1', 0)); server.listen(); server.settimeout(5)
            self.addCleanup(server.close)
            observed, errors = [], []
            def headers(sock):
                raw = b''
                while not raw.endswith(b'\r\n\r\n') and len(raw) < 8192: raw += sock.recv(1)
                return raw
            def peer():
                try:
                    conn, _ = server.accept(); conn.settimeout(5)
                    with conn:
                        observed.append(headers(conn))
                        conn.sendall(b'HTTP/1.1 200 Connection Established\r\n\r\n')
                        with context.wrap_socket(conn, server_side=True) as tls:
                            observed.append(headers(tls))
                            body = b'h=chatgpt.com\nip=8.8.8.8\nloc=SG\n'
                            tls.sendall(b'HTTP/1.1 200 OK\r\nContent-Length: '+str(len(body)).encode()+b'\r\nConnection: close\r\n\r\n'+body)
                except Exception as exc: errors.append(type(exc).__name__)
            thread = threading.Thread(target=peer, daemon=True); thread.start()
            session = requests.Session(); session.verify = str(cert)
            with mock.patch.object(requests, 'Session', return_value=session):
                result = pp.request_trace({'host': 'chatgpt.com', 'proxy': f'http://name:proxy-secret@127.0.0.1:{server.getsockname()[1]}'})
            thread.join(6); self.assertFalse(thread.is_alive()); self.assertEqual(errors, [])
            self.assertEqual(result['ip'], '8.8.8.8'); self.assertEqual(result['country_code'], 'SG')
            self.assertIn(b'CONNECT chatgpt.com:443', observed[0])
            self.assertIn(b'Proxy-Authorization:', observed[0])
            self.assertIn(b'GET /cdn-cgi/trace HTTP/1.1', observed[1])
            self.assertNotIn(b'Authorization:', observed[1]); self.assertNotIn(b'Cookie:', observed[1])
            self.assertFalse(session.trust_env)


if __name__ == '__main__': unittest.main()
