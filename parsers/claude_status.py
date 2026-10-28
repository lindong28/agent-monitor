import json
import logging
import os
from agent_monitor.quota import quota_applicability
from pathlib import Path
from typing import Optional

from agent_monitor.contracts.usage import RateLimits


STATUS_FILE = os.path.expanduser("~/.claude/tt-status.json")

logger = logging.getLogger(__name__)


def load_rate_limits(status_file: Optional[str] = None) -> Optional[RateLimits]:
    path = Path(status_file or STATUS_FILE)
    if not path.is_file():
        return None

    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (json.JSONDecodeError, OSError, PermissionError) as exc:
        logger.warning("Skipping Claude status file %s: %s", path, exc)
        return None

    if not isinstance(data, dict):
        return None

    rate_limits = data.get("rate_limits")
    if not isinstance(rate_limits, dict):
        return None

    five_hour = rate_limits.get("five_hour") or {}
    seven_day = rate_limits.get("seven_day") or {}
    if not isinstance(five_hour, dict) or not isinstance(seven_day, dict):
        return None

    five_pct = _number(five_hour.get("used_percentage"))
    five_reset = _number(five_hour.get("resets_at"))
    seven_pct = _number(seven_day.get("used_percentage"))
    seven_reset = _number(seven_day.get("resets_at"))

    if five_pct is None and seven_pct is None:
        return None

    model = data.get("model") or {}
    if not isinstance(model, dict):
        model = {}

    observed_at = data.get("_quota_observed_at")
    if quota_applicability.timestamp(observed_at) is None:
        observed_at = None
    return RateLimits(
        five_hour_pct=five_pct,
        five_hour_resets_at=five_reset,
        seven_day_pct=seven_pct,
        seven_day_resets_at=seven_reset,
        model=model.get("id") or "",
        updated_at=observed_at or "",
        quota_observation={"source": "claude_statusline", "observed_at": observed_at},
    )


def _number(value):
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return value
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
