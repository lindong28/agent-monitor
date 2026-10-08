"""Round-trip supported Gateway schemas through the monitor snapshot boundary."""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

import gateway_dependency
from llm_gateway import ledger
import statistics_snapshot as snapshots


class GatewayCurrentSchemaTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.fixture = ledger.Ledger(self.root / "current.db", run_id="snapshot-test")
        request = self.fixture.admit_request("fixture", "request", None, "logical", ["text"], "revision")
        credential = {"provider_id": "fixture", "credential_profile_id": "company_fixture",
                      "auth_type": "api_key_env", "credential_source_kind": "env_assignment_name",
                      "credential_source_ref": "FIXTURE_KEY", "funding_source": "company_paid"}
        route = {"route_id": "company_fixture/native/stream", "actual_model": "openai/native",
                 "provider_id": "fixture", "credential_profile_id": "company_fixture",
                 "transport": "fixture", "credential_source": credential}
        attempt = self.fixture.reserve_attempt(request["id"], route, credential, "revision")
        self.fixture.mark_dispatch_boundary(attempt["attempt_id"])
        self.fixture.finish_attempt(attempt["attempt_id"], "success")

    def source(self, version):
        path = self.root / f"v{version}.db"
        with sqlite3.connect(path) as target, sqlite3.connect(self.fixture.path) as source:
            target.executescript(getattr(ledger, f"V{version}_SCHEMA_SQL"))
            for table in ("logical_requests", "attempts"):
                columns = [r[1] for r in target.execute(f"PRAGMA table_info({table})")]
                names = ",".join(columns)
                target.executemany(f"INSERT INTO {table} ({names}) VALUES ({','.join('?' for _ in columns)})",
                                   source.execute(f"SELECT {names} FROM {table}"))
        return path

    def test_v8_v9_v10_round_trip_and_source_unchanged(self):
        for version in (8, 9, 10):
            with self.subTest(version=version):
                path = self.source(version)
                before = path.read_bytes()
                result = snapshots.write_snapshot(self.root / f"snapshot-{version}.db", [], ledger_path=path)
                self.assertEqual(result["gateway"]["schema_version"], version)
                self.assertEqual(len(result["gateway"]["requests"]), 1)
                self.assertEqual(len(result["gateway"]["attempts"]), 1)
                self.assertEqual("usage_missing_reason" in result["gateway"]["attempts"][0], version >= 10)
                self.assertEqual(path.read_bytes(), before)

    def test_v10_rejects_unknown_and_missing_fields(self):
        source = self.source(10)
        for table, field, remove in (("requests", "access_token", False),
                                     ("requests", "caller_route_constraint_json", True),
                                     ("attempts", "access_token", False),
                                     ("attempts", "usage_missing_reason", True)):
            with self.subTest(table=table, field=field):
                path = self.root / "mutated.db"
                snapshots.write_snapshot(path, [], ledger_path=source)
                with sqlite3.connect(path) as conn:
                    row = json.loads(conn.execute(f"SELECT payload FROM statistics_{table} LIMIT 1").fetchone()[0])
                    if remove:
                        del row[field]
                    else:
                        row[field] = "synthetic"
                    conn.execute(f"UPDATE statistics_{table} SET payload=?", (json.dumps(row),))
                with self.assertRaises(snapshots.StatisticsSnapshotError):
                    snapshots.read_snapshot(path)

    def test_future_schema_is_not_implicitly_accepted(self):
        path = self.root / "future.db"
        snapshots.write_snapshot(path, [], ledger_path=self.source(8))
        with sqlite3.connect(path) as conn:
            meta = json.loads(conn.execute("SELECT payload FROM statistics_meta").fetchone()[0])
            meta["gateway"]["schema_version"] = 11
            conn.execute("UPDATE statistics_meta SET payload=?", (json.dumps(meta),))
        with self.assertRaises(snapshots.StatisticsSnapshotError):
            snapshots.read_snapshot(path)
