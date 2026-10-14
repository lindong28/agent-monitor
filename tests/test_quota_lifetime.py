import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
PARENT = r"""
import os, subprocess, sys
sys.path.insert(0, sys.argv[1])
import quota_refresh as qr
from parsers.accounts import Account
qr.TIMEOUT = 1.5 if os.environ['LIFETIME_TEST_MODE'] in ('deadline', 'delayed_start') else 5
real = subprocess.Popen
def spawn(command, **kwargs):
    command[1] = sys.argv[1] + '/tests/fixtures/quota_lifetime_reader.py'
    return real(command, **kwargs)
qr.subprocess.Popen = spawn
try:
    value = qr.load_rate_limits('codex', Account('synthetic'))
    print(value.five_hour_pct, flush=True)
except qr.RefreshError:
    print('failed', flush=True)
"""


class QuotaLifetimeTests(unittest.TestCase):
    def test_parent_alive_dead_and_deadline(self):
        for mode in ('normal', 'parent_dead', 'deadline', 'delayed_start', 'stuck_cleanup'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                env = dict(os.environ, HOME=directory, LIFETIME_TEST_ROOT=directory, LIFETIME_TEST_MODE=mode,
                           PYTHONDONTWRITEBYTECODE='1')
                started = time.monotonic()
                parent = subprocess.Popen([sys.executable, '-c', PARENT, str(ROOT)], env=env,
                                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                try:
                    end = time.monotonic() + 4
                    while not (root / 'pids.json').exists() and parent.poll() is None and time.monotonic() < end:
                        time.sleep(.01)
                    self.assertTrue((root / 'pids.json').exists(), 'reader did not reach synthetic helper')
                    child_pid, helper_pid = json.loads((root / 'pids.json').read_text())
                    if mode in ('parent_dead', 'stuck_cleanup'):
                        parent.kill()
                    output, _ = parent.communicate(timeout=6)
                    if mode == "delayed_start":
                        # Restarting the 1.5s budget after the 1s delay takes >2.5s.
                        self.assertLess(time.monotonic() - started, 2.2)
                    end = time.monotonic() + 4
                    while not (root / 'helper-reaped').exists() and time.monotonic() < end:
                        time.sleep(.01)
                    if mode != 'stuck_cleanup':
                        self.assertTrue((root / 'helper-reaped').exists(), 'helper was not reaped by reader')
                    # wait() above proves our parent terminal; ps distinguishes orphan zombies from running.
                    for pid in (child_pid, helper_pid):
                        end = time.monotonic() + 3
                        while time.monotonic() < end:
                            state = subprocess.run(['ps', '-o', 'stat=', '-p', str(pid)], capture_output=True, text=True).stdout.strip()
                            if not state or state.startswith('Z'):
                                break
                            time.sleep(.01)
                        self.assertTrue(not state or state.startswith('Z'), 'owned descendant still running')
                    if mode == 'normal':
                        self.assertEqual(output.strip(), '12')
                    elif mode in ('deadline', 'delayed_start'):
                        self.assertEqual(output.strip(), 'failed')
                finally:
                    if parent.poll() is None:
                        parent.kill()
                    parent.communicate(timeout=3)
