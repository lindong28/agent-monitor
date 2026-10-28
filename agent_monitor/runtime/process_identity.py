"""Standalone launcher ownership. PID existence alone never authorizes a signal."""

import ctypes
import json
import os
from pathlib import Path
import signal
import sys
import time


class IdentityUnknown(RuntimeError):
    pass


def identity(pid):
    if type(pid) is not int or not 1 < pid < 2**31:
        raise IdentityUnknown("invalid process id")
    if sys.platform == "linux":
        try:
            # comm may contain spaces and parentheses; fields follow its final ')'.
            fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
            if fields[0] == "Z":
                return None
            return {"start": fields[19], "boot": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
                    "uid": Path(f"/proc/{pid}").stat().st_uid}
        except FileNotFoundError:
            return None
    if sys.platform == "darwin":
        # Darwin sys/proc_info.h: PROC_PIDTBSDINFO (3), struct proc_bsdinfo.
        class BsdInfo(ctypes.Structure):
            _fields_ = [(name, ctypes.c_uint32) for name in
                        ("flags", "status", "xstatus", "pid", "ppid", "uid", "gid", "ruid", "rgid", "svuid", "svgid", "reserved")]
            _fields_ += [("comm", ctypes.c_char * 16), ("name", ctypes.c_char * 32)]
            _fields_ += [(name, ctypes.c_uint32) for name in ("nfiles", "pgid", "jobc", "tdev", "tpgid", "nice")]
            _fields_ += [("start_sec", ctypes.c_uint64), ("start_usec", ctypes.c_uint64)]
        library = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
        library.proc_pidinfo.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_uint64,
                                        ctypes.c_void_p, ctypes.c_int]
        info = BsdInfo()
        count = library.proc_pidinfo(pid, 3, 0, ctypes.byref(info), ctypes.sizeof(info))
        if count == 0 and ctypes.get_errno() == 3:  # ESRCH
            return None
        if count != ctypes.sizeof(info) or info.pid != pid:
            raise IdentityUnknown("process identity could not be read")
        if info.status == 5:  # SZOMB
            return None
        return {"start": [info.start_sec, info.start_usec], "uid": info.uid}
    raise IdentityUnknown("process identity is unsupported on this platform")


def darwin_group_has_live_members(pgid):
    """Inspect a reserved group after killpg returned Darwin's zombie EPERM."""
    library = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
    listing = library.proc_listpgrppids
    listing.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_int]
    capacity = listing(pgid, None, 0)
    if capacity <= 0:
        raise IdentityUnknown("process group membership is unavailable")
    pids = (ctypes.c_int * (capacity + 32))()
    count = listing(pgid, pids, ctypes.sizeof(pids))
    if count < 0 or count >= len(pids):
        raise IdentityUnknown("process group membership is incomplete")
    return any(identity(pid) is not None for pid in pids[:count])


def record(state, root, pid):
    """Called by the launcher for the child it just forked, before exec."""
    observed = identity(pid)
    if observed is None:
        raise IdentityUnknown("launched process already exited")
    path = Path(state) / "pid-identity.json"
    temporary = path.with_name(path.name + ".%d" % os.getpid())
    try:
        with temporary.open("x") as output:
            os.chmod(temporary, 0o600)
            json.dump({"pid": pid, "root": str(Path(root).resolve()), "identity": observed}, output)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def read_owned(state, root):
    state = Path(state)
    try:
        try:
            pid = int((state / "pid").read_text().strip())
            if not 1 < pid < 2**31:
                raise IdentityUnknown("invalid process id")
        except FileNotFoundError:
            pid = None
        try:
            saved = json.loads((state / "pid-identity.json").read_text())
        except FileNotFoundError:
            if pid is None or identity(pid) is None:
                return None
            raise IdentityUnknown("saved process identity is missing")
        if (not isinstance(saved, dict) or type(saved.get("pid")) is not int
                or not 1 < saved["pid"] < 2**31
                or saved.get("root") != str(Path(root).resolve())
                or not isinstance(saved.get("identity"), dict)
                or "start" not in saved["identity"]
                or type(saved["identity"].get("uid")) is not int
                or saved["identity"]["uid"] != os.getuid()):
            raise IdentityUnknown("saved process identity is invalid")
        saved_pid = saved["pid"]
        # The launcher's atomic identity record precedes its compatibility PID
        # file. A missing/dead PID alias must not hide that owned live instance.
        # A different live alias is conflicting evidence, never a fallback.
        if pid is not None and pid != saved_pid and identity(pid) is not None:
            raise IdentityUnknown("PID file conflicts with the recorded instance")
        observed = identity(saved_pid)
        if observed is None:
            return None
        if saved["identity"] != observed or observed["uid"] != os.getuid():
            raise IdentityUnknown("saved process identity does not match")
        return saved_pid, observed
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise IdentityUnknown("saved process identity is missing or invalid") from exc


def stop(state, root, *, grace=10):
    owned = read_owned(state, root)
    if owned is None:
        return
    pid, expected = owned
    descriptor = None
    try:
        if hasattr(os, "pidfd_open") and hasattr(signal, "pidfd_send_signal"):
            descriptor = os.pidfd_open(pid)

        def send(value):
            current = identity(pid)
            if current is None:
                return False
            if current != expected:
                raise IdentityUnknown("process identity changed; no further signal sent")
            if descriptor is not None:
                signal.pidfd_send_signal(descriptor, value)
            else:
                # Darwin has no pidfd equivalent: recheck immediately before kill.
                os.kill(pid, value)
            return True

        if not send(signal.SIGTERM):
            return
        deadline = time.monotonic() + grace
        while time.monotonic() < deadline:
            current = identity(pid)
            if current is None:
                return
            if current != expected:
                raise IdentityUnknown("process identity changed while stopping")
            time.sleep(min(.2, max(0, deadline - time.monotonic())))
        if send(signal.SIGKILL):
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                if identity(pid) is None:
                    return
                time.sleep(.02)
            raise IdentityUnknown("process exit could not be confirmed")
    except ProcessLookupError:
        return
    finally:
        if descriptor is not None:
            os.close(descriptor)


def main():
    action, state, root = sys.argv[1:]
    try:
        if action in ("check", "pid"):
            owned = read_owned(state, root)
            if owned is None:
                return 1
            if action == "pid":
                print(owned[0])
            return 0
        if action == "stop":
            stop(state, root)
            return 0
        raise ValueError("unknown action")
    except (IdentityUnknown, OSError, ValueError) as exc:
        print("agent-monitor: process ownership is unverified or shutdown incomplete; state was retained. "
              "Inspect the recorded process before stopping it; do not delete its PID record to force a restart. "
              + str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
