"""Pure usage projections shared by dashboard view components."""
from collections import defaultdict
from datetime import timedelta

import rollup
from aggregators import extract_metric
from pricing_fetcher import is_estimated_pricing_model
from project_alias import canonical_project

def summary(entries):
    by_agent = defaultdict(float)
    total_cost = 0.0
    for entry in entries:
        if entry.cost_usd is not None:
            total_cost += entry.cost_usd
            by_agent[entry.agent_id] += entry.cost_usd
    return {
        "cost_usd": total_cost,
        "tokens": sum(extract_metric(entry, "total") for entry in entries),
        "by_agent": {"claude-code": by_agent["claude-code"], "codex": by_agent["codex"]},
    }


def daily_cost(entries, now):
    days = []
    by_day = defaultdict(lambda: {"claude_cost": 0.0, "codex_cost": 0.0})
    for entry in entries:
        day = entry.timestamp.astimezone(rollup.BUCKET_TIMEZONE).date().isoformat()
        if entry.cost_usd is None:
            continue
        key = "codex_cost" if entry.agent_id == "codex" else "claude_cost"
        by_day[day][key] += entry.cost_usd
    start = now.astimezone(rollup.BUCKET_TIMEZONE).date() - timedelta(days=29)
    for offset in range(30):
        day = (start + timedelta(days=offset)).isoformat()
        row = {"date": day}
        row.update(by_day[day])
        days.append(row)
    return days


def top_projects(entries, limit):
    totals = defaultdict(float)
    for entry in entries:
        if entry.cost_usd is not None:
            totals[entry.project] += entry.cost_usd
    return [
        {"project": project, "cost_usd": cost}
        for project, cost in sorted(totals.items(), key=lambda item: item[1], reverse=True)[:limit]
    ]


def model_mix(entries):
    totals = defaultdict(int)
    for entry in entries:
        totals[entry.model] += extract_metric(entry, "total")
    grand_total = sum(totals.values()) or 1
    return [
        {"model": model, "tokens": tokens, "pct": tokens / grand_total}
        for model, tokens in sorted(totals.items(), key=lambda item: item[1], reverse=True)
    ]


def group_sessions(entries):
    sessions = defaultdict(list)
    for entry in entries:
        sessions[entry.session_id].append(entry)
    return sessions


def group_execution_sessions(entries):
    sessions = defaultdict(list)
    for entry in entries:
        sessions[(entry.agent_id, entry.session_id)].append(entry)
    return sessions


def conversation_stats(entries, key, index, aliases=None):
    result = session_stats(entries)
    identity = index.metadata.get(key, {})
    own = next((entry for entry in entries if (entry.agent_id, entry.session_id) == key), None)
    result.update(session_id=key[1], agent_id=key[0], title=identity.get("title"),
                  created_at=identity.get("created_at"), account=identity.get("account"),
                  relationship_status=index.status(key),
                  subagent_count=len({(e.agent_id, e.session_id) for e in entries} - {key}),
                  project=own.project if own else canonical_project(identity.get("project") or result["project"], aliases))
    return result


def session_stats(entries):
    entries = sorted(entries, key=lambda entry: entry.timestamp)
    known_costs = [entry.cost_usd for entry in entries if entry.cost_usd is not None]
    cost = sum(known_costs) if known_costs else None
    start = entries[0].timestamp
    end = entries[-1].timestamp
    usage_events = sum(entry.message_count for entry in entries)
    return {
        "session_id": entries[0].session_id,
        "agent_id": entries[0].agent_id,
        "project": entries[0].project,
        "model": entries[0].model,
        "started_at": start.isoformat(),
        "ended_at": end.isoformat(),
        "duration_seconds": max(int((end - start).total_seconds()), 0),
        "cost_usd": cost,
        "unpriced_records": len(entries) - len(known_costs),
        "tokens": sum(extract_metric(entry, "total") for entry in entries),
        "usage_events": usage_events,
        "estimated": any(e.agent_id == "codex" or is_estimated_pricing_model(e.model) for e in entries),
    }


def entry_json(entry):
    return {
        "timestamp": entry.timestamp.isoformat(),
        "session_id": entry.session_id,
        "message_id": entry.message_id,
        "request_id": entry.request_id,
        "model": entry.model,
        "input_tokens": entry.input_tokens,
        "output_tokens": entry.output_tokens,
        "cache_creation_tokens": entry.cache_creation_tokens,
        "cache_read_tokens": entry.cache_read_tokens,
        "cost_usd": entry.cost_usd,
        "project": entry.project,
        "agent_id": entry.agent_id,
        "usage_event_count": entry.message_count,
        "usage_quality": entry.usage_quality,
        "service_context": entry.service_context,
        "cost_provenance": entry.cost_provenance,
    }


def session_sort_key(row, sort):
    if sort == "cost":
        return row["cost_usd"] if row["cost_usd"] is not None else -1
    if sort == "tokens":
        return row["tokens"]
    if sort == "duration":
        return row["duration_seconds"]
    return row["ended_at"]


def filter_time(entries, time_range):
    if time_range is None:
        return list(entries)
    start, end = time_range
    return [entry for entry in entries if start <= entry.timestamp.astimezone() <= end]


def filter_values(entries, query):
    agents = set(query.get("agent", []))
    projects = set(query.get("project", []))
    models = set(query.get("model", []))
    result = []
    for entry in entries:
        if agents and entry.agent_id not in agents:
            continue
        if projects and entry.project not in projects:
            continue
        if models and entry.model not in models:
            continue
        result.append(entry)
    return result
