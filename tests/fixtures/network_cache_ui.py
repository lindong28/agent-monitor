"""Real network HTTP/view lifecycle with synthetic probes; exits after 15 minutes."""
import argparse
import json
import subprocess
import sys
import threading
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from tests.fixtures.local_network_ui import source_guard


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--artifacts', required=True, type=Path)
    args = parser.parse_args()
    args.artifacts.mkdir(parents=True, exist_ok=True)
    sys.addaudithook(source_guard)
    from agent_monitor import http_server as server
    from agent_monitor.views.network import NetworkReport
    from tests.test_ip_check_json import PUBLIC_PROXY_OK
    # Both the subprocess boundary and source discovery are isolated.
    counts = {'server_reads': 0, 'process_reads': 0, 'pair_requests': 0}
    control = {'failure': False, 'delay': 0}
    report_data = {'timestamp': '2026-11-10T00:00:00Z', 'verdict': 'low',
                   'coverage': {'missing': []}, 'conclusions': [],
                   'local': {'ipv4': '192.0.2.10', 'dns': []},
                   'public': {**PUBLIC_PROXY_OK, 'ok': True},
                   'tz_check': {'system_tz': 'UTC', 'ip_tz': 'UTC', 'match': True}}
    clock_offset = [0]
    import time
    def run(*args, **kwargs):
        counts['server_reads'] += 1
        if control['delay']:
            threading.Event().wait(control['delay'])
        data = {'error': 'Synthetic network failure', 'verdict': 'unknown'} if control['failure'] else report_data
        with (args_path / 'probe-events.jsonl').open('a') as stream:
            stream.write(json.dumps({'probe': counts['server_reads'], 'failed': control['failure']}) + '\n')
        return subprocess.CompletedProcess([], 0, json.dumps(data), '')
    args_path = args.artifacts
    def view():
        return NetworkReport(runner=run, child_environment=lambda env: {},
            state_path=args.artifacts / 'network-report.json', hostname=lambda: 'fixture-server-device',
            clock=lambda: time.monotonic() + clock_offset[0], wall_clock=lambda: time.time() + clock_offset[0])
    class Handler(server.Handler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            path = urlsplit(self.path).path
            if path == '/fixture/stats':
                return self._send_json(counts)
            if path == '/api/runtime-context':
                return self._send_json({'host': 'fixture-server-device', 'local_network': True})
            if path == '/api/timezone':
                return self._send_json({'timezone': 'UTC'})
            if path == '/api/health':
                return self._send_json({'instance_id': 'network-cache-fixture', 'stale': False})
            if path == '/':
                self.path = '/network'
            return super().do_GET()

        def do_POST(self):
            path = urlsplit(self.path).path
            if path == '/fixture/config':
                body = json.loads(self.rfile.read(int(self.headers.get('Content-Length', 0))) or '{}')
                clock_offset[0] += body.pop('advance', 0)
                if body.pop('restart', False):
                    self.server.views.network = view()
                    self.server.routes = server.create_routes(self.server.views)
                control.update(body)
                return self._send_json({'ok': True})
            if path == '/fixture/stop':
                self._send_json({'ok': True})
                threading.Thread(target=self.server.shutdown, daemon=True).start()
                return
            return super().do_POST()
    views = server.create_views(network=view())
    http = server.ThreadingHTTPServer(('127.0.0.1', 0), Handler, views=views)
    timer = threading.Timer(900, http.shutdown)
    timer.daemon = True
    timer.start()
    print(json.dumps({'url': f'http://127.0.0.1:{http.server_port}'}), flush=True)
    try:
        http.serve_forever()
    finally:
        timer.cancel()
        http.server_close()


if __name__ == '__main__':
    main()
