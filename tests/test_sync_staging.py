"""Local-only lifecycle faults; never contact an SSH host or provider."""
import fcntl
import os
from pathlib import Path
import select
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

from agent_monitor.snapshots import staging

ROOT = Path(__file__).resolve().parents[1]


class StagingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="sync-staging-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def child(self, code, *args):
        child = subprocess.Popen([sys.executable, "-B", "-c", code, *map(str, args)],
                                 cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 text=True, start_new_session=True)
        def stop():
            if child.poll() is None:
                os.killpg(child.pid, signal.SIGKILL)
            child.communicate(timeout=5)
        self.addCleanup(stop)
        return child

    def line(self, child):
        self.assertTrue(select.select([child.stdout], [], [], 8)[0], "child readiness timeout")
        return child.stdout.readline().strip()

    def wait_for(self, predicate, seconds=10):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(0.02)
        self.fail("bounded condition did not occur")

    def test_normal_failure_and_busy(self):
        for fail in (False, True):
            with self.subTest(fail=fail):
                try:
                    with staging.Staging(self.root) as stage:
                        (stage.path / "bundle").mkdir()
                        (stage.path / "bundle" / "payload").write_text("keep until finished")
                        self.assertEqual(staging.reap(self.root), 0)
                        self.assertTrue(stage.path.exists())
                        if fail:
                            raise ValueError("business failure")
                except ValueError as exc:
                    self.assertEqual(str(exc), "business failure")
                self.assertFalse(stage.path.exists())

    def test_retained_reasons_are_summarized_with_bounded_examples(self):
        stages = [staging.Staging(self.root).__enter__() for _ in range(3)]
        (stages[0].path / staging.OWNER).write_text("unknown")
        (stages[1].path / staging.LEASE).unlink()
        (stages[1].path / staging.LEASE).touch()
        (stages[2].path / staging.TRANSFER).touch()
        for stage in stages:
            stage._close()
        for index in range(5):
            (self.root / f".sync-legacy-{index}").mkdir()
        with self.assertLogs(staging.LOGGER, level="WARNING") as captured:
            self.assertEqual(staging.reap(self.root), 0)
        self.assertEqual(len(captured.output), 1)
        message = captured.output[0]
        for reason in ("missing-ownership=5", "unknown-protocol=1", "identity-changed=1", "unfinished-transfer=1"):
            self.assertIn(reason, message)
        self.assertIn(str(self.root), message)
        self.assertEqual(sum(f".sync-legacy-{index}" in message for index in range(5)), 2)
        self.assertTrue(all(stage.path.exists() for stage in stages))
        self.assertEqual(len(list(self.root.glob(".sync-legacy-*"))), 5)

    def test_busy_and_successful_cleanup_do_not_warn(self):
        with self.assertNoLogs(staging.LOGGER, level="WARNING"):
            with staging.Staging(self.root) as stage:
                self.assertEqual(staging.reap(self.root), 0)
                self.assertTrue(stage.path.exists())
            self.assertFalse(stage.path.exists())
            self.assertEqual(staging.reap(self.root), 0)

    def test_killed_self_producer_reaped(self):
        child = self.child("""
import sys,time
from pathlib import Path
from agent_monitor.snapshots.staging import Staging
with Staging(Path(sys.argv[1])) as s:
 (s.path/'bundle').mkdir()
 print(s.path,flush=True)
 time.sleep(20)
""", self.root)
        directory = Path(self.line(child))
        self.assertEqual(staging.reap(self.root), 0)
        child.kill()
        child.wait(timeout=5)
        self.assertEqual(staging.reap(self.root), 1)
        self.assertFalse(directory.exists())

    def test_legacy_unknown_and_links_preserved(self):
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "sentinel").write_text("unchanged")
        legacy = self.root / ".sync-legacy"
        legacy.mkdir()
        (self.root / ".sync-link").symlink_to(outside)
        with staging.Staging(self.root) as s:
            (s.path / "bundle").symlink_to(outside)
        self.assertTrue(s.path.exists())
        self.assertEqual((outside / "sentinel").read_text(), "unchanged")
        with staging.Staging(self.root) as other:
            pass
        self.assertFalse(other.path.exists())
        self.assertTrue(legacy.exists())
        self.assertTrue((self.root / ".sync-link").is_symlink())

    def test_lease_symlink_unknown_protocol_and_inode_replacement(self):
        for mode in ("link", "protocol", "inode"):
            with self.subTest(mode=mode):
                stage = staging.Staging(self.root).__enter__()
                if mode == "link":
                    (stage.path / staging.LEASE).unlink()
                    (stage.path / staging.LEASE).symlink_to(self.root / staging.REGISTRY)
                elif mode == "protocol":
                    (stage.path / staging.OWNER).write_text("unknown")
                else:
                    # An open lease still identifies the original inode.
                    held = stage.lease
                    (stage.path / staging.LEASE).unlink()
                    (stage.path / staging.LEASE).touch()
                    self.assertFalse(staging._same(held, stage.directory, staging.LEASE))
                stage.__exit__(None, None, None)
                self.assertTrue(stage.path.exists())

    def test_payload_cleanup_failure_keeps_retry_metadata(self):
        stage = staging.Staging(self.root).__enter__()
        (stage.path / "bundle").mkdir()
        (stage.path / "bundle" / "payload").write_text("saved")
        def failure(*args, **kwargs):
            raise PermissionError("fixture")
        with mock.patch.object(staging, "_remove_tree", failure):
            stage.__exit__(None, None, None)
        self.assertTrue((stage.path / staging.OWNER).read_bytes().startswith(staging.PROTOCOL))
        self.assertTrue((stage.path / "bundle" / "payload").exists())
        self.assertEqual(staging.reap(self.root), 1)

    def test_registry_serializes_publication_and_reaper(self):
        child = self.child("""
import os,sys,time
from pathlib import Path
from agent_monitor.snapshots import staging
root=Path(sys.argv[1])
original=staging.os.mkdir
def pause(*a,**k):
 original(*a,**k)
 print('created',flush=True)
 sys.stdin.read(0)
 time.sleep(0.5)
staging.os.mkdir=pause
with staging.Staging(root) as s:
 print('published',flush=True)
 time.sleep(1)
""", self.root)
        self.assertEqual(self.line(child), "created")
        # Reaper cannot pass registry before creator publishes its locked lease.
        start = time.monotonic()
        self.assertEqual(staging.reap(self.root), 0)
        self.assertGreater(time.monotonic() - start, 0.2)
        self.assertEqual(self.line(child), "published")
        self.assertEqual(child.wait(timeout=5), 0)
        self.assertEqual(list(self.root.glob(".sync-????????????????????????????????")), [])

    def test_killed_during_payload_cleanup_is_retryable(self):
        child = self.child("""
import os,sys
from pathlib import Path
from agent_monitor.snapshots import staging
def interrupted(parent,name):
 directory=os.open(name,os.O_RDONLY|os.O_DIRECTORY,dir_fd=parent)
 os.unlink('first',dir_fd=directory)
 os._exit(17)
staging._remove_tree=interrupted
with staging.Staging(Path(sys.argv[1])) as s:
 (s.path/'bundle').mkdir()
 (s.path/'bundle'/'first').write_text('first')
 (s.path/'bundle'/'second').write_text('second')
 print(s.path,flush=True)
""", self.root)
        directory = Path(self.line(child))
        self.assertEqual(child.wait(timeout=5), 17)
        self.assertFalse((directory / "bundle" / "first").exists())
        self.assertTrue((directory / "bundle" / "second").exists())
        self.assertTrue((directory / staging.OWNER).exists())
        self.assertEqual(staging.reap(self.root), 1)

    def test_timeout_retains_intent_and_terminates_transfer_group(self):
        with staging.Staging(self.root) as s:
            pidfile = self.root / "pid"
            result = s.transfer_runner(subprocess.run)(
                [sys.executable, "-c", "import os,sys,time;from pathlib import Path;Path(sys.argv[1]).write_text(str(os.getpid()));time.sleep(30)", str(pidfile)],
                capture_output=True, text=True, check=False, timeout=2)
            self.assertEqual(result.returncode, 125)
            self.assertIn("TimeoutExpired", result.stderr)
            self.assertTrue((s.path / staging.TRANSFER).exists())
            with self.assertRaises(ProcessLookupError):
                os.kill(int(pidfile.read_text()), 0)
        self.assertTrue(s.path.exists())

    def test_transfer_exit_codes_and_output(self):
        for code in (0, 7):
            with staging.Staging(self.root) as s:
                result = s.transfer_runner(subprocess.run)(
                    [sys.executable, "-c", "import sys;print('out');print('err',file=sys.stderr);sys.exit("+str(code)+")"],
                    capture_output=True, text=True, check=False, timeout=5)
                self.assertEqual((result.returncode, result.stdout, result.stderr), (code, "out\n", "err\n"))
                self.assertFalse((s.path / staging.TRANSFER).exists())
            self.assertFalse(s.path.exists())

    @unittest.skipUnless(shutil.which("rsync"), "local rsync required")
    def test_real_rsync_parent_death_and_guardian_death(self):
        # Local executable and local files only. Each fixture transfer has a
        # 12-second upper budget; helper processes are killed/reaped in cleanup.
        source = self.root / "source"
        source.write_bytes(os.urandom(2 * 1024 * 1024))
        for mode in ("parent", "guardian", "guardian-close-lease"):
            with self.subTest(mode=mode):
                kill_guardian = mode != "parent"
                case = self.root / mode
                case.mkdir()
                info = case / "identity"
                child = self.child("""
import os,sys,subprocess
from pathlib import Path
from agent_monitor.snapshots import staging
root,source,info,rsync,mode=sys.argv[1:]
with staging.Staging(Path(root)) as s:
 (s.path/'bundle').mkdir()
 real=subprocess.Popen
 def popen(*a,**k):
  p=real(*a,**k)
  Path(info).write_text(str(p.pid))
  return p
 subprocess.Popen=popen
 print(s.path,flush=True)
 command=[rsync,'--bwlimit=512',source,str(s.path/'bundle'/'copy')]
 wrapper="import os,sys;from pathlib import Path;Path(sys.argv[1]).write_text(str(os.getpid()));"
 if mode=='guardian-close-lease': wrapper+='os.close(int(sys.argv[2]));'
 wrapper+='os.execv(sys.argv[3],sys.argv[3:])'
 command=[sys.executable,'-c',wrapper,str(Path(info).with_name('transfer-pid')),str(s.lease),*command]
 s.transfer_runner(subprocess.run)(command,capture_output=True,text=True,check=True,timeout=12)
""", case, source, info, shutil.which("rsync"), mode)
                directory = Path(self.line(child))
                self.wait_for(info.exists)
                guardian = int(info.read_text())
                transfer_pid_file = case / "transfer-pid"
                self.wait_for(transfer_pid_file.exists)
                transfer_pid = int(transfer_pid_file.read_text())
                def stop_transfer(pid=transfer_pid):
                    try:
                        os.killpg(pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                self.addCleanup(stop_transfer)
                # Require real rsync output to have begun before killing parent.
                self.wait_for(lambda: any(p.stat().st_size > 0 for p in (directory / "bundle").iterdir()))
                child.kill()
                child.wait(timeout=5)
                self.assertEqual(staging.reap(case), 0)
                self.assertTrue(directory.exists())
                if kill_guardian:
                    os.killpg(transfer_pid, signal.SIGSTOP)
                    os.kill(guardian, signal.SIGKILL)
                    self.assertEqual(staging.reap(case), 0)
                    self.assertTrue(directory.exists())
                    os.killpg(transfer_pid, signal.SIGCONT)
                    self.wait_for(lambda: (directory / "bundle" / "copy").exists(), 10)
                    # Even after real rsync completes, lost guardian proof is
                    # retained rather than inferred from PID, mtime or bytes.
                    self.assertEqual(staging.reap(case), 0)
                    self.assertTrue((directory / staging.TRANSFER).exists())
                else:
                    self.wait_for(lambda: not (directory / staging.TRANSFER).exists(), 10)
                    self.assertEqual((directory / "bundle" / "copy").read_bytes(), source.read_bytes())
                    self.wait_for(lambda: staging.reap(case) == 1)
                    self.assertFalse(directory.exists())
                # Reap/terminate every fixture descendant through its owned group.
                try:
                    os.killpg(child.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass


if __name__ == "__main__":
    unittest.main()
