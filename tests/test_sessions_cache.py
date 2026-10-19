import tempfile
import unittest
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
                        mock.patch.object(server, "_maybe_sync_remotes"),
                        mock.patch.object(server, "_SESSION_CACHE", SessionCache()),
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
        with mock.patch.object(server, "_SESSION_CACHE", SessionCache(max_entries=0)):
            self.assertEqual(actual, server.sessions_endpoint(query))

    def test_reuse_keeps_responses_independent_and_only_changed_source_rebuilds(self):
        with mock.patch.object(server.statistics_snapshot, "read_admitted_usage", wraps=server.statistics_snapshot.read_admitted_usage) as read:
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
                with mock.patch.object(server.statistics_snapshot, "read_admitted_usage", side_effect=AssertionError("unexpected repeated decode")):
                    self.assertEqual(first, server.sessions_endpoint(query))
                self.assert_uncached_equal(query, first)

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
