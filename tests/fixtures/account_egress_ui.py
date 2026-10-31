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
import os
import pwd
import re

# Reject accidental discovery of the operator provider roots, including aliases.
_REAL_HOME = Path(pwd.getpwuid(os.getuid()).pw_dir)
_PROTECTED = [(_REAL_HOME / p).resolve() for p in (".codex", ".claude", ".local/share/codex-accounts")]
def source_guard(event, args):
    if event in ("open", "os.listdir", "os.scandir", "sqlite3.connect") and args and isinstance(args[0], (str, bytes, os.PathLike)):
        target = Path(os.fsdecode(args[0])).resolve()
        if any(target == root or root in target.parents for root in _PROTECTED):
            raise RuntimeError("SOURCE_GUARD: real provider root rejected")
sys.addaudithook(source_guard)
try:
    open(_REAL_HOME / ".codex" / "guard-positive-control")
except RuntimeError as exc:
    assert str(exc).startswith("SOURCE_GUARD:")
else:
    raise AssertionError("source guard did not reject real root")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from agent_monitor.network import client_network
from agent_monitor.network.client_connector import Connector


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--facade', action='store_true', help='Exercise real same-origin forwarding to the synthetic owner.')
    parser.add_argument('--loopback', action='store_true', help='Use a loopback page origin for automated acceptance.')
    args = parser.parse_args()
    accounts = [dict(id=str(i), email=f'fixture{i}@example.test', account_id=f'workspace-{i}',
                     eligible=True, busy=False, has_credentials=True) for i in range(6)]
    state = dict(disclosure=False, mode='mixed', delay=0, starts=[], previews=0, paused=False,
                 pair_mode='ready', status_mode='ready', pair_requests=0, status_requests=0,
                 pause_record='matched', probe_used=False, recovery_mode='success', recoveries=[], resumes=[], restricted=False, recovery_reads=0, last_recovery=None, pause_id='fixture-pause', recovery_state='ready')
    state.update(process_reads=0, server_reads=0, process_probes=0, process_probe_kinds=[], process_mode="ready")
    from agent_monitor.network import process_network
    def process_report():
        state['process_reads'] += 1
        processes = []
        for i, name in enumerate(('codex', 'claude')):
            proxy = f'http://proxy-{name}.test:8080'
            target = f'https://{"chatgpt.com" if name == "codex" else "api.anthropic.com"}/cdn-cgi/trace'
            options = {
                'provider': dict(status='available', target=target, proxy_address=proxy),
                'third_party': dict(status='available', target='http://ip-api.com/json/?fields=status,query,countryCode,country,regionName,city', proxy_address=proxy),
            }
            if state['process_mode'] == 'blocked':
                options['provider'].update(status='blocked', reason='合成绕过规则尚未核实', next_step='核对原有策略后重新读取；不要移除绕过规则')
            processes.append(dict(agent=name, pid=100+i, instance=f'{100+i}:123', environment_status='startup_snapshot',
                 context=dict(cwd=f'/work/fixture-{name}', started_at='2026-10-30T00:00:00Z', parent_pid=90+i, terminal=f'ttys{i}'),
                 environment={'HTTPS_PROXY': dict(address=proxy)}, probe_options=options,
                 proxy_observation=dict(status='observed', connections=[dict(host='fixture-service.test', port=443,
                     local=f'127.0.0.1:{4000+i}', peer=f'127.0.0.1:{5000+i}', chains=['fixture-exit', 'GLOBAL'],
                     rule='MATCH', observed_at='2026-10-30T00:00:00Z')]),
                 connections_status='observed', connections=[dict(local=f'127.0.0.1:{4000+i}', peer=f'127.0.0.1:{5000+i}', state='ESTABLISHED')]))
        if state['process_mode'] == 'reordered':
            processes.reverse()
        if state['process_mode'] == 'removed':
            processes = processes[:1]
        return dict(schema_version=1, host='fixture-browser-device', observed_at=f'2026-10-30T00:00:{state["process_reads"]:02d}Z', errors=[], processes=processes)
    process_network.read_report = process_report
    network_html = re.sub(r'<script\b[^>]*>.*?</script>', '', (ROOT / 'web/network.html').read_text(), flags=re.S)
    network_html = network_html.replace('</body>', '''<script src="/web/network.js"></script><script>
      window.AgentMonitorModules.network({platform:{window,document,fetch:(...a)=>fetch(...a)}, qs:s=>document.querySelector(s),
        updateNavLinks:()=>{}, api:async (url,p)=>{const r=await fetch(url,{method:p?.force?'POST':'GET',headers:{'X-Agent-Monitor':'action'}});return r.json()},
        withRefresh:(_,fn)=>fn(),formatDate:String,
        escapeHtml:s=>String(s).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('"','&quot;'),
        onPageCleanup:()=>{}}).initNetwork();
      </script></body>''')
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
            elif self.path == '/network':
                self.send(network_html.encode(), 'text/html; charset=utf-8')
            elif self.path == '/api/network/cache':
                self.send(dict(available=False))
            elif self.path in ('/web/styles.css', '/web/codex-accounts.js', '/web/account-egress.js', '/web/quota.js', '/web/network.js'):
                body = (ROOT / self.path.lstrip('/')).read_bytes()
                if self.path.endswith(('/codex-accounts.js', '/network.js')):
                    body = body.replace(b'127.0.0.1:39002', f'127.0.0.1:{pair.server_port}'.encode())
                self.send(body, 'text/css' if self.path.endswith('.css') else 'text/javascript')
            elif self.path == '/api/codex-accounts':
                visible = [dict(a, restrictions={'unknown': True} if state['restricted'] and a['id'] == '0' else {}) for a in accounts]
                items = []
                if state['disclosure']:
                    warning = '服务端明确限制了额度使用，已暂停发送。可仅刷新配额；只有服务端明确允许使用后才解除此暂停，0% 或重置时间经过不代表恢复。'
                    for i, account in enumerate(visible):
                        blocked = i in (1, 5)
                        op = dict(id=f'op-{i}', batch_id='fixture-old', stage='failed' if blocked else 'succeeded',
                                  message_status='not_sent' if blocked else 'succeeded', started_at='2026-10-23T01:00:00Z',
                                  detail=warning if blocked else '发送成功', message='请描述今天的天气。')
                        op.update(before=dict(observed_at='2026-10-23T00:59:00Z', seven_day_resets_at=1893456000, seven_day_used_pct=20),
                                  after=dict(observed_at='2026-10-23T01:01:00Z', seven_day_resets_at=1893456000, seven_day_used_pct=21),
                                  diagnostics=dict(last_request='turn/start', last_request_at='2026-10-23T01:00:00Z',
                                                   last_event='turn/completed', last_event_at='2026-10-23T01:01:00Z',
                                                   thread_id=f'fixture-thread-{i}', turn_id=f'fixture-turn-{i}', interrupt='acknowledged'))
                        if blocked:
                            op['failure'] = dict(stage='quota', category='quota_exhausted', http_status=429)
                        account.update(batch_result=dict(id=f'history-{i}', batch_id='older-batch', message_status='succeeded',
                                                         started_at='2026-10-20T00:00:00Z', message='历史批次消息。'),
                                       last_failure=dict(id=f'failure-{i}', detail='历史连接失败，保留供核对。'))
                        account.update(operation=op, restrictions={'quota': {'reason': 'quota'}} if blocked else {},
                                       restriction_details=[warning] if blocked else [])
                        items.append(dict(profile_id=account['id'], operation=op))
                self.send(dict(accounts=visible, batch=dict(id='fixture-old', items=items, created_at='2026-10-23T01:00:00Z'),
                               upstream_pause=pause() if state['paused'] else None))
            elif self.path.startswith('/api/quota-recovery?'):
                state['recovery_reads'] += 1
                if state['recovery_state'] == 'unavailable':
                    self.send(dict(error='Synthetic state read failure'), status=503)
                    return
                self.send(dict(machine='fixture-hub', source_host_identity='fixture-host', observed_at='2026-10-28T00:00:00Z',
                    pause=(dict(id='unreadable') if state['recovery_state']=='unreadable' else pause()) if state['paused'] else None,
                    active=dict(id='active') if state['recovery_state']=='active' else None,
                    account=dict(account_id='' if state['recovery_state']=='identity' else 'workspace-0', account_label='fixture0@example.test', source='default', credential_path='/fixture/.codex/auth.json'),
                    last_recovery=state['last_recovery']))
            elif self.path == '/fixture/quota':
                self.send(quota())
            elif self.path == '/fixture/stats':
                self.send(state)
            else:
                self.send({}, status=404)

        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers.get('Content-Length', '0'))) or '{}')
            if self.path == '/api/network':
                state['server_reads'] += 1
                self.send(dict(checked_host='fixture-server-device', error='Synthetic server unavailable', verdict='unknown'))
            elif self.path == '/api/codex-accounts/egress-preview':
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
                for key in ('disclosure', 'mode', 'delay', 'paused', 'pair_mode', 'status_mode', 'pause_record', 'probe_used', 'recovery_mode', 'restricted', 'recovery_state', 'process_mode'):
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
    connector.process_report_lock = threading.Lock()
    connector.guard = threading.Lock()
    connector.process_probe_tickets = {}
    class SyntheticProbe:
        def read(self, pid, instance, kind='provider'):
            assert kind in ('provider', 'third_party'), 'unexpected synthetic probe kind'
            state['process_probes'] += 1
            state['process_probe_kinds'].append(kind)
            name = 'codex' if int(pid) == 100 else 'claude'
            host = ('chatgpt.com' if name == 'codex' else 'api.anthropic.com') if kind == 'provider' else 'ip-api.com'
            target = f'https://{host}/cdn-cgi/trace' if kind == 'provider' else 'http://ip-api.com/json/?fields=status,query,countryCode,country,regionName,city'
            return dict(instance=instance, probe_kind=kind, target=target, host=host, proxy_address=f'http://proxy-{name}.test:8080',
                        ip='192.0.2.10' if kind == 'provider' else '192.0.2.20', country_code='SG', observed_at=time.time(), cached=False,
                        process_still_matches=True)
    connector.process_probe_cache = SyntheticProbe()
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
                body = (f'<script>window.opener.postMessage({payload},{json.dumps(page_origin)});window.close()</script>').encode()
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
    page_origin = origin
    facade = None
    if args.facade:
        # Real facade transport/admission and real connector owner validation.
        # Only the account owner and health identity are synthetic fixtures.
        from agent_monitor import http_server as server
        from agent_monitor.views import hub_reads, hub_actions
        from agent_monitor.network import client_connector, network_policy
        from agent_monitor.runtime import process_identity
        import requests
        fixture_root = Path(os.environ['HOME']) / 'facade-installation'
        fixture_state = fixture_root / 'state'
        fixture_state.mkdir(parents=True)
        client_connector.PROJECT_ROOT = fixture_root
        def session():
            result = requests.Session()
            result.trust_env = False
            return result
        network_policy.session = session
        hub_reads.origin = lambda: origin
        class Facade(server.Handler):
            def log_message(self, *_args): pass
            def do_GET(self):
                path = urlsplit(self.path).path
                if hub_actions.supports('GET', path):
                    return super().do_GET()
                if path == '/api/runtime-context':
                    return Handler.send(self, dict(local_network=True, statistics_source='hub', hub_url=origin))
                if path == '/api/health':
                    return Handler.send(self, dict(pid=os.getpid(), source_root=str(fixture_root)))
                return Handler.do_GET(self)
            def do_POST(self):
                if hub_actions.supports('POST', urlsplit(self.path).path):
                    return super().do_POST()
                return Handler.do_POST(self)
            send = Handler.send
        facade = server.ThreadingHTTPServer(('127.0.0.1', 0), Facade)
        page_origin = f'http://127.0.0.1:{facade.server_port}'
        (fixture_state / 'port').write_text(str(facade.server_port))
        process_identity.record(fixture_state, fixture_root, os.getpid())
        html = html.replace("pageScope:()=>()=>true", "hubUrl:()=>" + json.dumps(origin) + ",pageScope:()=>()=>true")
        threading.Thread(target=facade.serve_forever, daemon=True).start()
    timer = threading.Timer(600, http.shutdown)
    timer.daemon = True
    timer.start()
    print(json.dumps(dict(url=page_origin, hub_origin=origin, pair_port=pair.server_port, lifetime_seconds=600)), flush=True)
    try:
        http.serve_forever()
    finally:
        timer.cancel()
        http.server_close()
        connector.stopped.set()
        pair.shutdown(); pair.server_close(); route.close()
        if facade:
            facade.shutdown(); facade.server_close()


if __name__ == '__main__':
    main()
