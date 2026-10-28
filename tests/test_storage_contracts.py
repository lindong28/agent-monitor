"""SQLite read lifetime and generation publication contracts."""
import contextlib
import sqlite3
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from agent_monitor.snapshots import generation
from agent_monitor.accounting import rollup
from agent_monitor import storage
import test_generation as generation_fixtures


class StorageContractTests(unittest.TestCase):
    def test_private_copy_excludes_cooperating_writer_and_stays_fixed_after_release(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "rollup.db"
            rollup.run(db_path=path, entries_loader=list)
            attempted = threading.Event()
            completed = threading.Event()
            errors = []
            copyfile = storage.shutil.copyfile

            def write():
                attempted.set()
                try:
                    with rollup.rollup_lock(path, timeout=2):
                        with contextlib.closing(sqlite3.connect(path)) as conn, conn:
                            conn.execute("INSERT INTO rollup_meta VALUES ('later', '1')")
                    completed.set()
                except BaseException as exc:
                    errors.append(exc)

            writer = threading.Thread(target=write)

            def copy_while_writer_waits(source, target):
                if Path(source) == path:
                    writer.start()
                    self.assertTrue(attempted.wait(2))
                    self.assertFalse(completed.wait(0.05))
                return copyfile(source, target)

            try:
                with mock.patch("agent_monitor.storage.shutil.copyfile", side_effect=copy_while_writer_waits):
                    with storage._private_read_copy(path) as private:
                        self.assertTrue(completed.wait(2))
                        with storage.read_connection(private, immutable=True) as conn:
                            self.assertIsNone(conn.execute("SELECT value FROM rollup_meta WHERE key='later'").fetchone())
                with storage.read_connection(path) as conn:
                    self.assertEqual(conn.execute("SELECT value FROM rollup_meta WHERE key='later'").fetchone()[0], "1")
            finally:
                if writer.ident is not None:
                    writer.join(3)
            self.assertFalse(writer.is_alive())
            self.assertEqual(errors, [])

    def test_writer_connection_close_stays_inside_rollup_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "rollup.db"
            original_connect = sqlite3.connect
            closed_under_lock = []

            class CheckedConnection(sqlite3.Connection):
                def close(self):
                    rollup._assert_lock_held(path)
                    closed_under_lock.append(True)
                    return super().close()

            def connect(target, *args, **kwargs):
                if Path(str(target)) == path:
                    kwargs["factory"] = CheckedConnection
                return original_connect(target, *args, **kwargs)

            with mock.patch.object(sqlite3, "connect", side_effect=connect):
                rollup.run(db_path=path, entries_loader=list)
            self.assertEqual(closed_under_lock, [True])
            with self.assertRaises(rollup.RollupLockNotHeld):
                rollup._connect(path)

    def test_checker_reads_committed_wal_without_changing_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "rollup.db"
            rollup.run(db_path=path, entries_loader=list)
            with contextlib.closing(sqlite3.connect(path)) as writer:
                with rollup.rollup_lock(path):
                    writer.execute("INSERT INTO rollup_meta VALUES ('test', 'committed')")
                    writer.commit()
                    self.assertGreater(Path(str(path) + "-wal").stat().st_size, 0)
                    for checkpoint in (False, True):
                        with self.subTest(full_checkpoint=checkpoint):
                            if checkpoint:
                                status = writer.execute("PRAGMA wal_checkpoint(FULL)").fetchone()
                                self.assertEqual(status[0], 0)
                                self.assertEqual(status[1], status[2])
                                self.assertGreater(Path(str(path) + "-wal").stat().st_size, 0)
                            before = {p.name: p.read_bytes() for p in path.parent.iterdir() if p.is_file()}
                            result = rollup._read_check_rows(path)
                            after = {p.name: p.read_bytes() for p in path.parent.iterdir() if p.is_file()}
                            self.assertEqual(before, after)
                            self.assertEqual(result[0], "ready")

    def test_fallback_reads_private_copy_including_committed_wal(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "rollup.db"
            rollup.run(db_path=path, entries_loader=list)
            connect = storage._connect_read_only

            def unavailable_source(uri):
                if uri == path.resolve().as_uri() + "?mode=ro":
                    raise sqlite3.OperationalError("source read unavailable")
                return connect(uri)

            with contextlib.closing(sqlite3.connect(path)) as writer:
                with rollup.rollup_lock(path):
                    writer.execute("INSERT INTO rollup_meta VALUES ('test', 'committed')")
                    writer.commit()
                with mock.patch("agent_monitor.storage._connect_read_only", side_effect=unavailable_source):
                    with storage.read_connection(path) as conn:
                        self.assertEqual(conn.execute("SELECT value FROM rollup_meta WHERE key='test'").fetchone()[0], "committed")

    def test_published_time_excludes_preparation(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = generation_fixtures.GenerationTests()
            path = fixture.make_db(Path(tmp) / "snapshot.db", 10)
            meta = fixture.make_meta(path, "host-a")
            clock = [datetime(2026, 10, 14, tzinfo=timezone.utc)]
            copyfile = generation.shutil.copyfile

            def delayed_copy(*args, **kwargs):
                clock[0] += timedelta(seconds=60)
                return copyfile(*args, **kwargs)

            with mock.patch("agent_monitor.snapshots.generation.shutil.copyfile", side_effect=delayed_copy):
                result = generation.publish_generation("node-b", path, meta, root=Path(tmp) / "generations", now=lambda: clock[0])
            self.assertEqual(datetime.fromisoformat(result.meta["published_at"].replace("Z", "+00:00")), clock[0])

    def test_automatic_freshness_and_explicit_restore_are_separate(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = generation_fixtures.GenerationTests()
            root = Path(tmp) / "generations"
            old = fixture.make_db(Path(tmp) / "old.db", 10)
            new = fixture.make_db(Path(tmp) / "new.db", 20)
            old_meta = fixture.make_meta(old, "host-a", "2026-08-04T12:00:00Z")
            new_meta = fixture.make_meta(new, "host-a", "2026-08-04T12:01:00Z")
            generation.publish_generation("node-b", new, new_meta, root=root)
            with self.assertRaisesRegex(generation.GenerationError, "older"):
                generation.publish_generation("node-b", old, old_meta, root=root, require_fresh=True)
            current = generation.read_current_generation("node-b", root=root)
            try:
                self.assertEqual(current.meta["generation_id"], new_meta["generation_id"])
            finally:
                current.close()
            restored = generation.publish_generation("node-b", old, old_meta, root=root)
            self.assertEqual(restored.meta["generation_id"], old_meta["generation_id"])
