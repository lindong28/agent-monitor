import json
import tempfile
import unittest
from dataclasses import replace
from datetime import timezone
from pathlib import Path

from parsers import codex
from tests.test_codex_forks import event, usage
from tests.test_source_metric_repairs import meta, turn
import tests.test_cost as cost_fixtures
from agent_monitor.ingestion.usage_archive import UsageArchive


def checkpoint(owner, model='gpt-5.5'):
    # protocol.rs ThreadSettingsAppliedEvent: copied snapshots keep original owner.
    return {'type': 'event_msg', 'payload': {'type': 'thread_settings_applied',
            'thread_id': owner, 'thread_settings': {'model': model}}}


class CodexForkOwnerTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def write(self, name, rows):
        path = self.root / (name + '.jsonl')
        path.write_text(''.join(json.dumps(row) + '\n' for row in rows))
        return path

    def test_startup_owner_survives_response_filter_and_parent_growth(self):
        call = {'type': 'response_item', 'payload': {'type': 'function_call',
                'call_id': 'call1', 'name': 'exec_command', 'arguments': '{}'}}
        parent_rows = [meta('parent'), checkpoint('parent'), turn('gpt-5.4'), call,
                       event(usage(100, 0, 0))]
        for filtered in (False, True):
            copied = [row for row in parent_rows if not filtered or row is not call]
            child = self.write('child', [meta('child', forked_from_id='parent', cli_version='0.162.1'),
                *copied, checkpoint('child'), turn('gpt-5.5'), event(usage(130, 0, 0), usage(30, 0, 0))])
            for grown in (False, True):
                with self.subTest(filtered=filtered, parent_grown=grown):
                    parent = self.write('parent', parent_rows + ([event(usage(200, 0, 0))] if grown else []))
                    errors = []
                    entries = codex.parse_file(child, source_errors=errors,
                                               history=codex.HistoryResolver([parent, child]))
                    self.assertEqual([(e.input_tokens, e.model) for e in entries], [(30, 'gpt-5.5')])
                    self.assertTrue(errors)  # The pre-checkpoint prefix remains unproven.

    def test_foreign_or_ownerless_checkpoint_does_not_release_unknown_prefix(self):
        for owner in ('parent', None):
            with self.subTest(owner=owner):
                child = self.write('child', [meta('child', forked_from_id='missing'),
                    event(usage(100, 0, 0)), checkpoint(owner), turn('gpt-5.5'),
                    event(usage(130, 0, 0), usage(30, 0, 0))])
                errors = []
                self.assertEqual(codex.parse_file(child, source_errors=errors), [])
                self.assertTrue(errors)

    def test_resume_checkpoint_keeps_old_prefix_and_recovers_future_usage(self):
        # A newer CLI may first write an owner checkpoint when resuming an older
        # rollout. Therefore the earlier 100 and 30 cannot all be called inherited.
        child = self.write('child', [meta('child', forked_from_id='missing'),
            event(usage(100, 0, 0)), event(usage(130, 0, 0)), checkpoint('child'),
            event(usage(160, 0, 0), usage(30, 0, 0))])
        for seeded in (False, True):
            archive = UsageArchive(self.root / ('archive-%s.sqlite3' % seeded))
            if seeded:
                base = replace(cost_fixtures.CostTests.entry('gpt-5.4'), agent_id='codex', session_id='child',
                    message_id='child', cache_creation_tokens=0, cache_read_tokens=0, output_tokens=0)
                old = [replace(base, request_id='token-count:2', input_tokens=100),
                       replace(base, request_id='token-count:3', input_tokens=30)]
                archive.collect([child], lambda _: old, lambda _: 'old', [], timezone.utc)
            for force in (False, False, True):
                with self.subTest(seeded=seeded, force=force):
                    errors = []
                    entries, _ = archive.collect([child],
                        lambda path: codex.parse_file(path, source_errors=errors),
                        lambda _: 'current', errors, timezone.utc, force_reload=force)
                    self.assertEqual(sum(e.input_tokens for e in entries), 160 if seeded else 30)
                    own, = [e for e in entries if e.request_id == 'token-count:5']
                    self.assertEqual((own.input_tokens, own.model), (30, 'gpt-5.5'))
                    self.assertTrue(errors)

    def test_owner_without_prior_counter_keeps_first_amount_unknown(self):
        child = self.write('child', [meta('child', forked_from_id='missing'),
            checkpoint('child'), event(usage(1000, 0, 0)), event(usage(1030, 0, 0))])
        errors = []
        entries = codex.parse_file(child, source_errors=errors)
        self.assertEqual([e.input_tokens for e in entries], [30])
        self.assertTrue(errors)
