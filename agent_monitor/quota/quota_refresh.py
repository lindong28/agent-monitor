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
from agent_monitor.network import network_policy, automatic_egress
from agent_monitor.accounts import claude_oauth
from agent_monitor.accounts import upstream_controls
from agent_monitor import diagnostics
from agent_monitor.quota import quota_applicability
from agent_monitor.runtime.python_entrypoint import module_command

from agent_monitor.paths import PROJECT_ROOT

ROOT = PROJECT_ROOT
TIMEOUT = 35
CODEX_RPC_SECONDS = 28


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


def _bridge_explainable_failure(detail):
    """Only transport failures can be replaced by a proven local bridge denial.

    Keep completed upstream/identity/parse outcomes even if a sibling revokes
    the batch before the parent consumes them. Unknown payloads remain failures.
    """
    if detail.get("http_status") or detail.get("upstream_code") or detail.get("stage") in {"account_check", "quota_parse"}:
        return False
    # Codex wraps transport failures as JSON-RPC internal errors. Protocol
    # errors (including unsupported methods) and unknown codes are not transport.
    return (detail.get("kind") in {"timeout", "process_exit"}
            or (detail.get("kind") == "rpc_error" and detail.get("rpc_code") == -32603)
            or detail.get("error_type") in {"URLError", "ConnectionError", "ConnectionResetError",
                                           "ConnectionAbortedError", "BrokenPipeError", "TimeoutError"})


def load_rate_limits(provider, account, *, codex_home=None, **options):
    if codex_home is None:
        return _load_rate_limits(provider, account, **options)
    import fcntl
    # Preserve the account-action reader's profile-parent operation lock. Keep it through child cleanup, in addition to provider admission.
    try:
        lock = (Path(codex_home).parent / ".lock").open("a")
    except OSError:
        raise LocalUnavailable("账号目录不可用，未开始查询。") from None
    with lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise LocalUnavailable("该账号已有操作，未开始查询。") from None
        return _load_rate_limits(provider, account, codex_home=codex_home, **options)


def _load_rate_limits(provider, account, *, probe_pause_id=None, recovery_account_id=None, admitted_probe=None,
                     codex_home=None, root=None, account_profile=None, renew_auth=False, pause_scope=None, automatic_batch=None):
    root = Path(root) if root is not None else ROOT
    if account is None:
        raise NotSignedIn("这台机器没有登录任何账号。")
    if recovery_account_id is not None and (provider != "claude" or recovery_account_id != account.account_id):
        raise RefreshError("恢复检查账号不一致，未发出请求。")
    if (provider == "claude" and probe_pause_id is None and recovery_account_id is None
            and quota_applicability.unavailable(quota_applicability.Store(root / "state").get(provider, account.account_id))):
        raise NoSubscription("用户已确认此账号无有效 Claude Code 订阅；未请求配额。")
    if provider == "codex":
        from agent_monitor.accounts import account_holds
        from parsers import accounts
        auth_path = Path(codex_home) / "auth.json" if codex_home else accounts.CODEX_AUTH
        if pause_scope == "account" and not renew_auth and account_holds.rejected_credential(root / "state", auth_path):
            raise LocalUnavailable("当前凭据仍是已拒绝的版本，请使用认证修复；未重查配额。")
        reason = account_holds.blocked(root / "state", account.account_id, account.label, auth_path=auth_path,
            probe_pause_id=probe_pause_id if pause_scope == "account" else None)
        if reason:
            raise LocalUnavailable(reason)
    try:
        options = {} if probe_pause_id is None else {"probe_pause_id": probe_pause_id}
        if pause_scope == "account":
            if provider != "codex" or not probe_pause_id or not (account_profile or "").startswith("monitor-"):
                raise LocalUnavailable("账号恢复范围无效，未发出请求。")
            lease = account_holds.acquire(root / "state", account.account_id, account.label, pause_id=probe_pause_id)
        else:
            lease = upstream_controls.Controls(root / "state/upstream_controls").acquire(provider, **options)
        if admitted_probe is not None and probe_pause_id is not None:
            admitted_probe.update(lease.state['pause']['probe_attempt'])
    except (upstream_controls.Paused, upstream_controls.Busy) as exc:
        failure = None
        if isinstance(exc, upstream_controls.Paused):
            state = upstream_controls.Controls(root / "state/upstream_controls").read(provider)
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
    bridge_explainable = False
    account_rejection = None
    try:
        auth_failure = None
        if renew_auth:
            from agent_monitor.quota import codex_quota
            first = (lease.state.get("pause") or {}).get("failure") or {}
            if (provider != "codex" or probe_pause_id is None or not admitted_probe
                    or not (account_profile or "").startswith("monitor-")
                    or first.get("account_profile") != account_profile
                    or first.get("account_fingerprint") != account_fingerprint(provider, account)
                    or not codex_quota.authentication_quota_failure(first)):
                raise LocalUnavailable("认证修复与当前暂停不匹配，未发出上游请求。")
            auth_failure = diagnostics.safe_failure(first)
        if provider == "codex":
            from agent_monitor.accounts import account_holds
            reason = account_holds.blocked(root / "state", account.account_id, account.label, auth_path=auth_path,
                probe_pause_id=probe_pause_id if pause_scope == "account" else None)
            if reason:
                raise LocalUnavailable(reason)
        env = automatic_batch.environment() if automatic_batch else network_policy.child_environment(os.environ)
        deadline = time.monotonic() + TIMEOUT
        parent_read, parent_write = os.pipe()
        child = subprocess.Popen(
            module_command("agent_monitor.quota.quota_refresh", provider, parent_read, deadline),
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, start_new_session=True, pass_fds=(parent_read,),
            env=env,
        )
        os.close(parent_read)
        parent_read = None
        stage = "child_result"
        try:
            # The lease remains held through the bounded child's cleanup.
            target = dataclasses.asdict(account)
            if renew_auth:
                target["renew_auth"] = True
                target["auth_failure"] = auth_failure
            if codex_home is not None:
                target["codex_home"] = str(Path(codex_home).absolute())
            output, _ = child.communicate(json.dumps(target),
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
            if renew_auth and admitted_probe is not None:
                from agent_monitor.quota.codex_quota import safe_auth_recovery
                admitted_probe["authentication_recovery"] = safe_auth_recovery(payload.get("authentication_recovery"))
            if child.returncode or payload.get("error"):
                detail = diagnostics.safe_failure(payload.get("failure"))
                if provider == "codex" and pause_scope == "account":
                    from agent_monitor.quota import codex_quota
                    proof = payload.get("account_quota_rejection")
                    if (isinstance(proof, str) and len(proof) == 64
                            and all(c in "0123456789abcdef" for c in proof)
                            and proof == codex_quota.credential_digest(auth_path)):
                        codex_quota.check_identity(auth_path, account.account_id, account.label)
                        account_rejection = proof
                # A sibling can revoke this batch after our provider has already
                # answered. Retain that result and its existing pause semantics.
                # RPC transport errors alone do not prove an upstream response.
                bridge_explainable = _bridge_explainable_failure(detail)
                kind = (NotSignedIn if payload.get("signed_out") is True else
                        LocalUnavailable if payload.get("local_unavailable") is True else RefreshError)
                raise kind(payload.get("error") or "配额读取失败，请核对后解除本地暂停。",
                           failure=detail)
            reading = RateLimits(**payload["reading"])
            return dataclasses.replace(reading, quota_observation={
                "source": "active_query", "observed_at": reading.updated_at})
        except (ValueError, KeyError, TypeError):
            raise RefreshError("配额读取返回了无效结果，请核对后解除本地暂停。",
                               failure={"stage": stage, "kind": "invalid_result"}) from None
    except network_policy.NetworkPolicyError as exc:
        raise LocalUnavailable(str(exc), failure={"stage": stage, "kind": exc.kind}) from None
    except (NotSignedIn, LocalUnavailable, automatic_egress.Skipped):
        raise
    except BaseException as exc:
        if automatic_batch and (bridge_explainable or isinstance(exc, (subprocess.TimeoutExpired, ConnectionError, TimeoutError))):
            automatic_batch.check()
        failure = exc
        facts = diagnostics.failure_details(exc, stage=stage,
                    kind="timeout" if isinstance(exc, subprocess.TimeoutExpired) else "exception",
                    returncode=child.returncode if child is not None else None)
        facts["account_fingerprint"] = hashlib.sha256(
            quota_identity(provider, account.account_id, account.label).encode()).hexdigest()
        if account_profile is not None:
            facts["account_profile"] = account_profile
        try:
            facts["reader_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
        except OSError:
            pass  # The original error remains usable without source identity.
        save_error = None
        if child is not None:
            try:
                no_new = (pause_scope == "account" and renew_auth and admitted_probe is not None
                    and (admitted_probe.get("authentication_recovery") or {}).get("outcome") == "no_new_credentials")
                if account_rejection:
                    lease.state.setdefault("rejected_credentials", {}).setdefault(account_rejection, datetime.now(timezone.utc).isoformat())
                pause = lease.pause_local if no_new or account_rejection else lease.pause
                pause("quotaRefreshFailed", failure=facts)
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
    # User guidance is separate from the bounded, redacted diagnostic summary.
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
                if isinstance(detail, dict) and detail.get("code") in {"token_revoked", "token_expired"}:
                    return f"上游返回 401/{detail['code']}，本次未取得配额；原因尚未核实，请保留诊断信息并排查目标机器。"
    return "Codex 配额请求失败，本次未取得新读数；请查看诊断信息后处理。"


def _rpc_failure(error, request_id, secrets=()):
    from agent_monitor.quota.codex_quota import rpc_failure
    return rpc_failure(error, stage="initialize" if request_id == 1 else "quota_read", secrets=secrets)


def account_fingerprint(provider, account):
    if account is None:
        return None
    return hashlib.sha256(quota_identity(provider, account.account_id, account.label).encode()).hexdigest()


def _codex(account, *, recovery=None):
    from agent_monitor.quota import codex_quota
    if account.get("renew_auth"):
        if recovery is None:
            raise LocalUnavailable("认证修复缺少记录通道，未发出请求。")
        recovery["initial_failure"] = diagnostics.safe_failure(account.get("auth_failure"))
        recovery["outcome"] = "refresh_failed"
    try:
        env = network_policy.child_environment(os.environ)
    except network_policy.NetworkPolicyError as exc:
        raise LocalUnavailable(str(exc)) from None
    # Bind the exact home consumed by the child, including the native default.
    home = Path(account.get("codex_home") or env.get("CODEX_HOME") or Path.home() / ".codex")
    auth_path = home / "auth.json"
    try:
        codex_quota.check_identity(auth_path, account["account_id"], account.get("label"))
        if account.get("renew_auth"):
            codex_quota.managed_access_token(auth_path)
    except codex_quota.ReadingError as exc:
        raise LocalUnavailable(str(exc), failure=exc.failure) from None
    if account.get("renew_auth"):
        from agent_monitor.accounts.codex_accounts import child_environment
        # Managed recovery has the same identity boundary as managed login.
        # The helper keeps the selected Monitor route but excludes alternate
        # tokens and parent Codex configuration before starting the supplier.
        env = child_environment(home.parent)
    env["CODEX_HOME"] = str(home)
    redactions = codex_quota.credential_secrets(auth_path)
    # SSH/launchd do not load shell rc files. Supply installed user binary paths
    # to the existing shim as well as to its backend lookup.
    env["PATH"] = os.pathsep.join([str(Path.home() / ".local/bin"),
                                  "/opt/homebrew/bin", "/usr/local/bin", env.get("PATH", "")])
    shim = Path.home() / ".local/libexec/agent-shims/codex"
    command = str(shim) if shim.is_file() else shutil.which("codex", path=env["PATH"])
    if not command:
        raise LocalUnavailable("Codex CLI 不可用，请在这台机器上安装后刷新。")
    try:
        arguments = [command, "app-server", "--listen", "stdio://", "-c", "features.respect_system_proxy=false"]
        if account.get("renew_auth"):
            arguments += ["-c", 'cli_auth_credentials_store="file"',
                          "-c", 'forced_login_method="chatgpt"']
        process = subprocess.Popen(arguments,
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE if account.get("renew_auth") else subprocess.DEVNULL, env=env)
    except OSError as exc:
        raise LocalUnavailable("Codex CLI 无法启动，请检查这台机器上的安装；" +
                               diagnostics.failure_summary(diagnostics.failure_details(exc, stage="child_start"))) from None
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    if process.stderr is not None:
        selector.register(process.stderr, selectors.EVENT_READ)
    client_output = bytearray()
    client_output_truncated = False
    def retain_client_output(chunk):
        nonlocal client_output_truncated
        available = 65536 - len(client_output)
        client_output.extend(chunk[:available])
        client_output_truncated |= len(chunk) > available
    buffer = b""
    deadline = time.monotonic() + CODEX_RPC_SECONDS

    def send(message):
        process.stdin.write((json.dumps(message) + "\n").encode())
        process.stdin.flush()

    def receive(request_id, method=None):
        nonlocal buffer
        while True:
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                message = json.loads(line)
                if message.get("id") == request_id:
                    if "error" in message:
                        # Keep failures visible and preserve the prior reading.
                        facts = _rpc_failure(message["error"], request_id,
                            redactions + codex_quota.credential_secrets(auth_path))
                        if method == "account/read":
                            facts["stage"] = "account_check"
                        error = RefreshError(_codex_rpc_error(message["error"], request_id), failure=facts)
                        error.authentication_quota_transport = codex_quota.authentication_quota_transport(message["error"], method)
                        if error.authentication_quota_transport:
                            error.failure["http_status"] = 401
                        raise error
                    return message["result"]
            remaining = deadline - time.monotonic()
            ready = selector.select(max(0, remaining)) if remaining > 0 else []
            if not ready:
                raise RefreshError("Codex 配额请求超时，请重新刷新。",
                                   failure={"kind": "timeout", "timeout_seconds": CODEX_RPC_SECONDS})
            for key, _ in ready:
                chunk = os.read(key.fd, 65536)
                if key.fileobj is process.stderr:
                    if chunk:
                        retain_client_output(chunk)
                    else:
                        selector.unregister(process.stderr)
                    continue
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
            sequence = 1
            def request(method, params):
                nonlocal redactions, sequence
                if time.monotonic() >= deadline:
                    raise RefreshError("本次检查已达到总时限，未发出下一阶段请求。", failure={"kind": "timeout", "timeout_seconds": CODEX_RPC_SECONDS})
                redactions += codex_quota.credential_secrets(auth_path)
                sequence += 1
                send({"id": sequence, "method": method, "params": params})
                return receive(sequence, method)
            try:
                options = {}
                if account.get("renew_auth"):
                    options = {"renew_auth": True, "recovery": recovery}
                reading = codex_quota.read_bound(request, auth_path, account["account_id"], account.get("label"), **options)
            except codex_quota.ReadingError as exc:
                raise RefreshError(str(exc), failure=exc.failure) from None
        value = _reading((reading["five_hour_used_pct"], reading["five_hour_resets_at"]),
                         (reading["seven_day_used_pct"], reading["seven_day_resets_at"]), plan=reading["plan"])
        return dataclasses.replace(value, **{key: reading[key] for key in (
            "codex_quota_available", "ordinary_usage_allowed", "spend_control_reached", "rate_limit_reached_type")})

    finally:
        selector.close()
        process.terminate()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        if process.stderr is not None:
            os.set_blocking(process.stderr.fileno(), False)
            while True:
                try:
                    chunk = os.read(process.stderr.fileno(), 65536)
                except BlockingIOError:
                    break
                if not chunk:
                    break
                retain_client_output(chunk)
            process.stderr.close()
            if recovery is not None and client_output:
                recovery["client_diagnostics"] = _safe_client_excerpt(
                    client_output.decode(errors="replace"),
                    redactions + codex_quota.credential_secrets(auth_path),
                    truncated=client_output_truncated)
        process.stdin.close()
        process.stdout.close()


def _safe_client_excerpt(value, secrets, *, truncated=False):
    # Redact before the display limit, including a credential cut by the bounded
    # input buffer. Never classify free-form client logs as an upstream result.
    for secret in sorted(set(secrets), key=len, reverse=True):
        if not secret:
            continue
        if truncated:
            for size in range(min(len(secret) - 1, len(value)), 0, -1):
                if value.endswith(secret[:size]):
                    value = value[:-size] + "[redacted truncated credential]"
                    break
        value = value.replace(secret, "[redacted]")
    return diagnostics.safe_upstream_detail(value)


if __name__ == "__main__":
    recovery = {}
    try:
        reader = {"claude": _claude, "codex": _codex}[sys.argv[1]]
        with _reader_lifetime(int(sys.argv[2]), float(sys.argv[3])):
            target = json.load(sys.stdin)
            reading = reader(target, recovery=recovery) if sys.argv[1] == "codex" else reader(target)
        print(json.dumps({"reading": dataclasses.asdict(reading), "authentication_recovery": recovery}))
    except RefreshError as exc:
        # `signed_out` is the class, carried as data because the parent runs in a
        # different process and only sees this line.
        print(json.dumps({"error": str(exc), "signed_out": isinstance(exc, NotSignedIn),
                          "local_unavailable": isinstance(exc, LocalUnavailable),
                          "failure": diagnostics.failure_details(exc), "authentication_recovery": recovery,
                          "account_quota_rejection": getattr(exc, "account_quota_rejection", None)}))
        sys.exit(1)
    except Exception as exc:
        # External errors can contain request headers or provider payloads.
        print(json.dumps({"error": "配额请求失败，请检查这台机器的网络与登录状态后刷新。",
                          "failure": diagnostics.failure_details(exc), "authentication_recovery": recovery,
                          "account_quota_rejection": getattr(exc, "account_quota_rejection", None)}))
        sys.exit(1)
