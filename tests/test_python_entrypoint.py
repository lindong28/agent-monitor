"""Child startup preserves caller-relative identities and pins implementation."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from agent_monitor.network import account_egress
from agent_monitor.quota import quota_refresh
from agent_monitor.snapshots import sync_process
from parsers.accounts import Account

ROOT = Path(__file__).resolve().parents[1]
PROBE = "tests.fixtures.entrypoint_context"


class PythonEntrypointTests(unittest.TestCase):
    def test_management_children_preserve_cwd_and_pin_checkout(self):
        from agent_monitor.runtime import service_manager
        for action, module in (('deploy', 'agent_monitor.runtime.deploy'),
                               ('rebuild-runtime', 'agent_monitor.runtime.runtime_rebuild')):
            with self.subTest(action=action), self.context('management') as (_, caller, record, environment):
                # CI runs this test from the checkout's .venv. Rescue must use
                # its base interpreter, which survives replacing that runtime.
                python = sys.executable if action == 'deploy' else str(Path(sys.executable).resolve())
                arguments = ['--expect', 'synthetic'] if action == 'deploy' else ['--python', python]
                def execute(executable, command):
                    self.assertEqual(executable, python)
                    result = subprocess.run(self.probe_command(command, module), capture_output=True,
                                            text=True, timeout=10)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    # execv never returns to the parser on success.
                    raise SystemExit(0)
                with mock.patch.object(service_manager.os, 'execv', side_effect=execute):
                    with self.assertRaises(SystemExit) as completed:
                        service_manager.main([action, *arguments])
                    self.assertEqual(completed.exception.code, 0)
                value = self.assert_context(caller, record, environment)
                self.assertEqual(value['arguments'], arguments)

    @contextmanager
    def context(self, kind):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            caller = root / "caller with spaces"
            caller.mkdir()
            record = root / "context.json"
            environment = {
                "HOME": str(root / "home"),
                # Keep explicit PYTHONPATH independent of the caller directory.
                "PYTHONPATH": str(ROOT),
                "CLAUDE_CONFIG_DIR": "relative-claude-profile",
                "CLAUDE_SECURESTORAGE_CONFIG_DIR": "relative-secure-profile",
                "CODEX_HOME": "relative-codex-profile",
                "ENTRYPOINT_LITERAL": "literal $value with spaces\nand newline",
                "ENTRYPOINT_RECORD": str(record),
                "ENTRYPOINT_KIND": kind,
            }
            for name in ("CLAUDE_CONFIG_DIR", "CLAUDE_SECURESTORAGE_CONFIG_DIR", "CODEX_HOME"):
                path = caller / environment[name]
                path.mkdir()
                (path / "identity.txt").write_text("caller:" + name)
            # Retaining -c's cwd entry also shadows dependencies such as json.
            for name in ("agent_monitor", "tests", "project_paths", "json"):
                (caller / (name + ".py")).write_text("raise AssertionError('foreign cwd module loaded')\n")
            previous = Path.cwd()
            try:
                os.chdir(caller)
                with mock.patch.dict(os.environ, environment):
                    yield root, caller, record, environment
            finally:
                os.chdir(previous)

    def assert_context(self, caller, record, environment):
        value = json.loads(record.read_text())
        self.assertEqual(value["identities"], {
            name: "caller:" + name for name in
            ("CLAUDE_CONFIG_DIR", "CLAUDE_SECURESTORAGE_CONFIG_DIR", "CODEX_HOME")})
        self.assertEqual(value["cwd"], str(caller))
        for name, content in value["environment"].items():
            self.assertEqual(content, environment[name], name)
        self.assertEqual(value["implementation"], str(ROOT / "tests/fixtures/entrypoint_context.py"))
        return value

    @staticmethod
    def probe_command(command, module):
        command = list(command)
        command[command.index(module)] = PROBE
        return command

    def test_quota_reader_preserves_relative_identity_and_environment(self):
        popen = subprocess.Popen
        def spawn(command, **kwargs):
            return popen(self.probe_command(command, "agent_monitor.quota.quota_refresh"), **kwargs)
        with self.context("quota") as (root, caller, record, environment), \
                mock.patch.object(quota_refresh, "ROOT", root), \
                mock.patch.object(quota_refresh.network_policy, "child_environment", side_effect=dict), \
                mock.patch.object(quota_refresh.subprocess, "Popen", side_effect=spawn):
            reading = quota_refresh.load_rate_limits("codex", Account("synthetic"))
            self.assertEqual(reading.five_hour_pct, 12)
            value = self.assert_context(caller, record, environment)
            self.assertEqual(value["arguments"][0], "codex")

    def test_egress_probe_preserves_relative_context_and_explicit_environment(self):
        run = subprocess.run
        def launch(command, **kwargs):
            return run(self.probe_command(command, "agent_monitor.network.account_egress"), **kwargs)
        with self.context("egress") as (_, caller, record, environment), \
                mock.patch.object(account_egress.subprocess, "run", side_effect=launch):
            self.assertEqual(account_egress._run_probe("chatgpt.com", dict(os.environ)), {"host": "chatgpt.com"})
            self.assert_context(caller, record, environment)

    def test_supervised_module_preserves_relative_context_and_ignores_foreign_modules(self):
        with self.context("worker") as (_, caller, record, environment):
            with sync_process.supervised_worker(module=PROBE) as process:
                self.assertTrue(process.stdout.readline())
                self.assertEqual(process.wait(timeout=3), 0)
            value = self.assert_context(caller, record, environment)
            self.assertEqual(value["arguments"], ["--worker"])

    def test_supervised_script_retains_original_cwd(self):
        with self.context("worker") as (_, caller, record, environment):
            with sync_process.supervised_worker(ROOT / "tests/fixtures/entrypoint_context.py") as process:
                self.assertTrue(process.stdout.readline())
                self.assertEqual(process.wait(timeout=3), 0)
            self.assert_context(caller, record, environment)
