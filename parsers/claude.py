import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from agent_monitor.contracts.usage import SERVICE_CONTEXT_KEYS, UsageEntry, complete_usage_observation


# Ported and dependency-stripped from token-tracker src/adapters/claude.py.
CLAUDE_DIRS = [
    os.path.expanduser("~/.claude/projects"),
    os.path.expanduser("~/.config/claude/projects"),
]


def load_entries(hours_back=0, base_dirs=None):
    entries = {}
    cutoff = None
    if hours_back > 0:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours_back)

    for base_dir in base_dirs or _get_claude_dirs():
        base = Path(base_dir)
        if not base.is_dir():
            continue
        for jsonl_path in base.rglob("*.jsonl"):
            fallback_project = _extract_project_from_dir(jsonl_path, base)
            for entry in parse_file(jsonl_path, fallback_project=fallback_project):
                if cutoff and entry.timestamp < cutoff:
                    continue
                entries[entry.dedup_key] = complete_usage_observation(entries.get(entry.dedup_key), entry)

    return sorted(entries.values(), key=lambda entry: entry.timestamp)


def parse_file(path, fallback_project="unknown", source_errors=None):
    entries = {}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError as exc:
                    _record_source_error(source_errors, path, line_number, exc)
                    continue

                if not isinstance(data, dict) or data.get("type") != "assistant":
                    continue

                entry = _parse_assistant_entry(data, fallback_project)
                if entry is None:
                    continue
                entries[entry.dedup_key] = complete_usage_observation(entries.get(entry.dedup_key), entry, source_errors)
    except (OSError, PermissionError, UnicodeError) as exc:
        _record_source_error(source_errors, path, None, exc)
        return []
    return list(entries.values())


def _record_source_error(source_errors, path, line_number, exc):
    if source_errors is None:
        return
    detail = "%s: %s" % (type(exc).__name__, exc)
    if line_number is not None:
        detail = "line %d: %s" % (line_number, detail)
    source_errors.append({"path": str(path), "stage": "parse", "error": detail})


def _get_claude_dirs():
    dirs = list(CLAUDE_DIRS)
    env = os.environ.get("CLAUDE_CONFIG_DIR")
    if env:
        for raw_path in env.split(","):
            projects_dir = os.path.join(raw_path.strip(), "projects")
            if projects_dir not in dirs:
                dirs.insert(0, projects_dir)
    return dirs


def _parse_assistant_entry(data, project):
    message = data.get("message")
    if not isinstance(message, dict):
        return None

    usage = message.get("usage")
    if not isinstance(usage, dict):
        return None

    input_tokens = _int(usage.get("input_tokens"))
    output_tokens = _int(usage.get("output_tokens"))
    cache_creation = _int(usage.get("cache_creation_input_tokens"))
    cache_read = _int(usage.get("cache_read_input_tokens"))
    cache_creation_1h = _cache_creation_1h(usage, cache_creation)

    stop_reason = message.get("stop_reason")
    quality = ("incomplete" if "stop_reason" in message and stop_reason is None else
               "observed" if isinstance(stop_reason, str) and stop_reason else "unknown")
    context = {key: value if isinstance(value, str) and len(value) <= 128 else None
               for key, value in usage.items() if key in SERVICE_CONTEXT_KEYS}
    if quality != "incomplete" and input_tokens == 0 and output_tokens == 0 and cache_creation == 0 and cache_read == 0:
        return None

    try:
        timestamp = _parse_timestamp(data.get("timestamp", ""))
    except (TypeError, ValueError):
        return None

    cwd = data.get("cwd", "")
    if cwd:
        project = _project_from_cwd(cwd)

    return UsageEntry(
        timestamp=timestamp,
        session_id=data.get("sessionId", ""),
        message_id=message.get("id", ""),
        request_id=data.get("requestId") or "",
        model=message.get("model", "unknown"),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_creation_tokens=cache_creation,
        cache_read_tokens=cache_read,
        cost_usd=data.get("costUSD"),
        project=project,
        agent_id="claude-code",
        cache_creation_1h_tokens=cache_creation_1h,
        usage_quality=quality,
        service_context=context or None,
    )


def _cache_creation_1h(usage, cache_creation_total):
    breakdown = usage.get("cache_creation")
    if not isinstance(breakdown, dict):
        return 0
    return min(_int(breakdown.get("ephemeral_1h_input_tokens")), cache_creation_total)


def _parse_timestamp(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _int(value):
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _project_from_cwd(cwd):
    return cwd or "unknown"


def _extract_project_from_dir(jsonl_path, base):
    rel = jsonl_path.relative_to(base)
    project_dir = str(rel.parts[0]) if rel.parts else "unknown"
    decoded = project_dir.replace("-", os.sep).strip(os.sep)
    if project_dir.startswith("-"):
        decoded = os.sep + decoded
    return decoded or "unknown"
