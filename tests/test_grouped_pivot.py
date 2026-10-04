"""SQL-style grouping against real isolated rollup databases."""
import contextlib
import json
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path
from datetime import datetime
from unittest import mock
from urllib.parse import parse_qs

import rollup
import server


class GroupedPivotTests(unittest.TestCase):
    def test_browser_ranking_aggregates_every_joint_group_and_preserves_unknowns(self):
        script = r'''
global.window = {};
global.AgentMonitor = {};
eval(require('fs').readFileSync('web/pivot.js', 'utf8'));
const rows = Array.from({length: 12}, (_, i) => [
  {key: ['2026-09-01', 'agent', 'project-' + i], value: i},
  {key: ['2026-09-02', 'agent', 'project-' + i], value: i}
]).flat();
rows.push({key: ['2026-09-01', 'unknown', 'p'], value: null});
rows.push({key: ['2026-09-01', 'partial', 'p'], value: null});
rows.push({key: ['2026-09-02', 'partial', 'p'], value: 1});
rows.push({key: ['2026-09-01', 'a|b', 'c'], value: 100});
rows.push({key: ['2026-09-01', 'a', 'b|c'], value: 101});
process.stdout.write(JSON.stringify(window.AgentMonitorPivot.rankingData({dimensions: ['day', 'agent', 'project'], rows})));
'''
        result = subprocess.run(["node", "-e", script], cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, check=True)
        ranked = json.loads(result.stdout)
        self.assertEqual(ranked["dimensions"], ["agent", "project"])
        self.assertEqual(len(ranked["rows"]), 16)
        self.assertEqual([row["value"] for row in ranked["rows"]], [101, 100, 22, 20, 18, 16, 14, 12, 10, 8, 6, 4, 2, 1, 0, None])
        groups = {tuple(row["key"]): row for row in ranked["rows"]}
        self.assertEqual(groups[("agent", "project-0")]["periods"], [
            {"label": "2026-09-01", "value": 0}, {"label": "2026-09-02", "value": 0}])
        self.assertEqual(groups[("partial", "p")]["periods"][0]["value"], None)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.generations = []
        for host, multiplier in (("mac-a", 1), ("mac-b", 10)):
            path = Path(self.temp.name) / (host + ".db")
            with contextlib.closing(sqlite3.connect(path)) as db, db:
                db.executescript(rollup.SCHEMA)
                db.execute("DROP TABLE daily_usage_rollup")
                db.execute("INSERT INTO rollup_meta VALUES ('bucket_timezone', 'Asia/Shanghai')")
                for day, agent, project, model, value, cost in (
                    ("2026-08-31", "claude", "a", "m1", 2, 1),
                    ("2026-09-01", "codex", "a", "m2", 3, None),
                    ("2026-09-08", "codex", "b", "m1", 5, 2),
                ):
                    db.execute("""INSERT INTO daily_rollup
                        (date, agent_id, project, model, input_tokens, output_tokens,
                         cache_read_tokens, cache_creation_tokens, cost_usd, cost_known_count,
                         entry_count, message_count) VALUES (?, ?, ?, ?, ?, 0, 0, 0, ?, ?, 1, 1)""",
                               (day, agent, project, model, value * multiplier,
                                (cost or 0) * multiplier, int(cost is not None)))
            self.generations.append(rollup.AdmittedGeneration(host, path))
        patcher = mock.patch("rollup.admitted_generations", return_value=tuple(self.generations))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_five_dimensions_preserve_joint_keys_and_machine_values(self):
        result = rollup.query_grouped(["day", "agent", "project", "model", "machine"], "total")
        values = {tuple(row["key"]): row["value"] for row in result["rows"]}
        self.assertEqual(len(values), 6)
        self.assertEqual(values[("2026-09-01", "codex", "a", "m2", "mac-b")], 30)
        self.assertEqual(sum(values.values()), 110)
        self.assertEqual(result["totals_provenance"]["basis"], "project_attributed")

    def test_all_filters_intersect_and_empty_selection_stays_empty(self):
        filters = dict(agents={"codex"}, projects={"b"}, models={"m1"}, machines={"mac-b"})
        self.assertEqual(rollup.query_grouped([], "total", **filters)["rows"], [{"key": [], "value": 50}])
        self.assertEqual(rollup.query_grouped([], "total", **dict(filters, models={"absent"}))["rows"], [])
        window = rollup.range_window("7d", datetime(2026, 9, 9).astimezone())
        self.assertEqual(rollup.query_grouped([], "total", time_range=window)["rows"], [{"key": [], "value": 55}])

    def test_zero_week_month_and_unknown_cost(self):
        self.assertEqual(rollup.query_grouped([], "total")["rows"], [{"key": [], "value": 110}])
        self.assertEqual(rollup.query_grouped(["week"], "total")["rows"],
                         [{"key": ["2026-08-31"], "value": 55}, {"key": ["2026-09-07"], "value": 55}])
        self.assertEqual(rollup.query_grouped(["month"], "total")["rows"],
                         [{"key": ["2026-08"], "value": 22}, {"key": ["2026-09"], "value": 88}])
        self.assertEqual(rollup.query_grouped(["model"], "cost", models={"m2"})["rows"],
                         [{"key": ["m2"], "value": None}])
        self.assertEqual(rollup.query_grouped([], "cost")["rows"], [{"key": [], "value": 33}])

    def test_project_independent_usage_is_used_until_project_is_requested(self):
        path = self.generations[0].db_path
        with contextlib.closing(sqlite3.connect(path)) as db, db:
            db.executescript(rollup.SCHEMA)
            db.execute("INSERT INTO rollup_meta VALUES ('usage_rollup_schema', '2')")
            db.execute("INSERT INTO rollup_meta VALUES ('usage_rollup_authoritative_from', '2026-08-31')")
            db.execute("""INSERT INTO daily_usage_rollup
                (date, agent_id, model, input_tokens, output_tokens, cache_read_tokens,
                 cache_creation_tokens, cost_usd, cost_known_count, entry_count, message_count)
                VALUES ('2026-09-01', 'codex', 'm2', 300, 0, 0, 0, 0, 0, 1, 1)""")
        self.assertEqual(rollup.query_grouped([], "total")["rows"][0]["value"], 407)
        self.assertEqual(sum(r["value"] for r in rollup.query_grouped(["project"], "total")["rows"]), 110)

    def test_invalid_groupings_are_rejected(self):
        for dims in (["none", "agent"], ["agent", "agent"], ["day", "week"], ["bogus"]):
            with self.subTest(dimensions=dims), self.assertRaises(ValueError):
                rollup.query_grouped(dims, "total")

    def test_custom_dates_include_both_ends_and_scope_filter_options(self):
        query = parse_qs("group_by=day&metric=total&range=custom&start=2026-08-31&end=2026-09-01&sync=0")
        self.assertEqual(server.pivot_endpoint(query)["rows"], [
            {"key": ["2026-08-31"], "value": 22}, {"key": ["2026-09-01"], "value": 33}])
        self.assertEqual(server.pivot_filters_endpoint(query)["project"], ["a"])
        query["start"] = ["2026-09-01"]
        self.assertEqual(server.pivot_endpoint(query)["rows"], [{"key": ["2026-09-01"], "value": 33}])
        query["end"] = ["2026-09-07"]
        self.assertEqual(server.pivot_endpoint(query)["rows"], [{"key": ["2026-09-01"], "value": 33}])

    def test_invalid_custom_dates_do_not_start_sync(self):
        for suffix in ("", "&start=2026-09-01", "&start=bad&end=2026-09-01",
                       "&start=2026-02-30&end=2026-09-01", "&start=2026-09-02&end=2026-09-01"):
            for endpoint in (server.pivot_endpoint, server.pivot_filters_endpoint):
                with self.subTest(suffix=suffix, endpoint=endpoint.__name__), mock.patch.object(server, "_maybe_sync_remotes") as sync:
                    with self.assertRaises(ValueError):
                        endpoint(parse_qs("range=custom" + suffix))
                    sync.assert_not_called()

    def test_endpoint_distinguishes_total_multigroup_and_legacy(self):
        for query, keys in (("group_by=none", []), ("group_by=day,agent,project", ["day", "agent", "project"])):
            with self.subTest(query=query):
                result = server.pivot_endpoint(parse_qs(query + "&metric=total&range=all&sync=0"))
                self.assertEqual(result["dimensions"], keys)
                self.assertEqual(sum(row["value"] for row in result["rows"]), 110)
        legacy = server.pivot_endpoint(parse_qs("x=agent&group=machine&metric=total&range=all&sync=0"))
        self.assertEqual(set(legacy["columns"]), {"mac-a", "mac-b"})
        self.assertEqual(sum(sum(row["values"].values()) for row in legacy["rows"]), 110)
