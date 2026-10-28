"""Readback never repeats provider work or mistakes old receipts for current state."""
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from types import SimpleNamespace
from agent_monitor.accounts.upstream_controls import Controls
from agent_monitor.quota import quota_recovery, quota_refresh, quota_sync
from agent_monitor.snapshots import exporter
from parsers import accounts


class RecoveryStatusTests(unittest.TestCase):
    def test_failed_and_successful_attempts_survive_readback(self):
        for success, keep_pause in ((False, False), (False, True), (True, False)):
            with self.subTest(success=success, keep_pause=keep_pause), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                controls = Controls(root / 'state/upstream_controls')
                lease = controls.acquire('codex')
                lease.pause('original')
                lease.close()
                original = controls.read('codex')['pause']['id']
                request = dict(machine='fixture', account_id='a', account_label='a@example.test', pause_id=original)
                def query(*args, **kwargs):
                    lease = controls.acquire('codex', probe_pause_id=original)
                    kwargs['admitted_probe'].update(lease.state['pause']['probe_attempt'])
                    if not success and not keep_pause:
                        lease.pause('failed', failure={'stage':'quota_read', 'rpc_code':-32603})
                    lease.close()
                    if not success:
                        raise quota_refresh.RefreshError('fixture failure')
                    controls.resume('codex', original)
                    return {'account_id':'a'}
                with mock.patch.object(exporter, 'ROOT', root), \
                        mock.patch.object(exporter, 'exporter_version', return_value='fixture'), \
                        mock.patch.object(accounts, 'codex_account', return_value=accounts.Account('a', 'a@example.test')), \
                        mock.patch('agent_monitor.snapshots.generation.self_certified_host_identity', return_value='host'), \
                        mock.patch.object(quota_recovery.network_policy, 'child_environment', return_value={quota_recovery.network_policy.INTERNAL:'fixture'}), \
                        mock.patch.object(quota_recovery.network_policy, 'using_route'), \
                        mock.patch.object(quota_recovery.network_policy, 'observations', return_value={'coverage':'recent_connections_only', 'events':[]}), \
                        mock.patch('agent_monitor.quota.quota_service.recover_codex_quota', side_effect=query) as provider:
                    receipt = quota_recovery.local_recovery(request)
                    status = quota_recovery.local_status()
                    if not success:
                        before = (controls.root / 'codex.json').read_bytes()
                        replay = quota_recovery.local_recovery(request)
                        self.assertFalse(replay['ok'])
                        self.assertEqual(before, (controls.root / 'codex.json').read_bytes())
                self.assertEqual(provider.call_count, 1 if success else 2)
                self.assertEqual(receipt['ok'], success)
                self.assertNotIn('record_error', receipt)
                self.assertEqual(status['last_recovery']['request'], request)
                self.assertEqual(status['last_recovery']['ok'], success)
                self.assertEqual(status['pause'] is None, success)

    def test_reader_reports_only_the_probe_it_admitted(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            controls = Controls(root / 'state/upstream_controls')
            lease = controls.acquire('codex')
            lease.pause('original')
            lease.close()
            pause_id = controls.read('codex')['pause']['id']
            accepted, refused = {}, {}
            with mock.patch.object(quota_refresh, 'ROOT', root), \
                    mock.patch.object(quota_refresh.network_policy, 'child_environment', return_value={}), \
                    mock.patch.object(quota_refresh.subprocess, 'Popen', side_effect=OSError('fixture spawn failure')) as child:
                with self.assertRaises(OSError):
                    quota_refresh.load_rate_limits('codex', accounts.Account('a', 'a@example.test'),
                                                  probe_pause_id=pause_id, admitted_probe=accepted)
                with self.assertRaises(quota_refresh.RefreshError):
                    quota_refresh.load_rate_limits('codex', accounts.Account('a', 'a@example.test'),
                                                  probe_pause_id=pause_id, admitted_probe=refused)
            self.assertEqual(child.call_count, 1)
            self.assertEqual(accepted['id'], controls.read('codex')['pause']['probe_attempt']['id'])
            self.assertEqual(refused, {})

    def test_network_projection_is_bounded_and_retains_connection_failures(self):
        event = dict(target='https://user:password@host.test/path?token=secret',
                     route='PROXY 127.0.0.1:1234', result='proxy_connect_timeout',
                     at='2026-10-28T00:00:00Z', generation=2)
        value = quota_recovery.safe_network({'coverage':'recent_connections_only', 'events':[event]*70})
        self.assertEqual(len(value['events']), 64)
        self.assertEqual(value['events'][0]['target'], 'https://host.test')
        self.assertEqual(value['events'][0]['result'], 'proxy_connect_timeout')
        self.assertEqual(value['events'][0]['generation'], 2)
        self.assertNotIn('secret', str(value))
        self.assertNotIn('password', str(value))
        self.assertEqual(quota_recovery.safe_network(None)['coverage'], 'unavailable')

    def test_receipt_requires_consumed_matching_probe_and_cannot_change_pause(self):
        with tempfile.TemporaryDirectory() as tmp:
            controls = Controls(Path(tmp))
            lease = controls.acquire('codex')
            lease.pause('original')
            lease.close()
            original = controls.read('codex')['pause']['id']
            value = {'request': {'pause_id': original}, 'ok': False}
            with self.assertRaises(RuntimeError):
                controls.record_recovery(value, attempt_id='not-admitted')
            lease = controls.acquire('codex', probe_pause_id=original)
            attempt_id = lease.state['pause']['probe_attempt']['id']
            lease.pause('failed')
            lease.close()
            before = controls.read('codex')['pause']
            value['pause_id'] = before['id']
            controls.record_recovery(value, attempt_id=attempt_id)
            self.assertEqual(Controls(Path(tmp)).read('codex')['last_recovery'], value)
            self.assertEqual(controls.read('codex')['pause'], before)
            with self.assertRaises(RuntimeError):
                controls.record_recovery({'request': {'pause_id': 'stale'}, 'ok': True}, attempt_id=attempt_id)
            self.assertEqual(controls.read('codex')['last_recovery'], value)
            with self.assertRaises(RuntimeError):
                controls.record_recovery({'request': {'pause_id': original}, 'ok': True}, attempt_id=attempt_id)
            with self.assertRaises(RuntimeError):
                controls.record_recovery(value, attempt_id='not-admitted')

    def test_status_is_read_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            auth = root / 'managed/codex/auth.json'
            auth.parent.mkdir(parents=True)
            auth.write_text('{}')
            controls = Controls(root / 'state/upstream_controls')
            lease = controls.acquire('codex')
            lease.pause('fixture')
            lease.close()
            before = (controls.root / 'codex.json').read_bytes()
            with mock.patch.object(exporter, 'ROOT', root), \
                    mock.patch.object(accounts, 'CODEX_AUTH', auth), \
                    mock.patch.object(accounts, 'codex_account', return_value=accounts.Account('workspace', 'a@example.test')), \
                    mock.patch('agent_monitor.snapshots.generation.self_certified_host_identity', return_value='host'), \
                    mock.patch('agent_monitor.network.network_policy.child_environment') as network, \
                    mock.patch('agent_monitor.quota.quota_service.recover_codex_quota') as provider:
                status = quota_recovery.local_status()
            self.assertEqual(status['account']['account_label'], 'a@example.test')
            self.assertEqual(status['account']['credential_path'], str(auth))
            self.assertEqual(status['pause']['id'], controls.read('codex')['pause']['id'])
            self.assertEqual(status['source_host_identity'], 'host')
            self.assertIsNone(status['last_recovery'])
            self.assertEqual(before, (controls.root / 'codex.json').read_bytes())
            network.assert_not_called()
            provider.assert_not_called()

    def test_status_rejects_unadmitted_or_mismatched_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager = quota_sync.Manager(Path(tmp)/'quota.json')
            machine = SimpleNamespace(name='fixture', ssh_host='fixture', is_self=False)
            current = SimpleNamespace(host='fixture', meta={'source_host_identity':'expected'})
            admission = mock.MagicMock()
            admission.__enter__.return_value = SimpleNamespace(admitted=[current])
            with mock.patch('agent_monitor.snapshots.generation.generation_admission_snapshot', return_value=admission), \
                    mock.patch.object(quota_recovery, 'read_status', return_value={'source_host_identity':'wrong'}) as read:
                with self.assertRaises(ValueError):
                    manager.recovery_status(machine)
                admission.__enter__.return_value = SimpleNamespace(admitted=[])
                read.reset_mock()
                with self.assertRaises(ValueError):
                    manager.recovery_status(machine)
                read.assert_not_called()
