"""Independent machine quota observations; statistics still own membership."""

import copy
import json
import os
import selectors
import subprocess
import sys
import threading
import time
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path

import hub
import sync_process
import diagnostics
from parsers.accounts import quota_identity


MACHINE_TIMEOUT = 140


def round_timeout(machines):
    # Each selected machine owns one existing bounded query, plus worker cleanup.
    return max(1, len(machines)) * MACHINE_TIMEOUT + 20


def _time(value):
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except (AttributeError, TypeError, ValueError, OverflowError):
        return 0


def _validate(payload):
    if (not isinstance(payload, dict)
            or set(payload) != {"schema_version", "source_host_identity", "exporter_commit", "rate_limits"}
            or payload["schema_version"] != 1
            or not isinstance(payload["source_host_identity"], str)
            or not payload["source_host_identity"]
            or not isinstance(payload["exporter_commit"], str)
            or len(payload["exporter_commit"]) != 40
            or any(c not in "0123456789abcdef" for c in payload["exporter_commit"])
            or not isinstance(payload["rate_limits"], dict)
            or set(payload["rate_limits"]) != {"claude", "codex"}
            or not all(isinstance(b, dict) for b in payload["rate_limits"].values())):
        raise ValueError("配额查询返回的来源或格式无效")


@diagnostics.measured("quota.query_worker")
def run_queries(machines, complete):
    """Stream small results; never acquire a generation lease or publish a DB."""
    expected = {m.name for m in machines}
    seen = set()
    done = False
    with sync_process.supervised_worker(__file__, text=False) as process:
        process.stdin.write((json.dumps([m.as_config_dict() for m in machines]) + "\n").encode())
        process.stdin.flush()
        deadline = time.monotonic() + round_timeout(machines)
        buffer = b""
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not selector.select(remaining):
                    raise TimeoutError("配额刷新超时，请重试")
                chunk = os.read(process.stdout.fileno(), 65536)
                if not chunk:
                    break
                buffer += chunk
                while b"\n" in buffer:
                    line, buffer = buffer.split(b"\n", 1)
                    value = json.loads(line)
                    if done:
                        raise ValueError("配额查询结束后仍返回数据")
                    if value == {"done": True}:
                        done = True
                        continue
                    if (not isinstance(value, dict) or set(value) != {"machine", "payload", "error"}
                            or value["machine"] not in expected or value["machine"] in seen
                            or (value["payload"] is None) == (value["error"] is None)
                            or (value["error"] is not None and not isinstance(value["error"], str))):
                        raise ValueError("配额查询进度无效")
                    if value["payload"] is not None:
                        _validate(value["payload"])
                    if value["error"] or any(block.get("refresh_error") for block in
                                             (value["payload"] or {}).get("rate_limits", {}).values()):
                        diagnostics.monitor.outcome("source_failure")
                    seen.add(value["machine"])
                    complete(value["machine"], value["payload"], value["error"])
        if process.wait(timeout=max(.1, deadline - time.monotonic())) or not done or seen != expected or buffer:
            raise RuntimeError("配额查询未完整结束，请重试")


class Manager:
    def __init__(self, path, on_complete=None):
        self.path = Path(path)
        self.on_complete = on_complete
        self.condition = threading.Condition()
        self.requested = self.completed = self.revision = 0
        self.running = False
        self.stopping = False
        self.thread = None
        self.threads = []
        self.pending = ()
        self.errors = {}
        self.machines = ()
        self.records = {}
        self.load_error = None
        try:
            data = json.loads(self.path.read_text())
            if set(data) != {"version", "machines"} or data["version"] != 1 or not isinstance(data["machines"], dict):
                raise ValueError()
            for record in data["machines"].values():
                if (set(record) != {"ssh_host", "payload", "error", "attempted_at"}
                        or not isinstance(record["ssh_host"], str)
                        or not _time(record["attempted_at"])
                        or (record["error"] is not None and not isinstance(record["error"], str))):
                    raise ValueError()
                if record["payload"] is not None:
                    _validate(record["payload"])
            self.records = data["machines"]
        except FileNotFoundError:
            pass
        except (OSError, ValueError, TypeError, AttributeError):
            self.load_error = "保存的配额查询结果不可读，请刷新重试"

    def request(self, machines):
        with self.condition:
            if self.stopping:
                raise RuntimeError("配额刷新正在关闭，请稍后重试")
            machines = tuple(machines)
            if self.running:
                if machines != self.machines:
                    raise RuntimeError("已有配额刷新正在运行；本轮结束后再刷新新的机器范围。")
                return self.requested
            self.requested += 1
            self.machines = machines
            ticket = self.requested
            if not self.running:
                self.running = True
                self.pending = tuple(m.name for m in machines)
                try:
                    self.thread = threading.Thread(target=self._run, name="agent-monitor-quota", daemon=True)
                    self.threads = [t for t in self.threads if t.is_alive()] + [self.thread]
                    self.thread.start()
                except Exception:
                    self.running = False
                    self.completed = ticket
                    self.errors = {m.name: "配额刷新任务未能启动，请重试" for m in machines}
                    self.condition.notify_all()
            return ticket

    def status(self):
        with self.condition:
            return {"running": self.running, "requested": self.requested, "completed": self.completed,
                    "pending_machines": list(self.pending), "errors": dict(self.errors),
                    "revision": self.revision, "cache_error": self.load_error}

    def wait(self, ticket, timeout=None):
        with self.condition:
            if timeout is None:
                timeout = round_timeout(self.machines) + 5
            return self.condition.wait_for(lambda: self.completed >= ticket, timeout=timeout)

    def shutdown(self):
        with self.condition:
            self.stopping = True
            threads = list(self.threads)
        sync_process.shutdown()
        deadline = time.monotonic() + 5
        for thread in threads:
            thread.join(timeout=max(0, deadline - time.monotonic()))
            if thread.is_alive():
                raise RuntimeError("配额刷新尚未停止，拒绝重启")

    def _run(self):
        while True:
            with self.condition:
                ticket, machines = self.requested, self.machines
                self.pending = tuple(m.name for m in machines)
                self.errors = {}
            by_name = {m.name: m for m in machines}

            def complete(name, payload, error):
                with self.condition:
                    old = self.records.get(name, {})
                    if old.get("ssh_host") != by_name[name].ssh_host:
                        old = {}
                    previous = old.get("payload")
                    if payload is not None and previous is not None and payload["source_host_identity"] == previous["source_host_identity"]:
                        payload = copy.deepcopy(payload)
                        for provider, incoming in payload["rate_limits"].items():
                            before = previous["rate_limits"][provider]
                            identity = lambda b: quota_identity(provider, b.get("account_id"), b.get("account_label"))
                            if identity(incoming) == identity(before) and _time(before.get("updated_at")) > _time(incoming.get("updated_at")):
                                for field in ("five_hour_pct", "five_hour_resets_at", "seven_day_pct", "seven_day_resets_at", "updated_at"):
                                    incoming[field] = before.get(field)
                    self.records[name] = {"ssh_host": by_name[name].ssh_host,
                                          "payload": payload if payload is not None else old.get("payload"),
                                          "attempted_at": datetime.now(timezone.utc).isoformat(),
                                          "error": error}
                    failures = [b.get("refresh_error") for b in (payload or {}).get("rate_limits", {}).values()
                                if b.get("refresh_error")]
                    if error or failures:
                        self.errors[name] = error or "；".join(str(v) for v in failures)
                    self.pending = tuple(n for n in self.pending if n != name)
                    self.revision += 1
                    try:
                        hub._write_quota_message(self.path, {"version": 1, "machines": self.records})
                        self.load_error = None
                    except OSError:
                        self.load_error = "配额已查询，但结果保存失败；重启后可能回到旧读数"
                        self.errors[name] = self.load_error
            try:
                run_queries(machines, complete)
            except Exception:
                # Never expose subprocess argv, provider output or credentials.
                with self.condition:
                    pending = self.pending
                for name in pending:
                    complete(name, None, "配额查询未能完成，请重试")
                if not pending:
                    with self.condition:
                        self.errors["worker"] = "配额查询进程未正常结束，请重试"
            with self.condition:
                self.completed = ticket
                self.condition.notify_all()
                if self.requested == ticket or self.stopping:
                    self.running = False
                    break
        if self.on_complete is not None and not self.stopping:
            self.on_complete()

    def merge(self, current, machine):
        result = copy.deepcopy(current.meta.get("rate_limits") or {})
        with self.condition:
            record = copy.deepcopy(self.records.get(current.host))
        if not record or machine is None or record["ssh_host"] != machine.ssh_host:
            return result
        payload = record["payload"]
        error = record["error"]
        if payload is not None and payload["source_host_identity"] != current.meta["source_host_identity"]:
            payload = None
            error = "配额来源身份与已确认机器不符，请检查机器配置"
        for provider in ("claude", "codex"):
            base = result.get(provider)
            if not isinstance(base, dict):
                base = {}
            if _time(base.get("updated_at")) > _time(record["attempted_at"]):
                continue
            incoming = (payload or {}).get("rate_limits", {}).get(provider)
            if incoming is not None:
                identity = lambda block: quota_identity(provider, block.get("account_id"), block.get("account_label"))
                if identity(incoming) != identity(base):
                    provider_error = "登录账号已变化，等待统计同步确认"
                else:
                    provider_error = incoming.get("refresh_error")
                    if _time(incoming.get("updated_at")) >= _time(base.get("updated_at")):
                        # Only the observation moves; membership/plan come from admission.
                        for field in ("five_hour_pct", "five_hour_resets_at", "seven_day_pct", "seven_day_resets_at", "updated_at"):
                            base[field] = incoming.get(field)
                    # Login state is the latest query outcome, not the age of
                    # the quota reading returned as its fallback.
                    base["signed_out"] = incoming.get("signed_out", False)
                    # A subsequent admitted query failure remains visible too.
                    if (_time(current.meta.get("generated_at")) > _time(record["attempted_at"])
                            and base.get("refresh_error") and not provider_error):
                        provider_error = base["refresh_error"]
                    base["refresh_error"] = provider_error
                if provider_error:
                    base["refresh_error"] = provider_error
            if error:
                base["refresh_error"] = error
                base["signed_out"] = False
            result[provider] = base
        return result


def _worker():
    import exporter
    import sync
    from machine_config import Machine

    machines = [Machine(v["name"], v["ssh_host"], v["self"]) for v in json.loads(sys.stdin.readline())]
    output = sys.stdout
    sync_process.watch_parent()

    def query(machine):
        try:
            if machine.is_self:
                payload = exporter.export_quota()
            else:
                result = subprocess.run([*sync._SSH_PREFIX, machine.ssh_host,
                    "~/.local/bin/agent-monitor export --quota-only --gui-quota"],
                    capture_output=True, text=True, timeout=MACHINE_TIMEOUT, check=True)
                payload = json.loads(result.stdout)
            _validate(payload)
            return {"machine": machine.name, "payload": payload, "error": None}
        except Exception as exc:
            return {"machine": machine.name, "payload": None, "error": "配额查询失败：" +
                    diagnostics.failure_summary(diagnostics.failure_details(exc, stage="quota_worker"))}

    with redirect_stdout(sys.stderr):
        # This conservative stop applies to this combined round only. Provider
        # pauses remain local to each machine; there is no distributed lock.
        stopped = None
        for machine in machines:
            if stopped:
                result = {"machine": machine.name, "payload": None,
                          "error": "本轮前序机器查询异常；本机尚未尝试。触发原因：" + stopped}
            else:
                result = query(machine)
                causes = ([result["error"]] if result["error"] else [
                    provider + "：" + str(block["refresh_error"]) for provider, block in
                    result["payload"]["rate_limits"].items() if block.get("refresh_error")])
                if causes:
                    stopped = machine.name + " / " + "；".join(causes)
            output.write(json.dumps(result) + "\n")
            output.flush()
        output.write(json.dumps({"done": True}) + "\n")
        output.flush()


if __name__ == "__main__":
    if sys.argv[1:] != ["--worker"]:
        raise SystemExit("internal quota worker")
    _worker()
