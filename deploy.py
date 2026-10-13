#!/usr/bin/env python3
"""Run the existing installer only from the published main checkout."""
import argparse
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent


class PreflightError(Exception):
    pass


def git(*args):
    try:
        result = subprocess.run(
            ["git", "-C", str(ROOT), *args], capture_output=True, text=True,
            stdin=subprocess.DEVNULL, timeout=30,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        )
    except (OSError, subprocess.TimeoutExpired):
        raise PreflightError("Git check unavailable or timed out; inspect Git access and retry.") from None
    if result.returncode:
        # Git diagnostics may contain credentials embedded in a remote URL.
        raise PreflightError("Git check failed; inspect repository state and origin access, then retry.")
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
    if git("status", "--porcelain=v1", "--untracked-files=no"):
        raise PreflightError("Tracked changes present; preserve WIP and finish development in its own worktree.")
    # Runtime state may be an untracked symlink. Other untracked source can alter installation.
    extras = git("ls-files", "--others", "--exclude-standard", "-z").split("\0")
    if any(p and p != "state" and not p.startswith("state/") for p in extras):
        raise PreflightError("Untracked files present outside state; inspect and preserve them before deployment.")
    return git("rev-parse", "HEAD")


def preflight(expected=None):
    head = check_local()
    if expected is not None and expected != head:
        raise PreflightError("Checkout differs from the release SHA; fast-forward to the intended published release.")
    git("remote", "get-url", "origin")
    rows = git("ls-remote", "--exit-code", "origin", "refs/heads/main").splitlines()
    if len(rows) != 1 or rows[0].split() != [head, "refs/heads/main"]:
        raise PreflightError("HEAD differs from live origin/main; publish approved work or fetch and fast-forward first.")
    # Detect a cooperative checkout change during the network round-trip.
    if check_local() != head:
        raise PreflightError("Checkout changed during preflight; coordinate writers and retry.")
    return head


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expect", help="full release commit SHA; must also equal live origin/main")
    parser.add_argument("--check-only", action="store_true", help="check source identity without installing")
    parser.add_argument("installer_args", nargs=argparse.REMAINDER, help="arguments after -- go to install.sh")
    args = parser.parse_args()
    installer_args = args.installer_args
    if installer_args[:1] == ["--"]:
        installer_args = installer_args[1:]
    try:
        head = preflight(args.expect)
    except PreflightError as error:
        print(f"{ROOT.name} deployment blocked: {error} Installer was not started.", file=sys.stderr)
        return 1
    print(f"{ROOT.name} source verified: main = origin/main = {head}", flush=True)
    if args.check_only:
        print("Source check only; installation and service health were not checked.")
        return 0
    print("Starting install.sh; verify installed runtime and consumer health after installation.", flush=True)
    os.execv(str(ROOT / "install.sh"), [str(ROOT / "install.sh"), *installer_args])


if __name__ == "__main__":
    raise SystemExit(main())
