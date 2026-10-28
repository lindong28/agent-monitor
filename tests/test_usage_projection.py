"""Persistent accounting projection correctness and historical work bounds."""
import json
import math
import sqlite3
import tempfile
import unittest
from contextlib import closing
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from agent_monitor.accounting import pricing_fetcher, usage_buckets, usage_projection, rollup
from agent_monitor.ingestion.usage_archive import UsageArchive, _encode
from agent_monitor.contracts.usage import UsageEntry


class UsageProjectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.archive = UsageArchive(self.root / "archive.db")
        self.entry = UsageEntry(datetime(2026, 10, 28, tzinfo=timezone.utc), "s", "m", "r", "gpt-5",
                                100, 2, 0, 0, None, "source", "codex")
        self.pricing = {"gpt-5": {"input_cost_per_token": 0.000001,
                                   "output_cost_per_token": 0.000002}}
        self.context = {"projects": {"source": "project"}, "blocked_paths": set(),
                        "metadata": {"blocked_sources": [], "history_authoritative_projects": None}}
        self.values = {}
        self.versions = {}

    def source(self, name, values):
        self.values[self.root / name] = values
        self.versions[self.root / name] = self.versions.get(self.root / name, 0) + 1

    def collect(self, materialize=False):
        errors = []
        def prepare(conn):
            return usage_projection.prepare_usage(conn, self.pricing, self.context, errors,
                                                   materialize=materialize)
        result, authority = self.archive.collect(self.values, lambda p: self.values[p],
            lambda p: str(self.versions[p]), errors, timezone.utc, prepare=prepare)
        result.history_authoritative_from = authority
        return result

    def assert_buckets(self, prepared, expected):
        project = usage_buckets._bucket_entries(expected)
        usage = usage_buckets._bucket_usage_entries(expected)
        for actual, wanted in ((prepared.project_buckets, project), (prepared.usage_buckets, usage)):
            self.assertEqual(actual.keys(), wanted.keys())
            for key in wanted:
                for field, value in wanted[key].items():
                    if field == "cost_usd":
                        self.assertAlmostEqual(actual[key][field], value, places=12)
                    else:
                        self.assertEqual(actual[key][field], value)

    def test_normal_round_prices_only_changed_keys_at_two_history_sizes(self):
        for count in (20, 200):
            with self.subTest(count=count):
                self.archive = UsageArchive(self.root / (str(count) + ".db"))
                initial = [replace(self.entry, message_id=str(i)) for i in range(count)]
                self.source("history", initial)
                self.collect()
                # A new object proves that reuse is persisted, not process-local.
                self.archive = UsageArchive(self.archive.path)
                with mock.patch.object(pricing_fetcher, "with_calculated_cost", wraps=pricing_fetcher.with_calculated_cost) as price:
                    unchanged = self.collect()
                    self.assertEqual(price.call_count, 0)
                    self.source("new", [replace(self.entry, message_id="new")])
                    updated = self.collect()
                    self.assertEqual(price.call_count, 1)
                expected = [replace(pricing_fetcher.with_calculated_cost(e, self.pricing), project="project")
                            for e in initial + self.values[self.root / "new"]]
                self.assert_buckets(updated, expected)
                self.assertEqual(sum(b["entry_count"] for b in unchanged.usage_buckets.values()), count)
                self.values.pop(self.root / "new")

    def test_repricing_blocking_and_unblocking_rebuild(self):
        self.source("one", [self.entry, replace(self.entry, message_id="unknown", model="unpriced")])
        first = self.collect()
        self.pricing["gpt-5"]["input_cost_per_token"] *= 2
        second = self.collect()
        self.assertGreater(next(iter(second.usage_buckets.values()))["cost_usd"], next(iter(first.usage_buckets.values()))["cost_usd"])
        self.context["blocked_paths"].add("source")
        blocked = self.collect()
        self.assertEqual(blocked.project_buckets, {})
        self.assertEqual(blocked.usage_buckets, second.usage_buckets)
        full = self.collect(materialize=True)
        self.assertEqual(len(full), 0)
        self.assertEqual(len(full.unattributed_entries), 2)
        self.context["blocked_paths"].clear()
        self.context["projects"]["source"] = "renamed"
        self.assertEqual({k[2] for k in self.collect().project_buckets}, {"renamed"})

    def test_project_invalidation_only_prices_affected_history(self):
        self.source("one", [replace(self.entry, message_id=str(i)) for i in range(20)])
        self.collect()
        self.source("two", [replace(self.entry, message_id="new", project="新项目")])
        self.context["projects"]["新项目"] = "second"
        with mock.patch.object(pricing_fetcher, "with_calculated_cost", wraps=pricing_fetcher.with_calculated_cost) as price:
            added = self.collect()
            self.assertEqual(price.call_count, 1)
            self.context["blocked_paths"].add("新项目")
            blocked = self.collect()
            self.assertEqual(price.call_count, 2)
            self.assertEqual(sum(b["entry_count"] for b in blocked.project_buckets.values()), 20)
            self.assertEqual(blocked.usage_buckets, added.usage_buckets)
            self.context["blocked_paths"].clear()
            self.context["projects"]["新项目"] = "renamed"
            self.assertIn("renamed", {k[2] for k in self.collect().project_buckets})
            self.assertEqual(price.call_count, 3)
            self.context["projects"].pop("新项目")
            self.assertIn("新项目", {k[2] for k in self.collect().project_buckets})
            self.assertEqual(price.call_count, 4)

    def test_full_and_incremental_cost_summation_match_exactly(self):
        for count in (6, 100):
            with self.subTest(count=count):
                values = [replace(self.entry, message_id=str(i), cost_usd=0.1) for i in range(count)]
                self.source("one", values)
                with mock.patch.object(pricing_fetcher, "with_calculated_cost", side_effect=lambda entry, **kw: entry):
                    prepared = self.collect()
                    materialized = self.collect(materialize=True)
                self.assertEqual(prepared.usage_buckets, usage_buckets._bucket_usage_entries(materialized))
                self.assertEqual(prepared.project_buckets, usage_buckets._bucket_entries(materialized))
                self.assertEqual(next(iter(prepared.usage_buckets.values()))["cost_usd"], math.fsum([0.1] * count))

    def test_correction_removes_empty_bucket_and_no_subtraction_drift(self):
        self.source("one", [self.entry])
        self.collect()
        for index in range(20):
            changed = replace(self.entry, model="gpt-5" if index % 2 else "unknown", input_tokens=index + 1)
            self.source("one", [changed])
            self.assert_buckets(self.collect(), [replace(pricing_fetcher.with_calculated_cost(changed, self.pricing), project="project")])
        self.values.clear()
        self.assertEqual(sum(b["entry_count"] for b in self.collect().usage_buckets.values()), 1)

    def test_projection_failure_rolls_back_raw_and_recovers(self):
        self.source("one", [self.entry])
        before = self.collect()
        self.source("one", [replace(self.entry, input_tokens=200)])
        with mock.patch.object(usage_projection, "_contribute", side_effect=RuntimeError("injected")):
            with self.assertRaisesRegex(RuntimeError, "injected"):
                self.collect()
        self.assertEqual(self.archive.read()[0][0].input_tokens, 100)
        after = self.collect()
        self.assertEqual(next(iter(after.usage_buckets.values()))["input_tokens"], 200)
        self.assertEqual(next(iter(before.usage_buckets.values()))["input_tokens"], 100)

    def test_old_writer_after_source_disappears_and_missing_projection(self):
        self.source("one", [self.entry])
        self.collect()
        self.values.clear()
        with closing(sqlite3.connect(self.archive.path)) as conn, conn:
            conn.execute("UPDATE usage_events SET entry=?", (json.dumps(_encode(replace(self.entry, input_tokens=700))),))
        self.assertEqual(next(iter(self.collect().usage_buckets.values()))["input_tokens"], 700)
        with closing(sqlite3.connect(self.archive.path)) as conn, conn:
            conn.execute("DELETE FROM archive_meta WHERE key='usage_projection_signature'")
        self.assertEqual(next(iter(self.collect().usage_buckets.values()))["input_tokens"], 700)

    def test_missing_derived_table_rebuilds_both_tables(self):
        self.source("one", [self.entry])
        expected = self.collect()
        for table in ("prepared_usage", "prepared_buckets"):
            with self.subTest(table=table):
                with closing(sqlite3.connect(self.archive.path)) as conn, conn:
                    conn.execute("DROP TABLE " + table)
                actual = self.collect()
                self.assertEqual(actual.project_buckets, expected.project_buckets)
                self.assertEqual(actual.usage_buckets, expected.usage_buckets)

    def test_real_rollup_consumes_complete_prepared_buckets_and_replays(self):
        self.source("one", [self.entry])
        db = self.root / "rollup.db"
        run = lambda: rollup.run(db_path=db, entries_loader=self.collect, now=lambda: self.entry.timestamp)
        run()
        self.source("two", [replace(self.entry, message_id="second", input_tokens=20)])
        with mock.patch.object(rollup, "_connect", side_effect=RuntimeError("rollup failure")):
            with self.assertRaisesRegex(RuntimeError, "rollup failure"):
                run()
        run()
        with closing(sqlite3.connect(db)) as conn:
            self.assertEqual(conn.execute("SELECT sum(input_tokens) FROM daily_usage_rollup").fetchone()[0], 120)


if __name__ == "__main__":
    unittest.main()
