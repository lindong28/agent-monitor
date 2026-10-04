"""Exercise snapshot compatibility with the independent Gateway's real writer."""

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

import gateway_dependency
from llm_gateway import ledger
import statistics_snapshot as snapshots


@unittest.skipUnless(hasattr(ledger, "V8_SCHEMA_SQL"), "requires Gateway schema 8 writer")
class GatewaySchema8Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "ledger.db"
        writer = ledger.Ledger(self.source, run_id="schema8-regression")
        self.constraints = [None, {"funding_scope": "personal"}]
        for index, constraint in enumerate(self.constraints):
            writer.admit_request(
                "fixture", str(index), None, "logical", ["text"], "revision",
                caller_username="fixture-user", caller_route_constraint=constraint,
            )
        self.snapshot = self.root / "snapshot.db"

    def write(self):
        return snapshots.write_snapshot(self.snapshot, [], ledger_path=self.source)

    def test_v8_round_trip_preserves_null_and_explicit_constraints(self):
        result = self.write()["gateway"]
        self.assertEqual(result["schema_version"], 8)
        actual = [json.loads(row["caller_route_constraint_json"])
                  if row["caller_route_constraint_json"] is not None else None
                  for row in result["requests"]]
        self.assertEqual(actual, self.constraints)
        self.assertTrue(all(row["caller_username"] == "fixture-user"
                            for row in result["requests"]))

    def test_v8_rejects_missing_and_unknown_request_fields(self):
        for mutation in ("missing", "unknown"):
            with self.subTest(mutation=mutation):
                self.write()
                with sqlite3.connect(self.snapshot) as conn:
                    row = json.loads(conn.execute(
                        "SELECT payload FROM statistics_requests WHERE position=0"
                    ).fetchone()[0])
                    if mutation == "missing":
                        del row["caller_route_constraint_json"]
                    else:
                        row["access_token"] = "synthetic"
                    conn.execute("UPDATE statistics_requests SET payload=? WHERE position=0",
                                 (json.dumps(row),))
                with self.assertRaises(snapshots.StatisticsSnapshotError):
                    snapshots.read_snapshot(self.snapshot)

    def test_v7_nonempty_archive_still_reads_without_migration(self):
        archive = self.root / "v7.db"
        with sqlite3.connect(self.source) as source, sqlite3.connect(archive) as target:
            target.executescript(ledger.V7_SCHEMA_SQL)
            columns = [row[1] for row in target.execute("PRAGMA table_info(logical_requests)")]
            names = ",".join(columns)
            rows = source.execute("SELECT " + names + " FROM logical_requests").fetchall()
            target.executemany("INSERT INTO logical_requests (" + names + ") VALUES ("
                               + ",".join("?" for _ in columns) + ")", rows)
        before = archive.read_bytes()
        result = snapshots.write_snapshot(self.snapshot, [], ledger_path=archive)["gateway"]
        self.assertEqual(result["schema_version"], 7)
        self.assertEqual(len(result["requests"]), 2)
        self.assertTrue(all("caller_route_constraint_json" not in row for row in result["requests"]))
        self.assertEqual(archive.read_bytes(), before)
