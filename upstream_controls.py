"""Bounded, local provider admission shared by processes using one state root.

This is a local precaution, not a statement about a provider's account policy.
The stable lock covers admission, the entire operation and child cleanup.
"""

import fcntl
import json
import math
import os
import threading
import time
import uuid
from pathlib import Path
import diagnostics


class Paused(RuntimeError):
    pass


class Busy(RuntimeError):
    pass


def pause_summary(pause):
    return "暂停标识：%s；%s" % (pause["id"], diagnostics.failure_summary(pause.get("failure")))


class Controls:
    def __init__(self, root):
        self.root = Path(root)

    def _path(self, provider):
        if provider not in ("codex", "claude"):
            raise ValueError("Unsupported provider")
        return self.root / (provider + ".json")

    def read(self, provider):
        path = self._path(provider)
        try:
            value = json.loads(path.read_text())
            if not isinstance(value, dict) or value.get("version") != 1:
                raise ValueError()
            for key in ("active", "pause"):
                item = value.get(key)
                if item is not None and (not isinstance(item, dict) or not isinstance(item.get("id"), str)):
                    raise ValueError()
            released = value.get("released_at", 0)
            if type(released) not in (float, int) or not math.isfinite(released):
                raise ValueError()
            return value
        except FileNotFoundError:
            return {"version": 1}
        except (OSError, ValueError, TypeError):
            return {"version": 1, "pause": {"id": "unreadable", "reason": "stateUnreadable"}}

    def _save(self, provider, value):
        path = self._path(provider)
        temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
        try:
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w") as stream:
                json.dump(value, stream)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    def _lock(self, provider, cancelled, timeout):
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = self._path(provider).with_suffix(".lock")
        handle = os.fdopen(os.open(path, os.O_RDWR | os.O_CREAT, 0o600), "r+")
        deadline = time.monotonic() + timeout
        try:
            while True:
                if cancelled.is_set():
                    raise Busy("操作已取消，未开始新的上游请求。")
                try:
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    return handle
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise Busy("同一服务已有操作，本次未发出请求。")
                    cancelled.wait(min(.1, max(0, deadline - time.monotonic())))
        except BaseException:
            handle.close()
            raise

    def acquire(self, provider, *, cancelled=None, timeout=0, interval=0):
        cancelled = cancelled or threading.Event()
        handle = self._lock(provider, cancelled, timeout)
        try:
            state = self.read(provider)
            if state.get("active") and not state.get("pause"):
                state["pause"] = {"id": uuid.uuid4().hex, "reason": "interrupted", "at": time.time()}
                state.pop("active", None)
                self._save(provider, state)
            if state.get("pause"):
                raise Paused("本机已暂停该服务的自动请求；%s。请先核对原始失败并明确解除本地暂停。" %
                             pause_summary(state["pause"]))
            delay = max(0, state.get("released_at", 0) + interval - time.time())
            if cancelled.wait(min(delay, interval)):
                raise Busy("操作已取消，未开始新的上游请求。")
            state["active"] = {"id": uuid.uuid4().hex, "at": time.time()}
            self._save(provider, state)
            return Lease(self, provider, handle, state)
        except BaseException:
            handle.close()
            raise

    def resume(self, provider, expected_id):
        handle = self._lock(provider, threading.Event(), 0)
        try:
            state = self.read(provider)
            pause = state.get("pause")
            if not pause or pause["id"] != expected_id:
                raise Paused("暂停状态已变化，请刷新页面后重新核对。")
            if pause["id"] == "unreadable":
                raise Paused("保护状态无法读取，请修复本机状态文件；不能直接解除。")
            state["last_pause"] = pause
            state.pop("pause", None)
            state.pop("active", None)
            self._save(provider, state)
        finally:
            handle.close()


class Lease:
    def __init__(self, controls, provider, handle, state):
        self.controls, self.provider, self.handle, self.state = controls, provider, handle, state

    def pause(self, reason="unknown", *, failure=None):
        # Only fixed internal categories belong here; never upstream bodies/tokens.
        self.state["pause"] = {"id": uuid.uuid4().hex, "reason": reason, "at": time.time()}
        if failure:
            self.state["pause"]["failure"] = diagnostics.safe_failure(failure)
        self.controls._save(self.provider, self.state)

    def close(self, *, completed=True):
        if self.handle.closed:
            return
        try:
            if completed:
                self.state.pop("active", None)
                self.state["released_at"] = time.time()
                self.controls._save(self.provider, self.state)
        finally:
            self.handle.close()


def main(argv=None):
    import argparse
    import sys
    parser = argparse.ArgumentParser(description="查看或解除本机上游请求暂停；不会调用上游服务。")
    parser.add_argument("action", choices=("status", "resume"))
    parser.add_argument("provider", choices=("codex", "claude"))
    parser.add_argument("--pause-id", help="核对原因后，填入 status 显示的当前暂停标识")
    parser.add_argument("--state-root", type=Path, default=Path(__file__).resolve().parent / "state/upstream_controls")
    args = parser.parse_args(argv)
    controls = Controls(args.state_root)
    if args.action == "resume":
        if not args.pause_id:
            parser.error("解除前请先运行 status 核对暂停原因，再提供 --pause-id。")
        try:
            controls.resume(args.provider, args.pause_id)
        except (Paused, Busy, OSError) as exc:
            print(f"{args.provider} 本地暂停未解除：{exc}", file=sys.stderr)
            return 1
        print(f"{args.provider} 本地暂停已解除。未发出任何上游请求；这不证明上游已恢复，原始暂停记录保留。")
        return 0
    state = controls.read(args.provider)
    pause = state.get("pause")
    if pause:
        print(f"{args.provider} 本机请求已暂停。原因：{pause.get('reason', 'unknown')}；{pause_summary(pause)}。")
        print("请先核对原始失败、账号状态与服务端要求；确认后用 resume 和 --pause-id 解除本地暂停。")
    elif state.get("active"):
        print(f"{args.provider} 有未闭合的操作记录；可能仍在运行。异常退出后，下次调用将暂停，不会自动重试。")
    else:
        print(f"{args.provider} 当前没有本地通道暂停；未查询上游状态，账号独立限制仍可能生效。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
