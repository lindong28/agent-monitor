"""Account-scoped subscription confirmations and quota observation evidence.

This module never contacts a provider. User confirmations are not inferred from
HTTP failures or credential plan names. All times describe the fact they name.
"""
import argparse
import contextlib
import fcntl
import json
import math
import os
from pathlib import Path
import uuid
from datetime import datetime, timezone
from project_paths import PROJECT_ROOT


ROOT = PROJECT_ROOT
SOURCES = {"active_query", "claude_statusline", "codex_session"}


def timestamp(value):
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return None
        return parsed.timestamp()
    except (ValueError, TypeError, AttributeError, OverflowError):
        return None


def observation(value):
    if not isinstance(value, dict) or value.get("source") not in SOURCES:
        return None
    observed = value.get("observed_at")
    result = {"source": value["source"], "observed_at": observed if timestamp(observed) is not None else None}
    if value["source"] == "codex_session" and isinstance(value.get("session_id"), str) and value["session_id"]:
        result["session_id"] = value["session_id"]
    return result


def confirmation(value):
    if (not isinstance(value, dict) or value.get("state") != "no_subscription"
            or value.get("source") != "user_confirmed"
            or timestamp(value.get("confirmed_at")) is None):
        return None
    note = {key: value[key] for key in ("state", "source", "confirmed_at")}
    restored = value.get("restored_at")
    if restored is not None:
        if timestamp(restored) is None or timestamp(restored) <= timestamp(note["confirmed_at"]):
            return None
        note["restored_at"] = restored
    return note


def confirmation_time(note):
    return (timestamp(note["confirmed_at"]), timestamp(note.get("restored_at") or note["confirmed_at"]))


def unavailable(note):
    return bool(note and not note.get("restored_at"))


def current_window(reading, now=None):
    now = datetime.now(timezone.utc).timestamp() if now is None else now
    for prefix in ("five_hour", "seven_day"):
        used = reading.get(prefix + "_pct", reading.get(prefix + "_used_pct"))
        reset = reading.get(prefix + "_resets_at")
        if (type(used) in (int, float) and math.isfinite(used) and 0 <= used <= 100
                and type(reset) in (int, float) and math.isfinite(reset) and reset > now):
            return True
    return False


def supersedes(reading, note, now=None):
    evidence = observation(reading.get("quota_observation"))
    return bool(evidence and evidence["source"] == "active_query"
                and timestamp(evidence["observed_at"]) is not None
                and timestamp(evidence["observed_at"]) > timestamp(note["confirmed_at"])
                and not reading.get("refresh_error") and current_window(reading, now))


class Store:
    def __init__(self, root=None):
        self.path = Path(root or ROOT / "state") / "quota_applicability.json"

    def read(self):
        try:
            payload = json.loads(self.path.read_text())
        except FileNotFoundError:
            return {}
        if (not isinstance(payload, dict) or payload.get("version") != 1
                or not isinstance(payload.get("accounts"), dict)):
            raise ValueError("配额适用状态文件无效，原文件已保留。")
        entries = payload["accounts"]
        if any(not isinstance(key, str) or not confirmation(value) for key, value in entries.items()):
            raise ValueError("配额适用状态记录无效，原文件已保留。")
        return entries

    @staticmethod
    def key(provider, account_id):
        if provider != "claude" or not isinstance(account_id, str) or not account_id or len(account_id) > 256:
            raise ValueError("请指定 Claude 账号 ID。")
        return provider + ":" + account_id

    def get(self, provider, account_id):
        if provider != "claude" or not account_id:
            return None
        return self.read().get(self.key(provider, account_id))

    @contextlib.contextmanager
    def locked(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.with_suffix(".lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            yield

    def _write(self, entries):
        temp = self.path.with_name("." + self.path.name + "." + uuid.uuid4().hex)
        try:
            with os.fdopen(os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as out:
                json.dump({"version": 1, "accounts": entries}, out, allow_nan=False)
                out.flush()
                os.fsync(out.fileno())
            os.replace(temp, self.path)
        finally:
            temp.unlink(missing_ok=True)

    def confirm(self, provider, account_id, confirmed_at=None):
        key = self.key(provider, account_id)
        note = confirmation({"state": "no_subscription", "source": "user_confirmed",
                             "confirmed_at": confirmed_at or datetime.now(timezone.utc).isoformat()})
        if note is None:
            raise ValueError("确认时间必须包含时区。")
        with self.locked():
            entries = self.read()
            old = entries.get(key)
            if old and confirmation_time(old) > confirmation_time(note):
                raise ValueError("已有更新的订阅确认，未覆盖。")
            entries[key] = note
            self._write(entries)
        return note

    def adopt(self, provider, account_id, note):
        """Persist newer evidence received through the trusted snapshot channel."""
        key = self.key(provider, account_id)
        note = confirmation(note)
        if note is None:
            raise ValueError("订阅确认记录无效。")
        with self.locked():
            entries = self.read()
            old = entries.get(key)
            if old and confirmation_time(old) >= confirmation_time(note):
                return old
            entries[key] = note
            self._write(entries)
        return note

    def restore(self, provider, account_id, reading, *, expected_note=None):
        key = self.key(provider, account_id)
        with self.locked():
            entries = self.read()
            note = entries.get(key)
            if expected_note is not None and note != expected_note:
                return False
            if note and supersedes(reading, note):
                entries[key] = {**note, "restored_at": reading["quota_observation"]["observed_at"]}
                self._write(entries)
                return True
        return False


def main():
    parser = argparse.ArgumentParser(description="记录 Claude 订阅确认；只有 check-restored 会发起一次配额检查。")
    parser.add_argument("action", choices=("status", "no-subscription", "check-restored"))
    parser.add_argument("--account-id", required=True)
    parser.add_argument("--confirmed-at")
    parser.add_argument("--pause-id")
    parser.add_argument("--expected-label")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    store = Store()
    try:
        if args.action == "no-subscription":
            result = store.confirm("claude", args.account_id, args.confirmed_at)
            message = "已记录用户确认：该 Claude 账号无有效订阅；历史配额保留，普通采集不再请求其配额。"
        elif args.action == "check-restored":
            if not args.expected_label:
                parser.error("恢复检查需 --expected-label；有本地暂停时还需 --pause-id。仅在明确批准一次上游检查后执行。")
            from agent_monitor import quota_service
            result = quota_service.recover_claude_quota(args.account_id, args.expected_label, args.pause_id, root=ROOT)
            message = "该账号已取得新的有效配额，读数已保存，如指定了本地暂停，该暂停已解除。其他机器的本地确认和暂停仍保留，需分别核对恢复。"
        else:
            result = store.get("claude", args.account_id)
            message = "已确认无有效 Claude Code 订阅。" if unavailable(result) else "没有生效的本地无订阅确认；这不代表订阅或套餐已核实。"
        print(json.dumps(result, ensure_ascii=False) if args.json else message)
    except (OSError, ValueError, RuntimeError) as exc:
        # Provider errors are already bounded and redacted by the quota reader.
        print("操作未完成：" + str(exc))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
