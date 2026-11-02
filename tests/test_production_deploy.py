"""Deployment admission against real isolated Git repositories and an inert installer."""
import os
import fcntl
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

SOURCE = Path(__file__).resolve().parents[1]


class ProductionDeploymentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        home = self.root / "home"
        home.mkdir()
        self.env = {**os.environ, "HOME": str(home), "GIT_CONFIG_GLOBAL": os.devnull,
                    "CLAUDE_CONFIG_DIR": str(home / '.claude'), "CODEX_HOME": str(home / '.codex'),
                    "AGENT_MONITOR_PYTHON": sys.executable, "PYTHONDONTWRITEBYTECODE": '1', "GIT_CONFIG_NOSYSTEM": "1", "GIT_TERMINAL_PROMPT": "0"}
        for key in list(self.env):
            if key.startswith(("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_CONFIG_COUNT", "GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_")):
                del self.env[key]
        self.repo = self.root / "checkout"
        self.repo.mkdir()
        self.remote = self.root / "origin.git"
        self.run_git(self.root, "init", "--bare", str(self.remote))
        self.run_git(self.repo, "init", "-b", "main")
        self.run_git(self.repo, "config", "user.name", "Deployment Test")
        self.run_git(self.repo, "config", "user.email", "test@example.invalid")
        shutil.copytree(SOURCE / "agent_monitor", self.repo / "agent_monitor", ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copy2(SOURCE / "agent-monitor", self.repo / "agent-monitor")
        (self.repo / ".gitignore").write_text("installed\n")
        (self.repo / "agent_monitor/runtime/install.sh").write_text('#!/bin/sh\n# AGENT_MONITOR_INSTALL_BODY\nprintf "%s\\n" "$@" > installed\nexit "${TEST_INSTALL_EXIT:-0}"\n')
        shutil.copy2(SOURCE / "install.sh", self.repo / "install.sh")
        (self.repo / 'config').mkdir()
        (self.repo / 'config/machines.json').write_text('{"machines":[{"name":"local","ssh_host":"localhost","self":true}],"retired_names":[]}')
        (self.repo / "source.txt").write_text("published\n")
        self.run_git(self.repo, "add", "agent-monitor", "agent_monitor", "install.sh", ".gitignore", "source.txt", "config")
        self.run_git(self.repo, "commit", "-m", "fixture")
        self.run_git(self.repo, "remote", "add", "origin", str(self.remote))
        self.run_git(self.repo, "push", "origin", "main")
        self.head = self.run_git(self.repo, "rev-parse", "HEAD")

    def run_git(self, root, *args):
        return subprocess.check_output(["git", "-C", str(root), *args], env=self.env,
                                       stderr=subprocess.DEVNULL, text=True, timeout=20).strip()

    def deploy(self, *args, root=None):
        repo = root or self.repo
        return subprocess.run([str(repo / "agent-monitor"), "service", "deploy", *args], cwd=repo,
                              env=self.env, capture_output=True, text=True, timeout=40)

    def blocked(self, result):
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("Installer was not started", result.stderr)
        self.assertFalse((self.repo / "installed").exists())

    def test_published_main_runs_installer_and_forwards_arguments(self):
        result = self.deploy("--expect", self.head, "--", "rollup-daemon", "--no-services")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.repo / "installed").read_text().splitlines(), ["rollup-daemon", "--no-services"])
        self.assertIn(self.head, result.stdout)

    def test_source_edit_after_successful_preflight_is_not_the_baseline(self):
        code = '''from agent_monitor.runtime import deploy; from agent_monitor.runtime import deployment_guard
def admission():
    head = deploy.preflight(None)
    (deploy.ROOT / 'source.txt').write_text('changed after admission')
    return head
deployment_guard.run_install(deploy.ROOT, [], preflight=admission)
'''
        result = subprocess.run([sys.executable, '-c', code], cwd=self.repo, env=self.env,
                                capture_output=True, text=True, timeout=20)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Source changed', result.stderr)
        self.assertFalse((self.repo / 'installed').exists())

    def test_check_only_does_not_install(self):
        result = self.deploy("--check-only")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.repo / "installed").exists())
        self.assertIn("health were not checked", result.stdout)
        self.assertFalse((self.repo / 'state').exists())

    def test_existing_state_symlink_is_preserved(self):
        state = self.root / "persistent-state"
        state.mkdir()
        link = self.repo / "state"
        link.symlink_to(state, target_is_directory=True)
        inode = link.lstat().st_ino
        result = self.deploy()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(link.lstat().st_ino, inode)
        self.assertEqual(link.resolve(), state)

    def test_installer_failure_is_propagated(self):
        self.env["TEST_INSTALL_EXIT"] = "7"
        self.assertEqual(self.deploy().returncode, 7)

    def publish_installer(self, body):
        (self.repo / 'agent_monitor/runtime/install.sh').write_text('#!/bin/sh\n# AGENT_MONITOR_INSTALL_BODY\nset -eu\n' + body)
        self.run_git(self.repo, 'add', 'agent_monitor/runtime/install.sh')
        self.run_git(self.repo, 'commit', '-m', 'inert installer scenario')
        self.run_git(self.repo, 'push', 'origin', 'main')
        self.head = self.run_git(self.repo, 'rev-parse', 'HEAD')

    def test_source_edit_during_install_blocks_following_checkpoint(self):
        self.publish_installer('printf changed > source.txt\n'
                               '"' + sys.executable + '" agent_monitor/runtime/deployment_guard.py check "$PWD"\n'
                               'touch installed\n')
        result = self.deploy('--expect', self.head)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Source changed', result.stderr)
        self.assertFalse((self.repo / 'installed').exists())

    def test_head_change_during_install_is_rejected_at_completion(self):
        self.publish_installer('git commit --allow-empty -m changed >/dev/null\n')
        result = self.deploy('--expect', self.head)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Source changed', result.stderr)

    def test_lock_is_acquired_before_production_preflight(self):
        state = self.repo / 'state'
        state.mkdir()
        self.run_git(self.repo, 'remote', 'remove', 'origin')
        with (state / 'deployment.lock').open('w') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = self.deploy()
        self.blocked(result)
        self.assertIn('holds state/deployment.lock', result.stderr)

    def test_dirty_tracked_states_block(self):
        for mode in ("unstaged", "staged", "deleted"):
            with self.subTest(mode=mode):
                self.run_git(self.repo, "reset", "--hard", self.head)
                source = self.repo / "source.txt"
                if mode == "deleted":
                    source.unlink()
                else:
                    source.write_text("unpublished edit\n")
                    if mode == "staged":
                        self.run_git(self.repo, "add", "source.txt")
                self.blocked(self.deploy())

    def test_untracked_source_blocks(self):
        (self.repo / "extra.py").write_text("# uncommitted source\n")
        self.blocked(self.deploy())

    def test_hidden_index_flags_block_even_without_content_changes(self):
        for flag in ("assume-unchanged", "skip-worktree"):
            for dirty in (False, True):
                with self.subTest(flag=flag, dirty=dirty):
                    self.run_git(self.repo, "update-index", "--" + flag, "source.txt")
                    if dirty:
                        (self.repo / "source.txt").write_text("unpublished edit\n")
                    self.blocked(self.deploy("--expect", self.head))
                    self.run_git(self.repo, "update-index", "--no-" + flag, "source.txt")
                    self.run_git(self.repo, "checkout", "--", "source.txt")

    def test_git_intermediate_states_block(self):
        for name in ("rebase-merge", "rebase-apply", "MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD", "sequencer", "BISECT_START"):
            with self.subTest(state=name):
                marker = self.repo / ".git" / name
                marker.write_text(self.head + "\n")
                self.blocked(self.deploy())
                marker.unlink()

    def test_branch_and_detached_head_block(self):
        self.run_git(self.repo, "switch", "-c", "development")
        self.blocked(self.deploy())
        self.run_git(self.repo, "checkout", "--detach", self.head)
        self.blocked(self.deploy())

    def test_development_worktree_blocks(self):
        other = self.root / "development"
        self.run_git(self.repo, "worktree", "add", "-b", "task", str(other))
        result = self.deploy(root=other)
        self.blocked(result)
        self.assertFalse((other / "installed").exists())

    def test_expected_release_mismatch_blocks(self):
        self.blocked(self.deploy("--expect", "0" * len(self.head)))

    def test_local_unpublished_commit_blocks(self):
        self.run_git(self.repo, "commit", "--allow-empty", "-m", "not published")
        self.blocked(self.deploy())

    def advance_remote(self):
        other = self.root / "publisher"
        self.run_git(self.root, "clone", "-b", "main", str(self.remote), str(other))
        self.run_git(other, "-c", "user.name=Publisher", "-c", "user.email=test@example.invalid", "commit", "--allow-empty", "-m", "new remote")
        self.run_git(other, "push", "origin", "main")

    def test_remote_advance_with_stale_tracking_ref_blocks(self):
        self.advance_remote()
        self.assertEqual(self.run_git(self.repo, "rev-parse", "origin/main"), self.head)
        self.blocked(self.deploy())

    def test_fixed_release_allows_published_ancestor_after_fetch(self):
        self.advance_remote()
        self.run_git(self.repo, 'fetch', 'origin')
        result = self.deploy('--expect', self.head)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(self.head, result.stdout)
        self.assertNotIn('main = origin/main', result.stdout)
        self.assertEqual(self.run_git(self.repo, 'rev-parse', 'HEAD'), self.head)

    def test_fixed_release_rejects_unfetched_live_history(self):
        self.advance_remote()
        self.blocked(self.deploy('--expect', self.head))

    def test_fixed_release_rejects_unpublished_divergence(self):
        self.advance_remote()
        self.run_git(self.repo, 'fetch', 'origin')
        self.run_git(self.repo, 'commit', '--allow-empty', '-m', 'diverged')
        self.blocked(self.deploy('--expect', self.run_git(self.repo, 'rev-parse', 'HEAD')))

    def test_active_release_blocks_conflicting_local_deploy_and_installer(self):
        import json
        state = self.repo / 'state'
        state.mkdir()
        (state / 'release-reservation.json').write_text(json.dumps({'id': 'other', 'revision': '0' * 40}))
        result = self.deploy('--local', '--expect', self.head)
        self.blocked(result)
        self.assertIn('reserves this node', result.stderr)
        result = subprocess.run(['bash', 'install.sh', '--no-services'], cwd=self.repo,
                                env=self.env, capture_output=True, text=True, timeout=30)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.repo / 'installed').exists())

    def test_same_release_local_repair_is_allowed(self):
        import json
        state = self.repo / 'state'
        state.mkdir()
        (state / 'release-reservation.json').write_text(json.dumps({'id': 'ongoing', 'revision': self.head}))
        result = self.deploy('--local', '--expect', self.head)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_node_bootstrap_uses_installed_checkout_and_matching_reservation(self):
        import json
        script = (SOURCE / 'agent_monitor/runtime/rollout_node.py').read_text()
        def node(action, **extra):
            request = {'root': str(self.repo), 'action': action, 'revision': self.head, 'id': 'fixture', **extra}
            result = subprocess.run([sys.executable, '-c', script, json.dumps(request)],
                                    cwd=self.repo, env=self.env, capture_output=True, text=True, timeout=40)
            lines = [line.partition('=')[2] for line in result.stdout.splitlines()
                     if line.startswith('AGENT_MONITOR_RELEASE_RESULT=')]
            self.assertEqual(len(lines), 1, result.stderr)
            return result, json.loads(lines[0])
        result, receipt = node('inspect')
        self.assertEqual(result.returncode, 0, receipt)
        observed = receipt['observed']
        self.assertEqual(observed['revision'], self.head)
        result, receipt = node('reserve', observed=observed)
        self.assertEqual(result.returncode, 0, receipt)
        result, receipt = node('apply', observed=observed)
        self.assertEqual(result.returncode, 0, receipt)
        self.assertTrue((self.repo / 'installed').exists())
        self.assertNotIn('Starting install.sh', result.stdout)
        result, receipt = node('release', observed=observed)
        self.assertEqual(result.returncode, 0, receipt)
        self.assertFalse((self.repo / 'state/release-reservation.json').exists())
        # The delete may succeed while its transport receipt is lost.
        result, receipt = node('release', observed=observed)
        self.assertEqual(result.returncode, 0, receipt)

    def test_diverged_history_blocks(self):
        self.advance_remote()
        self.run_git(self.repo, "commit", "--allow-empty", "-m", "local divergence")
        self.blocked(self.deploy())

    def prepare_node_source(self, revision, *, forbid_fetch=False):
        code = '''import sys
from agent_monitor.runtime import deploy, rollout_node
original = deploy.git
if sys.argv[2] == 'yes':
 def no_fetch(*args):
  if args[0] == 'fetch': raise AssertionError('unnecessary object download')
  return original(*args)
 deploy.git = no_fetch
rollout_node.prepare_source(deploy, sys.argv[1])
'''
        return subprocess.run([sys.executable, '-c', code, revision, 'yes' if forbid_fetch else 'no'],
                              cwd=self.repo, env=self.env, capture_output=True, text=True, timeout=40)

    def test_preloaded_release_still_verifies_live_origin_without_fetch(self):
        result = self.prepare_node_source(self.head, forbid_fetch=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.run_git(self.repo, 'remote', 'set-url', 'origin', str(self.root / 'sensitive-url-fragment'))
        result = self.prepare_node_source(self.head, forbid_fetch=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('reading live origin/main', result.stderr)
        self.assertIn('Git ls-remote exited', result.stderr)
        self.assertNotIn('sensitive-url-fragment', result.stderr)

    def test_missing_release_objects_are_fetched_before_fast_forward(self):
        self.advance_remote()
        published = self.run_git(self.remote, 'rev-parse', 'main')
        result = self.prepare_node_source(published)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.run_git(self.repo, 'rev-parse', 'HEAD'), published)

    def test_unrelated_history_explains_migration_without_replacing_source(self):
        unrelated = self.run_git(self.repo, 'commit-tree', self.head + '^{tree}', '-m', 'separate public history')
        self.run_git(self.repo, 'push', 'origin', unrelated + ':refs/heads/public-history')
        self.run_git(self.remote, 'update-ref', 'refs/heads/main', unrelated)
        result = self.prepare_node_source(unrelated, forbid_fetch=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('checking checkout ancestry', result.stderr)
        self.assertIn('Transition an existing installation', result.stderr)
        self.assertEqual(self.run_git(self.repo, 'rev-parse', 'HEAD'), self.head)
        self.assertFalse((self.repo / 'installed').exists())

    def test_missing_origin_blocks(self):
        self.run_git(self.repo, "remote", "remove", "origin")
        self.blocked(self.deploy())

    def test_missing_remote_main_blocks(self):
        self.run_git(self.remote, "update-ref", "-d", "refs/heads/main")
        self.blocked(self.deploy())

    def test_unreachable_remote_does_not_echo_url(self):
        self.run_git(self.repo, "remote", "set-url", "origin", str(self.root / "sensitive-url-fragment"))
        result = self.deploy()
        self.blocked(result)
        self.assertNotIn("sensitive-url-fragment", result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
