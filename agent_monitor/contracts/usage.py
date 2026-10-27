"""Shared usage observations, quota readings and loaded-history metadata.

No parser, persistence or service implementation is imported here.
"""
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Mapping, Optional


@dataclass(frozen=True)
class UsageEntry:
    timestamp: datetime
    session_id: str
    message_id: str
    request_id: str
    model: str
    input_tokens: int
    output_tokens: int
    cache_creation_tokens: int
    cache_read_tokens: int
    cost_usd: Optional[float]
    project: str
    agent_id: str
    message_count: int = 1
    # Subset of cache_creation_tokens written with a 1-hour TTL, which Anthropic
    # bills at a higher rate than the 5-minute default. Cost-only: the total
    # stays in cache_creation_tokens so token counters are unaffected.
    cache_creation_1h_tokens: int = 0

    # Observed means an end marker was persisted, not provider-final assurance.
    usage_quality: str = "unknown"
    service_context: Optional[dict] = None
    cost_provenance: Optional[dict] = None

    @property
    def dedup_key(self):
        # Keyed on the API call, not the transcript it lives in: Claude Code
        # copies whole transcripts into a new session file on resume/fork, so a
        # session-scoped key counts one call once per copy.
        return "%s:%s:%s" % (self.agent_id, self.message_id, self.request_id)


@dataclass(frozen=True)
class RateLimits:
    five_hour_pct: Optional[float]
    five_hour_resets_at: Optional[float]
    seven_day_pct: Optional[float]
    seven_day_resets_at: Optional[float]
    model: str = ""
    updated_at: str = ""
    # The plan the reading itself reports, when the provider states one inside
    # the same object as the percentages. It is the only plan known to come from
    # the same event as them — a plan read from a credential file is a separate
    # fact on its own clock. Same event, not same observation: a percentage
    # whose window has since reset is rewritten to zero while this keeps the
    # value the event carried. Codex fills it; Claude's status file
    # carries no plan, so it stays None there. See ADR 20260822-586a.
    plan: Optional[str] = None
    quota_observation: Optional[dict] = None


USAGE_QUALITIES = frozenset(("unknown", "incomplete", "observed"))
SERVICE_CONTEXT_KEYS = frozenset(("speed", "inference_geo", "service_tier"))
COST_PROVENANCE_KEYS = frozenset(("basis", "sources", "verified_at", "time_basis", "reason"))
COST_BASES = frozenset(("recorded", "nominal", "unpriced"))
COST_REASONS = frozenset(("usage_incomplete", "unknown_model", "missing_rates",
                         "unsupported_service_context", "conflicting_service_context",
                         "mode_unreported", "invalid_recorded_cost"))


def validate_usage_metadata(row: Mapping) -> None:
    """Shared validator for the optional archive/snapshot usage metadata."""
    quality = row.get("usage_quality", "unknown")
    if not isinstance(quality, str) or quality not in USAGE_QUALITIES:
        raise ValueError("invalid usage_quality")
    context = row.get("service_context")
    if context is not None:
        if not isinstance(context, dict) or not set(context) <= SERVICE_CONTEXT_KEYS | {"conflicts"}:
            raise ValueError("invalid service_context")
        for key in SERVICE_CONTEXT_KEYS & context.keys():
            if context[key] is not None and (not isinstance(context[key], str) or len(context[key]) > 128):
                raise ValueError("invalid service_context value")
        if "conflicts" in context:
            conflicts = context["conflicts"]
            if (not isinstance(conflicts, list) or not 1 <= len(conflicts) <= 3
                    or any(not isinstance(key, str) or key not in SERVICE_CONTEXT_KEYS for key in conflicts)
                    or len(set(conflicts)) != len(conflicts)):
                raise ValueError("invalid service_context conflicts")
    provenance = row.get("cost_provenance")
    if provenance is None:
        return
    if not isinstance(provenance, dict) or set(provenance) != COST_PROVENANCE_KEYS:
        raise ValueError("invalid cost_provenance keys")
    for key, allowed in (("basis", COST_BASES), ("time_basis", {"valuation_time", "unknown"}),
                         ("reason", COST_REASONS | {None})):
        if not isinstance(provenance[key], (str, type(None))) or provenance[key] not in allowed:
            raise ValueError("invalid cost_provenance " + key)
    basis = provenance["basis"]
    if "cost_usd" in row and (basis == "unpriced") != (row["cost_usd"] is None):
        raise ValueError("cost_provenance contradicts amount")
    if provenance["time_basis"] != ("valuation_time" if basis == "nominal" else "unknown"):
        raise ValueError("cost_provenance contradicts time basis")
    valid_reason = (provenance["reason"] in {None, "mode_unreported"} if basis == "nominal" else
                    provenance["reason"] is None if basis == "recorded" else
                    provenance["reason"] in COST_REASONS - {"mode_unreported"})
    if not valid_reason:
        raise ValueError("cost_provenance contradicts reason")
    sources = provenance["sources"]
    if (not isinstance(sources, list) or len(sources) > 3
            or any(not isinstance(value, str) or not value or len(value) > 1024 for value in sources)
            or len(set(sources)) != len(sources)):
        raise ValueError("invalid cost_provenance sources")
    day = provenance["verified_at"]
    if day is not None:
        try:
            if not isinstance(day, str) or len(day) != 10 or datetime.strptime(day, "%Y-%m-%d").strftime("%Y-%m-%d") != day:
                raise ValueError()
        except (TypeError, ValueError):
            raise ValueError("invalid cost_provenance verified_at") from None


def usage_quality_counts(entries):
    counts = {quality: 0 for quality in ("observed", "incomplete", "unknown")}
    for entry in entries:
        if entry.agent_id == "claude-code":
            counts[entry.usage_quality] += 1
    return counts


def _merge_service_context(existing, incoming, stronger, equal_quality):
    context = dict(existing or {})
    conflicts = set(context.pop("conflicts", []))
    incoming = incoming or {}
    conflicts.update(incoming.get("conflicts", []))
    for key in SERVICE_CONTEXT_KEYS & incoming.keys():
        if key not in context or stronger:
            context[key] = incoming[key]
            if stronger and key not in incoming.get("conflicts", []):
                conflicts.discard(key)
        elif equal_quality and context[key] != incoming[key]:
            conflicts.add(key)
    if conflicts:
        context["conflicts"] = sorted(conflicts)
    return context or None


def complete_usage_observation(existing, incoming, source_errors=None):
    """Keep attribution and real token vectors; merge independently observed metadata."""
    if existing is None or incoming.agent_id != "claude-code":
        return incoming
    fields = ("input_tokens", "output_tokens", "cache_creation_tokens",
              "cache_read_tokens", "cache_creation_1h_tokens")
    old = tuple(getattr(existing, name) for name in fields)
    new = tuple(getattr(incoming, name) for name in fields)
    if old == new:
        rank = {"unknown": 0, "incomplete": 1, "observed": 2}
        stronger = rank[incoming.usage_quality] > rank[existing.usage_quality]
        context = _merge_service_context(existing.service_context, incoming.service_context, stronger,
                                         incoming.usage_quality == existing.usage_quality)
        quality = incoming.usage_quality if stronger else existing.usage_quality
        # Derived prices must be recomputed after fresh evidence, not become recorded facts.
        cost = existing.cost_usd
        provenance = existing.cost_provenance
        if incoming.cost_usd is not None:
            cost, provenance = incoming.cost_usd, incoming.cost_provenance
        elif (quality != existing.usage_quality or context != existing.service_context) and provenance and provenance["basis"] != "recorded":
            cost, provenance = None, None
        return replace(existing, usage_quality=quality, service_context=context,
                       cost_usd=cost, cost_provenance=provenance)
    if all(a <= b for a, b in zip(new, old)):
        return existing
    if all(a <= b for a, b in zip(old, new)):
        cost, provenance = incoming.cost_usd, incoming.cost_provenance
        if cost is None and existing.cost_usd is not None and (existing.cost_provenance is None or existing.cost_provenance["basis"] == "recorded"):
            cost, provenance = existing.cost_usd, existing.cost_provenance
        return replace(existing, **{name: getattr(incoming, name) for name in fields},
                       cost_usd=cost, usage_quality=incoming.usage_quality,
                       service_context=incoming.service_context, cost_provenance=provenance)
    if source_errors is not None:
        source_errors.append({"path": incoming.dedup_key, "stage": "usage_observation",
                              "error": "incomparable usage observations; retained earlier observed snapshot"})
    return existing


class LoadedEntries(list):
    def __init__(
        self,
        entries=(),
        blocked_sources=(),
        persisted_blockers=(),
        current_blockers=(),
        unattributed_entries=(),
        source_errors=(),
        scan_complete=True,
        history_authoritative_from=None,
        history_authoritative_projects=None,
        session_metadata=(),
    ):
        super().__init__(entries)
        self.blocked_sources = list(blocked_sources)
        self.persisted_blockers = list(persisted_blockers)
        self.current_blockers = list(current_blockers)
        self.unattributed_entries = list(unattributed_entries)
        self.source_errors = list(source_errors)
        self.scan_complete = scan_complete
        self.history_authoritative_from = history_authoritative_from
        self.history_authoritative_projects = history_authoritative_projects
        self.session_metadata = list(session_metadata)
