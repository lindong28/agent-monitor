#!/usr/bin/env python3
"""Verify two date ranges through a fresh browser on the local Agent Monitor."""
import argparse
from datetime import date, datetime, timedelta, timezone
import json
import os
from pathlib import Path
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlsplit
from uuid import uuid4


PROJECT = Path(__file__).resolve().parents[1]
PROBE = Path(__file__).with_name("overview_release_probe.js")
BUCKET_ZONE = timezone(timedelta(hours=8))


class CheckError(Exception):
    pass


def local_origin(value):
    url = urlsplit(value)
    if (url.scheme != "http" or url.hostname not in ("127.0.0.1", "::1")
            or url.username is not None or url.password is not None
            or url.path not in ("", "/") or url.query or url.fragment or not url.port):
        raise ValueError("supply a numeric loopback HTTP origin with an explicit port")
    return f"http://{url.netloc}"


def report_range(range_name, observed, first_day, last_day):
    try:
        dates = [date.fromisoformat(item) for item in observed["dates"]]
        start, end = (date.fromisoformat(observed[key]) for key in ("start", "end"))
    except (KeyError, ValueError, TypeError) as exc:
        raise CheckError("invalid trend dates") from exc
    window = int(range_name[:-1])
    if (not dates or dates != sorted(set(dates)) or len(dates) > window
            or not first_day <= end <= last_day or start != end - timedelta(days=window - 1)
            or dates[0] < start or dates[-1] > end):
        raise CheckError(f"{range_name}: trend dates outside the selected window or out of order")
    return {"rows": len(dates), "first_date": dates[0].isoformat(), "last_date": dates[-1].isoformat()}


def process_identity(pid):
    # The CLI daemon detaches from its launcher. Pair its PID (from our private
    # socket directory) with the process start time and executable before signalling.
    result = subprocess.run(["/bin/ps", "-p", str(pid), "-o", "lstart=,comm="],
                            capture_output=True, timeout=3)
    return result.stdout.strip() if result.returncode == 0 else None


def reap_owned(pid, identity):
    for action in (None, signal.SIGTERM, signal.SIGKILL):
        if process_identity(pid) != identity:
            return
        if action:
            try:
                os.kill(pid, action)
            except ProcessLookupError:
                return
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            if process_identity(pid) != identity:
                return
            time.sleep(0.1)
    raise CheckError("private browser process did not exit")


def run_check(url, agent_browser, chrome, root, sockets):
    config = root / "config.json"
    config.write_text("{}")
    probe = root / "probe.js"
    probe.write_text("(" + PROBE.read_text() + ")(" + json.dumps(url) + ");")

    # The public CLI owns the browser when domain restrictions are active.
    # Capture only the process started by this private launcher.
    launcher = root / "chrome"
    browser_pid = root / "chrome.pid"
    launcher.write_text(
        "#!/bin/sh\n"
        "for arg do case \"$arg\" in --no-sandbox|--no-sandbox=*|--disable-setuid-sandbox|"
        "--disable-setuid-sandbox=*) exit 1;; esac; done\n"
        f"echo $$ > {shlex.quote(str(browser_pid))}\nexec {shlex.quote(chrome)} \"$@\"\n"
    )
    launcher.chmod(0o700)
    env = {name: os.environ[name] for name in ("PATH", "LANG") if name in os.environ}
    env.update(HOME=str(root), TMPDIR=str(root), XDG_CONFIG_HOME=str(root),
               AGENT_BROWSER_SOCKET_DIR=str(sockets), AGENT_BROWSER_IDLE_TIMEOUT_MS="45000",
               AGENT_BROWSER_DEFAULT_TIMEOUT="35000",
               AGENT_BROWSER_ARGS="--mute-audio,--use-mock-keychain,--password-store=basic,"
                                  "--disable-background-networking,--disable-component-update,--disable-sync")
    session = "overview-" + uuid4().hex[:10]
    pid_file = sockets / (session + ".pid")
    owned = []
    started = time.monotonic()
    cli = [agent_browser, "--session", session, "--config", str(config), "--executable-path",
           str(launcher), "--init-script", str(probe), "--allowed-domains", urlsplit(url).hostname, "--json"]
    try:
        def command(*args):
            remaining = max(0.1, 120 - (time.monotonic() - started))
            result = subprocess.run(cli + list(args), env=env, cwd=root, stdin=subprocess.DEVNULL,
                                    capture_output=True, timeout=min(40, remaining))
            if result.returncode:
                raise CheckError(f"agent-browser {args[0]} failed (exit {result.returncode})")
            try:
                message = json.loads(result.stdout)
                if message["success"]:
                    return message["data"]
            except (ValueError, KeyError):
                pass
            raise CheckError(f"agent-browser {args[0]} returned no valid result")

        def read(expression):
            return command("eval", expression).get("result")

        def observe(range_name, visible=False):
            predicate = f"window.releaseOverview?.result('{range_name}', {json.dumps(visible)})"
            # Let the page end the wait even for a stale table. This leaves the
            # daemon available for the final readout and orderly close.
            deadline_ms = int(time.time() * 1000) + 28000
            try:
                command("wait", "--fn", f"({predicate}) || Date.now() >= {deadline_ms}")
            except (CheckError, subprocess.TimeoutExpired) as exc:
                raise CheckError(f"{range_name}: did not render the requested API trend") from exc
            result = read(predicate)
            if not isinstance(result, dict) or "dates" not in result:
                reason = (result or {}).get("failure", "did not render the requested API trend")
                raise CheckError(f"{range_name}: {reason}")
            return result

        command("get", "url")
        try:
            for path in (browser_pid, pid_file):
                pid = int(path.read_text().strip())
                identity = process_identity(pid) if pid > 1 else None
                if not identity:
                    raise ValueError("missing process identity")
                owned.append((pid, identity))
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            raise CheckError("private browser process identity unavailable") from exc
        command("open", url + "/?range=7d")
        observe("7d")
        ranges = {}
        for selected in ("30d", "7d"):
            read("window.releaseOverview.reset(); true")
            first_day = datetime.now(BUCKET_ZONE).date()
            command("select", "#range", selected)
            if not read("document.querySelector('#daily-cost-data').open"):
                command("scrollintoview", "#daily-cost-data summary")
                command("click", "#daily-cost-data summary")
            observed = observe(selected, visible=True)
            ranges[selected] = report_range(selected, observed, first_day, datetime.now(BUCKET_ZONE).date())
        return {"status": "passed", "ranges": ranges}
    finally:
        errors = []
        if pid_file.exists():
            try:
                subprocess.run(cli + ["close"], env=env, cwd=root, stdin=subprocess.DEVNULL,
                               capture_output=True, timeout=8, check=True)
            except (subprocess.SubprocessError, OSError):
                errors.append("private browser session close failed")
        for pid, identity in owned:
            try:
                reap_owned(pid, identity)
            except (CheckError, subprocess.SubprocessError, OSError):
                errors.append("private process cleanup unverified")
        if errors:
            primary = sys.exc_info()[1]
            reason = str(primary) if isinstance(primary, CheckError) else "overview verification"
            raise CheckError("; ".join([reason, *errors]))



def cancel(_signum, _frame):
    raise CheckError("verification cancelled")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", type=local_origin, default="http://127.0.0.1:39001")
    parser.add_argument("--agent-browser", help="public agent-browser executable")
    parser.add_argument("--browser", help="Chrome or Chromium executable")
    args = parser.parse_args()
    pinned = PROJECT / "node_modules/.bin/agent-browser"
    cli = args.agent_browser or (str(pinned) if pinned.exists() else shutil.which("agent-browser"))
    chrome = args.browser or shutil.which("chromium") or shutil.which("google-chrome")
    if not chrome and sys.platform == "darwin":
        chrome = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    try:
        signal.signal(signal.SIGTERM, cancel)
        if not cli or not chrome:
            raise CheckError("agent-browser or Chrome missing; specify --agent-browser and --browser")
        # Disposable supervisor-owned state, including a fresh profile and socket namespace.
        with tempfile.TemporaryDirectory(prefix="am-overview-") as root, tempfile.TemporaryDirectory(
                prefix="am-sock-", dir="/tmp") as sockets:
            result = run_check(args.url, cli, chrome, Path(root), Path(sockets))
        print(json.dumps(result, separators=(",", ":")))
    except (CheckError, OSError, ValueError, subprocess.TimeoutExpired) as exc:
        # Raw tool responses can contain account data. Only expose our own checks.
        reason = str(exc) if isinstance(exc, CheckError) else type(exc).__name__
        print(json.dumps({"status": "failed", "reason": reason}), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
