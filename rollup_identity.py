#!/usr/bin/env python3
import argparse
import json
import shlex
import sys

import aggregators


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="agent-monitor rollup",
        description=(
            "Inspect persistent project-identity blockers and explicitly pin a resolved source. "
            "Blocked paths retain archived usage; their project attribution needs recovery. "
            "Recovery preserves historical rollup rows."
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser(
        "blockers",
        help="list persistent active blockers and available recovery guidance",
    )
    recover = subparsers.add_parser(
        "recover",
        help="pin a source after its current path or remote uniquely matches an existing identity",
    )
    recover.add_argument("--path", required=True, help="blocked source path")
    recover.add_argument(
        "--pin-existing",
        required=True,
        help="existing identity that must be directly and uniquely derivable from this source",
    )
    reviewed = subparsers.add_parser(
        "recover-reviewed",
        help="preview operator-reviewed project assignments; --apply writes identities only",
    )
    reviewed.add_argument("--manifest", required=True, help="reviewed version 1 assignment JSON")
    reviewed.add_argument("--apply", action="store_true", help="commit the validated batch of identities")
    args = parser.parse_args(argv)

    try:
        if args.command == "blockers":
            return _print_blockers()
        if args.command == "recover-reviewed":
            return _recover_reviewed(args)
        recovered = aggregators.pin_project_identity(args.path, args.pin_existing)
        print(
            "RECOVERED: pinned {source_path} to directly matched identity {project}; status={status}".format(
                source_path=recovered["source_path"],
                project=args.pin_existing,
                status=recovered["status"],
            )
        )
        return 0
    except aggregators.ProjectIdentityRecoveryError as exc:
        print("RECOVERY REFUSED: %s" % exc, file=sys.stderr)
        return 2


def _recover_reviewed(args):
    try:
        with open(args.manifest, encoding="utf-8") as handle:
            manifest = json.load(handle)
    except (OSError, ValueError) as exc:
        raise aggregators.ProjectIdentityRecoveryError(
            "Cannot read reviewed manifest; check --manifest JSON: %s" % exc
        ) from exc
    assignments = aggregators.recover_reviewed_project_identities(manifest, apply=args.apply)
    if args.apply:
        print("RECOVERED: committed %d reviewed project identities." % len(assignments))
    else:
        print("PREVIEW: %d reviewed project assignments validated; no data changed." % len(assignments))
    print("Scope: project identities only. Raw usage and historical rollup rows are unchanged.")
    print("Later normal refresh keeps existing history protections; this does not backfill all historical project totals.")
    for item in assignments:
        print("  %s -> %s" % (item["source_path"], item["project"]))
    if not args.apply:
        print("To commit this reviewed batch: agent-monitor rollup recover-reviewed --manifest %s --apply" % shlex.quote(args.manifest))
    return 0


def _print_blockers():
    blockers = aggregators.list_project_identity_blockers(status="active")
    if not blockers:
        print("OK: no active project identity blockers.")
        return 0

    print(
        "ATTENTION: %d ACTIVE project identity blocker(s). Existing rollup rows are preserved, "
        "archived usage is retained, but project attribution for these paths is blocked." % len(blockers)
    )
    for blocker in blockers:
        candidate = blocker["resolved_candidate"] or "unavailable"
        path_arg = shlex.quote(blocker["source_path"])
        print("")
        print("source_path: %s" % blocker["source_path"])
        print("reason: %s" % blocker["reason"])
        print("resolved_candidate: %s" % candidate)
        print("pin_candidate: %s" % (blocker["pin_candidate"] or "unavailable"))
        print("first_seen: %s" % blocker["first_seen"])
        print("last_seen: %s" % blocker["last_seen"])
        print("status: %s" % blocker["status"])
        print("Recovery never rewrites historical rollup rows.")
        if blocker["pin_candidate"]:
            print("Available recovery command:")
            pin_target = shlex.quote(blocker["pin_candidate"])
            print("  agent-monitor rollup recover --path %s --pin-existing %s" % (path_arg, pin_target))
        else:
            print("Automatic pinning is not possible; this blocker needs an explicitly reviewed assignment manifest.")
            print("Preview it with: agent-monitor rollup recover-reviewed --manifest <reviewed.json>")
            print("Migrating historical project keys is not supported by this tooling.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
