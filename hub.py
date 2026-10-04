"""Opt-in hub deployment; collection and API execution remain local to each Mac."""

import argparse
import fcntl
import json
import os
import plistlib
import re
import subprocess
import sys
import time
import uuid
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "hub.json"
MACHINE_PATH = ROOT / "state" / "hub-machine"
WEB_LABEL = "com.agent-monitor.hub"
QUOTA_REQUEST_TIMEOUT = 120
QUOTA_KICK_INTERVAL = 2
QUOTA_POLL_INTERVAL = 0.1


class QuotaRequestError(RuntimeError):
    pass


def _quota_message(path):
    try:
        value = json.loads(path.read_text())
        return value if isinstance(value, dict) else {}
    except (FileNotFoundError, ValueError):
        return {}


def _write_quota_message(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    staging = path.with_name(path.name + "." + uuid.uuid4().hex)
    try:
        with staging.open("x") as handle:
            os.chmod(staging, 0o600)
            json.dump(value, handle, allow_nan=False)
            handle.flush()
            os.fsync(handle.fileno())
        staging.replace(path)
    finally:
        staging.unlink(missing_ok=True)


def _pending_quota_nonce():
    state = ROOT / "state"
    nonce = _quota_message(state / "quota-refresh-request.json").get("nonce")
    if not isinstance(nonce, str) or not re.fullmatch(r"[0-9a-f]{32}", nonce):
        return None
    if _quota_message(state / "quota-refresh-receipt.json").get("nonce") == nonce:
        return None
    return nonce


def _kick_quota_collector(deadline):
    target = "gui/%s/com.agent-monitor.rollup" % os.getuid()

    def run(*args):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise QuotaRequestError("GUI 配额刷新超时，请重新刷新。")
        return subprocess.run(["launchctl", *args, target], stdout=subprocess.PIPE,
                              stderr=subprocess.DEVNULL, text=True, timeout=min(10, remaining))

    loaded = run("print")
    if loaded.returncode:
        raise QuotaRequestError("GUI 配额采集器不可用，请登录这台 Mac 并检查采集器安装。")
    arguments = []
    in_arguments = False
    for line in loaded.stdout.splitlines():
        line = line.strip()
        if line == "arguments = {":
            in_arguments = True
        elif in_arguments:
            if line == "}":
                break
            arguments.append(line)
    if arguments != [str(ROOT / "agent-monitor"), "collect"]:
        raise QuotaRequestError("已加载的 GUI 配额采集器属于另一条命令，请重新运行本 checkout 的安装脚本。")
    kicked = run("kickstart")
    if kicked.returncode:
        raise QuotaRequestError("GUI 配额采集器无法启动，请检查这台 Mac 的登录会话后重新刷新。")


def request_quota_refresh(*, timeout=QUOTA_REQUEST_TIMEOUT):
    """Serialize SSH requests, leaving the collector and rollup lock independent."""
    deadline = time.monotonic() + timeout
    state = ROOT / "state"
    request = state / "quota-refresh-request.json"
    receipt = state / "quota-refresh-receipt.json"
    nonce = uuid.uuid4().hex
    try:
        state.mkdir(parents=True, exist_ok=True)
        with (state / "quota-refresh-request.lock").open("a") as lock:
            while True:
                if time.monotonic() >= deadline:
                    raise QuotaRequestError("GUI 配额刷新在等待另一个请求时超时，请重新刷新。")
                try:
                    fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    time.sleep(min(QUOTA_POLL_INTERVAL, max(0, deadline - time.monotonic())))
            try:
                _write_quota_message(request, {"nonce": nonce})
                next_kick = 0
                while time.monotonic() < deadline:
                    acknowledgement = _quota_message(receipt)
                    limits = acknowledgement.get("rate_limits")
                    if (acknowledgement.get("nonce") == nonce and isinstance(limits, dict)
                            and set(limits) == {"claude", "codex"}
                            and all(isinstance(block, dict) for block in limits.values())
                            and time.monotonic() < deadline):
                        return limits
                    if time.monotonic() >= next_kick:
                        _kick_quota_collector(deadline)
                        next_kick = time.monotonic() + QUOTA_KICK_INTERVAL
                    time.sleep(min(QUOTA_POLL_INTERVAL, max(0, deadline - time.monotonic())))
                raise QuotaRequestError("GUI 配额刷新超时，请重新刷新。")
            finally:
                if _quota_message(request).get("nonce") == nonce:
                    request.unlink(missing_ok=True)
    except (OSError, subprocess.SubprocessError) as exc:
        raise QuotaRequestError("GUI 配额刷新未能完成，请检查这台 Mac 的采集器后重新刷新。") from exc


def configuration(path=None):
    data = json.loads(Path(path or CONFIG_PATH).read_text())
    fields = {"schema_version", "hub", "url", "port", "sync_interval_seconds",
              "quota_interval_seconds", "machines"}
    if not isinstance(data, dict) or set(data) != fields or data["schema_version"] != 1:
        raise ValueError("unsupported hub configuration")
    names = data["machines"]
    if not isinstance(names, list) or not names or any(
        not isinstance(n, str) or not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", n) for n in names
    ) or len(set(names)) != len(names) or data["hub"] not in names:
        raise ValueError("hub machines must be unique machine names containing the hub")
    for key in ("port", "sync_interval_seconds", "quota_interval_seconds"):
        if type(data[key]) is not int or data[key] < 1:
            raise ValueError("%s must be a positive integer" % key)
    url = urlparse(data["url"])
    if (data["port"] > 65535 or url.scheme not in {"http", "https"} or not url.hostname
            or url.username or url.password or url.query or url.fragment or url.path not in {"", "/"}):
        raise ValueError("hub URL must be an HTTP origin without credentials")
    if url.scheme != "http" or (url.port or 80) != data["port"]:
        raise ValueError("hub URL must use HTTP and the configured listening port")
    return data


def local_machine():
    if not MACHINE_PATH.exists():
        return None
    name = MACHINE_PATH.read_text().strip()
    if name not in configuration()["machines"]:
        raise ValueError("installed hub machine is absent from hub.json")
    return name


def enabled():
    return local_machine() is not None


def is_hub():
    return enabled() and local_machine() == configuration()["hub"]


def origin():
    return configuration()["url"].rstrip("/") if enabled() else None


def interval():
    return configuration()["sync_interval_seconds"] if enabled() else 600


def collect():
    """One bounded scheduled job. No dependency on the central server or SSH."""
    import rollup

    result = rollup.run()
    stamp = ROOT / "state" / "quota-collection-at"
    try:
        last = float(stamp.read_text())
    except (FileNotFoundError, ValueError):
        last = 0
    quota_due = time.time() - last >= configuration()["quota_interval_seconds"]
    nonce = _pending_quota_nonce()
    quota_due = quota_due or nonce is not None
    if quota_due:
        import exporter
        # Provider refresh has its own deadlines and persists its observed state.
        limits = exporter._rate_limits(refresh=True)
        if nonce is not None:
            _write_quota_message(ROOT / "state" / "quota-refresh-receipt.json",
                                 {"nonce": nonce, "rate_limits": limits})
        stamp.write_text(str(time.time()))
    print("agent-monitor collection: local statistics saved; Quota %s. Central transfer is independent."
          % ("queried" if quota_due else "using its hourly schedule"))
    return result


def web_plist(root, python, *, ssh_auth_sock=None, path=None):
    root = Path(root)
    env = {"PYTHONPATH": str(root), "PATH": path or os.defpath}
    env["LLM_GATEWAY_ROOT"] = str(Path(os.environ.get("LLM_GATEWAY_ROOT", Path.home() / "research/llm-gateway")).expanduser().resolve())
    socket = Path(ssh_auth_sock or "")
    # Apple creates this socket for each login session. Pinning it in a plist
    # overrides launchd's fresh environment after a reboot. Custom agents keep
    # their explicit path (for example, the stable 1Password agent socket).
    apple_session_socket = (
        socket.name == "Listeners"
        and socket.parent.name.startswith("com.apple.launchd.")
        and str(socket.parent.parent) in ("/var/run", "/private/var/run", "/tmp", "/private/tmp")
    )
    if ssh_auth_sock and not apple_session_socket:
        env["SSH_AUTH_SOCK"] = ssh_auth_sock
    return {
        "Label": WEB_LABEL,
        "ProgramArguments": [str(python), str(root / "hub.py"), "serve"],
        "WorkingDirectory": str(root), "RunAtLoad": True, "KeepAlive": True,
        "ThrottleInterval": 10,
        "EnvironmentVariables": env,
        "StandardOutPath": str(root / "state" / "hub.log"),
        "StandardErrorPath": str(root / "state" / "hub.log"),
    }


def install_web():
    if not is_hub():
        print("agent-monitor hub: client configured; local collection stays active.")
        return
    plist_dir = Path.home() / "Library" / "LaunchAgents"
    dest = plist_dir / (WEB_LABEL + ".plist")
    plist = web_plist(ROOT, sys.executable, ssh_auth_sock=os.environ.get("SSH_AUTH_SOCK"),
                      path=os.environ.get("PATH"))
    domain = "gui/%s" % os.getuid()
    previous = plistlib.loads(dest.read_bytes()) if dest.exists() else None
    if previous and previous.get("ProgramArguments", [])[1:2] != [str(ROOT / "hub.py")]:
        raise ValueError("hub launchd job belongs to another checkout; incumbent preserved")
    loaded = subprocess.run(["launchctl", "print", domain + "/" + WEB_LABEL],
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, timeout=10)
    if loaded.returncode == 0:
        # A matching on-disk owner alone does not establish the loaded job's owner.
        if str(ROOT / "hub.py") not in {line.strip() for line in loaded.stdout.splitlines()}:
            raise ValueError("loaded hub job owner differs; incumbent preserved")
        if previous == plist:
            print("agent-monitor hub: existing launchd service unchanged; reachability not checked.")
            return
        subprocess.run(["launchctl", "bootout", domain + "/" + WEB_LABEL], check=True, timeout=15)
    plist_dir.mkdir(parents=True, exist_ok=True)
    candidate = dest.with_suffix(".plist.new")
    candidate.write_bytes(plistlib.dumps(plist))
    candidate.chmod(0o600)
    candidate.replace(dest)
    subprocess.run(["launchctl", "bootstrap", domain, str(dest)], check=True, timeout=15)
    print("agent-monitor hub: launchd service loaded; browser reachability and source admission still need verification.")


def stop_web(*, uninstall=False):
    dest = Path.home() / "Library" / "LaunchAgents" / (WEB_LABEL + ".plist")
    previous = plistlib.loads(dest.read_bytes()) if dest.exists() else None
    if previous and previous.get("ProgramArguments", [])[1:2] != [str(ROOT / "hub.py")]:
        raise ValueError("hub launchd job belongs to another checkout; incumbent preserved")
    target = "gui/%s/%s" % (os.getuid(), WEB_LABEL)
    loaded = subprocess.run(["launchctl", "print", target], stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, text=True, timeout=10)
    if loaded.returncode != 0:
        if not uninstall:
            raise ValueError("hub launchd job is not loaded; no service stopped")
        if loaded.returncode != 113:
            raise ValueError("cannot inspect loaded hub job; startup definition preserved")
    else:
        if str(ROOT / "hub.py") not in {line.strip() for line in loaded.stdout.splitlines()}:
            raise ValueError("loaded hub job owner differs; incumbent preserved")
        subprocess.run(["launchctl", "bootout", target], check=True, timeout=15)
    if uninstall:
        dest.unlink(missing_ok=True)
        print("agent-monitor hub: launchd service removed. Local collectors and saved data are preserved.")
    else:
        print("agent-monitor hub: stopped. Local collectors and saved data remain available.")


def serve():
    if not is_hub():
        raise ValueError("only the configured hub may run the central service")
    import server
    cfg = configuration()
    sys.argv = [str(ROOT / "server.py"), "--host", "0.0.0.0", "--port", str(cfg["port"])]
    server.main()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["configure", "install-web", "stop-web", "uninstall-web", "serve", "collect", "url", "interval", "role"])
    parser.add_argument("machine", nargs="?")
    args = parser.parse_args()
    try:
        if args.command == "configure":
            if args.machine not in configuration()["machines"]:
                raise ValueError("configure requires a machine listed in hub.json")
            MACHINE_PATH.parent.mkdir(parents=True, exist_ok=True)
            MACHINE_PATH.write_text(args.machine + "\n")
            print("agent-monitor hub: %s configured; services have not yet been installed." % args.machine)
        elif args.command == "install-web":
            install_web()
        elif args.command == "stop-web":
            stop_web()
        elif args.command == "uninstall-web":
            stop_web(uninstall=True)
        elif args.command == "serve":
            serve()
        elif args.command == "collect":
            collect()
        elif args.command == "url":
            print(origin() or "")
        elif args.command == "interval":
            print(interval())
        elif args.command == "role":
            print("hub" if is_hub() else "client" if enabled() else "standalone")
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print("agent-monitor hub: failed — %s. Check hub.json and rerun the hub installer." % exc, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
