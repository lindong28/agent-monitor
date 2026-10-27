import tempfile
import unittest
import json
import sqlite3
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from aggregators import LoadedEntries
import exporter
import generation
from machine_config import Machine
from parsers import UsageEntry
from session_cache import SessionCache
from conversation_cache import ConversationCache
from session_identity import codex_metadata
import server
import sync


NOW = datetime(2026, 10, 19, 7, tzinfo=timezone.utc)


class Clock(datetime):
    value = NOW

    @classmethod
    def now(cls, tz=None):
        return cls.value.astimezone(tz)


class SessionsCacheTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.serial = 0
        self.base = UsageEntry(NOW - timedelta(hours=1), "session", "message", "request", "A",
                               10, 2, 0, 0, None, "repo", "codex")
        self.entries = LoadedEntries([self.base, replace(self.base, session_id="second", model="B",
                                                        message_id="second", cost_usd=0)])
        self.currents = [self.publish("one", self.entries), self.publish("two", self.entries)]
        Clock.value = NOW
        for patcher in (mock.patch.object(server, "datetime", Clock),
                        mock.patch.object(server.hub, "enabled", return_value=True),
                        mock.patch.object(server.COLLECTION, "request"),
                        mock.patch.object(server, "_SESSION_CACHE", SessionCache()),
                        mock.patch.object(server, "_CONVERSATION_CACHE", ConversationCache()),
                        mock.patch.object(server.generation, "generation_admission_snapshot")):
            value = patcher.start()
            self.addCleanup(patcher.stop)
        value.return_value.__enter__.side_effect = lambda: SimpleNamespace(admitted=self.currents)
        self.query = {"range": ["30d"], "with_filters": ["1"], "sync": ["0"]}

    def publish(self, host, entries):
        self.serial += 1
        bundle = self.root / str(self.serial)
        with mock.patch.object(exporter, "exporter_version", return_value="a" * 40):
            manifest = exporter.export_bundle(self.root / (host + ".db"), bundle,
                entries_loader=lambda: entries, rate_limits={},
                source_host_identity="host-v1:" + "1" * 64,
                generated_at=(NOW + timedelta(seconds=self.serial)).isoformat(), rollup_now=lambda: NOW)
        current = sync.install_export_bundle(Machine(host, host, False), bundle,
            expected_manifest=manifest, root=self.root / "generations", accept_first_use=True)
        current.close()
        current = generation.read_current_generation(host, root=self.root / "generations")
        self.addCleanup(current.close)
        return current

    def assert_uncached_equal(self, query, actual):
        with mock.patch.object(server, "_SESSION_CACHE", SessionCache(max_entries=0)), \
             mock.patch.object(server._CONVERSATION_CACHE, "read", return_value=None):
            self.assertEqual(actual, server.sessions_endpoint(query))

    def test_reuse_keeps_responses_independent_and_only_changed_source_rebuilds(self):
        with mock.patch.object(server.statistics_snapshot, "admitted_usage_rows", wraps=server.statistics_snapshot.admitted_usage_rows) as read:
            first = server.sessions_endpoint(self.query)
            first["sessions"][0]["model"] = "mutation"
            first["filters"]["model"].append("mutation")
            second = server.sessions_endpoint(self.query)
            self.assertNotEqual("mutation", second["sessions"][0]["model"])
            self.assertNotIn("mutation", second["filters"]["model"])
            self.assertEqual(read.call_count, 2)
            Clock.value += timedelta(minutes=1)
            self.assertEqual(second, server.sessions_endpoint(self.query))
            self.assertEqual(read.call_count, 2)
            self.currents[0] = self.publish("one", LoadedEntries([*self.entries,
                replace(self.base, session_id="new", message_id="new", timestamp=NOW)]))
            changed = server.sessions_endpoint(self.query)
            self.assertEqual(read.call_count, 3)
            self.assertEqual(len(changed["sessions"]), len(second["sessions"]) + 1)
        self.assert_uncached_equal(self.query, changed)
        self.currents.pop()
        self.assertTrue(all(row["machine"] == "one" for row in server.sessions_endpoint(self.query)["sessions"]))

    def test_query_dimensions_and_global_sort_match_uncached_results(self):
        queries = [self.query, {**self.query, "agent": ["codex"]},
                   {**self.query, "model": ["B"]}, {**self.query, "project": ["repo"]},
                   {**self.query, "cost_status": ["unknown"]},
                   {**self.query, "machine": ["one", "two"]},
                   {**self.query, "machine": ["two", "one"]},
                   {**self.query, "sort": ["cost"], "order": ["asc"]},
                   {"range": ["all"]},
                   {"range": ["custom"], "start": ["2026-10-18"], "end": ["2026-10-19"]}]
        for query in queries:
            with self.subTest(query=query):
                first = server.sessions_endpoint(query)
                with mock.patch.object(server.statistics_snapshot, "admitted_usage_rows", side_effect=AssertionError("unexpected repeated decode")):
                    self.assertEqual(first, server.sessions_endpoint(query))
                self.assert_uncached_equal(query, first)

    def test_new_generation_decodes_only_changed_conversation(self):
        server.sessions_endpoint(self.query)
        self.currents[0] = self.publish("one", LoadedEntries([*self.entries,
            replace(self.base, session_id="new", message_id="new", timestamp=NOW)]))
        with mock.patch.object(server.statistics_snapshot, "_usage_entry",
                               wraps=server.statistics_snapshot._usage_entry) as decode:
            actual = server.sessions_endpoint(self.query)
            self.assertEqual(decode.call_count, 1)
        self.assert_uncached_equal(self.query, actual)

    def test_old_event_and_root_metadata_changes_rebuild_only_affected_groups(self):
        metadata = [codex_metadata("session", "cli", title="original"),
                    codex_metadata("child", {"subagent": {"thread_spawn": {"parent_thread_id": "session"}}}),
                    codex_metadata("second", "cli")]
        entries = LoadedEntries([*self.entries, replace(self.base, session_id="child", message_id="child")],
                                session_metadata=metadata)
        self.currents = [self.publish("one", entries)]
        server.sessions_endpoint(self.query)
        entries[2] = replace(entries[2], cost_usd=3., input_tokens=20)
        self.currents = [self.publish("one", entries)]
        with mock.patch.object(server.statistics_snapshot, "_usage_entry", wraps=server.statistics_snapshot._usage_entry) as decode:
            changed = server.sessions_endpoint(self.query)
            self.assertEqual(decode.call_count, 2)
        self.assert_uncached_equal(self.query, changed)
        metadata[0] = codex_metadata("session", "cli", title="renamed")
        metadata[1] = codex_metadata("child", {"subagent": {"thread_spawn": {"parent_thread_id": "second"}}})
        entries.session_metadata = metadata
        self.currents = [self.publish("one", entries)]
        changed = server.sessions_endpoint(self.query)
        self.assertEqual(next(r for r in changed["sessions"] if r["session_id"] == "session")["title"], "renamed")
        self.assert_uncached_equal(self.query, changed)

    def test_cache_uses_current_first_visible_positions_for_stable_ties(self):
        ignored = replace(self.base, session_id="a", message_id="ignored", model="hidden", cost_usd=0)
        a = replace(ignored, message_id="a", model="visible")
        b = replace(a, session_id="b", message_id="b")
        query = {**self.query, "model": ["visible"], "sort": ["cost"]}
        self.currents = [self.publish("ties", LoadedEntries([ignored, b, a]))]
        first = server.sessions_endpoint(query)
        self.assertEqual([r["session_id"] for r in first["sessions"]], ["b", "a"])
        self.currents = [self.publish("ties", LoadedEntries([ignored, a, b]))]
        with mock.patch.object(server.statistics_snapshot, "_usage_entry", wraps=server.statistics_snapshot._usage_entry) as decode:
            second = server.sessions_endpoint(query)
            self.assertEqual(decode.call_count, 0)
        self.assertEqual([r["session_id"] for r in second["sessions"]], ["a", "b"])
        self.assert_uncached_equal(query, second)

    def change_payload(self, transform):
        # Simulate a newly admitted signature, including direct-publication data
        # which the normal importer would reject before it reaches this reader.
        current = self.currents[0]
        with closing(sqlite3.connect(current.db_path)) as conn:
            position, payload = conn.execute("SELECT position,payload FROM statistics_usage ORDER BY position LIMIT 1").fetchone()
            conn.execute("UPDATE statistics_usage SET payload=? WHERE position=?", (transform(payload), position))
            conn.commit()
        stat = current.db_path.stat()
        self.currents[0] = replace(current, _signature=(stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns))
        self.addCleanup(self.currents[0].close)

    def test_changed_invalid_metadata_is_not_hidden_by_conversation_cache(self):
        self.currents = self.currents[:1]
        server.sessions_endpoint(self.query)
        self.change_payload(lambda raw: json.dumps({**json.loads(raw), "service_context": "invalid"}))
        with self.assertRaises(server.statistics_snapshot.StatisticsSnapshotError):
            server.sessions_endpoint(self.query)

    def test_duplicate_identity_never_inherits_a_same_root_suitability_proof(self):
        metadata = [codex_metadata(sid, "cli") for sid in ("r", "other")]
        metadata += [codex_metadata(sid, {"subagent": {"thread_spawn": {"parent_thread_id": "r"}}}) for sid in ("a", "b")]
        self.currents = [self.publish("duplicate", LoadedEntries([
            replace(self.base, session_id="b")], session_metadata=metadata))]
        self.change_payload(lambda raw: '{"session_id":"a",' + raw[1:])
        with mock.patch.object(server.statistics_snapshot, "read_admitted_usage", wraps=server.statistics_snapshot.read_admitted_usage) as fallback:
            first = server.sessions_endpoint(self.query)
            self.assertEqual(fallback.call_count, 1)
        self.assertEqual(first["sessions"][0]["session_id"], "r")
        with closing(sqlite3.connect(self.currents[0].db_path)) as conn:
            for position, raw in conn.execute("SELECT position,payload FROM statistics_sessions").fetchall():
                row = json.loads(raw)
                if row["session_id"] == "b":
                    row["parent_session_id"] = "other"
                    conn.execute("UPDATE statistics_sessions SET payload=? WHERE position=?", (json.dumps(row), position))
            conn.commit()
        self.change_payload(lambda raw: raw)
        second = server.sessions_endpoint(self.query)
        self.assertEqual(second["sessions"][0]["session_id"], "other")
        self.assert_uncached_equal(self.query, second)

    def test_large_integers_and_escaped_surrogate_identity_preserve_python_values(self):
        self.currents = self.currents[:1]
        self.change_payload(lambda raw: json.dumps({**json.loads(raw), "session_id": "surrogate-\ud800", "input_tokens": 2 ** 80}))
        first = server.sessions_endpoint(self.query)
        row = next(r for r in first["sessions"] if r["session_id"] == "surrogate-\ud800")
        self.assertEqual(row["tokens"], 2 ** 80 + 2)
        self.change_payload(lambda raw: raw)
        self.assertEqual(first, server.sessions_endpoint(self.query))
        self.assert_uncached_equal(self.query, first)

    def test_conversation_retention_limits_do_not_truncate_results(self):
        for cache in (ConversationCache(max_entries=1), ConversationCache(max_bytes=1)):
            with mock.patch.object(server, "_CONVERSATION_CACHE", cache), \
                 mock.patch.object(server, "_SESSION_CACHE", SessionCache(max_entries=0)):
                first = server.sessions_endpoint(self.query)
                self.assertEqual(first, server.sessions_endpoint(self.query))
                self.assertLessEqual(len(cache._items), cache.max_entries)
                self.assertLessEqual(cache._bytes, cache.max_bytes)
                self.assert_uncached_equal(self.query, first)

    def test_legacy_statistics_versions_match_the_original_reader(self):
        for version in (1, 2, 3):
            with self.subTest(version=version):
                self.currents = [self.publish("legacy-" + str(version), self.entries)]
                with closing(sqlite3.connect(self.currents[0].db_path)) as conn:
                    meta = json.loads(conn.execute("SELECT payload FROM statistics_meta").fetchone()[0])
                    meta["version"] = version
                    if version < 3:
                        meta.pop("usage_quality_counts")
                        for position, raw in conn.execute("SELECT position,payload FROM statistics_usage").fetchall():
                            row = json.loads(raw)
                            for key in ("usage_quality", "service_context", "cost_provenance"):
                                row.pop(key)
                            conn.execute("UPDATE statistics_usage SET payload=? WHERE position=?", (json.dumps(row), position))
                    if version == 1:
                        meta["gateway"] = {}
                        for name in ("statistics_requests", "statistics_attempts"):
                            conn.execute(f"CREATE TABLE {name} (position INTEGER PRIMARY KEY, payload TEXT NOT NULL)")
                    conn.execute("DROP TABLE statistics_sessions")
                    conn.execute("UPDATE statistics_meta SET payload=?", (json.dumps(meta),))
                    conn.commit()
                first = server.sessions_endpoint(self.query)
                self.change_payload(lambda raw: raw)
                self.assertEqual(first, server.sessions_endpoint(self.query))
                self.assert_uncached_equal(self.query, first)

    def test_future_records_clock_rollback_and_midnight_never_return_stale_window(self):
        self.currents = [self.publish("future", LoadedEntries([self.base,
            replace(self.base, message_id="future", timestamp=NOW + timedelta(minutes=1))]))]
        first = server.sessions_endpoint(self.query)
        Clock.value += timedelta(minutes=2)
        second = server.sessions_endpoint(self.query)
        self.assertEqual(second["sessions"][0]["tokens"], first["sessions"][0]["tokens"] * 2)
        for instant in (NOW, NOW + timedelta(hours=10), NOW + timedelta(days=1), NOW + timedelta(days=30)):
            Clock.value = instant
            self.assert_uncached_equal(self.query, server.sessions_endpoint(self.query))

    def test_admission_error_and_closed_lease_cannot_be_hidden_by_cache(self):
        server.sessions_endpoint(self.query)
        with mock.patch.object(server.generation, "generation_admission_snapshot", side_effect=ValueError("invalid admission")):
            with self.assertRaises(ValueError):
                server.sessions_endpoint(self.query)
        self.currents[0].close()
        with self.assertRaises(server.statistics_snapshot.StatisticsSnapshotError):
            server.sessions_endpoint(self.query)

    def test_parallel_misses_and_hits_produce_independent_equal_results(self):
        with ThreadPoolExecutor(max_workers=3) as pool:
            results = list(pool.map(lambda _: server.sessions_endpoint(self.query), range(6)))
        self.assertTrue(all(result == results[0] for result in results))
        results[0]["sessions"].clear()
        self.assertTrue(results[1]["sessions"])
        self.assert_uncached_equal(self.query, results[1])

    def test_entry_and_encoded_byte_limits_evict_without_truncating_response(self):
        current = self.currents[0]
        cache = SessionCache(max_entries=1, max_bytes=100)
        cache.put(current, {}, None, [], {"rows": ["small"]})
        cache.put(current, {"agent": ["codex"]}, None, [], {"rows": ["other"]})
        self.assertIsNone(cache.get(current, {}, None))
        self.assertEqual(cache.get(current, {"agent": ["codex"]}, None), {"rows": ["other"]})
        cache.put(current, {}, None, [], {"rows": ["x" * 101]})
        self.assertIsNone(cache.get(current, {}, None))
        self.assertLessEqual(cache._bytes, 100)
        with mock.patch.object(server, "_SESSION_CACHE", SessionCache(max_bytes=1)):
            self.assert_uncached_equal(self.query, server.sessions_endpoint(self.query))


if __name__ == "__main__":
    unittest.main()
