"""Publication must not hold reader admission hostage to history validation."""

from concurrent.futures import ThreadPoolExecutor, TimeoutError
from pathlib import Path
import tempfile
import threading
import unittest
import sqlite3
from contextlib import closing
from unittest import mock

import generation
import statistics_snapshot
from tests import test_generation_gc as generation_fixtures


class SnapshotContentionTests(unittest.TestCase):
    def test_readers_continue_during_snapshot_copy(self):
        for padding_bytes in (0, 1024 * 1024):
            with self.subTest(padding_bytes=padding_bytes):
                self.check_readers_during_copy(padding_bytes)

    def check_readers_during_copy(self, padding_bytes):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            root = path / "generations"
            first = generation_fixtures.GenerationGCTests.make_db(path / "first.db", 10)
            old = generation.publish_generation("macbook", first,
                generation_fixtures.GenerationGCTests.make_meta(first, "2026-08-04T12:00:00Z"), root=root)
            second = generation_fixtures.GenerationGCTests.make_db(path / "second.db", 20)
            with closing(sqlite3.connect(second)) as conn, conn:
                conn.execute("CREATE TABLE test_payload (data BLOB)")
                conn.execute("INSERT INTO test_payload VALUES (zeroblob(?))", (padding_bytes,))
            meta = generation_fixtures.GenerationGCTests.make_meta(second, "2026-08-04T12:01:00Z")
            entered, release = threading.Event(), threading.Event()
            copy = generation.shutil.copyfile

            def paused(*args, **kwargs):
                entered.set()
                if not release.wait(5):
                    raise AssertionError("copy test was not released")
                return copy(*args, **kwargs)

            with mock.patch.object(generation.shutil, "copyfile", side_effect=paused):
                with ThreadPoolExecutor(max_workers=2) as pool:
                    writer = pool.submit(generation.publish_generation, "macbook", second, meta, root=root)
                    try:
                        self.assertTrue(entered.wait(5))
                        reader = pool.submit(generation.read_current_generation, "macbook", root=root)
                        try:
                            current = reader.result(timeout=1)
                        except TimeoutError:
                            self.fail("reader blocked behind snapshot copy")
                        self.assertEqual(current.meta["generation_id"], old.meta["generation_id"])
                        current.close()
                        # Another publisher/recovery pass must not delete the
                        # live preparation while its large copy is in flight.
                        pool.submit(generation.gc_generations, "macbook", root=root).result(timeout=1)
                    finally:
                        release.set()
                    writer.result(timeout=5)
            self.assertEqual(list((root / "macbook").glob(".preparing-*")), [])

    def test_abandoned_preparations_are_reaped_and_changed_copy_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            root = path / "generations"
            first = generation_fixtures.GenerationGCTests.make_db(path / "first.db", 10)
            old = generation.publish_generation("macbook", first,
                generation_fixtures.GenerationGCTests.make_meta(first, "2026-08-04T12:00:00Z"), root=root)
            abandoned = root / "macbook" / ".preparing-abandoned"
            abandoned.mkdir()
            (abandoned / "snapshot.db").write_bytes(b"partial")
            generation.gc_generations("macbook", root=root)
            self.assertFalse(abandoned.exists())
            second = generation_fixtures.GenerationGCTests.make_db(path / "second.db", 20)
            def mutate(previous, candidate):
                with Path(candidate).open("ab") as handle:
                    handle.write(b"modified after validation")
            with mock.patch.object(statistics_snapshot, "ensure_retained", side_effect=mutate):
                with self.assertRaisesRegex(generation.GenerationValidationError, "changed before publication"):
                    generation.publish_generation("macbook", second,
                        generation_fixtures.GenerationGCTests.make_meta(second, "2026-08-04T12:01:00Z"), root=root)
            current = generation.read_current_generation("macbook", root=root)
            self.assertEqual(current.meta["generation_id"], old.meta["generation_id"])
            current.close()
            self.assertEqual(list((root / "macbook").glob(".preparing-*")), [])


    def test_readers_continue_during_retention_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            root = path / "generations"
            first = generation_fixtures.GenerationGCTests.make_db(path / "first.db", 10)
            meta = generation_fixtures.GenerationGCTests.make_meta(first, "2026-08-04T12:00:00Z")
            old = generation.publish_generation("macbook", first, meta, root=root)
            second = generation_fixtures.GenerationGCTests.make_db(path / "second.db", 20)
            meta = generation_fixtures.GenerationGCTests.make_meta(second, "2026-08-04T12:01:00Z")
            entered, release = threading.Event(), threading.Event()
            check = statistics_snapshot.ensure_retained

            def paused(*args):
                entered.set()
                if not release.wait(5):
                    raise AssertionError("retention test was not released")
                return check(*args)

            with mock.patch.object(statistics_snapshot, "ensure_retained", side_effect=paused):
                with ThreadPoolExecutor(max_workers=2) as pool:
                    writer = pool.submit(generation.publish_generation, "macbook", second, meta, root=root)
                    try:
                        self.assertTrue(entered.wait(5))
                        reader = pool.submit(generation.read_current_generation, "macbook", root=root)
                        try:
                            current = reader.result(timeout=1)
                        except TimeoutError:
                            self.fail("reader blocked behind full history validation")
                        self.assertEqual(current.meta["generation_id"], old.meta["generation_id"])
                        current.close()
                    finally:
                        release.set()
                    writer.result(timeout=5)

    def test_retention_rechecks_if_another_writer_publishes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            root = path / "generations"
            snapshots = [generation_fixtures.GenerationGCTests.make_db(path / (str(i) + ".db"), i * 10)
                         for i in range(1, 4)]
            metas = [generation_fixtures.GenerationGCTests.make_meta(p, "2026-08-04T12:0%d:00Z" % i)
                     for i, p in enumerate(snapshots)]
            generation.publish_generation("macbook", snapshots[0], metas[0], root=root)
            check = statistics_snapshot.ensure_retained
            checked = []

            def interleave(previous, candidate):
                checked.append(Path(previous).parent.name)
                if len(checked) == 1:
                    with mock.patch.object(statistics_snapshot, "ensure_retained", side_effect=check):
                        generation.publish_generation("macbook", snapshots[1], metas[1], root=root)
                return check(previous, candidate)

            with mock.patch.object(statistics_snapshot, "ensure_retained", side_effect=interleave):
                generation.publish_generation("macbook", snapshots[2], metas[2], root=root)
            self.assertEqual(checked, [metas[0]["generation_id"], metas[1]["generation_id"]])

    def test_cold_validation_keeps_lease_without_blocking_publication(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            root = path / "generations"
            first = generation_fixtures.GenerationGCTests.make_db(path / "first.db", 10)
            old = generation.publish_generation("macbook", first,
                generation_fixtures.GenerationGCTests.make_meta(first, "2026-08-04T12:00:00Z"), root=root)
            with generation._READ_VALIDATIONS_LOCK:
                generation._READ_VALIDATIONS.clear()
            entered, release = threading.Event(), threading.Event()
            validate = generation._validate_generation

            def paused(*args, **kwargs):
                if threading.current_thread().name.startswith("cold-reader"):
                    entered.set()
                    if not release.wait(5):
                        raise AssertionError("cold reader was not released")
                return validate(*args, **kwargs)

            with mock.patch.object(generation, "_validate_generation", side_effect=paused):
                with ThreadPoolExecutor(max_workers=1, thread_name_prefix="cold-reader") as pool:
                    future = pool.submit(generation.read_current_generation, "macbook", root=root)
                    try:
                        self.assertTrue(entered.wait(5))
                        for i in (1, 2):
                            snapshot = generation_fixtures.GenerationGCTests.make_db(path / (str(i) + ".db"), 20 + i)
                            generation.publish_generation("macbook", snapshot,
                                generation_fixtures.GenerationGCTests.make_meta(snapshot, "2026-08-04T12:0%d:00Z" % i), root=root)
                        self.assertTrue(old.db_path.exists(), "GC removed the generation during validation")
                    finally:
                        release.set()
                    loaded = future.result(timeout=5)
                    self.assertEqual(loaded.meta["generation_id"], old.meta["generation_id"])
                    loaded.close()
            generation.gc_generations("macbook", root=root)
            self.assertFalse(old.db_path.exists())
