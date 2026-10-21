"""Read current-account quotas without generating a model turn.

Each provider runs in a bounded child process. Credentials stay on their owner
machine and only reach the provider through its existing authenticated client.
"""

import dataclasses
import json
import math
import os
import select
import selectors
import shutil
import signal
import subprocess
import sys
import time
import threading
from contextlib import contextmanager
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from parsers import RateLimits
from parsers.codex import select_codex_rate_limit
import claude_oauth
import upstream_controls

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

    Upstream credential rejection remains a failure; it must not silently
    become an ordinary local sign-out or permit trying another credential.
    """


class LocalUnavailable(RefreshError):
    """A local prerequisite is missing; no upstream attempt was made."""


def load_rate_limits(provider, account):
    if account is None:
        raise NotSignedIn("这台机器没有登录任何账号。")
    try:
        lease = upstream_controls.Controls(ROOT / "state/upstream_controls").acquire(provider)
    except (upstream_controls.Paused, upstream_controls.Busy) as exc:
        raise RefreshError(str(exc)) from None
    child = None
    parent_read = parent_write = None
    try:
        deadline = time.monotonic() + TIMEOUT
        parent_read, parent_write = os.pipe()
        child = subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), provider, str(parent_read), str(deadline)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, start_new_session=True, pass_fds=(parent_read,),
        )
        os.close(parent_read)
        parent_read = None
        try:
            # The lease remains held through the bounded child's cleanup.
            output, _ = child.communicate(json.dumps(dataclasses.asdict(account)),
                                          timeout=max(0, deadline - time.monotonic()) + 2)
        except BaseException:
            try:
                if child.returncode is None:
                    os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            child.communicate()
            raise
        try:
            payload = json.loads(output)
            if not isinstance(payload, dict):
                raise ValueError()
            if child.returncode or payload.get("error"):
                kind = (NotSignedIn if payload.get("signed_out") is True else
                        LocalUnavailable if payload.get("local_unavailable") is True else RefreshError)
                raise kind(payload.get("error") or "配额读取失败，请核对后解除本地暂停。")
            return RateLimits(**payload["reading"])
        except (ValueError, KeyError, TypeError):
            raise RefreshError("配额读取返回了无效结果，请核对后解除本地暂停。") from None
    except (NotSignedIn, LocalUnavailable):
        raise
    except BaseException as exc:
        if child is not None:
            lease.pause("quotaRefreshFailed")
        if isinstance(exc, subprocess.TimeoutExpired):
            raise RefreshError("配额请求超时，本机已暂停该服务的后续请求。") from None
        raise
    finally:
        for fd in (parent_read, parent_write):
            if fd is not None:
                os.close(fd)
        lease.close(completed=child is None or child.returncode is not None)


@contextmanager
def _reader_lifetime(parent_fd, deadline):
    """Child-entry guard; stdin EOF is unrelated to its parent's lifetime.

    The monitor needs Python scheduling. It does not cover SIGSTOP, arbitrary
    GIL starvation, or descendants that deliberately leave this owned group.
    """
    stopped = threading.Event()
    cancelled = threading.Event()
    reason = ["配额读取的父进程已退出。"]

    def cancel(_signum, _frame):
        if not stopped.is_set():
            cancelled.set()
            raise RefreshError(reason[0])

    signal.signal(signal.SIGTERM, cancel)

    def watch():
        try:
            while not stopped.is_set():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    reason[0] = "配额请求超时，请重新刷新。"
                    break
                ready, _, _ = select.select([parent_fd], [], [], remaining)
                if not ready:
                    reason[0] = "配额请求超时，请重新刷新。"
                    break
                if not os.read(parent_fd, 1):
                    break
            if stopped.is_set():
                return
            os.kill(os.getpid(), signal.SIGTERM)
            if not stopped.wait(2) and os.getpgrp() == os.getpid():
                # This live process is the group leader: no reaped PID reuse.
                os.killpg(os.getpid(), signal.SIGKILL)
        except OSError:
            # The normal exit path closes this read end after marking stopped.
            if not stopped.is_set():
                os.kill(os.getpid(), signal.SIGTERM)

    watcher = threading.Thread(target=watch, name="quota-parent-lifetime", daemon=True)
    watcher.start()
    try:
        yield
    finally:
        stopped.set()
        os.close(parent_fd)
        if cancelled.is_set() and os.getpgrp() == os.getpid():
            # The reader normally reaps its helper in finally. Also cover an
            # interruption during helper creation, before that finally exists.
            try:
                print(json.dumps({"error": reason[0]}), flush=True)
            except (BrokenPipeError, OSError):
                pass
            os.killpg(os.getpid(), signal.SIGKILL)


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
    attempted = False
    for token in helper.read_tokens():
        attempted = True
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
            raise RefreshError("Claude 配额请求失败（HTTP %s）；已停止，不会换凭据重试。" % exc.code) from None

        def window(name):
            value = payload.get(name) or {}
            return _number(value.get("utilization")), helper.to_epoch(value.get("resets_at")) or None

        return _reading(window("five_hour"), window("seven_day"))
    if not attempted:
        raise NotSignedIn("这台机器上没有可用的 Claude 凭据。")
    raise RefreshError("Claude 返回的账号与当前账号不匹配；未采用配额读数。")


def _codex_rpc_error(error, request_id):
    # Never expose upstream free text: it can contain URLs and credentials.
    if isinstance(error, dict):
        if error.get("code") == -32601:
            return "Codex CLI 不支持配额查询接口，请更新这台机器上的 CLI 后重试。"
        message = error.get("message")
        if request_id == 2 and isinstance(message, str):
            # The CLI embeds the provider's JSON body in its RPC error message.
            try:
                body = json.loads(message.partition("body=")[2])
            except ValueError:
                body = None
            if isinstance(body, dict) and body.get("status") == 401:
                detail = body.get("error")
                if isinstance(detail, dict) and detail.get("code") == "token_revoked":
                    return "Codex 登录凭据已被撤销，无法刷新配额；请在这台机器上运行 codex login 重新登录后重试。"
    return "Codex 配额请求失败，请检查这台机器的网络与登录状态后重试。"


def _codex(account):
    env = os.environ.copy()
    # SSH/launchd do not load shell rc files. Supply installed user binary paths
    # to the existing shim as well as to its backend lookup.
    env["PATH"] = os.pathsep.join([str(Path.home() / ".local/bin"),
                                  "/opt/homebrew/bin", "/usr/local/bin", env.get("PATH", "")])
    shim = Path.home() / ".local/libexec/agent-shims/codex"
    command = str(shim) if shim.is_file() else shutil.which("codex", path=env["PATH"])
    if not command:
        raise LocalUnavailable("Codex CLI 不可用，请在这台机器上安装后刷新。")
    try:
        process = subprocess.Popen([command, "app-server", "--listen", "stdio://"],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.DEVNULL, env=env)
    except OSError:
        raise LocalUnavailable("Codex CLI 无法启动，请检查这台机器上的安装。") from None
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
                        # Keep failures visible and preserve the prior reading.
                        raise RefreshError(_codex_rpc_error(message["error"], request_id))
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
            # A null accountId is permitted by the current protocol.
            raise RefreshError("Codex 配额响应未提供账号，无法核验读数归属；请稍后重试。")
        if reported_account != account["account_id"]:
            # A different account really is a sign-in mismatch: whatever this
            # machine is signed in as now, it is not the credential we read.
            raise RefreshError("Codex 当前登录的账号与这台机器的凭据不是同一个。")
        limit = select_codex_rate_limit(result) or {}
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
        with _reader_lifetime(int(sys.argv[2]), float(sys.argv[3])):
            reading = reader(json.load(sys.stdin))
        print(json.dumps({"reading": dataclasses.asdict(reading)}))
    except RefreshError as exc:
        # `signed_out` is the class, carried as data because the parent runs in a
        # different process and only sees this line.
        print(json.dumps({"error": str(exc), "signed_out": isinstance(exc, NotSignedIn),
                          "local_unavailable": isinstance(exc, LocalUnavailable)}))
        sys.exit(1)
    except Exception:
        # External errors can contain request headers or provider payloads.
        print(json.dumps({"error": "配额请求失败，请检查这台机器的网络与登录状态后刷新。"}))
        sys.exit(1)
