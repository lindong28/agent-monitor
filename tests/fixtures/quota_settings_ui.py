"""Isolated full-page settings acceptance using the real settings HTTP handlers."""
import argparse
import contextlib
import json
import os
from pathlib import Path
import sys
import threading
from types import SimpleNamespace
from unittest import mock
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from local_network_ui import source_guard, REAL_HOME


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--artifacts', type=Path, required=True)
    parser.add_argument('--vendor', type=Path, required=True)
    args = parser.parse_args()
    sys.addaudithook(source_guard)
    try:
        open(REAL_HOME / '.codex/guard-positive-control')
    except RuntimeError:
        pass
    else:
        raise AssertionError('source guard failed')
    from agent_monitor import http_server as server
    from agent_monitor.views import hub_reads
    from agent_monitor.quota import quota_settings, quota_refresh
    from agent_monitor.network import account_egress
    from agent_monitor import hub_config
    registry = Path.home() / '.local/share/codex-accounts'
    def profile(name):
        folder = registry / name
        folder.mkdir(parents=True)
        (folder / 'profile.json').write_text(json.dumps({'schema_version': 1, 'name': name,
            'identity': {'account_id': 'workspace', 'email': name+'@example.test', 'subject': name}}))
        (folder / 'codex').mkdir()
    profile('alpha'); profile('beta')
    counts = {'previews': 0, 'writes': 0, 'provider_requests': 0}
    catalog = quota_settings.catalog
    observed = '2026-11-03T00:00:00+00:00'
    machine = SimpleNamespace(name='fixture-machine', is_self=True)
    sync = dict(instance_id='fixture', machines=[], coverage={'admitted': 1, 'declared': 1}, syncing=False, terminal=True)
    rows = [dict(account_state='known', account_id='workspace', account_label=name+'@example.test',
                 updated_at=observed, five_hour_used_pct=12, seven_day_used_pct=23,
                 machines=[machine.name], presence='in_use') for name in ('alpha', 'beta')]
    overview = dict(today={'cost_usd': 12}, week={'cost_usd': 34}, range={'cost_usd': 56, 'total_tokens': 100},
                    rate_limits={'claude': {'accounts': []}, 'codex': {'accounts': rows}},
                    daily_cost_30d=[], top_projects_week=[], model_mix_month=[], sync=sync)
    def preview():
        counts['previews'] += 1
        return {'checked_host': machine.name, 'targets': [dict(target=key, ip='192.0.2.1', country_code='US',
            observed_at=1793664000, retry_after_seconds=0, cached=False) for key in ('chatgpt', 'auth')]}
    class Handler(server.Handler):
        def log_message(self, *_args): pass
        def _handle_request(self, send_body=True):
            path = urlsplit(self.path).path
            values = {'/api/overview': overview, '/api/timezone': {'timezone': 'UTC'},
                      '/api/sync-status': sync, '/api/codex-accounts': {'accounts': []}, '/fixture/stats': counts}
            if path in values:
                return self._send_json(values[path], send_body=send_body)
            if path.startswith('/web/vendor/'):
                data = (args.vendor / Path(path).name).read_bytes()
                self.send_response(200); self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                if send_body: self.wfile.write(data)
                return
            return super()._handle_request(send_body=send_body)
        def do_POST(self):
            if self.path == '/fixture/stop':
                self._send_json({'ok': True})
                threading.Thread(target=self.server.shutdown, daemon=True).start()
                return
            if self.path == '/fixture/config':
                value = json.loads(self.rfile.read(int(self.headers.get('Content-Length', '0'))))
                if value.get('add'): profile(value['add'])
                if value.get('empty'): counts['empty'] = True
                self._send_json({'ok': True}); return
            if self.path == '/api/quota-settings': counts['writes'] += 1
            return super().do_POST()
    http = server.ThreadingHTTPServer(('0.0.0.0', 0), Handler)
    http.views = server.create_views()
    http.routes = server.create_routes(http.views)
    with contextlib.ExitStack() as stack:
        for patch in (mock.patch.object(hub_reads, 'origin', return_value=None),
                      mock.patch.object(quota_settings, 'PROJECT_ROOT', args.artifacts),
                      mock.patch.object(quota_settings, 'catalog', side_effect=lambda: [] if counts.get('empty') else catalog()),
                      mock.patch.object(server.sync, 'load_machine_config', return_value=SimpleNamespace(by_name={machine.name: machine})),
                      mock.patch.object(account_egress, 'preview', side_effect=preview),
                      mock.patch.object(hub_config, 'quota_interval', return_value=3600),
                      mock.patch.object(hub_config, 'managed_quota_auto_refresh', return_value=False),
                      mock.patch.object(quota_refresh, 'load_rate_limits', side_effect=AssertionError('no provider in browser fixture'))):
            stack.enter_context(patch)
        timer = threading.Timer(180, http.shutdown); timer.daemon = True; timer.start()
        print(json.dumps({'url': f'http://127.0.0.1:{http.server_port}', 'guard': 'positive_control_passed'}), flush=True)
        try: http.serve_forever()
        finally: timer.cancel(); http.server_close()


if __name__ == '__main__':
    main()
