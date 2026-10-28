from agent_monitor import collection
from component_fixtures import reset_collection
import json
import contextlib
import os
import shutil
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from agent_monitor.snapshots import exporter
from agent_monitor.snapshots import generation
from agent_monitor.snapshots import sync
from agent_monitor import http_server as server
import test_generation as fixtures
from agent_monitor.snapshots.machine_config import Machine


class AsyncRefreshTests(unittest.TestCase):
    def publish(self, directory):
        source = fixtures.GenerationTests.make_db(directory / "source.db", 12)
        meta = fixtures.GenerationTests.make_meta(source, "host-a")
        meta["rate_limits"] = {p: {"account_id": "account-a", "updated_at": "2026-10-01T00:00:00Z"}
                               for p in ("claude", "codex")}
        meta["generation_id"] = generation._generation_id(meta)
        return generation.publish_generation("node-b", source, meta, root=directory / "generations")

    def test_unchanged_reads_reuse_validation_but_replacement_revalidates(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            current = self.publish(root)
            generation._READ_VALIDATIONS.clear()
            with mock.patch("agent_monitor.snapshots.generation._validate_generation", wraps=generation._validate_generation) as validate:
                for _ in range(2):
                    generation.read_current_generation("node-b", root=root / "generations").close()
                self.assertEqual(validate.call_count, 1)
                replacement = root / "replacement.db"
                shutil.copyfile(current.db_path, replacement)
                os.replace(replacement, current.db_path)
                generation.read_current_generation("node-b", root=root / "generations").close()
                self.assertEqual(validate.call_count, 2)
            current.close()

    def test_cached_read_rejects_database_metadata_and_wal_changes(self):
        for mutation in ("database", "metadata", "wal"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                current = self.publish(root)
                generation.read_current_generation("node-b", root=root / "generations").close()
                if mutation == "database":
                    old = current.db_path.stat()
                    with current.db_path.open("r+b") as handle:
                        handle.seek(-1, 2)
                        value = handle.read(1)
                        handle.seek(-1, 2)
                        handle.write(bytes([value[0] ^ 1]))
                    os.utime(current.db_path, ns=(old.st_atime_ns, old.st_mtime_ns))
                elif mutation == "metadata":
                    path = current.generation_dir / "meta.json"
                    meta = json.loads(path.read_text())
                    meta["rate_limits"] = {}
                    path.write_text(json.dumps(meta))
                else:
                    Path(str(current.db_path) + "-wal").write_bytes(b"nonempty WAL")
                with self.assertRaises(generation.GenerationError):
                    generation.read_current_generation("node-b", root=root / "generations")
                current.close()

    def test_quota_only_export_does_not_collect_statistics(self):
        limits = {p: {"signed_out": True} for p in ("claude", "codex")}
        with mock.patch("agent_monitor.snapshots.exporter.exporter_version", return_value="a" * 40),\
             mock.patch("agent_monitor.snapshots.exporter.generation.self_certified_host_identity", return_value="host-a"),\
             mock.patch("agent_monitor.snapshots.exporter._rate_limits", return_value=limits),\
             mock.patch("agent_monitor.snapshots.exporter.rollup.load_all_entries", side_effect=AssertionError("statistics scanned")):
            self.assertEqual(exporter.export_quota()["rate_limits"], limits)

    def test_quota_publication_preserves_statistics_and_reports_wrong_source(self):
        for wrong_source in (False, True):
            with self.subTest(wrong_source=wrong_source), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                current = self.publish(root)
                limits = {p: {"account_id": "account-b", "updated_at": "2026-10-03T00:00:00Z"}
                          for p in ("claude", "codex")}
                payload = {"schema_version": 1, "source_host_identity": "wrong" if wrong_source else current.meta["source_host_identity"],
                           "exporter_commit": "b" * 40, "rate_limits": limits}
                machine = Machine("node-b", "node-b", True)
                with mock.patch("agent_monitor.snapshots.sync.exporter.export_quota", return_value=payload):
                    updated = sync.sync_quota(machine, current, root=root / "generations")
                self.assertEqual(updated.db_path.read_bytes(), current.db_path.read_bytes())
                self.assertEqual(updated.meta["generated_at"], current.meta["generated_at"])
                self.assertEqual(updated.meta["exporter_commit"], current.meta["exporter_commit"])
                block = updated.meta["rate_limits"]["codex"]
                self.assertEqual(block["account_id"], "account-a" if wrong_source else "account-b")
                self.assertEqual("refresh_error" in block, wrong_source)
                updated.close()
                current.close()

    def test_fast_machine_starts_quota_before_slow_statistics_finish(self):
        quota_started = threading.Event()
        machines = [Machine("fast", "fast", True), Machine("slow", "slow", False)]
        def statistics(machine, **kwargs):
            self.assertFalse(kwargs["quota_refresh"])
            if machine.name == "slow":
                self.assertTrue(quota_started.wait(2), "quota waited for every machine's statistics")
            return SimpleNamespace(close=lambda: None)
        def quota(machine, current, **kwargs):
            if machine.name == "fast":
                quota_started.set()
            return current
        with mock.patch("agent_monitor.snapshots.sync.load_machine_config", return_value=SimpleNamespace(machines=machines)),\
             mock.patch("agent_monitor.snapshots.sync.sync_machine", side_effect=statistics) as collect,\
             mock.patch("agent_monitor.snapshots.sync.sync_quota", side_effect=quota):
            result = sync.sync_all(progressive=True)
        self.assertEqual(collect.call_count, 2)
        self.assertTrue(all(value.generation is not None for value in result.values()))

    def test_frontend_request_completion_does_not_wait_for_followup(self):
        script = r'''
const fs = require('fs');
global.window = {location: {origin:'http://example.test',pathname:'/',search:''},history:{replaceState(){}}};
global.document = {readyState:'loading',addEventListener(){},querySelector(){return null},querySelectorAll(){return []}};
global.setTimeout = cb => { cb(); return 1; };
let calls = 0;
let background = false;
global.fetch = async url => {
  if (url.pathname === '/api/timezone') return {ok:true,json:async()=>({timezone:'UTC'})};
  if (++calls > 1) throw new Error('waited for followup');
  return {ok:true,json:async()=>({instance_id:'hub',refresh_requested:2,refresh_completed:1,syncing:!background,queued_refresh:!background,machines:[]})};
};
require('./web/app.js')();
window.AgentMonitor.waitForSyncTerminal({instance_id:'hub',refresh_request:1,refresh_requested:2,refresh_completed:0,syncing:true,machines:[]})
 .then(async value=>{
   if(value.polling_error||calls!==1)process.exitCode=1;
   const done=await window.AgentMonitor.waitForSyncTerminal({instance_id:'hub',refresh_request:1,refresh_requested:2,refresh_completed:1,syncing:true,machines:[]});
   if(done.polling_error||calls!==1)process.exitCode=1;
   calls=0; background=true;
   const idle=await window.AgentMonitor.waitForSyncTerminal({instance_id:'hub',refresh_requested:1,refresh_completed:1,syncing:true,machines:[]},{waitForIdle:true});
   if(idle.polling_error||calls!==1||idle.syncing)process.exitCode=1;
 })
 .catch(()=>process.exitCode=1);
'''
        result = subprocess.run(["node", "-e", script], cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_quota_progress_reloads_without_a_new_statistics_generation(self):
        script = r'''
const fs = require('fs'), assert = require('assert');
global.window = {location:{origin:'http://example.test',pathname:'/',search:''},history:{replaceState(){}}};
global.document = {readyState:'loading',addEventListener(){},querySelector(){return null},querySelectorAll(){return []}};
global.setTimeout = cb => { cb(); return 1; };
const base = {instance_id:'hub',refresh_request:1,refresh_requested:1,refresh_completed:0,syncing:true,
  machines:[{name:'node-c',admitted:true,generation_id:'unchanged'}],quota_refresh:{revision:0}};
let polls=0, progress=[];
global.fetch=async url => ({ok:true,json:async()=>url.pathname==='/api/timezone'?{timezone:'UTC'}:
  Object.assign({},base,{quota_refresh:{revision:1},refresh_completed:++polls===2?1:0})});
require('./web/app.js')();
window.AgentMonitor.waitForSyncTerminal(base,{onProgress:async s=>progress.push(s.refresh_completed)})
 .then(()=>assert.deepStrictEqual(progress,[0])).catch(e=>{console.error(e);process.exitCode=1;});
'''
        result = subprocess.run(["node", "-e", script], cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_request_receipt_survives_later_request_before_response(self):
        reset_collection()
        admission = SimpleNamespace(records=())
        def later_request():
            server.COLLECTION.request({"force": ["1"]})
            return server.COLLECTION.snapshot()
        try:
            with mock.patch("agent_monitor.http_server.generation.generation_admission_snapshot", side_effect=lambda: contextlib.nullcontext(admission)),\
                 mock.patch("agent_monitor.http_server.sync.load_machine_config", return_value=SimpleNamespace(machines=())),\
                 mock.patch("agent_monitor.http_server.threading.Thread"),\
                 mock.patch("agent_monitor.http_server.COLLECTION.status", side_effect=later_request):
                status = server.refresh_endpoint({})
            self.assertEqual(status["refresh_request"], 1)
            self.assertEqual(status["refresh_requested"], 2)
            self.assertTrue(status["queued_refresh"])
        finally:
            reset_collection()

    def test_ack_does_not_read_snapshots_or_wait_for_publication(self):
        reset_collection()
        try:
            with mock.patch("agent_monitor.http_server.generation.generation_admission_snapshot", side_effect=AssertionError("snapshot lock reached")),\
                 mock.patch("agent_monitor.http_server.sync.load_machine_config", return_value=SimpleNamespace(machines=())),\
                 mock.patch("agent_monitor.http_server.threading.Thread"):
                status = server.refresh_endpoint({"ack": ["1"]})
            self.assertEqual(status["refresh_request"], 1)
            self.assertTrue(status["syncing"])
            self.assertEqual(status["instance_id"], server.SERVER_INSTANCE_ID)
        finally:
            reset_collection()


class CollectionComponentTests(unittest.TestCase):
    def test_coordinators_do_not_share_round_state(self):
        manager = mock.Mock()
        first = collection.CollectionCoordinator(manager, mock.Mock(), "first")
        second = collection.CollectionCoordinator(manager, mock.Mock(), "second")
        config = SimpleNamespace(machines=[Machine("fixture", "fixture", True)])
        with mock.patch.object(collection.hub, "enabled", return_value=False),\
                mock.patch.object(collection.sync, "load_machine_config", return_value=config),\
                mock.patch.object(collection.threading, "Thread") as thread:
            self.assertTrue(first.request({"force": ["1"]}, request_receipt={}))
        thread.return_value.start.assert_called_once_with()
        self.assertTrue(first.snapshot()["syncing"])
        self.assertEqual(first.snapshot()["refresh_requested"], 1)
        self.assertFalse(second.snapshot()["syncing"])
        self.assertEqual(second.snapshot()["refresh_requested"], 0)

    def test_query_view_and_scheduler_share_coordinator_lifetime(self):
        manager = mock.Mock()
        coordinator = collection.CollectionCoordinator(manager, mock.Mock(), "fixture")
        with mock.patch.object(coordinator, "capture_startup_config") as capture,\
                mock.patch.object(collection.hub, "enabled", return_value=True),\
                mock.patch.object(collection.hub, "is_hub", return_value=True),\
                mock.patch.object(collection.hub, "interval", return_value=120),\
                mock.patch.object(collection.query_view, "QueryView") as view,\
                mock.patch.object(collection.threading, "Thread") as thread:
            coordinator.start()
            thread.assert_not_called()
            capture.assert_called_once_with()
            view.return_value.start.assert_called_once_with(interval=10)
            coordinator.start_schedule()
            thread.return_value.start.assert_called_once_with()
            coordinator.close()
        self.assertTrue(coordinator._stop.is_set())
        manager.shutdown.assert_called_once_with()
        view.return_value.close.assert_called_once_with()
