"""Codex quota response semantics shared by native and account-action readers."""
import math
import json
from datetime import datetime, timezone
from parsers.codex import select_codex_rate_limit
from agent_monitor import diagnostics

REACHED_LIMITS = {"rate_limit_reached", "workspace_owner_credits_depleted",
                  "workspace_member_credits_depleted", "workspace_owner_usage_limit_reached",
                  "workspace_member_usage_limit_reached"}


def rpc_failure(error, *, stage="quota_read"):
    """Bounded diagnostic facts, never an admission or account-scope classifier."""
    facts = {"stage": stage, "kind": "rpc_error"}
    if isinstance(error, dict):
        facts["rpc_code"] = error.get("code")
        message = error.get("message")
        if isinstance(message, str):
            # Codex's quota transport wraps an upstream JSON body after body=.
            try:
                body = json.loads(message.partition("body=")[2])
            except ValueError:
                body = None
            if isinstance(body, dict):
                facts["http_status"] = body.get("status")
                reason = body.get("error")
                if isinstance(reason, dict):
                    facts["upstream_code"] = reason.get("code")
                    facts["detail"] = reason.get("message")
            else:
                facts["detail"] = message.partition("body=")[0]
    return diagnostics.safe_failure(facts)

def decode(result, account_id):
    if result.get("accountId") is None:
        raise ValueError("配额响应未提供账号，无法核验读数归属；请稍后重试。")
    if result.get("accountId") != account_id:
        raise ValueError("配额响应的账号与所选账号不匹配。")
    limit = select_codex_rate_limit(result) or {}
    reading = {"observed_at": datetime.now(timezone.utc).isoformat(), "codex_quota_available": bool(limit)}
    allowed = result.get("ordinaryUsageAllowed")
    reading["ordinary_usage_allowed"] = allowed if type(allowed) is bool else None
    spent = limit.get("spendControlReached")
    reading["spend_control_reached"] = spent if type(spent) is bool else None
    reached = limit.get("rateLimitReachedType")
    reading["rate_limit_reached_type"] = (reached if isinstance(reached, str) and reached in REACHED_LIMITS
                                          else "unrecognized" if reached is not None else None)
    for minutes, prefix in ((300, "five_hour"), (10080, "seven_day")):
        window = next((limit[key] for key in ("primary", "secondary")
                       if isinstance(limit.get(key), dict) and limit[key].get("windowDurationMins") == minutes), {})
        for source, target in (("usedPercent", "used_pct"), ("resetsAt", "resets_at")):
            value = window.get(source)
            reading[prefix + "_" + target] = value if type(value) in (int, float) and math.isfinite(value) else None
    reading["plan"] = limit.get("planType") if isinstance(limit.get("planType"), str) else None
    return reading

