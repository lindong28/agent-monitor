"""Deployment regressions; fake launchd and isolated checkout/HOME only."""
import contextlib
import fcntl
import io
import json
import os
from pathlib import Path
import plistlib
import subprocess
import shutil
import sys
import tempfile
import unittest
from unittest import mock
import hub
import connector_service as connector
import service_runtime

ROOT = Path(__file__).resolve().parents[1]


class DeploymentRegressionTests(unittest.TestCase):
    def test_matching_web_definition_with_old_process_reloads_and_requires_http(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dest = root / 'Library/LaunchAgents' / (hub.WEB_LABEL + '.plist')
            dest.parent.mkdir(parents=True)
            with mock.patch.object(hub, 'ROOT', root), mock.patch('hub.Path.home', return_value=root), mock.patch('agent_monitor.hub_config.is_hub', return_value=True):
                wanted = hub.web_plist(root, sys.executable, ssh_auth_sock=os.environ.get('SSH_AUTH_SOCK'), path=os.environ.get('PATH'))
                dest.write_bytes(plistlib.dumps(wanted))
                loaded = subprocess.CompletedProcess([], 0, 'arguments = {\n' + '\n'.join(wanted['ProgramArguments']) + '\n}\n', '')
                with mock.patch('hub.subprocess.run', return_value=loaded) as launch, mock.patch('hub.service_runtime.read_health', return_value={}), mock.patch('hub.service_runtime.wait_for_identity', side_effect=ValueError('identity missing')):
                    with self.assertRaisesRegex(ValueError, 'identity missing'):
                        hub.install_web()
                self.assertEqual([call.args[0][1] for call in launch.call_args_list], ['print', 'bootout', 'bootstrap'])
                self.assertTrue(dest.exists())

    def test_selected_configuration_changes_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'hub.json').write_text('{}')
            before = service_runtime.signature(root)
            (root / 'hub.local.json').write_text('{"port":39003}')
            self.assertNotEqual(before, service_runtime.signature(root))
            current = service_runtime.signature(root)
            (root / 'hub.json').write_text('{"unused":true}')
            self.assertEqual(current, service_runtime.signature(root))

    def test_readiness_requires_source_and_version_and_is_bounded(self):
        wanted = {'source_root': '/owned', 'deployment_signature': 'new'}
        for value in ({}, dict(wanted, source_root='/foreign'), dict(wanted, deployment_signature='old')):
            with self.subTest(value=value), self.assertRaises(ValueError):
                service_runtime.wait_for_identity(lambda: value, '/owned', 'new', timeout=0)
        self.assertEqual(service_runtime.wait_for_identity(lambda: wanted, '/owned', 'new', timeout=0), wanted)

    def test_web_definition_changes_when_python_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'server.py'
            source.write_text('VERSION = 1\n')
            before = hub.web_plist(root, '/python')
            source.write_text('VERSION = 2\n')
            self.assertNotEqual(before, hub.web_plist(root, '/python'))

    def test_stopping_already_stopped_hub_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory, mock.patch('hub.Path.home', return_value=Path(directory)), mock.patch('hub.subprocess.run', return_value=subprocess.CompletedProcess([], 113, '', '')), contextlib.redirect_stdout(io.StringIO()):
            hub.stop_web()

    def test_connector_does_not_stop_foreign_loaded_job(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dest, state = root / 'job.plist', root / 'state.json'
            with mock.patch.object(connector, 'ROOT', root), mock.patch.object(connector, 'paths', return_value=(dest, state)):
                dest.write_bytes(plistlib.dumps(connector.definition('http://hub:39001', state)))
                loaded = subprocess.CompletedProcess([], 0, 'arguments = {\n/foreign/python\n/foreign/client_connector.py\n}\n', '')
                with mock.patch.object(connector, 'launch', return_value=loaded) as launch:
                    with self.assertRaises(ValueError):
                        connector.stop()
                self.assertEqual(launch.call_count, 1)
                self.assertTrue(dest.exists())

    def test_connector_converge_distinguishes_absent_from_unknown_loaded_job(self):
        with tempfile.TemporaryDirectory() as directory:
            dest = Path(directory) / 'missing.plist'
            for code, expected in ((113, 0), (0, 1), (1, 1)):
                with self.subTest(code=code), mock.patch.object(connector, 'ROOT', Path(directory)), mock.patch.object(connector, 'paths', return_value=(dest, dest.with_suffix('.json'))), mock.patch('connector_service.platform.system', return_value='Darwin'), mock.patch.object(connector, 'launch', return_value=subprocess.CompletedProcess([], code, 'arguments = {\n/foreign/connector.py\n}\n', '')) as launch, contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                    self.assertEqual(connector.main(['converge']), expected)
                    self.assertEqual(launch.call_count, 1)


    def test_connector_status_accepts_disabled_but_not_unknown_or_disconnected(self):
        with tempfile.TemporaryDirectory() as directory:
            dest = Path(directory) / 'missing.plist'
            for code, live, expected in ((113, None, 0), (0, None, 1), (1, None, 1), (113, {'connected': False}, 1)):
                with self.subTest(code=code, live=live), mock.patch.object(connector, 'ROOT', Path(directory)), mock.patch.object(connector, 'paths', return_value=(dest, dest.with_suffix('.json'))), mock.patch('connector_service.platform.system', return_value='Darwin'), mock.patch.object(connector, 'launch', return_value=subprocess.CompletedProcess([], code, '', '')), mock.patch.object(connector, 'health', return_value=live), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                    self.assertEqual(connector.main(['status']), expected)


class ShellLifecycleTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name).resolve()
        self.root = self.home / 'checkout'
        self.root.mkdir()
        self.bin = self.home / 'bin'
        self.bin.mkdir()
        self.env = dict(os.environ, HOME=str(self.home), PATH=str(self.bin) + os.pathsep + os.environ['PATH'],
                        AGENT_MONITOR_PYTHON=sys.executable, SSH_CONNECTION='', DISPLAY='', WAYLAND_DISPLAY='')
        shutil.copytree(ROOT / 'agent_monitor', self.root / 'agent_monitor', ignore=shutil.ignore_patterns('__pycache__'))
        for name in ('project_paths.py', 'uninstall.sh', 'status.sh', 'agent-monitor', 'rollup_service.py', 'service_runtime.py', 'deployment_guard.py'):
            shutil.copy2(ROOT / name, self.root / name)

    def executable(self, name, source):
        path = self.bin / name
        path.write_text(source)
        path.chmod(0o755)

    def run_script(self, name, *arguments):
        return subprocess.run(['bash', str(self.root / name), *arguments], env=self.env,
                              capture_output=True, text=True, timeout=10)

    @unittest.skipUnless(sys.platform == "darwin", "native macOS launchd integration")
    def test_rollup_uninstall_preserves_foreign_disk_and_loaded_owners(self):
        dest = self.home / 'Library/LaunchAgents/com.agent-monitor.rollup.plist'
        dest.parent.mkdir(parents=True)
        calls = self.home / 'calls'
        self.executable('launchctl', '#!/bin/sh\nif [ "$1" = print ]; then\n[ ! -e "$HOME/unloaded" ] || exit 113\necho "program = $LOADED_PROGRAM"\nprintf "arguments = {\\n%s\\nrefresh\\n}\\n" "$LOADED_PROGRAM"\nexit "${PRINT_CODE:-0}"\nfi\necho "$1" >> "$HOME/calls"\ntouch "$HOME/unloaded"\n')
        for disk_owner, loaded_owner, allowed in (('/foreign', '/foreign', False),
                (str(self.root), '/foreign', False), (str(self.root), str(self.root), True)):
            with self.subTest(disk=disk_owner, loaded=loaded_owner):
                value = {'Label': 'com.agent-monitor.rollup', 'ProgramArguments': [disk_owner + '/agent-monitor', 'refresh']}
                dest.write_bytes(plistlib.dumps(value))
                self.env['LOADED_PROGRAM'] = loaded_owner + '/agent-monitor'
                result = self.run_script('uninstall.sh', 'rollup-daemon')
                self.assertEqual(result.returncode == 0, allowed, result.stdout + result.stderr)
                self.assertEqual(dest.exists(), not allowed)
                self.assertEqual(calls.exists(), allowed)
        self.assertEqual(calls.read_text().splitlines(), ['bootout'])
        self.env['PRINT_CODE'] = '113'
        self.assertEqual(self.run_script('uninstall.sh', 'rollup-daemon').returncode, 0)

    def test_web_uninstall_keeps_foreign_cli_links(self):
        # The lifecycle command is isolated; this test exercises link ownership.
        (self.root / 'hub.py').write_text('')
        (self.root / 'agent-monitor').write_text('#!/bin/sh\nexit 0\n')
        link_dir = self.home / '.local/bin'
        link_dir.mkdir(parents=True)
        (link_dir / 'agent-monitor').symlink_to('/foreign/agent-monitor')
        (link_dir / 'ip-check').symlink_to(self.root / 'ip-check')
        result = self.run_script('uninstall.sh', 'web')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((link_dir / 'agent-monitor').is_symlink())
        self.assertFalse((link_dir / 'ip-check').is_symlink())

    def test_client_management_requires_explicit_local_target(self):
        (self.root / 'hub.py').write_text("import sys\nprint('client' if sys.argv[1] == 'role' else 'http://fixture:39001')\n")
        (self.root / 'process_identity.py').write_text("raise SystemExit(1)\n")
        for command in ('start', 'stop', 'restart'):
            result = self.run_script('agent-monitor', command)
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertIn('agent-monitor local ' + command, result.stderr)
            self.assertFalse((self.root / 'state').exists())
        result = self.run_script('agent-monitor', 'status')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Role: Hub client', result.stdout)
        result = self.run_script('agent-monitor', 'local', 'status')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('not running', result.stdout)
        (self.root / 'hub.py').write_text("print('hub')\n")
        result = self.run_script('agent-monitor', 'local', 'stop')
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertFalse((self.root / 'state').exists())

    def test_web_uninstall_cleans_owned_links_for_each_role(self):
        (self.root / 'process_identity.py').write_text('raise SystemExit(1)\n')
        self.executable('launchctl', '#!/bin/sh\nexit 113\n')
        links = self.home / '.local/bin'
        links.mkdir(parents=True)
        for role in ('standalone', 'client', 'hub'):
            with self.subTest(role=role):
                (self.root / 'hub.py').write_text("import sys\nif sys.argv[1] == 'role': print(" + repr(role) + ")\n")
                for name in ('agent-monitor', 'ip-check'):
                    (links / name).symlink_to(self.root / name)
                result = self.run_script('uninstall.sh', 'web')
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(list(links.iterdir()), [])

    def test_public_service_mutators_share_the_install_lock(self):
        for name in ('hub.py', 'connector_service.py'):
            shutil.copy2(ROOT / name, self.root / name)
        state = self.root / 'state'
        state.mkdir()
        self.executable('launchctl', '#!/bin/sh\necho called >> "$HOME/unexpected-service-action"\nexit 113\n')
        with (state / 'deployment.lock').open('w') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            for script, action in (('hub.py', 'install-web'), ('hub.py', 'stop-web'),
                                   ('hub.py', 'configure'), ('connector_service.py', 'stop'),
                                   ('connector_service.py', 'converge'), ('rollup_service.py', 'install'),
                                   ('rollup_service.py', 'uninstall')):
                result = subprocess.run([sys.executable, str(self.root / script), action], env=self.env,
                                        capture_output=True, text=True, timeout=10)
                if script == 'rollup_service.py' and sys.platform != 'darwin':
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn('no service changed', result.stdout + result.stderr)
                    continue
                self.assertNotEqual(result.returncode, 0, script + action)
                self.assertIn('holds state/deployment.lock', result.stderr)
            (self.root / 'hub.py').write_text("print('standalone')\n")
            for script, action in (('agent-monitor', 'start'), ('agent-monitor', 'stop'),
                                   ('agent-monitor', 'restart'), ('uninstall.sh', 'all')):
                result = self.run_script(script, action)
                self.assertNotEqual(result.returncode, 0, result.stderr)
                self.assertIn('holds state/deployment.lock', result.stderr)
        self.assertFalse((self.home / 'unexpected-service-action').exists())

    def test_linux_open_uses_desktop_or_prints_url(self):
        # Configured Hub path avoids starting any server or accessing account state.
        (self.root / 'hub.py').write_text("print('http://fixture:39001')\n")
        self.executable('uname', '#!/bin/sh\necho Linux\n')
        self.executable('open', '#!/bin/sh\necho forbidden >&2\nexit 99\n')
        self.executable('xdg-open', '#!/bin/sh\necho "$1" > "$HOME/opened"\n')
        result = self.run_script('agent-monitor', 'open')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('http://fixture:39001', result.stdout)
        self.assertFalse((self.home / 'opened').exists())
        self.env['DISPLAY'] = ':fixture'
        result = self.run_script('agent-monitor', 'open')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.home / 'opened').read_text().strip(), 'http://fixture:39001')

    def test_linux_ssh_open_keeps_url_when_tailscale_is_unavailable(self):
        (self.root / 'hub.py').write_text('')
        (self.root / 'process_identity.py').write_text('import sys\nif sys.argv[1] == "pid": print(1)\n')
        (self.root / 'state').mkdir()
        (self.root / 'state/port').write_text('39001')
        self.executable('uname', '#!/bin/sh\necho Linux\n')
        self.executable('hostname', '#!/bin/sh\necho fixture\n')
        self.executable('curl', '#!/bin/sh\necho \'{"stale":false,"refresh_request":1}\'\n')
        self.env['SSH_CONNECTION'] = '192.0.2.1 555 192.0.2.2 22'
        for reply in ('exit 127', 'echo \'{"Self":{"DNSName":"fixture.example."}}\''):
            with self.subTest(tailscale=reply):
                self.executable('tailscale', '#!/bin/sh\n' + reply + '\n')
                result = self.run_script('agent-monitor', 'open')
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn('http://fixture:39001', result.stdout)

    def test_web_uninstall_preserves_links_when_stop_fails(self):
        (self.root / 'hub.py').write_text('')
        (self.root / 'agent-monitor').write_text('#!/bin/sh\necho "identity unverified" >&2\nexit 2\n')
        self.executable('launchctl', '#!/bin/sh\nexit 113\n')
        link_dir = self.home / '.local/bin'
        link_dir.mkdir(parents=True)
        for name in ('agent-monitor', 'ip-check'):
            (link_dir / name).symlink_to(self.root / name)
        result = self.run_script('uninstall.sh', 'web')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('identity unverified', result.stderr)
        self.assertTrue(all((link_dir / name).is_symlink() for name in ('agent-monitor', 'ip-check')))

    def test_connector_status_queries_loaded_state_without_plist(self):
        self.executable('uname', '#!/bin/sh\necho Darwin\n')
        (self.root / 'agent-monitor').write_text('#!/bin/sh\necho "loaded owner unverified" >&2\nexit 2\n')
        result = self.run_script('status.sh', 'connector')
        self.assertEqual(result.returncode, 2)
        self.assertIn('loaded owner unverified', result.stderr)
