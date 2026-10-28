"""Read current-account quotas without generating a model turn.

Each provider runs in a bounded child process. Credentials stay on their owner
machine and only reach the provider through its existing authenticated client.
"""

import dataclasses
import hashlib
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

from agent_monitor.contracts.usage import RateLimits
from parsers.codex import select_codex_rate_limit
from parsers.accounts import quota_identity
from agent_monitor.network import network_policy
from agent_monitor.accounts import claude_oauth
from agent_monitor.accounts import upstream_controls
from agent_monitor import diagnostics
from agent_monitor.quota import quota_applicability
from agent_monitor.runtime.python_entrypoint import module_command

from project_paths import PROJECT_ROOT

ROOT = PROJECT_ROOT
TIMEOUT = 35


class RefreshError(RuntimeError):
    def __init__(self, message, *, failure=None):
        super().__init__(message)
        self.failure = diagnostics.safe_failure(failure)


@contextmanager
def _failure_stage(stage):
    try:
        yield
    except Exception as exc:
        exc.failure = diagnostics.failure_details(exc, stage=stage)
        raise


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


class NoSubscription(RefreshError):
    """The user confirmed this account has no quota-bearing subscription."""


def load_rate_limits(provider, account, *, probe_pause_id=None, recovery_account_id=None):
    if account is None:
        raise NotSignedIn("这台机器没有登录任何账号。")
    if recovery_account_id is not None and (provider != "claude" or recovery_account_id != account.account_id):
        raise RefreshError("恢复检查账号不一致，未发出请求。")
    if (provider == "claude" and probe_pause_id is None and recovery_account_id is None
            and quota_applicability.unavailable(quota_applicability.Store(ROOT / "state").get(provider, account.account_id))):
        raise NoSubscription("用户已确认此账号无有效 Claude Code 订阅；未请求配额。")
    try:
        options = {} if probe_pause_id is None else {"probe_pause_id": probe_pause_id}
        lease = upstream_controls.Controls(ROOT / "state/upstream_controls").acquire(provider, **options)
    except (upstream_controls.Paused, upstream_controls.Busy) as exc:
        failure = None
        if isinstance(exc, upstream_controls.Paused):
            state = upstream_controls.Controls(ROOT / "state/upstream_controls").read(provider)
            candidate = diagnostics.safe_failure((state.get("pause") or {}).get("failure"))
            expected = hashlib.sha256(quota_identity(provider, account.account_id, account.label).encode()).hexdigest()
            if candidate and candidate.get("account_fingerprint") == expected:
                failure = candidate
        raise RefreshError(str(exc), failure=failure) from None
    except OSError as exc:
        exc.failure = diagnostics.failure_details(exc, stage="admission")
        raise
    child = None
    parent_read = parent_write = None
    failure = None
    stage = "child_start"
    try:
        deadline = time.monotonic() + TIMEOUT
        parent_read, parent_write = os.pipe()
        child = subprocess.Popen(
            module_command("agent_monitor.quota.quota_refresh", provider, parent_read, deadline),
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, start_new_session=True, pass_fds=(parent_read,),
            env=network_policy.child_environment(os.environ),
        )
        os.close(parent_read)
        parent_read = None
        stage = "child_result"
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
                raise kind(payload.get("error") or "配额读取失败，请核对后解除本地暂停。",
                           failure=diagnostics.safe_failure(payload.get("failure")))
            reading = RateLimits(**payload["reading"])
            return dataclasses.replace(reading, quota_observation={
                "source": "active_query", "observed_at": reading.updated_at})
        except (ValueError, KeyError, TypeError):
            raise RefreshError("配额读取返回了无效结果，请核对后解除本地暂停。",
                               failure={"stage": stage, "kind": "invalid_result"}) from None
    except network_policy.NetworkPolicyError as exc:
        raise LocalUnavailable(str(exc), failure={"stage": stage, "kind": exc.kind}) from None
    except (NotSignedIn, LocalUnavailable):
        raise
    except BaseException as exc:
        failure = exc
        facts = diagnostics.failure_details(exc, stage=stage,
                    kind="timeout" if isinstance(exc, subprocess.TimeoutExpired) else "exception",
                    returncode=child.returncode if child is not None else None)
        facts["account_fingerprint"] = hashlib.sha256(
            quota_identity(provider, account.account_id, account.label).encode()).hexdigest()
        try:
            facts["reader_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
        except OSError:
            pass  # The original error remains usable without source identity.
        save_error = None
        if child is not None:
            try:
                lease.pause("quotaRefreshFailed", failure=facts)
            except OSError as error:
                save_error = diagnostics.failure_summary(diagnostics.failure_details(error, stage="pause_save"))
        if not isinstance(exc, (RefreshError, subprocess.TimeoutExpired)):
            exc.failure = facts
            if save_error:
                exc.diagnostic_save_error = save_error
            raise
        message = str(exc) if isinstance(exc, RefreshError) else "配额请求失败。"
        if child is not None:
            message += "；" + upstream_controls.pause_summary(lease.state["pause"])
        else:
            message += " 原始诊断：" + diagnostics.failure_summary(facts)
        if save_error:
            message += "；暂停诊断保存失败：" + save_error
        raise RefreshError(message, failure=facts) from None
    finally:
        for fd in (parent_read, parent_write):
            if fd is not None:
                os.close(fd)
        try:
            lease.close(completed=child is None or child.returncode is not None)
        except OSError as exc:
            if failure is None:
                raise RefreshError("配额读取结束，但本地状态保存失败：" +
                    diagnostics.failure_summary(diagnostics.failure_details(exc, stage="cleanup"))) from None
            # Preserve the first failure; an unclosed active record still stops
            # the next request. Immediate safe diagnostics expose this write loss.
            diagnostics.monitor.record_failure("cleanup", exc)


@contextmanager
def _reader_lifetime(parent_fd, deadline):
    """Child-entry guard; stdin EOF is unrelated to its parent's lifetime.

    The monitor needs Python scheduling. It does not cover SIGSTOP, arbitrary
    GIL starvation, or descendants that deliberately leave this owned group.
    """
    stopped = threading.Event()
    cancelled = threading.Event()
    reason = ["配额读取的父进程已退出。"]
    failure = {"stage": "child_result", "kind": "process_exit"}

    def cancel(_signum, _frame):
        if not stopped.is_set():
            cancelled.set()
            raise RefreshError(reason[0], failure=failure)

    signal.signal(signal.SIGTERM, cancel)

    def watch():
        try:
            while not stopped.is_set():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    reason[0] = "配额请求超时，请重新刷新。"
                    failure.update(kind="timeout")
                    break
                ready, _, _ = select.select([parent_fd], [], [], remaining)
                if not ready:
                    reason[0] = "配额请求超时，请重新刷新。"
                    failure.update(kind="timeout")
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
                print(json.dumps({"error": reason[0], "failure": failure}), flush=True)
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
        raise RefreshError("服务商没有返回任何配额窗口，请重新刷新。",
                           failure={"stage": "quota_parse", "kind": "missing_windows"})
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
            with _failure_stage("profile_read"):
                with helper._OPENER.open(request, timeout=helper.TIMEOUT_S) as response:
                    profile = json.load(response)
            if (profile.get("account") or {}).get("uuid") != account["account_id"]:
                continue
            with _failure_stage("quota_read"):
                payload = helper.fetch(token)
        except urllib.error.HTTPError as exc:
            raise RefreshError("Claude 配额请求失败（HTTP %s）；已停止，不会换凭据重试。" % exc.code,
                               failure=diagnostics.failure_details(exc, kind="http_error")) from None

        def window(name):
            value = payload.get(name) or {}
            return _number(value.get("utilization")), helper.to_epoch(value.get("resets_at")) or None

        return _reading(window("five_hour"), window("seven_day"))
    if not attempted:
        raise NotSignedIn("这台机器上没有可用的 Claude 凭据。")
    raise RefreshError("Claude 返回的账号与当前账号不匹配；未采用配额读数。",
                       failure={"stage": "account_check", "kind": "account_mismatch"})


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


def _rpc_failure(error, request_id):
    facts = {"stage": "initialize" if request_id == 1 else "quota_read", "kind": "rpc_error"}
    if isinstance(error, dict):
        facts["rpc_code"] = error.get("code")
        message = error.get("message")
        if isinstance(message, str):
            try:
                body = json.loads(message.partition("body=")[2])
            except ValueError:
                body = None
            if isinstance(body, dict):
                facts["http_status"] = body.get("status")
                reason = body.get("error")
                if isinstance(reason, dict):
                    facts["upstream_code"] = reason.get("code")
                    facts["detail"] = reason.get("message")
            else:
                # Do not retain an unparsed response body or arbitrary headers.
                facts["detail"] = message.partition("body=")[0]
    return diagnostics.safe_failure(facts)


def account_fingerprint(provider, account):
    if account is None:
        return None
    return hashlib.sha256(quota_identity(provider, account.account_id, account.label).encode()).hexdigest()


def _codex(account):
    try:
        env = network_policy.child_environment(os.environ)
    except network_policy.NetworkPolicyError as exc:
        raise LocalUnavailable(str(exc)) from None
    # SSH/launchd do not load shell rc files. Supply installed user binary paths
    # to the existing shim as well as to its backend lookup.
    env["PATH"] = os.pathsep.join([str(Path.home() / ".local/bin"),
                                  "/opt/homebrew/bin", "/usr/local/bin", env.get("PATH", "")])
    shim = Path.home() / ".local/libexec/agent-shims/codex"
    command = str(shim) if shim.is_file() else shutil.which("codex", path=env["PATH"])
    if not command:
        raise LocalUnavailable("Codex CLI 不可用，请在这台机器上安装后刷新。")
    try:
        process = subprocess.Popen([command, "app-server", "--listen", "stdio://",
                                    "-c", "features.respect_system_proxy=false"],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.DEVNULL, env=env)
    except OSError as exc:
        raise LocalUnavailable("Codex CLI 无法启动，请检查这台机器上的安装；" +
                               diagnostics.failure_summary(diagnostics.failure_details(exc, stage="child_start"))) from None
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
                        raise RefreshError(_codex_rpc_error(message["error"], request_id),
                                           failure=_rpc_failure(message["error"], request_id))
                    return message["result"]
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not selector.select(remaining):
                raise RefreshError("Codex 配额请求超时，请重新刷新。",
                                   failure={"kind": "timeout", "timeout_seconds": 28})
            chunk = os.read(process.stdout.fileno(), 65536)
            if not chunk:
                raise RefreshError("Codex 配额读取进程退出，请检查 CLI 安装与登录状态。",
                                   failure={"kind": "process_exit", "returncode": process.poll()})
            buffer += chunk

    try:
        with _failure_stage("initialize"):
            send({"id": 1, "method": "initialize", "params": {
                "clientInfo": {"name": "agent_monitor", "version": "1"}}})
            receive(1)
        with _failure_stage("quota_read"):
            send({"method": "initialized"})
            send({"id": 2, "method": "account/rateLimits/read"})
            result = receive(2)
        reported_account = result.get("accountId")
        if reported_account is None:
            # A null accountId is permitted by the current protocol.
            raise RefreshError("Codex 配额响应未提供账号，无法核验读数归属；请稍后重试。",
                               failure={"stage": "account_check", "kind": "account_missing"})
        if reported_account != account["account_id"]:
            # A different account really is a sign-in mismatch: whatever this
            # machine is signed in as now, it is not the credential we read.
            raise RefreshError("Codex 当前登录的账号与这台机器的凭据不是同一个。",
                               failure={"stage": "account_check", "kind": "account_mismatch"})
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
                          "local_unavailable": isinstance(exc, LocalUnavailable),
                          "failure": diagnostics.failure_details(exc)}))
        sys.exit(1)
    except Exception as exc:
        # External errors can contain request headers or provider payloads.
        print(json.dumps({"error": "配额请求失败，请检查这台机器的网络与登录状态后刷新。",
                          "failure": diagnostics.failure_details(exc)}))
        sys.exit(1)
