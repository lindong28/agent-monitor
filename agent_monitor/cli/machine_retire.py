"""CLI adapter extracted from the public agent-monitor command."""


def main():
    import argparse

    import agent_monitor.snapshots.generation as generation

    parser = argparse.ArgumentParser(
        prog="agent-monitor machines retire",
        description=(
            "Deleting a machine declaration only deactivates it and is reversible. "
            "Permanent retirement only happens through this command, which records "
            "irreversible state before updating machines.json."
        ),
    )
    parser.add_argument(
        "names",
        nargs="+",
        help="active or generation-backed deactivated remote machine name",
    )
    args = parser.parse_args()

    config = generation.retire_machines(args.names)
    print("Machine retirement: COMMITTED")
    print("Retired names: %s" % ", ".join(sorted(args.names)))
    print("Active machines remaining: %d" % len(config.machines))
    print("Result: retirement state was persisted before machines.json was updated; these names cannot be reused.")


if __name__ == "__main__":
    main()
