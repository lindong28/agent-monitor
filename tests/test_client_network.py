"""Controlled sockets only: reverse transport, device isolation, and admission."""
import base64
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import http.client
import json
import os
from pathlib import Path
import socket
import ssl
import subprocess
import threading
import time
import tempfile
import unittest
from unittest import mock

import account_egress
import client_connector
import client_network
import network_policy
import server
import codex_accounts
import service_runtime


class NetworkTests(unittest.TestCase):
    def test_connector_management_http_reports_boot_source_and_settings(self):
        connector, _, _ = self.connector(b'identity')
        client = http.client.HTTPConnection('127.0.0.1', connector.pair_server.server_port, timeout=3)
        self.addCleanup(client.close)
        client.request('GET', '/health')
        response = client.getresponse()
        self.assertEqual(response.status, 200)
        value = json.loads(response.read())
        self.assertEqual(value['source_root'], str(Path(client_connector.__file__).resolve().parent))
        expected = service_runtime.configured_signature(client_connector.BOOT_DEPLOYMENT_SIGNATURE,
                                                        [connector.server, str(connector.state_file)])
        self.assertEqual(value['deployment_signature'], expected)
        self.assertNotEqual(expected, service_runtime.configured_signature(client_connector.BOOT_DEPLOYMENT_SIGNATURE,
                                                                           ['http://different', str(connector.state_file)]))

    def start_http(self, handler):
        http = ThreadingHTTPServer(('127.0.0.1', 0), handler)
        thread = threading.Thread(target=http.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(http.server_close)
        self.addCleanup(http.shutdown)
        return http

    def setUp(self):
        hub = client_network.Hub()
        patch = mock.patch.object(client_network, 'hub', hub)
        patch.start()
        self.addCleanup(patch.stop)
        self.addCleanup(hub.close)
        self.hub = hub
        self.http = self.start_http(server.Handler)
        self.url = f'http://127.0.0.1:{self.http.server_port}'

    def connector(self, marker, fallback_port=None):
        seen = []
        class Peer(BaseHTTPRequestHandler):
            def log_message(self, *_args): pass
            def do_CONNECT(self):
                seen.append(self.path)
                if fallback_port:
                    self.connection.settimeout(25)
                    try: self.connection.recv(1)
                    except OSError: pass
                    return
                self.send_response(200)
                self.end_headers()
                self.wfile.flush()
                self.connection.sendall(marker)
                self.connection.settimeout(4)
                try:
                    while data := self.connection.recv(16384):
                        self.connection.sendall(data)
                except OSError:
                    pass
        peer = self.start_http(Peer)
        command = ['node', str(Path(__file__).parent / 'fixtures/client_network_bridge.cjs'), str(peer.server_port)]
        if fallback_port: command.append(str(fallback_port))
        proc = subprocess.Popen(command,
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        def stop():
            proc.stdin.close()
            try: proc.wait(timeout=3)
            except subprocess.TimeoutExpired: proc.kill(); proc.wait()
            proc.stdout.close(); proc.stderr.close()
        self.addCleanup(stop)
        # The child reports readiness, or the test deadline below stops it.
        import select
        self.assertTrue(select.select([proc.stdout], [], [], 4)[0], 'fixture bridge startup timed out')
        port = json.loads(proc.stdout.readline())['port']
        route = f'http://agent-monitor:fixture-local-capability@127.0.0.1:{port}'
        connector = client_connector.Connector(self.url, marker.decode(), route, pair_port=0)
        failures = []
        def run():
            try: connector.run()
            except client_network.NetworkError as exc: failures.append(type(exc).__name__)
        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        def close():
            connector.close()
            thread.join(25)
            self.assertFalse(thread.is_alive())
        self.addCleanup(close)
        remote = self.hub.browser_route(connector.registration['connection'])
        return connector, remote, seen

    def test_two_device_routes_carry_bytes_and_disconnect_never_falls_back(self):
        clients = [self.connector(label) for label in (b'device-A', b'device-B')]
        def exchange(index):
            _, route, seen = clients[index]
            sock = client_connector.connect_via(route.endpoint, 'controlled.test:8443', scope='a' * 32)
            with sock:
                marker = sock.recv(8)
                data = bytes(range(256)) * 1024
                # Full duplex, larger than relay buffers.
                sender = threading.Thread(target=sock.sendall, args=(data,))
                sender.start()
                received = bytearray()
                while len(received) < len(data):
                    received.extend(sock.recv(32768))
                sender.join(3)
                self.assertEqual(bytes(received), data)
                self.assertEqual(marker, (b'device-A', b'device-B')[index])
            self.assertEqual(seen, ['controlled.test:8443'])
            deadline = time.monotonic() + 2
            facts = network_policy.observations(route.endpoint)
            while not facts.get('events') and time.monotonic() < deadline:
                time.sleep(.01)
                facts = network_policy.observations(route.endpoint)
            self.assertTrue(any(e['target'] == 'https://controlled.test:8443' and e['result'] == 'connected' for e in facts['events']))
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(exchange, range(2)))
        _, first, _ = clients[0]
        endpoint = first.endpoint
        first.close()
        with self.assertRaises(client_network.NetworkError):
            first.require()
        with self.assertRaises(OSError):
            client_connector.connect_via(endpoint, 'controlled.test:8443')
        clients[1][1].require()

    def test_candidate_fallback_can_outlast_both_old_transport_limits(self):
        class Target(BaseHTTPRequestHandler):
            def log_message(self, *_args): pass
            def do_GET(self):
                self.send_response(200); self.end_headers(); self.wfile.write(b'local target')
        target = self.start_http(Target)
        _, route, seen = self.connector(b'fallback', target.server_port)
        with client_connector.connect_via(route.endpoint, f'127.0.0.1:{target.server_port}') as sock:
            sock.sendall(b'GET / HTTP/1.1\r\nHost: fixture\r\n\r\n')
            response = http.client.HTTPResponse(sock); response.begin()
            self.assertEqual(response.read(), b'local target')
        self.assertEqual(len(seen), 2)

    def test_diagnostic_report_does_not_delay_business_channel(self):
        connector, route, _ = self.connector(b'device-A')
        original = connector.request
        release, entered = threading.Event(), threading.Event()
        def delayed(action, data=None):
            if action == 'report':
                entered.set(); release.wait(5)
            return original(action, data)
        with mock.patch.object(connector, 'request', side_effect=delayed):
            try:
                started = time.monotonic()
                with client_connector.connect_via(route.endpoint, 'controlled.test:8443') as sock:
                    self.assertEqual(sock.recv(8), b'device-A')
                self.assertLess(time.monotonic() - started, 2)
                self.assertTrue(entered.wait(2))
            finally: release.set()

    def test_browser_capability_cannot_control_or_open_tunnels(self):
        route = self.hub.register('device')
        for path, method in [('next', 'POST'), ('channel', 'CONNECT'), ('close', 'POST')]:
            connection = http.client.HTTPConnection('127.0.0.1', self.http.server_port, timeout=3)
            connection.request(method, '/api/client-network/' + path,
                               headers={'X-Connection-ID': route.id, 'Authorization': 'Bearer ' + route.browser})
            response = connection.getresponse()
            self.assertEqual(response.status, 403)
            response.read(); connection.close()
        connection = http.client.HTTPConnection('127.0.0.1', self.http.server_port, timeout=3)
        connection.request('POST', '/api/client-network/register', '{}',
                           {'Origin': self.url, 'X-Agent-Monitor': 'connector', 'Content-Type': 'application/json'})
        response = connection.getresponse()
        self.assertEqual(response.status, 403)
        response.read(); connection.close()

    def test_https_hub_and_end_to_end_provider_tls(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        certificate, key = (str(Path(temporary.name) / name) for name in ('cert.pem', 'key.pem'))
        subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1',
                        '-subj', '/CN=127.0.0.1', '-addext', 'subjectAltName=IP:127.0.0.1',
                        '-keyout', key, '-out', certificate], check=True, capture_output=True, timeout=10)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(certificate, key)
        self.http.socket = context.wrap_socket(self.http.socket, server_side=True)
        self.url = self.url.replace('http:', 'https:', 1)
        env = mock.patch.dict(os.environ, {'SSL_CERT_FILE': certificate, 'REQUESTS_CA_BUNDLE': certificate})
        env.start(); self.addCleanup(env.stop)
        connector, route, _ = self.connector(b'device-C', fallback_port=self.http.server_port)
        expected = bytes(range(256)) * 1024
        seen = []
        class Target(BaseHTTPRequestHandler):
            def log_message(self, *_args): pass
            def do_GET(self):
                seen.append(self.headers.get('Proxy-Authorization'))
                self.send_response(200)
                self.send_header('Content-Length', str(len(expected)))
                self.end_headers()
                self.wfile.write(expected)
        target = self.start_http(Target)
        target.socket = context.wrap_socket(target.socket, server_side=True)
        raw = client_connector.connect_via(route.endpoint, f'127.0.0.1:{target.server_port}')
        with ssl.create_default_context(cafile=certificate).wrap_socket(raw, server_hostname='127.0.0.1') as secure:
            secure.sendall(b'GET / HTTP/1.1\r\nHost: fixture\r\nConnection: close\r\n\r\n')
            response = http.client.HTTPResponse(secure)
            response.begin()
            self.assertEqual(response.status, 200)
            self.assertEqual(response.read(), expected)
        self.assertEqual(seen, [None])
        raw = client_connector.connect_via(route.endpoint, f'127.0.0.1:{target.server_port}')
        # Verification remains at the original client; wrong hostname fails.
        with self.assertRaises(ssl.SSLCertVerificationError):
            ssl.create_default_context(cafile=certificate).wrap_socket(raw, server_hostname='wrong.example')
        raw.close()

    def test_route_cache_and_context_do_not_cross_devices(self):
        a, b = self.hub.register('a'), self.hub.register('b')
        calls = []
        def probe(host, env):
            calls.append(env[network_policy.INTERNAL])
            return account_egress.unknown(host, 'controlled unknown')
        with mock.patch.object(account_egress, '_run_probe', side_effect=probe):
            for route in (a, b, a):
                self.assertEqual(account_egress.preview(route)['checked_host'], route.label)
        self.assertCountEqual(calls, [a.endpoint] * 2 + [b.endpoint] * 2)
        with mock.patch.dict(os.environ, {network_policy.INTERNAL: 'http://agent-monitor:server@127.0.0.1:1234'}):
            with network_policy.using_route(a.endpoint):
                self.assertEqual(network_policy.child_environment({})[network_policy.INTERNAL], a.endpoint)
            self.assertIn(':server@', network_policy.endpoint())

    def test_receipts_bind_route_selection_cas_expiry_and_replay(self):
        a, b = self.hub.register('a'), self.hub.register('b')
        token = a.issue(['one', 'two'], 'old')
        with self.assertRaises(client_network.NetworkError): b.consume(token, ['one', 'two'], 'old')
        with self.assertRaises(client_network.NetworkError): a.consume(token, ['one'], 'old')
        token = a.issue(['one'], 'old')
        with self.assertRaises(client_network.NetworkError): a.consume(token, ['one'], 'new')
        token = a.issue(['one'], None)
        a.consume(token, ['one'], None)
        with self.assertRaises(client_network.NetworkError): a.consume(token, ['one'], None)
        token = a.issue(['one'], None)
        a.receipts[token] = (0, frozenset(['one']), None)
        with self.assertRaises(client_network.NetworkError): a.consume(token, ['one'], None)

    def test_account_rpc_uses_bound_device_for_initial_and_recovery_peers(self):
        route = self.hub.register('device')
        calls = []
        def factory(directory, cancelled):
            calls.append(network_policy.child_environment({})[network_policy.INTERNAL])
            return object()
        with tempfile.TemporaryDirectory() as directory:
            manager = codex_accounts.Manager(Path(directory) / 'profiles', rpc_factory=factory)
            manager.start_interval = 0
            job = {'network_route': route, 'cancelled': threading.Event()}
            for _ in range(2): manager.open_rpc(Path(directory), job)
            self.assertEqual(calls, [route.endpoint, route.endpoint])
            route.close()
            with self.assertRaises(codex_accounts.ActionError): manager.open_rpc(Path(directory), job)
            self.assertEqual(len(calls), 2)


if __name__ == '__main__': unittest.main()
