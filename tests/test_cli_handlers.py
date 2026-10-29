"""Public CLI adapters keep domain operations behind explicit invocation."""
import contextlib
import importlib
import io
import json
import shutil
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock


class CLIHandlerTests(unittest.TestCase):
    def test_foreign_checkout_in_cwd_cannot_shadow_the_invoked_cli(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            foreign = Path(directory)
            (foreign / "agent_monitor").mkdir()
            (foreign / "agent_monitor/__init__.py").write_text("raise RuntimeError('wrong checkout')\n")
            (foreign / "project_paths.py").write_text("raise RuntimeError('wrong paths')\n")
            (foreign / "json.py").write_text("raise RuntimeError('wrong stdlib')\n")
            env = {"PATH": os.environ["PATH"], "HOME": directory,
                   "AGENT_MONITOR_PYTHON": sys.executable, "PYTHONDONTWRITEBYTECODE": "1"}
            for command in (("export", "--help"), ("machines", "accept", "--help")):
                with self.subTest(command=command):
                    result = subprocess.run([str(root / "agent-monitor"), *command],
                                            cwd=foreign, env=env, text=True,
                                            capture_output=True, timeout=10)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn("usage: agent-monitor", result.stdout)

    def test_public_launcher_preserves_symlinks_arguments_and_caller_directory(self):
        source = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory).resolve()
            root = base / "checkout with spaces"
            runtime = root / "agent_monitor/runtime"
            runtime.mkdir(parents=True)
            cli = root / "agent_monitor/cli"
            cli.mkdir()
            (runtime / "service_logs.py").write_text("def initialize(root): pass\n")
            (cli / "export.py").write_text(
                "import json, os, sys; print(json.dumps([os.getcwd(), sys.argv[1:]]))\n")
            shutil.copy2(source / "agent-monitor", root / "agent-monitor")
            shutil.copy2(source / "agent_monitor/runtime/cli.sh", runtime / "cli.sh")
            link = base / "linked-cli"
            link.symlink_to("checkout with spaces/agent-monitor")
            env = {"PATH": os.environ["PATH"], "HOME": directory,
                   "AGENT_MONITOR_PYTHON": sys.executable, "PYTHONDONTWRITEBYTECODE": "1"}
            for entry in (root / "agent-monitor", link):
                with self.subTest(entry=entry.name):
                    result = subprocess.run([str(entry), "export", "--out", "relative path"],
                                            cwd=base, env=env, capture_output=True,
                                            text=True, timeout=10)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(json.loads(result.stdout),
                                     [str(base), ["--out", "relative path"]])

    def test_import_does_not_dispatch_commands_or_parse_arguments(self):
        names = (
            "launch_web", "compile_sources", "rollup_run", "rollup_check",
            "rollup_adopt_timezone", "machine_retire", "machine_accept",
            "export", "session_quota",
        )
        with mock.patch.object(sys, "argv", ["unrelated", "--invalid"]), \
                mock.patch("argparse.ArgumentParser.parse_args", side_effect=AssertionError("parsed on import")), \
                mock.patch("os.fork", side_effect=AssertionError("forked on import")):
            for name in names:
                with self.subTest(handler=name):
                    module = importlib.import_module("agent_monitor.cli." + name)
                    importlib.reload(module)
                    self.assertTrue(callable(module.main))

    def test_machine_binding_confirmation_precedes_sync(self):
        from agent_monitor.cli import machine_accept
        from agent_monitor.snapshots import machine_config, sync

        machine = SimpleNamespace(name="fixture", ssh_host="fixture-host", is_self=False)
        config = SimpleNamespace(by_name={"fixture": machine})
        meta = dict(source_host_identity="fixture-id", row_count=2, schema_version=1,
                    data_start_date="2026-01-01")
        for answer, expected_exit in (("n\n", 1), ("", 1), ("y\n", None)):
            with self.subTest(answer=answer), \
                    mock.patch.object(sys, "argv", ["agent-monitor machines accept", "fixture"]), \
                    mock.patch.object(sys, "stdin", io.StringIO(answer)), \
                    contextlib.redirect_stdout(io.StringIO()), \
                    mock.patch.object(machine_config, "load_machine_config", return_value=config), \
                    mock.patch.object(sync, "sync_machine", return_value=SimpleNamespace(meta=meta)) as collect:
                if expected_exit is None:
                    machine_accept.main()
                    collect.assert_called_once_with(machine, accept_first_use_ssh_target=True)
                else:
                    with self.assertRaises(SystemExit) as raised:
                        machine_accept.main()
                    self.assertEqual(raised.exception.code, expected_exit)
                    collect.assert_not_called()


if __name__ == "__main__":
    unittest.main()
