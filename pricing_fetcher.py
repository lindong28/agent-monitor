import json
import logging
import math
import ssl
import time
import urllib.request
from pathlib import Path

import token_cost


# Adapted from token-tracker src/analyzer/cost.py; this version wraps the
# cache with fetched_at and enforces a 7-day TTL.
LITELLM_URL = "https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json"
TTL_SECONDS = 7 * 24 * 3600
ROOT = Path(__file__).resolve().parent
CACHE_PATH = ROOT / "state" / "pricing_cache.json"
FALLBACK_PATH = ROOT / "pricing.json"
ESTIMATED_PRICING_MODELS = {"glm-5.1", "glm-5.2"}

logger = logging.getLogger(__name__)
_PARTIAL_LOGGED = set()
_UNKNOWN_LOGGED = set()


def get_pricing(cache_path=CACHE_PATH, fetcher=None, now=None, persist=True):
    now_fn = now or time.time
    cache_path = Path(cache_path)
    cached = _read_fresh_cache(cache_path, now_fn())
    if cached is not None:
        return _with_bundled_supplements(cached)

    fetch = fetcher or _fetch_litellm_pricing
    try:
        data = fetch()
        if persist:
            _write_cache(cache_path, data, now_fn())
        return _with_bundled_supplements(data)
    except Exception as exc:
        logger.warning("Pricing fetch failed, using bundled fallback: %s", exc)
        return _fallback_pricing()


def calculate_cost(entry, pricing=None):
    if entry.cost_usd is not None:
        return entry.cost_usd

    table = pricing if pricing is not None else get_pricing()
    model_key = resolve_model_key(entry.model, table)
    if model_key is None:
        _log_unknown(entry.model)
        return None

    # Keep shared arithmetic unchanged; this adapter only exposes fully known
    # amounts because UsageEntry has no partial-price provenance field.
    priced = token_cost.price(
        token_cost.TokenCounts(
            input_tokens=entry.input_tokens,
            output_tokens=entry.output_tokens,
            cache_read_tokens=entry.cache_read_tokens,
            cache_creation_tokens=entry.cache_creation_tokens,
            cache_creation_1h_tokens=entry.cache_creation_1h_tokens,
        ),
        table[model_key],
    )
    if priced is None:
        return None
    hour = min(entry.cache_creation_1h_tokens, entry.cache_creation_tokens)
    missing = list(priced.missing_rates)
    # The shared calculator reports the 5m rate absent even when every write
    # is explicitly 1h. Only categories actually used make the amount unknown.
    if hour == entry.cache_creation_tokens and "cache_creation" in missing:
        missing.remove("cache_creation")
    if hour and not _has_rate(table[model_key], token_cost.CACHE_WRITE_1H_RATE, priced.tier):
        missing.append("cache_creation_1h")
    if missing:
        _log_partial_rates(model_key, tuple(missing))
        return None
    return priced.value


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

    if now - float(payload.get("fetched_at", 0)) > TTL_SECONDS:
        return None
    return payload["data"]


def _write_cache(cache_path, data, fetched_at):
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(
            json.dumps({"fetched_at": fetched_at, "data": data}, sort_keys=True),
            encoding="utf-8",
        )
    except OSError:
        logger.warning("Could not write pricing cache to %s", cache_path)


def _with_bundled_supplements(data):
    if not isinstance(data, dict):
        return data

    # Bundled entries fill holes in fresh LiteLLM cache data without changing
    # upstream prices. GLM-5.1/5.2 use zai/glm-5 as a GLM-family estimate.
    bundled = _fallback_pricing()
    if not bundled:
        return data
    merged = dict(data)
    for key, value in bundled.items():
        merged.setdefault(key, value)
    return merged


def _fetch_litellm_pricing():
    context = ssl.create_default_context()
    request = urllib.request.Request(LITELLM_URL, headers={"User-Agent": "agent-monitor/0.1"})
    try:
        with urllib.request.urlopen(request, timeout=10, context=context) as response:
            return json.loads(response.read().decode("utf-8"))
    except ssl.SSLCertVerificationError:
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        with urllib.request.urlopen(request, timeout=10, context=context) as response:
            return json.loads(response.read().decode("utf-8"))


def _fallback_pricing():
    try:
        return json.loads(FALLBACK_PATH.read_text(encoding="utf-8"))
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
