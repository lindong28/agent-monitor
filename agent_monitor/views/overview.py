"""Dashboard overview aggregation over an admitted usage snapshot."""
from agent_monitor.contracts.calendar import calendar_date_bounds
from datetime import datetime, timedelta

from .time_ranges import RANGE_DAYS, first, auto_time_dim, start_of_week


class OverviewView:
    def __init__(self, collection, quota_view, rollups, *, clock=None):
        self.collection = collection
        self.quota_view = quota_view
        self.rollups = rollups
        self.clock = clock or (lambda: datetime.now().astimezone())

    def read(self, query):
        completed_before = self.collection.snapshot()["completed_at"]
        sync_started = self.collection.request(query)
        with self.collection.admission() as admission:
            with self.rollups.use_admitted_generations(admission.admitted):
                return self.render(query, admission, completed_before=completed_before,
                                   sync_started=sync_started)

    def render(self,
        query,
        admission,
        *,
        completed_before,
        sync_started,
    ):
        now = self.clock()
        range_value = first(query, "range", "30d")
        rollup_range_window = self.rollups.range_window(range_value, now)
        cost_granularity = auto_time_dim(range_value)
        cost_over_time = self.rollups.query_pivot(
            cost_granularity, "agent", "cost", time_range=rollup_range_window
        )
        today = now.astimezone(self.rollups.BUCKET_TIMEZONE).date()
        today_window = (today, today)
        week_window = (today - timedelta(days=today.weekday()), today)
        month_window = (today.replace(day=1), today)
        week_summary = self.summary(week_window)
        week_summary["window"] = {
            "start": start_of_week(now).isoformat(),
            "end": now.isoformat(),
        }

        sync_status = dict(self.collection.status(now=now, admission=admission))
        sync_status["refresh_pending"] = bool(
            sync_started
            or sync_status["syncing"]
            or sync_status.get("completed_at") != completed_before
        )
        result = {
            "rate_limits": self.quota_view.rate_limits(
                admission=admission,
                sync_status=sync_status,
            ),
            "sync": sync_status,
            "today": self.summary(today_window),
            "week": week_summary,
            "range": self.summary(rollup_range_window),
            "daily_cost_30d": self.daily_cost(
                self.rollups.query_pivot(
                    "day", "agent", "cost", time_range=self.rollups.range_window("30d", now)
                )
            ),
            "cost_over_time": cost_over_time,
            "cost_over_time_granularity": cost_granularity,
            "rollup_coverage": self.coverage(rollup_range_window),
            "top_projects_week": self.top_projects(week_window, limit=5),
            "model_mix_month": self.model_mix(month_window),
            "attribution_windows": {
                "week": {"start": week_window[0].isoformat(), "end": today.isoformat()},
                "month": {"start": month_window[0].isoformat(), "end": today.isoformat()},
            },
            "history_gap": None,
            "codex_cost_estimated": True,
        }
        if first(query, "compare", "") == "1":
            result["comparison"] = self.period_comparison(range_value, now)
        return result


    def period_comparison(self, range_value, now):
        if range_value == "all":
            return {"available": False, "reason": "全部历史没有等长的前一期"}
        days = RANGE_DAYS.get(range_value, 30)
        end = now.astimezone(self.rollups.BUCKET_TIMEZONE).date() - timedelta(days=1)
        start = end - timedelta(days=days - 1)
        previous = (start - timedelta(days=days), start - timedelta(days=1))
        def period(window):
            return {"start": window[0].isoformat(), "end": window[1].isoformat(),
                    **self.rollups.period_summary(window)}
        current, prior = period((start, end)), period(previous)
        known = current["known_cost_usd"] is not None and prior["known_cost_usd"] is not None
        return {"available": True, "timezone": str(self.rollups.BUCKET_TIMEZONE), "days": days,
                "current": current, "previous": prior,
                "known_cost_delta_usd": current["known_cost_usd"] - prior["known_cost_usd"] if known else None,
                "coverage_complete": None}


    def coverage(self, range_window):
        earliest = self.rollups.earliest_rollup_date()
        range_start = None
        partial = False
        if earliest:
            if range_window is None:
                partial = True
            else:
                range_start = calendar_date_bounds(range_window)[0].isoformat()
                partial = range_start < earliest
        return {
            "earliest_date": earliest,
            "range_start": range_start,
            "partial_before_range": partial,
        }


    def summary(self, time_range):
        cost_pivot = self.rollups.query_pivot(
            "agent", "none", "cost", time_range=time_range
        )
        cost_rows = cost_pivot["rows"]
        token_rows = self.rollups.query_pivot(
            "agent", "none", "total", time_range=time_range
        )["rows"]
        by_agent = {"claude-code": None, "codex": None}
        for row in cost_rows:
            value = row["values"].get("value")
            if value is not None:
                by_agent[row["x"]] = value
        return {
            "cost_usd": (sum(value for value in by_agent.values() if value is not None)
                         if any(value is not None for value in by_agent.values()) else None),
            "tokens": sum(
                row["values"].get("value") or 0
                for row in token_rows
            ),
            "by_agent": by_agent,
            "cost_coverage": cost_pivot.get("cost_coverage"),
        }


    @staticmethod
    def daily_cost(pivot):
        return [
            {
                "date": row["x"],
                "claude_cost": row["values"].get("claude-code"),
                "codex_cost": row["values"].get("codex"),
            }
            for row in pivot["rows"]
        ]


    def top_projects(self, time_range, limit):
        pivot = self.rollups.query_pivot(
            "project", "none", "cost", time_range=time_range
        )
        return [
            {"project": row["x"], "cost_usd": row["values"].get("value"),
             "cost_coverage": row.get("cost_coverage", {}).get("value")}
            for row in pivot["rows"]
            if row["values"].get("value") is not None
        ][:limit]


    def model_mix(self, time_range):
        pivot = self.rollups.query_pivot(
            "model", "none", "total", time_range=time_range
        )
        rows = [
            {"model": row["x"], "tokens": row["values"].get("value") or 0}
            for row in pivot["rows"]
        ]
        grand_total = sum(row["tokens"] for row in rows) or 1
        for row in rows:
            row["pct"] = row["tokens"] / grand_total
        return rows
