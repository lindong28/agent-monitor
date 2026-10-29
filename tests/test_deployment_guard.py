"""Real subprocess/FD/lock checks; only synthetic checkouts and inert installers."""
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

from agent_monitor.runtime import deployment_guard as guard

SOURCE = Path(__file__).resolve().parents[1]


class DeploymentGuardTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name).resolve()
        self.root = self.home / 'checkout'
        self.root.mkdir()
        shutil.copytree(SOURCE / "agent_monitor", self.root / "agent_monitor", ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copy2(SOURCE / "project_paths.py", self.root / "project_paths.py")
        shutil.copy2(SOURCE / 'agent_monitor/runtime/deployment_guard.py', self.root / 'agent_monitor/runtime/deployment_guard.py')
        (self.root / 'source.txt').write_text('original\n')
        self.env = dict(os.environ, HOME=str(self.home), CLAUDE_CONFIG_DIR=str(self.home / '.claude'),
                        CODEX_HOME=str(self.home / '.codex'), PYTHONDONTWRITEBYTECODE='1',
                        TEST_PYTHON=sys.executable, PYTHONPATH=str(self.root))
        for name in (guard.LOCK_ENV, guard.SOURCE_ENV):
            self.env.pop(name, None)

    def installer(self, body):
        (self.root / 'install.sh').write_text(
            '#!/usr/bin/env bash\nset -euo pipefail\n'
            'ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"\n'
            'exec "$TEST_PYTHON" -m agent_monitor.runtime.deployment_guard install "$ROOT_DIR" -- "$@"\n')
        (self.root / 'agent_monitor/runtime/install.sh').write_text(
            '# AGENT_MONITOR_INSTALL_BODY\nset -euo pipefail\n'
            'ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"\n' + body)
        (self.root / 'install.sh').chmod(0o755)

    def run_guard(self, action, *args, env=None):
        return subprocess.run([sys.executable, '-m', 'agent_monitor.runtime.deployment_guard', action,
                               str(self.root), *args], cwd=self.home, env=env or self.env,
                              capture_output=True, text=True, timeout=10)

    def install(self, *args, env=None):
        return subprocess.run([str(self.root / 'install.sh'), *args], cwd=self.home,
                              env=env or self.env, capture_output=True, text=True, timeout=10)

    def start_install(self):
        process = subprocess.Popen([str(self.root / 'install.sh')], cwd=self.home,
                                   env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        def cleanup():
            if process.poll() is None:
                process.terminate()
            process.communicate(timeout=5)
        self.addCleanup(cleanup)
        deadline = time.monotonic() + 5
        while not (self.root / 'state/entered').exists():
            if process.poll() is not None or time.monotonic() >= deadline:
                self.fail('Inert installer did not reach its marker before the deadline')
            time.sleep(0.01)
        return process

    def test_repeated_install_preserves_arguments_and_root(self):
        self.installer('"$TEST_PYTHON" -m agent_monitor.runtime.deployment_guard check "$ROOT_DIR"\n'
                       'printf "%s\\n" "$ROOT_DIR" "$@" > state/installed\n')
        for _ in range(2):
            result = self.install('--no-services', 'argument with spaces')
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual((self.root / 'state/installed').read_text().splitlines(),
                             [str(self.root), '--no-services', 'argument with spaces'])

    def test_concurrent_install_rejected_then_lock_released(self):
        self.installer('touch state/entered\n"$TEST_PYTHON" -c "import time; time.sleep(1)"\n')
        first = self.start_install()
        denied = self.install()
        self.assertNotEqual(denied.returncode, 0)
        self.assertIn('holds state/deployment.lock', denied.stderr)
        self.assertEqual(first.communicate(timeout=5)[1], '')
        self.assertEqual(first.returncode, 0)
        self.assertEqual(self.run_guard('run', '--', sys.executable, '-c', 'pass').returncode, 0)

    def test_nonzero_installer_releases_lock_and_preserves_result(self):
        self.installer('exit 7\n')
        self.assertEqual(self.install().returncode, 7)
        self.assertEqual(self.run_guard('run', '--', sys.executable, '-c', 'pass').returncode, 0)

    def test_parent_ledger_descriptor_survives_wrapper(self):
        self.installer('printf "artifact\\tsynthetic\\tpresent\\tfixture\\n" >&"$INSTALL_LEDGER_FD"\n')
        ledger = self.home / 'ledger'
        with ledger.open('wb') as stream:
            env = dict(self.env, INSTALL_LEDGER_FD=str(stream.fileno()))
            result = subprocess.run([str(self.root / 'install.sh')], cwd=self.home, env=env,
                                    pass_fds=(stream.fileno(),), capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(ledger.read_text(), 'artifact\tsynthetic\tpresent\tfixture\n')

    def test_wrapper_death_does_not_release_active_installer_lock(self):
        self.installer('touch state/entered\n"$TEST_PYTHON" -c "import time; time.sleep(1)"\n')
        first = self.start_install()
        first.kill()
        first.wait(timeout=5)
        denied = self.run_guard('run', '--', sys.executable, '-c', 'pass')
        self.assertNotEqual(denied.returncode, 0)
        first.communicate(timeout=5)
        self.assertEqual(self.run_guard('run', '--', sys.executable, '-c', 'pass').returncode, 0)

    def test_state_symlink_and_existing_lock_inode_survive(self):
        target = self.home / 'persistent'
        target.mkdir()
        lock = target / 'deployment.lock'
        lock.write_text('previous operation\n')
        saved = target / 'other.lock'
        saved.write_text('preserve\n')
        (self.root / 'state').symlink_to(target, target_is_directory=True)
        identity = ((self.root / 'state').lstat().st_ino, lock.stat().st_ino)
        self.installer('true\n')
        self.assertEqual(self.install().returncode, 0)
        self.assertEqual(identity, ((self.root / 'state').lstat().st_ino, lock.stat().st_ino))
        self.assertEqual(saved.read_text(), 'preserve\n')

    def test_source_change_stops_before_service_checkpoint(self):
        self.installer('printf changed > source.txt\n'
                       '"$TEST_PYTHON" -m agent_monitor.runtime.deployment_guard check "$ROOT_DIR"\n'
                       'touch state/service-changed\n')
        result = self.install()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Source changed', result.stderr)
        self.assertFalse((self.root / 'state/service-changed').exists())

    def test_final_check_rejects_source_change_without_checkpoint(self):
        self.installer('printf changed > source.txt\n')
        result = self.install()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Earlier installation steps may remain', result.stderr)

    def test_installer_body_is_captured_before_execution(self):
        self.installer('printf "exit 99\\n" > agent_monitor/runtime/install.sh\n'
                       'touch state/original-body-finished\n')
        result = self.install()
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue((self.root / 'state/original-body-finished').exists())
        self.assertIn('Source changed', result.stderr)

    def test_source_environment_cannot_replace_install_baseline(self):
        self.installer('"$TEST_PYTHON" -m agent_monitor.runtime.deployment_guard check "$ROOT_DIR"\n')
        result = self.install(env=dict(self.env, **{guard.SOURCE_ENV: '999999'}))
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_fake_lock_environment_never_bypasses_lock(self):
        self.installer('touch state/incorrectly-installed\n')
        for value in ('1', '999999', 'not-a-fd'):
            with self.subTest(value=value):
                result = self.install(env=dict(self.env, **{guard.LOCK_ENV: value}))
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse((self.root / 'state/incorrectly-installed').exists())

    def test_held_requires_actual_inherited_lock_and_supports_reentry(self):
        self.assertNotEqual(self.run_guard('held').returncode, 0)
        self.assertFalse((self.root / 'state').exists())
        self.installer('"$TEST_PYTHON" -m agent_monitor.runtime.deployment_guard held "$ROOT_DIR"\n'
                       '"$TEST_PYTHON" -m agent_monitor.runtime.deployment_guard run "$ROOT_DIR" -- '
                       '"$TEST_PYTHON" -m agent_monitor.runtime.deployment_guard held "$ROOT_DIR"\n')
        result = self.install()
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_missing_checkpoint_and_wrong_root_fail_closed(self):
        self.assertNotEqual(self.run_guard('check').returncode, 0)
        wrong = subprocess.run([sys.executable, str(self.root / 'agent_monitor/runtime/deployment_guard.py'),
                                'install', str(self.home)], env=self.env, capture_output=True, timeout=5)
        self.assertNotEqual(wrong.returncode, 0)

    def test_long_lived_child_can_detach_without_holding_lock(self):
        code = ('import os,time; from agent_monitor.runtime import deployment_guard as g; pid=os.fork(); '
                '\nif pid == 0:\n g.detach_guard(); os.close(1); os.close(2); '
                'time.sleep(1); os._exit(0)\n')
        result = self.run_guard('run', '--', sys.executable, '-c', code)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.run_guard('run', '--', sys.executable, '-c', 'pass').returncode, 0)

    def test_group_permission_error_waits_for_confirmed_disappearance(self):
        child = mock.Mock(pid=12345)
        timeout = subprocess.TimeoutExpired('fixture', .3)
        child.wait.side_effect = [timeout, 0, 0]
        with mock.patch.object(guard.subprocess, 'Popen', return_value=child),\
                mock.patch.object(guard.os, 'killpg', side_effect=[
                    None, PermissionError(), PermissionError(), ProcessLookupError(),
                ]) as killpg, mock.patch.object(guard.time, 'sleep'):
            with self.assertRaises(subprocess.TimeoutExpired) as caught:
                guard._run(['fixture'], self.root, timeout=.3)
        self.assertIs(caught.exception, timeout)
        self.assertEqual(killpg.call_args_list, [
            mock.call(12345, signal.SIGTERM), mock.call(12345, signal.SIGKILL),
            mock.call(12345, 0), mock.call(12345, 0),
        ])

    def test_persistent_group_permission_error_does_not_confirm_termination(self):
        child = mock.Mock(pid=12345)
        child.wait.side_effect = [subprocess.TimeoutExpired('fixture', .3), 0, 0]
        with mock.patch.object(guard.subprocess, 'Popen', return_value=child),\
                mock.patch.object(guard.os, 'killpg', side_effect=[
                    None, PermissionError(), PermissionError(),
                ]), mock.patch.object(guard.time, 'monotonic', side_effect=[0, 6]):
            with self.assertRaisesRegex(guard.GuardError, 'termination is unverified'):
                guard._run(['fixture'], self.root, timeout=.3)

    def test_timeout_reaps_group_even_when_leader_exits_first(self):
        for resistant in (False, True):
            with self.subTest(resistant=resistant):
                late = self.root / 'state/late-write'
                leader = self.root / 'state/leader'
                code = '''import os,signal,time,pathlib
pathlib.Path('state/leader').write_text(str(os.getpid()))
if os.fork() == 0:
    if RESISTANT: signal.signal(signal.SIGTERM, signal.SIG_IGN)
    time.sleep(1)
    pathlib.Path('state/late-write').write_text('unexpected work')
    os._exit(0)
time.sleep(10)
'''.replace('RESISTANT', repr(resistant))
                try:
                    with guard.deployment_lock(self.root), self.assertRaises(subprocess.TimeoutExpired):
                        guard._run([sys.executable, '-c', code], self.root, timeout=.3)
                    with guard.deployment_lock(self.root):
                        pass
                    time.sleep(1)
                    self.assertFalse(late.exists())
                finally:
                    if leader.exists():
                        try:
                            os.killpg(int(leader.read_text()), signal.SIGKILL)
                        except ProcessLookupError:
                            pass


if __name__ == '__main__':
    unittest.main()
