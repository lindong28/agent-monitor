import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timedelta, timezone
from dataclasses import replace
from pathlib import Path
from unittest import mock

from aggregators import LoadedEntries
import exporter
from parsers import UsageEntry
import statistics_snapshot as snapshots
NOW = datetime(2026, 8, 31, 12, 0, tzinfo=timezone.utc)


class StatisticsSnapshotTests(unittest.TestCase):
    def test_publication_retains_current_when_source_archive_is_reset(self):
        import generation
        import sync
        from machine_config import Machine
        machine = Machine("node-b", "node-b", False)
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
        current = generation.read_current_generation("node-b", root=root)
        self.assertEqual(current.meta["generation_id"], before)
        self.assertEqual(snapshots.read_snapshot(current.db_path)["usage_entries"], self.usage())
        current.close()

    def test_retention_refuses_downgrade_missing_events(self):
        old = self.snapshot("old")
        missing = self.root / "legacy.db"
        with closing(sqlite3.connect(missing)) as conn:
            conn.execute("CREATE TABLE legacy (id INTEGER)")
        with self.assertRaises(snapshots.StatisticsSnapshotError):
            snapshots.ensure_retained(old, missing)
        empty = self.root / "empty.db"
        snapshots.write_snapshot(empty, [], observed_at=NOW)
        with self.assertRaises(snapshots.StatisticsSnapshotError):
            snapshots.ensure_retained(old, empty)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def usage(self):
        entries = LoadedEntries([UsageEntry(NOW, "session", "message", "request", "model", 10, 2, 0, 0,
                                           None, "project", "claude")])
        entries.history_authoritative_from = "2026-09-01"
        return entries

    def snapshot(self, name, *, observed_at=NOW):
        path = self.root / (name + ".db")
        snapshots.write_snapshot(path, self.usage(),
                                 observed_at=observed_at)
        return path

    def imported(self, entries):
        import generation
        import sync
        from machine_config import Machine
        bundle = self.root / "admitted-bundle"
        with mock.patch.object(exporter, "exporter_version", return_value="a" * 40):
            manifest = exporter.export_bundle(self.root / "admitted-rollup.db", bundle,
                entries_loader=lambda: entries, rate_limits={},
                source_host_identity="host-v1:" + "1" * 64,
                generated_at=NOW.isoformat(), rollup_now=lambda: NOW)
        current = sync.install_export_bundle(Machine("node-b", "node-b", False), bundle,
            expected_manifest=manifest, root=self.root / "generations", accept_first_use=True)
        current.close()
        current = generation.read_current_generation("node-b", root=self.root / "generations")
        self.addCleanup(current.close)
        return current

    def test_unpriced_pushdown_excludes_known_zero_before_decoding(self):
        entries = LoadedEntries([replace(self.usage()[0], message_id=str(i), request_id=str(i), cost_usd=cost)
                                 for i, cost in enumerate((None, 0, 3))])
        current = self.imported(entries)
        with mock.patch.object(snapshots, "_usage_entry", wraps=snapshots._usage_entry) as decode:
            result = snapshots.read_admitted_usage(current, query={"cost_status": ["unknown"]})
            self.assertEqual(result, [entries[0]])
            self.assertEqual(decode.call_count, 1)
        self.assertEqual(len(snapshots.read_admitted_usage(current)), 3)

    def test_admitted_reader_reuses_only_identical_valid_metadata_within_request(self):
        entries = LoadedEntries([replace(self.usage()[0], message_id=str(i),
            request_id=str(i), service_context={"speed": "fast" if i < 3 else "standard"})
            for i in range(6)])
        current = self.imported(entries)
        with mock.patch.object(snapshots, "validate_usage_metadata", wraps=snapshots.validate_usage_metadata) as validate:
            self.assertEqual(snapshots.read_admitted_usage(current), entries)
            self.assertEqual(validate.call_count, 2)
            self.assertEqual(snapshots.read_admitted_usage(current), entries)
            self.assertEqual(validate.call_count, 4)

    def test_admitted_reader_still_rejects_changed_invalid_metadata_and_amount(self):
        entries = LoadedEntries([replace(self.usage()[0], message_id=str(i), request_id=str(i)) for i in range(2)])
        current = self.imported(entries)
        with closing(sqlite3.connect(current.db_path)) as conn:
            position, payload = conn.execute("SELECT position, payload FROM statistics_usage ORDER BY position DESC LIMIT 1").fetchone()
        original = json.loads(payload)
        for change in ({"service_context": "invalid"}, {"usage_quality": True},
                       {"input_tokens": -1}, {"cost_usd": True},
                       {"cost_provenance": {"basis": "unpriced", "sources": [], "verified_at": None,
                                            "time_basis": "unknown", "reason": "unknown_model"}, "cost_usd": 0}):
            with self.subTest(change=change):
                with closing(sqlite3.connect(current.db_path)) as conn:
                    conn.execute("UPDATE statistics_usage SET payload=? WHERE position=?", (json.dumps({**original, **change}), position))
                    conn.commit()
                with self.assertRaises(snapshots.StatisticsSnapshotError):
                    snapshots.read_admitted_usage(current)

    def test_identical_provenance_is_revalidated_when_amount_nullness_changes(self):
        provenance = {"basis": "unpriced", "sources": [], "verified_at": None,
                      "time_basis": "unknown", "reason": "unknown_model"}
        entries = LoadedEntries([replace(self.usage()[0], message_id=str(i), request_id=str(i),
                                         cost_provenance=provenance) for i in range(2)])
        current = self.imported(entries)
        self.assertEqual(snapshots.read_admitted_usage(current), entries)
        with closing(sqlite3.connect(current.db_path)) as conn:
            position, payload = conn.execute("SELECT position, payload FROM statistics_usage ORDER BY position DESC LIMIT 1").fetchone()
            row = json.loads(payload)
            row["cost_usd"] = 0
            conn.execute("UPDATE statistics_usage SET payload=? WHERE position=?", (json.dumps(row), position))
            conn.commit()
        with self.assertRaises(snapshots.StatisticsSnapshotError):
            snapshots.read_admitted_usage(current)

    def test_admitted_conversation_keeps_ancestry_outside_window_and_decodes_only_members(self):
        from session_identity import codex_metadata
        metadata = [codex_metadata("root", "cli"), codex_metadata("child",
            {"subagent": {"thread_spawn": {"parent_thread_id": "root"}}}),
            codex_metadata("nested", {"subagent": {"thread_spawn": {"parent_thread_id": "child"}}})]
        entries = LoadedEntries([replace(self.usage()[0], agent_id="codex", session_id=sid,
            timestamp=NOW + timedelta(days=i), message_id=sid, request_id=sid)
            for i, sid in enumerate(("root", "child", "nested", "unrelated"))], session_metadata=metadata)
        current = self.imported(entries)
        window = (NOW + timedelta(days=2), NOW + timedelta(days=2, hours=1))
        with mock.patch.object(snapshots, "_usage_entry", wraps=snapshots._usage_entry) as decode:
            result = snapshots.read_admitted_usage(current, session_id="nested", session_agent="codex", time_range=window)
            # Date pushdown deliberately widens by one day; exact filtering is the API's job.
            self.assertEqual(result, entries[1:3])
            self.assertEqual(decode.call_count, 2)
            self.assertEqual(result.session_metadata, metadata)
        import server
        from types import SimpleNamespace
        with mock.patch("server.hub.enabled", return_value=True), mock.patch(
                "server.generation.generation_admission_snapshot") as admission:
            admission.return_value.__enter__.return_value = SimpleNamespace(admitted=[current])
            query = {"range": ["custom"], "start": ["2026-09-02"], "end": ["2026-09-02"], "session_agent": ["codex"]}
            detail = server.session_detail("nested", "node-b", query)
            self.assertEqual(detail["meta"]["session_id"], "root")
            self.assertEqual([e["session_id"] for e in detail["entries"]], ["nested"])
            all_detail = server.session_detail("root", "node-b", {**query, "scope": ["all"]})
            self.assertEqual([e["session_id"] for e in all_detail["entries"]], ["root", "child", "nested"])

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

    def test_admission_rejects_tampering_even_in_unselected_domain(self):
        import generation
        current = self.imported(self.usage())
        root = self.root / "generations"
        current.close()
        with closing(sqlite3.connect(current.db_path)) as conn, conn:
            conn.execute("CREATE TABLE unrelated_bytes (id INTEGER)")
        with self.assertRaises(generation.GenerationValidationError):
            generation.read_current_generation("node-b", root=root)

    def test_usage_indexes_bound_scanning_without_changing_results(self):
        entries = [replace(self.usage()[0], timestamp=NOW + timedelta(days=i),
                           session_id=str(i), message_id=str(i), request_id=str(i))
                   for i in range(2000)]
        path = self.root / "indexed.db"
        snapshots.write_snapshot(path, entries, observed_at=NOW)
        with closing(sqlite3.connect(path)) as conn:
            queries = [
                ("SELECT payload FROM statistics_usage WHERE "
                 "substr(json_extract(payload, '$.timestamp'), 1, 10) BETWEEN ? AND ? ORDER BY position",
                 (NOW.date().isoformat(), (NOW + timedelta(days=2)).date().isoformat())),
                ("SELECT payload FROM statistics_usage WHERE "
                 "json_extract(payload, '$.session_id') = ? ORDER BY position", ("1000",)),
            ]
            def measure(sql, args):
                steps = []
                conn.set_progress_handler(lambda: steps.append(1) or 0, 1)
                try:
                    return conn.execute(sql, args).fetchall(), len(steps)
                finally:
                    conn.set_progress_handler(None, 0)
            indexed = [measure(*query) for query in queries]
            conn.execute("DROP INDEX idx_usage_date")
            conn.execute("DROP INDEX idx_usage_session")
            unindexed = [measure(*query) for query in queries]
            for (rows, steps), (old_rows, old_steps) in zip(indexed, unindexed):
                self.assertEqual(rows, old_rows)
                self.assertTrue(rows)
                self.assertLess(steps, old_steps / 10)
        self.assertEqual(snapshots.read_snapshot(path)["usage_entries"], entries)

    def test_usage_roundtrip_and_safe_metadata(self):
        data = snapshots.read_snapshot(self.snapshot("usage"))
        self.assertEqual(data["usage_entries"], self.usage())
        self.assertEqual(data["history_authoritative_from"], "2026-09-01")
        self.assertNotIn("gateway", data)
        self.assertNotIn("source_errors", data)
        self.assertEqual(data["observed_at"], NOW.isoformat())

    def test_v1_usage_is_readable_without_decoding_retired_gateway_payload(self):
        legacy = self.snapshot("v1")
        with closing(sqlite3.connect(legacy)) as conn, conn:
            meta = json.loads(conn.execute("SELECT payload FROM statistics_meta").fetchone()[0])
            meta.update(version=1, gateway={"schema_version": "future-schema"})
            meta.pop("usage_quality_counts", None)
            conn.execute("UPDATE statistics_meta SET payload=?", (json.dumps(meta),))
            conn.execute("DROP TABLE statistics_sessions")
            for position, payload in conn.execute("SELECT position, payload FROM statistics_usage").fetchall():
                row = json.loads(payload)
                for key in snapshots.EVIDENCE_FIELDS:
                    row.pop(key, None)
                conn.execute("UPDATE statistics_usage SET payload=? WHERE position=?", (json.dumps(row), position))
            for table in ("statistics_requests", "statistics_attempts"):
                conn.execute(f"CREATE TABLE {table} (position INTEGER PRIMARY KEY, payload TEXT NOT NULL)")
                conn.execute(f"INSERT INTO {table} VALUES (0, 'not JSON')")
        before = legacy.read_bytes()
        self.assertEqual(snapshots.read_snapshot(legacy)["usage_entries"], self.usage())
        self.assertNotIn("gateway", snapshots.read_metadata(legacy))
        snapshots.ensure_retained(legacy, self.snapshot("v2"))
        self.assertEqual(legacy.read_bytes(), before)
        empty = self.root / "empty.db"
        snapshots.write_snapshot(empty, [], observed_at=NOW)
        with self.assertRaises(snapshots.StatisticsSnapshotError):
            snapshots.ensure_retained(legacy, empty)

    def test_missing_extension_is_distinct_from_empty_and_corrupt(self):
        legacy = self.root / "legacy.db"
        with closing(sqlite3.connect(legacy)) as conn:
            conn.execute("CREATE TABLE legacy (id INTEGER)")
        self.assertEqual(snapshots.read_snapshot(legacy)["state"], "not_collected")
        path = self.snapshot("collected")
        self.assertEqual(snapshots.read_snapshot(path)["state"], "available")
        with closing(sqlite3.connect(path)) as conn:
            conn.execute("DROP TABLE statistics_usage")
        with self.assertRaises(snapshots.StatisticsSnapshotError):
            snapshots.read_snapshot(path)

    def test_cache_creation_subset_is_validated_on_write_and_read(self):
        entry = replace(self.usage()[0], cache_creation_tokens=10, cache_creation_1h_tokens=5)
        path = self.root / "cache.db"
        snapshots.write_snapshot(path, [entry], observed_at=NOW)
        self.assertEqual(snapshots.read_snapshot(path)["usage_entries"][0], entry)
        invalid = replace(entry, cache_creation_tokens=0)
        with self.assertRaises(ValueError):
            snapshots.write_snapshot(self.root / "invalid.db", [invalid], observed_at=NOW)
        with closing(sqlite3.connect(path)) as conn, conn:
            raw = json.loads(conn.execute("SELECT payload FROM statistics_usage").fetchone()[0])
            raw["cache_creation_tokens"] = 0
            conn.execute("UPDATE statistics_usage SET payload=?", (json.dumps(raw),))
        with self.assertRaises(snapshots.StatisticsSnapshotError):
            snapshots.read_snapshot(path)


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

    def test_export_uses_one_loaded_snapshot_and_digest_binds_extension(self):
        entries = self.usage()
        loader = mock.Mock(return_value=entries)
        with mock.patch.object(exporter, "exporter_version", return_value="a" * 40):
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

if __name__ == "__main__":
    unittest.main()
