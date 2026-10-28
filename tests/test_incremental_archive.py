"""Synthetic incremental archive tests; never opens provider stores."""
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

from agent_monitor.ingestion import usage_archive
from agent_monitor.ingestion.usage_archive import UsageArchive, _encode
from agent_monitor.contracts.usage import UsageEntry


class IncrementalArchiveTests(unittest.TestCase):
    def setUp(self):
        artifact_root = Path(os.environ.get("ARCHIVE_TEST_ROOT", Path.home() / "task-artifacts/agent-monitor/incremental-archive-tests"))
        artifact_root.mkdir(parents=True, exist_ok=True)
        self.root = Path(tempfile.mkdtemp(prefix="archive-case-", dir=artifact_root))
        self.archive = UsageArchive(self.root / "archive.sqlite3")
        self.values, self.versions, self.errors = {}, {}, []
        self.parser = mock.Mock(side_effect=lambda path: self.values[path])
        self.entry = UsageEntry(datetime(2026, 2, 10, tzinfo=timezone.utc), "session", "message", "request", "model", 10, 0, 0, 0, None, "project", "codex")

    def source(self, name, entries):
        path = self.root / name
        self.values[path] = entries
        self.versions[path] = self.versions.get(path, 0) + 1
        return path

    def collect(self, paths=None, **kwargs):
        self.errors = []
        return self.archive.collect(self.values if paths is None else paths, self.parser,
                                    lambda path: str(self.versions[path]), self.errors,
                                    timezone.utc, **kwargs)[0]

    def rows(self, sql):
        with closing(sqlite3.connect(self.archive.path)) as conn, conn:
            return conn.execute(sql).fetchall()

    @staticmethod
    def consume(conn):
        rows = conn.execute("SELECT dedup_key FROM archive_dirty ORDER BY dedup_key").fetchall()
        conn.execute("DELETE FROM archive_dirty")
        return rows

    def test_unchanged_prepare_decodes_no_events_at_two_history_sizes(self):
        for count in (12, 120):
            with self.subTest(count=count):
                self.archive = UsageArchive(self.root / (str(count) + ".sqlite3"))
                self.source("a", [replace(self.entry, message_id=str(i)) for i in range(count)])
                self.assertEqual(len(self.collect(prepare=self.consume)), count)
                self.parser.reset_mock()
                with mock.patch.object(usage_archive, "_decode", wraps=usage_archive._decode) as decode:
                    self.assertEqual(self.collect(prepare=self.consume), [])
                self.assertEqual(decode.call_count, 0)
                self.parser.assert_not_called()
                self.assertEqual(self.rows("SELECT DISTINCT entries FROM parsed_files"), [("[]",)])

    def test_changed_duplicate_only_decodes_affected_candidates(self):
        a = self.source("a", [self.entry])
        b = self.source("b", [replace(self.entry, model="later")])
        self.source("unrelated", [replace(self.entry, message_id=str(i)) for i in range(100)])
        self.collect(prepare=self.consume)
        self.source("b", [replace(self.entry, model="changed")])
        self.parser.reset_mock()
        with mock.patch.object(usage_archive, "_decode", wraps=usage_archive._decode) as decode:
            self.assertEqual(self.collect(prepare=self.consume), [])
        self.assertEqual(decode.call_count, 3)
        self.parser.assert_called_once_with(b)
        del self.values[a]
        self.collect(prepare=self.consume)
        winner = next(e for e in self.archive.read()[0] if e.dedup_key == self.entry.dedup_key)
        self.assertEqual(winner.model, "changed")

    def test_same_file_duplicates_and_path_component_order(self):
        self.source("a/z", [replace(self.entry, model="first"), replace(self.entry, model="second")])
        self.source("a-", [replace(self.entry, model="third")])
        self.assertEqual(self.collect()[0].model, "first")
        self.assertEqual(self.collect()[0].model, "first")

    def test_removed_and_replaced_source_retains_missing_keys(self):
        a = self.source("a", [self.entry])
        self.collect()
        self.source("a", [replace(self.entry, message_id="new", input_tokens=30)])
        self.assertEqual(len(self.collect()), 2)
        self.assertEqual(len(self.collect(paths=[])), 2)
        self.assertEqual(self.rows("SELECT count(*) FROM file_observations"), [(0,)])
        self.assertEqual(self.rows("SELECT count(*) FROM file_observations_v2"), [(0,)])
        self.assertEqual(self.rows("SELECT count(*) FROM source_files_v2"), [(0,)])
        self.assertEqual(len(self.collect(paths=[a])), 2)

    def test_claude_completion_and_recurring_conflict(self):
        incomplete = replace(self.entry, agent_id="claude-code", usage_quality="incomplete")
        a = self.source("a", [incomplete])
        self.source("b", [replace(incomplete, output_tokens=10, usage_quality="observed", project="copy")])
        result = self.collect()[0]
        self.assertEqual((result.project, result.output_tokens, result.usage_quality), ("project", 10, "observed"))
        self.source("a", [replace(incomplete, input_tokens=20)])
        self.assertEqual(self.collect()[0], result)
        first_errors = self.errors.copy()
        self.assertEqual(len(first_errors), 1)
        with mock.patch.object(usage_archive, "_decode", wraps=usage_archive._decode) as decode:
            self.collect(prepare=self.consume)
        self.assertEqual(self.errors, first_errors)
        self.assertEqual(decode.call_count, 0)
        self.collect(paths=[a])
        self.assertEqual(self.errors, first_errors)

    def test_first_round_diagnostics_differ_from_stable_next_round(self):
        claude = replace(self.entry, agent_id="claude-code", usage_quality="observed")
        self.source("a", [claude])
        self.source("b", [replace(claude, input_tokens=5, output_tokens=20)])
        self.source("c", [replace(claude, input_tokens=30, output_tokens=30)])
        self.assertEqual(self.collect()[0].input_tokens, 30)
        self.assertEqual(len(self.errors), 1)
        self.collect(prepare=self.consume)
        self.assertEqual(self.errors, [])

    def test_partial_parse_and_exception_retry(self):
        a = self.source("a", [self.entry])
        self.source("b", [replace(self.entry, model="fallback")])
        self.collect()
        self.versions[a] += 1
        def parse(path):
            if path == a:
                raise ValueError("synthetic failure")
            return self.values[path]
        self.parser.side_effect = parse
        self.assertEqual(self.collect()[0].model, "fallback")
        self.assertEqual(self.errors[0]["stage"], "archive_parse")
        self.parser.reset_mock()
        self.collect()
        self.parser.assert_called_once_with(a)
        def partial(path):
            if path == a:
                self.errors.append({"path": str(a), "stage": "parse", "error": "partial"})
            return self.values[path]
        self.parser.side_effect = partial
        self.assertEqual(self.collect()[0].model, "model")
        self.parser.reset_mock()
        self.collect()
        self.parser.assert_called_once_with(a)
        self.parser.side_effect = lambda path: self.values[path]
        self.collect()
        self.parser.reset_mock()
        self.collect()
        self.parser.assert_not_called()

    def test_prepare_failure_rolls_back_raw_cache_dirty_and_counts(self):
        self.source("a", [self.entry])
        self.collect(prepare=self.consume)
        original = self.archive.path.read_bytes()
        self.source("a", [replace(self.entry, input_tokens=50, project="other")])
        def fail(conn):
            self.assertEqual(conn.execute("SELECT count(*) FROM archive_dirty").fetchone()[0], 1)
            self.assertEqual(json.loads(conn.execute("SELECT project FROM usage_project_counts").fetchone()[0]), "other")
            conn.execute("DELETE FROM archive_dirty")
            raise RuntimeError("rollback")
        with self.assertRaisesRegex(RuntimeError, "rollback"):
            self.collect(prepare=fail)
        self.assertEqual(self.archive.path.read_bytes(), original)
        self.assertEqual(self.collect()[0].input_tokens, 50)

    def test_new_schema_rolls_back_with_prepare(self):
        self.source("a", [self.entry])
        with self.assertRaises(RuntimeError):
            self.collect(prepare=lambda conn: (_ for _ in ()).throw(RuntimeError()))
        self.assertEqual(self.rows("SELECT name FROM sqlite_master WHERE type='table'"), [])
        self.assertEqual(len(self.collect()), 1)

    def test_old_cache_migration_and_old_write_followed_by_source_deletion(self):
        path = self.source("a", [self.entry])
        with closing(sqlite3.connect(self.archive.path)) as conn, conn:
            conn.execute("CREATE TABLE archive_meta(key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            conn.execute("CREATE TABLE usage_events(dedup_key TEXT PRIMARY KEY, entry TEXT NOT NULL)")
            conn.execute("CREATE TABLE parsed_files(path TEXT PRIMARY KEY, signature TEXT, entries TEXT NOT NULL)")
            conn.execute("INSERT INTO usage_events VALUES (?, ?)", (self.entry.dedup_key, json.dumps(_encode(self.entry))))
            conn.execute("INSERT INTO parsed_files VALUES (?, ?, ?)", (str(path), "1", json.dumps([_encode(self.entry)])))
        self.assertEqual(self.collect(prepare=self.consume), [(self.entry.dedup_key,)])
        self.parser.assert_called_once_with(path)
        updated = replace(self.entry, input_tokens=80, project="downgrade")
        with closing(sqlite3.connect(self.archive.path)) as conn, conn:
            conn.execute("UPDATE usage_events SET entry=?", (json.dumps(_encode(updated)),))
            conn.execute("UPDATE parsed_files SET signature='old-cache', entries=?", (json.dumps([_encode(updated)]),))
        self.assertEqual(self.collect(paths=[], prepare=self.consume), [(updated.dedup_key,)])
        self.assertEqual(self.archive.read()[0], [updated])
        self.assertEqual([(json.loads(p), n) for p, n in self.rows("SELECT * FROM usage_project_counts")], [("downgrade", 1)])

    def test_external_write_replays_unchanged_active_candidates(self):
        self.source("a", [self.entry])
        self.collect(prepare=self.consume)
        with closing(sqlite3.connect(self.archive.path)) as conn, conn:
            conn.execute("UPDATE usage_events SET entry=?", (json.dumps(_encode(replace(self.entry, input_tokens=99))),))
        self.assertEqual(self.collect()[0], self.entry)

    def test_project_counts_for_null_literal_and_old_writes(self):
        self.source("a", [self.entry, replace(self.entry, message_id="null", project=None), replace(self.entry, message_id="literal", project="null")])
        self.collect(prepare=self.consume)
        self.assertEqual({json.loads(key): count for key, count in self.rows("SELECT * FROM usage_project_counts")}, {"project": 1, None: 1, "null": 1})
        with closing(sqlite3.connect(self.archive.path)) as conn, conn:
            conn.execute("UPDATE usage_events SET entry=? WHERE dedup_key=?", (json.dumps(_encode(replace(self.entry, project="null"))), self.entry.dedup_key))
            conn.execute("DELETE FROM usage_events WHERE dedup_key=?", (replace(self.entry, message_id="null").dedup_key,))
            extra = replace(self.entry, message_id="extra", project="null")
            conn.execute("INSERT INTO usage_events VALUES (?, ?)", (extra.dedup_key, json.dumps(_encode(extra))))
        self.assertEqual([(json.loads(p), n) for p, n in self.rows("SELECT * FROM usage_project_counts")], [("null", 3)])
        self.assertEqual(len(self.rows("SELECT * FROM archive_dirty")), 3)

    def test_session_transform_runs_before_prepare_without_usage_changes(self):
        metadata = {"agent_id": "codex", "session_id": "session", "parent_session_id": None,
                    "relation": "root", "title": "before", "created_at": None, "project": "project"}
        self.source("a", [self.entry])
        self.collect(session_metadata=[metadata], prepare=self.consume)
        def transform(rows):
            return [dict(row, title="after") for row in rows]
        def prepare(conn):
            return json.loads(conn.execute("SELECT metadata FROM usage_sessions").fetchone()[0])["title"]
        self.assertEqual(self.collect(paths=[], session_metadata_transform=transform, prepare=prepare), "after")
        self.assertEqual(self.archive.read()[0], [self.entry])
        self.assertEqual(self.archive.session_metadata[0]["title"], "after")

    def test_signature_changes_during_parse_retry_then_cache(self):
        path = self.source("a", [self.entry])
        def unstable(path):
            self.versions[path] += 1
            return self.values[path]
        self.parser.side_effect = unstable
        self.collect()
        self.assertEqual(self.rows("SELECT signature FROM parsed_files"), [(None,)])
        self.parser.side_effect = lambda path: self.values[path]
        self.collect()
        self.parser.reset_mock()
        self.collect()
        self.parser.assert_not_called()

    def test_unchanged_full_read_does_not_rewrite_database(self):
        self.source("a", [self.entry])
        initial = self.collect()
        before = self.archive.path.read_bytes()
        self.assertEqual(self.collect(), initial)
        self.assertEqual(self.archive.path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()

