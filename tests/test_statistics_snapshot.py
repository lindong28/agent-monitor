import json
import sqlite3
import subprocess
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timedelta, timezone
from dataclasses import replace
from pathlib import Path
from unittest import mock

from aggregators import LoadedEntries
import exporter
import llm_attempts
from parsers import UsageEntry
import statistics_snapshot as snapshots
from tests.test_llm_attempts import LedgerFixture, NOW, valid_completed_ledger


class StatisticsSnapshotTests(unittest.TestCase):
    def test_publication_retains_current_when_source_archive_is_reset(self):
        import generation
        import sync
        from machine_config import Machine
        machine = Machine("macbook", "macbook", False)
        root = self.root / "generations"
        manifests = []
        for name, entries in (("first", self.usage()), ("reset", LoadedEntries([]))):
            with mock.patch.object(exporter, "exporter_version", return_value="a" * 40):
                manifests.append(exporter.export_bundle(
                    self.root / "rollup.db", self.root / name,
                    entries_loader=lambda: entries, rate_limits={},
                    source_host_identity="host-v1:" + "1" * 64,
                    generated_at=NOW.isoformat(), rollup_now=lambda: NOW,
                ))
        current = sync.install_export_bundle(machine, self.root / "first",
            expected_manifest=manifests[0], root=root, accept_first_use=True)
        before = current.meta["generation_id"]
        current.close()
        with self.assertRaises(generation.GenerationValidationError):
            sync.install_export_bundle(machine, self.root / "reset",
                expected_manifest=manifests[1], root=root)
        current = generation.read_current_generation("macbook", root=root)
        self.assertEqual(current.meta["generation_id"], before)
        self.assertEqual(snapshots.read_snapshot(current.db_path)["usage_entries"], self.usage())
        current.close()

    def test_retention_refuses_downgrade_missing_events_and_missing_ledger(self):
        old = self.snapshot("old")
        missing = self.root / "legacy.db"
        with closing(sqlite3.connect(missing)) as conn:
            conn.execute("CREATE TABLE legacy (id INTEGER)")
        with self.assertRaises(snapshots.StatisticsSnapshotError):
            snapshots.ensure_retained(old, missing)
        empty = self.root / "empty.db"
        snapshots.write_snapshot(empty, [], ledger_path=self.root / "missing", observed_at=NOW)
        with self.assertRaises(snapshots.StatisticsSnapshotError):
            snapshots.ensure_retained(old, empty)
        fixture, with_ledger = self.source("ledger")
        with self.assertRaises(snapshots.StatisticsSnapshotError):
            snapshots.ensure_retained(with_ledger, old)
        snapshots.ensure_retained(old, with_ledger)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def usage(self):
        entries = LoadedEntries([UsageEntry(NOW, "session", "message", "request", "model", 10, 2, 0, 0,
                                           None, "project", "claude")])
        entries.history_authoritative_from = "2026-09-01"
        return entries

    def snapshot(self, name, *, fixture=None, observed_at=NOW):
        path = self.root / (name + ".db")
        snapshots.write_snapshot(path, self.usage(), ledger_path=fixture.path if fixture else self.root / "missing",
                                 observed_at=observed_at)
        return path

    def source(self, name, **attempt_kwargs):
        fixture = LedgerFixture(self.root / (name + "-ledger.db"))
        fk = fixture.request("same-request", "2026-08-31T01:00:00Z")
        fixture.attempt(fk, "same-attempt", "2026-08-31T01:01:00Z", **attempt_kwargs)
        return fixture, self.snapshot(name, fixture=fixture)

    def calls(self, sources, **query):
        return llm_attempts.llm_calls({"range": ["all"], **query}, sources=sources, now=NOW, snapshot_reader=snapshots.read_snapshot)

    def imported(self, entries, *, ledger=None):
        import generation
        import sync
        from machine_config import Machine
        bundle = self.root / "admitted-bundle"
        with mock.patch.object(exporter, "exporter_version", return_value="a" * 40), \
             mock.patch.object(llm_attempts, "DEFAULT_LEDGER_PATH", ledger or self.root / "missing"):
            manifest = exporter.export_bundle(self.root / "admitted-rollup.db", bundle,
                entries_loader=lambda: entries, rate_limits={}, ledger_path=ledger,
                source_host_identity="host-v1:" + "1" * 64,
                generated_at=NOW.isoformat(), rollup_now=lambda: NOW)
        current = sync.install_export_bundle(Machine("macbook", "macbook", False), bundle,
            expected_manifest=manifest, root=self.root / "generations", accept_first_use=True)
        current.close()
        current = generation.read_current_generation("macbook", root=self.root / "generations")
        self.addCleanup(current.close)
        return current

    def test_admitted_usage_matches_full_reader_at_boundaries_and_filters(self):
        import server
        start = NOW.replace(hour=0, minute=0, second=0, microsecond=1)
        end = start + timedelta(days=1)
        entries = LoadedEntries([replace(self.usage()[0], timestamp=stamp,
            session_id="session-%s" % (i % 2), message_id=str(i), request_id=str(i),
            agent_id=("claude", "codex")[i % 2], project=("project", "quote' OR 1=1 --")[i % 2],
            model=("model", "other")[i % 2]) for i, stamp in enumerate((
                start - timedelta(days=10), start - timedelta(microseconds=1), start,
                start + timedelta(hours=1), end, end + timedelta(microseconds=1),
                end + timedelta(days=10)))])
        current = self.imported(entries)
        full = snapshots.read_snapshot(current.db_path)["usage_entries"]
        for time_range in (None, (start, end)):
            for query in ({}, {"agent": ["codex"]}, {"project": ["quote' OR 1=1 --"]},
                          {"model": ["other", "model"]}, {"agent": ["claude"], "model": ["other"]}):
                with self.subTest(time_range=time_range, query=query):
                    expected = server._filter_values(server._filter_time(full, time_range), query)
                    actual = server._filter_time(snapshots.read_admitted_usage(
                        current, time_range=time_range, query=query), time_range)
                    self.assertEqual(actual, expected)
        for session_id in ("session-0", "session-1", "missing"):
            self.assertEqual(snapshots.read_admitted_usage(current, session_id=session_id),
                             [e for e in full if e.session_id == session_id])
        current.close()
        with self.assertRaises(snapshots.StatisticsSnapshotError):
            snapshots.read_admitted_usage(current)

    def test_admitted_gateway_preserves_parent_windows_filters_and_costs(self):
        fixture = LedgerFixture(self.root / "domain-ledger.db")
        old = fixture.request("old-parent", "2026-07-01T00:00:00Z")
        fixture.attempt(old, "old-child", "2026-07-01T00:00:01Z", cost_state="unknown")
        fixture.attempt(old, "recent-child", "2026-08-31T01:01:00Z", attempt_no=2, parent_attempt_id="old-child")
        recent = fixture.request("recent-parent", "2026-08-31T01:00:00Z")
        fixture.attempt(recent, "recent", "2026-08-31T01:01:00Z", cost_state="estimated", cost_value=3)
        current = self.imported(self.usage(), ledger=fixture.path)
        sources = [("macbook", current.db_path)]
        for query in ({"range": ["all"]}, {"range": ["7d"], "page_size": ["1"]},
                      {"range": ["7d"], "attempt_outcome": ["success"]}):
            for endpoint in (llm_attempts.llm_calls, llm_attempts.llm_call_filters):
                with self.subTest(query=query, endpoint=endpoint.__name__):
                    values = {"range": query["range"]} if endpoint is llm_attempts.llm_call_filters else query
                    expected = endpoint(values, sources=sources, now=NOW, snapshot_reader=snapshots.read_snapshot)
                    actual = endpoint(values, sources=sources, now=NOW,
                        snapshot_reader=lambda path: snapshots.read_admitted_gateway(current))
                    self.assertEqual(actual, expected)
        # The usage reader must not revisit unrelated Gateway projections.
        with mock.patch.object(snapshots, "_validate_gateway", side_effect=AssertionError("full validation")):
            self.assertEqual(snapshots.read_admitted_usage(current), self.usage())
            self.assertEqual(snapshots.read_admitted_gateway(current)["gateway"]["state"], "available")

    def test_llm_page_route_reads_one_admitted_snapshot_and_normalizes_ui_filters(self):
        from contextlib import nullcontext
        from types import SimpleNamespace
        import server
        fixture, _ = self.source("page")
        current = self.imported(self.usage(), ledger=fixture.path)
        load_observation = llm_attempts._load_observation
        def fixed_observation(values, **kwargs):
            kwargs["now"] = NOW
            return load_observation(values, **kwargs)
        query = {"range": ["all"], "machine": ["removed-machine"]}
        with mock.patch.object(server.hub, "enabled", return_value=True), \
             mock.patch.object(server.generation, "generation_admission_snapshot",
                 side_effect=lambda: nullcontext(SimpleNamespace(admitted=[current]))), \
             mock.patch.object(llm_attempts, "_load_observation", side_effect=fixed_observation), \
             mock.patch.object(snapshots, "read_admitted_gateway", wraps=snapshots.read_admitted_gateway) as reader:
            actual = server.ROUTES["/api/llm-calls-page"](query)
            self.assertEqual(reader.call_count, 1)
            self.assertEqual(actual["filters"], server.llm_call_filters_endpoint({"range": ["all"]}))
            self.assertEqual(actual["calls"], server.llm_calls_endpoint({"range": ["all"]}))
            self.assertEqual(server.llm_calls_endpoint(query)["requests"]["matching_count"], 0)
        self.assertEqual(actual["calls"]["requests"]["matching_count"], 1)

    def test_admission_rejects_tampering_even_in_unselected_domain(self):
        import generation
        current = self.imported(self.usage())
        root = self.root / "generations"
        current.close()
        with closing(sqlite3.connect(current.db_path)) as conn, conn:
            conn.execute("INSERT INTO statistics_attempts VALUES (0, '{}')")
        with self.assertRaises(generation.GenerationValidationError):
            generation.read_current_generation("macbook", root=root)

    def test_usage_roundtrip_and_safe_metadata(self):
        data = snapshots.read_snapshot(self.snapshot("usage"))
        self.assertEqual(data["usage_entries"], self.usage())
        self.assertEqual(data["history_authoritative_from"], "2026-09-01")
        self.assertEqual(data["gateway"]["state"], "missing")
        self.assertNotIn("source_errors", data)
        self.assertEqual(data["observed_at"], NOW.isoformat())

    def test_v3_archive_and_v5_source_preserve_funding_and_cost_partitions(self):
        from llm_gateway.ledger import Ledger

        fixture = LedgerFixture(self.root / "funding-ledger.db")
        cases = (
            ("company", "company_paid", "exact", "USD", "usd_per_request"),
            ("personal", "personal_paid", "estimated", "EUR", "per_request"),
            ("company", "subscription", "exact", "USD", "usd_per_request"),
            ("personal", "subscription", "estimated", "USD", "usd_per_request"),
            ("company", "subscription", "not_incurred", None, None),
            ("personal", "subscription", "not_incurred", None, None),
            ("company", "company_paid", "unknown", None, None),
        )
        for index, (scope, category, cost_state, currency, basis) in enumerate(cases):
            fk = fixture.request("request-%s" % index, "2026-08-31T01:00:00Z")
            fixture.attempt(fk, "attempt-%s" % index, "2026-08-31T01:01:00Z",
                            billing_scope=scope, funding_type=category, cost_state=cost_state,
                            cost_currency=currency, cost_basis=basis, cost_value=index + 1)
        archive = self.snapshot("immutable-v3-archive", fixture=fixture)
        original_archive = archive.read_bytes()
        original_ledger = fixture.path.read_bytes()
        query = {"range": ["all"]}
        before = llm_attempts.llm_calls(query, db_path=fixture.path, now=NOW, snapshot_reader=snapshots.read_snapshot)
        self.assertEqual(before["ledger"]["schema_version"], 3)
        self.assertEqual(fixture.path.read_bytes(), original_ledger)
        self.assertTrue(Ledger.migrate_known_predecessor(fixture.path))
        after = llm_attempts.llm_calls(query, db_path=fixture.path, now=NOW, snapshot_reader=snapshots.read_snapshot)
        for field in ("requests", "attempts", "request_summary", "attempt_summary", "cost_summary"):
            self.assertEqual(before[field], after[field], field)
        # The current writer adds resources in v7 without changing funding rows.
        self.assertEqual(after["ledger"]["schema_version"], 7)
        current = self.snapshot("v7-current", fixture=fixture)
        for path, version in ((archive, 3), (current, 7)):
            raw = snapshots.read_snapshot(path)["gateway"]
            self.assertEqual(raw["schema_version"], version)
            credential = json.loads(raw["attempts"][0]["credential_source_json"])
            self.assertEqual("funding_source" in credential, version >= 4)
            self.assertEqual("billing_scope" in credential, version == 3)
        snapshots.ensure_retained(archive, current)
        old_calls, new_calls = self.calls([("machine", archive)]), self.calls([("machine", current)])
        for field in ("requests", "attempts", "request_summary", "attempt_summary", "cost_summary"):
            self.assertEqual(old_calls[field], new_calls[field], field)
        rows = new_calls["attempts"]["items"]
        self.assertEqual({row["funding_source"] for row in rows},
                         {"company_paid", "personal_paid", "company_subscription", "personal_subscription"})
        self.assertTrue(all("billing_scope" not in row and "funding_type" not in row for row in rows))
        groups = new_calls["cost_summary"]["monetary_subtotals"]
        subscriptions = [g for g in groups if g["funding_type"] == "subscription"]
        self.assertEqual(len(subscriptions), 1)
        self.assertEqual((subscriptions[0]["exact_amount"], subscriptions[0]["estimated_amount"]), (3, 4))
        self.assertEqual(new_calls["cost_summary"]["no_per_call_charge_attempts"], 2)
        self.assertEqual(new_calls["attempt_summary"]["unknown_cost_attempts"], 1)
        mixed = self.calls([("old-machine", archive), ("new-machine", current)])
        self.assertEqual(mixed["projection_version"], 4)
        self.assertIsNone(mixed["ledger"]["schema_version"])
        self.assertEqual([s["schema_version"] for s in mixed["sources"]], [3, 7])
        filters = llm_attempts.llm_call_filters(query, sources=[("old-machine", archive), ("new-machine", current)], now=NOW, snapshot_reader=snapshots.read_snapshot)
        self.assertEqual(filters["projection_version"], 4)
        self.assertEqual(archive.read_bytes(), original_archive)

    def test_reader_rejects_legacy_cross_payer_and_mislabeled_snapshot_without_writes(self):
        fixture, snapshot = self.source("cross-payer", billing_scope="company", funding_type="company_paid")
        with closing(sqlite3.connect(fixture.path)) as conn, conn:
            raw = json.loads(conn.execute("SELECT credential_source_json FROM attempts").fetchone()[0])
            raw["funding_type"] = "personal_paid"
            conn.execute("UPDATE attempts SET credential_source_json=?", (json.dumps(raw),))
        before = fixture.path.read_bytes()
        with self.assertRaises(llm_attempts.ProjectionError) as error:
            llm_attempts.llm_calls({"range": ["all"]}, db_path=fixture.path, now=NOW, snapshot_reader=snapshots.read_snapshot)
        self.assertEqual(error.exception.code, "invalid_ledger_data")
        self.assertEqual(fixture.path.read_bytes(), before)
        with closing(sqlite3.connect(snapshot)) as conn, conn:
            meta = json.loads(conn.execute("SELECT payload FROM statistics_meta").fetchone()[0])
            meta["gateway"]["schema_version"] = 4
            conn.execute("UPDATE statistics_meta SET payload=?", (json.dumps(meta),))
        before = snapshot.read_bytes()
        with self.assertRaises(snapshots.StatisticsSnapshotError):
            snapshots.read_snapshot(snapshot)
        self.assertEqual(snapshot.read_bytes(), before)

    def test_missing_extension_is_distinct_from_empty_and_corrupt(self):
        legacy = self.root / "legacy.db"
        with closing(sqlite3.connect(legacy)) as conn:
            conn.execute("CREATE TABLE legacy (id INTEGER)")
        self.assertEqual(snapshots.read_snapshot(legacy)["state"], "not_collected")
        path = self.snapshot("collected")
        self.assertEqual(snapshots.read_snapshot(path)["state"], "available")
        with closing(sqlite3.connect(path)) as conn:
            conn.execute("DROP TABLE statistics_attempts")
        with self.assertRaises(snapshots.StatisticsSnapshotError):
            snapshots.read_snapshot(path)

    def test_cache_creation_subset_is_validated_on_write_and_read(self):
        entry = replace(self.usage()[0], cache_creation_tokens=10, cache_creation_1h_tokens=5)
        path = self.root / "cache.db"
        snapshots.write_snapshot(path, [entry], ledger_path=self.root / "missing", observed_at=NOW)
        self.assertEqual(snapshots.read_snapshot(path)["usage_entries"][0], entry)
        invalid = replace(entry, cache_creation_tokens=0)
        with self.assertRaises(ValueError):
            snapshots.write_snapshot(self.root / "invalid.db", [invalid], ledger_path=self.root / "missing", observed_at=NOW)
        with closing(sqlite3.connect(path)) as conn, conn:
            raw = json.loads(conn.execute("SELECT payload FROM statistics_usage").fetchone()[0])
            raw["cache_creation_tokens"] = 0
            conn.execute("UPDATE statistics_usage SET payload=?", (json.dumps(raw),))
        with self.assertRaises(snapshots.StatisticsSnapshotError):
            snapshots.read_snapshot(path)

    def test_three_sources_colliding_ids_and_timestamps_paginate_without_loss(self):
        sources = [(name, self.source(name)[1]) for name in ("macbook", "macmini", "macstudio")]
        payload = self.calls(sources, page_size=["1"])
        requests, attempts = [], []
        while True:
            requests.extend(row["machine"] for row in payload["requests"]["items"])
            attempts.extend(row["machine"] for row in payload["attempts"]["items"])
            for attempt in payload["attempts"]["items"]:
                self.assertEqual(attempt["machine"], attempt["parent_request"]["machine"])
                self.assertEqual(attempt["machine"], attempt["attempt_id"]["machine"])
            if not payload["requests"]["next_cursor"]:
                break
            payload = self.calls(sources, page_size=["1"], request_cursor=[payload["requests"]["next_cursor"]],
                                 attempt_cursor=[payload["attempts"]["next_cursor"]])
        self.assertEqual(requests, ["macstudio", "macmini", "macbook"])
        self.assertEqual(attempts, requests)
        self.assertEqual(payload["requests"]["matching_count"], 3)

    def test_machine_filter_and_cost_partitions(self):
        sources = [("a", self.source("a", cost_value=2)[1]),
                   ("b", self.source("b", cost_state="estimated", cost_value=3, cost_currency="EUR",
                                    cost_basis="per_request", funding_type="personal_paid", billing_scope="personal")[1]),
                   ("c", self.source("c", cost_state="unknown")[1])]
        payload = self.calls(sources)
        self.assertEqual(payload["attempt_summary"]["unknown_cost_attempts"], 1)
        costs = payload["cost_summary"]["monetary_subtotals"]
        self.assertEqual([(row["currency"], row["funding_type"], row["cost_basis"]) for row in costs],
                         [("EUR", "personal_paid", "per_request"), ("USD", "company_paid", "usd_per_request")])
        one = self.calls(sources, machine=["b"])
        self.assertEqual(one["requests"]["matching_count"], 1)
        self.assertEqual(one["attempts"]["matching_count"], 1)
        self.assertEqual(one["cost_summary"]["monetary_subtotals"][0]["estimated_amount"], 3)
        dimensions = llm_attempts.llm_call_filters({"range": ["all"]}, sources=sources, now=NOW, snapshot_reader=snapshots.read_snapshot)
        self.assertEqual(dimensions["request_dimensions"]["machines"], ["a", "b", "c"])

    def test_source_observation_times_and_high_watermarks_not_http_time(self):
        fixture, first = self.source("a")
        later = datetime(2026, 9, 1, tzinfo=timezone.utc)
        second = self.snapshot("b", fixture=fixture, observed_at=later)
        payload = llm_attempts.llm_calls({"range": ["all"]}, sources=[("a", first), ("b", second)],
                                         now=datetime(2026, 9, 8, tzinfo=timezone.utc), snapshot_reader=snapshots.read_snapshot)
        self.assertEqual(payload["as_of"], NOW.isoformat())
        self.assertEqual(payload["sources"][1]["observed_at"], later.isoformat())
        self.assertEqual(payload["high_watermark"]["b"]["attempt_ingest_sequence"], 1)

    def test_empty_sources_does_not_fall_back_to_local_ledger(self):
        with mock.patch.object(llm_attempts, "_read_observation", side_effect=AssertionError("local read")):
            self.assertEqual(self.calls([])["ledger"]["state"], "not_collected")
            self.assertIsNone(self.calls([])["as_of"])

    def test_invalid_source_fails_closed_without_discarding_it(self):
        _, good = self.source("good")
        bad = self.snapshot("bad")
        with closing(sqlite3.connect(bad)) as conn, conn:
            conn.execute("UPDATE statistics_meta SET payload='{}'")
        with self.assertRaises(llm_attempts.ProjectionError) as error:
            self.calls([("good", good), ("bad", bad)])
        self.assertEqual(error.exception.code, "invalid_statistics_snapshot")

    def test_observation_timestamp_rejects_missing_empty_and_wrong_type(self):
        path = self.snapshot("invalid-time")
        for invalid in (None, "", 42, "2026-09-01"):
            with self.subTest(observed_at=invalid):
                with closing(sqlite3.connect(path)) as conn, conn:
                    meta = json.loads(conn.execute("SELECT payload FROM statistics_meta").fetchone()[0])
                    meta["observed_at"] = invalid
                    conn.execute("UPDATE statistics_meta SET payload=?", (json.dumps(meta),))
                with self.assertRaises(snapshots.StatisticsSnapshotError):
                    snapshots.read_snapshot(path)

    def test_existing_in_flight_attempt_updates_on_next_snapshot(self):
        fixture = LedgerFixture(self.root / "mutable-ledger.db")
        fk = fixture.request("mutable", "2026-08-31T01:00:00Z", outcome="in_flight", active_attempt_id="active")
        fixture.attempt(fk, "active", "2026-08-31T01:01:00Z", outcome="in_flight", cost_state="unknown", latency_ms=None)
        first = self.snapshot("before", fixture=fixture)
        self.assertEqual(self.calls([("a", first)])["attempts"]["items"][0]["outcome"], "in_flight")
        with closing(sqlite3.connect(fixture.path)) as conn, conn:
            conn.execute("UPDATE logical_requests SET request_outcome='success', active_attempt_id=NULL")
            conn.execute("UPDATE attempts SET outcome='success', latency_ms=30, usage_state='reported', usage_json=?",
                         (json.dumps({"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}),))
        second = self.snapshot("after", fixture=fixture)
        self.assertEqual(self.calls([("a", second)])["attempts"]["items"][0]["outcome"], "success")
        self.assertEqual(self.calls([("a", first)])["attempts"]["items"][0]["outcome"], "in_flight")
        self.assertEqual(self.calls([("a", first)])["high_watermark"], self.calls([("a", second)])["high_watermark"])

    def test_no_unknown_raw_fields_or_credential_payload_can_enter_extension(self):
        _, path = self.source("safe")
        with closing(sqlite3.connect(path)) as conn, conn:
            raw = json.loads(conn.execute("SELECT payload FROM statistics_attempts").fetchone()[0])
            raw["prompt"] = "must not travel"
            conn.execute("UPDATE statistics_attempts SET payload=?", (json.dumps(raw),))
        with self.assertRaises(snapshots.StatisticsSnapshotError):
            snapshots.validate_snapshot(path)

    def test_export_uses_one_loaded_snapshot_and_digest_binds_extension(self):
        entries = self.usage()
        loader = mock.Mock(return_value=entries)
        with mock.patch.object(exporter, "exporter_version", return_value="a" * 40), \
             mock.patch.object(llm_attempts, "DEFAULT_LEDGER_PATH", self.root / "do-not-read"):
            manifest = exporter.export_bundle(self.root / "rollup.db", self.root / "bundle", entries_loader=loader,
                                               source_host_identity="host-v1:" + "1" * 64,
                                               generated_at=NOW.isoformat(), rollup_now=lambda: NOW, rate_limits={})
        self.assertEqual(loader.call_count, 1)
        path = self.root / "bundle" / "snapshot.db"
        self.assertEqual(snapshots.read_snapshot(path)["usage_entries"], entries)
        with closing(sqlite3.connect(path)) as conn, conn:
            conn.execute("UPDATE statistics_usage SET payload='{}'")
        with self.assertRaisesRegex(exporter.ExportError, "transfer_digest"):
            exporter.validate_export_manifest(manifest, path)
        manifest["transfer_digest"] = exporter._file_digest(path)
        manifest["manifest_digest"] = exporter.manifest_digest(manifest)
        with self.assertRaisesRegex(exporter.ExportError, "statistics extension"):
            exporter.validate_export_manifest(manifest, path)

    def test_retry_parent_namespaces_and_dual_time_windows(self):
        sources = []
        for name in ("a", "b", "c"):
            fixture = LedgerFixture(self.root / (name + "-retry.db"))
            fk = fixture.request("same-request", "2026-07-01T00:00:00Z")
            fixture.attempt(fk, "first", "2026-08-31T01:00:00Z", outcome="http_error", cost_state="unknown")
            fixture.attempt(fk, "second", "2026-08-31T01:01:00Z", attempt_no=2, parent_attempt_id="first")
            sources.append((name, self.snapshot(name, fixture=fixture)))
        payload = llm_attempts.llm_calls({"range": ["7d"], "attempt_outcome": ["success"]}, sources=sources, now=NOW, snapshot_reader=snapshots.read_snapshot)
        self.assertEqual(payload["requests"]["matching_count"], 0)
        self.assertEqual(payload["attempts"]["matching_count"], 3)
        for row in payload["attempts"]["items"]:
            self.assertEqual(row["parent_attempt_id"], {"machine": row["machine"], "id": "first"})
            self.assertEqual(row["parent_request"]["machine"], row["machine"])
        dimensions = llm_attempts.llm_call_filters({"range": ["7d"]}, sources=sources, now=NOW, snapshot_reader=snapshots.read_snapshot)["request_dimensions"]
        self.assertEqual(dimensions["machines"], ["a", "b", "c"])
        self.assertEqual(dimensions["projects"], ["philo-prompt"])
        self.assertEqual(dimensions["logical_models"], ["claude-text"])
        self.assertEqual(dimensions["request_outcomes"], ["success"])
        self.assertTrue(dimensions["session_refs"])
        selected = llm_attempts.llm_calls({"range": ["7d"], "machine": ["b"]}, sources=sources, now=NOW, snapshot_reader=snapshots.read_snapshot)
        self.assertEqual(selected["requests"]["matching_count"], 0)
        self.assertEqual(selected["attempts"]["matching_count"], 2)

    def test_source_is_read_only_and_exact_schema_only(self):
        fixture = valid_completed_ledger(self.root / "readonly.db")
        original = fixture.path.read_bytes()
        self.snapshot("first", fixture=fixture)
        self.assertEqual(fixture.path.read_bytes(), original)
        with closing(sqlite3.connect(fixture.path)) as conn:
            conn.execute("ALTER TABLE attempts ADD COLUMN prompt TEXT")
        with self.assertRaises(llm_attempts.ProjectionError) as error:
            self.snapshot("unsupported", fixture=fixture)
        self.assertEqual(error.exception.code, "unsupported_ledger_schema")

    def test_refresh_waits_for_collection_before_reading_filters_and_calls(self):
        script = r'''
const fs = require("fs");
const events = [];
let release;
const gate = new Promise(resolve => { release = resolve; });
function node() {
  return { children: [], value: "", textContent: "", className: "",
    setAttribute() {}, appendChild(child) { this.children.push(child); },
    removeChild(child) { this.children.splice(this.children.indexOf(child), 1); },
    cloneNode() { return node(); },
    get firstChild() { return this.children[0]; },
    get options() { return this.children; } };
}
const nodes = {};
function qs(selector) { if (!nodes[selector]) { nodes[selector] = node(); nodes[selector].children.push(node()); } return nodes[selector]; }
global.window = { location: { origin: "http://example.test" } };
global.document = { createElement: node, querySelector: qs };
global.AgentMonitor = { qs, getRange: () => "all", params: () => new URLSearchParams(), integer: String,
  pageScope: () => () => true,
  refreshStatistics: () => { events.push("refresh"); return gate; },
  renderSyncStatus: () => events.push("sync-render") };
global.fetch = async url => {
  events.push(url.pathname);
  const empty = { items: [], matching_count: 0, next_cursor: null };
  const calls = {
    ledger: { state: "not_collected" }, requests: empty, attempts: empty,
    request_summary: {}, attempt_summary: {}, range: { value: "all" },
    cost_summary: { monetary_subtotals: [] }, request_selection: {},
  };
  const payload = url.pathname === "/api/llm-calls-page" ? {
    calls, filters: {request_dimensions: {}, attempt_dimensions: {}}
  } : {};
  return { ok: true, json: async () => payload };
};
eval(fs.readFileSync("web/llm-calls.js", "utf8").replace("window.AgentMonitorLLMCalls = { init };", "window.AgentMonitorLLMCalls = { init, reloadRange };"));
(async () => {
  const pending = window.AgentMonitorLLMCalls.reloadRange(true);
  await Promise.resolve();
  const before = [...events];
  release();
  await pending;
  process.stdout.write(JSON.stringify({ before, after: events }));
})().catch(error => { console.error(error); process.exitCode = 1; });
'''
        result = subprocess.run(["node", "-e", script], cwd=Path(__file__).resolve().parents[1],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        result = json.loads(result.stdout)
        self.assertEqual(result["before"], ["refresh"])
        self.assertEqual(result["after"][0], "refresh")
        self.assertEqual(result["after"].count("/api/llm-calls-page"), 1)
        self.assertNotIn("/api/llm-call-filters", result["after"])
        self.assertNotIn("/api/llm-calls", result["after"])
        self.assertIn("sync-render", result["after"])


if __name__ == "__main__":
    unittest.main()
