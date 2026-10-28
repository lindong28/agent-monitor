"""Source admission and paths must survive internal module extraction."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from agent_monitor import source_inventory
import project_paths
from agent_monitor.runtime import service_runtime


class SourceInventoryTests(unittest.TestCase):
    def test_nested_runtime_sources_compile_and_change_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for package in source_inventory.PACKAGE_ROOTS:
                with self.subTest(package=package):
                    path = root / package / "nested/component.py"
                    path.parent.mkdir(parents=True)
                    before = service_runtime.signature(root)
                    path.write_text("value = 1\n")
                    self.assertIn(path, source_inventory.python_sources(root))
                    self.assertNotEqual(before, service_runtime.signature(root))
                    source_inventory.compile_sources(root)
                    good = service_runtime.signature(root)
                    path.write_text("value = (\n")
                    with self.assertRaises(SyntaxError):
                        source_inventory.compile_sources(root)
                    self.assertNotEqual(good, service_runtime.signature(root))
                    path.unlink()
                    self.assertEqual(before, service_runtime.signature(root))

    def test_runtime_outputs_and_tests_are_not_python_sources(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            before = service_runtime.signature(root)
            for relative in ("state/generated.py", ".venv/installed.py", "tests/broken.py",
                             "web/vendor/helper.py", "agent_monitor/__pycache__/module.pyc"):
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("not executable Python !!!")
            self.assertEqual([], source_inventory.python_sources(root))
            source_inventory.compile_sources(root)
            self.assertEqual(before, service_runtime.signature(root))

    def test_deployment_configuration_is_distinct_from_compile_inventory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            default = root / "hub.json"
            local = root / "hub.local.json"
            default.write_text("{}")
            before = service_runtime.signature(root)
            local.write_text('{"role":"hub"}')
            self.assertNotEqual(before, service_runtime.signature(root))
            self.assertIn(local, source_inventory.deployment_sources(root))
            self.assertNotIn(default, source_inventory.deployment_sources(root))
            self.assertEqual([], source_inventory.python_sources(root))

    def test_real_git_pathspecs_include_added_package_code_but_not_state(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            def git(*args):
                return subprocess.run(["git", "-C", str(root), *args], check=True,
                                      capture_output=True, text=True).stdout
            git("init", "-q")
            state = root / "state/generated.py"
            state.parent.mkdir()
            state.write_text("synthetic state")
            args = ("status", "--porcelain=v1", "--untracked-files=all", "--",
                    *source_inventory.authority_pathspecs())
            self.assertEqual("", git(*args))
            for package in source_inventory.PACKAGE_ROOTS:
                path = root / package / "nested/new.py"
                path.parent.mkdir(parents=True)
                path.write_text("value = 1\n")
                self.assertIn(str(path.relative_to(root)), git(*args))
                path.unlink()
                self.assertEqual("", git(*args))


class ProjectPathsTests(unittest.TestCase):
    def test_state_symlink_and_cwd_do_not_change_locations(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            for name in ("checkout", "other-checkout"):
                root = base / name
                root.mkdir()
                data = base / (name + "-data")
                data.mkdir()
                (root / "state").symlink_to(data, target_is_directory=True)
                expected = root / "state/lock"
                previous = Path.cwd()
                try:
                    os.chdir(data)
                    self.assertEqual(expected, project_paths.state_path("lock", root=root))
                    self.assertEqual(root / "web/app.js", project_paths.resource_path("web", "app.js", root=root))
                    self.assertEqual(Path(__file__).resolve().parents[1], project_paths.project_root())
                finally:
                    os.chdir(previous)
                self.assertTrue((root / "state").is_symlink())
                self.assertFalse(expected.exists())
