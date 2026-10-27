"""Shared accounting calendar and inclusive day-window rules."""
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

BUCKET_TIMEZONE_NAME = "Asia/Shanghai"
BUCKET_TIMEZONE = ZoneInfo(BUCKET_TIMEZONE_NAME)
RANGE_DAYS = {"7d": 7, "30d": 30, "90d": 90, "6m": 180, "1y": 365, "2y": 730}


def range_window(value, now=None):
    if value == "all":
        return None
    if isinstance(value, str) and value.endswith("d") and value[:-1].isdigit():
        days = int(value[:-1])
    else:
        days = RANGE_DAYS.get(value, 30)
    if days < 1:
        raise ValueError("range days must be positive")
    current = now() if callable(now) else now
    if current is None:
        current = datetime.now().astimezone()
    if current.tzinfo is None or current.utcoffset() is None:
        raise ValueError("range now must be timezone-aware")
    today = current.astimezone(BUCKET_TIMEZONE).date()
    return today - timedelta(days=days - 1), today


def calendar_date_bounds(time_range, calendar_days=None):
    start, end = time_range
    if isinstance(start, datetime) and isinstance(end, datetime):
        if any(value.tzinfo is None or value.utcoffset() is None for value in (start, end)):
            raise ValueError("datetime range endpoints must be timezone-aware")
        if end < start:
            raise ValueError("time range end must not precede start")
        if type(calendar_days) is not int or calendar_days < 1:
            raise ValueError(
                "datetime ranges require a positive explicit calendar_days value"
            )
        end_date = end.astimezone(BUCKET_TIMEZONE).date()
        return end_date - timedelta(days=calendar_days - 1), end_date
    if type(start) is date and type(end) is date:
        if calendar_days is not None:
            raise ValueError("calendar_days is only valid with datetime ranges")
        if end < start:
            raise ValueError("time range end must not precede start")
        return start, end
    raise TypeError("time range endpoints must both be dates or timezone-aware datetimes")
