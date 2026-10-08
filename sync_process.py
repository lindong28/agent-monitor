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


@dataclass(frozen=True)
class Outcome:
    success: bool
    error: object = None


_lock = threading.Lock()
_active = set()
_stopping = False


def _stop(process):
    # The worker is a session leader; include local export/SSH descendants.
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()
    finally:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


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
        process = subprocess.Popen(
            [sys.executable, str(Path(path).resolve()), "--worker"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=text,
            start_new_session=True,
        )
        _active.add(process)
    try:
        yield process
    finally:
        if process.poll() is None:
            _stop(process)
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
