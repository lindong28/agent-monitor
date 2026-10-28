"""Collection-local preparation preserves the shared calculator's results."""
import copy
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from unittest import mock

from parsers import UsageEntry
from agent_monitor.accounting import pricing_fetcher as pricing


class PreparedPricingTests(unittest.TestCase):
    def entry(self, **changes):
        return replace(UsageEntry(
            timestamp=datetime(2026, 4, 15, tzinfo=timezone.utc),
            session_id="s", message_id="m", request_id="r", model="model",
            input_tokens=10, output_tokens=2, cache_creation_tokens=3,
            cache_read_tokens=4, cost_usd=None, project="p", agent_id="claude-code",
        ), **changes)

    def table(self):
        rates = {
            "input_cost_per_token": 1e-6, "output_cost_per_token": 2e-6,
            "cache_read_input_token_cost": 0.1e-6,
            "cache_creation_input_token_cost": 1.25e-6,
            "cache_creation_input_token_cost_above_1hr": 2e-6,
            "input_cost_per_token_above_200k_tokens": 3e-6,
            "output_cost_per_token_above_200k_tokens": 4e-6,
            "source": "fixture", "verified_at": "2026-04-15",
            **{"unused_%d" % i: i for i in range(100)},
        }
        rates["service_modes"] = {"fast": {**rates, "input_cost_per_token": 5e-6}}
        rates["inference_geo_us"] = {"multiplier": 1.1, "source": "geo", "verified_at": "2026-04-15"}
        return pricing.PricingTable({"model": rates}, {"model": "catalog"})

    def test_cost_and_provenance_match_unprepared_table(self):
        table = self.table()
        original = copy.deepcopy(table)
        prepared = pricing.PreparedPricing(table)
        for tokens in (0, 10, 199993, 199994, 300000):
            for context in ({}, {"speed": "standard"}, {"speed": "fast"},
                            {"speed": "fast", "inference_geo": "us"},
                            {"inference_geo": "not_available"}, {"speed": "future"}):
                for hour in (0, 3, 4):
                    with self.subTest(tokens=tokens, context=context, hour=hour):
                        entry = self.entry(input_tokens=tokens, service_context=context,
                                           cache_creation_1h_tokens=hour)
                        self.assertEqual(pricing.with_calculated_cost(entry, table),
                                         pricing.with_calculated_cost(entry, prepared))
        self.assertEqual(table, original)
        self.assertAlmostEqual(pricing.calculate_cost(self.entry(), prepared), 18.15e-6)

    def test_unknown_recorded_incomplete_and_missing_rates_match(self):
        table = self.table()
        del table["model"]["cache_creation_input_token_cost"]
        table["model"]["input_cost_per_token_above_200k_tokens"] = float("nan")
        prepared = pricing.PreparedPricing(table)
        for entry in (self.entry(), self.entry(model="missing"),
                      self.entry(model="anthropic/model"), self.entry(cost_usd=12),
                      self.entry(usage_quality="incomplete"), self.entry(input_tokens=-1)):
            self.assertEqual(pricing.with_calculated_cost(entry, table),
                             pricing.with_calculated_cost(entry, prepared))

    def test_preparation_reuses_lookup_but_next_collection_sees_new_prices(self):
        table = self.table()
        prepared = pricing.PreparedPricing(table)
        with mock.patch.object(pricing, "resolve_model_key", wraps=pricing.resolve_model_key) as resolve:
            before = pricing.calculate_cost(self.entry(), prepared)
            pricing.calculate_cost(self.entry(input_tokens=11), prepared)
            self.assertEqual(resolve.call_count, 1)
        self.assertLess(len(prepared.price_rates(table["model"])), len(table["model"]))
        table["model"]["input_cost_per_token"] *= 2
        after = pricing.calculate_cost(self.entry(), pricing.PreparedPricing(table))
        self.assertAlmostEqual(after - before, 10e-6)


if __name__ == "__main__":
    unittest.main()
