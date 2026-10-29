import json
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

from agent_monitor.snapshots import generation
import agent_monitor.views.query_view as query_view
from agent_monitor.accounting import rollup
from agent_monitor import http_server as server
from agent_monitor.snapshots.machine_config import Machine; from agent_monitor.snapshots.machine_config import machine_config_fingerprint
from tests.fixtures.rollup_snapshot import make_generation_meta, make_rollup_snapshot


class QueryViewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)
        self.root = self.path / "generations"
        self.config = self.path / "machines.json"
        self.machine = Machine("node-b", "node-b", True)
        self.config.write_text(json.dumps({"machines": [self.machine.as_config_dict()], "retired_names": []}))
        self.view = query_view.QueryView(config_path=self.config, root=self.root)
        self.addCleanup(self.view.close)

    def publish(self, index):
        db = make_rollup_snapshot(self.path / ("source%d.db" % index), index * 10)
        meta = make_generation_meta(
            db, "2026-08-04T12:%02d:00Z" % index,
            machine_config_fingerprint=machine_config_fingerprint(self.machine),
        )
        return generation.publish_generation("node-b", db, meta, root=self.root)

    def test_http_queries_reuse_prepared_admission_and_existing_aggregates(self):
        for index in (1, 2):
            current = self.publish(index)
            self.view.refresh()
            with mock.patch.object(server.COLLECTION, "query_view", self.view), mock.patch.object(server.COLLECTION, "request"),\
                    mock.patch.object(server.hub, "enabled", return_value=False),\
                    mock.patch.object(server.QUOTA_VIEW, "rate_limits", return_value={}),\
                    mock.patch.object(generation, "_validate_generation", side_effect=AssertionError("HTTP did validation")),\
                    mock.patch.object(generation, "generation_admission_snapshot", side_effect=AssertionError("HTTP did admission")):
                for repeat in (1, 2):
                    with self.subTest(generation=index, repeat=repeat):
                        result = server.pivot_endpoint({"range": ["all"], "group_by": ["none"], "metric": ["input"]})
                        self.assertEqual(result["rows"], [{"key": [], "value": index * 10}])
                        self.assertEqual(server.pivot_filters_endpoint({"range": ["all"]})["machine"], ["node-b"])
                        status = server.sync_status_endpoint({})
                        self.assertEqual(status["machines"][0]["generation_id"], current.meta["generation_id"])
                        overview = server.create_views().overview.read({"range": ["all"]})
                        self.assertEqual(overview["sync"]["coverage"], {"admitted": 1, "declared": 1})
                        self.assertEqual(overview["range"]["tokens"], index * 10 + 9)

    def test_old_readers_keep_leases_across_refresh_and_gc(self):
        first = self.publish(1)
        self.view.refresh()
        with self.view.snapshot() as admission:
            self.publish(2)
            self.publish(3)
            self.view.refresh()
            generation.gc_generations("node-b", root=self.root)
            self.assertTrue(first.db_path.exists())
            with rollup.use_admitted_generations(admission.admitted):
                self.assertEqual(rollup.query_grouped([], "input")["rows"][0]["value"], 10)
        generation.gc_generations("node-b", root=self.root)
        self.assertFalse(first.db_path.exists())
        with self.view.snapshot() as admission:
            self.assertEqual(admission.admitted[0].meta["generation_id"], self.publish_meta_id(3))

    def publish_meta_id(self, index):
        return generation.build_generation_meta(self.path / ("source%d.db" % index),
            machine_config_fingerprint=machine_config_fingerprint(self.machine),
            source_host_identity="host-v1:" + "5" * 64, aliases=[], rate_limits={},
            exporter_commit="a" * 40, generated_at="2026-08-04T12:%02d:00Z" % index)["generation_id"]

    def test_preparation_does_not_block_current_readers(self):
        first = self.publish(1)
        self.view.refresh()
        self.publish(2)
        with generation._READ_VALIDATIONS_LOCK:
            generation._READ_VALIDATIONS.clear()
        entered, release = threading.Event(), threading.Event()
        validate = generation._validate_generation

        def paused(*args, **kwargs):
            entered.set()
            if not release.wait(5):
                raise AssertionError("preparation not released")
            return validate(*args, **kwargs)

        with mock.patch.object(generation, "_validate_generation", side_effect=paused), ThreadPoolExecutor(2) as pool:
            preparing = pool.submit(self.view.refresh)
            try:
                self.assertTrue(entered.wait(2))
                def read():
                    with self.view.snapshot() as admission:
                        return admission.admitted[0].meta["generation_id"]
                self.assertEqual(pool.submit(read).result(timeout=1), first.meta["generation_id"])
            finally:
                release.set()
            preparing.result(timeout=3)

    def test_configuration_change_and_snapshot_mutation_fail_closed_then_recover(self):
        first = self.publish(1)
        self.view.refresh()
        original = self.config.read_text()
        self.config.write_text(original.replace('"ssh_host": "node-b"', '"ssh_host": "other"'))
        with self.assertRaises(query_view.ViewUnavailable):
            with self.view.snapshot():
                pass
        self.view.refresh()
        with self.view.snapshot() as admission:
            self.assertEqual(admission.records[0].exclusion_reason, "machine_config_fingerprint")
        self.config.write_text(original)
        self.view.refresh()
        with first.db_path.open("ab") as handle:
            handle.write(b"tampered")
        with self.assertRaises(query_view.ViewUnavailable):
            with self.view.snapshot():
                pass
        self.view.refresh()
        with self.view.snapshot() as admission:
            self.assertFalse(admission.records[0].admitted)
            self.assertEqual(admission.records[0].exclusion_reason, "digest")

    def test_build_failure_is_unavailable_and_close_releases_active_reader_later(self):
        self.publish(1)
        self.view.refresh()
        with mock.patch.object(generation, "generation_admission_snapshot", side_effect=ValueError("config unavailable")):
            with self.assertRaises(ValueError):
                self.view.refresh()
        with self.assertRaises(query_view.ViewUnavailable):
            with self.view.snapshot():
                pass
        self.view.refresh()
        with self.view.snapshot() as admission:
            lease = admission.admitted[0]._lease
            self.view.close()
            self.assertFalse(lease.closed)
        self.assertTrue(lease.closed)
