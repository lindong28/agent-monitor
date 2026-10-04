"""Token-priced cost arithmetic shared by llm-gateway and tt-web.

This file is DUPLICATED VERBATIM in two repositories that must not depend on
each other:

    llm-gateway      src/llm_gateway/token_cost.py
    ai-agent-config  tt-web/token_cost.py

Keep the two copies byte-identical and bump SHARED_REVISION together; a diff
between them is a defect, not a local customisation. The module deliberately
imports nothing outside the standard library so that either copy runs wherever
its host runs.

Price tables use the LiteLLM `model_prices_and_context_window.json` shape: a
mapping of model key to a dict of per-token rates. Both repositories already
obtain that table (llm-gateway through the pinned `litellm` package, tt-web by
fetching and caching the same upstream JSON), so this module never fetches it.

Token conventions, stated once so callers normalise rather than guess:

    input_tokens            FRESH prompt tokens only — cached reads and cache
                            writes are NOT included. Providers that report a
                            prompt total INCLUDING cache must go through
                            counts_from_inclusive_prompt().
    output_tokens           total billable output.
    reasoning_tokens        the subset of output_tokens billed at a reasoning
                            rate when the catalog carries one; callers whose
                            provider reports reasoning OUTSIDE the output total
                            must add it into output_tokens first.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
import re
from typing import Any, Mapping, Optional, Tuple

SHARED_REVISION = "2026-09-21"

# Exactly `<base>_above_<threshold>_tokens`; anything with a further suffix is a
# service-tier variant (…_flex, …_priority) that this module does not select.
_THRESHOLD_KEY = re.compile(r"^input_cost_per_token_above_([0-9]+k?)_tokens$")

INPUT_RATE = "input_cost_per_token"
OUTPUT_RATE = "output_cost_per_token"
CACHE_READ_RATE = "cache_read_input_token_cost"
CACHE_WRITE_RATE = "cache_creation_input_token_cost"
CACHE_WRITE_1H_RATE = "cache_creation_input_token_cost_above_1hr"
REASONING_RATE = "output_cost_per_reasoning_token"

# Provider namespaces a caller may prepend to a model name. A key is tried
# verbatim first; only then is one of these stripped. `self_hosted` is absent on
# purpose — that namespace exists so a locally served model never picks up a
# commercial provider's price.
_STRIPPABLE_PREFIXES = ("openai/", "anthropic/", "azure/", "azure_ai/")


@dataclass(frozen=True)
class TokenCounts:
    """Normalised token counts. See the module docstring for the conventions."""

    input_tokens: int
    output_tokens: int
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    cache_creation_1h_tokens: int = 0
    reasoning_tokens: int = 0

    @property
    def billable_input_tokens(self) -> int:
        """Every input-side token, which is what tiered thresholds compare against."""
        return self.input_tokens + self.cache_read_tokens + self.cache_creation_tokens


@dataclass(frozen=True)
class ModelMatch:
    key: str
    kind: str  # "exact" | "prefix" | "alias"


@dataclass(frozen=True)
class PricedCost:
    value: float
    # Cache categories that carried tokens while the catalog carried no rate for
    # them. Priced at zero, because a missing cache rate most often means the
    # provider does not charge for that category at all (OpenAI bills no cache
    # write) — but the caller must be able to say so rather than present the
    # amount as fully priced.
    missing_rates: Tuple[str, ...] = ()
    # The tiered band applied, e.g. "above_200k_tokens"; None when base rates ran.
    tier: Optional[str] = None


def resolve_model(model: Optional[str], table: Mapping[str, Any], *,
                  allow_alias: bool = False) -> Optional[ModelMatch]:
    """Find a priceable catalog entry for `model`.

    Exact key, then the key with one provider namespace stripped, then — only
    when `allow_alias` — a substring match in either direction. Every candidate
    must be priceable: a catalog entry carrying no base rate is not a match, it
    is a miss. Without that rule a substring hit on a rate-less entry yields a
    confident $0.00, which reads as "this call was free" rather than "unknown".
    """
    if not model or not isinstance(table, Mapping):
        return None
    if _priceable(table.get(model)):
        return ModelMatch(model, "exact")
    for prefix in _STRIPPABLE_PREFIXES:
        if model.startswith(prefix):
            bare = model[len(prefix):]
            if _priceable(table.get(bare)):
                return ModelMatch(bare, "prefix")
            break
    if not allow_alias:
        return None
    for key in table:
        if (model in key or key in model) and _priceable(table.get(key)):
            return ModelMatch(key, "alias")
    lowered = model.lower()
    for key in table:
        low = key.lower()
        if (lowered in low or low in lowered) and _priceable(table.get(key)):
            return ModelMatch(key, "alias")
    return None


def counts_from_inclusive_prompt(prompt_tokens: int, completion_tokens: int, *,
                                 cache_read_tokens: int = 0,
                                 cache_creation_tokens: int = 0,
                                 cache_creation_1h_tokens: int = 0,
                                 reasoning_tokens: int = 0) -> Optional[TokenCounts]:
    """Build counts from a provider whose prompt total INCLUDES cache tokens.

    Returns None when the reported numbers cannot all be true at once — a prompt
    total smaller than the cache tokens it supposedly contains. Refusing is the
    point: subtracting anyway yields a negative fresh-token count, which prices
    as a silent discount on a real charge rather than as an error.
    """
    for value in (prompt_tokens, completion_tokens, cache_read_tokens,
                  cache_creation_tokens, cache_creation_1h_tokens, reasoning_tokens):
        if not _whole(value):
            return None
    cached = cache_read_tokens + cache_creation_tokens
    if prompt_tokens < cached:
        return None
    return TokenCounts(
        input_tokens=prompt_tokens - cached,
        output_tokens=completion_tokens,
        cache_read_tokens=cache_read_tokens,
        cache_creation_tokens=cache_creation_tokens,
        cache_creation_1h_tokens=min(cache_creation_1h_tokens, cache_creation_tokens),
        reasoning_tokens=min(reasoning_tokens, completion_tokens),
    )


def price(counts: TokenCounts, entry: Any) -> Optional[PricedCost]:
    """Price `counts` against one catalog entry, or None when it cannot be priced.

    A missing base rate for a category that actually carries tokens makes the
    whole amount unavailable; a missing cache rate does not, it is reported in
    `missing_rates` instead.
    """
    if not isinstance(entry, Mapping):
        return None
    if not all(_whole(getattr(counts, field)) for field in (
            "input_tokens", "output_tokens", "cache_read_tokens",
            "cache_creation_tokens", "cache_creation_1h_tokens", "reasoning_tokens")):
        return None
    tier = _tier(entry, counts.billable_input_tokens)

    input_rate = _rate(entry, INPUT_RATE, tier)
    output_rate = _rate(entry, OUTPUT_RATE, tier)
    if counts.input_tokens and input_rate is None:
        return None
    if counts.output_tokens and output_rate is None:
        return None

    missing = []
    cache_read_rate = _rate(entry, CACHE_READ_RATE, tier)
    if cache_read_rate is None:
        if counts.cache_read_tokens:
            missing.append("cache_read")
        cache_read_rate = 0.0
    cache_write_rate = _rate(entry, CACHE_WRITE_RATE, tier)
    if cache_write_rate is None:
        if counts.cache_creation_tokens:
            missing.append("cache_creation")
        cache_write_rate = 0.0
    # A 1-hour cache write is billed above the 5-minute rate. With no 1-hour
    # entry, fall back to the 5-minute rate rather than invent a multiplier;
    # providers without a 1-hour TTL report no 1-hour tokens anyway.
    cache_write_1h_rate = _rate(entry, CACHE_WRITE_1H_RATE, tier)
    if cache_write_1h_rate is None:
        cache_write_1h_rate = cache_write_rate

    hour = min(counts.cache_creation_1h_tokens, counts.cache_creation_tokens)
    value = counts.input_tokens * (input_rate or 0.0)
    value += counts.cache_read_tokens * cache_read_rate
    value += (counts.cache_creation_tokens - hour) * cache_write_rate
    value += hour * cache_write_1h_rate

    reasoning_rate = _rate(entry, REASONING_RATE, tier)
    reasoning = min(counts.reasoning_tokens, counts.output_tokens)
    if reasoning_rate is None or not reasoning:
        value += counts.output_tokens * (output_rate or 0.0)
    else:
        value += (counts.output_tokens - reasoning) * (output_rate or 0.0)
        value += reasoning * reasoning_rate

    if not math.isfinite(value) or value < 0:
        return None
    return PricedCost(value, tuple(missing), tier)


def _priceable(entry: Any) -> bool:
    return isinstance(entry, Mapping) and any(
        _number(entry.get(key)) is not None for key in (INPUT_RATE, OUTPUT_RATE)
    )


def _tier(entry: Mapping[str, Any], input_tokens: int) -> Optional[str]:
    """Highest `_above_<N>_tokens` band the input total actually exceeds."""
    best_threshold, best = 0, None
    for key in entry:
        match = _THRESHOLD_KEY.match(key) if isinstance(key, str) else None
        if not match or _number(entry.get(key)) is None:
            continue
        raw = match.group(1)
        threshold = int(raw[:-1]) * 1000 if raw.endswith("k") else int(raw)
        if input_tokens > threshold >= best_threshold:
            best_threshold, best = threshold, "above_%s_tokens" % raw
    return best


def _rate(entry: Mapping[str, Any], base_key: str, tier: Optional[str]) -> Optional[float]:
    if tier is not None:
        tiered = _number(entry.get("%s_%s" % (base_key, tier)))
        if tiered is not None:
            return tiered
    return _number(entry.get(base_key))


def _number(value: Any) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) and number >= 0 else None


def _whole(value: Any) -> bool:
    return not isinstance(value, bool) and isinstance(value, int) and value >= 0
