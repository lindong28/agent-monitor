"""Isolated lifecycle acceptance; real child processes/HTTP/locks, no provider stores."""
from contextlib import ExitStack, redirect_stdout
import fcntl
import io
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from agent_monitor.runtime import deployment_guard
from agent_monitor.runtime import local_web
from agent_monitor.runtime import process_identity
from agent_monitor.runtime import runtime_rebuild
from agent_monitor.runtime import service_manager
from agent_monitor.runtime import service_runtime

SOURCE = Path(__file__).resolve().parents[1]


class IsolatedCheckout(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name).resolve()
        self.root = self.home / 'repo'
        self.root.mkdir()
        (self.home / 'retained-state').mkdir()
        (self.root / 'state').symlink_to(self.home / 'retained-state', target_is_directory=True)
        (self.root / '.venv/bin').mkdir(parents=True)
        (self.root / '.venv/bin/python').symlink_to(sys.executable)
        shutil.copytree(SOURCE / 'agent_monitor', self.root / 'agent_monitor', ignore=shutil.ignore_patterns('__pycache__'))
        for name in ('project_paths.py', 'agent-monitor', 'agent_monitor/runtime/deployment_guard.py', 'agent_monitor/runtime/process_identity.py', 'agent_monitor/runtime/service_runtime.py', 'agent_monitor/runtime/local_web.py'):
            shutil.copy2(SOURCE / name, self.root / name)
        (self.root / 'agent_monitor/runtime/hub.py').write_text("def is_hub(): return False\ndef enabled(): return False\nif __name__ == '__main__': print('standalone')\n")
        (self.root / 'agent_monitor/http_server.py').write_text('''import argparse, json
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import TCPServer
class FixtureServer(HTTPServer):
    server_bind = TCPServer.server_bind  # The fixture does not need reverse DNS.
from pathlib import Path
from agent_monitor.runtime import service_runtime
p = argparse.ArgumentParser(); p.add_argument('--host'); p.add_argument('--port', type=int); a = p.parse_args()
r = Path(__file__).resolve().parents[1]
identity = {'source_root': str(r), 'deployment_signature': service_runtime.signature(r)}
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        data = json.dumps(identity).encode(); self.send_response(200); self.end_headers(); self.wfile.write(data)
    def log_message(self, *args): pass
FixtureServer((a.host, a.port), Handler).serve_forever()
''')
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            port = listener.getsockname()[1]
        self.stack.enter_context(mock.patch.dict(os.environ, HOME=str(self.home), AGENT_MONITOR_BIND='127.0.0.1', AGENT_MONITOR_PORT=str(port), AGENT_MONITOR_PYTHON=str(self.root / '.venv/bin/python')))
        for module in (service_manager, runtime_rebuild):
            self.stack.enter_context(mock.patch.object(module, 'ROOT', self.root))
        self.addCleanup(self.stop)

    def stop(self):
        # Direct process owner teardown still works after an intentionally broken runtime.
        try:
            process_identity.stop(self.root / 'state', self.root)
        except (OSError, process_identity.IdentityUnknown):
            pass

    def start(self):
        result = subprocess.run([str(self.root / 'agent-monitor'), 'local', 'start'], capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_capture_preserves_full_environment_and_private_recipe(self):
        os.environ['MANAGEMENT_TEST_VALUE'] = 'spaces and = signs'
        self.addCleanup(os.environ.pop, 'MANAGEMENT_TEST_VALUE', None)
        self.start()
        recipe = local_web.capture(self.root)
        self.assertEqual(recipe['environment']['MANAGEMENT_TEST_VALUE'], 'spaces and = signs')
        self.assertEqual(recipe['host'], '127.0.0.1')
        path = self.root / 'state/local-web-launch.json'
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(json.loads(path.read_text()), recipe)

    def test_converge_stale_process_then_repeat_retains_pid_and_disabled_stays_disabled(self):
        with mock.patch('agent_monitor.hub_config.is_hub', return_value=False):
            local_web.converge(self.root)
            self.assertIsNone(process_identity.read_owned(self.root / 'state', self.root))
            self.start()
            before = process_identity.read_owned(self.root / 'state', self.root)[0]
            port = (self.root / 'state/port').read_text()
            (self.root / 'version.py').write_text('VERSION = 2\n')
            self.assertEqual(service_manager.local_status(), 1)
            local_web.converge(self.root)
            after = process_identity.read_owned(self.root / 'state', self.root)[0]
            self.assertNotEqual(before, after)
            self.assertEqual((self.root / 'state/port').read_text(), port)
            self.assertEqual(service_manager.local_status(), 0)
            local_web.converge(self.root)
            self.assertEqual(process_identity.read_owned(self.root / 'state', self.root)[0], after)

    def test_busy_profile_refuses_restart_and_releases_catalog_after_exception(self):
        self.start()
        pid = process_identity.read_owned(self.root / 'state', self.root)[0]
        profile = self.root / 'state/codex-accounts/a'
        profile.mkdir(parents=True)
        (profile / 'profile.json').write_text('{}')
        with (profile / '.lock').open('a+') as stream:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaisesRegex(ValueError, 'active'):
                service_manager.restart('local-web')
        self.assertEqual(process_identity.read_owned(self.root / 'state', self.root)[0], pid)
        with local_web.account_quiescence(self.root):
            check = subprocess.run([sys.executable, '-c', 'import fcntl,sys; f=open(sys.argv[1], "a+"); fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)', str(profile.parent / '.catalog.lock')], capture_output=True, timeout=5)
            self.assertNotEqual(check.returncode, 0)
        check = subprocess.run([sys.executable, '-c', 'import fcntl,sys; f=open(sys.argv[1], "a+"); fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)', str(profile.parent / '.catalog.lock')], capture_output=True, timeout=5)
        self.assertEqual(check.returncode, 0)

    def test_unknown_process_is_not_stopped(self):
        with mock.patch('agent_monitor.runtime.local_web.process_identity.read_owned', side_effect=process_identity.IdentityUnknown('foreign')):
            with self.assertRaises(process_identity.IdentityUnknown):
                local_web.converge(self.root)

    def install_fixture(self, fail=False):
        (self.root / 'agent_monitor/runtime/install.sh').write_text('#!/bin/bash\n' + deployment_guard.BODY_MARKER +
            ('exit 7\n' if fail else '"$AGENT_MONITOR_PYTHON" -m venv --without-pip .venv\n'))
        shutil.copy2(SOURCE / 'install.sh', self.root / 'install.sh')
        self.stack.enter_context(mock.patch('agent_monitor.runtime.runtime_rebuild.deploy.preflight', return_value='a' * 40))
        self.stack.enter_context(mock.patch('agent_monitor.runtime.runtime_rebuild.capture_jobs', return_value=[]))

    def test_real_environment_rebuild_restores_only_running_local_web_and_state_inodes(self):
        self.install_fixture()
        self.start()
        state = self.root / 'state'
        lock = state / 'rollup.db.lock'
        lock.write_text('retained')
        before = lock.stat().st_ino
        (self.root / '.venv/original').write_text('old')
        self.assertEqual(runtime_rebuild.rebuild(Path(sys.executable)), 0)
        self.assertTrue(state.is_symlink())
        self.assertEqual(lock.stat().st_ino, before)
        self.assertEqual(service_manager.local_status(), 0)
        backups = list((self.root / '.runtime/runtime-backups').glob('rebuild-*'))
        self.assertEqual(len(backups), 1)
        self.assertTrue((backups[0] / 'previous-venv/original').exists())
        receipt = json.loads((backups[0] / 'transaction.json').read_text())
        self.assertEqual(receipt['phase'], 'complete')
        self.assertEqual(receipt['jobs'], [])

    def test_failed_build_restores_original_runtime_and_leaves_disabled_web_disabled(self):
        self.install_fixture(fail=True)
        original = self.root / '.venv/original'
        original.write_text('old')
        with self.assertRaisesRegex(ValueError, 'original runtime/services restored'):
            runtime_rebuild.rebuild(Path(sys.executable))
        self.assertEqual(original.read_text(), 'old')
        self.assertIsNone(process_identity.read_owned(self.root / 'state', self.root))
        receipt = next((self.root / '.runtime/runtime-backups').glob('*/transaction.json'))
        self.assertEqual(json.loads(receipt.read_text())['phase'], 'rolled-back')

    def test_partial_restore_attempts_all_original_components(self):
        manifest = {'jobs': [{'label': 'first'}, {'label': 'second'}], 'local_web': {'port': 1}}
        with mock.patch('agent_monitor.runtime.runtime_rebuild.start_job', side_effect=[ValueError('failure'), None]) as start, mock.patch('agent_monitor.runtime.runtime_rebuild.local_web.restore') as restore:
            with self.assertRaisesRegex(ValueError, 'first'):
                runtime_rebuild.restore_components(manifest)
            self.assertEqual(start.call_count, 2)
            restore.assert_called_once()

    def test_new_runtime_readiness_failure_rolls_back_then_restores_original_web(self):
        self.install_fixture()
        self.start()
        (self.root / '.venv/original').write_text('old')
        restore = runtime_rebuild.restore_components
        count = 0
        def replacement(manifest):
            nonlocal count
            count += 1
            restore(manifest)
            if count == 1:
                raise ValueError('injected post-start readiness failure')
        with mock.patch('agent_monitor.runtime.runtime_rebuild.restore_components', side_effect=replacement):
            with self.assertRaisesRegex(ValueError, 'original runtime/services restored'):
                runtime_rebuild.rebuild(Path(sys.executable))
        self.assertEqual(count, 2)
        self.assertEqual((self.root / '.venv/original').read_text(), 'old')
        self.assertEqual(service_manager.local_status(), 0)

    def test_explicit_restore_consumes_backup_without_enabling_disabled_services(self):
        self.install_fixture()
        (self.root / '.venv/original').write_text('old')
        runtime_rebuild.rebuild(Path(sys.executable))
        directory = next((self.root / '.runtime/runtime-backups').glob('rebuild-*'))
        self.assertEqual(runtime_rebuild.recover(directory.name), 0)
        self.assertTrue((self.root / '.venv/original').exists())
        self.assertFalse((directory / 'previous-venv').exists())
        self.assertIsNone(process_identity.read_owned(self.root / 'state', self.root))

    def test_saved_recipe_recovers_stopped_web_and_occupied_port_does_not_drift(self):
        self.start()
        recipe = local_web.capture(self.root)
        local_web.run_cli(self.root, 'stop')
        with socket.socket() as listener:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind(('127.0.0.1', recipe['port']))
            listener.listen()
            with self.assertRaisesRegex(ValueError, 'start failed'):
                service_manager.restart('local-web')
            self.assertIsNone(process_identity.read_owned(self.root / 'state', self.root))
        self.assertEqual(service_manager.restart('local-web'), 0)
        self.assertEqual(int((self.root / 'state/port').read_text()), recipe['port'])

    def test_status_reports_local_web_on_client_and_continues_after_failure(self):
        output = io.StringIO()
        with mock.patch('agent_monitor.hub_config.is_hub', return_value=False), mock.patch('agent_monitor.hub_config.enabled', return_value=True), mock.patch('agent_monitor.hub_config.configuration', return_value={'url': 'http://central:39001'}), mock.patch('agent_monitor.runtime.service_manager.run', side_effect=[1, 0]) as run, mock.patch('agent_monitor.runtime.service_manager.sys.platform', 'darwin'), mock.patch('agent_monitor.runtime.service_manager.local_status', return_value=1) as local, redirect_stdout(output):
            self.assertEqual(service_manager.status(), 1)
        local.assert_called_once()
        self.assertEqual(run.call_count, 2)
        self.assertIn('http://central:39001 (availability not checked)', output.getvalue())

    def test_status_continues_after_collector_timeout(self):
        with mock.patch('agent_monitor.hub_config.is_hub', return_value=False), mock.patch('agent_monitor.hub_config.enabled', return_value=False), mock.patch('agent_monitor.runtime.service_manager.run', side_effect=[subprocess.TimeoutExpired('collector', 1), 0]) as run, mock.patch('agent_monitor.runtime.service_manager.sys.platform', 'darwin'):
            self.assertEqual(service_manager.status(), 1)
            self.assertEqual(run.call_count, 2)

    def test_status_continues_after_hub_timeout(self):
        with mock.patch('agent_monitor.hub_config.is_hub', return_value=True), mock.patch('agent_monitor.runtime.service_manager.run', side_effect=[subprocess.TimeoutExpired('hub', 1), 0, 0]) as run, mock.patch('agent_monitor.runtime.service_manager.sys.platform', 'darwin'):
            self.assertEqual(service_manager.status(), 1)
            self.assertEqual(run.call_count, 3)

    def test_corrupt_new_interpreter_cannot_prevent_rollback(self):
        self.install_fixture()
        self.start()
        original = self.root / '.venv/original'
        original.write_text('old')
        (self.root / 'agent_monitor/runtime/install.sh').write_text('#!/bin/bash\n' + deployment_guard.BODY_MARKER +
            "mkdir -p .venv/bin\nprintf '#!/bin/sh\\nexit 42\\n' > .venv/bin/python\nchmod +x .venv/bin/python\nexit 7\n")
        with self.assertRaisesRegex(ValueError, 'original runtime/services restored'):
            runtime_rebuild.rebuild(Path(sys.executable))
        self.assertEqual(original.read_text(), 'old')
        self.assertEqual(service_manager.local_status(), 0)

    def test_loaded_argv_does_not_override_foreign_program(self):
        job = {'label': 'fixture', 'definition': {'ProgramArguments': ['/owned/python', '/owned/hub.py', 'serve']}}
        for program in ('/owned/python', '/foreign/python'):
            result = subprocess.CompletedProcess([], 0, f'program = {program}\narguments = {{\n/owned/python\n/owned/hub.py\nserve\n}}\n')
            with mock.patch('agent_monitor.runtime.runtime_rebuild.launch', return_value=result):
                if program == '/owned/python':
                    self.assertIsNotNone(runtime_rebuild.loaded(job))
                else:
                    with self.assertRaisesRegex(ValueError, 'ownership'):
                        runtime_rebuild.loaded(job)
        job['definition']['Program'] = '/foreign/python'
        with mock.patch('agent_monitor.runtime.runtime_rebuild.launch', return_value=result):
            with self.assertRaisesRegex(ValueError, 'ownership'):
                runtime_rebuild.loaded(job)

    def test_foreground_runtime_user_prevents_replacement(self):
        self.install_fixture()
        original = self.root / '.venv/original'
        original.write_text('old')
        child = subprocess.Popen([str(self.root / '.venv/bin/python'), '-c', 'import time; time.sleep(20)'])
        self.addCleanup(child.wait, timeout=5)
        self.addCleanup(child.terminate)
        with self.assertRaisesRegex(ValueError, 'original runtime/services restored'):
            runtime_rebuild.rebuild(Path(sys.executable))
        self.assertEqual(original.read_text(), 'old')

    def test_relative_runtime_user_prevents_replacement(self):
        child = subprocess.Popen(['.venv/bin/python', '-c', 'import time; time.sleep(20)'], cwd=self.root)
        self.addCleanup(child.wait, timeout=5)
        self.addCleanup(child.terminate)
        with self.assertRaisesRegex(ValueError, 'Processes still use'):
            runtime_rebuild.no_runtime_users()


if __name__ == '__main__':
    unittest.main()
