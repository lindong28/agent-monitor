"""Codex quota response semantics shared by native and account-action readers."""
import math
import json
from datetime import datetime, timezone
from pathlib import Path
from parsers.accounts import codex_account
from parsers.codex import select_codex_rate_limit
from agent_monitor import diagnostics

REACHED_LIMITS = {"rate_limit_reached", "workspace_owner_credits_depleted",
                  "workspace_member_credits_depleted", "workspace_owner_usage_limit_reached",
                  "workspace_member_usage_limit_reached"}


def credential_secrets(auth_path):
    """In-memory redaction values only; never include these in a receipt."""
    try:
        auth = json.loads(Path(auth_path).read_text())
    except (OSError, ValueError):
        return ()
    if not isinstance(auth, dict):
        return ()
    tokens = auth.get("tokens")
    values = [tokens.get(key) for key in ("access_token", "refresh_token", "id_token")] if isinstance(tokens, dict) else []
    values.extend(auth.get(key) for key in ("OPENAI_API_KEY", "personal_access_token", "bedrock_api_key"))
    return tuple(value for value in values if isinstance(value, str) and value)


def rpc_failure(error, *, stage="quota_read", secrets=()):
    """Bounded diagnostic facts, never an admission or account-scope classifier."""
    facts = {"stage": stage, "kind": "rpc_error"}
    if isinstance(error, dict):
        facts["rpc_code"] = error.get("code")
        message = error.get("message")
        if isinstance(message, str):
            for secret in sorted(set(secrets), key=len, reverse=True):
                message = message.replace(secret, "[redacted]")
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
    # JSON escapes can decode into known credentials after the raw replacement.
    for key, value in facts.items():
        if isinstance(value, str):
            for secret in sorted(set(secrets), key=len, reverse=True):
                value = value.replace(secret, "[redacted]")
            facts[key] = value
    return diagnostics.safe_failure(facts)


class ReadingError(ValueError):
    def __init__(self, message, kind="account_mismatch", stage="account_check"):
        super().__init__(message)
        self.failure = {"stage": stage, "kind": kind}


def check_identity(auth_path, account_id, label):
    current = codex_account(auth_path)
    if (current is None or current.account_id != account_id
            or (current.label or "").casefold() != (label or "").casefold()):
        raise ReadingError("此配置的登录身份与账号记录不一致，未采纳配额读数。")
    return current


def read_bound(request, auth_path, account_id, label):
    """One quota RPC, bound to the same local identity before and after it.

    Callers retain transport, cancellation, admission and pause ownership.
    account/read is not a prerequisite of the upstream quota method.
    """
    check_identity(auth_path, account_id, label)
    result = request("account/rateLimits/read", {})
    check_identity(auth_path, account_id, label)
    reading = decode(result, account_id)
    if reading["five_hour_used_pct"] is None and reading["seven_day_used_pct"] is None:
        raise ReadingError("服务端未返回可用配额窗口，本次保留上次读数。", "missing_windows", "quota_parse")
    return reading


def decode(result, account_id):
    if result.get("accountId") is None:
        raise ReadingError("配额响应未提供账号，无法核验读数归属；请稍后重试。", "account_missing")
    if result.get("accountId") != account_id:
        raise ReadingError("配额响应的账号与所选账号不匹配。")
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

