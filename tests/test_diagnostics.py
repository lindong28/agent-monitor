import http.client
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest import mock
from contextlib import contextmanager
from types import SimpleNamespace
import subprocess
import sys

from agent_monitor import diagnostics
from agent_monitor import http_server as server
from agent_monitor.quota import quota_sync


class DiagnosticsTests(unittest.TestCase):
    def test_extracted_component_frames_remain_visible_without_external_paths(self):
        for relative, expected in (("agent_monitor/collection.py", "collection.py"),
                                   ("agent_monitor/quota/quota_service.py", "quota_service.py"),
                                   ("agent_monitor/http_server.py", "http_server.py"),
                                   ("agent_monitor/accounts/codex_accounts.py", "codex_accounts.py"),
                                   ("untrusted/collection.py", None)):
            with self.subTest(source=relative):
                try:
                    exec(compile("raise ValueError('synthetic')", str(diagnostics.PROJECT_ROOT / relative), "exec"))
                except ValueError as exc:
                    facts = diagnostics.failure_details(exc)
                files = [frame["file"] for frame in facts.get("frames", [])]
                self.assertEqual(files, [] if expected is None else [expected])

    def test_legacy_failure_frames_still_survive_persistence_readback(self):
        legacy = {"frames": [{"file": "server.py", "line": 30},
                             {"file": "quota_refresh.py", "line": 20}]}
        self.assertEqual(diagnostics.safe_failure(json.loads(json.dumps(legacy))), legacy)

    def test_quota_worker_reports_machine_and_provider_failures(self):
        for failure in (None, "machine", "provider"):
            with self.subTest(failure=failure):
                monitor = diagnostics.Monitor()
                payload = {"schema_version": 1, "source_host_identity": "fixture",
                           "exporter_commit": "a" * 40, "rate_limits": {"claude": {}, "codex": {}}}
                if failure == "provider":
                    payload["rate_limits"]["codex"]["refresh_error"] = "fixture failure"
                message = {"machine": "fixture", "payload": None if failure == "machine" else payload,
                           "error": "fixture failure" if failure == "machine" else None}
                stream = json.dumps(message) + "\n" + json.dumps({"done": True}) + "\n"
                @contextmanager
                def worker(*args, **kwargs):
                    with subprocess.Popen([sys.executable, "-c", "import sys;sys.stdin.readline();sys.stdout.write(" + repr(stream) + ")"],
                                          stdin=subprocess.PIPE, stdout=subprocess.PIPE) as child:
                        yield child
                complete = mock.Mock()
                machine = SimpleNamespace(name="fixture", as_config_dict=lambda: {})
                with mock.patch.object(diagnostics, "monitor", monitor), mock.patch("agent_monitor.quota.quota_sync.sync_process.supervised_worker", worker):
                    quota_sync.run_queries([machine], complete)
                complete.assert_called_once_with("fixture", message["payload"], message["error"])
                self.assertEqual(monitor.snapshot()["counts_since_start"]["failed"], int(failure is not None))

    def test_rejected_reexec_keeps_observer_available(self):
        for failure in (None, "cleanup", "exec"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as tmp:
                monitor = diagnostics.Monitor()
                monitor.start(Path(tmp), interval=.01)
                try:
                    with mock.patch.object(diagnostics, "monitor", monitor), mock.patch.object(server.threading, "Timer") as timer,\
                         mock.patch.object(server.codex_accounts.manager, "close", side_effect=RuntimeError("cleanup") if failure == "cleanup" else None),\
                         mock.patch.object(server.QUOTA_REFRESH, "shutdown"), mock.patch.object(server.COLLECTION, "query_view", None),\
                         mock.patch.object(server.os, "execv", side_effect=OSError("exec") if failure == "exec" else None) as execute:
                        guard = server.deployment_guard.deployment_lock(Path(tmp), inherit=False)
                        guard.__enter__()
                        server._schedule_reexec(guard)
                        if failure:
                            with self.assertRaises((RuntimeError, OSError)):
                                timer.call_args.args[1]()
                        else:
                            timer.call_args.args[1]()
                        self.assertEqual(execute.called, failure != "cleanup")
                        self.assertTrue(monitor.snapshot()["observer_running"])
                finally:
                    monitor.close()

    def test_completed_inflight_error_and_bounded_coverage(self):
        clock = [0.]
        monitor = diagnostics.Monitor(limit=2, clock=lambda: clock[0])
        tokens = [monitor.begin("fixture") for _ in range(3)]
        clock[0] = 31
        monitor.tick()
        result = monitor.snapshot()
        self.assertEqual(result["counts_since_start"]["active"], 3)
        self.assertEqual(result["coverage"]["active_details_omitted"], 1)
        self.assertEqual(len(result["active"]), 2)
        self.assertEqual({kind for kind, _ in monitor.incidents}, {"long_running", "sources_unknown"})
        self.assertEqual(len(monitor.events), 2)
        for i, token in enumerate(tokens):
            monitor.finish(token, "error" if i == 0 else "success")
        result = monitor.snapshot()
        self.assertEqual(result["counts_since_start"], dict(started=3, completed=3, failed=1, slow=3, active=0))
        self.assertEqual(len(result["recent"]), 2)
        self.assertEqual(result["coverage"]["completed_samples_evicted"], 1)
        self.assertGreater(result["coverage"]["events_dropped"], 0)
        with monitor.operation("fast"):
            pass
        self.assertEqual(monitor.snapshot()["recent"][-1]["duration_seconds"], 0)

    def test_source_stall_unknown_and_recovery_use_observation_not_publish_time(self):
        monitor = diagnostics.Monitor()
        def sources(age, error=None):
            monitor.set_sources({"fixture": {"generated_at": "2026-01-02T00:00:00Z",
                                             "observed_at": age}}, interval=60, stale_seconds=3600, error=error)
        with mock.patch("agent_monitor.diagnostics.time.time", return_value=diagnostics.timestamp("2026-01-02T00:00:00Z")):
            sources("2026-01-01T00:00:00Z")
            monitor.tick()
            row = monitor.snapshot()["sources"][0]
            self.assertTrue(row["stale"])
            self.assertEqual(row["generated_age_seconds"], 0)
            self.assertEqual(row["age_intervals"], 1440)
            self.assertIn(("source_stale", "fixture"), monitor.incidents)
            sources(None, "metadata_failed")
            monitor.tick()
            self.assertIn(("source_stale", "fixture"), monitor.incidents)
            sources("2026-01-02T00:00:00Z")
            monitor.tick()
            self.assertNotIn(("source_stale", "fixture"), monitor.incidents)
            self.assertTrue(any(e["kind"] == "source_stale" and e["transition"] == "cleared" for e in monitor.events))

    def test_persistence_failure_recovers_and_rotation_is_bounded(self):
        monitor = diagnostics.Monitor(slow_seconds=0)
        self.addCleanup(monitor.close)
        with tempfile.TemporaryDirectory() as tmp:
            monitor.path = Path(tmp) / "diagnostics"
            monitor.path.write_text("blocks directory")
            monitor.tick()
            self.assertIsNotNone(monitor.snapshot()["persistence_error"])
            monitor.path.unlink()
            monitor.tick()
            self.assertIsNone(monitor.snapshot()["persistence_error"])
            monitor.event_handler.maxBytes = 300
            for _ in range(12):
                with monitor.operation("fixture"):
                    pass
                monitor.tick()
            persisted = json.loads((monitor.path / "current.json").read_text())
            self.assertEqual(persisted["counts_since_start"]["completed"], 12)
            self.assertEqual(persisted["instance_id"], monitor.instance_id)
            self.assertLessEqual(len(list(monitor.path.glob("events.jsonl*"))), 3)
            self.assertTrue((monitor.path / "events.jsonl.2").exists())

    def test_http_diagnostics_progress_while_business_route_is_blocked(self):
        monitor = diagnostics.Monitor(long_seconds=.01)
        entered, release = threading.Event(), threading.Event()
        def blocked(query):
            with monitor.stage("fixture_wait"):
                entered.set()
                if not release.wait(3):
                    raise RuntimeError("fixture not released")
            return {"ok": True}
        httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        serving = threading.Thread(target=httpd.serve_forever, daemon=True)
        serving.start()
        def request(path):
            conn = http.client.HTTPConnection(*httpd.server_address, timeout=2)
            try:
                conn.request("GET", path)
                response = conn.getresponse()
                return response.status, json.loads(response.read())
            finally:
                conn.close()
        with mock.patch.object(diagnostics, "monitor", monitor), mock.patch.dict(httpd.routes, {"/api/fixture": blocked}):
            worker = threading.Thread(target=lambda: request("/api/fixture?credential=never-record"))
            try:
                worker.start()
                self.assertTrue(entered.wait(1))
                status, report = request("/api/diagnostics")
                self.assertEqual(status, 200)
                self.assertTrue(any(row["phase"] == "fixture_wait" for row in report["active"]))
                self.assertNotIn("credential", json.dumps(report))
                self.assertNotIn("never-record", json.dumps(report))
            finally:
                release.set()
                worker.join(3)
                httpd.shutdown()
                serving.join(3)
                httpd.server_close()
            self.assertEqual(monitor.snapshot()["counts_since_start"]["completed"], 2)
            self.assertFalse(monitor.snapshot()["active"])
