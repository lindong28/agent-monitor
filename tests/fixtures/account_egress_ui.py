"""Six synthetic accounts and controlled exit results; no provider imports/I/O."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
import time
import sys
import socket
from urllib.parse import urlsplit, parse_qs
import argparse

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from agent_monitor.network import client_network
from agent_monitor.network.client_connector import Connector


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--loopback', action='store_true', help='Use a loopback page origin for automated acceptance.')
    args = parser.parse_args()
    accounts = [dict(id=str(i), email=f'fixture{i}@example.test', account_id=f'workspace-{i}',
                     eligible=True, busy=False, has_credentials=True) for i in range(6)]
    state = dict(mode='mixed', delay=0, starts=[], previews=0, paused=False,
                 pair_mode='ready', status_mode='ready', pair_requests=0, status_requests=0,
                 pause_record='matched', probe_used=False, recovery_mode='success', recoveries=[], resumes=[], restricted=False, recovery_reads=0, last_recovery=None, pause_id='fixture-pause')
    def pause():
        return dict(id=state['pause_id'], at=1793004570, failure=dict(detail='Synthetic RPC failure', stage='quota_read', rpc_code=-32603), **({'probe_attempt':dict(id='used')} if state['probe_used'] else {}))
    def quota():
        target = dict(machine='fixture-hub', account_id='workspace-0', account_label='fixture0@example.test',
                      reason='本机已暂停该服务的自动请求', query_state='paused', pause=pause())
        if state['probe_used']:
            target['pause']['probe_attempt'] = dict(id='used')
        if state['pause_record'] == 'stale':
            target['pause']['id'] = 'old-pause'
        if state['pause_record'] == 'incomplete':
            target.pop('account_id')
        other = dict(machine='fixture-other', account_id='other', account_label='other@example.test',
                     reason='本机已暂停该服务的自动请求', query_state='paused', pause=dict(id='other-pause'))
        failures = [other, target] if state['paused'] else []
        if state['pause_record'] == 'duplicate' and state['paused']:
            failures.append(dict(target))
        return dict(codex=dict(accounts=[], refresh_errors=failures), claude=dict(accounts=[]))
    source = (ROOT / 'web/index.html').read_text()
    panel = source.split('<details id="codex-account-actions"', 1)[1].split('</section>', 1)[0]
    html = ('<!doctype html><html lang="zh"><meta charset="utf-8"><meta name="viewport" content="width=device-width">'
            '<link rel="stylesheet" href="/web/styles.css"><body><main><h1>出口确认隔离夹具</h1>'
            '<details id="codex-account-actions" open' + panel + '''
            <section><h2>配额记录</h2><table id="quota-accounts"></table></section></main><script>
            window.AgentMonitor={pageScope:()=>()=>true,ensureTimezone:async()=>{},
              formatDate:x=>new Date(x).toLocaleString(),onPageCleanup:()=>{},pageInterval:()=>{},
              latestCodexQuotaReading:()=>null};
            </script><script src="/web/quota.js"></script><script type="module">
            import {init} from '/web/codex-accounts.js';
            const quota=window.AgentMonitorModules.quota({platform:{window,document,fetch:(...args)=>fetch(...args)},
              qs:s=>document.querySelector(s),fmtAbs:String,formatDate:String,updateSyncOverviewLink:()=>{}});
            Object.assign(window.AgentMonitor,quota);
            init();
            fetch('/fixture/quota').then(r=>r.json()).then(q=>quota.renderQuotaAccounts(q));
            </script>''')

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def send(self, data, kind='application/json', status=200):
            body = json.dumps(data).encode() if kind == 'application/json' else data
            self.send_response(status)
            self.send_header('Content-Type', kind)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path == '/':
                self.send(html.encode(), 'text/html; charset=utf-8')
            elif self.path in ('/web/styles.css', '/web/codex-accounts.js', '/web/account-egress.js', '/web/quota.js'):
                body = (ROOT / self.path.lstrip('/')).read_bytes()
                if self.path.endswith('/codex-accounts.js'):
                    body = body.replace(b'127.0.0.1:39002', f'127.0.0.1:{pair.server_port}'.encode())
                self.send(body, 'text/css' if self.path.endswith('.css') else 'text/javascript')
            elif self.path == '/api/codex-accounts':
                visible = [dict(a, restrictions={'unknown': True} if state['restricted'] and a['id'] == '0' else {}) for a in accounts]
                self.send(dict(accounts=visible, batch=dict(id='fixture-old', items=[], created_at='2026-10-23T01:00:00Z'),
                               upstream_pause=pause() if state['paused'] else None))
            elif self.path.startswith('/api/quota-recovery?'):
                state['recovery_reads'] += 1
                self.send(dict(machine='fixture-hub', source_host_identity='fixture-host', observed_at='2026-10-28T00:00:00Z',
                    pause=pause() if state['paused'] else None, active=None,
                    account=dict(account_id='workspace-0', account_label='fixture0@example.test', source='default', credential_path='/fixture/.codex/auth.json'),
                    last_recovery=state['last_recovery']))
            elif self.path == '/fixture/quota':
                self.send(quota())
            elif self.path == '/fixture/stats':
                self.send(state)
            else:
                self.send({}, status=404)

        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers.get('Content-Length', '0'))) or '{}')
            if self.path == '/api/codex-accounts/egress-preview':
                try:
                    route = client_network.hub.browser_route(payload.get('connection'))
                except client_network.NetworkError as exc:
                    self.send(dict(error=str(exc)), status=exc.status)
                    return
                state['previews'] += 1
                time.sleep(state['delay'])
                if state['mode'] == 'failure':
                    self.send(dict(error='合成连接失败'), status=503)
                    return
                targets = [dict(host=host, ip=ip, country_code=country, observed_at=time.time(),
                                cached=False, retry_after_seconds=60, error=None) for host, ip, country in
                           [('chatgpt.com', '8.8.8.8', 'CN'), ('auth.openai.com', '1.1.1.1', 'US')]]
                if state['mode'] == 'partial':
                    targets[1].update(ip=None, country_code=None, error='合成超时')
                self.send(dict(checked_host='fixture-browser-device', targets=targets,
                               confirmation=route.issue(payload.get('profile_ids', []), payload.get('expected_batch_id'))))
            elif self.path == '/api/codex-accounts/network-status':
                state['status_requests'] += 1
                if state['status_mode'] == 'failure':
                    self.send(dict(error='合成本机连接失效'), status=409)
                    return
                try:
                    route = client_network.hub.browser_route(payload.get('connection'))
                    self.send(dict(label=route.label, connected=True))
                except client_network.NetworkError as exc:
                    self.send(dict(error=str(exc)), status=exc.status)
            elif self.path == '/api/codex-accounts/batch-start':
                route = client_network.hub.browser_route(payload.get('connection'))
                route.consume(payload.get('confirmation'), payload.get('profile_ids'), payload.get('expected_batch_id'))
                state['starts'].append(payload)
                self.send({})
            elif self.path == '/fixture/config':
                for key in ('mode', 'delay', 'paused', 'pair_mode', 'status_mode', 'pause_record', 'probe_used', 'recovery_mode', 'restricted'):
                    if key in payload:
                        state[key] = payload[key]
                self.send(state)
            elif self.path == '/api/quota-recovery':
                state['recoveries'].append(payload)
                state['pause_id'] = 'pause-after-' + str(len(state['recoveries']))
                receipt = dict(ok=state['recovery_mode']=='success', request=payload,
                    observed_at='2026-10-28T00:00:01Z', pause_id=state['pause_id'],
                    failure=dict(stage='quota_read', rpc_code=-32603, detail='Synthetic recovery failure'),
                    network=dict(policy='chromium-system', coverage='recent_connections_only', events=[
                        dict(target='https://chatgpt.com', route='PROXY 127.0.0.1:59527', result='connected')]))
                state['last_recovery'] = receipt
                if state['recovery_mode'] == 'unknown':
                    self.send(dict(ok=False, error='恢复回执未取得，执行结果未知；勿重复探测。'), status=409)
                elif state['recovery_mode'] == 'failure':
                    self.send(receipt, status=409)
                else:
                    state['paused'] = False
                    self.send(receipt)
            elif self.path == '/api/codex-accounts/resume':
                state['resumes'].append(payload)
                state['restricted'] = False
                self.send({})
            elif self.path == '/fixture/stop':
                self.send({})
                threading.Thread(target=http.shutdown, daemon=True).start()
            else:
                self.send({}, status=404)

    http = ThreadingHTTPServer(('0.0.0.0', 0), Handler)
    host = '127.0.0.1' if args.loopback else socket.gethostname().split('.')[0]
    origin = f'http://{host}:{http.server_port}'
    route = client_network.hub.register('fixture-browser-device')
    connector = Connector.__new__(Connector)
    connector.server, connector.stopped = origin, threading.Event()
    connector.registration = {'connection': {'id': route.id, 'capability': route.browser, 'label': route.label}}
    class Pair(connector.pair_handler()):
        def do_GET(self):
            state['pair_requests'] += 1
            if state['pair_mode'] == 'ready':
                return super().do_GET()
            if state['pair_mode'] == 'error':
                nonce = parse_qs(urlsplit(self.path).query).get('nonce', [''])[0]
                payload = json.dumps(dict(type='agent-monitor-network', nonce=nonce,
                                          error='合成连接端尚未连接服务器'))
                body = (f'<script>window.opener.postMessage({payload},{json.dumps(origin)});window.close()</script>').encode()
            else:
                body = '<p>合成本机配对无响应；用于超时和取消验收。</p>'.encode()
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
    pair = ThreadingHTTPServer(('127.0.0.1', 0), Pair)
    threading.Thread(target=pair.serve_forever, daemon=True).start()
    def heartbeat():
        while not connector.stopped.wait(1): route.last_seen = time.monotonic()
    threading.Thread(target=heartbeat, daemon=True).start()
    timer = threading.Timer(600, http.shutdown)
    timer.daemon = True
    timer.start()
    print(json.dumps(dict(url=origin, pair_port=pair.server_port, lifetime_seconds=600)), flush=True)
    try:
        http.serve_forever()
    finally:
        timer.cancel()
        http.server_close()
        connector.stopped.set()
        pair.shutdown(); pair.server_close(); route.close()


if __name__ == '__main__':
    main()
