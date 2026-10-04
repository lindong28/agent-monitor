"""Read current-account quotas without generating a model turn.

Each provider runs in a bounded child process. Credentials stay on their owner
machine and only reach the provider through its existing authenticated client.
"""

import dataclasses
import json
import math
import os
import selectors
import shutil
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from parsers import RateLimits
import claude_oauth

ROOT = Path(__file__).resolve().parent
TIMEOUT = 35


class RefreshError(RuntimeError):
    pass


class NotSignedIn(RefreshError):
    """This machine has no usable sign-in for the provider.

    Not a failure. A fleet does not have to sign every machine into every
    provider — a build host that only runs Codex, or a server that runs
    neither, is a configuration, not a fault. Callers publish it as a state
    and report nothing to fix; only the errors that a person could act on
    (timeouts, a missing CLI, an HTTP error from a reachable endpoint) stay
    RefreshError.

    Both ways to have no usable sign-in land here, because they are the same
    fact from the reader's side: no credential file at all, and a credential
    the provider no longer accepts.
    """


def load_rate_limits(provider, account):
    if account is None:
        raise NotSignedIn("这台机器没有登录任何账号。")
    child = subprocess.Popen(
        [sys.executable, str(Path(__file__).resolve()), provider],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        text=True, start_new_session=True,
    )
    try:
        output, _ = child.communicate(json.dumps(dataclasses.asdict(account)), timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        os.killpg(child.pid, signal.SIGKILL)
        child.communicate()
        raise RefreshError("配额请求超时，请重新刷新。") from None
    try:
        payload = json.loads(output)
        if child.returncode or payload.get("error"):
            # The child reports which of the two it was; the distinction cannot be
            # recovered from the message text, and guessing it by matching strings
            # would turn every future wording change into a silent reclassification.
            kind = NotSignedIn if payload.get("signed_out") is True else RefreshError
            raise kind(payload.get("error") or "配额读取失败，请重新刷新。")
        return RateLimits(**payload["reading"])
    except (ValueError, KeyError, TypeError):
        raise RefreshError("配额读取返回了无效结果，请更新 agent-monitor 后重试。") from None


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return value


def _reading(five, seven, *, plan=None):
    five_pct, five_reset = five
    seven_pct, seven_reset = seven
    if five_pct is None and seven_pct is None:
        raise RefreshError("服务商没有返回任何配额窗口，请重新刷新。")
    return RateLimits(five_pct, five_reset, seven_pct, seven_reset,
                      updated_at=datetime.now(timezone.utc).isoformat(), plan=plan)


def _claude(account):
    helper = claude_oauth
    for token in helper.read_tokens():
        try:
            request = urllib.request.Request(
                "https://api.anthropic.com/api/oauth/profile",
                headers={"Authorization": "Bearer " + token,
                         "anthropic-beta": helper.OAUTH_BETA, "Accept": "application/json"},
            )
            with helper._OPENER.open(request, timeout=helper.TIMEOUT_S) as response:
                profile = json.load(response)
            if (profile.get("account") or {}).get("uuid") != account["account_id"]:
                continue
            payload = helper.fetch(token)
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                continue
            raise RefreshError("Claude 配额请求失败（HTTP %s），请重新刷新。" % exc.code) from None

        def window(name):
            value = payload.get(name) or {}
            return _number(value.get("utilization")), helper.to_epoch(value.get("resets_at")) or None

        return _reading(window("five_hour"), window("seven_day"))
    raise NotSignedIn("这台机器上没有与当前账号匹配的可用 Claude 凭据。")


def _codex(account):
    env = os.environ.copy()
    # SSH/launchd do not load shell rc files. Supply installed user binary paths
    # to the existing shim as well as to its backend lookup.
    env["PATH"] = os.pathsep.join([str(Path.home() / ".local/bin"),
                                  "/opt/homebrew/bin", "/usr/local/bin", env.get("PATH", "")])
    shim = Path.home() / ".local/libexec/agent-shims/codex"
    command = str(shim) if shim.is_file() else shutil.which("codex", path=env["PATH"])
    if not command:
        raise RefreshError("Codex CLI 不可用，请在这台机器上安装后刷新。")
    process = subprocess.Popen([command, "app-server", "--listen", "stdio://"],
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.DEVNULL, env=env)
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    buffer = b""
    deadline = time.monotonic() + 28

    def send(message):
        process.stdin.write((json.dumps(message) + "\n").encode())
        process.stdin.flush()

    def receive(request_id):
        nonlocal buffer
        while True:
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                message = json.loads(line)
                if message.get("id") == request_id:
                    if "error" in message:
                        # Not classified as a sign-in problem. This helper also
                        # carries the `initialize` handshake, and a CLI that is
                        # too old to know the method answers with an error here
                        # while being perfectly signed in — calling that "not
                        # signed in" would retire the one alarm that tells the
                        # reader to update it.
                        raise RefreshError("Codex 拒绝了配额请求，请更新这台机器上的 CLI 后重试。")
                    return message["result"]
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not selector.select(remaining):
                raise RefreshError("Codex 配额请求超时，请重新刷新。")
            chunk = os.read(process.stdout.fileno(), 65536)
            if not chunk:
                raise RefreshError("Codex 配额读取进程退出，请检查 CLI 安装与登录状态。")
            buffer += chunk

    try:
        send({"id": 1, "method": "initialize", "params": {
            "clientInfo": {"name": "agent_monitor", "version": "1"}}})
        receive(1)
        send({"method": "initialized"})
        send({"id": 2, "method": "account/rateLimits/read"})
        result = receive(2)
        reported_account = result.get("accountId")
        if reported_account is None:
            # No account field at all is a CLI that predates it, not a machine
            # that is signed out. The two want opposite things from the reader:
            # one needs an update, the other needs nothing.
            raise RefreshError("Codex 没有随配额返回账号，请更新这台机器上的 CLI 后重试。")
        if reported_account != account["account_id"]:
            # A different account really is a sign-in mismatch: whatever this
            # machine is signed in as now, it is not the credential we read.
            raise NotSignedIn("Codex 当前登录的账号与这台机器的凭据不是同一个。")
        limit = (result.get("rateLimitsByLimitId") or {}).get("codex") or result.get("rateLimits") or {}
        windows = [limit.get(key) or {} for key in ("primary", "secondary")]

        def window(minutes):
            value = next((w for w in windows if w.get("windowDurationMins") == minutes), {})
            return _number(value.get("usedPercent")), _number(value.get("resetsAt"))

        plan = limit.get("planType")
        return _reading(window(300), window(10080), plan=plan if isinstance(plan, str) else None)
    finally:
        selector.close()
        process.terminate()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        process.stdin.close()
        process.stdout.close()


if __name__ == "__main__":
    try:
        reader = {"claude": _claude, "codex": _codex}[sys.argv[1]]
        reading = reader(json.load(sys.stdin))
        print(json.dumps({"reading": dataclasses.asdict(reading)}))
    except RefreshError as exc:
        # `signed_out` is the class, carried as data because the parent runs in a
        # different process and only sees this line.
        print(json.dumps({"error": str(exc), "signed_out": isinstance(exc, NotSignedIn)}))
        sys.exit(1)
    except Exception:
        # External errors can contain request headers or provider payloads.
        print(json.dumps({"error": "配额请求失败，请检查这台机器的网络与登录状态后刷新。"}))
        sys.exit(1)
