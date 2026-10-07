"""Explicit, private Codex account actions, separate from machine collection."""

import atexit
import copy
import fcntl
import json
import math
import os
from pathlib import Path
import selectors
import shutil
import signal
import subprocess
import threading
import time
import uuid
from datetime import datetime, timezone
from urllib.parse import urlparse

from parsers.accounts import codex_account


MESSAGE = "Please reply with OK."
ACTIVE = {"checking", "login", "sending", "reading"}
LOGIN_SECONDS = 600
SEND_SECONDS = 120


class ActionError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


class Cancelled(ActionError):
    pass


def now():
    return datetime.now(timezone.utc).isoformat()


def private_dir(path):
    if path.is_symlink():
        raise ActionError("账号目录不可使用符号链接。", 500)
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.chmod(0o700)
    return path


def write_record(path, value):
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with os.fdopen(os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as out:
            json.dump(value, out, ensure_ascii=False)
            out.flush()
            os.fsync(out.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def lock_file(path):
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    handle = os.fdopen(fd, "a+")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        raise ActionError("这个账号已有操作正在进行，请等待或取消。", 409)
    return handle


def child_environment(home):
    # Inherit network transport settings, not API keys, alternate auth or a
    # parent's Codex session/configuration. Resolve executables before HOME changes.
    names = ("PATH", "LANG", "LC_ALL", "TMPDIR", "SSL_CERT_FILE", "SSL_CERT_DIR",
             "CODEX_CA_CERTIFICATE", "http_proxy", "https_proxy", "all_proxy", "no_proxy",
             "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY", "AGENT_PROXY_ADDR")
    env = {key: os.environ[key] for key in names if key in os.environ}
    env["PATH"] = os.pathsep.join([str(Path.home() / ".local/bin"), "/opt/homebrew/bin",
                                  "/usr/local/bin", env.get("PATH", "/usr/bin:/bin")])
    env.update(HOME=str(home / "home"), CODEX_HOME=str(home / "codex"))
    return env


class Rpc:
    """One owned app-server, with bounded waits and no user configuration."""

    def __init__(self, home, cancelled):
        self.cancelled = cancelled
        self.sequence = 0
        self.pending = []
        self.buffer = b""
        for name in ("home", "codex", "work"):
            private_dir(home / name)
        env = child_environment(home)
        shim = Path.home() / ".local/libexec/agent-shims/codex"
        executable = str(shim) if shim.is_file() else shutil.which("codex", path=env["PATH"])
        if not executable:
            raise ActionError("服务所在机器未找到 Codex CLI，请安装后重试。", 503)
        config = {
            "cli_auth_credentials_store": '"file"', "forced_login_method": '"chatgpt"',
            "model_provider": '"openai"', "web_search": '"disabled"',
            "features.shell_tool": "false", "features.code_mode": "false",
            "features.code_mode_host": "false", "features.multi_agent": "false",
            "features.hooks": "false", "features.apps": "false", "features.plugins": "false",
            "project_doc_max_bytes": "0",
        }
        command = [executable, "app-server", "--listen", "stdio://"]
        for key, value in config.items():
            command.extend(["-c", key + "=" + value])
        self.process = subprocess.Popen(command, cwd=home / "work", env=env,
                                        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.DEVNULL, start_new_session=True, umask=0o077)
        self.selector = selectors.DefaultSelector()
        self.selector.register(self.process.stdout, selectors.EVENT_READ)
        self.home = home

    def send(self, value):
        if self.cancelled.is_set():
            raise Cancelled("操作已取消。")
        try:
            self.process.stdin.write((json.dumps(value) + "\n").encode())
            self.process.stdin.flush()
        except (BrokenPipeError, OSError):
            raise ActionError("Codex 进程已退出，请重试。", 502)

    def receive(self, deadline):
        while True:
            if self.cancelled.is_set():
                raise Cancelled("操作已取消。")
            if time.monotonic() >= deadline:
                raise ActionError("等待 Codex 超时；请检查登录状态或网络后重试。", 504)
            if b"\n" in self.buffer:
                line, self.buffer = self.buffer.split(b"\n", 1)
                try:
                    value = json.loads(line)
                except (ValueError, UnicodeDecodeError):
                    raise ActionError("Codex 返回了无法解析的协议数据，请检查 CLI 版本。", 502)
                if not isinstance(value, dict):
                    raise ActionError("Codex 返回了不兼容的协议数据。", 502)
                if "method" in value and "id" in value:
                    self.send({"id": value["id"], "error": {"code": -32601, "message": "Tools are disabled"}})
                    raise ActionError("Codex 请求了额外权限或工具，已停止本次操作。", 502)
                return value
            if len(self.buffer) > 4 * 1024 * 1024:
                raise ActionError("Codex 响应超过限制，操作已停止。", 502)
            if not self.selector.select(min(.25, max(0, deadline - time.monotonic()))):
                continue
            chunk = os.read(self.process.stdout.fileno(), 65536)
            if not chunk:
                raise ActionError("Codex 进程提前退出，请检查 CLI 与网络后重试。", 502)
            self.buffer += chunk

    def request(self, method, params, deadline):
        self.sequence += 1
        request_id = self.sequence
        self.send({"id": request_id, "method": method, "params": params})
        while True:
            value = self.receive(deadline)
            if value.get("id") == request_id:
                if "error" in value:
                    # Upstream messages can contain tokens, URLs or configuration.
                    raise ActionError("Codex 拒绝了操作（%s），请检查账号设置和 CLI 版本。" % method, 502)
                result = value.get("result")
                if not isinstance(result, dict):
                    raise ActionError("Codex 返回了不兼容的结果。", 502)
                return result
            self.pending.append(value)
            if len(self.pending) > 2000:
                raise ActionError("Codex 返回过多事件，操作已停止。", 502)

    def event(self, method, predicate, deadline):
        while True:
            for index, value in enumerate(self.pending):
                if value.get("method") == method and predicate(value.get("params", {})):
                    return self.pending.pop(index)["params"]
            value = self.receive(deadline)
            if value.get("method") == method and predicate(value.get("params", {})):
                return value["params"]
            self.pending.append(value)
            if len(self.pending) > 2000:
                raise ActionError("Codex 返回过多事件，操作已停止。", 502)

    def initialize(self):
        self.request("initialize", {"clientInfo": {"name": "agent_monitor_accounts", "version": "1"},
                                   "capabilities": {"experimentalApi": True}}, time.monotonic() + 20)
        self.send({"method": "initialized"})

    def close(self):
        self.selector.close()
        if self.process.poll() is None:
            try:
                os.killpg(self.process.pid, signal.SIGTERM)
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                os.killpg(self.process.pid, signal.SIGKILL)
                self.process.wait(timeout=2)
            except ProcessLookupError:
                self.process.wait(timeout=2)
        self.process.stdin.close()
        self.process.stdout.close()


def quota_reading(result, account_id):
    if result.get("accountId") != account_id:
        raise ActionError("配额响应的账号不匹配或缺失，请更新 CLI 后重试。", 502)
    limit = (result.get("rateLimitsByLimitId") or {}).get("codex") or result.get("rateLimits") or {}
    reading = {"observed_at": now()}
    for minutes, prefix in ((300, "five_hour"), (10080, "seven_day")):
        window = next((limit[key] for key in ("primary", "secondary")
                       if isinstance(limit.get(key), dict) and limit[key].get("windowDurationMins") == minutes), {})
        for source, target in (("usedPercent", "used_pct"), ("resetsAt", "resets_at")):
            value = window.get(source)
            reading[prefix + "_" + target] = value if type(value) in (int, float) and math.isfinite(value) else None
    return reading


class Manager:
    def __init__(self, root, rpc_factory=Rpc):
        self.root = Path(root)
        self.rpc_factory = rpc_factory
        self.guard = threading.RLock()
        self.jobs = {}

    def directory(self, profile_id):
        try:
            if str(uuid.UUID(profile_id)) != profile_id:
                raise ValueError()
        except (ValueError, TypeError, AttributeError):
            raise ActionError("账号标识无效。")
        directory = self.root / profile_id
        if directory.is_symlink() or not (directory / "profile.json").is_file():
            raise ActionError("未找到这个账号。", 404)
        return directory

    def read(self, directory):
        try:
            return json.loads((directory / "profile.json").read_text())
        except (OSError, ValueError):
            raise ActionError("账号记录无法读取，请检查服务主机的状态目录。", 500)

    def save(self, directory, record):
        write_record(directory / "profile.json", record)

    def add(self, email, account_id=None):
        if not isinstance(email, str) or len(email) > 254 or email != email.strip() or "@" not in email or any(c.isspace() for c in email):
            raise ActionError("请输入有效的账号邮箱。")
        if account_id is not None and (not isinstance(account_id, str) or not account_id or len(account_id) > 256):
            raise ActionError("工作区标识无效。")
        with self.guard:
            private_dir(self.root)
            with lock_file(self.root / ".catalog.lock"):
                matches = []
                for path in self.root.glob("*/profile.json"):
                    record = self.read(path.parent)
                    if record["email"].casefold() == email.casefold() and (account_id is None or record.get("account_id") in (None, account_id)):
                        matches.append((path.parent, record))
                if len(matches) > 1:
                    raise ActionError("这个邮箱有多个工作区，请在下方对应账号卡片上操作。", 409)
                if matches:
                    directory, record = matches[0]
                    if account_id is not None and record.get("account_id") is None:
                        with lock_file(directory / ".lock"):
                            record["account_id"] = account_id
                            self.save(directory, record)
                    return self.view(directory)
                profile_id = str(uuid.uuid4())
                directory = private_dir(self.root / profile_id)
                self.save(directory, {"id": profile_id, "email": email, "account_id": account_id, "operation": None})
                return self.view(directory)

    def view(self, directory):
        record = self.read(directory)
        operation = record.get("operation")
        job = self.jobs.get(record["id"])
        if operation and operation["stage"] in ACTIVE and not job:
            try:
                with lock_file(directory / ".lock"):
                    operation.update(stage="interrupted", detail="上次操作已中断；发送结果可能未知，不会自动重发。")
                    self.save(directory, record)
            except ActionError as exc:
                if exc.status != 409:
                    raise
        result = copy.deepcopy(record)
        result["has_credentials"] = (directory / "codex/auth.json").is_file()
        result["busy"] = bool(job) or bool(operation and operation["stage"] in ACTIVE)
        if job and operation and operation["stage"] == "login":
            result["operation"].update(job.get("authorization", {}))
        return result

    def list(self):
        with self.guard:
            return {"accounts": [self.view(path.parent) for path in sorted(self.root.glob("*/profile.json"))],
                    "message": MESSAGE}

    def start(self, profile_id, *, refresh_only=False):
        with self.guard:
            directory = self.directory(profile_id)
            if profile_id in self.jobs:
                raise ActionError("这个账号已有操作正在进行。", 409)
            lock = lock_file(directory / ".lock")
            try:
                record = self.read(directory)
                record["operation"] = {"id": str(uuid.uuid4()), "stage": "checking", "started_at": now(), "refresh_only": refresh_only,
                                       "message_status": "not_sent", "quota_status": "not_read", "detail": "正在检查登录状态。"}
                self.save(directory, record)
                job = {"cancelled": threading.Event(), "authorization": {}}
                self.jobs[profile_id] = job
                thread = threading.Thread(target=self.run, args=(directory, record, job, lock), daemon=True,
                                          name="codex-account-" + profile_id)
                job["thread"] = thread
                thread.start()
            except Exception:
                self.jobs.pop(profile_id, None)
                lock.close()
                raise
            return self.view(directory)

    def cancel(self, profile_id):
        with self.guard:
            self.directory(profile_id)
            job = self.jobs.get(profile_id)
            if not job:
                raise ActionError("本进程没有正在运行的这项操作；请刷新查看结果。", 409)
            job["cancelled"].set()
            return {"cancelling": True}

    def forget_login(self, profile_id):
        with self.guard:
            directory = self.directory(profile_id)
            with lock_file(directory / ".lock"):
                (directory / "codex/auth.json").unlink(missing_ok=True)
                return self.view(directory)

    def update(self, directory, record, **fields):
        with self.guard:
            record["operation"].update(fields)
            self.save(directory, record)

    def identity(self, rpc, directory, record):
        account = rpc.request("account/read", {"refreshToken": True}, time.monotonic() + 20).get("account")
        if account is None:
            return False
        credential = codex_account(directory / "codex/auth.json")
        if (not isinstance(account, dict) or account.get("type") != "chatgpt" or not credential
                or str(account.get("email", "")).casefold() != record["email"].casefold()
                or str(credential.label).casefold() != record["email"].casefold()
                or (record.get("account_id") and record["account_id"] != credential.account_id)):
            raise ActionError("登录的邮箱或工作区与所选账号不一致，未发送消息。请清除登录态后登录正确账号。", 409)
        record["account_id"] = credential.account_id
        (directory / "codex/auth.json").chmod(0o600)
        self.save(directory, record)
        return True

    def run(self, directory, record, job, lock):
        rpc = None
        try:
            rpc = self.rpc_factory(directory, job["cancelled"])
            rpc.initialize()
            if not self.identity(rpc, directory, record):
                login = rpc.request("account/login/start", {"type": "chatgptDeviceCode"}, time.monotonic() + 30)
                url = login.get("verificationUrl", "")
                parsed = urlparse(url)
                if parsed.scheme != "https" or parsed.netloc != "auth.openai.com" or not login.get("userCode") or not login.get("loginId"):
                    raise ActionError("Codex 未返回有效的官方设备授权信息。请更新 CLI。", 502)
                job["authorization"] = {"verification_url": url, "user_code": login["userCode"]}
                self.update(directory, record, stage="login", detail="请在 OpenAI 官方页面登录所选邮箱，再输入下方设备码。邮箱验证码也在官方页面填写。")
                completed = rpc.event("account/login/completed", lambda p: p.get("loginId") == login["loginId"], time.monotonic() + LOGIN_SECONDS)
                job["authorization"] = {}
                if not completed.get("success"):
                    raise ActionError("官方授权未完成。请确认已启用设备码登录，再重试。", 502)
                if not self.identity(rpc, directory, record):
                    raise ActionError("授权后仍未取得登录态，请重试。", 502)
            if record["operation"]["refresh_only"]:
                self.update(directory, record, stage="reading", detail="正在查询配额，本次不发送消息。")
                after = quota_reading(rpc.request("account/rateLimits/read", {}, time.monotonic() + 20), record["account_id"])
                self.update(directory, record, stage="succeeded", quota_status="observed", after=after,
                            detail="已读取服务端配额，本次未发送消息。")
                return
            deadline = time.monotonic() + SEND_SECONDS
            try:
                before = quota_reading(rpc.request("account/rateLimits/read", {}, min(deadline, time.monotonic() + 15)), record["account_id"])
                self.update(directory, record, before=before)
            except ActionError:
                if job["cancelled"].is_set():
                    raise Cancelled("操作已取消。")
            thread = rpc.request("thread/start", {"ephemeral": True, "cwd": str(directory / "work"),
                                 "approvalPolicy": "never", "sandbox": "read-only", "environments": [],
                                 "baseInstructions": "Respond briefly to the user's message.",
                                 "developerInstructions": "This is a connectivity check. Reply with OK."}, deadline)
            thread_id = thread["thread"]["id"]
            # Commit unknown before transmission: lost acknowledgement is not proof of no usage.
            self.update(directory, record, stage="sending", message_status="unknown", detail="正在发送一条短消息；中断后不会自动重发。")
            turn = rpc.request("turn/start", {"threadId": thread_id, "input": [{"type": "text", "text": MESSAGE}],
                                              "environments": []}, deadline)
            turn_id = turn["turn"]["id"]
            completed = rpc.event("turn/completed", lambda p: p.get("threadId") == thread_id and p.get("turn", {}).get("id") == turn_id, deadline)
            if completed["turn"].get("status") != "completed":
                self.update(directory, record, message_status="failed")
                raise ActionError("消息未成功完成。可能已消耗额度；不会自动重发。", 502)
            self.update(directory, record, stage="reading", message_status="succeeded", detail="消息已完成，正在查询服务端配额。")
            try:
                after = quota_reading(rpc.request("account/rateLimits/read", {}, time.monotonic() + 20), record["account_id"])
                has_reset = after["seven_day_resets_at"] is not None
                self.update(directory, record, stage="succeeded" if has_reset else "partial", quota_status="observed", after=after,
                            detail="消息已完成，已读取服务端重置时间。" if has_reset else "消息已完成，但服务端未返回七天窗口重置时间；不能确认已固定。")
            except ActionError:
                self.update(directory, record, stage="partial", quota_status="failed", detail="消息已完成，但配额查询未成功。不要为了查询结果重复发送。")
        except Cancelled:
            self.update(directory, record, stage="cancelled", detail="操作已取消。若消息已发出，额度消耗不会撤回；发送结果见下方。")
        except Exception as exc:
            detail = str(exc) if isinstance(exc, ActionError) else "操作未完成，请检查 Codex CLI 或服务状态目录后重试。"
            self.update(directory, record, stage="failed", detail=detail)
        finally:
            try:
                if rpc:
                    rpc.close()
            finally:
                with self.guard:
                    job["authorization"] = {}
                    self.jobs.pop(record["id"], None)
                    lock.close()

    def close(self):
        with self.guard:
            jobs = list(self.jobs.values())
            for job in jobs:
                job["cancelled"].set()
        for job in jobs:
            job["thread"].join(timeout=3)


manager = Manager(Path(__file__).resolve().parent / "state/codex-accounts")
atexit.register(manager.close)
