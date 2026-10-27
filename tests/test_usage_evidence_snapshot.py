from agent_monitor.views import projections
"""Transport observed evidence without changing the legacy statistics contract."""

from dataclasses import asdict, replace
from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from parsers import UsageEntry
import statistics_snapshot as snapshots


class UsageEvidenceSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.entry = UsageEntry(datetime(2026, 10, 14, tzinfo=timezone.utc),
                                "session", "message", "request", "model",
                                10, 1, 0, 0, None, "project", "claude-code")

    def legacy(self, version):
        path = self.root / (str(version) + ".db")
        row = asdict(self.entry)
        row["timestamp"] = self.entry.timestamp.isoformat()
        for key in ("usage_quality", "service_context", "cost_provenance"):
            row.pop(key, None)
        meta = {"version": version, "observed_at": row["timestamp"],
                "history_authoritative_from": None, "scan_complete": True,
                "blocked_source_count": 0, "source_error_count": 0}
        if version == 1:
            meta["gateway"] = {}
        with closing(sqlite3.connect(path)) as conn, conn:
            tables = snapshots.LEGACY_TABLES if version == 1 else snapshots.V3_TABLES
            for name in tables:
                conn.execute("CREATE TABLE " + name + " (position INTEGER PRIMARY KEY, payload TEXT NOT NULL)")
            for table, payload in (("statistics_meta", meta), ("statistics_usage", row)):
                conn.execute("INSERT INTO " + table + " VALUES (0, ?)", (json.dumps(payload),))
        return path

    def test_old_versions_keep_unknown_evidence_without_rewriting(self):
        for version in (1, 2):
            with self.subTest(version=version):
                path = self.legacy(version)
                before = path.read_bytes()
                result = snapshots.read_snapshot(path)
                self.assertIsNone(result["usage_quality_counts"])
                self.assertEqual(result["usage_entries"][0].usage_quality, "unknown")
                self.assertIsNone(result["usage_entries"][0].cost_provenance)
                self.assertEqual(path.read_bytes(), before)
                self.assertIsNone(snapshots.read_metadata(path)["usage_quality_counts"])

    def test_current_version_transports_quality_and_counts_only_claude_observations(self):
        entries = [replace(self.entry, message_id=str(index), usage_quality=quality)
                   for index, quality in enumerate(("incomplete", "observed", "unknown"))]
        entries.append(replace(self.entry, agent_id="codex", message_id="codex"))
        path = self.root / "current.db"
        result = snapshots.write_snapshot(path, entries)
        self.assertEqual(result["version"], 5)
        self.assertEqual(result["usage_quality_counts"], {"incomplete": 1, "observed": 1, "unknown": 1})
        self.assertEqual(result["usage_entries"], entries)
        self.assertTrue(result["scan_complete"])

    def test_metadata_cannot_claim_a_different_observation_population(self):
        path = self.root / "tampered.db"
        snapshots.write_snapshot(path, [self.entry])
        with closing(sqlite3.connect(path)) as conn, conn:
            row = json.loads(conn.execute("SELECT payload FROM statistics_meta").fetchone()[0])
            row["usage_quality_counts"] = {"incomplete": 0, "observed": 1, "unknown": 0}
            conn.execute("UPDATE statistics_meta SET payload=?", (json.dumps(row),))
        with self.assertRaises(snapshots.StatisticsSnapshotError):
            snapshots.read_snapshot(path)

    def test_legacy_version_cannot_smuggle_new_fields(self):
        for version in (1, 2):
            path = self.legacy(version)
            with closing(sqlite3.connect(path)) as conn, conn:
                row = json.loads(conn.execute("SELECT payload FROM statistics_usage").fetchone()[0])
                row["usage_quality"] = "observed"
                conn.execute("UPDATE statistics_usage SET payload=?", (json.dumps(row),))
            with self.assertRaises(snapshots.StatisticsSnapshotError):
                snapshots.read_snapshot(path)

    def test_provenance_and_amount_must_agree(self):
        from parsers import validate_usage_metadata
        for basis, value in (("recorded", None), ("nominal", None), ("unpriced", 1)):
            with self.subTest(basis=basis):
                provenance = {"basis": basis, "sources": [], "verified_at": None,
                              "time_basis": "valuation_time" if basis == "nominal" else "unknown",
                              "reason": "unknown_model" if basis == "unpriced" else None}
                with self.assertRaises(ValueError):
                    validate_usage_metadata({"cost_usd": value, "cost_provenance": provenance})
                validate_usage_metadata({"cost_usd": None if basis == "unpriced" else 0,
                                         "cost_provenance": provenance})

    def test_api_preserves_evidence_including_legacy_unknown(self):
        import server
        for basis, value in (("recorded", 2), ("nominal", 1), ("unpriced", None), (None, 0)):
            provenance = None if basis is None else {
                "basis": basis, "sources": [], "verified_at": None,
                "time_basis": "valuation_time" if basis == "nominal" else "unknown",
                "reason": "usage_incomplete" if basis == "unpriced" else None}
            for quality in ("observed", "incomplete", "unknown"):
                entry = replace(self.entry, usage_quality=quality, cost_usd=value,
                                service_context={"speed": "fast"}, cost_provenance=provenance)
                result = projections.entry_json(entry)
                self.assertEqual(result["cost_usd"], value)
                self.assertEqual(result["usage_quality"], quality)
                self.assertEqual(result["service_context"], {"speed": "fast"})
                self.assertEqual(result["cost_provenance"], provenance)


if __name__ == "__main__":
    unittest.main()
