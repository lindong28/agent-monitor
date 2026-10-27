"""Existing dashboard calendar/window semantics."""
from datetime import date, datetime, timedelta
from agent_monitor.contracts import calendar

from agent_monitor.contracts.calendar import RANGE_DAYS

def session_window(query, now):
    if first(query, "range", "30d") != "custom":
        return range_window(first(query, "range", "30d"), now)
    start, end = pivot_window(query)
    return (datetime.combine(start, datetime.min.time(), tzinfo=calendar.BUCKET_TIMEZONE),
            datetime.combine(end, datetime.max.time(), tzinfo=calendar.BUCKET_TIMEZONE))


def range_window(value, now):
    calendar_window = calendar.range_window(value, now)
    if calendar_window is None:
        return None
    start_date, _ = calendar_window
    start = datetime.combine(
        start_date, datetime.min.time(), tzinfo=calendar.BUCKET_TIMEZONE
    )
    return start, now


def pivot_window(query):
    if first(query, "range", "30d") != "custom":
        return calendar.range_window(first(query, "range", "30d"))
    try:
        start = date.fromisoformat(first(query, "start", ""))
        end = date.fromisoformat(first(query, "end", ""))
    except ValueError as exc:
        raise ValueError("请选择有效的起始和终止日期") from exc
    if start > end:
        raise ValueError("起始日期不能晚于终止日期")
    return start, end


def auto_time_dim(range_value):
    if range_value == "all":
        return "month"
    days = RANGE_DAYS.get(range_value, 30)
    if days <= 90:
        return "day"
    if days <= 365:
        return "week"
    return "month"


def first(query, key, default):
    values = query.get(key)
    return values[0] if values else default


def start_of_day(value):
    return value.astimezone(calendar.BUCKET_TIMEZONE).replace(
        hour=0, minute=0, second=0, microsecond=0
    )


def start_of_week(value):
    start = start_of_day(value)
    return start - timedelta(days=start.weekday())


def start_of_month(value):
    return value.astimezone(calendar.BUCKET_TIMEZONE).replace(
        day=1, hour=0, minute=0, second=0, microsecond=0
    )
