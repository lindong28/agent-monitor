"""Isolated archive maintenance: real SQLite, public CLI and failure controls."""
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import unittest

from tests.artifacts import ArtifactTestCase
from unittest import mock

from agent_monitor.cli import archive_maintenance as maintenance
from agent_monitor.contracts.usage import UsageEntry
from agent_monitor.ingestion.usage_archive import UsageArchive
from agent_monitor.storage import rollup_lock

ROOT = Path(__file__).resolve().parents[1]


class ArchiveMaintenanceTests(ArtifactTestCase):
    def setUp(self):
        evidence = Path(os.environ.get("AGENT_MONITOR_TEST_ARTIFACTS", Path.home() / "task-artifacts" / "agent-monitor-tests"))
        self.root = self.artifact_directory(root=evidence, prefix="archive-maintenance-")
        self.archive = self.root / "usage_archive.sqlite3"
        self.rollup = self.root / "rollup.db"
        self.backup = self.root / "recovery.sqlite3"
        self.entry = UsageEntry(timestamp=datetime(2026, 1, 2, tzinfo=timezone.utc),
            session_id="session", message_id="message", request_id="request", model="fixture", input_tokens=123,
            output_tokens=7, cache_creation_tokens=2, cache_read_tokens=3, cost_usd=0.25, project="fixture",
            agent_id="codex")
        source = self.root / "fixture.jsonl"
        source.write_text("synthetic")
        UsageArchive(self.archive).collect([source], lambda path: [self.entry], lambda path: "fixture-v1",
            [], timezone.utc, now=datetime(2026, 1, 1, tzinfo=timezone.utc))
        with closing(sqlite3.connect(self.rollup)) as conn:
            conn.execute("CREATE TABLE fixture (value INTEGER)")
        with rollup_lock(self.rollup):
            pass
        # Deliberately create reusable pages without removing retained facts.
        with closing(sqlite3.connect(self.archive)) as conn, conn:
            conn.execute("CREATE TABLE spare (value BLOB)")
            conn.executemany("INSERT INTO spare VALUES (zeroblob(4096))", [()] * 128)
            conn.execute("DROP TABLE spare")
        self.original = UsageArchive(self.archive).read()
        self.env = dict(os.environ, AGENT_MONITOR_PYTHON=sys.executable,
                        PYTHONDONTWRITEBYTECODE="1", PYTHONPATH=str(ROOT))

    def cli(self, *args, json_output=True):
        command = [str(ROOT / "agent-monitor"), "archive", *args, "--archive", str(self.archive),
                   "--rollup-db", str(self.rollup), "--budget-seconds", "5"]
        if json_output:
            command.append("--json")
        result = subprocess.run(command, cwd=self.root, env=self.env, capture_output=True, text=True, timeout=10)
        return result, json.loads(result.stdout) if json_output else result.stdout + result.stderr

    def compact(self, **kwargs):
        with mock.patch.dict(os.environ, SQLITE_TMPDIR=str(self.root)):
            return maintenance.maintain(self.archive, self.rollup, compact=True, backup=self.backup, **kwargs)

    def assert_history(self, path=None):
        self.assertEqual(UsageArchive(path or self.archive).read(), self.original)
        with closing(sqlite3.connect(path or self.archive)) as conn:
            self.assertEqual(conn.execute("PRAGMA quick_check").fetchall(), [("ok",)])

    def test_default_stats_read_only(self):
        before = hashlib.sha256(self.archive.read_bytes()).hexdigest()
        proc, result = self.cli()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(result["action"], "stats")
        self.assertGreater(result["before"]["free_pages"], 0)
        self.assertEqual(hashlib.sha256(self.archive.read_bytes()).hexdigest(), before)
        self.assertFalse(self.backup.exists())
        proc, text = self.cli(json_output=False)
        self.assertEqual(proc.returncode, 0)
        self.assertIn("read-only", text)
        self.assertIn("integrity and consumer results not checked", text)

    def test_compact_real_backup_and_consumer(self):
        identity = self.archive.stat().st_ino
        proc, result = self.cli("compact", "--backup", str(self.backup))
        self.assertEqual(proc.returncode, 0, result)
        self.assertEqual(result["backup_state"], "verified")
        self.assertGreater(result["archive_file_reduction_bytes"], 0)
        self.assertEqual(self.archive.stat().st_ino, identity)
        self.assert_history(); self.assert_history(self.backup)
        self.assertEqual(self.backup.stat().st_mode & 0o777, 0o600)
        self.assertGreater(result["backup_bytes"], result["after"]["file_bytes"])

    def test_missing_archive_is_not_created(self):
        absent = self.root / "missing" / "usage_archive.sqlite3"
        result = maintenance.maintain(absent, absent.with_name("rollup.db"))
        self.assertEqual(result["status"], "failed")
        self.assertFalse(absent.exists())

    def test_backup_required_existing_and_dangling_rejected(self):
        proc, result = self.cli("compact")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("requires", result["error"]["message"])
        for dangling in (False, True):
            with self.subTest(dangling=dangling):
                target = self.root / ("dangling" if dangling else "existing")
                if dangling:
                    target.symlink_to(self.root / "absent")
                else:
                    target.write_text("do not overwrite")
                with mock.patch.dict(os.environ, SQLITE_TMPDIR=str(self.root)):
                    result = maintenance.maintain(self.archive, self.rollup, compact=True, backup=target)
                self.assertEqual(result["status"], "failed")
                self.assertEqual(result["backup_state"], "not_created")
                self.assertTrue(target.is_symlink() if dangling else target.read_text() == "do not overwrite")
        self.assert_history()

    def test_wrong_lock_directory_and_missing_lock_rejected(self):
        result = maintenance.maintain(self.archive, self.root / "foreign" / "rollup.db", compact=True, backup=self.backup)
        self.assertEqual(result["status"], "failed")
        Path(str(self.rollup) + ".lock").unlink()
        result = self.compact()
        self.assertEqual(result["stage"], "lock")
        self.assertEqual(result["status"], "failed")
        self.assertFalse(self.backup.exists())
        self.assertFalse(Path(str(self.rollup) + ".lock").exists())

    def test_backup_cannot_use_live_sidecar_or_adopt_stale_sidecar(self):
        with mock.patch.dict(os.environ, SQLITE_TMPDIR=str(self.root)):
            result = maintenance.maintain(self.archive, self.rollup, compact=True,
                backup=Path(str(self.archive) + '-journal'))
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['backup_state'], 'not_created')
        sidecar = Path(str(self.backup) + '-wal')
        sidecar.write_text('unowned sidecar')
        result = self.compact()
        self.assertEqual(result['status'], 'failed')
        self.assertFalse(self.backup.exists())
        self.assertEqual(sidecar.read_text(), 'unowned sidecar')
        self.assert_history()

    def test_lock_contention_has_bounded_failure(self):
        with rollup_lock(self.rollup):
            proc = subprocess.run([str(ROOT / "agent-monitor"), "archive", "compact", "--archive", str(self.archive),
                "--backup", str(self.backup), "--budget-seconds", "0.05", "--json"], env=self.env,
                cwd=self.root, capture_output=True, text=True, timeout=5)
        result = json.loads(proc.stdout)
        self.assertNotEqual(proc.returncode, 0)
        self.assertEqual(result["stage"], "lock")
        self.assertFalse(self.backup.exists()); self.assert_history()

    def test_backup_io_failure_retains_unverified_file(self):
        real_connect = maintenance._connect
        class FailingBackup(sqlite3.Connection):
            def backup(self, target, **kwargs):
                raise sqlite3.OperationalError("injected backup write failure")
        def connect(path, mode, budget):
            if path == self.archive:
                return sqlite3.connect(path, factory=FailingBackup)
            return real_connect(path, mode, budget)
        with mock.patch.object(maintenance, "_connect", side_effect=connect):
            result = self.compact()
        self.assertEqual(result["stage"], "backup")
        self.assertEqual(result["backup_state"], "created_unverified")
        self.assertTrue(Path(result['backup_work_path']).exists())
        self.assertFalse(self.backup.exists()); self.assert_history()

    def test_final_symlink_before_backup_connect_never_overwrites_incumbent(self):
        incumbent = self.root / 'incumbent.sqlite3'
        with closing(sqlite3.connect(incumbent)) as conn, conn:
            conn.execute('CREATE TABLE incumbent_fact (value TEXT)')
            conn.execute("INSERT INTO incumbent_fact VALUES ('must survive')")
        original = incumbent.read_bytes()
        connect = maintenance._connect
        def replace_final_then_connect(path, mode, budget):
            if path != self.archive:
                self.backup.symlink_to(incumbent)
            return connect(path, mode, budget)
        with mock.patch.object(maintenance, '_connect', side_effect=replace_final_then_connect):
            result = self.compact()
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['stage'], 'backup_publish')
        self.assertEqual(result['backup_state'], 'working_copy_verified_not_published')
        self.assertEqual(incumbent.read_bytes(), original)
        self.assert_history(Path(result['backup_work_path']))
        self.assert_history()

    def test_final_replaced_after_backup_check_is_not_verified(self):
        check = maintenance._quick_check
        def replace_after_check(conn, budget):
            check(conn, budget)
            self.backup.write_text('not a database')
        with mock.patch.object(maintenance, '_quick_check', side_effect=replace_after_check):
            result = self.compact()
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['stage'], 'backup_publish')
        self.assertNotEqual(result['backup_state'], 'verified')
        self.assertEqual(self.backup.read_text(), 'not a database')
        self.assert_history(Path(result['backup_work_path']))
        self.assert_history()

    def test_published_backup_replaced_before_return_is_not_verified(self):
        check = maintenance._quick_check
        calls = []
        def replace_after_source_check(conn, budget):
            check(conn, budget)
            calls.append(True)
            if len(calls) == 2:
                self.backup.unlink()
                self.backup.write_text('not a database')
        with mock.patch.object(maintenance, '_quick_check', side_effect=replace_after_source_check):
            result = self.compact()
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['backup_state'], 'published_identity_lost')
        self.assert_history(Path(result['backup_work_path']))
        self.assert_history()

    def test_backup_verification_failure_prevents_vacuum(self):
        size = self.archive.stat().st_size
        with mock.patch.object(maintenance, "_quick_check", side_effect=sqlite3.DatabaseError("injected check failure")):
            result = self.compact()
        self.assertEqual(result["stage"], "backup_check")
        self.assertEqual(result["backup_state"], "created_unverified")
        self.assertEqual(self.archive.stat().st_size, size); self.assert_history()

    def test_late_writer_is_not_overwritten_by_backup(self):
        check = maintenance._quick_check
        calls = []
        def check_then_commit(conn, budget):
            check(conn, budget)
            if not calls:
                with closing(sqlite3.connect(self.archive)) as writer, writer:
                    row = writer.execute("SELECT entry FROM usage_events LIMIT 1").fetchone()[0]
                    value = json.loads(row)
                    value['message_id'] = 'late-message'
                    writer.execute("INSERT INTO usage_events VALUES (?, ?)", ('late-key', json.dumps(value)))
                calls.append('late writer committed after backup')
        with mock.patch.object(maintenance, "_quick_check", side_effect=check_then_commit):
            result = self.compact()
        self.assertEqual(result['status'], 'ok', result)
        self.assertEqual(calls, ['late writer committed after backup'])
        self.assertEqual(len(UsageArchive(self.archive).read()[0]), 2)
        self.assertEqual(len(UsageArchive(self.backup).read()[0]), 1)

    def test_vacuum_actual_interrupt_preserves_facts(self):
        real_connect = maintenance._connect
        observed = []
        class InterruptVacuum(sqlite3.Connection):
            def execute(self, sql, *args):
                if sql == "VACUUM":
                    observed.append("vacuum entered")
                    self.set_progress_handler(lambda: 1, 1)
                return super().execute(sql, *args)
        def connect(path, mode, budget):
            if path == self.archive:
                return sqlite3.connect(path, factory=InterruptVacuum)
            return real_connect(path, mode, budget)
        with mock.patch.object(maintenance, "_connect", side_effect=connect):
            result = self.compact()
        self.assertEqual(observed, ["vacuum entered"])
        self.assertEqual(result["stage"], "vacuum")
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["backup_state"], "verified")
        self.assertIn("interrupted", result["error"]["message"])
        self.assert_history(); self.assert_history(self.backup)

    def test_budget_backup_callback_interrupts(self):
        with mock.patch.object(maintenance.Budget, "backup_progress", side_effect=maintenance.BudgetExpired("injected budget expiry")):
            result = self.compact()
        self.assertEqual(result["stage"], "backup")
        self.assertEqual(result["backup_state"], "created_unverified")
        self.assert_history()

    def test_wal_logical_budget_and_physical_compaction(self):
        with closing(sqlite3.connect(self.archive)) as writer:
            writer.execute("PRAGMA journal_mode=WAL")
            writer.execute("PRAGMA wal_autocheckpoint=0")
            writer.execute("CREATE TABLE wal_fixture (value BLOB)")
            writer.executemany("INSERT INTO wal_fixture VALUES (zeroblob(4096))", [()] * 256)
            writer.commit()
            budget = maintenance.Budget(5)
            before = maintenance._stats(writer, self.archive)
            self.assertGreater(before["logical_bytes"], before["file_bytes"])
            self.assertGreater(before["wal_bytes"], 0)
            writer.execute("DROP TABLE wal_fixture"); writer.commit()
            result = self.compact()
            self.assertEqual(result["status"], "ok", result)
            self.assertEqual(result["space"]["source_required_bytes"], 3 * result["before"]["logical_bytes"])
            self.assertEqual(result["after"]["wal_bytes"], 0)
            self.assert_history(); self.assert_history(self.backup)

    def test_insufficient_space_same_and_different_volume(self):
        original_stat = Path.stat
        for same in (True, False):
            with self.subTest(same=same):
                destination = self.root / "backup-volume"
                destination.mkdir(exist_ok=True)
                backup = destination / "new.sqlite3"
                def stat(path, **kwargs):
                    value = original_stat(path, **kwargs)
                    if path == destination and not same:
                        return mock.Mock(st_dev=value.st_dev + 1)
                    return value
                with mock.patch.object(Path, "stat", stat), mock.patch.object(maintenance.shutil, "disk_usage", return_value=mock.Mock(free=10000)):
                    reading = maintenance._space(self.archive, backup, 1000)
                    self.assertEqual(reading["source_required_bytes"], 3000 if same else 2000)
                    self.assertEqual(reading["backup_required_bytes"], 3000 if same else 1000)
                with mock.patch.object(maintenance.shutil, "disk_usage", return_value=mock.Mock(free=1)):
                    result = self.compact()
                self.assertEqual(result["status"], "failed")
                self.assertEqual(result["backup_state"], "not_created")
                self.assertFalse(self.backup.exists())

    def test_readonly_or_wrong_temp_directory_rejected(self):
        with mock.patch.dict(os.environ, SQLITE_TMPDIR="/wrong"):
            result = maintenance.maintain(self.archive, self.rollup, compact=True, backup=self.backup)
        self.assertEqual(result["status"], "failed")
        with mock.patch.object(maintenance.os, "access", return_value=False):
            result = self.compact()
        self.assertEqual(result["error"]["type"], "PermissionError")
        self.assertFalse(self.backup.exists())

    def test_human_failure_exit_and_action(self):
        proc, text = self.cli("compact", json_output=False)
        self.assertNotEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout, "")
        self.assertIn("failed during preflight", text)
        self.assertIn("No backup was restored over live data", text)


if __name__ == "__main__":
    unittest.main()
