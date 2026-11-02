"""Release orchestration with isolated state; no SSH or provider roots accessed."""
from contextlib import ExitStack, redirect_stdout
import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from agent_monitor.runtime import deploy, rollout, rollout_node
from agent_monitor.snapshots.machine_config import Machine, MachineConfig
from agent_monitor.snapshots.machine_config import _config_json


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
            del self.reservations[machine.name]
        return copy.deepcopy(current)

    def record(self):
        return json.loads((self.root / 'state/release.json').read_text())

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
