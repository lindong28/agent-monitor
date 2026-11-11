#!/usr/bin/env python3
"""Run the existing installer only from the published main checkout."""
import argparse
import os
from pathlib import Path
import subprocess
import sys
from agent_monitor.runtime import deployment_guard

from agent_monitor.paths import PROJECT_ROOT

ROOT = PROJECT_ROOT


class PreflightError(ValueError):
    pass


def git(*args):
    try:
        result = subprocess.run(
            ["git", "-C", str(ROOT), *args], capture_output=True, text=True,
            stdin=subprocess.DEVNULL, timeout=30,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        )
    except subprocess.TimeoutExpired:
        raise PreflightError(f"Git {args[0]} timed out after 30 seconds; inspect repository access before retrying.") from None
    except OSError:
        raise PreflightError(f"Git {args[0]} could not start; inspect the installed Git executable.") from None
    if result.returncode:
        # Git diagnostics may contain credentials embedded in a remote URL.
        raise PreflightError(f"Git {args[0]} exited {result.returncode}; inspect repository state and origin access before retrying.")
    return result.stdout.rstrip("\n")


def check_local():
    if Path(git("rev-parse", "--show-toplevel")).resolve() != ROOT:
        raise PreflightError("The installer must belong to this repository root.")
    gd = Path(git("rev-parse", "--absolute-git-dir")).resolve()
    common = Path(git("rev-parse", "--path-format=absolute", "--git-common-dir")).resolve()
    if gd != common:
        raise PreflightError("Development worktree: use the primary main checkout for deployment.")
    for name in ("rebase-merge", "rebase-apply", "MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD", "sequencer", "BISECT_START"):
        if (gd / name).exists():
            raise PreflightError("Git operation in progress; resolve it with its owner before deployment.")
    if git("symbolic-ref", "--quiet", "HEAD") != "refs/heads/main":
        raise PreflightError("Deployment requires the main branch; inspect the checkout before switching.")
    entries = git("ls-files", "-v", "-z").split("\0")
    # Git -v uses lowercase tags for assume-unchanged and S/s for skip-worktree.
    if any(entry and (entry[0].islower() or entry[0] == "S") for entry in entries):
        raise PreflightError("Hidden index flags present (assume-unchanged or skip-worktree); inspect git ls-files -v, preserve local edits and clear the flags before retrying.")
    if git("status", "--porcelain=v1", "--untracked-files=no"):
        raise PreflightError("Tracked changes present; preserve WIP and finish development in its own worktree.")
    # Runtime state may be an untracked symlink. Other untracked source can alter installation.
    extras = git("ls-files", "--others", "--exclude-standard", "-z").split("\0")
    if any(p and p != "state" and not p.startswith("state/") for p in extras):
        raise PreflightError("Untracked files present outside state; inspect and preserve them before deployment.")
    return git("rev-parse", "HEAD")


def preflight(expected=None):
    head = check_local()
    from agent_monitor.runtime.rollout_node import check_reservation
    try:
        check_reservation(ROOT, head)
    except ValueError as error:
        raise PreflightError(str(error)) from None
    if expected is not None and expected != head:
        raise PreflightError("Checkout differs from the release SHA; fast-forward to the intended published release.")
    git("remote", "get-url", "origin")
    rows = git("ls-remote", "--exit-code", "origin", "refs/heads/main").splitlines()
    if len(rows) != 1 or len(rows[0].split()) != 2 or rows[0].split()[1] != "refs/heads/main":
        raise PreflightError("Live origin/main identity is unverified.")
    published = rows[0].split()[0]
    if published != head:
        if expected is None:
            raise PreflightError("HEAD differs from live origin/main; publish approved work or fetch and fast-forward first.")
        try:
            git("merge-base", "--is-ancestor", head, published)
        except PreflightError:
            raise PreflightError("Fixed release is not verified in live origin/main history; fetch origin and verify its ancestry before retrying.") from None
    # Detect a cooperative checkout change during the network round-trip.
    if check_local() != head:
        raise PreflightError("Checkout changed during preflight; coordinate writers and retry.")
    return head


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--new", action="store_true", help="start a new topology release; refuse unfinished releases (requires --expect)")
    parser.add_argument("--expect", help="full published release SHA; remains fixed if origin/main advances")
    parser.add_argument("--check-only", action="store_true", help="check source identity without installing")
    parser.add_argument("--local", action="store_true", help="repair only this node; not a complete topology deployment")
    parser.add_argument("--rollout-id", help=argparse.SUPPRESS)
    parser.add_argument("installer_args", nargs=argparse.REMAINDER, help="arguments after -- go to install.sh")
    args = parser.parse_args()
    installer_args = args.installer_args
    if installer_args[:1] == ["--"]:
        installer_args = installer_args[1:]
    try:
        from agent_monitor.runtime import rollout
        if args.new and args.local:
            raise PreflightError("--new cannot be combined with --local.")
        if not args.local:
            return rollout.run(args.expect, check_only=args.check_only, installer_args=installer_args, new_only=args.new)
        def admission():
            head = preflight(args.expect)
            rollout.check_reservation(ROOT, head, args.rollout_id)
            return head
        if not args.check_only:
            return deployment_guard.run_install(ROOT, installer_args,
                                                preflight=admission)
        head = admission()
    except (PreflightError, ValueError) as error:
        print(f"{ROOT.name} deployment blocked: {error} Installer was not started.", file=sys.stderr)
        return 1
    except (deployment_guard.GuardError, OSError, subprocess.TimeoutExpired) as error:
        detail = str(error) if isinstance(error, deployment_guard.GuardError) else "Local installation failed or timed out; inspect permissions and service status before retrying."
        print(f"{ROOT.name} deployment failed: {detail}", file=sys.stderr)
        return 1
    print(f"{ROOT.name} local source verified: published release {head}", flush=True)
    print("Source check only; installation and service health were not checked. Other topology nodes were not deployed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
