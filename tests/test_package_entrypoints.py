"""Entry migration retains checkout ownership across old and module argv."""
import os
from pathlib import Path
import plistlib
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from agent_monitor.runtime import connector_service, hub, local_web

ROOT = Path(__file__).resolve().parents[1]


class PackageEntrypointTests(unittest.TestCase):
    def test_ip_check_version_uses_its_local_package(self):
        from ip_check import __version__
        with tempfile.TemporaryDirectory() as directory:
            for option in ('--version', '-v', '-V'):
                result = subprocess.run([str(ROOT / 'ip-check'), option], cwd=directory,
                                        env={**os.environ, 'AGENT_MONITOR_PYTHON': sys.executable},
                                        capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.strip(), f'ipcheck {__version__}')

    def test_unified_cli_dispatch_from_foreign_cwd(self):
        with tempfile.TemporaryDirectory() as directory:
            env = {**os.environ, 'HOME': directory, 'AGENT_MONITOR_PYTHON': sys.executable,
                   'PYTHONDONTWRITEBYTECODE': '1'}
            for args, expected in ((['service', 'deploy', '--help'], '--check-only'),
                                   (['service', 'status', '--help'], 'rollup-daemon'),
                                   (['upstream-holds', 'status', 'codex', '--state-root', directory], '未查询上游状态')):
                result = subprocess.run([str(ROOT / 'agent-monitor'), *args], cwd=directory,
                                        env=env, capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(expected, result.stdout)

    def test_local_web_old_module_and_foreign_argv(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'state').mkdir()
            environment = {'HOME': directory, 'PATH': os.defpath, 'PYTHONPATH': str(root)}
            suffix = ['--host', '127.0.0.1', '--port', '39001']
            cases = (
                ([sys.executable, str(root / 'server.py'), *suffix], environment, True),
                ([sys.executable, '-m', 'agent_monitor.http_server', *suffix], environment, True),
                ([sys.executable, '/foreign/server.py', *suffix], environment, False),
                ([sys.executable, '-m', 'agent_monitor.http_server', *suffix], dict(environment, PYTHONPATH='/foreign'), False),
                ([sys.executable, '-m', 'unrelated', *suffix], environment, False),
            )
            for argv, env, accepted in cases:
                with self.subTest(argv=argv, accepted=accepted), mock.patch.object(local_web.process_identity, 'read_owned', return_value=(123, 'fixture')), mock.patch.object(local_web, 'process_context', return_value=(argv, env)):
                    if accepted:
                        self.assertEqual(local_web.capture(root)['port'], 39001)
                    else:
                        with self.assertRaises(ValueError):
                            local_web.capture(root)

    def test_connector_old_module_and_foreign_definitions(self):
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(connector_service, 'ROOT', Path(directory)):
            root = Path(directory)
            path = root / 'connector.plist'
            current = connector_service.definition('http://fixture:39001', root / 'status.json')
            old = dict(current, ProgramArguments=[str(root / '.venv/bin/python'), str(root / 'client_connector.py'), '--server', 'http://fixture:39001', '--state-file', str(root / 'status.json')])
            foreign = dict(current, WorkingDirectory='/foreign')
            for value, accepted in ((old, True), (current, True), (foreign, False), (dict(old, ProgramArguments=['/foreign/python', *old['ProgramArguments'][1:]]), False)):
                with self.subTest(argv=value['ProgramArguments'], accepted=accepted):
                    path.write_bytes(plistlib.dumps(value))
                    if accepted:
                        self.assertEqual(connector_service.owned_definition(path), value)
                    else:
                        with self.assertRaises(ValueError):
                            connector_service.owned_definition(path)

    def test_hub_old_module_and_foreign_checkout(self):
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(hub, 'ROOT', Path(directory)):
            root = Path(directory)
            current = hub.web_plist(root, sys.executable)
            old = dict(current, ProgramArguments=[sys.executable, str(root / 'hub.py'), 'serve'])
            self.assertTrue(hub.owned_web_definition(old))
            self.assertTrue(hub.owned_web_definition(current))
            self.assertFalse(hub.owned_web_definition(dict(current, WorkingDirectory='/foreign')))
            self.assertFalse(hub.owned_web_definition(dict(old, ProgramArguments=[sys.executable, '/foreign/hub.py', 'serve'])))

    def test_public_runtime_rebuild_without_project_venv_outside_checkout(self):
        with tempfile.TemporaryDirectory() as directory:
            env = dict(os.environ, HOME=directory, PYTHONDONTWRITEBYTECODE='1')
            env.pop('PYTHONPATH', None)
            result = subprocess.run([sys.executable, str(ROOT / 'runtime_rebuild.py'), '--help'], cwd=directory, env=env, capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('--python', result.stdout)
