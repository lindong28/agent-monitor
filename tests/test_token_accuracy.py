import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import exporter
import generation
import rollup
from aggregators import LoadedEntries
from parsers import UsageEntry
from parsers import codex


class TokenAccuracyTests(unittest.TestCase):
    def test_rollup_cli_reports_total_usage_separately_from_project_blockers(self):
        launcher = Path(__file__).resolve().parents[1].joinpath("agent-monitor").read_text(
            encoding="utf-8"
        )

        self.assertIn("usage_would_skip", launcher)
        self.assertIn("agent/model totals still include this usage", launcher)
        self.assertIn("then rerun 'agent-monitor rollup --check'", launcher)
        self.assertIn("full-history totals basis", launcher)
        self.assertNotIn("but new usage was not written", launcher)

    def test_codex_uses_event_deltas_event_time_and_does_not_add_reasoning_twice(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "rollout-example.jsonl"
            rows = [
                {
                    "timestamp": "2026-08-29T15:00:00Z",
                    "type": "session_meta",
                    "payload": {
                        "id": "thread-1",
                        "timestamp": "2026-08-29T15:00:00Z",
                        "cwd": "/tmp/project",
                    },
                },
                self.token_event(
                    "2026-08-29T15:59:00Z",
                    total=(100, 20, 10, 4),
                    last=(100, 20, 10, 4),
                ),
                self.token_event(
                    "2026-08-29T15:59:30Z",
                    total=(100, 20, 10, 4),
                    last=(0, 0, 0, 0, 50),
                ),
                self.token_event(
                    "2026-08-29T16:01:00Z",
                    total=(160, 50, 20, 9),
                    last=(60, 30, 10, 5),
                ),
                self.token_event(
                    "2026-08-29T16:02:00Z",
                    total=(30, 10, 5, 2),
                    last=(30, 10, 5, 2),
                ),
            ]
            path.write_text(
                "\n".join(json.dumps(row) for row in rows) + "\n",
                encoding="utf-8",
            )

            entries = codex.parse_file(path)

        self.assertEqual(len(entries), 3)
        self.assertEqual(
            [entry.request_id for entry in entries],
            ["token-count:2", "token-count:4", "token-count:5"],
        )
        self.assertEqual(
            [entry.timestamp.isoformat() for entry in entries],
            [
                "2026-08-29T15:59:00+00:00",
                "2026-08-29T16:01:00+00:00",
                "2026-08-29T16:02:00+00:00",
            ],
        )
        self.assertEqual(
            [
                (
                    entry.input_tokens,
                    entry.cache_read_tokens,
                    entry.output_tokens,
                    entry.message_count,
                )
                for entry in entries
            ],
            [(80, 20, 10, 1), (30, 30, 10, 1), (20, 10, 5, 1)],
        )
        self.assertEqual(
            [
                entry.input_tokens + entry.cache_read_tokens + entry.output_tokens
                for entry in entries
            ],
            [110, 70, 35],
        )

    def test_codex_invalid_timestamp_does_not_advance_cumulative_baseline(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "rollout-invalid-timestamp.jsonl"
            rows = [
                {
                    "timestamp": "2026-08-29T15:00:00Z",
                    "type": "session_meta",
                    "payload": {
                        "id": "thread-1",
                        "timestamp": "2026-08-29T15:00:00Z",
                        "cwd": "/tmp/project",
                    },
                },
                self.token_event(
                    "2026-08-29T15:01:00Z",
                    total=(10, 0, 0, 0),
                    last=(10, 0, 0, 0),
                ),
                self.token_event(
                    "not-a-timestamp",
                    total=(20, 0, 0, 0),
                    last=(10, 0, 0, 0),
                ),
                self.token_event(
                    "2026-08-29T15:03:00Z",
                    total=(30, 0, 0, 0),
                    last=(10, 0, 0, 0),
                ),
            ]
            path.write_text(
                "\n".join(json.dumps(row) for row in rows) + "\n",
                encoding="utf-8",
            )

            entries = codex.parse_file(path)

        self.assertEqual([entry.input_tokens for entry in entries], [10, 20])
        self.assertEqual(sum(entry.input_tokens for entry in entries), 30)

    def test_project_blocker_keeps_usage_available_for_project_independent_rollup(self):
        stable = self.entry("stable", "repo", 10)
        blocked = self.entry("blocked", "/missing/repo", 100)
        loaded = LoadedEntries(
            [stable],
            blocked_sources=[{"source_path": "/missing/repo"}],
            unattributed_entries=[blocked],
        )

        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "rollup.db"
            now = datetime(2026, 8, 30, 12, tzinfo=timezone.utc)
            rollup.run(
                db_path=db_path,
                entries_loader=lambda: loaded,
                now=lambda: now,
            )

            with sqlite3.connect(db_path) as conn:
                project_total = conn.execute(
                    "SELECT SUM(input_tokens) FROM daily_rollup"
                ).fetchone()[0]
                usage_total = conn.execute(
                    "SELECT SUM(input_tokens) FROM daily_usage_rollup"
                ).fetchone()[0]
            agent_total = rollup.query_pivot(
                "day", "agent", "input", db_path=db_path
            )
            project_view = rollup.query_pivot(
                "project", "none", "input", db_path=db_path
            )

        self.assertEqual(project_total, 10)
        self.assertEqual(usage_total, 110)
        self.assertEqual(agent_total["rows"][0]["values"]["codex"], 110)
        self.assertEqual(
            agent_total["totals_provenance"],
            {
                "basis": "project_independent_usage",
                "legacy_fallback_bucket_count": 0,
                "bucket_dimensions": ["date", "agent", "model"],
            },
        )
        self.assertEqual(
            project_view["rows"],
            [{"x": "repo", "values": {"value": 10}}],
        )
        self.assertEqual(
            project_view["totals_provenance"],
            {
                "basis": "project_attributed",
                "legacy_fallback_bucket_count": 0,
                "bucket_dimensions": ["date", "agent", "project", "model"],
            },
        )

    def test_v1_migration_rebuilds_only_the_selected_28_day_window(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "rollup.db"
            self.create_v1_rollup(
                db_path,
                (("2026-07-01", 100), ("2026-08-30", 50)),
            )
            loaded = LoadedEntries(
                [
                    self.entry_at("july", "repo", 100, "2026-07-01"),
                    self.entry_at("august", "repo", 50, "2026-08-30"),
                ],
                scan_complete=True,
            )

            rollup.run(
                db_path=db_path,
                entries_loader=lambda: loaded,
                now=lambda: datetime(2026, 8, 30, 12, tzinfo=timezone.utc),
            )
            second_run = rollup.run(
                db_path=db_path,
                entries_loader=lambda: loaded,
                now=lambda: datetime(2026, 8, 30, 12, tzinfo=timezone.utc),
            )
            check = rollup._check_rollup(
                db_path,
                entries_loader=lambda: loaded,
                now=lambda: datetime(2026, 8, 30, 12, tzinfo=timezone.utc),
            )

            with sqlite3.connect(db_path) as conn:
                marker = conn.execute(
                    "SELECT value FROM rollup_meta WHERE key = 'usage_rollup_schema'"
                ).fetchone()[0]
                authority_floor = conn.execute(
                    "SELECT value FROM rollup_meta "
                    "WHERE key = 'usage_rollup_authoritative_from'"
                ).fetchone()[0]
                usage_rows = conn.execute(
                    "SELECT date, SUM(input_tokens) FROM daily_usage_rollup "
                    "GROUP BY date ORDER BY date"
                ).fetchall()
            pivot = rollup.query_pivot(
                "day", "agent", "input", db_path=db_path
            )

        self.assertEqual(marker, "2")
        self.assertEqual(authority_floor, "2026-08-03")
        self.assertEqual(usage_rows, [("2026-08-30", 50)])
        self.assertEqual(second_run["usage_dates_backfilled"], 0)
        self.assertEqual(check["usage_would_write"], {"count": 0, "items": []})
        self.assertEqual(
            pivot["rows"],
            [
                {"x": "2026-07-01", "values": {"codex": 100}},
                {"x": "2026-08-30", "values": {"codex": 50}},
            ],
        )
        self.assertEqual(
            pivot["totals_provenance"]["basis"],
            "usage_with_legacy_project_fallback",
        )

    def test_v1_migration_uses_explicit_legacy_fallback_for_unrebuildable_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "rollup.db"
            self.create_v1_rollup(
                db_path,
                (("2026-07-01", 100), ("2026-08-30", 50)),
            )
            loaded = LoadedEntries(
                [self.entry_at("august", "repo", 50, "2026-08-30")],
                scan_complete=True,
            )

            rollup.run(
                db_path=db_path,
                entries_loader=lambda: loaded,
                now=lambda: datetime(2026, 8, 30, 12, tzinfo=timezone.utc),
            )

            with sqlite3.connect(db_path) as conn:
                marker = conn.execute(
                    "SELECT value FROM rollup_meta WHERE key = 'usage_rollup_schema'"
                ).fetchone()[0]
                usage_rows = conn.execute(
                    "SELECT date, SUM(input_tokens) FROM daily_usage_rollup "
                    "GROUP BY date ORDER BY date"
                ).fetchall()
            pivot = rollup.query_pivot(
                "day", "agent", "input", db_path=db_path
            )
            recent_pivot = rollup.query_pivot(
                "day",
                "agent",
                "input",
                db_path=db_path,
                time_range=(
                    datetime(2026, 8, 30, 12, tzinfo=timezone.utc),
                    datetime(2026, 8, 30, 12, tzinfo=timezone.utc),
                ),
                calendar_days=1,
            )
            historical_pivot = rollup.query_pivot(
                "day",
                "agent",
                "input",
                db_path=db_path,
                time_range=(
                    datetime(2026, 7, 1, 12, tzinfo=timezone.utc),
                    datetime(2026, 7, 1, 12, tzinfo=timezone.utc),
                ),
                calendar_days=1,
            )
            stats = generation.snapshot_stats(db_path)

        self.assertEqual(marker, "2")
        self.assertEqual(usage_rows, [("2026-08-30", 50)])
        self.assertEqual(
            pivot["rows"],
            [
                {"x": "2026-07-01", "values": {"codex": 100}},
                {"x": "2026-08-30", "values": {"codex": 50}},
            ],
        )
        self.assertEqual(
            pivot["totals_provenance"],
            {
                "basis": "usage_with_legacy_project_fallback",
                "legacy_fallback_bucket_count": 1,
                "bucket_dimensions": ["date", "agent", "model"],
            },
        )
        self.assertEqual(
            recent_pivot["totals_provenance"],
            {
                "basis": "project_independent_usage",
                "legacy_fallback_bucket_count": 0,
                "bucket_dimensions": ["date", "agent", "model"],
            },
        )
        self.assertEqual(
            historical_pivot["totals_provenance"],
            {
                "basis": "legacy_project_derived",
                "legacy_fallback_bucket_count": 1,
                "bucket_dimensions": ["date", "agent", "model"],
            },
        )
        self.assertEqual(
            stats["metric_totals_basis"],
            "usage_with_legacy_project_fallback",
        )
        self.assertEqual(stats["legacy_fallback_bucket_count"], 1)
        self.assertEqual(
            stats["legacy_fallback_bucket_dimensions"],
            ["date", "agent", "model"],
        )
        self.assertEqual(stats["metric_totals"]["input"], 150)

    def test_v1_migration_uses_raw_logs_as_authority_inside_selected_window(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "rollup.db"
            self.create_v1_rollup(db_path, (("2026-08-30", 100),))
            with sqlite3.connect(db_path) as conn:
                conn.execute(
                    """
                    INSERT INTO daily_rollup
                      (date, agent_id, project, model, input_tokens, entry_count, message_count)
                    VALUES ('2026-08-30', 'codex', 'second-repo', 'gpt-5', 50, 1, 1)
                    """
                )
            loaded = LoadedEntries(
                [self.entry_at("first", "repo", 100, "2026-08-30")],
                scan_complete=True,
            )

            rollup.run(
                db_path=db_path,
                entries_loader=lambda: loaded,
                now=lambda: datetime(2026, 8, 30, 12, tzinfo=timezone.utc),
            )

            with sqlite3.connect(db_path) as conn:
                usage_rows = conn.execute(
                    "SELECT date, agent_id, model, input_tokens "
                    "FROM daily_usage_rollup"
                ).fetchall()
            pivot = rollup.query_pivot(
                "day", "agent", "input", db_path=db_path
            )
            stats = generation.snapshot_stats(db_path)

        self.assertEqual(
            usage_rows,
            [("2026-08-30", "codex", "gpt-5", 100)],
        )
        self.assertEqual(
            pivot["rows"],
            [{"x": "2026-08-30", "values": {"codex": 100}}],
        )
        self.assertEqual(
            pivot["totals_provenance"],
            {
                "basis": "project_independent_usage",
                "legacy_fallback_bucket_count": 0,
                "bucket_dimensions": ["date", "agent", "model"],
            },
        )
        self.assertEqual(stats["metric_totals"]["input"], 100)
        self.assertEqual(stats["legacy_fallback_bucket_count"], 0)

    def test_legacy_fallback_provenance_counts_distinct_usage_buckets(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "rollup.db"
            self.create_v1_rollup(db_path, (("2026-08-30", 100),))
            with sqlite3.connect(db_path) as conn:
                conn.execute(
                    """
                    INSERT INTO daily_rollup
                      (date, agent_id, project, model, input_tokens, entry_count, message_count)
                    VALUES ('2026-08-30', 'codex', 'second-repo', 'gpt-5', 50, 1, 1)
                    """
                )

            pivot = rollup.query_pivot(
                "day", "agent", "input", db_path=db_path
            )

        self.assertEqual(
            pivot["rows"],
            [{"x": "2026-08-30", "values": {"codex": 150}}],
        )
        self.assertEqual(
            pivot["totals_provenance"],
            {
                "basis": "legacy_project_derived",
                "legacy_fallback_bucket_count": 1,
                "bucket_dimensions": ["date", "agent", "model"],
            },
        )

    def test_generation_v2_totals_are_bound_to_project_independent_usage_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "snapshot.db"
            with sqlite3.connect(db_path) as conn:
                conn.executescript(rollup.SCHEMA)
                conn.execute(
                    "INSERT INTO rollup_meta (key, value) VALUES ('bucket_timezone', 'Asia/Shanghai')"
                )
                conn.execute(
                    "INSERT INTO rollup_meta (key, value) VALUES ('usage_rollup_schema', '2')"
                )
                conn.execute(
                    "INSERT INTO rollup_meta (key, value) VALUES (?, '2026-08-03')",
                    (rollup.USAGE_ROLLUP_AUTHORITY_FLOOR_KEY,),
                )
                conn.execute(
                    """
                    INSERT INTO daily_rollup
                      (date, agent_id, project, model, input_tokens)
                    VALUES ('2026-08-30', 'codex', 'repo', 'gpt-5', 10)
                    """
                )
                conn.execute(
                    """
                    INSERT INTO daily_usage_rollup
                      (date, agent_id, model, input_tokens)
                    VALUES ('2026-08-30', 'codex', 'gpt-5', 110)
                    """
                )

            stats = generation.snapshot_stats(db_path)
            meta = generation.build_generation_meta(
                db_path,
                machine_config_fingerprint="0" * 64,
                source_host_identity="host-v1:" + "1" * 64,
                aliases=[],
                rate_limits={},
                exporter_commit="a" * 40,
                generated_at="2026-08-30T12:00:00Z",
            )

        self.assertEqual(stats["metric_totals"]["input"], 110)
        self.assertEqual(meta["schema_version"], 2)
        self.assertEqual(meta["metric_totals_basis"], "project_independent_usage")
        self.assertEqual(meta["project_row_count"], 1)
        self.assertEqual(meta["usage_row_count"], 1)
        self.assertEqual(meta["row_count"], 2)
        self.assertEqual(meta["legacy_fallback_bucket_count"], 0)
        self.assertEqual(
            meta["legacy_fallback_bucket_dimensions"],
            ["date", "agent", "model"],
        )

    def test_integrity_check_reports_usage_rollup_shrink(self):
        now = datetime(2026, 8, 30, 12, tzinfo=timezone.utc)
        first = LoadedEntries(
            [self.entry("stable", "repo", 10)],
            unattributed_entries=[self.entry("blocked", "/missing/repo", 100)],
        )
        smaller = LoadedEntries(
            [self.entry("stable", "repo", 10)],
            unattributed_entries=[self.entry("blocked", "/missing/repo", 50)],
        )

        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "rollup.db"
            rollup.run(
                db_path=db_path,
                entries_loader=lambda: first,
                now=lambda: now,
            )
            result = rollup._check_rollup(
                db_path,
                entries_loader=lambda: smaller,
                now=lambda: now,
            )

        self.assertEqual(result["status"], "attention")
        self.assertEqual(result["usage_would_skip"]["count"], 1)
        self.assertEqual(
            result["usage_would_skip"]["items"][0]["old"]["input_tokens"],
            110,
        )
        self.assertEqual(
            result["usage_would_skip"]["items"][0]["new"]["input_tokens"],
            60,
        )

    def test_invalid_v2_check_preserves_the_observed_schema_marker(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "rollup.db"
            self.create_v1_rollup(db_path, ())
            with sqlite3.connect(db_path) as conn:
                conn.execute(
                    "INSERT INTO rollup_meta (key, value) "
                    "VALUES ('usage_rollup_schema', '2')"
                )
            with rollup.rollup_lock(db_path):
                pass

            result = rollup._check_rollup(db_path, list)

        self.assertEqual(result["status"], "indeterminate")
        self.assertEqual(result["db_state"], "usage_schema_invalid")
        self.assertEqual(result["usage_rollup_schema"], 2)
        self.assertEqual(result["db_span_scope"], "project_rollup")

    def test_unsupported_numeric_usage_schema_is_indeterminate(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "rollup.db"
            rollup.run(
                db_path=db_path,
                entries_loader=list,
                now=lambda: datetime(2026, 8, 30, 12, tzinfo=timezone.utc),
            )
            with sqlite3.connect(db_path) as conn:
                conn.execute(
                    "UPDATE rollup_meta SET value = '3' "
                    "WHERE key = 'usage_rollup_schema'"
                )
                conn.commit()
                conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")

            result = rollup._check_rollup(db_path, list)

        self.assertEqual(result["status"], "indeterminate")
        self.assertEqual(result["db_state"], "usage_schema_invalid")
        self.assertEqual(result["usage_rollup_schema"], 3)
        self.assertIn(
            "unsupported usage_rollup_schema",
            result["diagnostic_errors"][0]["error"],
        )

    def test_invalid_usage_authority_floor_is_indeterminate(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "rollup.db"
            rollup.run(
                db_path=db_path,
                entries_loader=list,
                now=lambda: datetime(2026, 8, 30, 12, tzinfo=timezone.utc),
            )
            with sqlite3.connect(db_path) as conn:
                conn.execute(
                    "INSERT INTO rollup_meta (key, value) VALUES (?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (rollup.USAGE_ROLLUP_AUTHORITY_FLOOR_KEY, "not-a-date"),
                )
                conn.commit()
                conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")

            result = rollup._check_rollup(db_path, list)

        self.assertEqual(result["status"], "indeterminate")
        self.assertEqual(result["db_state"], "usage_schema_invalid")
        self.assertIn(
            rollup.USAGE_ROLLUP_AUTHORITY_FLOOR_KEY,
            result["diagnostic_errors"][0]["error"],
        )

    def test_usage_row_before_authority_floor_is_indeterminate(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "rollup.db"
            rollup.run(
                db_path=db_path,
                entries_loader=list,
                now=lambda: datetime(2026, 8, 30, 12, tzinfo=timezone.utc),
            )
            with sqlite3.connect(db_path) as conn:
                conn.execute(
                    "INSERT INTO daily_usage_rollup "
                    "(date, agent_id, model, input_tokens) "
                    "VALUES ('2026-08-02', 'codex', 'gpt-5', 1)"
                )
                conn.commit()
                conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")

            result = rollup._check_rollup(db_path, list)

        self.assertEqual(result["status"], "indeterminate")
        self.assertEqual(result["db_state"], "usage_schema_invalid")
        self.assertIn(
            "precedes usage_rollup_authoritative_from",
            result["diagnostic_errors"][0]["error"],
        )

    def test_missing_usage_authority_floor_is_indeterminate(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "rollup.db"
            rollup.run(
                db_path=db_path,
                entries_loader=list,
                now=lambda: datetime(2026, 8, 30, 12, tzinfo=timezone.utc),
            )
            with sqlite3.connect(db_path) as conn:
                conn.execute(
                    "DELETE FROM rollup_meta WHERE key = ?",
                    (rollup.USAGE_ROLLUP_AUTHORITY_FLOOR_KEY,),
                )
                conn.commit()
                conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")

            result = rollup._check_rollup(db_path, list)

        self.assertEqual(result["status"], "indeterminate")
        self.assertEqual(result["db_state"], "usage_schema_invalid")
        self.assertIn(
            rollup.USAGE_ROLLUP_AUTHORITY_FLOOR_KEY,
            result["diagnostic_errors"][0]["error"],
        )

    def test_usage_authority_floor_without_schema_marker_blocks_migration(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "rollup.db"
            self.create_v1_rollup(db_path, (("2026-08-30", 100),))
            with sqlite3.connect(db_path) as conn:
                conn.execute(
                    "INSERT INTO rollup_meta (key, value) VALUES (?, ?)",
                    (rollup.USAGE_ROLLUP_AUTHORITY_FLOOR_KEY, "2026-08-03"),
                )

            with self.assertRaisesRegex(
                rollup.RollupUsageMigrationRequired,
                "exists without a usage_rollup_schema marker",
            ):
                rollup.run(
                    db_path=db_path,
                    entries_loader=list,
                    now=lambda: datetime(2026, 8, 30, 12, tzinfo=timezone.utc),
                )

    def test_snapshot_rejects_usage_table_without_schema_marker(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "snapshot.db"
            with sqlite3.connect(db_path) as conn:
                conn.executescript(rollup.SCHEMA)
                conn.execute(
                    "INSERT INTO rollup_meta (key, value) "
                    "VALUES ('bucket_timezone', 'Asia/Shanghai')"
                )

            with self.assertRaisesRegex(
                generation.GenerationValidationError,
                "without a usage_rollup_schema marker",
            ):
                generation.snapshot_stats(db_path)

    def test_export_v2_carries_project_independent_totals(self):
        now = datetime(2026, 8, 30, 12, tzinfo=timezone.utc)
        entries = LoadedEntries(
            [self.entry("stable", "repo", 10)],
            unattributed_entries=[self.entry("blocked", "/missing/repo", 100)],
        )
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "rollup.db"
            bundle = Path(tmp) / "bundle"
            rollup.run(
                db_path=db_path,
                entries_loader=lambda: entries,
                now=lambda: now,
            )
            with mock.patch.object(
                exporter,
                "exporter_version",
                return_value="a" * 40,
            ):
                manifest = exporter.export_bundle(
                    db_path=db_path,
                    output_path=bundle,
                    refresh=False,
                    source_host_identity="host-v1:" + "1" * 64,
                    generated_at="2026-08-30T12:00:00Z",
                    rate_limits={},
                )

            snapshot_stats = generation.snapshot_stats(bundle / "snapshot.db")

        self.assertEqual(manifest["schema_version"], 2)
        self.assertEqual(manifest["metric_totals"]["input"], 110)
        self.assertEqual(snapshot_stats["schema_version"], 2)
        self.assertEqual(snapshot_stats["metric_totals"]["input"], 110)

    def test_new_reader_unions_v1_legacy_and_v2_usage_generations(self):
        now = datetime(2026, 8, 30, 12, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as tmp:
            v1 = Path(tmp) / "v1.db"
            v2 = Path(tmp) / "v2.db"
            self.create_v1_rollup(v1, (("2026-08-30", 10),))
            entries = LoadedEntries(
                [self.entry("stable", "repo", 10)],
                unattributed_entries=[self.entry("blocked", "/missing/repo", 100)],
            )
            rollup.run(
                db_path=v2,
                entries_loader=lambda: entries,
                now=lambda: now,
            )
            generations = (
                rollup.AdmittedGeneration("legacy", v1),
                rollup.AdmittedGeneration("current", v2),
            )
            with mock.patch.object(
                rollup,
                "admitted_generations",
                return_value=generations,
            ):
                total_view = rollup.query_pivot("day", "agent", "input")
                project_view = rollup.query_pivot("project", "none", "input")

        self.assertEqual(total_view["rows"][0]["values"]["codex"], 120)
        self.assertEqual(
            total_view["totals_provenance"]["basis"],
            "usage_with_legacy_project_fallback",
        )
        self.assertEqual(
            total_view["totals_provenance"]["legacy_fallback_bucket_count"],
            1,
        )
        self.assertEqual(
            project_view["rows"],
            [{"x": "repo", "values": {"value": 20}}],
        )

    def test_empty_pivot_does_not_claim_a_totals_authority(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "rollup.db"
            rollup.run(
                db_path=db_path,
                entries_loader=list,
                now=lambda: datetime(2026, 8, 30, 12, tzinfo=timezone.utc),
            )

            pivot = rollup.query_pivot(
                "day", "agent", "input", db_path=db_path
            )

        self.assertEqual(pivot["rows"], [])
        self.assertEqual(
            pivot["totals_provenance"],
            {
                "basis": "no_data",
                "legacy_fallback_bucket_count": 0,
                "bucket_dimensions": ["date", "agent", "model"],
            },
        )

    @staticmethod
    def token_event(timestamp, total, last):
        total_input, total_cached, total_output, total_reasoning = total
        if len(last) == 4:
            last_input, last_cached, last_output, last_reasoning = last
            last_total = last_input + last_output
        else:
            last_input, last_cached, last_output, last_reasoning, last_total = last
        return {
            "timestamp": timestamp,
            "type": "event_msg",
            "payload": {
                "type": "token_count",
                "info": {
                    "total_token_usage": {
                        "input_tokens": total_input,
                        "cached_input_tokens": total_cached,
                        "output_tokens": total_output,
                        "reasoning_output_tokens": total_reasoning,
                        "total_tokens": total_input + total_output,
                    },
                    "last_token_usage": {
                        "input_tokens": last_input,
                        "cached_input_tokens": last_cached,
                        "output_tokens": last_output,
                        "reasoning_output_tokens": last_reasoning,
                        "total_tokens": last_total,
                    },
                },
            },
        }

    @staticmethod
    def entry(session_id, project, input_tokens):
        return UsageEntry(
            timestamp=datetime(2026, 8, 30, 10, tzinfo=timezone.utc),
            session_id=session_id,
            message_id=session_id,
            request_id="token-count:1",
            model="gpt-5",
            input_tokens=input_tokens,
            output_tokens=0,
            cache_creation_tokens=0,
            cache_read_tokens=0,
            cost_usd=1.0,
            project=project,
            agent_id="codex",
            message_count=1,
        )

    @staticmethod
    def entry_at(session_id, project, input_tokens, day):
        return UsageEntry(
            timestamp=datetime.fromisoformat(day + "T10:00:00+00:00"),
            session_id=session_id,
            message_id=session_id,
            request_id="token-count:1",
            model="gpt-5",
            input_tokens=input_tokens,
            output_tokens=0,
            cache_creation_tokens=0,
            cache_read_tokens=0,
            cost_usd=1.0,
            project=project,
            agent_id="codex",
            message_count=1,
        )

    @staticmethod
    def create_v1_rollup(db_path, rows):
        with sqlite3.connect(db_path) as conn:
            conn.executescript(
                """
                CREATE TABLE daily_rollup (
                  date TEXT NOT NULL,
                  agent_id TEXT NOT NULL,
                  project TEXT NOT NULL,
                  model TEXT NOT NULL,
                  input_tokens INTEGER NOT NULL DEFAULT 0,
                  output_tokens INTEGER NOT NULL DEFAULT 0,
                  cache_creation_tokens INTEGER NOT NULL DEFAULT 0,
                  cache_read_tokens INTEGER NOT NULL DEFAULT 0,
                  cost_usd REAL NOT NULL DEFAULT 0,
                  cost_known_count INTEGER NOT NULL DEFAULT 0,
                  entry_count INTEGER NOT NULL DEFAULT 0,
                  message_count INTEGER NOT NULL DEFAULT 0,
                  PRIMARY KEY (date, agent_id, project, model)
                );
                CREATE TABLE rollup_meta (key TEXT PRIMARY KEY, value TEXT);
                INSERT INTO rollup_meta (key, value)
                VALUES ('bucket_timezone', 'Asia/Shanghai');
                """
            )
            conn.executemany(
                """
                INSERT INTO daily_rollup
                  (date, agent_id, project, model, input_tokens, entry_count, message_count)
                VALUES (?, 'codex', 'repo', 'gpt-5', ?, 1, 1)
                """,
                rows,
            )


if __name__ == "__main__":
    unittest.main()
