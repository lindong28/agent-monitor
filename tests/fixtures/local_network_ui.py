"""Finite, synthetic normal-entry fixture. No collectors or real provider roots.

Run with --artifacts DIR --vendor DIR; stops after 15 minutes or SIGINT.
Write {"mode":"failure|hub_down|ready"} to DIR/control.json to vary responses.
"""
import argparse
import contextlib
import copy
import http.server
import json
import os
from pathlib import Path
import pwd
import sys
import threading
from unittest import mock
from urllib.parse import urlsplit, parse_qs

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
REAL_HOME = Path(pwd.getpwuid(os.getuid()).pw_dir)
PROTECTED = [(REAL_HOME / p).resolve() for p in ('.codex', '.claude', '.local/share/codex-accounts')]


def source_guard(event, args):
    if event in ('open', 'os.listdir', 'os.scandir', 'sqlite3.connect') and args and isinstance(args[0], (str, bytes, os.PathLike)):
        target = Path(os.fsdecode(args[0])).resolve()
        if any(target == root or root in target.parents for root in PROTECTED):
            raise RuntimeError('SOURCE_GUARD: real provider root rejected')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--artifacts', required=True, type=Path)
    parser.add_argument('--vendor', required=True, type=Path)
    parser.add_argument('--host-label', default='Fixture-A')
    parser.add_argument('--processes', type=int, default=1)
    args = parser.parse_args()
    args.artifacts.mkdir(parents=True, exist_ok=True)
    sys.addaudithook(source_guard)
    try:
        open(REAL_HOME / '.codex' / 'guard-positive-control')
    except RuntimeError:
        pass
    else:
        raise AssertionError('source guard failed')
    from agent_monitor import http_server as server
    from agent_monitor.views import hub_reads
    import requests
    events = args.artifacts / 'requests.jsonl'
    def record(value):
        with events.open('a') as stream:
            stream.write(json.dumps(value) + '\n')
    def mode():
        try:
            return json.loads((args.artifacts / 'control.json').read_text()).get('mode', 'ready')
        except FileNotFoundError:
            return 'ready'
    def send(handler, data, status=200):
        raw = json.dumps(data).encode()
        handler.send_response(status); handler.send_header('Content-Type', 'application/json')
        handler.send_header('Content-Length', str(len(raw))); handler.end_headers(); handler.wfile.write(raw)
    sync = dict(instance_id='fixture-hub', machines=[], all_machines=[], coverage={'admitted': 0, 'declared': 0}, syncing=False, terminal=True)
    overview = dict(today={'cost_usd': 12}, week={'cost_usd': 34}, range={'cost_usd': 56, 'total_tokens': 100},
                    rate_limits={'claude': {'accounts': []}, 'codex': {'accounts': []}},
                    daily_cost_30d=[], top_projects_week=[], model_mix_month=[], sync=sync)
    class HubHandler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_GET(self):
            url = urlsplit(self.path)
            record(dict(side='hub', method='GET', path=url.path, query=parse_qs(url.query)))
            if mode() == 'hub_down': return send(self, {'error': 'fixture Hub offline'}, 503)
            if url.path == '/api/overview': return send(self, overview)
            if url.path == '/api/timezone': return send(self, {'timezone': 'UTC'})
            if url.path == '/api/sync-status': return send(self, sync)
            return send(self, {'fixture_hub_page': True})
    hub = http.server.ThreadingHTTPServer(('127.0.0.1', 0), HubHandler)
    hub_origin = f'http://127.0.0.1:{hub.server_port}'
    class LocalHandler(server.Handler):
        def log_message(self, *args): pass
        def do_GET(self):
            path = urlsplit(self.path).path
            record(dict(side='local', method='GET', path=path))
            if path.startswith('/web/vendor/'):
                name = path.rsplit('/', 1)[-1]
                raw = (args.vendor / name).read_bytes()
                self.send_response(200); self.send_header('Content-Type', 'application/javascript')
                self.send_header('Content-Length', str(len(raw))); self.end_headers(); self.wfile.write(raw); return
            if path == '/api/health': return send(self, {'instance_id': 'fixture-local', 'stale': False})
            return super().do_GET()
        def do_POST(self):
            record(dict(side='local', method='POST', path=urlsplit(self.path).path))
            return super().do_POST()
    local = server.ThreadingHTTPServer(('127.0.0.1', 0), LocalHandler)
    local.local_network.subject_id = args.host_label
    def report():
        if mode() == 'failure': raise RuntimeError('synthetic read failure')
        data = dict(schema_version=1, subject_id=args.host_label, host=args.host_label, observed_at='2026-10-31T11:00:00Z', errors=[],
                    host_network=dict(observed_at='2026-10-31T19:00:00+08:00', timezone={'name': 'Asia/Singapore', 'offset': '+0800'},
                        addresses=[{'interface': 'en0', 'address': '192.0.2.10'}], dns=['192.0.2.53'],
                        system_proxy={'status': 'observed', 'entries': [{'protocol': 'HTTPS', 'enabled': True, 'address': 'http://127.0.0.1:8080'}]}, errors=[]),
                    processes=[dict(agent='codex', pid=101, instance='101:123', context={'cwd': '/fixture/project', 'started_at': '2026-10-31T10:00:00Z'},
                        environment={'HTTPS_PROXY': {'address': 'http://127.0.0.1:8080'}}, connections_status='observed', connections=[],
                        probe_options={kind: dict(status='available', target=target, proxy_address='http://127.0.0.1:8080') for kind, target in
                            [('third_party', 'http://ip-api.com/json/'), ('provider', 'https://chatgpt.com/cdn-cgi/trace')]} )])
        if args.processes > 1:
            template = data['processes'][0]
            data['processes'] = []
            for i in range(args.processes):
                item = copy.deepcopy(template)
                item.update(pid=101+i, instance=f'{101+i}:123', agent='claude' if i % 2 else 'codex')
                item['context']['cwd'] = '/fixture/research/very-long-project-and-worktree-directory-name/' * 2 + f'project-{i+1}'
                item['proxy_observation'] = {'status': 'observed', 'connections': [{'host': 'fixture-model.example.test', 'port': 443, 'chains': ['Fixture exit node', 'Fixture policy group'], 'observed_at': '2026-10-31T11:00:00Z'}]}
                data['processes'].append(item)
        return data
    local.local_network.read = report
    def probe(pid, instance, kind):
        return dict(instance=instance, probe_kind=kind, ip='192.0.2.20', country='Fixture Country', isp='Fixture ISP', org='Fixture Org',
                    timezone='Etc/UTC', proxy=False, hosting=True, observed_at=1793444400, target=kind, proxy_address='http://127.0.0.1:8080')
    local.local_network.probe = probe
    @contextlib.contextmanager
    def fixture_session():
        with requests.Session() as session:
            session.trust_env = False
            yield session
    threads = [threading.Thread(target=s.serve_forever, daemon=True) for s in (hub, local)]
    with mock.patch.object(hub_reads, 'origin', return_value=hub_origin), mock.patch.object(hub_reads.network_policy, 'session', fixture_session), \
         mock.patch.object(server.socket, 'gethostname', return_value=args.host_label), \
         mock.patch.object(server.COLLECTION, 'request', side_effect=AssertionError('fixture must not collect')), \
         mock.patch.object(local.views.network, 'probe', side_effect=AssertionError('fixture must not diagnose')):
        for thread in threads: thread.start()
        receipt = dict(local_origin=f'http://127.0.0.1:{local.server_port}', hub_origin=hub_origin, host=args.host_label, guard='positive_control_passed', deadline_seconds=900)
        (args.artifacts / 'fixture.json').write_text(json.dumps(receipt))
        print(json.dumps(receipt), flush=True)
        try:
            threads[1].join(900)
        finally:
            for service in (local, hub): service.shutdown(); service.server_close()
            for thread in threads: thread.join(3)


if __name__ == '__main__':
    main()
