"""CLI adapter extracted from the public agent-monitor command."""


def main():
    import argparse
    import json
    import sys

    import agent_monitor.snapshots.exporter as exporter

    parser = argparse.ArgumentParser(prog="agent-monitor export")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--out", help="create an export bundle directory")
    mode.add_argument("--quota-only", action="store_true", help="refresh quota without exporting statistics")
    mode.add_argument(
        "--version",
        action="store_true",
        help="print the exporter commit without reading or refreshing rollup state",
    )
    parser.add_argument("--cached-quota", action="store_true", help="collect statistics without actively querying provider Quota")
    parser.add_argument("--gui-quota", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--quota-exclusions", help=argparse.SUPPRESS)
    args = parser.parse_args()

    try:
        if args.version:
            print(exporter.exporter_version())
        elif args.quota_only:
            exclusions = json.loads(args.quota_exclusions) if args.quota_exclusions else None
            from agent_monitor.quota.quota_recovery import validate_exclusions
            validate_exclusions(exclusions or {})
            print(json.dumps(exporter.export_quota(gui_quota=args.gui_quota, blocked_accounts=exclusions), sort_keys=True))
        else:
            manifest = exporter.export_bundle(output_path=args.out, quota_refresh=not args.cached_quota, gui_quota=args.gui_quota)
            print(json.dumps(manifest, sort_keys=True))
    except exporter.ExportError as exc:
        print("Export refused: %s" % exc, file=sys.stderr)
        raise SystemExit(2)


if __name__ == "__main__":
    main()
