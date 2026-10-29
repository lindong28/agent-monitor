"""Isolated raw-FD capacity tests; never use provider state or change HOME."""
import ast
import fcntl
import os
from pathlib import Path
import shutil
import socketserver
import subprocess
import sys
import time
import unittest

from tests.artifacts import ArtifactTestCase
from unittest import mock

from agent_monitor.runtime import service_logs as logs

ROOT = Path(__file__).resolve().parents[1]


class ServiceLogTests(ArtifactTestCase):
    def setUp(self):
        self.root = self.artifact_directory(prefix="service-logs-")
        (self.root / "state").mkdir()
        self.path = self.root / "state/server.log"
        self.path.write_bytes(b"")
        logs._warn_after = 0

    def child(self, code, *, path=None, env=None):
        with open(path or self.path, "ab", buffering=0) as output:
            return subprocess.run([sys.executable, "-c", code, str(self.root)],
                                  cwd=ROOT, stdout=output, stderr=output,
                                  env=env, timeout=15)

    def test_tail_and_same_inode_append(self):
        self.path.write_bytes(b"old-data-" + b"recent!!")
        inode = self.path.stat().st_ino
        with self.path.open("ab", buffering=0) as writer:
            logs.LogMaintenance(self.path, 8).check()
            os.write(writer.fileno(), b"after")
        self.assertEqual(self.path.stat().st_ino, inode)
        self.assertEqual(self.path.read_bytes(), b"after")
        self.assertEqual(Path(str(self.path)+".1").read_bytes(), b"recent!!")

    def test_multiple_segments(self):
        monitor = logs.LogMaintenance(self.path, 8, 3)
        for marker in (b"a", b"b", b"c"):
            with self.path.open("ab") as out:
                out.write(marker * 9)
            monitor.check(force=True)
        self.assertEqual(Path(str(self.path)+".1").read_bytes(), b"c"*8)
        self.assertEqual(Path(str(self.path)+".2").read_bytes(), b"b"*8)
        self.assertFalse(Path(str(self.path)+".3").exists())
        manual = self.path.with_name('server.log.saved')
        manual.write_bytes(b'keep')
        self.path.write_bytes(b'd'*9)
        logs.LogMaintenance(self.path,8,2).check()
        self.assertFalse(Path(str(self.path)+'.2').exists())
        self.assertEqual(manual.read_bytes(),b'keep')

    def test_bounded_read_and_idle_stat(self):
        self.path.write_bytes(b"x" * (9 * 1024 * 1024))
        original = os.pread
        with mock.patch.object(logs.os, "pread", wraps=original) as reads:
            start = time.perf_counter()
            logs.LogMaintenance(self.path).check()
            elapsed = time.perf_counter()-start
        self.assertEqual(sum(c.args[1] for c in reads.call_args_list), 8*1024*1024)
        monitor = logs.LogMaintenance(self.path)
        with mock.patch.object(Path, "lstat", autospec=True, return_value=self.path.stat()) as stats:
            for _ in range(10000):
                monitor.check()
        self.assertEqual(stats.call_count, 1)
        print("capacity_read_bytes=8388608 elapsed_seconds=%.6f idle_calls=10000 stats=1" % elapsed)

    def test_failure_is_nonfatal_and_limited(self):
        self.path.write_bytes(b"x"*20)
        monitor = logs.LogMaintenance(self.path, 8)
        with mock.patch.object(logs.os, "pread", side_effect=OSError("private detail")), mock.patch.object(logs.sys, "stderr") as output:
            monitor.check(force=True)
            monitor.check(force=True)
        self.assertEqual(self.path.read_bytes(), b"x"*20)
        text = "".join(call.args[0] for call in output.write.call_args_list)
        self.assertEqual(text.count("service continues"), 1)
        self.assertNotIn("private detail", text)
        self.assertEqual(list(self.path.parent.glob(".server.log-*")), [])

    def test_unknown_paths_untouched(self):
        victim = self.root / "victim"
        victim.write_bytes(b"keep"*10)
        self.path.unlink()
        self.path.symlink_to(victim)
        logs.LogMaintenance(self.path, 8).check()
        self.assertEqual(victim.read_bytes(), b"keep"*10)
        self.path.unlink()
        os.link(victim, self.path)
        logs.LogMaintenance(self.path, 8).check()
        self.assertEqual(victim.read_bytes(), b"keep"*10)

    def test_maintenance_contention(self):
        self.path.write_bytes(b"x"*20)
        with self.path.open("rb") as locked:
            fcntl.flock(locked, fcntl.LOCK_EX|fcntl.LOCK_NB)
            logs.LogMaintenance(self.path, 8).check()
            self.assertEqual(self.path.stat().st_size, 20)
        logs.LogMaintenance(self.path, 8).check()
        self.assertEqual(self.path.stat().st_size, 0)

    def test_nonappend_raw_subprocess_and_exec(self):
        self.path.write_bytes(b"before restart\n")
        code = """import os,sys,fcntl,subprocess
from agent_monitor.runtime import service_logs as logs
# Model launchd providing a regular descriptor without O_APPEND.
fcntl.fcntl(1,fcntl.F_SETFL,fcntl.fcntl(1,fcntl.F_GETFL)&~os.O_APPEND)
logs.initialize(sys.argv[1])
assert fcntl.fcntl(1,fcntl.F_GETFL)&os.O_APPEND
os.write(1,b'raw-stdout\\n'); os.write(2,b'raw-stderr\\n')
subprocess.run([sys.executable,'-c',"import os;os.write(2,b'child-stderr\\\\n')"],check=True)
logs.LogMaintenance(os.path.join(sys.argv[1],'state/server.log'),32).check()
os.write(1,b'after-truncate\\n')
os.execv(sys.executable,[sys.executable,'-c',"from agent_monitor.runtime import service_logs as l;import os,sys;l.initialize(sys.argv[1]);assert l._monitors;os.write(2,b'after-exec'+bytes([10]))",sys.argv[1]])
"""
        self.assertEqual(self.child(code).returncode,0)
        self.assertEqual(self.path.read_bytes(), b"after-truncate\n" + b"after-exec\n")
        self.assertIn(b"child-stderr", Path(str(self.path)+".1").read_bytes())

    def test_cli_bootstrap_collector_end_and_restart(self):
        # Real shell dispatch, isolated package replaces only business collector.
        shutil.copy2(ROOT/"agent-monitor", self.root/"agent-monitor")
        runtime = self.root/"agent_monitor/runtime"
        runtime.mkdir(parents=True)
        shutil.copy2(ROOT/"agent_monitor/runtime/service_logs.py", runtime/"service_logs.py")
        shutil.copy2(ROOT/"agent_monitor/runtime/cli.sh", runtime/"cli.sh")
        cli = self.root/"agent_monitor/cli"
        cli.mkdir()
        (cli/"rollup_run.py").write_text("import os;os.write(2,b'collector-output-'*8)\n")
        env = dict(os.environ, AGENT_MONITOR_PYTHON=sys.executable,
                   AGENT_MONITOR_LOG_SEGMENT_BYTES="32")
        path = self.root/"state/rollup-daemon.log"
        path.write_bytes(b"pre-restart")
        for _ in range(2):
            with path.open("ab") as out:
                result = subprocess.run([str(self.root/"agent-monitor"),"rollup"],cwd=self.root,
                                        stdout=out,stderr=out,env=env,timeout=15)
            self.assertEqual(result.returncode,0)
            self.assertLessEqual(path.stat().st_size,32)
            self.assertEqual(Path(str(path)+".1").stat().st_size,32)

    def test_web_loop_uses_existing_service_actions(self):
        # Execute the actual override without importing provider-facing globals.
        tree = ast.parse((ROOT/"agent_monitor/http_server.py").read_text())
        cls = next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=="ThreadingHTTPServer")
        method = next(n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name=="service_actions")
        class Base(socketserver.BaseServer):
            def service_actions(self):
                self.called = True
                self._BaseServer__shutdown_request = True
        module = ast.Module(body=[ast.ClassDef(name="Server",bases=[ast.Name(id="Base",ctx=ast.Load())],keywords=[],body=[method],decorator_list=[])],type_ignores=[])
        scope = dict(Base=Base,service_logs=logs)
        exec(compile(ast.fix_missing_locations(module),"actual-service-actions","exec"),scope)
        with mock.patch.object(logs,"maintain") as maintain, mock.patch.object(socketserver,"_ServerSelector") as selector:
            selector.return_value.__enter__.return_value.select.return_value = []
            server=scope["Server"](None,None)
            server.serve_forever(poll_interval=0)
        self.assertTrue(server.called)
        maintain.assert_called_once()

    def test_direct_web_hub_early_bootstrap(self):
        for relative in ('agent_monitor/http_server.py','agent_monitor/runtime/hub.py'):
            with self.subTest(entry=relative):
                tree = ast.parse((ROOT/relative).read_text())
                prefix = []
                for node in tree.body:
                    if isinstance(node,ast.Import) and node.names[0].name == 'argparse':
                        break
                    prefix.append(node)
                with mock.patch.object(logs,'initialize') as initialize:
                    exec(compile(ast.Module(body=prefix,type_ignores=[]),relative,'exec'),
                         {'__file__':str(self.root/relative)})
                initialize.assert_called_once_with(self.root)

    def test_pipe_and_unrelated_files_unchanged(self):
        path = self.root/"other.log"
        path.write_bytes(b"keep")
        code="from agent_monitor.runtime import service_logs as l;import sys;l.initialize(sys.argv[1]);assert not l._monitors;print('protocol')"
        result=subprocess.run([sys.executable,"-c",code,str(self.root)],cwd=ROOT,capture_output=True,timeout=15)
        self.assertEqual(result.stdout,b"protocol\n")
        self.assertEqual(path.read_bytes(),b"keep")

    def test_all_three_streams_persistent_policy_and_append(self):
        (self.root/'state/service-logs.json').write_text('{"segment_bytes":32,"segments":2}')
        for name in logs.NAMES:
            with self.subTest(name=name):
                path = self.root/'state'/name
                path.write_bytes(b'old'*20)
                code = "from agent_monitor.runtime import service_logs as l;import sys,os;l.initialize(sys.argv[1]);os.write(1,b'recent')"
                self.assertEqual(self.child(code,path=path).returncode,0)
                self.assertEqual(path.read_bytes(),b'recent')
                self.assertEqual(Path(str(path)+'.1').stat().st_size,32)

    def test_invalid_policy_business_continues(self):
        (self.root/'state/service-logs.json').write_text('{"segments":0}')
        code = "from agent_monitor.runtime import service_logs as l;import sys,os;l.initialize(sys.argv[1]);os.write(1,b'business completed')"
        self.assertEqual(self.child(code).returncode,0)
        self.assertIn(b'business completed',self.path.read_bytes())
        self.assertIn(b'maintenance failed',self.path.read_bytes())

    def test_restarts_below_capacity_preserve_previous_bytes(self):
        self.path.write_bytes(b'before restart\n')
        code = "from agent_monitor.runtime import service_logs as l;import sys,os;l.initialize(sys.argv[1]);os.write(2,b'next run'+bytes([10]))"
        for _ in range(2):
            self.assertEqual(self.child(code).returncode,0)
        self.assertEqual(self.path.read_bytes(),b'before restart\nnext run\nnext run\n')

    def test_hard_exit_pending_is_bounded_and_next_run_recovers(self):
        self.path.write_bytes(b'old-prefix-recent!!')
        code = """import os,sys
from pathlib import Path
from agent_monitor.runtime import service_logs as logs
original = os.fsync
def crash(fd):
    original(fd)
    os._exit(73)
logs.os.fsync = crash
logs.LogMaintenance(Path(sys.argv[1])/'state/server.log',8).check()
"""
        for _ in range(3):
            self.assertEqual(self.child(code).returncode,73)
        pending = list(self.path.parent.glob('.server.log*'))
        self.assertEqual(len(pending),1)
        self.assertEqual(sum(p.stat().st_size for p in pending),8)
        self.assertEqual(self.path.read_bytes(),b'old-prefix-recent!!')
        logs.LogMaintenance(self.path,8).check()
        self.assertEqual(self.path.read_bytes(),b'')
        self.assertEqual(Path(str(self.path)+'.1').read_bytes(),b'recent!!')
        self.assertEqual(list(self.path.parent.glob('.server.log*')),[])

    def test_pending_unknown_links_are_not_taken_over(self):
        self.path.write_bytes(b'current'*4)
        victim = self.root/'pending-victim'
        victim.write_bytes(b'untouched')
        pending = self.path.with_name('.server.log.pending')
        for kind in ('symlink','hardlink'):
            with self.subTest(kind=kind):
                if kind == 'symlink':
                    pending.symlink_to(victim)
                else:
                    os.link(victim,pending)
                logs.LogMaintenance(self.path,8).check()
                self.assertEqual(victim.read_bytes(),b'untouched')
                self.assertEqual(self.path.read_bytes(),b'current'*4)
                self.assertTrue(pending.exists())
                pending.unlink()


if __name__ == "__main__":
    unittest.main()
