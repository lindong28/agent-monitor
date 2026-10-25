"""Lifecycle tests isolate HOME and launchctl; no provider requests."""
import contextlib
import io
import json
from pathlib import Path
import plistlib
import subprocess
import tempfile
import unittest
from unittest import mock
import connector_service as service


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.plist = self.root / 'LaunchAgents/connector.plist'
        self.state = self.root / 'state/status.json'
        (self.root / '.venv/bin').mkdir(parents=True)
        (self.root / '.venv/bin/python').touch()
        for patch in (mock.patch.object(service, 'ROOT', self.root), mock.patch.object(service, 'paths', return_value=(self.plist, self.state)), mock.patch.object(service, 'health', return_value=None)):
            patch.start()
            self.addCleanup(patch.stop)
        patch = mock.patch.object(service.service_runtime, 'wait_for_identity')
        patch.start()
        self.addCleanup(patch.stop)
        patch = contextlib.redirect_stdout(io.StringIO())
        patch.__enter__()
        self.addCleanup(patch.__exit__, None, None, None)

    def save(self, value=None):
        self.plist.parent.mkdir(parents=True, exist_ok=True)
        self.plist.write_bytes(plistlib.dumps(value or service.definition('http://hub:39001', self.state)))

    def result(self, code):
        args = service.definition('http://hub:39001', self.state)['ProgramArguments']
        return subprocess.CompletedProcess([], 113 if code == 1 else code, 'arguments = {\n' + '\n'.join(args) + '\n}\n', '')

    def test_origin_rejects_credentials_paths_and_invalid_ports(self):
        self.assertEqual(service.origin('http://hub:39001/'), 'http://hub:39001')
        for value in ['http://user:secret@hub', 'http://hub/path', 'http://hub?secret=x', 'http://hub:bad', 'file:///tmp/a']:
            with self.subTest(value=value), self.assertRaises(ValueError):
                service.origin(value)

    def test_install_bootstraps_without_claiming_ready(self):
        with mock.patch.object(service, 'launch', side_effect=[self.result(1), self.result(0), self.result(0)]) as launch, mock.patch.object(service.socket, 'socket'):
            value = service.install('http://hub:39001')
        self.assertFalse(value['connected'])
        saved = plistlib.loads(self.plist.read_bytes())
        self.assertTrue(saved['KeepAlive'])
        self.assertEqual(saved['ThrottleInterval'], 30)
        self.assertEqual(self.plist.stat().st_mode & 0o777, 0o600)
        self.assertEqual([c.args[0] for c in launch.call_args_list], ['print', 'bootstrap', 'print'])

    def test_same_configuration_preserves_route(self):
        self.save()
        live = {'source_root': str(self.root), 'deployment_signature': service.definition('http://hub:39001', self.state)['EnvironmentVariables']['AGENT_MONITOR_DEPLOYMENT_SIGNATURE']}
        with mock.patch.object(service, 'launch', return_value=self.result(0)) as launch, mock.patch.object(service, 'health', return_value=live):
            service.install('http://hub:39001')
        self.assertEqual([c.args[0] for c in launch.call_args_list], ['print', 'print'])

    def test_foreign_and_malformed_definitions_preserved(self):
        for value in [{'Label': service.LABEL, 'ProgramArguments': ['foreign']}, {'Label': service.LABEL, 'ProgramArguments': 3}]:
            self.save(value)
            before = self.plist.read_bytes()
            with self.assertRaises(ValueError), mock.patch.object(service, 'launch') as launch:
                service.stop()
            self.assertEqual(self.plist.read_bytes(), before)
            launch.assert_not_called()

    def test_port_conflict_preserves_occupant(self):
        with mock.patch.object(service, 'launch', return_value=self.result(1)) as launch, mock.patch.object(service.socket, 'socket') as sock:
            sock.return_value.__enter__.return_value.bind.side_effect = OSError(48, 'busy')
            with self.assertRaises(ValueError):
                service.install('http://hub:39001')
        self.assertFalse(self.plist.exists())
        self.assertEqual(launch.call_count, 1)

    def test_stop_unloads_and_removes_autostart(self):
        self.save()
        with mock.patch.object(service, 'launch', return_value=self.result(0)) as launch:
            service.stop()
        self.assertEqual([c.args[0] for c in launch.call_args_list], ['print', 'bootout'])
        self.assertFalse(self.plist.exists())

    def test_failed_stop_preserves_configuration(self):
        self.save()
        with mock.patch.object(service, 'launch', side_effect=[self.result(0), self.result(1)]), self.assertRaises(ValueError):
            service.stop()
        self.assertTrue(self.plist.exists())

    def test_unknown_loaded_job_is_not_adopted(self):
        with mock.patch.object(service, 'launch', return_value=self.result(0)) as launch, self.assertRaises(ValueError):
            service.install('http://hub:39001')
        self.assertEqual(launch.call_count, 1)

    def test_status_separates_loaded_and_ready(self):
        self.save()
        for server, expected in [('http://other', False), ('http://hub:39001', True)]:
            with mock.patch.object(service, 'launch', return_value=self.result(0)), mock.patch.object(service, 'health', return_value={'connected': True, 'server': server, 'label': 'device'}):
                self.assertEqual(service.inspect()['connected'], expected)

    def test_latest_failure_retained_in_single_private_file(self):
        service.record(self.state, 'failed', server='http://hub', failure={'stage': 'register', 'http_status': 503})
        service.record(self.state, 'connected', server='http://hub', instance_id='new')
        saved = json.loads(self.state.read_text())
        self.assertEqual(saved['stage'], 'connected')
        self.assertEqual(saved['last_failure']['http_status'], 503)
        self.assertEqual(self.state.stat().st_mode & 0o777, 0o600)
        self.assertEqual(list(self.state.parent.iterdir()), [self.state])

    def test_bootstrap_failure_is_reported(self):
        with mock.patch.object(service, 'launch', side_effect=[self.result(1), self.result(1)]), mock.patch.object(service.socket, 'socket'), self.assertRaises(ValueError):
            service.install('http://hub:39001')
        self.assertTrue(self.plist.exists())

    def test_optional_convergence_skips_fresh_and_preserves_installed_server(self):
        with mock.patch.object(service.platform, 'system', return_value='Darwin'), mock.patch.object(service, 'launch', return_value=self.result(1)), mock.patch.object(service, 'install') as install:
            self.assertEqual(service.main(['converge']), 0)
            install.assert_not_called()
            self.save()
            install.return_value = {'connected': False, 'installed': True, 'loaded': True, 'last_status': {}}
            self.assertEqual(service.main(['converge']), 0)
            install.assert_called_once_with('http://hub:39001')

    def test_new_code_reloads_installed_connector_with_same_server_and_state(self):
        self.save()
        (self.root / 'client_connector.py').write_text('new code')
        with mock.patch.object(service, 'launch', return_value=self.result(0)) as launch:
            service.install('http://hub:39001')
        self.assertEqual([c.args[0] for c in launch.call_args_list], ['print', 'bootout', 'bootstrap', 'print'])
        self.assertEqual(plistlib.loads(self.plist.read_bytes())['ProgramArguments'][3:], ['http://hub:39001', '--state-file', str(self.state)])
