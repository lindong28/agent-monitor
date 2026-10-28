"""Best-effort copytruncate for existing service FDs, not an audit store."""
import atexit
import fcntl
import json
import os
from pathlib import Path
import stat
import sys
import time

NAMES = ('server.log', 'hub.log', 'rollup-daemon.log')
DEFAULT_BYTES = 8 * 1024 * 1024
CHECK_SECONDS = 30.0
_monitors = {}
_warn_after = 0.0


def _warn(error):
    global _warn_after
    now = time.monotonic()
    if now >= _warn_after:
        _warn_after = now + 300
        try:
            print('agent-monitor: service log capacity maintenance failed ('
                  + type(error).__name__ + '); service continues, logs may grow. '
                  'Check log permissions and AGENT_MONITOR_LOG_* settings.', file=sys.stderr)
        except Exception:
            pass


def _regular(info):
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1:
        raise ValueError('log ownership or file type is unverified')


def _identity(info):
    return info.st_dev, info.st_ino


class LogMaintenance:
    def __init__(self, path, segment_bytes=DEFAULT_BYTES, segments=2):
        if segment_bytes < 1 or not 2 <= segments <= 100:
            raise ValueError('invalid log capacity')
        self.path = Path(path)
        self.segment_bytes = segment_bytes
        self.segments = segments
        self.next_check = 0.0

    def check(self, *, force=False):
        now = time.monotonic()
        if not force and now < self.next_check:
            return
        self.next_check = now + CHECK_SECONDS
        try:
            self._maintain()
        except Exception as error:
            _warn(error)

    def _maintain(self):
        info = self.path.lstat()
        _regular(info)
        if info.st_size <= self.segment_bytes:
            return
        fd = os.open(self.path, os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            opened = os.fstat(fd)
            _regular(opened)
            if _identity(info) != _identity(opened):
                raise ValueError('log changed')
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return
            size = os.fstat(fd).st_size
            if size <= self.segment_bytes:
                return
            backups = [self.path.with_name(self.path.name + '.' + str(i))
                       for i in range(1, self.segments)]
            obsolete = []
            # Only the helper's numbered generations, never manual snapshots.
            for candidate in self.path.parent.glob(self.path.name + '.*'):
                suffix = candidate.name[len(self.path.name) + 1:]
                if suffix.isascii() and suffix.isdecimal() and str(int(suffix)) == suffix:
                    if self.segments <= int(suffix) < 100:
                        _regular(candidate.lstat())
                        obsolete.append(candidate)
            for backup in backups:
                try:
                    _regular(backup.lstat())
                except FileNotFoundError:
                    pass
            # A hard exit may skip finally. Reuse one reserved pending inode
            # under the active log's flock rather than accumulating temp files.
            temporary = self.path.with_name('.' + self.path.name + '.pending')
            out = os.open(temporary, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
            with os.fdopen(out, 'wb') as stream:
                pending_info = os.fstat(stream.fileno())
                _regular(pending_info)
                if _identity(temporary.lstat()) != _identity(pending_info):
                    raise ValueError('pending log changed')
                os.ftruncate(stream.fileno(), 0)
                offset = max(0, size - self.segment_bytes)
                while offset < size:
                    block = os.pread(fd, min(65536, size - offset), offset)
                    if not block:
                        raise OSError('log shortened during copy')
                    stream.write(block)
                    offset += len(block)
                stream.flush()
                os.fsync(stream.fileno())
            current = self.path.lstat()
            _regular(current)
            if _identity(current) != _identity(opened):
                raise ValueError('log replaced during maintenance')
            pending_current = temporary.lstat()
            _regular(pending_current)
            if _identity(pending_current) != _identity(pending_info):
                raise ValueError('pending log replaced during maintenance')
            for index in range(len(backups) - 1, 0, -1):
                if backups[index - 1].exists():
                    os.replace(backups[index - 1], backups[index])
            os.replace(temporary, backups[0])
            # Keep the inode used by inherited FDs. Concurrent bytes can be lost.
            os.ftruncate(fd, 0)
            for candidate in obsolete:
                candidate.unlink()
        finally:
            os.close(fd)


def initialize(root):
    """Attach only to actual managed stdout/stderr, preserving PIPE protocols."""
    try:
        settings = None
        for name in NAMES:
            path = Path(root) / 'state' / name
            try:
                info = path.lstat()
            except FileNotFoundError:
                continue
            matched = False
            for fd in (1, 2):
                if _identity(os.fstat(fd)) == _identity(info):
                    _regular(info)
                    flags = fcntl.fcntl(fd, fcntl.F_GETFL)
                    fcntl.fcntl(fd, fcntl.F_SETFL, flags | os.O_APPEND)
                    matched = True
            if matched and path not in _monitors:
                if settings is None:
                    settings = configuration(root)
                size, count = settings
                monitor = LogMaintenance(path, size, count)
                _monitors[path] = monitor
                monitor.check(force=True)
    except Exception as error:
        _warn(error)


def configuration(root):
    """Persistent policy works for launchd as well as interactive launchers."""
    policy = Path(root) / 'state' / 'service-logs.json'
    try:
        fd = os.open(policy, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        values = {}
    else:
        with os.fdopen(fd, 'rb') as stream:
            _regular(os.fstat(stream.fileno()))
            data = stream.read(4097)
            if len(data) > 4096:
                raise ValueError('log policy is oversized')
            values = json.loads(data)
    if not isinstance(values, dict) or set(values) - {'segment_bytes', 'segments'}:
        raise ValueError('invalid log policy')
    size = os.environ.get('AGENT_MONITOR_LOG_SEGMENT_BYTES', values.get('segment_bytes', DEFAULT_BYTES))
    count = os.environ.get('AGENT_MONITOR_LOG_SEGMENTS', values.get('segments', 2))
    if type(size) not in (int, str) or type(count) not in (int, str):
        raise ValueError('invalid log policy')
    return int(size), int(count)


def maintain(*, force=False):
    for monitor in tuple(_monitors.values()):
        monitor.check(force=force)


atexit.register(lambda: maintain(force=True))
