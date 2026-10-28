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
        priced_key = next(key for key in first.usage_buckets if key[-1] == "gpt-5")
        self.assertGreater(second.usage_buckets[priced_key]["cost_usd"], first.usage_buckets[priced_key]["cost_usd"])
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

    def test_price_invalidation_only_touches_dependent_models(self):
        for count in (20, 200):
            with self.subTest(count=count):
                self.archive = UsageArchive(self.root / ("prices-%s.db" % count))
                self.source("one", [replace(self.entry, message_id=str(i)) for i in range(count)] +
                            [replace(self.entry, message_id="other", model="other"),
                             replace(self.entry, message_id="null", model=None)])
                self.collect()
                with mock.patch.object(pricing_fetcher, "with_calculated_cost", wraps=pricing_fetcher.with_calculated_cost) as price:
                    self.pricing["unused"] = {"input_cost_per_token": 2}
                    self.collect()
                    self.assertEqual(price.call_count, 0)
                    self.pricing["other"] = {"input_cost_per_token": 0.01, "output_cost_per_token": 0.02}
                    self.collect()
                    self.assertEqual(price.call_count, 1)
                    self.pricing["other"]["verified_at"] = "2026-10-28"
                    self.collect()
                    self.assertEqual(price.call_count, 2)
                    del self.pricing["other"]
                    result = self.collect()
                    self.assertEqual(price.call_count, 3)
                complete = self.collect(materialize=True)
                self.assert_buckets(result, complete)
                with closing(sqlite3.connect(self.archive.path)) as conn:
                    plan = conn.execute("EXPLAIN QUERY PLAN SELECT dedup_key FROM usage_events WHERE json_quote(json_extract(entry, '$.model'))=?", ('"other"',)).fetchall()
                    self.assertTrue(any("usage_events_model" in row[-1] for row in plan))
                    self.assertEqual(dict(conn.execute("SELECT model,event_count FROM usage_model_counts")),
                                     {'"gpt-5"': count, '"other"': 1, 'null': 1})

    def test_equal_threshold_member_order_invalidates_price(self):
        entry = replace(self.entry, input_tokens=2000, output_tokens=1)
        self.source("one", [entry])
        fields = [("input_cost_per_token", 1), ("output_cost_per_token", 1),
                  ("input_cost_per_token_above_1k_tokens", 2),
                  ("output_cost_per_token_above_1k_tokens", 2),
                  ("input_cost_per_token_above_1000_tokens", 3),
                  ("output_cost_per_token_above_1000_tokens", 3)]
        self.pricing["gpt-5"] = dict(fields)
        first = self.collect(materialize=True)[0].cost_usd
        self.pricing["gpt-5"] = dict(fields[:2] + fields[4:] + fields[2:4])
        second = self.collect(materialize=True)[0].cost_usd
        self.assertEqual((first, second), (6003.0, 4002.0))

    def test_price_dependencies_include_nested_modes_geo_and_sources(self):
        self.pricing = pricing_fetcher.PricingTable(self.pricing, {"gpt-5": "first-source"})
        self.pricing["gpt-5"].update(
            service_modes={"fast": {"input_cost_per_token": 0.01,
                                    "output_cost_per_token": 0.02, "source": "fast-source"}},
            inference_geo_us={"multiplier": 2, "source": "geo-source"})
        entry = replace(self.entry, service_context={"speed": "fast", "inference_geo": "us"})
        self.source("one", [entry, replace(self.entry, message_id="normal")])
        self.collect()
        for mutate in (
            lambda: self.pricing["gpt-5"]["service_modes"]["fast"].update(input_cost_per_token=0.03),
            lambda: self.pricing["gpt-5"]["inference_geo_us"].update(multiplier=3),
            lambda: self.pricing.sources.update({"gpt-5": "second-source"}),
        ):
            mutate()
            actual = {e.message_id: e for e in self.collect(materialize=True)}
            for raw in self.values[self.root / "one"]:
                expected = replace(pricing_fetcher.with_calculated_cost(raw, self.pricing), project="project")
                self.assertEqual(actual[raw.message_id], expected)

    def test_model_count_tracks_old_writer_and_recovers_missing_table(self):
        self.source("one", [self.entry])
        self.collect()
        self.values.clear()
        with closing(sqlite3.connect(self.archive.path)) as conn, conn:
            conn.execute("UPDATE usage_events SET entry=?", (json.dumps(_encode(replace(self.entry, model=None))),))
            self.assertEqual(dict(conn.execute("SELECT model,event_count FROM usage_model_counts")), {'null': 1})
        self.assertIsNone(self.collect(materialize=True)[0].model)
        with closing(sqlite3.connect(self.archive.path)) as conn, conn:
            conn.execute("DROP TABLE usage_model_counts")
        self.assertIsNone(self.collect(materialize=True)[0].model)

    def test_missing_model_table_recovers_before_changed_sources_write(self):
        for change in ("new", "correction", "removed"):
            with self.subTest(change=change):
                self.archive = UsageArchive(self.root / (change + ".db"))
                self.values.clear()
                self.source("one", [self.entry])
                self.collect()
                with closing(sqlite3.connect(self.archive.path)) as conn, conn:
                    conn.execute("DROP TABLE usage_model_counts")
                if change == "new":
                    self.source("two", [replace(self.entry, message_id="new", model="other")])
                elif change == "correction":
                    self.source("one", [replace(self.entry, input_tokens=200, model=None)])
                else:
                    self.values.clear()
                full = self.collect(materialize=True)
                self.assertEqual(len(full), 2 if change == "new" else 1)
                self.assertEqual(full[0].input_tokens, 200 if change == "correction" else 100)
                with closing(sqlite3.connect(self.archive.path)) as conn:
                    self.assertEqual(sum(n for _, n in conn.execute("SELECT model,event_count FROM usage_model_counts")), len(full))
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

    def test_compact_projection_preserves_complete_entries_and_old_deletion(self):
        for cost in (None, 0.0, 0.1):
            with self.subTest(cost=cost):
                entry = replace(self.entry, cost_usd=cost, model="unpriced",
                                service_context={"service_tier": "default"},
                                cache_creation_1h_tokens=5, usage_quality="exact")
                self.source("one", [entry])
                expected = replace(pricing_fetcher.with_calculated_cost(entry, self.pricing), project="project")
                self.assertEqual(self.collect(materialize=True), [expected])
                with closing(sqlite3.connect(self.archive.path)) as conn, conn:
                    payload = json.loads(conn.execute("SELECT entry FROM prepared_usage").fetchone()[0])
                    self.assertNotIn("session_id", payload)
                    conn.execute("DELETE FROM usage_events")
                    result = usage_projection.prepare_usage(conn, self.pricing, self.context, [], materialize=False)
                    self.assertEqual(result.project_buckets, {})
                    self.assertEqual(result.usage_buckets, {})
                # A new observation must refill the archive on the next iteration.
                self.versions[self.root / "one"] += 1

    def test_legacy_full_projection_is_rebuilt_before_decoding(self):
        self.source("one", [self.entry])
        expected = self.collect(materialize=True)
        with closing(sqlite3.connect(self.archive.path)) as conn, conn:
            conn.execute("UPDATE prepared_usage SET entry=?", (json.dumps(_encode(expected[0])),))
            conn.execute("UPDATE archive_meta SET value='legacy-code' WHERE key='usage_projection_signature'")
        self.assertEqual(self.collect(materialize=True), expected)

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
