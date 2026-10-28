"""CLI adapter extracted from the public agent-monitor command."""


def main():
    import agent_monitor.accounting.rollup as rollup

    result = rollup.run()
    print(
        "agent-monitor rollup updated: {buckets_written} project bucket(s), "
        "{usage_buckets_written} total-usage bucket(s) over {days_recomputed} days; "
        "last_rollup_ts={last_rollup_ts}".format(
            **result
        )
    )
    if result.get("sources_blocked"):
        print(
            "ATTENTION: {sources_blocked} source path(s) are blocked; project rows were preserved "
            "and new usage from those paths was not attributed to a project. The agent/model totals "
            "still include this usage. Run 'agent-monitor rollup blockers' for persistent status and "
            "available recovery guidance.".format(
                **result
            )
        )


if __name__ == "__main__":
    main()
