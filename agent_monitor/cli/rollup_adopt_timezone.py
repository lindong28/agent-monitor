"""CLI adapter extracted from the public agent-monitor command."""


def main():
    import argparse

    import agent_monitor.accounting.rollup as rollup

    parser = argparse.ArgumentParser(
        prog="agent-monitor rollup adopt-timezone",
        description="Explicitly adopt an unmarked +08:00 legacy rollup as Asia/Shanghai.",
    )
    parser.add_argument("--db", required=True, help="exact legacy rollup database to adopt")
    parser.add_argument("--known-utc-offset", required=True, choices=["+08:00"])
    parser.add_argument(
        "--raw-log-start",
        action="append",
        required=True,
        metavar="AGENT=YYYY-MM-DD",
        help="earliest reconstructible raw-log date for one agent; repeat per agent",
    )
    parser.add_argument(
        "--authorize-pre-log-assumption",
        action="store_true",
        help=(
            "authorize only history before each agent's raw-log start; "
            "does not cover in-range counter mismatches"
        ),
    )
    parser.add_argument(
        "--authorize-non-exact-overlap-assumption",
        action="store_true",
        help=(
            "separately authorize in-range buckets whose protected counters differ; "
            "cost attribution remains unverified"
        ),
    )
    args = parser.parse_args()

    if rollup.normalize_db_path(args.db) != rollup.normalize_db_path(rollup.DEFAULT_DB_PATH):
        parser.error(
            "--db must be this checkout's canonical owning-host rollup: %s"
            % rollup.DEFAULT_DB_PATH
        )

    raw_log_starts = {}
    for item in args.raw_log_start:
        if "=" not in item:
            parser.error("--raw-log-start must use AGENT=YYYY-MM-DD")
        agent, start = item.split("=", 1)
        if not agent or agent in raw_log_starts:
            parser.error("--raw-log-start agents must be non-empty and unique")
        raw_log_starts[agent] = start

    # The source snapshot is fully materialized before the adoption transaction.
    entries = rollup.load_all_entries(read_only=True)
    result = rollup.adopt_bucket_timezone(
        args.db,
        entries,
        known_utc_offset=args.known_utc_offset,
        raw_log_starts=raw_log_starts,
        authorize_pre_log_assumption=args.authorize_pre_log_assumption,
        authorize_non_exact_overlap_assumption=(
            args.authorize_non_exact_overlap_assumption
        ),
    )
    overlap = result["evidence"]["reconstructible_overlap"]
    keys = overlap["bucket_keys"]
    window = overlap["comparison_window"]
    growth = overlap["post_rollup_source_growth"]
    protected = overlap["protected_counter_verification"]
    non_equal = overlap["non_equal_protected_counter_assumption"]
    cost = overlap["cost_attribution_verification"]
    history = result["evidence"]["pre_log_history"]
    print("Rollup timezone adoption: ADOPTED")
    print("Database: %s" % args.db)
    print("Bucket boundary: Asia/Shanghai (known machine fact: UTC+08:00)")
    print(
        "Reconstructible overlap bucket keys: expected=%d matched=%d missing=%d"
        % (keys["expected"], keys["matched"], keys["missing"])
    )
    print(
        "Comparison window: through %s inclusive; source_entries=%d bucket_keys=%d."
        % (
            window["end_inclusive"],
            window["source_entries_in_scope"],
            window["source_bucket_keys_in_scope"],
        )
    )
    print(
        "Post-rollup source growth: outside comparison; source_entries=%d bucket_keys=%d "
        "missing_from_database=%d."
        % (
            growth["source_entries"],
            growth["source_bucket_keys"],
            growth["bucket_keys_missing_from_database"],
        )
    )
    print(
        "The window ends at the last recorded rollup start, not at a source snapshot: "
        "records appended while that rollup was reading can already be in the database, "
        "and their bucketing was not verified here."
    )
    print(
        "A later rollup picks up a missing key only when its date falls in the recompute "
        "window or is absent from the database; older dates already present need an "
        "explicit backfill."
    )
    print(
        "Protected-counter equality: expected=%d equal=%d status=%s"
        % (
            protected["equal_bucket_keys"]["expected"],
            protected["equal_bucket_keys"]["actual"],
            protected["status"],
        )
    )
    print(
        "Cost attribution verification: %s; fields=%s."
        % (cost["status"], ",".join(cost["fields"]))
    )
    print(
        "Non-equal protected-counter overlap: bucket_keys=%d status=%s."
        % (non_equal["bucket_keys"], non_equal["status"])
    )
    print(
        "Pre-log history: %s onward is recorded as a user-authorized historical assumption, not verified fact."
        % history["start_date"]
    )
    print("Result: the explicit marker was written; ordinary readers and writers never perform this adoption automatically.")


if __name__ == "__main__":
    main()
