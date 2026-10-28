import io
import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock
from contextlib import closing

from agent_monitor.accounting import rollup
from agent_monitor.snapshots import exporter, generation, statistics_snapshot
import test_rollup
import test_exporter


class ExportWorkReuseTests(unittest.TestCase):
    def test_loader_fixtures_never_read_real_account_or_session_paths(self):
        from parsers import codex_profiles
        roots = (Path.home() / ".local/share/codex-accounts", Path.home() / ".codex", Path.home() / ".claude")
        def guard(original):
            def checked(path, *args, **kwargs):
                if isinstance(path, (str, bytes, os.PathLike)):
                    candidate = Path(os.fsdecode(path)).absolute()
                    if any(candidate.is_relative_to(root) for root in roots):
                        raise AssertionError("real account/session path accessed")
                return original(path, *args, **kwargs)
            return checked
        with mock.patch.object(Path, "iterdir", guard(Path.iterdir)), \
                mock.patch.object(io, "open", guard(io.open)), \
                mock.patch.object(os, "scandir", guard(os.scandir)):
            # The old unmanaged discovery is rejected before touching disk.
            with self.assertRaisesRegex(AssertionError, "real account/session"):
                codex_profiles.discover()
            names = (
                "test_load_all_entries_path_to_remote_preserves_exact_sum",
                "test_load_all_entries_remote_to_missing_path_blocks_only_that_source",
                "test_load_all_entries_remote_change_blocks_path_and_collects_other_project",
                "test_persisted_blocker_survives_restart_and_source_log_disappearance",
                "test_load_all_entries_new_no_remote_project_does_not_abort_other_projects",
            )
            for name in names:
                with self.subTest(loader_test=name):
                    getattr(test_rollup.RollupTests(name), name)()

    def test_authority_changes_orphans_and_legacy_guards(self):
        for table, columns in (("daily_rollup", ("date", "agent_id", "project", "model")),
                               ("daily_usage_rollup", ("date", "agent_id", "model"))):
            for projects in (None, set(), {"p"}):
                if table == "daily_usage_rollup" and projects is not None:
                    continue
                for minimum in (None, "2026-08-05"):
                    with self.subTest(table=table, projects=projects, minimum=minimum), closing(sqlite3.connect(":memory:")) as conn:
                        conn.executescript(rollup.SCHEMA)
                        def key(day, model):
                            return (day, "codex", "p", model) if len(columns) == 4 else (day, "codex", model)
                        def bucket(value):
                            return {field: value for field in rollup.BUCKET_FIELDS}
                        old = {key("2026-08-03", "legacy"): bucket(10),
                               key("2026-08-04", "current"): bucket(10),
                               key("2026-08-04", "orphan"): bucket(10),
                               key("2026-08-05", "newer"): bucket(10)}
                        rollup._write_bucket_table(conn, table, columns, old, set())
                        current = {k: bucket(5) for k in old if k[-1] != "orphan"}
                        current[key("2026-08-03", "renamed")] = bucket(20)
                        current[key("2026-08-05", "added")] = bucket(1)
                        rollup._write_bucket_table(conn, table, columns, current,
                            {"2026-08-03", "2026-08-04", "2026-08-05"}, minimum_date=minimum,
                            history_authoritative_from="2026-08-04", history_authoritative_projects=projects)
                        rows = {tuple(row[:-1]): row[-1] for row in conn.execute(
                            "SELECT %s, input_tokens FROM %s" % (", ".join(columns), table))}
                        self.assertEqual(rows[key("2026-08-03", "legacy")], 10)
                        self.assertNotIn(key("2026-08-03", "renamed"), rows)
                        authorized = projects is None or "p" in projects
                        self.assertEqual(rows[key("2026-08-05", "newer")], 5 if authorized else 10)
                        if authorized:
                            self.assertEqual(rows[key("2026-08-05", "added")], 1)
                        else:
                            self.assertNotIn(key("2026-08-05", "added"), rows)
                        if authorized and minimum is None:
                            self.assertNotIn(key("2026-08-04", "orphan"), rows)
                            self.assertEqual(rows[key("2026-08-04", "current")], 5)
                        else:
                            self.assertEqual(rows[key("2026-08-04", "orphan")], 10)
                            self.assertEqual(rows[key("2026-08-04", "current")], 10)
                        statements = []
                        conn.set_trace_callback(statements.append)
                        replay = rollup._write_bucket_table(conn, table, columns, current,
                            {"2026-08-03", "2026-08-04", "2026-08-05"}, minimum_date=minimum,
                            history_authoritative_from="2026-08-04", history_authoritative_projects=projects)
                        self.assertEqual(replay["written"], 0)
                        self.assertFalse([s for s in statements if s.startswith(("INSERT", "UPDATE", "DELETE"))])

    def test_unchanged_rollup_does_not_write_bucket_rows(self):
        now = datetime(2026, 8, 4, 12, tzinfo=timezone.utc)
        entries = [test_rollup.RollupTests.entry(now, "s", "p", "m", "codex", 1, 10, 2, 0, 0, 1)]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "rollup.db"
            rollup.run(db_path=path, entries_loader=lambda: entries, now=lambda: now)
            statements = []
            connect = rollup._connect

            def traced(*args, **kwargs):
                conn = connect(*args, **kwargs)
                conn.set_trace_callback(statements.append)
                return conn

            with mock.patch.object(rollup, "_connect", side_effect=traced):
                result = rollup.run(db_path=path, entries_loader=lambda: entries, now=lambda: now)
            dml = [sql for sql in statements if sql.startswith(("INSERT INTO daily_", "UPDATE daily_", "DELETE FROM daily_"))]
            self.assertEqual(dml, [])
            self.assertEqual(result["buckets_written"], 0)
            self.assertEqual(test_rollup.RollupTests.fetch_daily_rows(path)[0]["input_tokens"], 10)

    def test_private_export_reads_statistics_stats_and_digest_once(self):
        now = datetime(2026, 8, 4, 12, tzinfo=timezone.utc)
        for count in (0, 20, 200):
            with self.subTest(count=count), tempfile.TemporaryDirectory() as tmp:
                entries = [test_rollup.RollupTests.entry(now, str(i), "p", "m", "codex", 1, 10, 2, 0, 0, 1)
                           for i in range(count)]
                source = test_exporter.ExporterTests.make_db(Path(tmp) / "rollup.db", 10)
                with mock.patch.object(exporter, "exporter_version", return_value="a" * 40), \
                        mock.patch.object(statistics_snapshot, "read_snapshot", wraps=statistics_snapshot.read_snapshot) as read, \
                        mock.patch.object(generation, "snapshot_stats", wraps=generation.snapshot_stats) as stats, \
                        mock.patch.object(exporter, "_file_digest", wraps=exporter._file_digest) as digest, \
                        mock.patch.object(generation, "_file_digest", wraps=generation._file_digest) as generation_digest:
                    manifest = exporter.export_bundle(source, Path(tmp) / "bundle", entries_loader=lambda: entries,
                        source_host_identity="host-v1:" + "1" * 64, rate_limits={}, rollup_now=lambda: now)
                self.assertEqual(read.call_count, 1)
                self.assertEqual(stats.call_count, 1)
                self.assertEqual(digest.call_count + generation_digest.call_count, 1)
                snapshot = Path(tmp) / "bundle/snapshot.db"
                exporter.validate_export_manifest(manifest, snapshot)
                self.assertEqual(statistics_snapshot.read_snapshot(snapshot)["usage_entries"], entries)

    def test_rollup_failure_rolls_back_deletion_and_changed_sibling(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = test_exporter.ExporterTests.make_db(Path(tmp) / "rollup.db", 10)
            with closing(sqlite3.connect(source)) as conn:
                original = conn.execute("SELECT * FROM daily_rollup").fetchall()
                conn.execute("CREATE TRIGGER reject_insert BEFORE INSERT ON daily_rollup "
                             "BEGIN SELECT RAISE(ABORT, 'injected write failure'); END")
                conn.commit()
                with self.assertRaisesRegex(sqlite3.IntegrityError, "injected"):
                    with conn:
                        conn.execute("BEGIN IMMEDIATE")
                        rollup._write_bucket_table(conn, "daily_rollup", ("date", "agent_id", "project", "model"),
                            {("2026-08-04", "codex", "repo", "replacement"): {f: 1 for f in rollup.BUCKET_FIELDS}},
                            {"2026-08-04"}, history_authoritative_from="2026-08-04")
                self.assertEqual(conn.execute("SELECT * FROM daily_rollup").fetchall(), original)

    def test_private_reuse_rejects_changed_file_or_wal_and_cleans_staging(self):
        for phase in ("inspection", "publication"):
            for change in ("content", "replace", "wal"):
                with self.subTest(phase=phase, change=change), tempfile.TemporaryDirectory() as tmp:
                    source = test_exporter.ExporterTests.make_db(Path(tmp) / "rollup.db", 10)
                    output = Path(tmp) / "bundle"
                    def mutate(path):
                        path = Path(path)
                        if change == "content":
                            with closing(sqlite3.connect(path)) as conn, conn:
                                conn.execute("UPDATE daily_rollup SET input_tokens = 99")
                        elif change == "replace":
                            replacement = path.with_suffix(".replacement")
                            replacement.write_bytes(path.read_bytes())
                            os.replace(replacement, path)
                        else:
                            Path(str(path) + "-wal").write_bytes(b"uncommitted")
                    if phase == "inspection":
                        original = exporter._validated_snapshot_stats
                        def inspect(path):
                            stats = original(path)
                            mutate(path)
                            return stats
                        patch = mock.patch.object(exporter, "_validated_snapshot_stats", side_effect=inspect)
                    else:
                        original = exporter.file_io.fsync_file
                        def fsync(path):
                            original(path)
                            if Path(path).name == "snapshot.db" and Path(path).with_name("export.json").exists():
                                mutate(path)
                        patch = mock.patch.object(exporter.file_io, "fsync_file", side_effect=fsync)
                    with patch, mock.patch.object(exporter, "exporter_version", return_value="a" * 40):
                        with self.assertRaisesRegex(exporter.ExportError, "changed|nonempty WAL"):
                            exporter.export_bundle(source, output, refresh=False,
                                source_host_identity="host-v1:" + "1" * 64, rate_limits={})
                    self.assertFalse(output.exists())
                    self.assertEqual(list(Path(tmp).glob(".bundle.staging-*")), [])
                    with closing(sqlite3.connect(source)) as conn:
                        self.assertEqual(conn.execute("SELECT input_tokens FROM daily_rollup").fetchone()[0], 10)

    def test_public_validation_reads_each_time_and_rejects_redigested_bad_content(self):
        now = datetime(2026, 8, 4, 12, tzinfo=timezone.utc)
        entries = [test_rollup.RollupTests.entry(now, "s", "p", "m", "codex", 1, 10, 2, 0, 0, 1)]
        with tempfile.TemporaryDirectory() as tmp:
            source = test_exporter.ExporterTests.make_db(Path(tmp) / "rollup.db", 10)
            with mock.patch.object(exporter, "exporter_version", return_value="a" * 40):
                manifest = exporter.export_bundle(source, Path(tmp) / "bundle", entries_loader=lambda: entries,
                    source_host_identity="host-v1:" + "1" * 64, rate_limits={}, rollup_now=lambda: now)
            snapshot = Path(tmp) / "bundle/snapshot.db"
            with mock.patch.object(statistics_snapshot, "read_snapshot", wraps=statistics_snapshot.read_snapshot) as read:
                for _ in range(2):
                    exporter.validate_export_manifest(manifest, snapshot)
                self.assertEqual(read.call_count, 2)
            with closing(sqlite3.connect(snapshot)) as conn, conn:
                conn.execute("UPDATE statistics_usage SET payload = '{}' WHERE position = 0")
            with self.assertRaisesRegex(exporter.ExportError, "transfer_digest"):
                exporter.validate_export_manifest(manifest, snapshot)
            manifest["transfer_digest"] = exporter._file_digest(snapshot)
            manifest["manifest_digest"] = exporter.manifest_digest(manifest)
            with self.assertRaisesRegex(exporter.ExportError, "statistics extension"):
                exporter.validate_export_manifest(manifest, snapshot)
