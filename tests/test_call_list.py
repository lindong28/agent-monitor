"""Compare bounded list selection with the existing complete audit projection."""
from unittest import mock
from contextlib import closing
import sqlite3
import unittest

from aggregators import LoadedEntries
import call_list
import exporter
import generation
import llm_attempts
from machine_config import Machine
import statistics_snapshot
import sync
from tests import test_llm_call_diagnostics as diagnostics
from tests.test_llm_attempts import NOW, SESSION


class RequestListTests(unittest.TestCase):
    setUp = diagnostics.CallDiagnosticsTests.setUp
    stop_server = diagnostics.CallDiagnosticsTests.stop_server
    get = diagnostics.CallDiagnosticsTests.get

    def full(self, query):
        sources = {g.db_path: g for g in self.generations}
        self.admission_active = True
        return llm_attempts.llm_calls_page(query, now=NOW,
            sources=[(g.host, g.db_path) for g in self.generations],
            snapshot_reader=lambda path: statistics_snapshot.read_admitted_gateway(sources[path]),
            normalize_filters=call_list.SELECTORS)

    def test_page_and_filters_equal_complete_projection(self):
        for query in (
            {'range': ['all']}, {'range': ['7d'], 'machine': ['alpha']},
            {'range': ['7d'], 'project': ['one']},
            {'range': ['all'], 'session_ref': [SESSION]},
            {'range': ['all'], 'attempt_outcome': ['http_error']},
            {'range': ['7d'], 'machine': ['absent'], 'project': ['absent']},
            {'range': ['custom'], 'start': ['2026-08-31'], 'end': ['2026-08-31']},
        ):
            with self.subTest(query=query):
                expected = self.full(query)
                actual = call_list.page(query, self.generations, now=NOW)
                self.assertEqual(actual['calls']['requests']['items'], expected['calls']['requests']['items'])
                self.assertEqual(actual['calls']['requests']['next_cursor'], expected['calls']['requests']['next_cursor'])
                for key in ('sources', 'as_of', 'range', 'applied_filters'):
                    self.assertEqual(actual['calls'][key], expected['calls'][key])
                self.assertIsNone(actual['calls']['requests']['matching_count'])
                self.assertIsNone(actual['calls']['cost_summary'])
                self.assertIsNone(actual['calls']['attempts'])

    def test_equal_timestamp_cross_machine_pagination(self):
        query = {'range': ['all'], 'page_size': ['17']}
        expected = self.full({'range': ['all'], 'page_size': ['100']})['calls']['requests']['items']
        seen = []
        while True:
            page = call_list.page(query, self.generations, now=NOW)['calls']['requests']
            seen.extend(page['items'])
            if not page['next_cursor']:
                break
            query['request_cursor'] = [page['next_cursor']]
        self.assertEqual(seen[:100], expected)
        self.assertEqual(len(seen), 108)
        self.assertEqual(len({(r['machine'], r['canonical_project_id'], r['logical_request_id']) for r in seen}), 108)

    def test_http_lightweight_response_and_invalid_query(self):
        status, result = self.get('/api/llm-call-list', range='all')
        self.assertEqual(status, 200)
        self.assertEqual(len(result['calls']['requests']['items']), 50)
        self.assertIsNone(result['calls']['request_summary'])
        self.assertEqual(self.get('/api/llm-call-list', range='invalid')[0], 400)

    def test_precise_and_sqlite_unparseable_timestamps_across_pages(self):
        generations = []
        for index, name in enumerate(('alpha', 'beta')):
            with closing(sqlite3.connect(self.ledgers[index])) as conn, conn:
                ids = conn.execute("SELECT id FROM logical_requests WHERE canonical_project_id='bulk'").fetchall()
                for n, (identity,) in enumerate(ids):
                    timestamp = ('2026-08-31T02:01:30+00:00:30' if n == 0 else
                                 '2026-08-31T02:00:00.%06dZ' % (999 + n))
                    conn.execute('UPDATE logical_requests SET request_timestamp=? WHERE id=?', (timestamp, identity))
            bundle = self.root / ('precise-' + name)
            with mock.patch.object(exporter, 'exporter_version', return_value='b' * 40):
                manifest = exporter.export_bundle(self.root / ('precise-rollup-' + name), bundle,
                    entries_loader=lambda: LoadedEntries([]), rate_limits={}, ledger_path=self.ledgers[index],
                    source_host_identity='host-v1:' + str(index + 1) * 64,
                    generated_at=NOW.isoformat(), rollup_now=lambda: NOW)
            g = sync.install_export_bundle(Machine(name, name, False), bundle, expected_manifest=manifest,
                root=self.root / 'precise-generations', accept_first_use=True)
            g.close()
            g = generation.read_current_generation(name, root=self.root / 'precise-generations')
            generations.append(g); self.addCleanup(g.close)
        self.generations = generations
        q = {'range': ['all'], 'page_size': ['1'], 'project': ['bulk']}
        for _ in range(9):
            expected = self.full(q)['calls']['requests']
            actual = call_list.page(q, generations, now=NOW)['calls']['requests']
            self.assertEqual(actual['items'], expected['items'])
            self.assertEqual(actual['next_cursor'], expected['next_cursor'])
            if not actual['next_cursor']:
                break
            q['request_cursor'] = [actual['next_cursor']]
