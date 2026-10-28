import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from parsers import codex
from agent_monitor.accounting import pricing_fetcher
import tests.test_cost as cost_tests
from tests.test_codex_forks import event, usage
from agent_monitor.ingestion.usage_archive import UsageArchive
from datetime import timezone


def meta(identity, **extra):
    return {'type': 'session_meta', 'payload': {'id': identity, 'model': 'gpt-5.4', **extra}}


def turn(model):
    return {'type': 'turn_context', 'payload': {'model': model}}


class SourceMetricRepairTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def write(self, name, rows):
        path = self.root / (name + '.jsonl')
        path.write_text(''.join(json.dumps(row) + '\n' for row in rows))
        return path

    def copied(self):
        inherited = [meta('parent'), turn('gpt-5.4'), event(usage(100, 0, 0)), event(usage(150, 0, 0))]
        parent = self.write('parent', inherited)
        # The recorder rewrites envelope timestamps while retaining item payloads.
        copied = [dict(row, timestamp='2026-10-14T00:00:00Z') for row in inherited]
        child = self.write('child', [meta('child', forked_from_id='parent', forked_from_ordinal_exclusive=4), *copied,
                                     turn('gpt-5.5'), event(usage(180, 0, 0))])
        return parent, child

    def test_copied_prefix_is_zeroed_with_original_keys(self):
        parent, child = self.copied()
        errors = []
        entries = codex.parse_file(child, history=codex.HistoryResolver([parent, child]), source_errors=errors)
        self.assertEqual([e.input_tokens for e in entries], [0, 0, 30])
        self.assertEqual([e.request_id for e in entries], ['token-count:4', 'token-count:5', 'token-count:7'])
        self.assertEqual(errors, [])

    def test_explicit_ordinal_can_resolve_without_parent(self):
        rows = [meta('child', forked_from_id='missing', subagent_history_start_ordinal=3),
                event(usage(100, 0, 0)), event(usage(150, 0, 0)),
                turn('gpt-5.5'), event(usage(180, 0, 0))]
        entries = codex.parse_file(self.write('child', rows))
        self.assertEqual([e.input_tokens for e in entries], [0, 0, 30])

    def test_missing_parent_does_not_turn_later_copies_into_child_usage(self):
        rows = [meta('child', forked_from_id='missing'), event(usage(100, 0, 0), usage(100, 0, 0)),
                turn('gpt-5.5'), event(usage(150, 0, 0), usage(50, 0, 0))]
        errors = []
        self.assertEqual(codex.parse_file(self.write('child', rows), source_errors=errors), [])
        self.assertTrue(errors)

    def test_turn_models_override_current_thread_model(self):
        rows = [meta('one'), turn('gpt-5.4'), event(usage(1000000, 0, 0)),
                turn('gpt-5.5'), event(usage(2000000, 0, 0))]
        entries = codex.parse_file(self.write('one', rows), models={'one': 'gpt-5.5'})
        self.assertEqual([e.model for e in entries], ['gpt-5.4', 'gpt-5.5'])
        self.assertAlmostEqual(sum(pricing_fetcher.calculate_cost(e, pricing_fetcher._fallback_pricing()) for e in entries), 7.5)

    def test_unknown_model_is_not_guessed_from_current_thread(self):
        path = self.write('unknown', [{'type': 'session_meta', 'payload': {'id': 'one'}}, event(usage(10, 0, 0))])
        entry, = codex.parse_file(path, models={'one': 'gpt-5.5'})
        self.assertEqual(entry.model, 'unknown')
        self.assertIsNone(pricing_fetcher.calculate_cost(entry, pricing_fetcher._fallback_pricing()))

    def test_model_matching_requires_exact_catalog_identity(self):
        table = pricing_fetcher._fallback_pricing()
        self.assertEqual(pricing_fetcher.resolve_model_key('gpt-5.5', table), 'gpt-5.5')
        for model in ('custom-gpt-5.5-unpriced', 'azure/gpt-5.5', 'anthropic/gpt-5.5'):
            with self.subTest(model=model):
                self.assertIsNone(pricing_fetcher.resolve_model_key(model, table))

    def test_used_cache_categories_require_explicit_prices(self):
        entry = replace(cost_tests.CostTests.entry('test'), input_tokens=0, output_tokens=0,
                        cache_creation_tokens=0, cache_read_tokens=0)
        base = {'input_cost_per_token': 2e-6, 'output_cost_per_token': 10e-6}
        cases = [({'cache_read_tokens': 1000000}, 'cache_read_input_token_cost', .2e-6, .2),
                 ({'cache_creation_tokens': 1000000}, 'cache_creation_input_token_cost', 2.5e-6, 2.5),
                 ({'cache_creation_tokens': 1000000, 'cache_creation_1h_tokens': 1000000},
                  'cache_creation_input_token_cost_above_1hr', 4e-6, 4)]
        for counts, rate, value, expected in cases:
            with self.subTest(rate=rate):
                rates = dict(base)
                if 'cache_creation_1h_tokens' in counts:
                    rates['cache_creation_input_token_cost'] = 2.5e-6
                self.assertIsNone(pricing_fetcher.calculate_cost(replace(entry, **counts), {'test': rates}))
                rates[rate] = value
                self.assertAlmostEqual(pricing_fetcher.calculate_cost(replace(entry, **counts), {'test': rates}), expected)
        self.assertEqual(pricing_fetcher.calculate_cost(entry, {'test': base}), 0)

    def test_sonnet_five_official_rates(self):
        rates = pricing_fetcher._fallback_pricing()['claude-sonnet-5']
        # Official Claude pricing fetched 2026-10-14; cancelled September increase.
        keys = ('input_cost_per_token', 'output_cost_per_token', 'cache_read_input_token_cost',
                'cache_creation_input_token_cost', 'cache_creation_input_token_cost_above_1hr')
        for key, expected in zip(keys, [2, 10, .2, 2.5, 4]):
            self.assertAlmostEqual(rates[key] * 1000000, expected)

    def test_expired_quota_stays_an_observation(self):
        for expire in (False, True):
            for reset in (1, 4102444800):
                limits = codex._build_rate_limits({'primary': {'used_percent': 42, 'resets_at': reset},
                    'secondary': {'used_percent': 67, 'resets_at': reset}}, '2026-10-14T00:00:00Z', 'one', {}, expire=expire)
                self.assertEqual((limits.five_hour_pct, limits.seven_day_pct), (42, 67))

    def test_archive_fresh_warm_and_reparse_preserve_keys(self):
        parent, child = self.copied()
        archive = UsageArchive(self.root / 'archive.sqlite3')
        errors = []
        history = codex.HistoryResolver([parent, child])
        def signature(path):
            return json.dumps([path.stat().st_mtime_ns, history.signature(path)])
        def parse(path):
            return codex.parse_file(path, history=history, source_errors=errors)
        # Seed old identities independently of the parser under test.
        base = replace(cost_tests.CostTests.entry('gpt-5.4'), agent_id='codex', session_id='child',
                       message_id='child', cache_creation_tokens=0, cache_read_tokens=0, output_tokens=0)
        old = [replace(base, request_id='token-count:%d' % line, input_tokens=n, message_count=1)
               for line, n in [(4, 100), (5, 50), (7, 30)]]
        archive.collect([child], lambda _: old, lambda _: 'old-parser', errors, timezone.utc)
        before = {e.dedup_key for e in archive.read()[0]}
        for force in (False, False, True):
            entries, _ = archive.collect([child], parse, signature, errors, timezone.utc, force_reload=force)
            self.assertEqual(sum(e.input_tokens for e in entries), 30)
            self.assertEqual({e.dedup_key for e in entries}, before)
        self.assertEqual(errors, [])
        with mock.patch.object(codex.json, 'loads', side_effect=AssertionError('warm signature decoded body')):
            self.assertIsNotNone(history.signature(child))
        # Parent replacement invalidates the dependency without touching child bytes.
        before_signature = signature(child)
        with parent.open('a') as handle:
            handle.write(json.dumps(turn('gpt-5.5')) + '\n')
        self.assertNotEqual(signature(child), before_signature)
    def test_explicit_boundary_without_counter_retains_unknown_first_segment(self):
        rows = [meta('child', forked_from_id='missing', subagent_history_start_ordinal=2),
                turn('gpt-5.4'), turn('gpt-5.5'), event(usage(1000, 0, 0)), event(usage(1030, 0, 0))]
        errors = []
        entries = codex.parse_file(self.write('child', rows), source_errors=errors)
        self.assertEqual([e.input_tokens for e in entries], [30])
        self.assertTrue(errors)

    def test_paginated_subagent_starts_own_counter_at_zero(self):
        # Codex 0.162.1 spawn filters parent TokenCount before seeding the child.
        metadata = meta('child', forked_from_id='missing', subagent_history_start_ordinal=2,
                        cli_version='0.162.1', history_mode='paginated', thread_source='subagent')
        checkpoint = {'type': 'event_msg', 'ordinal': 2, 'payload': {
            'type': 'thread_settings_applied', 'thread_id': 'child',
            'thread_settings': {'model': 'gpt-5.5'}}}
        rows = [metadata, turn('parent-model'), checkpoint,
                event(usage(1000, 800, 10)), event(usage(1030, 800, 15))]
        errors = []
        entries = codex.parse_file(self.write('fresh-child', rows), source_errors=errors)
        self.assertEqual([(e.input_tokens, e.cache_read_tokens, e.output_tokens) for e in entries],
                         [(200, 800, 10), (30, 0, 5)])
        self.assertEqual(errors, [])
        # A resume checkpoint, foreign owner, or unverified producer must not
        # acquire the same zero-baseline meaning merely by matching usage.
        for field, value in [('cli_version', '0.162.0'), ('history_mode', 'legacy'),
                             ('thread_source', 'cli'), ('checkpoint_owner', 'parent'),
                             ('checkpoint_ordinal', 3)]:
            with self.subTest(field=field):
                altered = json.loads(json.dumps(rows))
                if field == 'checkpoint_owner':
                    altered[2]['payload']['thread_id'] = value
                elif field == 'checkpoint_ordinal':
                    altered[2]['ordinal'] = value
                else:
                    altered[0]['payload'][field] = value
                errors = []
                entries = codex.parse_file(self.write('unknown-child', altered), source_errors=errors)
                self.assertEqual([e.input_tokens for e in entries], [30])
                self.assertTrue(errors)

    def test_verified_paginated_producer_versions_and_old_owner_schema(self):
        # Each version's spawn/initialization/persistence chain is pinned in
        # docs/references/metrics.md; 0.147.0 did not emit checkpoint thread_id.
        for version in ('0.147.0', '0.152.0', '0.153.4', '0.154.0', '0.158.0', '0.162.1'):
            with self.subTest(version=version):
                owner = {} if version == '0.147.0' else {'thread_id': 'child'}
                rows = [meta('child', forked_from_id='parent', subagent_history_start_ordinal=2,
                             cli_version=version, history_mode='paginated', thread_source='subagent'),
                        turn('parent'), {'type': 'event_msg', 'ordinal': 2, 'payload': {
                            'type': 'thread_settings_applied', 'thread_settings': {'model': 'gpt-5.5'}, **owner}},
                        event(usage(30, 0, 5))]
                errors = []
                entries = codex.parse_file(self.write('versioned', rows), source_errors=errors)
                self.assertEqual([(e.input_tokens, e.output_tokens) for e in entries], [(30, 5)])
                self.assertEqual(errors, [])
                rows[2]['payload']['thread_id'] = 'parent'
                errors = []
                self.assertEqual(codex.parse_file(self.write('foreign', rows), source_errors=errors), [])
                self.assertTrue(errors)

    def test_unknown_fork_keeps_archived_facts_and_reports_each_scan(self):
        path = self.write('child', [meta('child', forked_from_id='missing'),
                                  event(usage(100, 0, 0)), event(usage(150, 0, 0))])
        archive = UsageArchive(self.root / 'unknown-archive.sqlite3')
        base = replace(cost_tests.CostTests.entry('gpt-5.4'), agent_id='codex', session_id='child',
                       message_id='child', cache_creation_tokens=0, cache_read_tokens=0, output_tokens=0)
        old = [replace(base, request_id='token-count:2', input_tokens=100),
               replace(base, request_id='token-count:3', input_tokens=50)]
        archive.collect([path], lambda _: old, lambda _: 'legacy', [], timezone.utc)
        for _ in range(2):
            errors = []
            entries, _ = archive.collect([path], lambda p: codex.parse_file(p, source_errors=errors),
                                         lambda _: 'current', errors, timezone.utc)
            self.assertEqual(sum(e.input_tokens for e in entries), 150)
            self.assertEqual({e.dedup_key for e in entries}, {e.dedup_key for e in old})
            self.assertTrue(errors)

    def test_parent_cutoff_ignores_later_appended_history(self):
        inherited = [meta('parent'), event(usage(100, 0, 0)), event(usage(150, 0, 0))]
        parent = self.write('parent', [*inherited, event(usage(900, 0, 0))])
        child = self.write('child', [meta('child', forked_from_id='parent', forked_from_ordinal_exclusive=3),
                                     *inherited, event(usage(180, 0, 0))])
        entries = codex.parse_file(child, history=codex.HistoryResolver([parent, child]))
        self.assertEqual([e.input_tokens for e in entries], [0, 0, 30])

    def test_all_one_hour_writes_need_only_the_one_hour_rate(self):
        entry = replace(cost_tests.CostTests.entry('test'), input_tokens=0, output_tokens=0,
                        cache_read_tokens=0, cache_creation_tokens=1000000, cache_creation_1h_tokens=1000000)
        rates = {'input_cost_per_token': 2e-6, 'cache_creation_input_token_cost_above_1hr': 4e-6}
        self.assertAlmostEqual(pricing_fetcher.calculate_cost(entry, {'test': rates}), 4)
        rates['cache_creation_input_token_cost_above_1hr'] = 0
        self.assertEqual(pricing_fetcher.calculate_cost(entry, {'test': rates}), 0)

    def test_parent_cutoff_beyond_available_prefix_is_unknown(self):
        inherited = [meta('parent'), event(usage(100, 0, 0))]
        parent = self.write('parent', inherited)
        child = self.write('child', [meta('child', forked_from_id='parent', forked_from_ordinal_exclusive=10),
                                     *inherited, event(usage(180, 0, 0))])
        errors = []
        entries = codex.parse_file(child, history=codex.HistoryResolver([parent, child]), source_errors=errors)
        self.assertEqual(entries, [])
        self.assertTrue(errors)
    def test_fresh_archive_and_unknown_model_zero_correction(self):
        parent, child = self.copied()
        archive = UsageArchive(self.root / 'fresh-archive.sqlite3')
        errors = []
        history = codex.HistoryResolver([parent, child])
        entries, _ = archive.collect([parent, child],
            lambda path: codex.parse_file(path, source_errors=errors, history=history),
            lambda path: json.dumps([path.stat().st_mtime_ns, history.signature(path)]), errors, timezone.utc)
        self.assertEqual(sum(e.input_tokens for e in entries), 180)
        self.assertEqual(sum(e.input_tokens for e in entries if e.session_id == 'child'), 30)
        self.assertEqual(errors, [])
        unknown = self.write('unknown', [{'type': 'session_meta', 'payload': {
            'id': 'unknown', 'forked_from_id': 'missing', 'subagent_history_start_ordinal': 2}},
            event(usage(100, 0, 0)), turn('gpt-5.5'), event(usage(130, 0, 0))])
        zero, own = codex.parse_file(unknown)
        self.assertEqual((zero.model, zero.cost_usd, zero.message_count), ('unknown', 0.0, 0))
        self.assertEqual((own.model, own.input_tokens), ('gpt-5.5', 30))
