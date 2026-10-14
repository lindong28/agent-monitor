"""Run the shipped remote scripts locally, with only owned fixture processes."""
import fcntl
import json
import os
import select
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

import sync


class RemoteLifetimeTests(unittest.TestCase):
    def cleanup(self, directory):
        return subprocess.run(
            [sys.executable, "-B", "-c", sync._REMOTE_CLEANUP_SCRIPT, "clean", str(directory)],
            capture_output=True, timeout=5,
        )

    def test_success_and_nonzero_keep_export_status(self):
        for code in (0, 7):
            with self.subTest(code=code), tempfile.TemporaryDirectory() as tmp:
                directory = Path(tmp) / "export"
                directory.mkdir()
                result = subprocess.run(
                    [sys.executable, "-B", "-c", sync._REMOTE_EXPORT_SCRIPT, str(directory), "5", sys.executable, "-B", "-c", "print('manifest'); raise SystemExit(%s)" % code],
                    capture_output=True, text=True, timeout=8,
                )
                self.assertEqual(result.returncode, code, result.stderr)
                self.assertEqual(result.stdout.strip(), "manifest")
                self.assertEqual(self.cleanup(directory).returncode, 0)
                self.assertFalse(directory.exists())

    def test_deadline_and_parent_death_end_owned_group(self):
        for failure in ("deadline", "supervisor"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as tmp:
                directory = Path(tmp) / "export"
                directory.mkdir()
                code = (
                    "import json,os,subprocess,sys,time; "
                    "helper=subprocess.Popen([sys.executable,'-B','-c','import time; time.sleep(30)']); "
                    "print(json.dumps([os.getppid(),os.getpid(),helper.pid]),flush=True); time.sleep(30)"
                )
                parent = subprocess.Popen(
                    [sys.executable, "-B", "-c", sync._REMOTE_EXPORT_SCRIPT, str(directory), "1" if failure == "deadline" else "5", sys.executable, "-B", "-c", code],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                )
                pids = []
                try:
                    ready, _, _ = select.select([parent.stdout], [], [], 4)
                    self.assertTrue(ready, "fixture did not announce its owned pids")
                    pids = json.loads(parent.stdout.readline())
                    self.assertEqual(self.cleanup(directory).returncode, 75)
                    self.assertTrue(directory.exists())
                    if failure == "supervisor":
                        parent.kill()
                    parent.communicate(timeout=7)
                    self.assertEqual(parent.returncode, 124 if failure == "deadline" else -signal.SIGKILL)
                    self.assert_stopped(pids)
                    pids = []
                    self.assertEqual(self.cleanup(directory).returncode, 0)
                finally:
                    self.stop_owned(parent, pids)

    def test_export_inherits_lease_if_guardian_dies_first(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp) / "export"
            directory.mkdir()
            parent = subprocess.Popen(
                [sys.executable, "-B", "-c", sync._REMOTE_EXPORT_SCRIPT, str(directory), "5", sys.executable, "-B", "-c", "import os,json,time; print(json.dumps([os.getppid(),os.getpid()]),flush=True); time.sleep(30)"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            )
            pids = []
            try:
                ready, _, _ = select.select([parent.stdout], [], [], 4)
                self.assertTrue(ready)
                pids = json.loads(parent.stdout.readline())
                os.kill(pids[0], signal.SIGKILL)
                self.assertEqual(parent.wait(timeout=3), 125)
                self.assertEqual(self.cleanup(directory).returncode, 75)
                self.assertTrue(directory.exists())
                os.kill(pids[1], signal.SIGKILL)
                self.assert_stopped(pids)
                pids = []
                self.assertEqual(self.cleanup(directory).returncode, 0)
            finally:
                self.stop_owned(parent, pids)

    def test_reaper_retains_active_and_unproven_legacy_directories(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            active, inactive, legacy = [root / ("agent-monitor-export." + name) for name in ("active", "inactive", "legacy")]
            for path in (active, inactive, legacy):
                path.mkdir()
            (active / ".lease").touch()
            (inactive / ".lease").touch()
            for path in (active, inactive, legacy):
                os.utime(path, (time.time() - 7200,) * 2)
            with (active / ".lease").open("r+") as lease:
                fcntl.flock(lease, fcntl.LOCK_EX)
                result = subprocess.run([sys.executable, "-B", "-c", sync._REMOTE_CLEANUP_SCRIPT, "reap", str(root)], capture_output=True, timeout=5)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertTrue(active.exists())
                self.assertTrue(legacy.exists())
                self.assertFalse(inactive.exists())

    def test_real_cli_launcher_preserves_export_lease(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp) / "export"
            directory.mkdir()
            reader = Path(tmp) / "isolated-python"
            reader.write_text(
                "#!" + sys.executable + " -B\n"
                "import json,os,time\n"
                "print(json.dumps([os.getpgrp(),os.getppid(),os.getpid()]),flush=True)\n"
                "time.sleep(30)\n"
            )
            reader.chmod(0o700)
            launcher = Path(sync.__file__).parent / "agent-monitor"
            parent = subprocess.Popen(
                [sys.executable, "-B", "-c", sync._REMOTE_EXPORT_SCRIPT, str(directory), "5", str(launcher), "export", "--out", str(directory / "bundle")],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                env={**os.environ, "AGENT_MONITOR_PYTHON": str(reader)},
            )
            pids = []
            try:
                ready, _, _ = select.select([parent.stdout], [], [], 4)
                self.assertTrue(ready)
                pids = json.loads(parent.stdout.readline())
                os.kill(pids[0], signal.SIGKILL)
                self.assertEqual(parent.wait(timeout=3), 125)
                self.assertEqual(self.cleanup(directory).returncode, 75)
                for pid in pids[1:]:
                    os.kill(pid, signal.SIGKILL)
                self.assert_stopped(pids)
                pids = []
                self.assertEqual(self.cleanup(directory).returncode, 0)
            finally:
                self.stop_owned(parent, pids)

    def assert_stopped(self, pids):
        deadline = time.monotonic() + 3
        while True:
            states = [subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True, timeout=1).stdout.strip() for pid in pids]
            if all(not state or state.startswith("Z") for state in states):
                return
            if time.monotonic() >= deadline:
                self.fail("owned fixture processes still running: %r" % states)
            time.sleep(0.02)

    @staticmethod
    def stop_owned(parent, pids):
        # Captured fixture PIDs only; no searches by command line or user name.
        for pid in pids:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        if parent.poll() is None:
            parent.kill()
        parent.communicate(timeout=5)
