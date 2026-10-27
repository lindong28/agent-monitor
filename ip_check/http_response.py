# Extracted from the agent-monitor ip-check integration; see ../THIRD-PARTY-NOTICES.md.
"""Shared HTTP rejection handling; never retries a request."""
import datetime
import math
from email.utils import parsedate_to_datetime


def require_success(response, on_retry_after=None):
    if 200 <= response.status_code < 300:
        return
    raw = response.headers.get("Retry-After", "")
    delay = None
    if isinstance(raw, str) and len(raw) <= 128:
        value = raw.strip()
        if value.isascii() and value.isdecimal() and len(value) <= 20:
            delay = int(value)
        elif value:
            try:
                date = parsedate_to_datetime(value)
                if date.tzinfo is not None:
                    delay = max(0, math.ceil((date - datetime.datetime.now(datetime.timezone.utc)).total_seconds()))
            except (ValueError, TypeError, OverflowError):
                pass
    message = f"HTTP {response.status_code}，未重试"
    if delay is not None:
        message += f"；服务端 Retry-After：至少 {delay} 秒后再试"
        if on_retry_after is not None:
            on_retry_after(delay)
    elif raw:
        message += "；服务端 Retry-After 无法解析"
    raise ValueError(message)
