"""Release orchestration with isolated state; no SSH or provider roots accessed."""
from contextlib import ExitStack, redirect_stdout
import copy
import io
import json
from pathlib import Path
import tempfile
import subprocess
import unittest
from unittest import mock

from agent_monitor.runtime import deploy, rollout, rollout_node
from agent_monitor.snapshots.machine_config import Machine, MachineConfig
from agent_monitor.snapshots.machine_config import _config_json


class GitPredicateTests(unittest.TestCase):
    def test_unknown_checks_do_not_trigger_fetch_or_migration(self):
        for failed_phase in ('objects', 'ancestry'):
            for failure in (subprocess.TimeoutExpired('git', 30), OSError('unavailable'),
                            subprocess.CompletedProcess('git', 128)):
                with self.subTest(phase=failed_phase, failure=type(failure).__name__):
                    owner = mock.Mock(ROOT=Path('/fixture'), PreflightError=deploy.PreflightError)
                    owner.git.return_value = 'a' * 40 + '\trefs/heads/main'
                    owner.check_local.return_value = 'b' * 40
                    steps = ([] if failed_phase == 'objects' else
                             [subprocess.CompletedProcess('git', 0)] * 2) + [failure]
                    with mock.patch.object(rollout_node.subprocess, 'run', side_effect=steps):
                        with self.assertRaisesRegex(ValueError, 'unverified') as raised:
                            rollout_node.prepare_source(owner, 'a' * 40)
                    self.assertNotIn('not an ancestor', str(raised.exception))
                    self.assertFalse(any(call.args[0] in ('fetch', 'merge') for call in owner.git.call_args_list))


class TopologyReleaseTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        (self.root / 'state').mkdir()
        self.revision = 'a' * 40
        self.config = MachineConfig(tuple(Machine(n, n, n == 'hub') for n in ('laptop', 'mini', 'hub')), frozenset({'retired'}))
        self.observations = {m.name: {'root': '/fixture/' + m.name, 'revision': 'b' * 40,
            'machine': m.name, 'members': sorted(self.config.by_name), 'hub': 'hub',
            'config': 'config-' + m.name, 'signature': 'old', 'runtime': {'web': True}}
            for m in self.config.machines}
        self.calls = []
        self.failures = set()
        self.reservations = {}
        self.stack.enter_context(mock.patch.object(rollout, 'ROOT', self.root))
        self.stack.enter_context(mock.patch.object(rollout, 'load_machine_config', return_value=self.config))
        self.stack.enter_context(mock.patch.object(rollout.hub_config, 'enabled', return_value=True))
        self.stack.enter_context(mock.patch.object(rollout.hub_config, 'configuration', return_value={'hub': 'hub', 'machines': sorted(self.config.by_name)}))
        self.stack.enter_context(mock.patch.object(deploy, 'preflight', side_effect=lambda expected: expected or self.revision))
        self.stack.enter_context(mock.patch.object(rollout, 'node', side_effect=self.node))
        self.stack.enter_context(mock.patch.object(deploy, 'check_local', return_value='b' * 40))
        self.output = self.stack.enter_context(redirect_stdout(io.StringIO()))

    def node(self, machine, action, **kwargs):
        self.calls.append((machine.name, action))
        if (machine.name, action) in self.failures:
            raise ValueError('fixture failure')
        current = self.observations[machine.name]
        if action == 'reserve':
            wanted = (kwargs['release_id'], kwargs['revision'])
            if self.reservations.get(machine.name, wanted) != wanted:
                raise ValueError('conflicting reservation')
            self.reservations[machine.name] = wanted
        elif action == 'apply':
            self.assertEqual(self.reservations[machine.name], (kwargs['release_id'], kwargs['revision']))
            current['revision'] = kwargs['revision']
            current['signature'] = 'new'
        elif action == 'release':
            wanted = (kwargs['release_id'], kwargs['revision'])
            if self.reservations.get(machine.name, wanted) != wanted:
                raise ValueError('conflicting reservation')
            self.reservations.pop(machine.name, None)
        return copy.deepcopy(current)

    def record(self):
        return json.loads((self.root / 'state/release.json').read_text())

    def test_strict_new_returns_own_identity_and_preserves_same_sha_history(self):
        self.assertEqual(rollout.run(self.revision, new_only=True), 0)
        first = self.record()
        self.assertIn('Release ID: ' + first['id'], self.output.getvalue())
        self.assertIn('Targets: ' + json.dumps(first['targets'], sort_keys=True), self.output.getvalue())
        self.assertEqual(rollout.run(self.revision, new_only=True), 0)
        self.assertNotEqual(first['id'], self.record()['id'])
        self.assertEqual(rollout.release_status(first['id']), {'original': first, 'abandonment': None})
        calls = list(self.calls)
        with self.assertRaisesRegex(ValueError, 'does not match'):
            rollout.resume(first['id'], self.revision)
        self.assertEqual(calls, self.calls)

    def test_strict_new_refuses_active_and_pending_cleanup_without_node_calls(self):
        for failure in ('apply', 'release'):
            with self.subTest(failure=failure):
                self.failures.add(('mini', failure))
                self.assertEqual(rollout.run(self.revision, new_only=True), 1)
                before = self.record()
                calls = list(self.calls)
                with self.assertRaisesRegex(ValueError, 'unfinished'):
                    rollout.run(self.revision, new_only=True)
                self.assertEqual(before, self.record())
                self.assertEqual(calls, self.calls)
                self.failures.clear()
                self.assertEqual(rollout.resume(before['id'], self.revision), 0)

    def test_resume_identity_conflict_and_partial_recovery(self):
        self.failures.add(('mini', 'apply'))
        self.assertEqual(rollout.run(self.revision, new_only=True), 1)
        before = self.record()
        for identity, revision in [('0' * 32, self.revision), (before['id'], 'c' * 40)]:
            with self.subTest(identity=identity, revision=revision):
                calls = list(self.calls)
                with self.assertRaisesRegex(ValueError, 'does not match'):
                    rollout.resume(identity, revision)
                self.assertEqual(before, self.record())
                self.assertEqual(calls, self.calls)
        self.failures.clear()
        self.calls.clear()
        self.assertEqual(rollout.resume(before['id'], self.revision), 0)
        self.assertEqual(before['id'], self.record()['id'])
        self.assertEqual([n for n, a in self.calls if a == 'apply'], ['mini'])
        self.calls.clear()
        self.assertEqual(rollout.resume(before['id'], self.revision), 0)
        self.assertTrue(all(a == 'inspect' for _, a in self.calls))

    def test_resume_cleanup_only_and_configuration_conflict_preserve_receipt(self):
        self.failures.add(('mini', 'release'))
        self.assertEqual(rollout.run(self.revision, new_only=True), 1)
        before = self.record()
        self.observations['mini']['config'] = 'changed'
        self.calls.clear()
        with self.assertRaisesRegex(ValueError, 'configuration'):
            rollout.resume(before['id'], self.revision)
        self.assertEqual(before, self.record())
        self.assertTrue(all(a == 'inspect' for _, a in self.calls))
        self.observations['mini']['config'] = 'config-mini'
        self.failures.clear()
        self.calls.clear()
        self.assertEqual(rollout.resume(before['id'], self.revision), 0)
        self.assertFalse(any(a in ('reserve', 'apply') for _, a in self.calls))

    def test_resume_config_conflict_at_execute_keeps_success_and_receipt(self):
        self.failures.add(('mini', 'apply'))
        self.assertEqual(rollout.run(self.revision, new_only=True), 1)
        before = self.record()
        self.assertEqual(before['nodes']['hub']['status'], 'installed')
        self.failures.clear()
        self.observations['mini']['config'] = 'changed'
        self.calls.clear()
        with self.assertRaisesRegex(ValueError, 'configuration'):
            rollout.resume(before['id'], self.revision)
        self.assertEqual(before, self.record())
        self.assertTrue(all(a == 'inspect' for _, a in self.calls))
        self.observations['mini']['config'] = 'config-mini'
        self.failures.add(('mini', 'reserve'))
        self.assertEqual(rollout.resume(before['id'], self.revision), 1)
        self.assertEqual(self.record()['nodes']['hub']['status'], 'installed')
        self.assertIn(f"service release resume --id {before['id']} --expect {self.revision}", self.output.getvalue())
        self.failures.clear()
        self.calls.clear()
        self.assertEqual(rollout.resume(before['id'], self.revision), 0)
        self.assertEqual([n for n, a in self.calls if a == 'apply'], ['mini'])

    def test_strict_operations_respect_coordinator_lock(self):
        self.assertEqual(rollout.run(self.revision, new_only=True), 0)
        record = self.record()
        calls = list(self.calls)
        with rollout.release_lock():
            for action in (lambda: rollout.run(self.revision, new_only=True),
                           lambda: rollout.resume(record['id'], self.revision)):
                with self.assertRaisesRegex(ValueError, 'Another topology release'):
                    action()
        self.assertEqual(calls, self.calls)
        self.assertEqual(record, self.record())

    def test_archive_failure_preserves_completed_current(self):
        self.assertEqual(rollout.run(self.revision, new_only=True), 0)
        before = self.record()
        path = rollout.history_path(before['id'])
        path.parent.mkdir()
        path.write_text('{}')
        self.calls.clear()
        with self.assertRaisesRegex(ValueError, 'conflicts'):
            rollout.run(self.revision, new_only=True)
        self.assertEqual(before, self.record())
        self.assertTrue(all(a == 'inspect' for _, a in self.calls))
        path.unlink()
        with mock.patch.object(rollout.rollout_node, 'write_record', side_effect=OSError('archive write failed')):
            with self.assertRaisesRegex(OSError, 'archive write failed'):
                rollout.run(self.revision, new_only=True)
        self.assertEqual(before, self.record())

    def test_abandoned_cannot_resume_and_allows_new(self):
        self.failures.add(('mini', 'apply'))
        self.assertEqual(rollout.run(self.revision, new_only=True), 1)
        record = self.record()
        self.failures.clear()
        self.assertEqual(rollout.abandon(record['id'], self.revision, 'replace'), 0)
        before = self.record()
        calls = list(self.calls)
        with self.assertRaisesRegex(ValueError, 'abandonment'):
            rollout.resume(record['id'], self.revision)
        self.assertEqual(before, self.record())
        self.assertEqual(calls, self.calls)
        self.assertEqual(rollout.run(self.revision, new_only=True), 0)
        self.assertNotEqual(record['id'], self.record()['id'])

    def test_complete_topology_includes_mini_and_excludes_retired(self):
        self.assertEqual(rollout.run(self.revision), 0)
        self.assertEqual(set(self.record()['nodes']), {'laptop', 'mini', 'hub'})
        self.assertEqual([n for n, a in self.calls if a == 'apply'], ['hub', 'laptop', 'mini'])
        first_apply = next(i for i, (_, a) in enumerate(self.calls) if a == 'apply')
        self.assertEqual(sum(a == 'reserve' for _, a in self.calls[:first_apply]), 3)
        self.assertFalse(self.reservations)
        self.assertEqual(self.record()['status'], 'installed')
        self.assertIn('Browser workflows and provider availability were not checked', self.output.getvalue())

    def test_offline_active_node_cannot_be_omitted(self):
        self.failures.add(('mini', 'inspect'))
        self.assertEqual(rollout.run(self.revision), 1)
        self.assertEqual(self.record()['nodes']['mini']['status'], 'unverified')
        self.assertFalse(any(a == 'apply' for _, a in self.calls))
        self.assertNotIn('All configured nodes installed', self.output.getvalue())

    def test_completion_is_recorded_while_all_nodes_are_reserved(self):
        original = self.node
        def interleave(machine, action, **kwargs):
            if action == 'release':
                record = self.record()
                self.assertEqual(record['status'], 'installed')
                self.assertTrue(record['verified_at'])
            result = original(machine, action, **kwargs)
            if action == 'release':
                # A later local repair must not change the recorded verification.
                self.observations[machine.name]['revision'] = 'c' * 40
            return result
        with mock.patch.object(rollout, 'node', side_effect=interleave):
            self.assertEqual(rollout.run(self.revision), 0)
        self.assertTrue(all(n['observed']['revision'] == self.revision
                            for n in self.record()['nodes'].values()))

    def test_cleanup_failure_resumes_only_cleanup_without_redeploying(self):
        self.failures.add(('mini', 'release'))
        self.assertEqual(rollout.run(self.revision), 1)
        first = self.record()
        self.assertEqual(first['status'], 'installed')
        self.assertEqual(first['cleanup_pending'], ['mini', 'hub'])
        self.assertIn('fixture failure', self.output.getvalue())
        self.failures.clear()
        self.calls.clear()
        with self.assertRaises(deploy.PreflightError):
            rollout.run('c' * 40)
        self.assertEqual(rollout.run(), 0)
        self.assertEqual(self.calls, [('mini', 'release'), ('hub', 'release')])
        self.assertEqual(self.record()['id'], first['id'])
        self.assertEqual(self.record()['verified_at'], first['verified_at'])

    def test_failure_retains_release_and_resumes_unfinished_nodes(self):
        self.failures.add(('mini', 'apply'))
        self.assertEqual(rollout.run(self.revision), 1)
        first = self.record()
        self.assertEqual(first['nodes']['laptop']['status'], 'installed')
        self.assertEqual(len(self.reservations), 3)
        self.failures.clear()
        self.calls.clear()
        self.assertEqual(rollout.run(), 0)
        self.assertEqual(self.record()['id'], first['id'])
        self.assertEqual([n for n, a in self.calls if a == 'apply'], ['mini'])
        self.assertIn(('laptop', 'inspect'), self.calls)

    def test_resume_rechecks_historical_success_and_repairs_runtime_drift(self):
        self.failures.add(('mini', 'apply'))
        self.assertEqual(rollout.run(self.revision), 1)
        self.failures.clear()
        self.observations['laptop']['signature'] = 'drifted'
        self.calls.clear()
        self.assertEqual(rollout.run(), 0)
        self.assertIn(('laptop', 'apply'), self.calls)

    def test_different_target_or_topology_cannot_replace_incomplete_release(self):
        self.failures.add(('mini', 'apply'))
        self.assertEqual(rollout.run(self.revision), 1)
        with self.assertRaises(deploy.PreflightError):
            rollout.run('c' * 40)
        with mock.patch.object(rollout, 'load_machine_config', return_value=MachineConfig(self.config.machines[1:], frozenset())), mock.patch.object(rollout.hub_config, 'configuration', return_value={'hub': 'hub', 'machines': ['mini', 'hub']}):
            with self.assertRaises(deploy.PreflightError):
                rollout.run(self.revision)

    def test_config_change_requires_reconciliation_before_mutation(self):
        self.failures.add(('mini', 'apply'))
        self.assertEqual(rollout.run(self.revision), 1)
        self.failures.clear()
        self.observations['mini']['config'] = 'changed'
        self.calls.clear()
        self.assertEqual(rollout.run(), 1)
        self.assertFalse(any(a == 'apply' for _, a in self.calls))

    def test_missing_hub_member_and_wrong_machine_identity_are_rejected(self):
        with mock.patch.object(rollout.hub_config, 'configuration', return_value={'hub': 'hub', 'machines': ['laptop', 'hub']}):
            with self.assertRaises(deploy.PreflightError):
                rollout.run(self.revision)
        self.observations['mini']['machine'] = 'wrong-machine'
        self.assertEqual(rollout.run(self.revision), 1)
        self.assertFalse(any(a == 'apply' for _, a in self.calls))

    def test_client_cannot_start_independent_coordinator(self):
        config = MachineConfig(tuple(Machine(m.name, m.ssh_host, m.name == 'laptop') for m in self.config.machines), frozenset())
        with mock.patch.object(rollout, 'load_machine_config', return_value=config):
            with self.assertRaisesRegex(deploy.PreflightError, 'configured Hub'):
                rollout.run(self.revision)
        self.assertFalse(self.calls)

    def test_standalone_exporter_keeps_its_own_identity_and_membership(self):
        self.observations['mini'].update(machine='local', hub=None, members=['local'], runtime={'web': None})
        self.assertEqual(rollout.run(self.revision), 0)
        self.assertIn(('mini', 'apply'), self.calls)
        self.assertEqual(self.record()['nodes']['mini']['observed']['machine'], 'local')

    def test_two_coordinators_cannot_interleave(self):
        with rollout.release_lock():
            with self.assertRaisesRegex(deploy.PreflightError, 'Another topology release'):
                rollout.run(self.revision)
        self.assertFalse(self.calls)

    def test_check_only_creates_no_release_or_reservations(self):
        self.assertEqual(rollout.run(self.revision, check_only=True), 0)
        self.assertEqual(list((self.root / 'state').iterdir()), [])
        self.assertTrue(all(a == 'inspect' for _, a in self.calls))

    def test_unhealthy_required_runtime_cannot_report_complete(self):
        self.observations['mini']['runtime']['web'] = False
        self.assertEqual(rollout.run(self.revision), 1)
        self.assertEqual(self.record()['status'], 'incomplete')
        self.assertEqual(len(self.reservations), 3)


    def incomplete(self):
        self.failures.add(('mini', 'apply'))
        self.assertEqual(rollout.run(self.revision), 1)
        self.failures.clear()
        self.calls.clear()
        return self.record()

    def abandon(self, record, reason='superseded by published fix'):
        return rollout.abandon(record['id'], record['revision'], reason)

    def test_abandon_archives_original_then_releases_and_allows_new_version(self):
        original = self.incomplete()
        perform = self.node
        def checked(machine, action, **kwargs):
            if action == 'release':
                self.assertEqual(self.record()['status'], 'abandoning')
                archive = json.loads(rollout.history_path(original['id']).read_text())
                self.assertEqual(archive['original'], original)
            return perform(machine, action, **kwargs)
        with mock.patch.object(rollout, 'node', side_effect=checked):
            self.assertEqual(self.abandon(original), 0)
        self.assertEqual(self.record()['status'], 'abandoned')
        self.assertFalse(self.reservations)
        self.assertFalse(any(a == 'apply' for _, a in self.calls))
        calls = list(self.calls)
        self.assertEqual(self.abandon(original), 0)
        self.assertEqual(self.calls, calls)
        self.assertEqual(rollout.run('c' * 40), 0)
        history = rollout.release_status(original['id'])
        self.assertEqual(history['original'], original)
        self.assertTrue(history['abandonment']['finished_at'])
        with self.assertRaisesRegex(deploy.PreflightError, 'ID/revision'):
            self.abandon(original)

    def test_abandon_refuses_wrong_identity_and_completed_release(self):
        original = self.incomplete()
        before = (self.root / 'state/release.json').read_bytes()
        for identity, revision in [('f' * 32, self.revision), (original['id'], 'c' * 40)]:
            with self.assertRaisesRegex(deploy.PreflightError, 'ID/revision'):
                rollout.abandon(identity, revision, 'reason')
        self.assertFalse(self.calls)
        self.assertEqual((self.root / 'state/release.json').read_bytes(), before)
        self.assertEqual(rollout.run(self.revision), 0)
        with self.assertRaisesRegex(deploy.PreflightError, 'Only incomplete'):
            self.abandon(self.record())

    def test_partial_abandon_resumes_only_pending_and_blocks_single_node_bypass(self):
        original = self.incomplete()
        self.failures.add(('mini', 'release'))
        self.assertEqual(self.abandon(original), 1)
        self.assertEqual(self.record()['abandonment']['pending'], ['mini'])
        with mock.patch.object(rollout, 'load_machine_config', return_value=MachineConfig((self.config.machines[-1],), frozenset())):
            with self.assertRaisesRegex(deploy.PreflightError, 'abandonment is unfinished'):
                rollout.run('c' * 40)
        self.failures.clear(); self.calls.clear()
        self.assertEqual(self.abandon(original), 0)
        self.assertEqual(self.calls, [('mini', 'release')])

    def test_lost_release_receipt_is_retryable_without_touching_other_reservation(self):
        original = self.incomplete()
        perform = self.node
        def lose(machine, action, **kwargs):
            result = perform(machine, action, **kwargs)
            if machine.name == 'mini' and action == 'release':
                raise ValueError('lost receipt')
            return result
        with mock.patch.object(rollout, 'node', side_effect=lose):
            self.assertEqual(self.abandon(original), 1)
        self.reservations['mini'] = ('newer-id', 'c' * 40)
        self.assertEqual(self.abandon(original), 1)
        self.assertEqual(self.reservations['mini'], ('newer-id', 'c' * 40))
        self.reservations.pop('mini')
        self.assertEqual(self.abandon(original), 0)

    def test_archive_or_current_write_failure_preserves_resume_authority(self):
        original = self.incomplete()
        write = rollout_node.write_record
        with mock.patch.object(rollout_node, 'write_record', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                self.abandon(original)
        self.assertFalse(self.calls)
        self.assertEqual(self.record(), original)
        current_writes = 0
        def fail_after_release(path, record):
            nonlocal current_writes
            if path.name == 'release.json':
                current_writes += 1
                if current_writes == 2:
                    raise OSError('current write lost')
            return write(path, record)
        with mock.patch.object(rollout_node, 'write_record', side_effect=fail_after_release):
            with self.assertRaises(OSError):
                self.abandon(original)
        self.assertEqual(self.record()['status'], 'abandoning')
        self.assertEqual(self.abandon(original), 0)

    def test_first_inspection_failure_never_guesses_an_installation_root(self):
        self.failures.add(('mini', 'inspect'))
        self.assertEqual(rollout.run(self.revision), 1)
        original = self.record()
        self.assertNotIn('observed', original['nodes']['mini'])
        self.calls.clear()
        self.assertEqual(self.abandon(original), 0)
        self.assertNotIn(('mini', 'release'), self.calls)
        self.assertEqual(self.record()['abandonment']['results']['mini'], 'not_reserved')

    def test_invalid_node_history_and_archive_conflicts_fail_before_release(self):
        original = self.incomplete()
        bad = copy.deepcopy(original)
        bad['nodes']['mini'].pop('observed')
        rollout_node.write_record(self.root / 'state/release.json', bad)
        with self.assertRaises(deploy.PreflightError):
            self.abandon(bad)
        self.assertFalse(self.calls)
        rollout_node.write_record(self.root / 'state/release.json', original)
        archive = rollout.history_path(original['id']); archive.parent.mkdir()
        rollout_node.write_record(archive, {'original': {'id': original['id']}})
        with self.assertRaisesRegex(deploy.PreflightError, 'different release'):
            self.abandon(original)
        self.assertFalse(self.calls)

    def test_status_reports_completed_installation_with_failed_reservation_cleanup(self):
        self.failures.add(('mini', 'release'))
        self.assertEqual(rollout.run(self.revision), 1)
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(rollout.main(['status']), 0)
        self.assertIn('reservation cleanup pending', output.getvalue())
        self.assertIn('fixture failure', output.getvalue())
        self.assertIn(f"service release resume --id {self.record()['id']} --expect {self.revision}", output.getvalue())
        before = self.record()
        with self.assertRaisesRegex(deploy.PreflightError, 'Only incomplete'):
            self.abandon(before)
        self.assertEqual(self.record(), before)

    def test_status_is_local_even_with_broken_topology_or_git(self):
        original = self.incomplete()
        with mock.patch.object(deploy, 'check_local', side_effect=AssertionError('Git must not be read')), mock.patch.object(rollout, 'topology', side_effect=AssertionError('No topology discovery')):
            self.assertEqual(rollout.main(['status', '--json']), 0)
            self.assertEqual(rollout.release_status()['original'], original)
        self.calls.clear()
        self.assertEqual(self.abandon(original), 0)
        archive = rollout.history_path(original['id'])
        archive.unlink()
        with self.assertRaisesRegex(deploy.PreflightError, 'archive is missing'):
            rollout.run('c' * 40)


class ReservationTests(unittest.TestCase):
    def test_configuration_migration_preserves_binding_but_operator_changes_do_not(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            folder = root / 'config'
            folder.mkdir()
            def signature(base, suffix='.json', local='local'):
                return rollout_node.configuration_signature(
                    lambda name: base / (name + suffix), local, _config_json)
            # Defaults belong to the fixed source release, including moves.
            for base in (root, folder):
                for name in ('hub', 'machines'):
                    (base / (name + '.json')).write_text('{"default":true}')
            before = signature(root)
            self.assertEqual(signature(folder), before)
            (folder / 'hub.json').write_text('{"default":"new-release"}')
            self.assertEqual(signature(folder), before)
            # Overrides preserve semantic identity across layout/format moves.
            for name in ('hub', 'machines'):
                (root / (name + '.local.json')).write_text('{"operator":1,"enabled":true}')
                (folder / (name + '.local.json')).write_text('{"enabled": true, "operator": 1}')
            override = signature(root, '.local.json')
            self.assertNotEqual(override, before)
            self.assertEqual(signature(folder, '.local.json'), override)
            (folder / 'hub.local.json').write_text('{"operator":2,"enabled":true}')
            self.assertNotEqual(signature(folder, '.local.json'), override)
            self.assertNotEqual(signature(root, '.local.json', 'other'), override)

    def test_exact_id_version_and_unknown_record(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'state').mkdir()
            path = root / 'state' / rollout_node.RESERVATION
            rollout_node.check_reservation(root, 'a')
            with self.assertRaises(ValueError):
                rollout_node.check_reservation(root, 'a', 'missing')
            rollout_node.write_record(path, {'id': 'one', 'revision': 'a'})
            rollout_node.check_reservation(root, 'a', 'one')
            rollout_node.check_reservation(root, 'a')
            for revision, identity in [('b', 'one'), ('a', 'other'), ('b', None)]:
                with self.assertRaises(ValueError):
                    rollout_node.check_reservation(root, revision, identity)
            path.write_text('{')
            with self.assertRaises(ValueError):
                rollout_node.check_reservation(root, 'a')


if __name__ == '__main__':
    unittest.main()
