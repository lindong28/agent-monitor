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
from tests.test_llm_attempts import LedgerFixture


class SnapshotContentionTests(unittest.TestCase):
    def test_exported_detail_queries_use_indexes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            ledger = LedgerFixture(path / "ledger.db")
            request = ledger.request("selected", "2026-08-31T00:00:00Z", project="one")
            ledger.attempt(request, "selected-attempt", "2026-08-31T00:00:01Z")
            snapshot = path / "snapshot.db"
            statistics_snapshot.write_snapshot(snapshot, [], ledger_path=ledger.path)
            with closing(sqlite3.connect(snapshot)) as conn:
                for table, field, value in (("requests", "logical_request_id", "selected"),
                                             ("requests", "id", request),
                                             ("attempts", "logical_request_fk", request),
                                             ("attempts", "attempt_id", "selected-attempt")):
                    with self.subTest(table=table, field=field):
                        plan = conn.execute("EXPLAIN QUERY PLAN SELECT payload FROM statistics_%s "
                                            "WHERE json_extract(payload, '$.%s') = ? ORDER BY position"
                                            % (table, field), (value,)).fetchall()
                        self.assertTrue(any("USING INDEX" in row[3] for row in plan), plan)
            self.assertEqual(statistics_snapshot.read_snapshot(snapshot)["gateway"]["requests"][0]["logical_request_id"], "selected")

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
