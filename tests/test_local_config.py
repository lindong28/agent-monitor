"""Public installation configuration stays separate from tracked defaults."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class LocalConfigTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        # Real modules, with their imports resolved in this disposable source tree.
        for path in ROOT.glob("*.py"):
            shutil.copy2(path, self.root / path.name)
        for name in ("agent_monitor", "parsers", "ip_check", "proxy_runtime"):
            shutil.copytree(ROOT / name, self.root / name, ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copy2(ROOT / "pricing.json", self.root / "pricing.json")
        for name in ("machines.json", "hub.json"):
            shutil.copy2(ROOT / "examples" / name, self.root / name)
        self.env = dict(os.environ, HOME=str(self.root / "home"),
                        CLAUDE_CONFIG_DIR=str(self.root / "home/.claude"),
                        CODEX_HOME=str(self.root / "home/.codex"))

    def run_source(self, source):
        return subprocess.run([sys.executable, "-c", source], cwd=self.root,
                              env=self.env, capture_output=True, text=True, timeout=30)

    def test_default_and_local_overrides_reach_real_consumers(self):
        source = '''
import inspect, json, machine_config, hub, generation, sync, query_view
path = machine_config.DEFAULT_CONFIG_PATH
assert generation.DEFAULT_CONFIG_PATH == sync.DEFAULT_CONFIG_PATH == path
assert inspect.signature(query_view.QueryView).parameters['config_path'].default == path
assert inspect.signature(generation.retire_machines).parameters['config_path'].default == path
print(json.dumps([path.name, machine_config.load_machine_config().self_machine.name,
                  hub.CONFIG_PATH.name, hub.configuration()['hub']]))
'''
        result = self.run_source(source)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), ["machines.json", "local", "hub.json", "local"])
        (self.root / "machines.local.json").write_text(json.dumps({
            "machines": [{"name": "mine", "ssh_host": "localhost", "self": True}],
            "retired_names": []}))
        config = json.loads((self.root / "hub.json").read_text())
        config.update(hub="mine", machines=["mine"])
        (self.root / "hub.local.json").write_text(json.dumps(config))
        result = self.run_source(source)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), ["machines.local.json", "mine", "hub.local.json", "mine"])
        # Explicit paths must not be redirected to the local overrides.
        result = self.run_source("import machine_config,hub; assert machine_config.load_machine_config('machines.json').self_machine.name == 'local'; assert hub.configuration('hub.json')['hub'] == 'local'")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_invalid_local_file_does_not_fall_back(self):
        for name, statement in (("machines", "import machine_config; machine_config.load_machine_config()"),
                                ("hub", "import hub; hub.configuration()")):
            local = self.root / (name + ".local.json")
            for kind in ("malformed", "broken_symlink"):
                with self.subTest(config=name, kind=kind):
                    if kind == "malformed":
                        local.write_text("invalid json")
                    else:
                        local.symlink_to(self.root / "absent.json")
                    result = self.run_source(statement)
                    self.assertNotEqual(result.returncode, 0)
                    local.unlink()

    def test_retirement_writes_selected_local_file_and_preserves_default(self):
        default = (self.root / "machines.json").read_bytes()
        local = self.root / "machines.local.json"
        local.write_text(json.dumps({"machines": [
            {"name": "mine", "ssh_host": "localhost", "self": True},
            {"name": "remote", "ssh_host": "remote.example", "self": False}], "retired_names": []}))
        result = self.run_source("import generation; generation.retire_machines(['remote'])")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.root / "machines.json").read_bytes(), default)
        result = self.run_source("import machine_config; c=machine_config.load_machine_config(); assert set(c.by_name)=={'mine'}; assert c.retired_names=={'remote'}")
        self.assertEqual(result.returncode, 0, result.stderr)
