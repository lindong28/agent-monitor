"""Exercise monitor HTTP adapters against admitted, machine-separated snapshots."""
from contextlib import contextmanager
from datetime import datetime, timezone
import http.client
import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest import mock
from urllib.parse import urlencode

from aggregators import LoadedEntries
import exporter
import generation
import llm_attempts
from machine_config import Machine
import server
import statistics_snapshot
import sync
from tests.test_llm_attempts import LedgerFixture, NOW


class CallDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.generations = []
        self.ledgers = []
        for machine in ("alpha", "beta"):
            ledger = LedgerFixture(self.root / (machine + ".sqlite3"))
            first = ledger.request("shared-id", "2026-07-01T00:00:00Z", project="one")
            ledger.attempt(first, "shared-attempt", "2026-07-01T00:00:01Z",
                           outcome="http_error", cost_state="unknown", latency_ms=None)
            ledger.attempt(first, "retry", "2026-08-31T00:00:01Z", attempt_no=2,
                           parent_attempt_id="shared-attempt", cost_state="estimated",
                           cost_value=.00000321 if machine == "alpha" else 2.5)
            second = ledger.request("shared-id", "2026-08-31T01:00:00Z", project="two")
            ledger.attempt(second, "other-project", "2026-08-31T01:00:01Z",
                           cost_value=0, latency_ms=0)
            for i in range(52):
                fk = ledger.request("bulk-%02d" % i, "2026-08-31T02:00:00Z", project="bulk")
                ledger.attempt(fk, "bulk-attempt-%02d" % i, "2026-08-31T02:00:01Z")
            self.ledgers.append(ledger.path)
            bundle = self.root / (machine + "-bundle")
            with mock.patch.object(exporter, "exporter_version", return_value="a" * 40):
                manifest = exporter.export_bundle(self.root / (machine + "-rollup.db"), bundle,
                    entries_loader=lambda: LoadedEntries([]), rate_limits={}, ledger_path=ledger.path,
                    source_host_identity="host-v1:" + ("1" if machine == "alpha" else "2") * 64,
                    generated_at=NOW.isoformat(), rollup_now=lambda: NOW)
            current = sync.install_export_bundle(Machine(machine, machine, False), bundle,
                expected_manifest=manifest, root=self.root / "generations", accept_first_use=True)
            current.close()
            current = generation.read_current_generation(machine, root=self.root / "generations")
            self.generations.append(current)
            self.addCleanup(current.close)
        self.admission_active = False

        @contextmanager
        def admission():
            self.admission_active = True
            try:
                yield SimpleNamespace(admitted=self.generations)
            finally:
                self.admission_active = False

        original_reader = statistics_snapshot.read_admitted_gateway

        def admitted_reader(current, **kwargs):
            self.assertTrue(self.admission_active, "reader escaped admitted snapshot lifetime")
            return original_reader(current, **kwargs)

        for patcher in (
            mock.patch.object(server.hub, "enabled", return_value=True),
            mock.patch.object(generation, "generation_admission_snapshot", side_effect=admission),
            mock.patch.object(statistics_snapshot, "read_admitted_gateway", side_effect=admitted_reader),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_server)

    def stop_server(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(5)

    def get(self, path, **query):
        connection = http.client.HTTPConnection("127.0.0.1", self.httpd.server_port, timeout=5)
        try:
            connection.request("GET", path + "?" + urlencode(query))
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    def test_detail_uses_machine_project_and_complete_chain(self):
        for machine, cost in (("alpha", .00000321), ("beta", 2.5)):
            with self.subTest(machine=machine):
                status, payload = self.get("/api/llm-call-request", machine=machine,
                    project="one", logical_request_id="shared-id", range="7d")
                self.assertEqual(status, 200)
                request = payload["request"]
                self.assertEqual((request["machine"], request["canonical_project_id"]), (machine, "one"))
                attempts = request["attempts"]
                self.assertEqual([a["attempt_no"] for a in attempts], [1, 2])
                self.assertEqual(attempts[0]["attempt_id"], {"machine": machine, "id": "shared-attempt"})
                self.assertIsNone(attempts[0]["cost_value"])
                self.assertEqual(attempts[0]["cost_state"], "unknown")
                self.assertIsNone(attempts[0]["latency_ms"])
                self.assertEqual(attempts[1]["cost_value"], cost)
                self.assertEqual(attempts[1]["credential_profile_display_name_source"], "source_registry_not_collected")
                self.assertEqual({s["machine"] for s in payload["sources"]}, {"alpha", "beta"})
                self.assertIn("as_of", payload)
        status, payload = self.get("/api/llm-call-request", machine="alpha", project="two", logical_request_id="shared-id")
        self.assertEqual(status, 200)
        self.assertEqual(payload["request"]["attempts"][0]["cost_value"], 0)
        self.assertEqual(payload["request"]["attempts"][0]["latency_ms"], 0)

    def test_lookup_rejects_ambiguous_or_missing_identity(self):
        for query, expected, code in (
            ({"logical_request_id": "shared-id"}, 400, "invalid_query"),
            ({"machine": "alpha", "logical_request_id": "shared-id"}, 409, "ambiguous_request_id"),
            ({"machine": "absent", "logical_request_id": "shared-id"}, 404, "request_not_found"),
            ({"machine": "alpha", "project": "absent", "logical_request_id": "shared-id"}, 404, "request_not_found"),
        ):
            with self.subTest(query=query):
                status, payload = self.get("/api/llm-call-request", **query)
                self.assertEqual((status, payload["error"]["code"]), (expected, code))
        status, payload = self.get("/api/llm-call-request", machine="beta", attempt_id="shared-attempt")
        self.assertEqual(status, 200)
        self.assertEqual(payload["request"]["canonical_project_id"], "one")
        self.assertEqual(payload["request"]["machine"], "beta")

    def test_detail_decodes_only_selected_request_and_complete_chain(self):
        for identity in ({"logical_request_id": "shared-id", "project": "one"},
                         {"attempt_id": "retry"}):
            with self.subTest(identity=identity), \
                 mock.patch.object(llm_attempts, "_request_row", wraps=llm_attempts._request_row) as requests, \
                 mock.patch.object(llm_attempts, "_attempt_row", wraps=llm_attempts._attempt_row) as attempts:
                status, payload = self.get("/api/llm-call-request", machine="alpha", **identity)
                self.assertEqual(status, 200)
                self.assertEqual(len(payload["request"]["attempts"]), 2)
                self.assertEqual(requests.call_count, 1)
                self.assertEqual(attempts.call_count, 2)

    def test_detail_matches_full_reader_projection(self):
        for identity in ({"logical_request_id": "shared-id", "project": "one"},
                         {"attempt_id": "other-project"}):
            query = {k: [v] for k, v in {"machine": "beta", **identity}.items()}
            sources = {g.db_path: g for g in self.generations}
            self.admission_active = True
            expected = llm_attempts.llm_call_request(query,
                sources=[(g.host, g.db_path) for g in self.generations],
                snapshot_reader=lambda p: statistics_snapshot.read_admitted_gateway(sources[p]))
            status, actual = self.get("/api/llm-call-request", machine="beta", **identity)
            self.assertEqual(status, 200)
            for payload in (expected, actual):
                payload["range"].pop("end_at")
            self.assertEqual(actual, expected)

    def test_export_is_complete_filtered_and_preserves_identity(self):
        for kind, expected in (("requests", 108), ("attempts", 110)):
            status, payload = self.get("/api/llm-calls-export", range="all", kind=kind, format="json", page_size=1)
            self.assertEqual(status, 200)
            self.assertEqual(len(payload["items"]), expected)
            self.assertEqual({r["machine"] for r in payload["items"]}, {"alpha", "beta"})
        status, payload = self.get("/api/llm-calls-export", range="all", kind="attempts", machine="beta", project="one")
        self.assertEqual(status, 200)
        self.assertEqual(len(payload["items"]), 2)
        self.assertEqual({r["parent_request"]["canonical_project_id"] for r in payload["items"]}, {"one"})
        self.assertEqual(payload["applied_filters"]["request_dimensions"]["machine"], ["beta"])

    def test_standalone_uses_local_reader_without_admission(self):
        with mock.patch.object(server.hub, "enabled", return_value=False), \
             mock.patch.object(llm_attempts, "DEFAULT_LEDGER_PATH", self.ledgers[0]), \
             mock.patch.object(llm_attempts, "DEFAULT_REGISTRY_PATH", self.root / "missing-registry.json"):
            status, payload = self.get("/api/llm-call-request", project="one", logical_request_id="shared-id")
            self.assertEqual(status, 200)
            self.assertNotIn("machine", payload["request"])
            status, payload = self.get("/api/llm-calls-export", range="all", kind="requests")
            self.assertEqual(status, 200)
            self.assertEqual(len(payload["items"]), 54)


if __name__ == "__main__":
    unittest.main()
