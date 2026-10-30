"""Collector lifecycle against isolated state and a fake launchctl executable."""
import contextlib
import io
import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from agent_monitor.runtime import rollup_service as service

SOURCE = Path(__file__).resolve().parents[1]


class RollupServiceTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.home = Path(temp.name)
        self.root = self.home / "checkout with spaces"
        self.root.mkdir()
        self.bin = self.home / "bin"
        self.bin.mkdir()
        fake = self.bin / "launchctl"
        fake.write_text("#!" + sys.executable + "\n" + r'''import json, os, pathlib, plistlib, sys
h = pathlib.Path.home()
op = sys.argv[1]
with (h / "calls").open("a") as stream:
    stream.write(op + "\n")
loaded = h / "loaded.json"
if op == "print":
    rc = int(os.environ.get("PRINT_RC", "0"))
    if rc: sys.exit(rc)
    if not loaded.exists(): sys.exit(113)
    data = json.loads(loaded.read_text())
    print("program = " + data.get("Program", data["ProgramArguments"][0]))
    if not os.environ.get("OMIT_ARGUMENTS"):
        print("arguments = {")
        for argument in data["ProgramArguments"]: print("    " + argument)
        print("}")
    for key, value in json.loads(os.environ.get("JOB_FIELDS", "{}")).items():
        print(key + " = " + str(value))
    print("run interval = " + str(data["StartInterval"]) + " seconds")
elif op == "bootstrap":
    if os.environ.get("FAIL_BOOTSTRAP"): sys.exit(7)
    if not os.environ.get("BOOTSTRAP_NO_LOAD"):
        loaded.write_text(json.dumps(plistlib.loads(pathlib.Path(sys.argv[3]).read_bytes())))
elif op == "bootout":
    if os.environ.get("FAIL_BOOTOUT"): sys.exit(8)
    if not os.environ.get("BOOTOUT_NO_UNLOAD"): loaded.unlink()
else: sys.exit(9)
''')
        fake.chmod(0o755)
        self.env = dict(HOME=str(self.home), CLAUDE_CONFIG_DIR=str(self.home / ".claude"),
                        CODEX_HOME=str(self.home / ".codex"), PATH=str(self.bin) + os.pathsep + os.environ["PATH"])
        for ctx in (patch.dict(os.environ, self.env), patch.object(service, "ROOT", self.root),
                    patch.object(service.platform, "system", return_value="Darwin")):
            ctx.start()
            self.addCleanup(ctx.stop)
        self.plist = service.plist_path()

    def invoke(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = service.main(list(args))
        return rc, out.getvalue(), err.getvalue()

    def seed(self, command="refresh", interval=3600, loaded=True):
        data = service.definition(command, interval)
        self.plist.parent.mkdir(parents=True, exist_ok=True)
        self.plist.write_bytes(plistlib.dumps(data))
        if loaded:
            (self.home / "loaded.json").write_text(json.dumps(data))
        return data

    def mutations(self):
        path = self.home / "calls"
        return [x for x in path.read_text().splitlines() if x != "print"] if path.exists() else []

    def test_install_repeat_update_and_remove_preserve_state(self):
        state = self.home / "persistent-state"
        state.mkdir()
        lock = state / "rollup.db.lock"
        lock.write_text("held")
        inode = lock.stat().st_ino
        (self.root / "state").symlink_to(state, target_is_directory=True)
        for args, token in [((), "installed"), ((), "present"),
                            (("--command", "collect", "--interval", "120"), "updated")]:
            rc, out, err = self.invoke("install", *args)
            self.assertEqual((rc, out.strip()), (0, token), err)
        self.assertEqual(self.mutations(), ["bootstrap", "bootout", "bootstrap"])
        self.assertEqual(plistlib.loads(self.plist.read_bytes())["ProgramArguments"][-1], "collect")
        self.assertEqual(self.invoke("uninstall")[0], 0)
        self.assertEqual(self.invoke("uninstall")[0], 0)
        self.assertTrue((self.root / "state").is_symlink())
        self.assertEqual(lock.stat().st_ino, inode)
        self.assertEqual(lock.read_text(), "held")

    def test_unknown_query_never_changes_definition_or_job(self):
        self.seed()
        before = self.plist.read_bytes()
        loaded = (self.home / "loaded.json").read_bytes()
        for code in (3, 7):
            with patch.dict(os.environ, PRINT_RC=str(code)):
                for action in ("install", "status", "uninstall"):
                    rc, out, err = self.invoke(action)
                    self.assertEqual(rc, 1)
                    self.assertIn("unverified", err)
                    self.assertIn("exit=" + str(code), err)
        self.assertEqual(self.mutations(), [])
        self.assertEqual(self.plist.read_bytes(), before)
        self.assertEqual((self.home / "loaded.json").read_bytes(), loaded)

    def test_unknown_fresh_install_does_not_create_configuration(self):
        with patch.dict(os.environ, PRINT_RC="7"):
            self.assertEqual(self.invoke("install")[0], 1)
        self.assertFalse(self.plist.exists())
        self.assertEqual([p.name for p in (self.root / "state").iterdir()], ["deployment.lock"])
        self.assertEqual(self.mutations(), [])

    def test_foreign_and_unrecognized_loaded_jobs_are_preserved(self):
        data = self.seed()
        for argv in (["/other/agent-monitor", "refresh"], [str(self.root / "agent-monitor"), "other"]):
            data["ProgramArguments"] = argv
            (self.home / "loaded.json").write_text(json.dumps(data))
            for action in ("install", "status", "uninstall"):
                self.assertIn("foreign or unknown loaded job", self.invoke(action)[2])
        self.seed()
        with patch.dict(os.environ, OMIT_ARGUMENTS="1"):
            self.assertEqual(self.invoke("install")[0], 1)
        self.assertEqual(self.mutations(), [])

    def test_foreign_and_malformed_definition_are_preserved(self):
        data = self.seed(loaded=False)
        data["Label"] = "foreign"
        for content in (plistlib.dumps(data), b"not a plist", plistlib.dumps([])):
            self.plist.write_bytes(content)
            for action in ("install", "status", "uninstall"):
                self.assertEqual(self.invoke(action)[0], 1)
                self.assertEqual(self.plist.read_bytes(), content)
        self.assertEqual(self.mutations(), [])

    def test_foreign_program_cannot_claim_owned_arguments(self):
        data = self.seed()
        data["Program"] = "/other/executable"
        (self.home / "loaded.json").write_text(json.dumps(data))
        self.assertIn("foreign or unknown loaded job", self.invoke("install")[2])
        self.plist.write_bytes(plistlib.dumps(data))
        self.assertIn("foreign or unknown startup definition", self.invoke("uninstall")[2])
        self.assertEqual(self.mutations(), [])

    def test_loaded_job_without_definition_is_not_adopted(self):
        self.seed()
        self.plist.unlink()
        for action in ("install", "status", "uninstall"):
            self.assertIn("foreign or unknown loaded job", self.invoke(action)[2])
        self.assertFalse(self.plist.exists())
        self.assertEqual(self.mutations(), [])

    def test_bootout_failure_preserves_old_definition(self):
        self.seed()
        before = self.plist.read_bytes()
        with patch.dict(os.environ, FAIL_BOOTOUT="1"):
            rc, _, err = self.invoke("install", "--interval", "120")
        self.assertEqual(rc, 1)
        self.assertIn("exit=8", err)
        self.assertEqual(self.plist.read_bytes(), before)
        self.assertEqual(self.mutations(), ["bootout"])

    def test_bootstrap_failure_retains_saved_definition_for_recovery(self):
        with patch.dict(os.environ, FAIL_BOOTSTRAP="1"):
            rc, _, err = self.invoke("install")
        self.assertEqual(rc, 1)
        self.assertIn("Configuration saved", err)
        self.assertTrue(self.plist.exists())
        self.assertEqual(self.invoke("install")[:2], (0, "updated\n"))

    def test_successful_exit_without_loaded_job_is_not_install_success(self):
        with patch.dict(os.environ, BOOTSTRAP_NO_LOAD="1"):
            rc, _, err = self.invoke("install")
        self.assertEqual(rc, 1)
        self.assertIn("not loaded after bootstrap", err)

    def test_successful_bootout_without_unload_preserves_definition(self):
        self.seed()
        with patch.dict(os.environ, BOOTOUT_NO_UNLOAD="1"):
            rc, _, err = self.invoke("uninstall")
        self.assertEqual(rc, 1)
        self.assertIn("remains loaded", err)
        self.assertTrue(self.plist.exists())

    def test_status_missing_and_installed_unloaded(self):
        self.assertEqual(self.invoke("status")[0], 0)
        self.assertIn("not installed", self.invoke("status")[1])
        self.seed(loaded=False)
        rc, out, _ = self.invoke("status")
        self.assertEqual(rc, 1)
        self.assertIn("installed but not loaded", out)

    def test_status_execution_states(self):
        self.seed()
        for fields, expected, code in [({"runs": 0}, "never run", 1), ({}, "unverified", 1),
                ({"runs": 1, "last exit code": 0}, "exited successfully", 0),
                ({"runs": 1, "last exit code": 9}, "last collection failed", 1),
                ({"pid": 123}, "running; current collection result is unverified", 0),
                ({"pid": 123, "last exit code": 9}, "previous exit=9", 1)]:
            with patch.dict(os.environ, JOB_FIELDS=json.dumps(fields)):
                rc, out, _ = self.invoke("status")
            self.assertEqual(rc, code, out)
            self.assertIn(expected, out)
        self.assertEqual(self.mutations(), [])

    def test_linux_does_not_query_launchd(self):
        with patch.object(service.platform, "system", return_value="Linux"):
            for action in ("install", "status", "uninstall"):
                rc, out, err = self.invoke(action)
                self.assertEqual(rc, 0)
                self.assertIn("not applicable", out + err)
        self.assertFalse((self.home / "calls").exists())

    def test_launch_timeout_is_bounded_and_not_missing(self):
        with patch.object(service.subprocess, "run", side_effect=subprocess.TimeoutExpired("launchctl", 15)) as run:
            rc, _, err = self.invoke("install")
        self.assertEqual(rc, 1)
        self.assertIn("timed out", err)
        self.assertEqual(run.call_args.kwargs["timeout"], 15)
        self.assertFalse(self.plist.exists())

    def test_missing_executable_is_not_missing_job(self):
        with patch.object(service.subprocess, "run", side_effect=FileNotFoundError(2, "missing")):
            self.assertIn("errno=2", self.invoke("status")[2])

    def test_shell_entrypoints_propagate_unknown_status(self):
        shutil.copytree(SOURCE / "agent_monitor", self.root / "agent_monitor", ignore=shutil.ignore_patterns("__pycache__"))
        for name in ("agent-monitor", "uninstall.sh", "agent_monitor/runtime/rollup_service.py", "agent_monitor/runtime/service_runtime.py", "agent_monitor/runtime/deployment_guard.py"):
            shutil.copy2(SOURCE / name, self.root / name)
        with patch.dict(os.environ, PRINT_RC="7"):
            for name, arguments in (("agent-monitor", ["service", "status", "rollup-daemon"]), ("uninstall.sh", ["rollup-daemon"])):
                result = subprocess.run(["bash", str(self.root / name), *arguments],
                                        capture_output=True, text=True, timeout=10)
                if sys.platform != "darwin":
                    self.assertEqual(result.returncode, 0)
                    self.assertIn("not applicable", result.stdout)
                else:
                    self.assertEqual(result.returncode, 1)
                    self.assertIn("unverified", result.stderr)
        self.assertEqual(self.mutations(), [])


if __name__ == "__main__":
    unittest.main()
