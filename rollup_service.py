"""Owned, bounded launchd lifecycle for the periodic collector.

Install stdout is an installer-ledger token; diagnostics use stderr.
No collector or provider request is run by this module.
"""
import argparse
import contextlib
import deployment_guard
import os
from pathlib import Path
import platform
import plistlib
import subprocess
import sys
import tempfile

import service_runtime

from project_paths import PROJECT_ROOT

ROOT = PROJECT_ROOT
LABEL = "com.agent-monitor.rollup"
LAUNCH_TIMEOUT = 15


def plist_path():
    return Path.home() / "Library/LaunchAgents" / (LABEL + ".plist")


def target():
    return f"gui/{os.getuid()}/{LABEL}"


def launch(*args):
    try:
        return subprocess.run(["launchctl", *args], capture_output=True,
                              text=True, timeout=LAUNCH_TIMEOUT)
    except subprocess.TimeoutExpired:
        raise ValueError(f"rollup schedule: launchctl {args[0]} timed out after {LAUNCH_TIMEOUT}s; "
                         "loaded state is unverified. Configuration and data remain; "
                         "run ./status.sh rollup-daemon before retrying.") from None
    except OSError as exc:
        raise ValueError(f"rollup schedule: cannot run launchctl {args[0]} (errno={exc.errno}); "
                         "state is unverified. Check macOS launchd availability; "
                         "no further changes attempted.") from None


def owned_definition(path):
    if not path.exists() and not path.is_symlink():
        return None
    try:
        value = plistlib.loads(path.read_bytes())
        argv = value.get("ProgramArguments") if isinstance(value, dict) else None
        if (isinstance(value, dict) and value.get("Label") == LABEL
                and isinstance(argv, list) and len(argv) == 2
                and argv[0] == str(ROOT / "agent-monitor") and isinstance(argv[1], str)
                and value.get("Program", argv[0]) == argv[0]):
            return value
    except (OSError, ValueError, TypeError):
        pass
    raise ValueError("rollup schedule: foreign or unknown startup definition preserved; "
                     "inspect its owning checkout before installing or uninstalling.")


def loaded_info(managed):
    result = launch("print", target())
    if result.returncode == 113:
        return None
    if result.returncode:
        raise ValueError(f"rollup schedule: loaded state unverified (launchctl print exit={result.returncode}); "
                         "job and configuration preserved. Check the macOS desktop login and "
                         f"launchctl print {target()} before retrying.")
    programs = [line.strip().partition(" = ")[2] for line in result.stdout.splitlines()
                if line.strip().startswith("program = ")]
    if (not managed or programs != [str(ROOT / "agent-monitor")]
            or service_runtime.arguments(result.stdout) != managed["ProgramArguments"]):
        raise ValueError("rollup schedule: foreign or unknown loaded job preserved; "
                         "inspect its owning checkout before installing or uninstalling.")
    fields = {}
    for line in result.stdout.splitlines():
        key, sep, value = line.strip().partition(" = ")
        if sep and key in ("pid", "runs", "last exit code", "run interval", "state"):
            fields[key] = value
    return fields


def definition(command, interval):
    if interval < 1 or not command:
        raise ValueError("rollup schedule: command must be nonempty and interval must be positive seconds.")
    log = str(ROOT / "state/rollup-daemon.log")
    return {"Label": LABEL, "ProgramArguments": [str(ROOT / "agent-monitor"), command],
            "RunAtLoad": True, "StartInterval": interval, "WorkingDirectory": str(ROOT),
            "StandardOutPath": log, "StandardErrorPath": log,
            "EnvironmentVariables": {"PYTHONPATH": str(ROOT)}}


def require_success(result, action, remaining):
    if result.returncode:
        raise ValueError(f"rollup schedule: launchctl {action} failed (exit={result.returncode}); "
                         f"{remaining} Run ./status.sh rollup-daemon before retrying.")


def install(command, interval):
    wanted = definition(command, interval)
    path = plist_path()
    old = owned_definition(path)
    loaded = loaded_info(old)
    if loaded is not None and old == wanted:
        print(f"rollup schedule unchanged: every {interval}s; collection result not checked. "
              "Use ./status.sh rollup-daemon.", file=sys.stderr)
        return "present"
    (ROOT / "state").mkdir(parents=True, exist_ok=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=LABEL + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            plistlib.dump(wanted, stream, sort_keys=False)
        if loaded is not None:
            require_success(launch("bootout", target()), "bootout", "Original configuration retained.")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    require_success(launch("bootstrap", f"gui/{os.getuid()}", str(path)), "bootstrap",
                    "Configuration saved; schedule availability is unverified.")
    if loaded_info(wanted) is None:
        raise ValueError("rollup schedule: configuration saved but job is not loaded after bootstrap; "
                         "log into the macOS desktop and rerun ./install.sh rollup-daemon.")
    print(f"rollup schedule loaded: every {interval}s; collection result not checked. "
          f"Use ./status.sh rollup-daemon; log={ROOT / 'state/rollup-daemon.log'}", file=sys.stderr)
    return "installed" if old is None else "updated"


def number(value):
    try:
        return int(value)
    except (ValueError, TypeError):
        return None


def status():
    path = plist_path()
    managed = owned_definition(path)
    info = loaded_info(managed)
    if info is None:
        if managed is None:
            print("rollup schedule: not installed; no automatic collection is scheduled. "
                  "To enable it, run ./install.sh rollup-daemon.")
            return 0
        print("rollup schedule: installed but not loaded; automatic collection is unavailable. "
              "Log into the macOS desktop and run ./install.sh rollup-daemon.")
        return 1
    pid = number(info.get("pid"))
    last_exit = number(info.get("last exit code"))
    runs = number(info.get("runs"))
    if pid is not None and pid > 0:
        message, result = "running; current collection result is unverified", 0
    elif last_exit is not None:
        message = "idle; last collection exited successfully" if last_exit == 0 else "idle; last collection failed"
        result = 0 if last_exit == 0 else 1
    elif runs == 0:
        message, result = "never run; collection result is unverified", 1
    else:
        message, result = "loaded; last collection result is unverified", 1
    if last_exit is not None and last_exit != 0:
        result = 1
        message += f"; previous exit={last_exit}. Inspect the collector log before retrying"
    elif result:
        message += "; check the collector log and rerun status after the scheduled run"
    print("rollup schedule: " + message + ".")
    print(f"  configured interval={managed.get('StartInterval', 'unknown')}s "
          f"loaded interval={info.get('run interval', 'unknown')} "
          f"pid={pid or 'not running'} last_exit={last_exit if last_exit is not None else 'unverified'}")
    print(f"  plist={path} log={ROOT / 'state/rollup-daemon.log'}")
    return result


def uninstall():
    path = plist_path()
    managed = owned_definition(path)
    loaded = loaded_info(managed)
    if loaded is not None:
        require_success(launch("bootout", target()), "bootout", "Original configuration retained.")
        if loaded_info(managed) is not None:
            raise ValueError("rollup schedule: job remains loaded after bootout; configuration retained. "
                             "Inspect ./status.sh rollup-daemon before retrying.")
    if managed:
        path.unlink()
    print("rollup schedule: owned integration removed or already absent; collected data, logs and locks retained.")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("install", "status", "uninstall"))
    parser.add_argument("--command", default="rollup")
    parser.add_argument("--interval", type=int, default=3600)
    args = parser.parse_args(argv)
    if platform.system() != "Darwin":
        message = "rollup schedule: not applicable on this platform (requires macOS launchd); no service changed."
        print(message, file=sys.stderr if args.action == "install" else sys.stdout)
        if args.action == "install":
            print("skipped")
        return 0
    try:
        guard = deployment_guard.mutation_guard(ROOT) if args.action in ('install', 'uninstall') else contextlib.nullcontext()
        with guard:
            if args.action == "install":
                print(install(args.command, args.interval))
                return 0
            return status() if args.action == "status" else uninstall()
    except (OSError, ValueError) as exc:
        print(str(exc) if isinstance(exc, ValueError) else
              f"rollup schedule: filesystem operation failed (errno={exc.errno}); "
              "service state unverified. Check ./status.sh rollup-daemon before retrying.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
