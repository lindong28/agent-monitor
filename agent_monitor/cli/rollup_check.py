"""CLI adapter extracted from the public agent-monitor command."""


def main():
    import json
    import sys

    import agent_monitor.accounting.rollup as rollup

    args = sys.argv[1:]
    if args not in ([], ["--json"]):
        print("usage: agent-monitor rollup --check [--json]", file=sys.stderr)
        raise SystemExit(2)

    result = rollup.check_rollup(
        rollup.DEFAULT_DB_PATH,
        lambda: rollup.load_all_entries(read_only=True, diagnostic=True),
    )
    if args == ["--json"]:
        print(json.dumps(result, sort_keys=True))
        raise SystemExit(0)

    status = result["status"]
    if status == "safe":
        if result.get("usage_rollup_schema") == 2:
            if result.get("legacy_fallback_bucket_count"):
                print(
                    "Rollup check status: SAFE — both rollup tables and the source scan "
                    "were complete; {count} legacy date/agent/model fallback bucket(s) remain.".format(
                        count=result["legacy_fallback_bucket_count"]
                    )
                )
            else:
                print("Rollup check status: SAFE — both rollup tables and the source scan were complete.")
        else:
            print(
                "Rollup check status: SAFE — the legacy project rollup and source scan "
                "were complete; total usage is still project-derived."
            )
        print("Action: none.")
    elif status == "attention":
        print(
            "Rollup check status: ATTENTION — some stored buckets cannot be safely replaced "
            "or project attribution is blocked."
        )
        print(
            "Action: inspect the would_skip and identity-blocker details below, restore the missing "
            "source or project identity when applicable, then rerun 'agent-monitor rollup --check'."
        )
    else:
        print("Rollup check status: INDETERMINATE — a complete consistent comparison was not available.")
        print(
            "Action: resolve the database, snapshot, permission, or source-scan errors below, "
            "then rerun 'agent-monitor rollup --check'; do not treat this result as healthy."
        )
    verdict = result["verdict"]
    if verdict == "unknown":
        print("Shrink-guard verdict: unknown (no complete database/source comparison).")
    elif verdict == "safe":
        if result.get("usage_rollup_schema") == 2:
            print("Shrink-guard verdict: safe for the project and total-usage buckets that were compared.")
        else:
            print("Shrink-guard verdict: safe for the legacy project buckets that were compared.")
    else:
        print("Shrink-guard verdict: attention; protected fields would shrink.")
    span = result["db_span"]
    print(
        "Project rollup: {rows} row(s), span {start}..{end}".format(
            rows=span["rows"],
            start=span["start"] or "none",
            end=span["end"] or "none",
        )
    )
    print(
        "Total-usage rollup: schema={schema}, basis={basis}".format(
            schema=result.get("usage_rollup_schema") or "unknown",
            basis=result.get("usage_totals_basis") or "unknown",
        )
    )
    if result["source_errors"]:
        print("Source scan errors: {count}; comparison is incomplete.".format(count=len(result["source_errors"])))
        for error in result["source_errors"]:
            print("  {path}: {error}".format(path=error.get("path") or "unknown", error=error["error"]))
    for error in result["diagnostic_errors"]:
        print("Diagnostic error ({stage}): {error}".format(**error))
    window = result["window"]
    print("Recompute window: {start}..{end} ({days} days, inclusive)".format(**window))
    def orphan_disposition(rows):
        removed = sum(item.get("action") == "remove" for item in rows["items"])
        return "{preserved} preserved; {removed} removed by authoritative replay".format(
            preserved=rows["count"] - removed, removed=removed)

    print(
        "Project dry run: orphan_rows={orphan} ({disposition}), would_skip={skip}, would_write={write}".format(
            orphan=result["orphan_rows"]["count"],
            disposition=orphan_disposition(result["orphan_rows"]),
            skip=result["would_skip"]["count"],
            write=result["would_write"]["count"],
        )
    )
    if result.get("usage_rollup_schema") == 2:
        print(
            "Total-usage dry run: orphan_rows={orphan} ({disposition}), "
            "would_skip={skip}, would_write={write}".format(
                orphan=result["usage_orphan_rows"]["count"],
                disposition=orphan_disposition(result["usage_orphan_rows"]),
                skip=result["usage_would_skip"]["count"],
                write=result["usage_would_write"]["count"],
            )
        )
    else:
        print("Total-usage dry run: not available in legacy schema 1.")
    for item in result["orphan_rows"]["items"]:
        print("  orphan_row {key}: {action}".format(key="/".join(item["key"]),
              action="would remove within authoritative history" if item.get("action") == "remove"
              else "preserved outside authoritative history"))
    for item in result["would_skip"]["items"]:
        changes = ", ".join(
            "{field} {old}->{new}".format(
                field=field,
                old=item["old"][field],
                new=item["new"][field],
            )
            for field in item["decreased_fields"]
        )
        print(
            "  would_skip {key}: {changes}".format(
                key="/".join(item["key"]),
                changes=changes,
            )
        )
    for item in result["usage_orphan_rows"]["items"]:
        print(
            "  total_usage_orphan {key}: {action}".format(
                key="/".join(item["key"]),
                action="would remove within authoritative history" if item.get("action") == "remove"
                else "preserved outside authoritative history"
            )
        )
    for item in result["usage_would_skip"]["items"]:
        changes = ", ".join(
            "{field} {old}->{new}".format(
                field=field,
                old=item["old"][field],
                new=item["new"][field],
            )
            for field in item["decreased_fields"]
        )
        print(
            "  total_usage_would_skip {key}: {changes}".format(
                key="/".join(item["key"]),
                changes=changes,
            )
        )
    if result["sources_blocked"]:
        print(
            "Identity blockers: {count}; project attribution from these paths is paused, "
            "while agent/model totals still include this usage.".format(
                count=result["sources_blocked"]
            )
        )
        for blocker in result["blocked_sources"]:
            print("  {source_path}: {reason}".format(**blocker))
        print("Next step: run 'agent-monitor rollup blockers' for persistent status and available recovery guidance.")
    else:
        print("Identity blockers: 0; no action required.")


if __name__ == "__main__":
    main()
