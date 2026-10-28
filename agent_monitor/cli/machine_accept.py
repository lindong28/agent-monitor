"""CLI adapter extracted from the public agent-monitor command."""


def main():
    import argparse
    import sys

    import agent_monitor.snapshots.machine_config as machine_config
    import agent_monitor.snapshots.sync as sync

    parser = argparse.ArgumentParser(
        prog="agent-monitor machines accept",
        description=(
            "Bind a declared machine to whatever currently answers on its SSH "
            "target. Needed once per machine before its usage can be pulled."
        ),
    )
    parser.add_argument("name", help="machine name from machines.json")
    parser.add_argument("--yes", action="store_true", help="skip the confirmation")
    args = parser.parse_args()

    try:
        config = machine_config.load_machine_config()
    except machine_config.MachineConfigError as exc:
        print("Cannot read machine config: %s" % exc, file=sys.stderr)
        raise SystemExit(2)

    machine = config.by_name.get(args.name)
    if machine is None:
        print(
            "Unknown machine %r; declared: %s"
            % (args.name, ", ".join(sorted(config.by_name))),
            file=sys.stderr,
        )
        raise SystemExit(2)
    if machine.is_self:
        print("%s is this machine; it needs no SSH binding." % machine.name)
        raise SystemExit(0)

    print("Machine name:  %s" % machine.name)
    print("SSH target:    %s" % machine.ssh_host)
    print()
    print(
        "Accepting binds this slot to whichever machine answers on that SSH "
        "target right now."
    )
    print(
        "It guarantees later syncs reach the same machine. It does not verify that "
        "the target is the machine you have in mind -- only your SSH config decides "
        "that, so confirm it points where you expect before accepting."
    )
    if not args.yes:
        print()
        try:
            reply = input("Bind %s to %s? [y/N] " % (machine.name, machine.ssh_host))
        except EOFError:
            reply = ""
        if reply.strip().lower() not in ("y", "yes"):
            print("Not accepted; no binding was written.")
            raise SystemExit(1)

    try:
        current = sync.sync_machine(machine, accept_first_use_ssh_target=True)
    except Exception as exc:
        print("Sync failed: %s" % exc, file=sys.stderr)
        raise SystemExit(1)

    print()
    print("Bound and pulled.")
    print("  source host identity: %s" % current.meta["source_host_identity"])
    print(
        "  snapshot physical rows: %d, schema v%d, data since %s"
        % (
            current.meta["row_count"],
            current.meta["schema_version"],
            current.meta["data_start_date"],
        )
    )
    if current.meta["schema_version"] == 2:
        print(
            "  row split: project=%d, usage=%d"
            % (current.meta["project_row_count"], current.meta["usage_row_count"])
        )
        print("  full-history totals basis: %s" % current.meta["metric_totals_basis"])
        if current.meta["legacy_fallback_bucket_count"]:
            print(
                "  legacy fallback: %d date/agent/model bucket(s)"
                % current.meta["legacy_fallback_bucket_count"]
            )
    else:
        print("  full-history totals basis: legacy_project_derived")
    print("Later syncs refuse if that identity changes.")


if __name__ == "__main__":
    main()
