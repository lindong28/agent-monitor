"""Tests for the shared token_cost module.

This file is DUPLICATED VERBATIM alongside the module it tests:

    llm-gateway      tests/test_token_cost.py
    ai-agent-config  tt-web/tests/test_token_cost.py

Keep both copies byte-identical. The import shim below is what lets one file
serve two packagings; nothing else here may branch on the host repository.
"""

import ast
import sys
import unittest
from pathlib import Path

try:  # tt-web ships the module at the top level, beside its callers
    import token_cost
except ImportError:  # llm-gateway ships it inside its package
    from llm_gateway import token_cost


# The repository this test file belongs to, whose copy it must be exercising.
REPO = Path(__file__).resolve().parents[1]


ANTHROPIC_LIKE = {
    "input_cost_per_token": 3e-06,
    "output_cost_per_token": 15e-06,
    "cache_creation_input_token_cost": 3.75e-06,
    "cache_creation_input_token_cost_above_1hr": 6e-06,
    "cache_read_input_token_cost": 3e-07,
}

# Shaped like the catalog's gemini-2.5-pro: every input-side rate doubles past
# the 200k band, which is the regression a base-rate-only implementation causes.
TIERED = {
    "input_cost_per_token": 1.25e-06,
    "output_cost_per_token": 10e-06,
    "cache_read_input_token_cost": 1.25e-07,
    "input_cost_per_token_above_200k_tokens": 2.5e-06,
    "output_cost_per_token_above_200k_tokens": 15e-06,
    "cache_read_input_token_cost_above_200k_tokens": 2.5e-07,
    # A service-tier variant must never be mistaken for a plain threshold key.
    "input_cost_per_token_above_200k_tokens_priority": 4.5e-06,
}

# Shaped like the catalog's gpt-4o: a cache read rate, and no cache write rate
# because the provider does not bill cache writes at all.
NO_CACHE_WRITE_RATE = {
    "input_cost_per_token": 2.5e-06,
    "output_cost_per_token": 10e-06,
    "cache_read_input_token_cost": 1.25e-06,
}


class InclusivePromptTests(unittest.TestCase):
    """The shape where a provider's prompt total already contains its cache."""

    def test_cache_tokens_are_subtracted_exactly_once(self):
        counts = token_cost.counts_from_inclusive_prompt(
            1000, 300, cache_read_tokens=800)
        self.assertEqual(counts.input_tokens, 200)
        self.assertEqual(counts.cache_read_tokens, 800)
        self.assertEqual(counts.billable_input_tokens, 1000)

    def test_a_prompt_total_smaller_than_its_cache_is_refused(self):
        # Subtracting anyway yields a negative fresh-token count, which prices
        # as a silent discount on a real charge instead of as an error.
        self.assertIsNone(token_cost.counts_from_inclusive_prompt(
            200, 300, cache_read_tokens=800, cache_creation_tokens=500))

    def test_no_negative_component_can_reach_the_price(self):
        counts = token_cost.counts_from_inclusive_prompt(
            1500, 300, cache_read_tokens=800, cache_creation_tokens=500)
        priced = token_cost.price(counts, ANTHROPIC_LIKE)
        self.assertAlmostEqual(
            priced.value,
            200 * 3e-06 + 300 * 15e-06 + 500 * 3.75e-06 + 800 * 3e-07,
            places=12)

    def test_reported_counts_must_be_whole_and_non_negative(self):
        self.assertIsNone(token_cost.counts_from_inclusive_prompt(-1, 10))
        self.assertIsNone(token_cost.counts_from_inclusive_prompt(10, 10, cache_read_tokens=-1))
        self.assertIsNone(token_cost.counts_from_inclusive_prompt(10.5, 10))
        self.assertIsNone(token_cost.counts_from_inclusive_prompt(True, 10))

    def test_an_overstated_reasoning_subset_is_capped_at_the_output(self):
        counts = token_cost.counts_from_inclusive_prompt(100, 30, reasoning_tokens=500)
        self.assertEqual(counts.reasoning_tokens, 30)


class PriceTests(unittest.TestCase):
    def test_plain_addition_matches_a_hand_computed_amount(self):
        counts = token_cost.TokenCounts(
            input_tokens=200, output_tokens=300,
            cache_read_tokens=800, cache_creation_tokens=500)
        priced = token_cost.price(counts, ANTHROPIC_LIKE)
        self.assertAlmostEqual(
            priced.value,
            200 * 3e-06 + 300 * 15e-06 + 500 * 3.75e-06 + 800 * 3e-07,
            places=12)
        self.assertEqual(priced.missing_rates, ())
        self.assertIsNone(priced.tier)

    def test_a_missing_cache_write_rate_prices_at_zero_and_is_reported(self):
        counts = token_cost.TokenCounts(
            input_tokens=200, output_tokens=300,
            cache_read_tokens=800, cache_creation_tokens=500)
        priced = token_cost.price(counts, NO_CACHE_WRITE_RATE)
        self.assertAlmostEqual(
            priced.value, 200 * 2.5e-06 + 300 * 10e-06 + 800 * 1.25e-06, places=12)
        self.assertEqual(priced.missing_rates, ("cache_creation",))

    def test_a_missing_cache_rate_is_only_reported_when_tokens_used_it(self):
        counts = token_cost.TokenCounts(input_tokens=200, output_tokens=300)
        self.assertEqual(token_cost.price(counts, NO_CACHE_WRITE_RATE).missing_rates, ())

    def test_a_missing_base_rate_makes_the_whole_amount_unavailable(self):
        counts = token_cost.TokenCounts(input_tokens=200, output_tokens=300)
        self.assertIsNone(token_cost.price(counts, {"input_cost_per_token": 3e-06}))
        self.assertIsNone(token_cost.price(counts, {"output_cost_per_token": 15e-06}))

    def test_an_unused_base_rate_may_be_absent(self):
        counts = token_cost.TokenCounts(input_tokens=200, output_tokens=0)
        priced = token_cost.price(counts, {"input_cost_per_token": 3e-06})
        self.assertAlmostEqual(priced.value, 200 * 3e-06, places=12)

    def test_the_one_hour_cache_write_uses_the_higher_rate(self):
        counts = token_cost.TokenCounts(
            input_tokens=0, output_tokens=0,
            cache_creation_tokens=500, cache_creation_1h_tokens=200)
        priced = token_cost.price(counts, ANTHROPIC_LIKE)
        self.assertAlmostEqual(priced.value, 300 * 3.75e-06 + 200 * 6e-06, places=12)

    def test_the_one_hour_cache_write_falls_back_to_the_five_minute_rate(self):
        entry = {k: v for k, v in ANTHROPIC_LIKE.items()
                 if k != "cache_creation_input_token_cost_above_1hr"}
        split = token_cost.TokenCounts(
            input_tokens=0, output_tokens=0,
            cache_creation_tokens=500, cache_creation_1h_tokens=200)
        flat = token_cost.TokenCounts(
            input_tokens=0, output_tokens=0, cache_creation_tokens=500)
        self.assertEqual(token_cost.price(split, entry).value,
                         token_cost.price(flat, entry).value)

    def test_the_one_hour_split_cannot_exceed_the_cache_write_total(self):
        overstated = token_cost.TokenCounts(
            input_tokens=0, output_tokens=0,
            cache_creation_tokens=500, cache_creation_1h_tokens=900)
        capped = token_cost.TokenCounts(
            input_tokens=0, output_tokens=0,
            cache_creation_tokens=500, cache_creation_1h_tokens=500)
        self.assertEqual(token_cost.price(overstated, ANTHROPIC_LIKE).value,
                         token_cost.price(capped, ANTHROPIC_LIKE).value)

    def test_reasoning_is_billed_at_its_own_rate_when_the_catalog_has_one(self):
        entry = dict(ANTHROPIC_LIKE, output_cost_per_reasoning_token=30e-06)
        counts = token_cost.TokenCounts(
            input_tokens=0, output_tokens=300, reasoning_tokens=100)
        priced = token_cost.price(counts, entry)
        self.assertAlmostEqual(priced.value, 200 * 15e-06 + 100 * 30e-06, places=12)

    def test_reasoning_stays_inside_the_output_rate_without_a_dedicated_rate(self):
        counts = token_cost.TokenCounts(
            input_tokens=0, output_tokens=300, reasoning_tokens=100)
        self.assertAlmostEqual(
            token_cost.price(counts, ANTHROPIC_LIKE).value, 300 * 15e-06, places=12)

    def test_non_integer_counts_are_refused_rather_than_multiplied(self):
        self.assertIsNone(token_cost.price(
            token_cost.TokenCounts(input_tokens=None, output_tokens=1), ANTHROPIC_LIKE))
        self.assertIsNone(token_cost.price(
            token_cost.TokenCounts(input_tokens=-5, output_tokens=1), ANTHROPIC_LIKE))

    def test_a_non_mapping_entry_is_refused(self):
        counts = token_cost.TokenCounts(input_tokens=1, output_tokens=1)
        self.assertIsNone(token_cost.price(counts, None))


class TieredPricingTests(unittest.TestCase):
    def test_the_base_band_applies_at_or_below_the_threshold(self):
        counts = token_cost.TokenCounts(input_tokens=200_000, output_tokens=100)
        priced = token_cost.price(counts, TIERED)
        self.assertIsNone(priced.tier)
        self.assertAlmostEqual(
            priced.value, 200_000 * 1.25e-06 + 100 * 10e-06, places=9)

    def test_every_input_side_rate_switches_bands_together(self):
        counts = token_cost.TokenCounts(
            input_tokens=200_001, output_tokens=100, cache_read_tokens=1_000)
        priced = token_cost.price(counts, TIERED)
        self.assertEqual(priced.tier, "above_200k_tokens")
        self.assertAlmostEqual(
            priced.value,
            200_001 * 2.5e-06 + 100 * 15e-06 + 1_000 * 2.5e-07,
            places=9)

    def test_the_threshold_counts_cache_tokens_as_input(self):
        # 150k fresh + 100k cached is a 250k prompt; the band must follow the
        # whole input side, not just the part that was not served from cache.
        counts = token_cost.TokenCounts(
            input_tokens=150_000, output_tokens=1, cache_read_tokens=100_000)
        self.assertEqual(token_cost.price(counts, TIERED).tier, "above_200k_tokens")

    def test_a_service_tier_key_is_not_read_as_a_threshold(self):
        only_tier_variant = {
            "input_cost_per_token": 1.25e-06,
            "output_cost_per_token": 10e-06,
            "input_cost_per_token_above_200k_tokens_priority": 4.5e-06,
        }
        counts = token_cost.TokenCounts(input_tokens=300_000, output_tokens=1)
        priced = token_cost.price(counts, only_tier_variant)
        self.assertIsNone(priced.tier)
        self.assertAlmostEqual(priced.value, 300_000 * 1.25e-06 + 1 * 10e-06, places=9)

    def test_the_highest_exceeded_band_wins(self):
        entry = dict(TIERED, input_cost_per_token_above_128k_tokens=2e-06)
        low = token_cost.TokenCounts(input_tokens=130_000, output_tokens=0)
        high = token_cost.TokenCounts(input_tokens=300_000, output_tokens=0)
        self.assertEqual(token_cost.price(entry=entry, counts=low).tier, "above_128k_tokens")
        self.assertEqual(token_cost.price(entry=entry, counts=high).tier, "above_200k_tokens")


class ResolveModelTests(unittest.TestCase):
    TABLE = {
        "claude-sonnet-4-6": ANTHROPIC_LIKE,
        "gpt-4o": NO_CACHE_WRITE_RATE,
        "dashscope/qwen3-max-2026-01-23": {},  # a real catalog shape: no rates
    }

    def test_an_exact_key_wins(self):
        match = token_cost.resolve_model("gpt-4o", self.TABLE)
        self.assertEqual((match.key, match.kind), ("gpt-4o", "exact"))

    def test_one_provider_namespace_is_stripped(self):
        match = token_cost.resolve_model("anthropic/claude-sonnet-4-6", self.TABLE)
        self.assertEqual((match.key, match.kind), ("claude-sonnet-4-6", "prefix"))

    def test_the_self_hosted_namespace_is_never_stripped(self):
        # That namespace exists so a locally served model cannot pick up a
        # commercial provider's price.
        self.assertIsNone(token_cost.resolve_model(
            "self_hosted/gpt-4o", self.TABLE, allow_alias=False))

    def test_aliasing_is_off_unless_asked_for(self):
        self.assertIsNone(token_cost.resolve_model("gpt-4o-2026-01-01", self.TABLE))

    def test_an_alias_matches_in_either_direction_when_enabled(self):
        match = token_cost.resolve_model("gpt-4o-2026-01-01", self.TABLE, allow_alias=True)
        self.assertEqual((match.key, match.kind), ("gpt-4o", "alias"))

    def test_an_entry_without_any_rate_is_a_miss_not_a_zero(self):
        # The failure this rule exists for: an alias landing on a rate-less
        # entry priced the call at exactly $0.00, which reads as "free" rather
        # than "unknown" and is then summed into a total.
        self.assertIsNone(token_cost.resolve_model(
            "qwen3-max-2026", self.TABLE, allow_alias=True))

    def test_an_absent_model_is_a_miss(self):
        self.assertIsNone(token_cost.resolve_model("my-local-llama", self.TABLE,
                                                   allow_alias=True))
        self.assertIsNone(token_cost.resolve_model(None, self.TABLE, allow_alias=True))


class SharedCopyTests(unittest.TestCase):
    def test_the_copy_under_test_is_this_repository_s_own(self):
        """Guard against exercising the other repository's copy.

        Both repositories end up with `llm_gateway` importable in some runs —
        tt-web's audit tests put it on `sys.path` — so an import shim alone can
        silently bind to the neighbour's file and let the two copies drift
        while every test stays green. Observed: a four-line edit made after the
        copy was taken went unnoticed by a full tt-web suite run.
        """
        loaded = Path(token_cost.__file__).resolve()
        self.assertTrue(loaded.is_relative_to(REPO),
                        "loaded %s, which is outside %s" % (loaded, REPO))

    def test_the_module_imports_only_the_standard_library(self):
        """Both copies must run wherever their host runs, with no shared dependency."""
        source = Path(token_cost.__file__).read_text(encoding="utf-8")
        imported = set()
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                imported.add(node.module.split(".")[0])
        self.assertTrue(imported <= set(sys.stdlib_module_names),
                        "non-stdlib imports: %s" % sorted(imported - set(sys.stdlib_module_names)))

    def test_the_copies_carry_a_shared_revision_marker(self):
        self.assertRegex(token_cost.SHARED_REVISION, r"^\d{4}-\d{2}-\d{2}$")


if __name__ == "__main__":
    unittest.main()
