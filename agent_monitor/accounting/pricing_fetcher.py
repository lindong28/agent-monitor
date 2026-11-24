import json
import logging
import math
import ssl
import tempfile
import time
import urllib.request
from agent_monitor.network import network_policy
from dataclasses import replace
from pathlib import Path

import token_cost


# Adapted from token-tracker src/analyzer/cost.py; this version wraps the
# cache with fetched_at and enforces a 7-day TTL.
LITELLM_URL = "https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json"
TTL_SECONDS = 7 * 24 * 3600
MISSING_MODEL_RECHECK_SECONDS = 3600
from agent_monitor.paths import PROJECT_ROOT

ROOT = PROJECT_ROOT
CACHE_PATH = ROOT / "state" / "pricing_cache.json"
FALLBACK_PATH = Path(__file__).with_name("pricing.json")
ESTIMATED_PRICING_MODELS = {"glm-5.1", "glm-5.2"}

logger = logging.getLogger(__name__)
_PARTIAL_LOGGED = set()
_UNKNOWN_LOGGED = set()


def get_pricing(cache_path=CACHE_PATH, fetcher=None, now=None, persist=True, *, required_models=()):
    now_fn = now or time.time
    cache_path = Path(cache_path)
    timestamp = now_fn()
    cached = _read_fresh_cache(cache_path, timestamp)
    if cached is not None:
        table, fetched_at, checked_at = cached
        merged = _with_bundled_supplements(table)
        if (not persist or timestamp - checked_at < MISSING_MODEL_RECHECK_SECONDS
                or all(resolve_model_key(model, merged) is not None
                       for model in required_models if model and model != "unknown")):
            return merged

    fetch = fetcher or _fetch_litellm_pricing
    try:
        data = fetch()
        if not isinstance(data, dict) or not data:
            raise ValueError("empty or invalid pricing catalog")
        data = PricingTable(data, {key: LITELLM_URL if fetcher is None else "configured" for key in data})
        if persist:
            _write_cache(cache_path, data, now_fn())
        return _with_bundled_supplements(data)
    except Exception as exc:
        if cached is not None:
            logger.warning("Pricing refresh failed, retaining cached rates: %s", exc)
            _write_cache(cache_path, table, fetched_at, checked_at=now_fn())
            return merged
        logger.warning("Pricing fetch failed, using bundled fallback: %s", exc)
        return _fallback_pricing()


class PricingTable(dict):
    """Rates with their retrieval origin, without adding fake model keys."""
    def __init__(self, data=(), sources=None):
        super().__init__(data)
        self.sources = sources or {}


class PreparedPricing(PricingTable):
    """One collection's fixed price table and reusable arithmetic inputs.

    Keep the original catalog for model admission, service context and source
    metadata. Only the input to the shared price function is narrowed: it reads
    the six rate families below, including their threshold-suffixed variants.
    No calculated amounts are cached, and a new collection gets a new instance.
    """

    def __init__(self, pricing):
        super().__init__(pricing, getattr(pricing, "sources", {}))
        self.model_keys = {}
        self._price_rates = {}

    def resolve_model_key(self, model):
        if model not in self.model_keys:
            self.model_keys[model] = resolve_model_key(model, self)
        return self.model_keys[model]

    def price_rates(self, rates):
        key = id(rates)
        if key not in self._price_rates:
            families = (token_cost.INPUT_RATE, token_cost.OUTPUT_RATE,
                        token_cost.CACHE_READ_RATE, token_cost.CACHE_WRITE_RATE,
                        token_cost.CACHE_WRITE_1H_RATE, token_cost.REASONING_RATE)
            self._price_rates[key] = {
                name: value for name, value in rates.items()
                if isinstance(name, str) and name.startswith(families)
            }
        return self._price_rates[key]


def _provenance(basis, sources=(), verified_at=None, reason=None):
    return {"basis": basis, "sources": list(dict.fromkeys(sources)),
            "verified_at": verified_at,
            "time_basis": "valuation_time" if basis == "nominal" else "unknown",
            "reason": reason}


def with_calculated_cost(entry, pricing=None):
    value, provenance = _cost_and_provenance(entry, pricing)
    return replace(entry, cost_usd=value, cost_provenance=provenance)


def calculate_cost(entry, pricing=None):
    return _cost_and_provenance(entry, pricing)[0]


def _cost_and_provenance(entry, pricing):
    previous = entry.cost_provenance
    if entry.cost_usd is not None and (previous is None or previous["basis"] == "recorded"):
        if (isinstance(entry.cost_usd, bool) or not isinstance(entry.cost_usd, (int, float))
                or not math.isfinite(entry.cost_usd) or entry.cost_usd < 0):
            return None, _provenance("unpriced", reason="invalid_recorded_cost")
        return entry.cost_usd, previous or _provenance("recorded", ["recorded"])
    if entry.usage_quality == "incomplete":
        return None, _provenance("unpriced", reason="usage_incomplete")
    context = entry.service_context or {}
    if context.get("conflicts"):
        return None, _provenance("unpriced", reason="conflicting_service_context")
    allowed = {"speed": {"standard", "fast"}, "inference_geo": {"global", "us"},
               "service_tier": {"standard"}}
    # Official Claude Code 2.1.296 applies a geo multiplier only for "us".
    # Recognize its observed sentinel without inventing an actual region or
    # accepting arbitrary future values; see docs/references/metrics.md#usage-and-cost.
    if entry.agent_id == "claude-code":
        allowed["inference_geo"].add("not_available")
    if any(key not in allowed or not isinstance(value, str) or value not in allowed[key]
           for key, value in context.items()):
        return None, _provenance("unpriced", reason="unsupported_service_context")
    table = pricing if pricing is not None else get_pricing()
    model_key = (table.resolve_model_key(entry.model) if isinstance(table, PreparedPricing)
                 else resolve_model_key(entry.model, table))
    if model_key is None:
        _log_unknown(entry.model)
        return None, _provenance("unpriced", reason="unknown_model")
    rates = table[model_key]
    source = getattr(table, "sources", {}).get(model_key, "configured")
    sources = [rates.get("source") or source]
    verified_at = rates.get("verified_at")
    if context.get("speed") == "fast":
        rates = rates.get("service_modes", {}).get("fast")
        if not isinstance(rates, dict):
            return None, _provenance("unpriced", reason="unsupported_service_context")
        sources = [rates["source"]]
        verified_at = rates.get("verified_at")
    multiplier = 1
    if context.get("inference_geo") == "not_available":
        sources.append("claude-code:2.1.296:sha256:c9b5341637becbd423ddffc5b254afb645682a3868cb708bbc6cc0e7bb419937")
    if context.get("inference_geo") == "us":
        geo = table[model_key].get("inference_geo_us")
        if not isinstance(geo, dict):
            return None, _provenance("unpriced", reason="unsupported_service_context")
        multiplier = geo["multiplier"]
        sources.append(geo["source"])
        if verified_at != geo.get("verified_at"):
            verified_at = None
    priced = token_cost.price(
        token_cost.TokenCounts(
            input_tokens=entry.input_tokens, output_tokens=entry.output_tokens,
            cache_read_tokens=entry.cache_read_tokens,
            cache_creation_tokens=entry.cache_creation_tokens,
            cache_creation_1h_tokens=entry.cache_creation_1h_tokens,
        ), table.price_rates(rates) if isinstance(table, PreparedPricing) else rates,
    )
    if priced is None:
        return None, _provenance("unpriced", sources, verified_at, "missing_rates")
    hour = min(entry.cache_creation_1h_tokens, entry.cache_creation_tokens)
    missing = list(priced.missing_rates)
    if hour == entry.cache_creation_tokens and "cache_creation" in missing:
        missing.remove("cache_creation")
    if hour and not _has_rate(rates, token_cost.CACHE_WRITE_1H_RATE, priced.tier):
        missing.append("cache_creation_1h")
    if missing:
        _log_partial_rates(model_key, tuple(missing))
        return None, _provenance("unpriced", sources, verified_at, "missing_rates")
    reason = "mode_unreported" if entry.agent_id == "claude-code" and not all(key in context for key in allowed) else None
    return priced.value * multiplier, _provenance("nominal", sources, verified_at, reason)


def _has_rate(rates, key, tier):
    keys = [key + "_" + tier, key] if tier else [key]
    return any(not isinstance(value, bool) and isinstance(value, (int, float))
               and math.isfinite(value) and value >= 0
               for value in (rates.get(candidate) for candidate in keys))


def resolve_model_key(model, pricing):
    """Require a priceable exact catalog identity, including its provider."""
    match = token_cost.resolve_model(model, pricing, allow_alias=False)
    return match.key if match is not None and match.kind == "exact" else None


def is_estimated_pricing_model(model):
    return _base_model_name(model) in ESTIMATED_PRICING_MODELS


def _read_fresh_cache(cache_path, now):
    if not cache_path.exists():
        return None
    try:
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None

    if not isinstance(payload, dict) or "fetched_at" not in payload or "data" not in payload:
        return None

    fetched_at = payload["fetched_at"]
    checked_at = payload.get("checked_at", fetched_at)
    if (not all(isinstance(value, (float, int)) and math.isfinite(value)
                for value in (fetched_at, checked_at))
            or now - fetched_at > TTL_SECONDS or not isinstance(payload["data"], dict)):
        return None
    data = payload["data"]
    table = PricingTable(data, payload.get("sources") or {key: "cached_catalog" for key in data})
    return table, fetched_at, checked_at


def _write_cache(cache_path, data, fetched_at, *, checked_at=None):
    payload = {"fetched_at": fetched_at, "data": data, "sources": getattr(data, "sources", {})}
    if checked_at is not None:
        payload["checked_at"] = checked_at
    temporary = None
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode="w", dir=cache_path.parent, delete=False,
                                         encoding="utf-8") as stream:
            temporary = Path(stream.name)
            stream.write(json.dumps(payload, sort_keys=True))
        temporary.replace(cache_path)
    except OSError:
        logger.warning("Could not write pricing cache to %s", cache_path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _with_bundled_supplements(data):
    if not isinstance(data, dict):
        return data

    # Bundled entries fill holes in fresh LiteLLM cache data without changing
    # upstream prices. GLM-5.1/5.2 use zai/glm-5 as a GLM-family estimate.
    bundled = _fallback_pricing()
    if not bundled:
        return data
    merged = PricingTable(data, dict(getattr(data, "sources", {})))
    for key, value in bundled.items():
        if key not in merged:
            merged[key] = value
            merged.sources[key] = "bundled"
        elif isinstance(merged[key], dict):
            # Official mode metadata is independent of catalog standard rates.
            merged[key] = dict(merged[key])
            for field in ("service_modes", "inference_geo_us"):
                if field in value:
                    merged[key].setdefault(field, value[field])
    return merged


def _fetch_litellm_pricing():
    context = ssl.create_default_context()
    request = urllib.request.Request(LITELLM_URL, headers={"User-Agent": "agent-monitor/0.1"})
    with network_policy.opener(urllib.request.HTTPSHandler(context=context)).open(request, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def _fallback_pricing():
    try:
        data = json.loads(FALLBACK_PATH.read_text(encoding="utf-8"))
        return PricingTable(data, {key: "bundled" for key in data})
    except (json.JSONDecodeError, OSError):
        logger.warning("Bundled pricing fallback missing or invalid")
        return {}


def _log_partial_rates(model_key, missing):
    pair = (model_key, missing)
    if pair in _PARTIAL_LOGGED:
        return
    _PARTIAL_LOGGED.add(pair)
    logger.warning("Unknown cost for %s: no rate for %s", model_key, ", ".join(missing))


def _log_unknown(model):
    if model in _UNKNOWN_LOGGED:
        return
    _UNKNOWN_LOGGED.add(model)
    logger.warning("Unknown pricing model: %s", model)


def _base_model_name(model):
    return (model or "").split("[", 1)[0]
