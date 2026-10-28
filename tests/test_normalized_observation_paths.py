"""Path-normalized source caches preserve legacy writer and replay behavior."""
import json
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from agent_monitor.contracts.usage import UsageEntry
from agent_monitor.ingestion import usage_archive
from agent_monitor.ingestion.usage_archive import UsageArchive, _encode


class NormalizedObservationPathTests(unittest.TestCase):
    def setUp(self):
        root = Path(os.environ.get("ARCHIVE_TEST_ROOT", Path.home() / "task-artifacts/agent-monitor/normalized-path-tests"))
        root.mkdir(parents=True, exist_ok=True)
        self.root = Path(tempfile.mkdtemp(prefix="path-case-", dir=root))
        self.archive = UsageArchive(self.root / "archive.sqlite3")
        self.sources, self.versions = {}, {}
        self.parser = mock.Mock(side_effect=lambda path: self.sources[path])
        self.entry = UsageEntry(datetime(2026, 2, 10, tzinfo=timezone.utc),
                                "s", "m", "r", "model", 10, 0, 0, 0, None, "project", "codex")

    def source(self, name, entries):
        path = self.root / name
        self.sources[path] = entries
        self.versions[path] = self.versions.get(path, 0) + 1
        return path

    def collect(self, **kwargs):
        self.errors = []
        return self.archive.collect(self.sources, self.parser,
                                    lambda path: str(self.versions[path]),
                                    self.errors, timezone.utc, **kwargs)[0]

    def rows(self, sql):
        with closing(sqlite3.connect(self.archive.path)) as conn:
            return conn.execute(sql).fetchall()

    def seed_v1(self):
        with closing(sqlite3.connect(self.archive.path)) as conn, conn:
            conn.execute("CREATE TABLE archive_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            conn.execute("CREATE TABLE usage_events (dedup_key TEXT PRIMARY KEY, entry TEXT NOT NULL)")
            conn.execute("CREATE TABLE parsed_files (path TEXT PRIMARY KEY, signature TEXT, entries TEXT NOT NULL)")
            UsageArchive._initialize_incremental(conn)
            for path, entries in self.sources.items():
                conn.execute("INSERT INTO parsed_files VALUES (?, ?, '[]')",
                             (str(path), "observations-v1:" + str(self.versions[path])))
                for position, entry in enumerate(entries):
                    payload = json.dumps(_encode(entry))
                    conn.execute("INSERT INTO file_observations VALUES (?, ?, ?, ?)",
                                 (str(path), position, entry.dedup_key, payload))
                    conn.execute("INSERT INTO usage_events VALUES (?, ?) ON CONFLICT(dedup_key) DO NOTHING",
                                 (entry.dedup_key, payload))
            conn.execute("DELETE FROM archive_dirty")
            conn.execute("DELETE FROM archive_pending_merge")

    def assert_cache_clean(self):
        self.assertEqual(self.rows("SELECT count(*) FROM file_observations"), [(0,)])
        self.assertEqual(self.rows("SELECT count(*) FROM legacy_observation_paths_v2"), [(0,)])
        self.assertEqual(self.rows("SELECT count(*) FROM source_files_v2 s WHERE NOT EXISTS (SELECT 1 FROM file_observations_v2 o WHERE o.file_id=s.file_id)"), [(0,)])

    def test_migration_preserves_exact_rows_and_skips_valid_sources(self):
        self.source("a", [self.entry, replace(self.entry, input_tokens=20)])
        self.source("empty", [])
        self.source("unicode-项目", [replace(self.entry, message_id="another")])
        self.seed_v1()
        old = self.rows("SELECT path, position, dedup_key, entry FROM file_observations ORDER BY path, position")
        raw = self.rows("SELECT * FROM usage_events ORDER BY dedup_key")
        with mock.patch.object(usage_archive, "_decode", wraps=usage_archive._decode) as decode:
            self.collect(prepare=lambda conn: None)
        self.parser.assert_not_called()
        self.assertEqual(decode.call_count, 0)
        self.assertEqual(self.rows("SELECT s.path, o.position, o.dedup_key, o.entry FROM file_observations_v2 o JOIN source_files_v2 s USING(file_id) ORDER BY s.path, o.position"), old)
        self.assertEqual(self.rows("SELECT * FROM usage_events ORDER BY dedup_key"), raw)
        self.assert_cache_clean()

    def test_migration_does_not_promote_null_or_opaque_signatures(self):
        a = self.source("a", [self.entry])
        b = self.source("b", [])
        self.seed_v1()
        with closing(sqlite3.connect(self.archive.path)) as conn, conn:
            conn.execute("UPDATE parsed_files SET signature=NULL WHERE path=?", (str(a),))
            conn.execute("UPDATE parsed_files SET signature='opaque' WHERE path=?", (str(b),))
        self.collect()
        self.assertEqual(self.parser.call_args_list, [mock.call(a), mock.call(b)])
        self.assert_cache_clean()

    def test_component_order_ignores_id_and_string_order(self):
        later = self.source("a-", [replace(self.entry, model="later")])
        self.collect()
        first = self.source("a/z", [replace(self.entry, model="first"),
                                    replace(self.entry, model="same-file-second")])
        self.assertEqual(self.collect()[0].model, "first")
        ids = dict(self.rows("SELECT path, file_id FROM source_files_v2"))
        self.assertGreater(ids[str(first)], ids[str(later)])
        self.assertEqual(self.collect()[0].model, "first")

    def test_legacy_write_forces_reparse_even_with_v2_signature(self):
        path = self.source("a", [self.entry])
        self.collect()
        with closing(sqlite3.connect(self.archive.path)) as conn, conn:
            conn.execute("INSERT INTO file_observations VALUES (?, 0, ?, ?)",
                         (str(path), self.entry.dedup_key, json.dumps(_encode(self.entry))))
        self.parser.reset_mock()
        self.collect()
        self.parser.assert_called_once_with(path)
        self.assert_cache_clean()

    def test_legacy_invalidation_reparses_only_its_source(self):
        changed = self.source("a", [self.entry])
        self.source("b", [replace(self.entry, message_id="unrelated")])
        self.collect()
        with closing(sqlite3.connect(self.archive.path)) as conn, conn:
            conn.execute("INSERT INTO file_observations VALUES (?, 0, ?, ?)",
                         (str(changed), self.entry.dedup_key, json.dumps(_encode(self.entry))))
        self.parser.reset_mock()
        self.collect(prepare=lambda conn: None)
        self.parser.assert_called_once_with(changed)
        self.assert_cache_clean()

    def test_legacy_insert_update_delete_tracks_both_paths(self):
        path = self.source("a", [self.entry])
        self.collect()
        second = str(self.root / "second")
        with closing(sqlite3.connect(self.archive.path)) as conn, conn:
            conn.execute("INSERT INTO file_observations VALUES (?, 0, ?, ?)",
                         (str(path), self.entry.dedup_key, json.dumps(_encode(self.entry))))
            conn.execute("UPDATE file_observations SET path=?", (second,))
            conn.execute("DELETE FROM file_observations")
        self.assertEqual({row[0] for row in self.rows("SELECT path FROM legacy_observation_paths_v2")}, {str(path), second})
        self.collect()
        self.assert_cache_clean()

    def test_disappeared_legacy_only_path_clears_queue_and_keeps_raw(self):
        self.source("a", [self.entry])
        self.collect()
        disappeared = self.root / "introduced-while-downgraded"
        old_entry = replace(self.entry, message_id="legacy")
        with closing(sqlite3.connect(self.archive.path)) as conn, conn:
            conn.execute("INSERT INTO file_observations VALUES (?, 0, ?, ?)",
                         (str(disappeared), old_entry.dedup_key, json.dumps(_encode(old_entry))))
            conn.execute("INSERT INTO usage_events VALUES (?, ?)", (old_entry.dedup_key, json.dumps(_encode(old_entry))))
            conn.execute("DELETE FROM file_observations WHERE path=?", (str(disappeared),))
        self.assertEqual(self.rows("SELECT path FROM legacy_observation_paths_v2"), [(str(disappeared),)])
        self.assertEqual(set(self.collect()), {self.entry, old_entry})
        self.assert_cache_clean()

    def test_missing_parsed_file_still_removes_dictionary_observations(self):
        path = self.source("a", [self.entry])
        self.collect()
        with closing(sqlite3.connect(self.archive.path)) as conn, conn:
            conn.execute("DELETE FROM parsed_files")
        self.sources.pop(path)
        self.assertEqual(self.collect(), [self.entry])
        self.assertEqual(self.rows("SELECT count(*) FROM source_files_v2"), [(0,)])
        self.assert_cache_clean()

    def test_empty_and_erroring_legacy_sources_retire_stale_v2(self):
        path = self.source("a", [self.entry])
        self.collect()
        with closing(sqlite3.connect(self.archive.path)) as conn, conn:
            conn.execute("UPDATE parsed_files SET signature='observations-v1:1'")
        self.sources[path] = []
        self.collect()
        self.assertEqual(self.rows("SELECT count(*) FROM file_observations_v2"), [(0,)])
        self.source("a", [self.entry])
        self.collect()
        self.versions[path] += 1
        self.parser.side_effect = ValueError("synthetic failure")
        self.assertEqual(self.collect(), [self.entry])
        self.assertEqual(self.errors[0]["stage"], "archive_parse")
        self.assertEqual(self.rows("SELECT signature FROM parsed_files"), [(None,)])
        self.assert_cache_clean()

    def test_migration_and_prepare_failure_rollback_then_retry(self):
        self.source("a", [self.entry])
        self.seed_v1()
        before = self.archive.path.read_bytes()
        def fail(conn):
            self.assertEqual(conn.execute("SELECT count(*) FROM file_observations_v2").fetchone()[0], 1)
            raise RuntimeError("synthetic callback failure")
        with self.assertRaisesRegex(RuntimeError, "callback"):
            self.collect(prepare=fail)
        self.assertEqual(self.archive.path.read_bytes(), before)
        self.assertFalse(self.rows("SELECT 1 FROM sqlite_master WHERE name='source_files_v2'"))
        self.assertEqual(self.collect(), [self.entry])
        self.assert_cache_clean()

    def test_legacy_queue_consumption_rolls_back_with_callback(self):
        path = self.source("a", [self.entry])
        self.collect()
        with closing(sqlite3.connect(self.archive.path)) as conn, conn:
            conn.execute("INSERT INTO file_observations VALUES (?, 0, ?, ?)",
                         (str(path), self.entry.dedup_key, json.dumps(_encode(self.entry))))
        before = self.archive.path.read_bytes()
        with self.assertRaises(RuntimeError):
            self.collect(prepare=lambda conn: (_ for _ in ()).throw(RuntimeError()))
        self.assertEqual(self.archive.path.read_bytes(), before)
        self.assertEqual(self.collect(), [self.entry])
        self.assert_cache_clean()

    def test_warm_round_no_write_or_observation_scan_at_two_sizes(self):
        for count in (12, 120):
            with self.subTest(count=count):
                self.archive = UsageArchive(self.root / (str(count) + ".sqlite3"))
                self.sources.clear()
                self.source("a", [replace(self.entry, message_id=str(i)) for i in range(count)])
                self.collect(prepare=lambda conn: conn.execute("DELETE FROM archive_dirty"))
                before = self.archive.path.read_bytes()
                before_mtime = self.archive.path.stat().st_mtime_ns
                self.parser.reset_mock()
                statements = []
                real_connect = sqlite3.connect
                def connect(*args, **kwargs):
                    conn = real_connect(*args, **kwargs)
                    conn.set_trace_callback(statements.append)
                    return conn
                with mock.patch.object(usage_archive, "_decode", wraps=usage_archive._decode) as decode, mock.patch.object(sqlite3, "connect", side_effect=connect):
                    self.collect(prepare=lambda conn: None)
                scans = [sql for sql in statements if sql.lstrip().upper().startswith("SELECT")
                         and ("FROM file_observations" in sql or "FROM usage_events" in sql)]
                self.assertEqual(scans, [])
                self.parser.assert_not_called()
                self.assertEqual(decode.call_count, 0)
                self.assertEqual(self.archive.path.read_bytes(), before)
                self.assertEqual(self.archive.path.stat().st_mtime_ns, before_mtime)
                self.assert_cache_clean()


if __name__ == "__main__":
    unittest.main()

