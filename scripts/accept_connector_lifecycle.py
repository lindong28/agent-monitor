#!/usr/bin/env python3
"""Explicit macOS acceptance with an isolated launchd job and loopback transport."""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import plistlib
import select
import signal
import socket
import socketserver
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def until(name, predicate, seconds):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(.1)
    raise AssertionError(name + ' did not complete within the test observation budget')


def worker(config_path):
    """Replace only resolver and pair port; execute the actual foreground entry."""
    config = json.loads(Path(config_path).read_text())
    Path(config_path).with_name(f"worker-{os.getpid()}.pid").write_text(str(os.getpid()))
    from agent_monitor.network import client_connector
    from agent_monitor.network import network_policy
    assert config['server'].startswith('http://127.0.0.1:')
    original_connector = client_connector.Connector

    class Runtime:
        def __init__(self):
            self.child = subprocess.Popen([config['node'], str(ROOT / 'tests/fixtures/client_network_bridge.cjs'), '0'],
                                          stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            Path(config_path).with_name(f"bridge-{self.child.pid}.pid").write_text(str(self.child.pid))
            try:
                if not select.select([self.child.stdout], [], [], 5)[0]:
                    raise RuntimeError('controlled bridge startup timed out')
                port = json.loads(self.child.stdout.readline())['port']
                self.endpoint = f'http://agent-monitor:fixture-local-capability@127.0.0.1:{port}'
            except BaseException:
                self.close()
                raise

        def close(self):
            self.child.stdin.close()
            try:
                self.child.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.child.kill()
                self.child.wait(timeout=3)
            self.child.stdout.close()

    class Connector(original_connector):
        def __init__(self, *args, **kwargs):
            kwargs['pair_port'] = 0
            super().__init__(*args, **kwargs)
            from agent_monitor.runtime.connector_service import atomic_json
            atomic_json(config['ready'], {'pid': os.getpid(), 'port': self.pair_server.server_port})

    network_policy._Runtime = Runtime
    client_connector.Connector = Connector
    sys.argv = ['agent_monitor/network/client_connector.py', '--server', config['server'], '--state-file', config['state']]
    return client_connector.main()


class InjectedFailure(Exception):
    pass


def accept(*, fail_after_ready=False):
    if sys.platform != 'darwin':
        raise RuntimeError('--launchd requires a macOS desktop login; no test job created')
    import shutil
    node = shutil.which('node')
    if not node:
        raise RuntimeError('Node is required for the controlled bridge')
    root = Path(tempfile.mkdtemp(prefix='monitor-launchd-'))
    os.environ['HOME'] = str(root)
    from agent_monitor.network import client_network
    from agent_monitor.network import client_connector
    from agent_monitor.runtime import connector_service
    label = 'com.agent-monitor.acceptance.' + uuid.uuid4().hex
    target = f'gui/{os.getuid()}/{label}'
    plist = root / 'fixture.plist'
    ready = root / 'ready.json'
    receipt = root / 'status.json'
    seen_ports = set()
    created = False
    cleanup_ok = False

    def launch(*args):
        return subprocess.run(['launchctl', *args], capture_output=True, timeout=15)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def _send_json(self, value, status=200):
            body = json.dumps(value).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            if not client_network.handle(self):
                self.send_error(404)

        do_CONNECT = do_POST

    class Echo(socketserver.BaseRequestHandler):
        def handle(self):
            self.request.settimeout(5)
            try:
                while data := self.request.recv(32768):
                    self.request.sendall(data)
            except OSError:
                pass

    class EchoServer(socketserver.ThreadingTCPServer):
        daemon_threads = True

    hub = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    echo = EchoServer(('127.0.0.1', 0), Echo)
    for server in (hub, echo):
        threading.Thread(target=server.serve_forever, daemon=True).start()
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def live(previous=None):
        try:
            value = json.loads(ready.read_text())
            seen_ports.add(value['port'])
            with opener.open(f'http://127.0.0.1:{value["port"]}/health', timeout=1) as response:
                health = json.load(response)
            return health if (health['pid'] == value['pid'] and health['pid'] != previous
                              and health['source_root'] == str(ROOT) and health['connected']) else None
        except (OSError, ValueError, KeyError):
            return None

    def processes_closed():
        # Workers record identity before importing the connector; each restart and
        # bridge gets a separate record, so the latest ready.json cannot hide it.
        records = list(root.glob('worker-*.pid')) + list(root.glob('bridge-*.pid'))
        if created and not records:
            raise RuntimeError('No worker identity observed; cleanup remains unverified')
        for record in records:
            pid = int(record.read_text())
            try:
                os.kill(pid, 0)
                return False
            except ProcessLookupError:
                pass
        return True

    def closed():
        for port in seen_ports:
            try:
                with socket.create_connection(('127.0.0.1', port), timeout=.2):
                    return False
            except ConnectionRefusedError:
                pass
        return True

    def alarm(*_):
        raise TimeoutError('Lifecycle acceptance exceeded its 120 second test budget')

    previous_alarm = signal.signal(signal.SIGALRM, alarm)
    previous_term = signal.signal(signal.SIGTERM, alarm)
    signal.alarm(120)
    try:
        if launch('print', target).returncode == 0:
            raise RuntimeError('Test job already exists; it was not adopted or changed')
        config = root / 'worker.json'
        server_url = f'http://127.0.0.1:{hub.server_port}'
        config.write_text(json.dumps(dict(server=server_url, node=node, ready=str(ready), state=str(receipt))))
        definition = connector_service.definition(server_url, receipt)
        definition['Label'] = label
        definition['ProgramArguments'] = [sys.executable, str(Path(__file__).resolve()), '--worker', str(config)]
        definition['EnvironmentVariables']['HOME'] = str(root)
        plist.write_bytes(plistlib.dumps(definition))
        # Ownership is established before bootstrap; even a partial failure gets cleanup.
        created = True
        assert launch('bootstrap', f'gui/{os.getuid()}', str(plist)).returncode == 0, 'test job bootstrap rejected'
        assert launch('print', target).returncode == 0, 'test job not loaded'
        first = until('connector readiness', live, 20)
        print('PASS: isolated LaunchAgent loaded and connector health reports connected.', flush=True)
        if fail_after_ready:
            raise InjectedFailure('intentional failure after readiness')
        with client_network.hub.guard:
            routes = list(client_network.hub.routes.values())
        assert len(routes) == 1
        data = bytes(range(256)) * 1024
        with client_connector.connect_via(routes[0].endpoint, f'127.0.0.1:{echo.server_address[1]}') as tunnel:
            tunnel.settimeout(5)
            sender = threading.Thread(target=tunnel.sendall, args=(data,), daemon=True)
            sender.start()
            received = bytearray()
            while len(received) < len(data):
                part = tunnel.recv(32768)
                assert part, 'controlled stream ended early'
                received.extend(part)
            sender.join(5)
            assert not sender.is_alive() and bytes(received) == data
        print('PASS: 262144 bytes returned through the real connector and controlled loopback route.', flush=True)
        assert launch('kill', 'SIGTERM', target).returncode == 0, 'test termination rejected'
        second = until('KeepAlive replacement readiness', lambda: live(first['pid']), 60)
        assert second['instance_id'] != first['instance_id']
        print('PASS: KeepAlive started a different connected process.', flush=True)
        assert launch('bootout', target).returncode == 0, 'test job unload rejected'
        print('PASS: launchd accepted test job unload; listener closure is checked separately.', flush=True)
        until('job absent', lambda: launch('print', target).returncode != 0, 5)
        until('listener closed', closed, 30)
        until('owned processes exited', processes_closed, 10)
        print('PASS: test job is absent, recorded workers/bridges exited, and both observed listener ports reject connections.', flush=True)
    finally:
        signal.alarm(0)
        # Closing the controlled hub releases a pending long poll during shutdown.
        if created and launch('print', target).returncode == 0:
            launch('bootout', target)
        client_network.hub.close()
        for server in (hub, echo):
            server.shutdown()
            server.server_close()
        try:
            until('cleanup job absent', lambda: launch('print', target).returncode != 0, 5)
            if ready.exists():
                seen_ports.add(json.loads(ready.read_text())['port'])
            until('cleanup listeners closed', closed, 10)
            until('cleanup processes exited', processes_closed, 10)
            cleanup_ok = True
        finally:
            signal.signal(signal.SIGALRM, previous_alarm)
            signal.signal(signal.SIGTERM, previous_term)
            if cleanup_ok:
                shutil.rmtree(root)
                print('PASS: isolated job and temporary files cleaned; formal connector was not managed.', flush=True)
            else:
                print(f'Cleanup incomplete: inspect {target}; evidence retained at {root}', file=sys.stderr)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--worker', help=argparse.SUPPRESS)
    args = parser.parse_args()
    try:
        if args.worker:
            raise SystemExit(worker(args.worker))
        accept()
        try:
            accept(fail_after_ready=True)
        except InjectedFailure:
            print('PASS: an injected failure after readiness also cleaned its job, listeners and files.')
    except Exception as exc:
        print(f'FAIL: {exc}', file=sys.stderr)
        raise SystemExit(1)
