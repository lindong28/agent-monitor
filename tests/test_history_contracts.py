import json
import sqlite3
import tempfile
import unittest
from dataclasses import asdict, replace
from datetime import timezone
from pathlib import Path
from unittest import mock

import parsers
from parsers import claude
import pricing_fetcher as pricing
from usage_archive import UsageArchive


class HistoryContractTests(unittest.TestCase):
    def entry(self, stop="end_turn", **usage):
        data = {"type": "assistant", "timestamp": "2026-10-14T00:00:00Z",
                "sessionId": "session", "requestId": "request",
                "message": {"id": "message", "model": "claude-opus-4-8",
                            "usage": {"input_tokens": 1000000, **usage}}}
        if stop != "absent":
            data["message"]["stop_reason"] = stop
        return claude._parse_assistant_entry(data, "project")

    def test_quality_and_zero_incomplete_observations(self):
        for stop, quality in ((None, "incomplete"), ("end_turn", "observed"), ("absent", "unknown")):
            self.assertEqual(self.entry(stop).usage_quality, quality)
        self.assertIsNotNone(self.entry(None, input_tokens=0))
        self.assertIsNone(pricing.calculate_cost(self.entry(None), pricing._fallback_pricing()))

    def test_modes_have_independent_official_nominal_expectations(self):
        table = pricing._fallback_pricing()
        self.assertEqual(pricing.calculate_cost(self.entry(speed="standard"), table), 5)
        self.assertEqual(pricing.calculate_cost(self.entry(speed="fast"), table), 10)
        self.assertAlmostEqual(pricing.calculate_cost(self.entry(speed="fast", inference_geo="us"), table), 11)
        for context in ({"speed": "future"}, {"speed": None}, {"service_tier": "priority"}, {"speed": {"private": "not retained"}}):
            self.assertIsNone(pricing.calculate_cost(self.entry(**context), table))

    def test_equal_tokens_upgrade_independent_metadata(self):
        old = self.entry("absent")
        incomplete = parsers.complete_usage_observation(old, self.entry(None))
        self.assertEqual(incomplete.usage_quality, "incomplete")
        fast = parsers.complete_usage_observation(old, self.entry("absent", speed="fast"))
        self.assertEqual(fast.service_context, {"speed": "fast"})
        observed = self.entry(speed="standard")
        self.assertEqual(parsers.complete_usage_observation(observed, self.entry(None)).usage_quality, "observed")
        conflict = parsers.complete_usage_observation(observed, self.entry(speed="fast"))
        self.assertEqual(conflict.service_context["conflicts"], ["speed"])
        self.assertIsNone(pricing.calculate_cost(conflict, pricing._fallback_pricing()))

    def test_archive_equal_token_old_schema_reparse_and_warm(self):
        for stop, context, quality, expected in ((None, {}, "incomplete", None), ("absent", {"speed": "fast"}, "unknown", 10)):
            with self.subTest(stop=stop), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                source = root / "source.jsonl"
                source.write_text("fixture")
                archive = UsageArchive(root / "archive.sqlite3")
                archive.collect([source], lambda _: [self.entry("absent")], lambda _: "old", [], timezone.utc)
                with sqlite3.connect(archive.path) as conn:
                    for table, column in (("usage_events", "entry"), ("parsed_files", "entries")):
                        for rowid, raw in conn.execute("SELECT rowid, " + column + " FROM " + table):
                            value = json.loads(raw)
                            for row in value if isinstance(value, list) else [value]:
                                for key in ("usage_quality", "service_context", "cost_provenance"):
                                    row.pop(key, None)
                            conn.execute("UPDATE " + table + " SET " + column + "=? WHERE rowid=?", (json.dumps(value), rowid))
                parse = mock.Mock(return_value=[self.entry(stop, **context)])
                archive.collect([source], parse, lambda _: "new", [], timezone.utc)
                retained = archive.read()[0][0]
                self.assertEqual(retained.usage_quality, quality)
                self.assertEqual(pricing.with_calculated_cost(retained, pricing._fallback_pricing()).cost_usd, expected)
                archive.collect([source], parse, lambda _: "new", [], timezone.utc)
                self.assertEqual(parse.call_count, 1)

    def test_provenance_validation_and_recorded_override(self):
        priced = pricing.with_calculated_cost(self.entry(speed="fast"), pricing._fallback_pricing())
        self.assertEqual(priced.cost_provenance["basis"], "nominal")
        self.assertEqual(priced.cost_provenance["time_basis"], "valuation_time")
        parsers.validate_usage_metadata(asdict(priced))
        self.assertEqual(pricing.with_calculated_cost(priced, pricing._fallback_pricing()).cost_provenance, priced.cost_provenance)
        recorded = pricing.with_calculated_cost(replace(self.entry(None, speed="future"), cost_usd=12), {})
        self.assertEqual(recorded.cost_usd, 12)
        self.assertEqual(recorded.cost_provenance["basis"], "recorded")
        with self.assertRaises(ValueError):
            parsers.validate_usage_metadata({"service_context": {"speed": {}}})
        self.assertEqual(parsers.usage_quality_counts([self.entry(), self.entry(None), self.entry("absent")]),
                         {"observed": 1, "incomplete": 1, "unknown": 1})

    def test_fast_cache_and_output_rates(self):
        table = pricing._fallback_pricing()
        for usage, expected in (({"output_tokens": 1000000}, 50),
                                ({"cache_read_input_tokens": 1000000}, 1),
                                ({"cache_creation_input_tokens": 1000000}, 12.5),
                                ({"cache_creation_input_tokens": 1000000,
                                  "cache_creation": {"ephemeral_1h_input_tokens": 1000000}}, 20)):
            with self.subTest(usage=usage):
                self.assertEqual(pricing.calculate_cost(self.entry(input_tokens=0, speed="fast", **usage), table), expected)

    def test_catalog_source_and_mode_source_are_distinct(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "pricing.json"
            table = pricing.get_pricing(cache_path=path, fetcher=lambda: {
                "claude-opus-4-8": {"input_cost_per_token": 7e-6}}, now=lambda: 100)
            standard = pricing.with_calculated_cost(self.entry(), table)
            fast = pricing.with_calculated_cost(self.entry(speed="fast"), table)
            self.assertEqual(standard.cost_usd, 7)
            self.assertEqual(standard.cost_provenance["sources"], ["configured"])
            self.assertIsNone(standard.cost_provenance["verified_at"])
            self.assertEqual(fast.cost_usd, 10)
            self.assertEqual(fast.cost_provenance["sources"], ["https://platform.claude.com/docs/en/about-claude/pricing"])
            self.assertEqual(fast.cost_provenance["verified_at"], "2026-10-14")
            cached = pricing.get_pricing(cache_path=path, fetcher=lambda: self.fail("warm fetch"), now=lambda: 101)
            self.assertEqual(pricing.with_calculated_cost(self.entry(), cached).cost_provenance, standard.cost_provenance)

    def test_invalid_recorded_cost_is_not_silently_valued(self):
        for cost in (True, "1", float("nan"), -1):
            result = pricing.with_calculated_cost(replace(self.entry(), cost_usd=cost), {})
            self.assertIsNone(result.cost_usd)
            self.assertEqual(result.cost_provenance["reason"], "invalid_recorded_cost")
