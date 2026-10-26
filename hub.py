"""Opt-in hub deployment; collection and API execution remain local to each Mac."""

import argparse
import contextlib
import deployment_guard
import fcntl
import json
import os
import plistlib
import re
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import urlparse
import service_runtime

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "hub.json"
_LOCAL_CONFIG_PATH = ROOT / "hub.local.json"
if _LOCAL_CONFIG_PATH.exists() or _LOCAL_CONFIG_PATH.is_symlink():
    CONFIG_PATH = _LOCAL_CONFIG_PATH
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


def _pending_quota_request():
    state = ROOT / "state"
    request = _quota_message(state / "quota-refresh-request-v2.json")
    version = "-v2" if request else ""
    if not request:
        request = _quota_message(state / "quota-refresh-request.json")
    nonce = request.get("nonce")
    if not isinstance(nonce, str) or not re.fullmatch(r"[0-9a-f]{32}", nonce):
        return None
    if _quota_message(state / ("quota-refresh-receipt" + version + ".json")).get("nonce") == nonce:
        return None
    from quota_recovery import validate_exclusions
    exclusions = request.get("blocked_accounts", {}) if version else {}
    validate_exclusions(exclusions)
    return {"nonce": nonce, "version": version, "blocked_accounts": exclusions}


def _pending_quota_nonce():
    request = _pending_quota_request()
    return request["nonce"] if request else None


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


def request_quota_refresh(*, timeout=QUOTA_REQUEST_TIMEOUT, blocked_accounts=None):
    """Serialize SSH requests, leaving the collector and rollup lock independent."""
    deadline = time.monotonic() + timeout
    state = ROOT / "state"
    # Old collectors must never execute a request whose exclusions they ignore.
    version = "-v2" if blocked_accounts else ""
    request = state / ("quota-refresh-request" + version + ".json")
    receipt = state / ("quota-refresh-receipt" + version + ".json")
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
                _write_quota_message(request, {"nonce": nonce, **({"blocked_accounts": blocked_accounts} if version else {})})
                next_kick = 0
                while time.monotonic() < deadline:
                    acknowledgement = _quota_message(receipt)
                    limits = acknowledgement.get("rate_limits")
                    if (acknowledgement.get("nonce") == nonce and isinstance(limits, dict)
                            and (not blocked_accounts or acknowledgement.get("blocked_accounts") == blocked_accounts)
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

    stamp = ROOT / "state" / "quota-collection-at"
    try:
        last = float(stamp.read_text())
    except (FileNotFoundError, ValueError):
        last = 0
    quota_due = time.time() - last >= configuration()["quota_interval_seconds"]
    stopping = threading.Event()
    queried = threading.Event()
    failures = []

    def query(request):
        import exporter
        # Capture the complete request once: timeout cleanup/replacement cannot
        # convert an already admitted restricted request into an unrestricted one.
        nonce = request["nonce"] if request else None
        version = request["version"] if request else ""
        exclusions = request["blocked_accounts"] if request else {}
        # Provider refresh has its own deadlines and persists its observed state.
        limits = exporter._rate_limits(refresh=True, **({"blocked_accounts": exclusions} if exclusions else {}))
        if nonce is not None:
            _write_quota_message(ROOT / "state" / ("quota-refresh-receipt" + version + ".json"),
                                 {"nonce": nonce, "rate_limits": limits, **({"blocked_accounts": exclusions} if version else {})})
        stamp.write_text(str(time.time()))
        queried.set()

    def serve_quota():
        try:
            request = _pending_quota_request()
            if quota_due or request is not None:
                query(request)
            while not stopping.wait(QUOTA_POLL_INTERVAL):
                request = _pending_quota_request()
                if request is not None:
                    query(request)
        except Exception as exc:
            failures.append(exc)

    # The collector owns this thread and joins it before exiting. Requests can
    # arrive while rollup holds its lock; quota reads do not require that lock.
    worker = threading.Thread(target=serve_quota, name="agent-monitor-gui-quota")
    worker.start()
    try:
        result = rollup.run()
    finally:
        stopping.set()
        worker.join()
    if failures:
        raise failures[0]
    print("agent-monitor collection: local statistics saved; Quota %s. Central transfer is independent."
          % ("queried" if queried.is_set() else "using its hourly schedule"))
    return result


def web_plist(root, python, *, ssh_auth_sock=None, path=None):
    root = Path(root)
    env = {"PYTHONPATH": str(root), "PATH": path or os.defpath,
           "AGENT_MONITOR_DEPLOYMENT_SIGNATURE": service_runtime.signature(root)}
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
        stop_web(uninstall=True)
        print("agent-monitor hub: client configured; local collection stays active.")
        return
    plist_dir = Path.home() / "Library" / "LaunchAgents"
    dest = plist_dir / (WEB_LABEL + ".plist")
    plist = web_plist(ROOT, sys.executable, ssh_auth_sock=os.environ.get("SSH_AUTH_SOCK"),
                      path=os.environ.get("PATH"))
    expected = plist["EnvironmentVariables"]["AGENT_MONITOR_DEPLOYMENT_SIGNATURE"]
    read_health = lambda: service_runtime.read_health(
        "http://127.0.0.1:%s/api/health?asset_watch=1" % configuration()["port"])
    domain = "gui/%s" % os.getuid()
    previous = plistlib.loads(dest.read_bytes()) if dest.exists() or dest.is_symlink() else None
    if previous and previous.get("ProgramArguments", [])[1:2] != [str(ROOT / "hub.py")]:
        raise ValueError("hub launchd job belongs to another checkout; incumbent preserved")
    loaded = subprocess.run(["launchctl", "print", domain + "/" + WEB_LABEL],
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, timeout=10)
    if loaded.returncode not in (0, 113):
        raise ValueError("cannot inspect loaded hub job; incumbent and startup definition preserved")
    if loaded.returncode == 0:
        # A matching on-disk owner alone does not establish the loaded job's owner.
        if service_runtime.arguments(loaded.stdout) != (previous or plist)["ProgramArguments"]:
            raise ValueError("loaded hub job owner differs; incumbent preserved")
        if previous == plist and service_runtime.matches(read_health(), ROOT, expected):
            print("agent-monitor hub: target source/configuration already serving; existing process retained.")
            return
        subprocess.run(["launchctl", "bootout", domain + "/" + WEB_LABEL], check=True, timeout=15)
    plist_dir.mkdir(parents=True, exist_ok=True)
    candidate = dest.with_suffix(".plist.new")
    candidate.write_bytes(plistlib.dumps(plist))
    candidate.chmod(0o600)
    candidate.replace(dest)
    for attempt in range(1, 4):
        try:
            subprocess.run(["launchctl", "bootstrap", domain, str(dest)], check=True, timeout=15)
            break
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            # A timeout has an uncertain outcome; do not blindly repeat it.
            if isinstance(exc, subprocess.CalledProcessError) and attempt < 3:
                time.sleep(1)
                continue
            prior = "Previous job was unloaded. " if loaded.returncode == 0 else ""
            raise RuntimeError(
                "agent-monitor hub: %sbootstrap did not confirm a replacement after %s attempt(s); "
                "service availability is unverified. Inspect 'launchctl print %s/%s' "
                "before retrying 'agent-monitor start'." % (prior, attempt, domain, WEB_LABEL)
            ) from exc
    service_runtime.wait_for_identity(read_health, ROOT, expected)
    print("agent-monitor hub: target source/configuration confirmed through local HTTP; browser and source admission still need verification.")


def stop_web(*, uninstall=False):
    dest = Path.home() / "Library" / "LaunchAgents" / (WEB_LABEL + ".plist")
    previous = plistlib.loads(dest.read_bytes()) if dest.exists() or dest.is_symlink() else None
    if previous and previous.get("ProgramArguments", [])[1:2] != [str(ROOT / "hub.py")]:
        raise ValueError("hub launchd job belongs to another checkout; incumbent preserved")
    target = "gui/%s/%s" % (os.getuid(), WEB_LABEL)
    loaded = subprocess.run(["launchctl", "print", target], stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, text=True, timeout=10)
    if loaded.returncode != 0:
        if loaded.returncode != 113:
            raise ValueError("cannot inspect loaded hub job; startup definition preserved")
    else:
        expected = previous["ProgramArguments"] if previous else [sys.executable, str(ROOT / "hub.py"), "serve"]
        if service_runtime.arguments(loaded.stdout) != expected:
            raise ValueError("loaded hub job owner differs; incumbent preserved")
        subprocess.run(["launchctl", "bootout", target], check=True, timeout=15)
    if uninstall:
        dest.unlink(missing_ok=True)
        print("agent-monitor hub: launchd service removed. Local collectors and saved data are preserved.")
    else:
        print("agent-monitor hub: stopped. Local collectors and saved data remain available.")


def status_web():
    dest = Path.home() / 'Library/LaunchAgents' / (WEB_LABEL + '.plist')
    previous = plistlib.loads(dest.read_bytes()) if dest.exists() or dest.is_symlink() else None
    expected_args = [sys.executable, str(ROOT / 'hub.py'), 'serve']
    if previous:
        expected_args = previous.get('ProgramArguments', [])
        if expected_args[1:] != [str(ROOT / 'hub.py'), 'serve']:
            raise ValueError('hub startup definition belongs to another checkout or is unrecognized')
    result = subprocess.run(['launchctl', 'print', 'gui/%s/%s' % (os.getuid(), WEB_LABEL)],
                            capture_output=True, text=True, timeout=10)
    if result.returncode == 113:
        print('agent-monitor hub: not loaded; dashboard availability is unverified. Run agent-monitor start to enable it.')
        return 1
    if result.returncode or service_runtime.arguments(result.stdout) != expected_args:
        raise ValueError('hub loaded ownership/state is unverified; inspect launchctl before changing it')
    health = service_runtime.read_health('http://127.0.0.1:%s/api/health?asset_watch=1' % configuration()['port'])
    if not service_runtime.matches(health, ROOT, service_runtime.signature(ROOT)):
        print('agent-monitor hub: loaded; target HTTP source/configuration is unverified. Inspect state/hub.log and rerun deployment.')
        return 1
    print('agent-monitor hub: loaded; local HTTP confirms the target source/configuration. Browser and collection results are not checked.')
    return 0


def serve():
    if not is_hub():
        raise ValueError("only the configured hub may run the central service")
    import server
    cfg = configuration()
    sys.argv = [str(ROOT / "server.py"), "--host", "0.0.0.0", "--port", str(cfg["port"])]
    server.main()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["configure", "install-web", "stop-web", "uninstall-web", "status-web", "serve", "collect", "url", "interval", "role"])
    parser.add_argument("machine", nargs="?")
    args = parser.parse_args()
    try:
        guard = deployment_guard.mutation_guard(ROOT) if args.command in ('configure', 'install-web', 'stop-web', 'uninstall-web') else contextlib.nullcontext()
        with guard:
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
            elif args.command == "status-web":
                return status_web()
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
        hint = ("Inspect state/rollup-daemon.log and agent-monitor service status; fix the reported collection failure before retrying."
                if args.command == "collect" else "Check hub.json and the affected service status before rerunning its management command.")
        print("agent-monitor hub: failed — %s. %s" % (exc, hint), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
