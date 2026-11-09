import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest import mock

from agent_monitor.network import automatic_egress as gate, network_policy


class AutomaticEgressTests(unittest.TestCase):
    def setUp(self):
        self.route = 'http://agent-monitor:fixture@127.0.0.1:9'
        gate._cache = None
        self.addCleanup(setattr, gate, '_cache', None)
        self.status = {'generation': 4, 'events': []}
        for patch in (mock.patch.dict(os.environ, {network_policy.INTERNAL: self.route}),
                      mock.patch.object(network_policy, 'observations', side_effect=lambda _: self.status),
                      mock.patch.object(network_policy, 'admit_automatic')):
            value = patch.start(); self.addCleanup(patch.stop)
        self.admit = network_policy.admit_automatic

    def probe(self, countries):
        def run(host, route):
            country = countries[gate.account_egress.TARGETS.index(host)]
            return ({'host': host, 'country_code': country, 'error': None if country else 'unknown'},
                    {'route': 'PROXY fixture:80', 'generation': 4})
        return mock.patch.object(gate, '_observed_probe', side_effect=run)

    def test_cn_dominates_and_unknown_skips_without_admission(self):
        for countries, expected in [(('CN', 'US'), 'china'), (('US', 'CN'), 'china'), ((None, None), 'unknown')]:
            with self.subTest(countries=countries), self.probe(countries) as probe:
                gate._cache = None
                batch = gate.Batch()
                for _ in range(2):
                    with self.assertRaises(gate.Skipped) as raised: batch.environment()
                    self.assertEqual(raised.exception.details['reason'], expected)
                self.assertEqual(probe.call_count, 2)
                self.admit.assert_not_called()

    def test_concurrent_batch_reuses_pair_and_binds_inherited_capability(self):
        with self.probe(('US', 'SG')) as probe:
            batch = gate.Batch()
            with ThreadPoolExecutor(max_workers=4) as pool:
                values = list(pool.map(lambda _: batch.environment(), range(4)))
            self.assertEqual(probe.call_count, 2)
            self.admit.assert_called_once()
            route = values[0][network_policy.INTERNAL]
            self.assertIn('agent-monitor.auto-', route)
            self.assertTrue(all(env[network_policy.INTERNAL] == route for env in values))
            with network_policy.using_route(route):
                self.assertEqual(network_policy.child_environment({})[network_policy.INTERNAL], route)
                with self.assertRaises(network_policy.NetworkPolicyError):
                    network_policy.child_environment({}, scope='a'*32)
            gate.Batch().environment()
            self.assertEqual(probe.call_count, 2)
            self.status = {'generation': 5}
            with self.assertRaises(gate.Skipped): gate.Batch().environment()
            self.assertEqual(probe.call_count, 4)

    def test_only_observed_non_cn_route_can_be_authorized(self):
        with self.probe(('US', None)):
            gate.Batch().environment()
        self.assertEqual(self.admit.call_args.args[3], ['PROXY fixture:80'])
        gate._cache = None
        with mock.patch.object(gate, '_observed_probe', return_value=({'country_code': 'US'}, {})):
            with self.assertRaises(gate.Skipped): gate.Batch().environment()

    def test_midbatch_denial_is_sticky_and_missing_bridge_is_unknown(self):
        with self.probe(('US', 'US')):
            batch = gate.Batch(); batch.environment()
            self.status = {'generation': 5, 'automatic_rejection': 'automatic_route_changed'}
            with self.assertRaises(gate.Skipped): batch.check()
            self.status = {'generation': 5}
            with self.assertRaises(gate.Skipped): batch.environment()
        self.status = {'coverage': 'unavailable'}
        with self.assertRaises(gate.Skipped) as raised: gate.Batch().environment()
        self.assertEqual(raised.exception.details['reason'], 'unknown')

    def test_retry_after_does_not_extend_successful_region_evidence(self):
        clock = [0]
        countries = ["US", None]
        def probe(host, route):
            country = countries[gate.account_egress.TARGETS.index(host)]
            return ({"host": host, "country_code": country, "error": None if country else "429",
                     "retry_after_seconds": 3600 if host == "auth.openai.com" else 0},
                    {"route": "DIRECT", "generation": 4})
        with mock.patch.object(gate.time, "monotonic", side_effect=lambda: clock[0]), \
                mock.patch.object(gate, "_observed_probe", side_effect=probe) as run:
            gate.Batch().environment(); self.assertEqual(run.call_count, 2)
            clock[0] = 30
            gate.Batch().environment(); self.assertEqual(run.call_count, 2)
            countries[0] = None; clock[0] = 61
            with self.assertRaises(gate.Skipped): gate.Batch().environment()
            self.assertEqual(run.call_count, 3)
            self.assertEqual(run.call_args.args[0], "chatgpt.com")
            clock[0] = 3601
            with self.assertRaises(gate.Skipped): gate.Batch().environment()
            self.assertEqual(run.call_count, 5)

    def test_generation_change_during_probe_is_not_admitted(self):
        def probe(host, route):
            self.status = {'generation': 5}
            return {'host': host, 'country_code': 'US'}, {'route': 'DIRECT', 'generation': 4}
        with mock.patch.object(gate, '_observed_probe', side_effect=probe):
            with self.assertRaises(gate.Skipped): gate.Batch().environment()
        self.admit.assert_not_called()

class AutomaticTransportTests(unittest.TestCase):
    def test_real_quota_child_uses_guarded_bridge_and_cn_unknown_send_nothing(self):
        import http.server
        import json
        from pathlib import Path
        import selectors
        import subprocess
        import sys
        import tempfile
        import threading
        from agent_monitor.quota import quota_refresh
        from agent_monitor.accounts.upstream_controls import Controls
        from parsers.accounts import Account
        repo = Path(__file__).resolve().parents[1]
        received = []
        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *_): pass
            def do_GET(self):
                received.append(self.path)
                self.send_response(200); self.end_headers(); self.wfile.write(b'ok')
        source = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=source.serve_forever, daemon=True); thread.start()
        self.addCleanup(source.server_close)
        self.addCleanup(source.shutdown)
        script = "const {createBridge}=require('./proxy_runtime/bridge.cjs'); const b=createBridge(async()=> 'DIRECT','fixture'); b.server.listen(0,'127.0.0.1',()=>console.log(b.server.address().port)); process.stdin.resume(); process.stdin.on('end',()=>{b.close();}); setTimeout(()=>{b.close();process.exit(2)},20000).unref();"
        bridge = subprocess.Popen(['node', '-e', script], cwd=repo, stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        def stop():
            bridge.stdin.close()
            try: bridge.wait(timeout=3)
            except subprocess.TimeoutExpired: bridge.kill(); bridge.wait(timeout=3)
            bridge.stdout.close()
        self.addCleanup(stop)
        with selectors.DefaultSelector() as poll:
            poll.register(bridge.stdout, selectors.EVENT_READ)
            self.assertTrue(poll.select(3), 'bridge startup timed out')
            port = int(bridge.stdout.readline())
        route = f'http://agent-monitor:fixture@127.0.0.1:{port}'
        child = repo / 'tests/fixtures/automatic_quota_reader.py'
        target = f'http://127.0.0.1:{source.server_port}/business'
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.dict(os.environ, {network_policy.INTERNAL: route}), \
                mock.patch.object(quota_refresh, 'module_command', return_value=[sys.executable, str(child), target]):
            for country, expected in [('CN', 0), (None, 0), ('US', 2)]:
                gate._cache = None
                self.addCleanup(setattr, gate, '_cache', None)
                batch = gate.Batch()
                with self.subTest(country=country), mock.patch.object(gate, '_observed_probe',
                        side_effect=lambda host, route: ({'host': host, 'country_code': country}, {'route': 'DIRECT', 'generation': 0})):
                    for _ in range(2):
                        if country == 'US':
                            value = quota_refresh.load_rate_limits('claude', Account('fixture'), root=tmp, automatic_batch=batch)
                            self.assertEqual(value.five_hour_pct, 12)
                        else:
                            with self.assertRaises(gate.Skipped):
                                quota_refresh.load_rate_limits('claude', Account('fixture'), root=tmp, automatic_batch=batch)
                    self.assertEqual(len(received), expected)
                    self.assertNotIn('pause', Controls(Path(tmp) / 'state/upstream_controls').read('claude'))
