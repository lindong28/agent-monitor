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

import exporter
import generation
import sync
import server
import test_generation as fixtures
from machine_config import Machine


class AsyncRefreshTests(unittest.TestCase):
    def publish(self, directory):
        source = fixtures.GenerationTests.make_db(directory / "source.db", 12)
        meta = fixtures.GenerationTests.make_meta(source, "host-a")
        meta["rate_limits"] = {p: {"account_id": "account-a", "updated_at": "2026-10-01T00:00:00Z"}
                               for p in ("claude", "codex")}
        meta["generation_id"] = generation._generation_id(meta)
        return generation.publish_generation("macbook", source, meta, root=directory / "generations")

    def test_unchanged_reads_reuse_validation_but_replacement_revalidates(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            current = self.publish(root)
            generation._READ_VALIDATIONS.clear()
            with mock.patch("generation._validate_generation", wraps=generation._validate_generation) as validate:
                for _ in range(2):
                    generation.read_current_generation("macbook", root=root / "generations").close()
                self.assertEqual(validate.call_count, 1)
                replacement = root / "replacement.db"
                shutil.copyfile(current.db_path, replacement)
                os.replace(replacement, current.db_path)
                generation.read_current_generation("macbook", root=root / "generations").close()
                self.assertEqual(validate.call_count, 2)
            current.close()

    def test_cached_read_rejects_database_metadata_and_wal_changes(self):
        for mutation in ("database", "metadata", "wal"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                current = self.publish(root)
                generation.read_current_generation("macbook", root=root / "generations").close()
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
                    generation.read_current_generation("macbook", root=root / "generations")
                current.close()

    def test_quota_only_export_does_not_collect_statistics(self):
        limits = {p: {"signed_out": True} for p in ("claude", "codex")}
        with mock.patch("exporter.exporter_version", return_value="a" * 40), \
             mock.patch("exporter.generation.self_certified_host_identity", return_value="host-a"), \
             mock.patch("exporter._rate_limits", return_value=limits), \
             mock.patch("exporter.rollup.load_all_entries", side_effect=AssertionError("statistics scanned")):
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
                machine = Machine("macbook", "macbook", True)
                with mock.patch("sync.exporter.export_quota", return_value=payload):
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
        with mock.patch("sync.load_machine_config", return_value=SimpleNamespace(machines=machines)), \
             mock.patch("sync.sync_machine", side_effect=statistics) as collect, \
             mock.patch("sync.sync_quota", side_effect=quota):
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
eval(fs.readFileSync('web/app.js','utf8'));
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

    def test_request_receipt_survives_later_request_before_response(self):
        server._reset_sync_state_for_tests()
        admission = SimpleNamespace(records=())
        def later_request():
            server._maybe_sync_remotes({"force": ["1"]})
            return server._sync_runtime_snapshot()
        try:
            with mock.patch("server.generation.generation_admission_snapshot", side_effect=lambda: contextlib.nullcontext(admission)), \
                 mock.patch("server.sync.load_machine_config", return_value=SimpleNamespace(machines=())), \
                 mock.patch("server.threading.Thread"), \
                 mock.patch("server._sync_status", side_effect=later_request):
                status = server.refresh_endpoint({})
            self.assertEqual(status["refresh_request"], 1)
            self.assertEqual(status["refresh_requested"], 2)
            self.assertTrue(status["queued_refresh"])
        finally:
            server._reset_sync_state_for_tests()

    def test_ack_does_not_read_snapshots_or_wait_for_publication(self):
        server._reset_sync_state_for_tests()
        try:
            with mock.patch("server.generation.generation_admission_snapshot", side_effect=AssertionError("snapshot lock reached")), \
                 mock.patch("server.sync.load_machine_config", return_value=SimpleNamespace(machines=())), \
                 mock.patch("server.threading.Thread"):
                status = server.refresh_endpoint({"ack": ["1"]})
            self.assertEqual(status["refresh_request"], 1)
            self.assertTrue(status["syncing"])
            self.assertEqual(status["instance_id"], server.SERVER_INSTANCE_ID)
        finally:
            server._reset_sync_state_for_tests()
