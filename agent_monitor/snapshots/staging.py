"""Owned local sync scratch; unknown or unfinished transfers are not reaped."""
import contextlib
import fcntl
import logging
import os
from pathlib import Path
import signal
import stat
import subprocess
import sys
import uuid

LOGGER = logging.getLogger("agent-monitor")
PROTOCOL = b"agent-monitor-local-sync-v1\n"
REGISTRY = ".sync-registry.lock"
LEASE = ".lease"
OWNER = ".owner"
TRANSFER = ".transfer-inflight"

def _identity(lease):
    value = os.fstat(lease)
    return PROTOCOL + f"{value.st_dev}:{value.st_ino}\n".encode("ascii")

def _open_file(directory, name, flags=os.O_RDONLY):
    fd = os.open(name, flags | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600, dir_fd=directory)
    value = os.fstat(fd)
    if not stat.S_ISREG(value.st_mode) or value.st_uid != os.getuid() or value.st_nlink != 1:
        os.close(fd)
        raise OSError("unsafe sync lease or marker")
    return fd

def _same(fd, directory, name):
    held = os.fstat(fd)
    current = os.stat(name, dir_fd=directory, follow_symlinks=False)
    return (held.st_dev, held.st_ino) == (current.st_dev, current.st_ino)

def _remove_tree(parent, name):
    """Descriptor-relative deletion, including on supported Python 3.10."""
    directory = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
    try:
        for entry in os.listdir(directory):
            value = os.stat(entry, dir_fd=directory, follow_symlinks=False)
            if stat.S_ISDIR(value.st_mode):
                _remove_tree(directory, entry)
            else:
                os.unlink(entry, dir_fd=directory)
        if not _same(directory, parent, name):
            raise OSError("sync payload directory replaced")
        os.rmdir(name, dir_fd=parent)
    finally:
        os.close(directory)

@contextlib.contextmanager
def _registry(root):
    # Existing state symlinks are legitimate; candidates below it are not.
    rootfd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    lock = None
    try:
        lock = _open_file(rootfd, REGISTRY, os.O_RDWR | os.O_CREAT)
        fcntl.flock(lock, fcntl.LOCK_EX)
        if not _same(lock, rootfd, REGISTRY):
            raise OSError("sync registry lock replaced")
        yield rootfd
    finally:
        if lock is not None:
            os.close(lock)
        os.close(rootfd)

def _reap_one(rootfd, name, retained):
    directory = lease = None
    phase = "open"
    def keep(reason):
        count, examples = retained.setdefault(reason, [0, []])
        retained[reason][0] = count + 1
        if len(examples) < 2:
            examples.append(name)
        return False
    try:
        directory = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=rootfd)
        if os.fstat(directory).st_uid != os.getuid():
            return keep("foreign-owner")
        phase = "ownership"
        lease = _open_file(directory, LEASE, os.O_RDWR)
        try:
            fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        owner = _open_file(directory, OWNER)
        try:
            expected = _identity(lease)
            actual = os.read(owner, len(expected) + 1)
            if not actual.startswith(PROTOCOL):
                return keep("unknown-protocol")
            if actual != expected:
                return keep("identity-changed")
        finally:
            os.close(owner)
        names = set(os.listdir(directory))
        if TRANSFER in names:
            return keep("unfinished-transfer")
        if names - {LEASE, OWNER, "bundle"}:
            return keep("unknown-contents")
        if not _same(lease, directory, LEASE) or not _same(directory, rootfd, name):
            return keep("identity-changed")
        # Keep metadata until large payload deletion finishes, including retries.
        phase = "cleanup"
        if "bundle" in names:
            _remove_tree(directory, "bundle")
        os.unlink(OWNER, dir_fd=directory)
        os.unlink(LEASE, dir_fd=directory)
        os.rmdir(name, dir_fd=rootfd)
        return True
    except FileNotFoundError:
        if phase == "open":
            return False  # Candidate already disappeared; no retained object.
        return keep("missing-ownership" if phase == "ownership" else "cleanup-failed")
    except OSError as exc:
        reason = "unsafe-or-unreadable" if phase != "cleanup" else "cleanup-failed"
        return keep(f"{reason}(errno={exc.errno})")
    finally:
        if lease is not None:
            os.close(lease)
        if directory is not None:
            os.close(directory)

def _reap(rootfd, names, root):
    retained = {}
    removed = sum(_reap_one(rootfd, name, retained) for name in names)
    if retained:
        summary = "; ".join(f"{reason}={count} examples={examples!r}"
                            for reason, (count, examples) in sorted(retained.items()))
        LOGGER.warning("Local sync scratch retained; root=%s; %s; inspect ownership and active users before cleanup",
                       root, summary)
    return removed

def reap(root):
    """Return actual removals of completed, newly owned scratch."""
    with _registry(root) as rootfd:
        return _reap(rootfd, (name for name in os.listdir(rootfd)
                             if name.startswith(".sync-") and name != REGISTRY), root)

class Staging:
    def __init__(self, root):
        self.root = Path(root)
        self.name = ".sync-" + uuid.uuid4().hex
        self.path = self.root / self.name
        self.lease = self.directory = None

    def __enter__(self):
        with _registry(self.root) as rootfd:
            _reap(rootfd, (name for name in os.listdir(rootfd)
                           if name.startswith(".sync-") and name != REGISTRY), self.root)
            os.mkdir(self.name, mode=0o700, dir_fd=rootfd)
            try:
                self.directory = os.open(self.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=rootfd)
                self.lease = _open_file(self.directory, LEASE, os.O_RDWR | os.O_CREAT | os.O_EXCL)
                fcntl.flock(self.lease, fcntl.LOCK_EX)
                owner = _open_file(self.directory, OWNER, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
                try:
                    os.write(owner, _identity(self.lease))
                finally:
                    os.close(owner)
            except BaseException:
                self._close()
                raise
        return self

    def _close(self):
        # LOCK_UN would release the description inherited by children.
        for attribute in ("lease", "directory"):
            fd = getattr(self, attribute)
            if fd is not None:
                os.close(fd)
                setattr(self, attribute, None)

    def __exit__(self, *exception):
        self._close()
        try:
            with _registry(self.root) as rootfd:
                _reap(rootfd, [self.name], self.root)
        except OSError as exc:
            LOGGER.warning("Local sync scratch retained: registry unavailable (errno=%s)", exc.errno)

    def transfer_runner(self, runner):
        def run(args, **kwargs):
            # A dead guardian does not prove children which close inherited FDs
            # finished. Publish intent before spawn; only the guardian clears it.
            fd = _open_file(self.directory, TRANSFER, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
            os.close(fd)
            if runner is not subprocess.run:
                result = runner(args, **kwargs)
                os.unlink(TRANSFER, dir_fd=self.directory)
                return result
            command = [sys.executable, str(Path(__file__).resolve()), str(self.directory),
                       str(self.lease), str(kwargs["timeout"]), *args]
            return runner(command, pass_fds=(self.directory, self.lease), **kwargs)
        return run

def _transfer_main():
    directory, lease = map(int, sys.argv[1:3])
    timeout = float(sys.argv[3])
    try:
        # communicate observes EOF as well as command exit. Guardian survives
        # parent death; child inheritance is additional protection.
        child = subprocess.Popen(sys.argv[4:], pass_fds=(lease,), stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, start_new_session=True)
        try:
            # Leave the caller's deadline room to receive the failure. The
            # unreaped group leader prevents PID reuse before the group signal.
            stdout, stderr = child.communicate(timeout=max(0, timeout - 0.5))
        except subprocess.TimeoutExpired:
            os.killpg(child.pid, signal.SIGKILL)
            child.communicate(timeout=0.4)
            raise
    except (OSError, subprocess.TimeoutExpired) as exc:
        print("local sync transfer unfinished: " + type(exc).__name__, file=sys.stderr)
        return 125  # Keep intent; descendants' lifetime remains unproven.
    os.unlink(TRANSFER, dir_fd=directory)
    sys.stdout.buffer.write(stdout)
    sys.stderr.buffer.write(stderr)
    return child.returncode if child.returncode >= 0 else 128 - child.returncode

if __name__ == "__main__":
    raise SystemExit(_transfer_main())
