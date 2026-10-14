"""Exercise Darwin's real waitid when the Python build does not expose it."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock

import sync_process


class _WithoutWaitid:
    def __getattr__(self, name):
        if name in ('waitid', 'P_PID', 'WEXITED', 'WNOWAIT'):
            raise AttributeError(name)
        return getattr(os, name)


@unittest.skipUnless(sys.platform == 'darwin', 'Darwin libc compatibility')
class DarwinWaitidTests(unittest.TestCase):
    def setUp(self):
        patch = mock.patch.object(sync_process, 'os', _WithoutWaitid())
        patch.start()
        self.addCleanup(patch.stop)

    def test_nonblocking_observation_preserves_live_and_exited_child(self):
        observe = sync_process._exit_observer()
        child = subprocess.Popen([sys.executable, '-c',
            'import sys; print("ready",flush=True); sys.stdin.read(); sys.exit(23)'],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        finished = threading.Event()
        errors = []
        def nonblocking():
            try:
                observe(child.pid, nohang=True)
            except BaseException as exc:
                errors.append(exc)
            finally:
                finished.set()
        try:
            self.assertEqual(child.stdout.readline().strip(), 'ready')
            thread = threading.Thread(target=nonblocking, daemon=True)
            thread.start()
            self.assertTrue(finished.wait(1), 'WNOHANG blocked on a live child')
            self.assertEqual(errors, [])
            self.assertIsNone(child.poll())
            child.stdin.close()
            observe(child.pid)
            # WNOWAIT must leave the same exit status available to its owner.
            self.assertEqual(child.wait(timeout=2), 23)
            with self.assertRaises(ChildProcessError):
                observe(child.pid, nohang=True)
        finally:
            if child.poll() is None:
                child.kill()
            child.wait(timeout=2)
            child.stdin.close()
            child.stdout.close()

    def test_missing_python_api_still_supervises_inherited_stdout(self):
        with tempfile.TemporaryDirectory() as directory:
            worker = Path(directory) / 'worker.py'
            worker.write_text('import subprocess,sys\n'
                'subprocess.Popen([sys.executable,"-c","import time;time.sleep(5)"])\n')
            with sync_process.supervised_worker(worker) as process:
                eof = threading.Event()
                reader = threading.Thread(target=lambda: (process.stdout.read(), eof.set()), daemon=True)
                reader.start()
                self.assertEqual(process.wait(timeout=2), 0)
                self.assertTrue(eof.wait(1), 'live descendant retained stdout')
                reader.join(1)
                with self.assertRaises(ChildProcessError):
                    os.waitpid(process.pid, os.WNOHANG)
                with mock.patch.object(sync_process, '_signal_group') as signal_group:
                    sync_process._stop(process)
                signal_group.assert_not_called()

    def test_native_waitid_retries_eintr_and_preserves_other_errors(self):
        import ctypes
        import errno
        native = mock.Mock()
        def result(*args):
            if native.call_count == 1:
                ctypes.set_errno(errno.EINTR)
                return -1
            ctypes.set_errno(errno.ECHILD)
            return -1
        native.side_effect = result
        with mock.patch.object(ctypes, 'CDLL', return_value=mock.Mock(waitid=native)):
            observe = sync_process._exit_observer()
            with self.assertRaises(ChildProcessError):
                observe(123)
        self.assertEqual(native.call_count, 2)


if __name__ == '__main__':
    unittest.main()
