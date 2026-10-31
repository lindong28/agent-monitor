"""Action facade admission and real HTTP serialization using synthetic data only."""
import contextlib
import http.client
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import tempfile
import threading
import unittest
from unittest import mock

from agent_monitor import http_server as server
from agent_monitor.views import hub_actions, hub_reads
from agent_monitor.network.client_connector import Connector
from agent_monitor.runtime import process_identity


class HubActionsTests(unittest.TestCase):
    def setUp(self):
        self.receipts = []
        self.status = 200
        self.value = {'ok': True}
        outer = self
        class Owner(BaseHTTPRequestHandler):
            def log_message(self, *_args): pass
            def do_GET(self): self.respond()
            def do_POST(self): self.respond()
            def respond(self):
                body = self.rfile.read(int(self.headers.get('Content-Length', '0')))
                outer.receipts.append((self.command, self.path, dict(self.headers), json.loads(body) if body else None))
                result = json.dumps(outer.value).encode()
                self.send_response(outer.status)
                self.send_header('Content-Length', str(len(result)))
                self.end_headers(); self.wfile.write(result)
        self.owner = ThreadingHTTPServer(('127.0.0.1', 0), Owner)
        self.facade = server.ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        self.threads=[]
        for http in (self.owner, self.facade):
            t=threading.Thread(target=http.serve_forever, daemon=True); t.start(); self.threads.append(t)
        self.addCleanup(self.close)
        self.target=f'http://127.0.0.1:{self.owner.server_port}'
        self.origin=f'http://127.0.0.1:{self.facade.server_port}'
        import requests
        def session():
            value=requests.Session(); value.trust_env=False; return value
        for patch in (mock.patch.object(server.socket, 'getfqdn', return_value='fixture.example'),
                      mock.patch.object(hub_reads, 'origin', return_value=self.target),
                      mock.patch.object(hub_actions.network_policy, 'session', side_effect=session)):
            patch.start(); self.addCleanup(patch.stop)

    def close(self):
        for http in (self.facade, self.owner): http.shutdown(); http.server_close()
        for t in self.threads: t.join(3)

    def request(self, path, method='POST', payload=None, headers=None):
        h={'Origin': self.origin, 'Content-Type':'application/json',
           'X-Agent-Monitor':'codex-accounts' if path.startswith('/api/codex-accounts') else 'action'}
        h.update(headers or {})
        c=http.client.HTTPConnection('127.0.0.1', self.facade.server_port, timeout=3)
        try:
            c.request(method, path, body=json.dumps(payload or {}) if method=='POST' else None,
                      headers={k:v for k,v in h.items() if v is not None})
            r=c.getresponse(); return r.status, json.loads(r.read())
        finally: c.close()

    def test_every_admitted_action_preserves_identity_and_owner_result(self):
        data={'account_id':'fixture-workspace', 'account_label':'fixture@example.test',
              'observed_at':'2026-10-31T00:00:00Z', 'pause_id':'a'*32,
              'connection':{'id':'fixture', 'capability':'synthetic-capability'}}
        for method, paths in (('POST', hub_actions.POST_PATHS), ('GET', hub_actions.GET_PATHS)):
            for path in sorted(paths):
                with self.subTest(method=method, path=path):
                    query='?machine=self' if method=='GET' and path.endswith('quota-recovery') else '?ack=1' if path.endswith('/refresh') and path=='/api/refresh' else ''
                    self.assertEqual(self.request(path+query, method, data, {'Cookie':'synthetic-cookie','Authorization':'Bearer synthetic-token'}),(200,{'ok':True}))
                    sent=self.receipts[-1]
                    self.assertEqual(sent[0:2],(method,path+query))
                    self.assertEqual(sent[2]['Origin'],self.target)
                    self.assertEqual(sent[2][hub_actions.HOP_HEADER],'1')
                    self.assertNotIn('Cookie',sent[2]); self.assertNotIn('Authorization',sent[2])
                    self.assertEqual(sent[3],data if method=='POST' else None)
        for status in (400,409,429,503):
            self.status=status; self.value={'error':'fixture owner rejection','pause_id':'unchanged'}
            self.assertEqual(self.request('/api/quota-recovery'),(status,self.value))

    def test_reject_bad_boundary_loop_method_shape_and_query_before_transport(self):
        for h in ({'Origin':'http://evil.test'}, {'Origin':None}, {'Host':'evil.test'},
                  {'X-Agent-Monitor':None}, {'Sec-Fetch-Site':'cross-site'}, {hub_actions.HOP_HEADER:'1'}):
            with self.subTest(headers=h): self.assertIn(self.request('/api/refresh',headers=h)[0],(403,502))
        self.assertEqual(self.request('/api/refresh','GET')[0],405)
        self.assertEqual(self.request('/api/codex-accounts/unknown')[0],405)
        self.assertEqual(self.request('/api/refresh?url=http://evil.test')[0],400)
        self.assertEqual(self.request('/api/refresh',payload={'oversize':'x'*4096})[0],400)
        self.assertEqual(self.request('/api/refresh',payload=['invalid'])[0],400)
        self.assertEqual(self.receipts,[])

    def test_lost_or_invalid_write_receipt_is_unknown_without_replay(self):
        for status, value in ((302,{}),(200,[])):
            self.status=status; self.value=value; before=len(self.receipts)
            code,data=self.request('/api/account-memory/remove')
            self.assertEqual(code,502); self.assertIn('结果未知',data['error'])
            self.assertEqual(len(self.receipts),before+1)
        session=mock.Mock(request=mock.Mock(side_effect=TimeoutError()))
        code,data=hub_actions.forward('POST','/api/quota-recovery',{}, {},target=self.target,
                                     session_factory=lambda:contextlib.nullcontext(session))
        self.assertEqual(code,502); self.assertIn('不要重复提交',data['error'])
        session.request.assert_called_once()
        self.assertEqual(session.request.call_args.kwargs['timeout'],(3,90))


class LocalPairTests(unittest.TestCase):
    def test_only_recorded_live_dashboard_with_matching_hub_can_receive_capability(self):
        connector=Connector.__new__(Connector); connector.server='http://hub.test'
        owned=(12345, {'start':1})
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); (root/'state').mkdir(); (root/'state/port').write_text('39001')
            health={'pid':12345,'source_root':str(root)}
            context={'hub_url':connector.server,'statistics_source':'hub','local_network':True}
            with mock.patch('agent_monitor.network.client_connector.PROJECT_ROOT',root), \
                 mock.patch.object(process_identity,'read_owned',return_value=owned) as owner, \
                 mock.patch('agent_monitor.network.client_connector.http.client.HTTPConnection') as http:
                def responses(*values):
                    http.return_value.getresponse.side_effect=[mock.Mock(status=200,read=mock.Mock(return_value=json.dumps(v).encode())) for v in values]
                responses(health,context)
                self.assertTrue(connector.local_pair_origin('http://127.0.0.1:39001'))
                self.assertEqual(http.call_args.args,('127.0.0.1',39001))
                for origin in ('http://127.0.0.1:39003','http://evil.test','http://localhost:39001','http://127.0.0.1:39001/other'):
                    http.reset_mock(); self.assertFalse(connector.local_pair_origin(origin)); http.assert_not_called()
                for h,c in (({**health,'pid':999},context),(health,{**context,'hub_url':'http://other.test'}),
                            ({**health,'source_root':'/other'},context),(health,{**context,'statistics_source':'local'})):
                    responses(h,c); self.assertFalse(connector.local_pair_origin('http://127.0.0.1:39001'))
                responses(health,context); owner.side_effect=[owned,(999,{})]
                self.assertFalse(connector.local_pair_origin('http://127.0.0.1:39001'))
                owner.side_effect=None; owner.return_value=None; http.reset_mock()
                self.assertFalse(connector.local_pair_origin('http://127.0.0.1:39001')); http.assert_not_called()


if __name__=='__main__': unittest.main()
