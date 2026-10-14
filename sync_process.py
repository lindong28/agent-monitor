"""Isolate sync export/publication work from the Hub's HTTP process."""

import atexit
from contextlib import contextmanager, redirect_stdout
from dataclasses import dataclass
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import diagnostics
import process_identity


@dataclass(frozen=True)
class Outcome:
    success: bool
    error: object = None


_lock = threading.Lock()
_active = set()
_stopping = False


def _signal_group(pid, value):
    try:
        os.killpg(pid, value)
    except ProcessLookupError:
        pass
    except PermissionError:
        if sys.platform != "darwin":
            raise
        # Darwin returns EPERM for a group containing only zombies. Do not
        # mistake that for an unsignalable live child. The unreaped leader still
        # reserves this PGID; an empty live group cannot create new descendants.
        if process_identity.darwin_group_has_live_members(pid):
            raise


def _exit_observer():
    """Return a wait operation that observes a child without consuming its PID."""
    if all(hasattr(os, name) for name in ("waitid", "P_PID", "WEXITED", "WNOWAIT")):
        def observe(pid, nohang=False):
            os.waitid(os.P_PID, pid, os.WEXITED | os.WNOWAIT | (os.WNOHANG if nohang else 0))
        return observe
    if sys.platform != "darwin":
        raise RuntimeError("safe worker supervision requires waitid WNOWAIT")

    # Older macOS Python builds omit os.waitid although libc provides it.
    # Darwin sys/wait.h: P_PID=1, WEXITED=4, WNOWAIT=32, WNOHANG=1.
    import ctypes
    import errno
    native = ctypes.CDLL(None, use_errno=True).waitid
    native.argtypes = [ctypes.c_int, ctypes.c_uint, ctypes.c_void_p, ctypes.c_int]
    native.restype = ctypes.c_int

    def observe(pid, nohang=False):
        # sys/signal.h siginfo_t is 104 bytes, aligned to 8 on 64-bit Darwin.
        # Overallocate aligned storage; no siginfo fields are consumed here.
        info = (ctypes.c_long * 32)()
        while native(1, pid, ctypes.byref(info), 4 | 32 | (1 if nohang else 0)) != 0:
            error = ctypes.get_errno()
            if error != errno.EINTR:
                raise OSError(error, os.strerror(error))
    return observe


class _OwnedWorker:
    """Observe exit without reaping, so the group ID cannot be recycled."""
    def __init__(self, process, observe_exit=None):
        self._wait_for_exit = observe_exit or _exit_observer()
        self._process = process
        self.pid, self.stdin, self.stdout = process.pid, process.stdin, process.stdout
        self._guard = threading.Lock()
        self._done = threading.Event()
        self._error = None
        # A descendant can inherit stdout after its leader exits. Waiting for
        # stdout EOF before cleanup would leave both reader and descendant stuck.
        self._observer = threading.Thread(target=self._observe_exit, daemon=True,
                                          name="agent-monitor-worker-reaper")
        self._observer.start()

    @property
    def returncode(self):
        return self._process.returncode if self._done.is_set() else None

    def _signal(self, value):
        with self._guard:
            if self._done.is_set():
                return
            # Also refuse if some external waiter violated ownership and reaped
            # this child. Neither kill(0) nor a remembered numeric PGID suffices.
            self._wait_for_exit(self.pid, nohang=True)
            _signal_group(self.pid, value)

    def _observe_exit(self):
        try:
            self._wait_for_exit(self.pid)
            with self._guard:
                # Leader is still an unreaped child throughout this operation.
                # No further group signal is allowed after wait() releases it.
                _signal_group(self.pid, signal.SIGKILL)
                self._process.wait()
                self._done.set()
        except BaseException as exc:
            self._error = exc
            self._done.set()

    def poll(self):
        if self._done.is_set() and self._error:
            raise RuntimeError("worker cleanup could not be confirmed") from self._error
        return self.returncode

    def wait(self, timeout=None):
        if not self._done.wait(timeout):
            raise subprocess.TimeoutExpired(self._process.args, timeout)
        return self.poll()


def _stop(process):
    # Raw Popen is supported for callers that explicitly hand over their child;
    # a previously reaped child never authorizes a signal to its old group.
    if not isinstance(process, _OwnedWorker):
        if process.returncode is not None:
            return
        process = _OwnedWorker(process)
    process._signal(signal.SIGTERM)
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        process._signal(signal.SIGKILL)
        process.wait(timeout=2)


def shutdown():
    """Called before same-PID exec as well as ordinary interpreter exit."""
    global _stopping
    with _lock:
        _stopping = True
        for process in list(_active):
            _stop(process)


atexit.register(shutdown)


@contextmanager
def supervised_worker(path, *, text=True):
    """Share the shutdown gate and process-group ownership across workers."""
    with _lock:
        if _stopping:
            raise RuntimeError("worker supervisor is shutting down")
        observe_exit = _exit_observer()
        process = _OwnedWorker(subprocess.Popen(
            [sys.executable, str(Path(path).resolve()), "--worker"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=text,
            start_new_session=True,
        ), observe_exit=observe_exit)
        _active.add(process)
    try:
        yield process
    finally:
        try:
            if process.poll() is None:
                _stop(process)
        finally:
            process.stdin.close()
            process.stdout.close()
            with _lock:
                _active.discard(process)


def watch_parent():
    # EOF identifies the parent execution instance, unlike a PID across exec.
    def watch():
        sys.stdin.read()
        os.killpg(os.getpgrp(), signal.SIGTERM)
    threading.Thread(target=watch, daemon=True).start()


def _outcome(value):
    if (not isinstance(value, dict) or set(value) != {"success", "error"}
            or type(value["success"]) is not bool
            or (value["success"] and value["error"] is not None)
            or (not value["success"] and not isinstance(value["error"], str))):
        raise ValueError("invalid sync worker outcome")
    return Outcome(**value)


@diagnostics.measured("statistics.sync_worker")
def sync_all(*, on_complete=None, on_statistics=None, **kwargs):
    # stdin is a lifetime channel: close-on-exec and parent exit both yield EOF.
    completed = {}
    result = None
    with supervised_worker(__file__) as process:
        process.stdin.write(json.dumps(kwargs) + "\n")
        process.stdin.flush()
        for line in process.stdout:
            message = json.loads(line)
            phase = message.get("phase")
            if result is not None:
                raise ValueError("sync worker sent data after final result")
            if phase in ("statistics", "complete"):
                if set(message) != {"phase", "machine", "outcome"} or not isinstance(message["machine"], str):
                    raise ValueError("invalid sync worker progress")
                name, outcome = message["machine"], _outcome(message["outcome"])
                if name in completed:
                    raise ValueError("sync worker repeated a completed machine")
                callback = on_statistics if phase == "statistics" else on_complete
                if phase == "complete":
                    completed[name] = outcome
                if callback:
                    with diagnostics.monitor.stage("publish_" + phase):
                        callback(name, outcome)
            elif phase == "result" and set(message) == {"phase", "results"}:
                result = {name: _outcome(value) for name, value in message["results"].items()}
                if result != completed:
                    raise ValueError("sync worker final result disagrees with progress")
            else:
                raise ValueError("invalid sync worker message")
        code = process.wait()
        if code != 0 or result is None:
            raise RuntimeError("sync worker exited without a complete result (exit %s)" % code)
    if any(not value.success for value in result.values()):
        diagnostics.monitor.outcome("source_failure")
    return result


def _worker():
    import sync

    kwargs = json.loads(sys.stdin.readline())
    output = sys.stdout
    output_lock = threading.Lock()

    watch_parent()

    def outcome(result):
        has_generation = result.generation is not None
        if has_generation == (result.error is not None):
            raise ValueError("sync result has no unambiguous outcome")
        return {"success": has_generation, "error": result.error}

    def emit(message):
        with output_lock:
            output.write(json.dumps(message) + "\n")
            output.flush()

    def progress(phase, name, result):
        emit({"phase": phase, "machine": name, "outcome": outcome(result)})

    # Existing exporters can print diagnostics; stdout is reserved for protocol.
    with redirect_stdout(sys.stderr):
        results = sync.sync_all(
            **kwargs,
            on_statistics=lambda name, value: progress("statistics", name, value),
            on_complete=lambda name, value: progress("complete", name, value),
        )
        try:
            values = {name: outcome(value) for name, value in results.items()}
        finally:
            for value in results.values():
                if value.generation is not None:
                    value.generation.close()
        emit({"phase": "result", "results": values})


if __name__ == "__main__":
    if sys.argv[1:] != ["--worker"]:
        raise SystemExit("internal sync worker")
    _worker()
