"""Statistics readers accept the shared writer and retain archived schemas."""

from contextlib import closing
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
import gateway_dependency

from llm_gateway.ledger import Ledger, V4_SCHEMA_SQL, V5_SCHEMA_SQL, V6_SCHEMA_SQL
import llm_attempts
import statistics_snapshot


class GatewayV5ReaderTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.path = self.root / "ledger.sqlite3"
        self.ledger = Ledger(self.path, run_id="reader-contract")

    def admit(self, mode="stream", pin=False):
        return self.ledger.admit_request(
            "company-project", "request-" + mode, None, "logical", ["text"], "revision",
            requested_mode=mode, requested_route_id="legacy-pin" if pin else None,
            caller_username="fixture-user",
            resolved_route_id="company_fixture/native/stream" if pin else None,
            route_selection_source="explicit_pin" if pin else "policy",
        )

    def test_modes_survive_snapshot_round_trip_and_existing_projection(self):
        for mode in ("stream", "batch"):
            self.admit(mode)
        snapshot = self.root / "snapshot.sqlite3"
        statistics_snapshot.write_snapshot(snapshot, [], ledger_path=self.path)
        result = statistics_snapshot.read_snapshot(snapshot)
        self.assertEqual(result["gateway"]["schema_version"], 7)
        self.assertTrue(all(r["caller_username"] == "fixture-user" for r in result["gateway"]["requests"]))
        self.assertEqual({r["requested_mode"] for r in result["gateway"]["requests"]}, {"stream", "batch"})
        payload = llm_attempts.llm_calls({"range": ["all"]}, db_path=self.path)
        self.assertEqual(payload["requests"]["matching_count"], 2)
        self.assertEqual(payload["projection_version"], 4)

    def test_legacy_pin_validates_against_frozen_resolved_target(self):
        request = self.admit(pin=True)
        credential = {"provider_id": "fixture", "credential_profile_id": "company_fixture",
                      "auth_type": "api_key_env", "credential_source_kind": "env_assignment_name",
                      "credential_source_ref": "FIXTURE_KEY", "funding_source": "company_paid"}
        route = {"route_id": "company_fixture/native/stream", "actual_model": "openai/native",
                 "provider_id": "fixture", "credential_profile_id": "company_fixture",
                 "transport": "fixture", "credential_source": credential}
        attempt = self.ledger.reserve_attempt(request["id"], route, credential, "revision")
        self.ledger.mark_dispatch_boundary(attempt["attempt_id"])
        self.ledger.finish_attempt(attempt["attempt_id"], "success")
        payload = llm_attempts.llm_calls({"range": ["all"]}, db_path=self.path)
        self.assertEqual(payload["attempts"]["matching_count"], 1)
        snapshot = self.root / "attempts.sqlite3"
        statistics_snapshot.write_snapshot(snapshot, [], ledger_path=self.path)
        data = statistics_snapshot.read_snapshot(snapshot)
        self.assertEqual(data["gateway"]["schema_version"], 7)
        self.assertEqual(data["gateway"]["attempts"][0]["attempt_id"], attempt["attempt_id"])
        self.assertEqual(data["gateway"]["attempts"][0]["outcome"], "success")
        self.assertEqual(data["gateway"]["requests"][0]["caller_username"], "fixture-user")
        with closing(sqlite3.connect(snapshot)) as connection:
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertEqual(tables, statistics_snapshot.TABLES)
        self.assertEqual(set(data["gateway"]["attempts"][0]), statistics_snapshot.ATTEMPT_FIELDS)
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute("UPDATE logical_requests SET resolved_route_id='wrong-target'")
        with self.assertRaises(llm_attempts.ProjectionError):
            llm_attempts.llm_calls({"range": ["all"]}, db_path=self.path)

    def test_session_uuid_versions_match_gateway_writer(self):
        sessions = [
            f"{harness}:019a1234-5678-{version}abc-8def-0123456789ab"
            for harness in ("claude", "codex") for version in (4, 7)
        ]
        for session in sessions:
            self.ledger.admit_request(
                "company-project", session, session, "logical", ["text"], "revision"
            )
        snapshot = self.root / "sessions.sqlite3"
        statistics_snapshot.write_snapshot(snapshot, [], ledger_path=self.path)
        rows = statistics_snapshot.read_snapshot(snapshot)["gateway"]["requests"]
        self.assertEqual({row["session_ref"] for row in rows}, set(sessions))
        payload = llm_attempts.llm_calls({"range": ["all"]}, db_path=self.path)
        self.assertEqual(payload["requests"]["matching_count"], len(sessions))
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute("UPDATE logical_requests SET session_ref='codex:invalid'")
        with self.assertRaises(llm_attempts.ProjectionError):
            llm_attempts.llm_calls({"range": ["all"]}, db_path=self.path)
        with self.assertRaises(ValueError):
            statistics_snapshot.write_snapshot(snapshot, [], ledger_path=self.path)

    def test_invalid_mode_is_rejected_by_both_readers(self):
        self.admit()
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute("UPDATE logical_requests SET requested_mode='urgent-ish'")
        with self.assertRaises(llm_attempts.ProjectionError):
            llm_attempts.llm_calls({"range": ["all"]}, db_path=self.path)
        with self.assertRaises((ValueError, llm_attempts.ProjectionError)):
            statistics_snapshot.write_snapshot(self.root / "bad.sqlite3", [], ledger_path=self.path)

    def test_v4_archive_remains_readable_without_migration(self):
        path = self.root / "v4.sqlite3"
        with closing(sqlite3.connect(path)) as connection, connection:
            connection.executescript(V4_SCHEMA_SQL)
        before = path.read_bytes()
        payload = llm_attempts.llm_calls({"range": ["all"]}, db_path=path)
        self.assertEqual(payload["ledger"]["schema_version"], 4)
        snapshot = self.root / "v4-snapshot.sqlite3"
        statistics_snapshot.write_snapshot(snapshot, [], ledger_path=path)
        self.assertEqual(statistics_snapshot.read_snapshot(snapshot)["gateway"]["schema_version"], 4)
        self.assertEqual(path.read_bytes(), before)

    def test_v4_v5_v6_nonempty_snapshots_remain_readable_without_migration(self):
        request = self.admit()
        credential = {"provider_id": "fixture", "credential_profile_id": "company_fixture",
                      "auth_type": "api_key_env", "credential_source_kind": "env_assignment_name",
                      "credential_source_ref": "FIXTURE_KEY", "funding_source": "company_paid"}
        route = {"route_id": "company_fixture/native/stream", "actual_model": "openai/native",
                 "provider_id": "fixture", "credential_profile_id": "company_fixture",
                 "transport": "fixture", "credential_source": credential}
        attempt = self.ledger.reserve_attempt(request["id"], route, credential, "revision")
        self.ledger.mark_dispatch_boundary(attempt["attempt_id"])
        self.ledger.finish_attempt(attempt["attempt_id"], "success")
        with closing(sqlite3.connect(self.path)) as current:
            current.row_factory = sqlite3.Row
            row = dict(current.execute("SELECT * FROM logical_requests").fetchone())
            attempt_row = dict(current.execute("SELECT * FROM attempts").fetchone())
        for version, schema in ((4, V4_SCHEMA_SQL), (5, V5_SCHEMA_SQL), (6, V6_SCHEMA_SQL)):
            with self.subTest(schema_version=version):
                path = self.root / ("legacy-%s.sqlite3" % version)
                with closing(sqlite3.connect(path)) as connection, connection:
                    connection.executescript(schema)
                    for table, source_row in (("logical_requests", row), ("attempts", attempt_row)):
                        columns = [r[1] for r in connection.execute("PRAGMA table_info(%s)" % table)]
                        connection.execute("INSERT INTO %s (%s) VALUES (%s)" % (
                            table, ",".join(columns), ",".join("?" for _ in columns)),
                            [source_row[column] for column in columns])
                before = path.read_bytes()
                snapshot = self.root / ("legacy-%s-snapshot.sqlite3" % version)
                statistics_snapshot.write_snapshot(snapshot, [], ledger_path=path)
                result = statistics_snapshot.read_snapshot(snapshot)["gateway"]
                self.assertEqual(result["schema_version"], version)
                self.assertEqual(result["requests"][0]["logical_request_id"], row["logical_request_id"])
                self.assertEqual(result["attempts"][0], attempt_row)
                self.assertEqual("caller_username" in result["requests"][0], version == 6)
                self.assertEqual("requested_mode" in result["requests"][0], version >= 5)
                self.assertEqual(path.read_bytes(), before)

    def test_v7_snapshot_rejects_resource_token_and_url_fields(self):
        self.admit()
        snapshot = self.root / "unsafe.sqlite3"
        for field in ("resource_id", "access_token", "url"):
            with self.subTest(field=field):
                statistics_snapshot.write_snapshot(snapshot, [], ledger_path=self.path)
                import json
                with closing(sqlite3.connect(snapshot)) as connection, connection:
                    row = json.loads(connection.execute("SELECT payload FROM statistics_requests").fetchone()[0])
                    row[field] = "synthetic-not-for-statistics"
                    connection.execute("UPDATE statistics_requests SET payload=?", (json.dumps(row),))
                with self.assertRaises(statistics_snapshot.StatisticsSnapshotError):
                    statistics_snapshot.read_snapshot(snapshot)


if __name__ == "__main__":
    unittest.main()
