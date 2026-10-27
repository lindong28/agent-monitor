"""Pricing coverage: UX A4/A5 and the known-subtotal comparison contract."""
import unittest
from unittest.mock import patch

import rollup
import server


def row(cost, *, agent="codex", model="model-a", day="2026-10-14"):
    return dict(date=day, agent_id=agent, project="project-a", model=model,
                input_tokens=10, output_tokens=2, cache_read_tokens=3,
                cache_creation_tokens=1, message_count=1, entry_count=1,
                cost_usd=cost or 0, cost_known_count=int(cost is not None),
                _totals_source="project_independent_usage", _usage_schema=2)


class MetricProjectionTests(unittest.TestCase):
    def test_empty_unknown_zero_positive_and_mixed_are_distinct(self):
        for costs, expected, unknown in [([], None, 0), ([None], None, 1),
                                         ([0], 0, 0), ([2], 2, 0),
                                         ([2, None], 2, 1)]:
            with self.subTest(costs=costs), patch.object(rollup, "_daily_rows", return_value=[row(c) for c in costs]):
                summary = server.create_views().overview.summary(None)
                self.assertEqual(summary["cost_usd"], expected)
                self.assertEqual(summary["cost_coverage"], {"known": len(costs)-unknown, "unknown": unknown})
                self.assertIsNone(summary["by_agent"]["claude-code"])

    def test_group_coverage_follows_filters_and_periods(self):
        rows = [row(2), row(None), row(0, agent="claude-code"),
                row(None, day="2026-10-13", model="model-b")]
        with patch.object(rollup, "_daily_rows", return_value=rows):
            grouped = rollup.query_grouped(["day", "agent"], "cost", agents=["codex"])
            by_key = {tuple(r["key"]): r for r in grouped["rows"]}
            self.assertEqual(by_key[("2026-10-14", "codex")]["cost_coverage"], {"known": 1, "unknown": 1})
            self.assertEqual(grouped["cost_coverage"], {"known": 1, "unknown": 2})
            pivot = rollup.query_pivot("day", "agent", "cost", agents=["codex"])
            self.assertEqual(pivot["rows"][1]["cost_coverage"]["codex"], {"known": 1, "unknown": 1})
            self.assertIsNone(server.create_views().overview.daily_cost(pivot)[0]["codex_cost"])

    def test_dimension_reduction_conserves_cost_coverage(self):
        rows = [row(None if n % 2 else n, model="m%02d" % n) for n in range(20)]
        with patch.object(rollup, "_daily_rows", return_value=rows):
            pivot = rollup.query_pivot("model", "none", "cost")
        counts = [r["cost_coverage"]["value"] for r in pivot["rows"]]
        self.assertEqual(sum(c["known"] for c in counts), 10)
        self.assertEqual(sum(c["unknown"] for c in counts), 10)


if __name__ == "__main__":
    unittest.main()
