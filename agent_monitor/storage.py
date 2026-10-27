"""Shared ownership of rollup locks, read snapshots and bucket timezone checks.

All cooperating writers and readers use this module's one lock registry. It has
no dependency on rollup computation or log aggregation.
"""
import errno
import fcntl
import os
import shutil
import sqlite3
import tempfile
import threading
import time
from contextlib import closing, contextmanager
from pathlib import Path
from zoneinfo import ZoneInfo


BUCKET_TIMEZONE_NAME = "Asia/Shanghai"


BUCKET_TIMEZONE = ZoneInfo(BUCKET_TIMEZONE_NAME)


_LOCKS_GUARD = threading.Lock()


_PATH_LOCKS = {}


_LOCK_OWNERSHIP = threading.local()


_OPEN_LOCK_FDS = set()


_ACTIVE_LOCK_HOLDERS = {}


class RollupLockNotHeld(RuntimeError):
    pass


class _RollupLockUnavailable(BlockingIOError):
    pass


class _RollupLockNotInitialized(FileNotFoundError):
    pass


class RollupLockNestingError(RuntimeError):
    pass


class RollupBucketTimezoneMigrationRequired(RuntimeError):
    pass


@contextmanager
def rollup_lock(db_path, blocking=True, timeout=30, create=True):
    if timeout is None:
        raise ValueError("timeout=None is not supported for rollup locks")
    if timeout < 0:
        raise ValueError("timeout must be >= 0")

    normalized_path = normalize_db_path(db_path)
    deadline = time.monotonic() + timeout
    creator_pid = os.getpid()
    lock_fd = None
    path_lock = None
    acquired_thread_lock = False
    try:
        if create:
            Path(normalized_path).parent.mkdir(parents=True, exist_ok=True)
        try:
            lock_fd = _open_lock_file(normalized_path + ".lock", create=create)
        except FileNotFoundError as exc:
            raise _RollupLockNotInitialized(
                errno.ENOENT,
                "rollup lock has not been initialised",
                normalized_path + ".lock",
            ) from exc
        lock_identity = _lock_identity(lock_fd)
        ownership = _ownership_holders()
        if ownership and lock_identity not in ownership:
            raise RollupLockNestingError(
                "cannot acquire rollup lock for %s while another rollup database lock is held"
                % normalized_path
            )

        path_lock = _path_lock(lock_identity)
        acquired_thread_lock = _acquire_thread_lock(path_lock, blocking, deadline)
        if not acquired_thread_lock:
            raise _RollupLockUnavailable(
                errno.EWOULDBLOCK,
                "rollup lock is already held",
                normalized_path,
            )

        holder = ownership.get(lock_identity)
        if holder is not None:
            if holder["pid"] != creator_pid:
                raise RollupLockNotHeld(
                    "rollup lock ownership for %s belongs to another process" % normalized_path
                )
            holder["depth"] += 1
            _close_lock_fd(lock_fd)
            lock_fd = None
            try:
                yield
            finally:
                if os.getpid() == creator_pid and holder["pid"] == creator_pid:
                    holder["depth"] -= 1
                    path_lock.release()
                    acquired_thread_lock = False
            return

        _acquire_file_lock(lock_fd, normalized_path, blocking, deadline)
        holder = {
            "depth": 1,
            "fd": lock_fd,
            "pid": creator_pid,
            "path": normalized_path,
        }
        ownership[lock_identity] = holder
        _register_active_holder(lock_identity, holder)
        try:
            yield
        finally:
            if os.getpid() == creator_pid and holder["pid"] == creator_pid:
                if ownership.get(lock_identity) is holder:
                    ownership.pop(lock_identity, None)
                _unregister_active_holder(lock_identity, holder)
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
    finally:
        if os.getpid() == creator_pid:
            if lock_fd is not None:
                _close_lock_fd(lock_fd)
            if acquired_thread_lock:
                path_lock.release()


def ensure_bucket_timezone(conn, db_path):
    row = conn.execute(
        "SELECT value FROM rollup_meta WHERE key = 'bucket_timezone'"
    ).fetchone()
    configured = row[0] if row else None
    if configured == BUCKET_TIMEZONE_NAME:
        return

    has_history = conn.execute("SELECT 1 FROM daily_rollup LIMIT 1").fetchone() is not None
    if configured is None and not has_history:
        conn.execute(
            "INSERT INTO rollup_meta (key, value) VALUES ('bucket_timezone', ?)",
            (BUCKET_TIMEZONE_NAME,),
        )
        return

    actual = configured or "unmarked"
    raise RollupBucketTimezoneMigrationRequired(
        "rollup database %s uses %s day buckets; explicit migration to %s is required"
        % (Path(db_path), actual, BUCKET_TIMEZONE_NAME)
    )


def require_bucket_timezone(conn, db_path):
    if not table_exists(conn, "daily_rollup"):
        return
    configured = None
    if table_exists(conn, "rollup_meta"):
        row = conn.execute(
            "SELECT value FROM rollup_meta WHERE key = 'bucket_timezone'"
        ).fetchone()
        configured = row[0] if row else None
    if configured == BUCKET_TIMEZONE_NAME:
        return
    has_history = conn.execute("SELECT 1 FROM daily_rollup LIMIT 1").fetchone() is not None
    if configured is None and not has_history:
        return
    actual = configured or "unmarked"
    raise RollupBucketTimezoneMigrationRequired(
        "rollup database %s uses %s day buckets; explicit migration to %s is required"
        % (Path(db_path), actual, BUCKET_TIMEZONE_NAME)
    )


@contextmanager
def read_connection(
    db_path,
    immutable=False,
    *,
    allow_unmarked_for_marker_probe=False,
    private_snapshot=False,
):
    db_path = Path(db_path)
    if not db_path.is_file():
        yield None
        return
    base_uri = db_path.resolve().as_uri()
    if private_snapshot:
        with _private_read_copy(db_path) as copy_path:
            with read_connection(copy_path, immutable=True, allow_unmarked_for_marker_probe=allow_unmarked_for_marker_probe) as conn:
                yield conn
        return
    try:
        conn = _open_read_only(base_uri, db_path, immutable)
    except sqlite3.OperationalError as original_error:
        if immutable:
            raise
        # Missing WAL sidecars can make mode=ro unavailable. Never infer an
        # immutable lifetime from a momentary filesystem observation.
        try:
            with _private_read_copy(db_path) as copy_path:
                with read_connection(copy_path, immutable=True, allow_unmarked_for_marker_probe=allow_unmarked_for_marker_probe) as conn:
                    yield conn
        except _RollupLockNotInitialized:
            raise original_error
        return
    try:
        if not allow_unmarked_for_marker_probe:
            require_bucket_timezone(conn, db_path)
        yield conn
    finally:
        conn.close()


def _open_read_only(base_uri, db_path, immutable):
    """Immutable requires a lease or privately owned copy, not a WAL stat."""
    if immutable and _wal_has_frames(db_path):
        raise sqlite3.OperationalError(
            "cannot use immutable mode while a nonempty WAL exists"
        )
    query = "?mode=ro&immutable=1" if immutable else "?mode=ro"
    return _connect_read_only(base_uri + query)


@contextmanager
def _private_read_copy(db_path):
    """Copy a cooperating mutable store without changing its SQLite sidecars.

    All source writers, including connection close/checkpoint, must hold the
    same existing lock. Recover WAL only in the private copy; SHM is rebuilt.
    """
    with tempfile.TemporaryDirectory(prefix="agent-monitor-read-") as tmp:
        target = Path(tmp) / "snapshot.db"
        with rollup_lock(db_path, create=False):
            shutil.copyfile(db_path, target)
            wal = _wal_sibling(db_path)
            if wal.exists():
                shutil.copyfile(wal, _wal_sibling(target))
        # A writable private connection can recover WAL even when mode=ro
        # cannot create SHM. Close it before opening the private reader.
        with closing(sqlite3.connect(target)) as recovery:
            recovery.execute("SELECT 1 FROM sqlite_master LIMIT 1").fetchone()
        yield target


def _connect_read_only(uri):
    conn = sqlite3.connect(uri, uri=True, timeout=30)
    try:
        conn.execute("PRAGMA query_only=ON")
        # sqlite3.connect is lazy and PRAGMA query_only never touches the file,
        # so without a real read the caller learns the database is unopenable
        # only once some later query fails -- too late for a fallback to help.
        conn.execute("SELECT 1 FROM sqlite_master LIMIT 1").fetchone()
    except BaseException:
        conn.close()
        raise
    return conn


def _wal_sibling(db_path):
    return Path(str(db_path) + "-wal")


def _wal_has_frames(db_path):
    """Conservatively detect nonempty WAL; checkpoint need not truncate it."""
    wal = _wal_sibling(db_path)
    try:
        return wal.stat().st_size > 0
    except FileNotFoundError:
        return False


def table_exists(conn, table_name):
    return (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table_name,),
        ).fetchone()
        is not None
    )


def bucket_timezone(db_path):
    # Deliberate diagnostic exception: admission must inspect the marker in
    # order to decide whether the database may enter the normal read path.
    with read_connection(
        db_path, allow_unmarked_for_marker_probe=True
    ) as conn:
        if conn is None or not table_exists(conn, "rollup_meta"):
            return None
        row = conn.execute(
            "SELECT value FROM rollup_meta WHERE key = 'bucket_timezone'"
        ).fetchone()
    return row[0] if row else None


def normalize_db_path(db_path):
    return os.path.realpath(os.path.abspath(os.path.expanduser(os.fspath(db_path))))


def _lock_identity(lock_fd):
    stat_result = os.fstat(lock_fd)
    return stat_result.st_dev, stat_result.st_ino


def _path_lock(lock_identity):
    with _LOCKS_GUARD:
        return _PATH_LOCKS.setdefault(lock_identity, threading.RLock())


def _ownership_holders():
    holders = getattr(_LOCK_OWNERSHIP, "holders", None)
    if holders is None:
        holders = {}
        _LOCK_OWNERSHIP.holders = holders
    return holders


def _open_lock_file(lock_path, create=True):
    with _LOCKS_GUARD:
        flags = os.O_RDWR | (os.O_CREAT if create else 0)
        lock_fd = os.open(lock_path, flags, 0o600)
        _OPEN_LOCK_FDS.add(lock_fd)
        return lock_fd


def _close_lock_fd(lock_fd):
    with _LOCKS_GUARD:
        _OPEN_LOCK_FDS.discard(lock_fd)
        os.close(lock_fd)


def _register_active_holder(lock_identity, holder):
    with _LOCKS_GUARD:
        _ACTIVE_LOCK_HOLDERS[lock_identity] = holder


def _unregister_active_holder(lock_identity, holder):
    with _LOCKS_GUARD:
        if _ACTIVE_LOCK_HOLDERS.get(lock_identity) is holder:
            _ACTIVE_LOCK_HOLDERS.pop(lock_identity, None)


def _reset_rollup_locks_after_fork():
    global _ACTIVE_LOCK_HOLDERS
    global _LOCKS_GUARD
    global _LOCK_OWNERSHIP
    global _OPEN_LOCK_FDS
    global _PATH_LOCKS

    for lock_fd in tuple(_OPEN_LOCK_FDS):
        try:
            os.close(lock_fd)
        except OSError:
            pass
    _LOCKS_GUARD = threading.Lock()
    _PATH_LOCKS = {}
    _LOCK_OWNERSHIP = threading.local()
    _OPEN_LOCK_FDS = set()
    _ACTIVE_LOCK_HOLDERS = {}


def _prepare_rollup_locks_for_fork():
    _LOCKS_GUARD.acquire()


def _resume_rollup_locks_after_fork_parent():
    _LOCKS_GUARD.release()


def assert_lock_held(db_path):
    normalized_path = normalize_db_path(db_path)
    try:
        stat_result = os.stat(normalized_path + ".lock")
    except OSError:
        holder = None
    else:
        holder = _ownership_holders().get((stat_result.st_dev, stat_result.st_ino))
    if holder is None or holder["pid"] != os.getpid() or holder["depth"] < 1:
        raise RollupLockNotHeld("rollup lock is not held for %s" % normalized_path)


def _acquire_thread_lock(path_lock, blocking, deadline):
    if not blocking:
        return path_lock.acquire(blocking=False)
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("timed out acquiring in-process rollup lock")
    if not path_lock.acquire(timeout=remaining):
        raise TimeoutError("timed out acquiring in-process rollup lock")
    return True


def _acquire_file_lock(lock_fd, normalized_path, blocking, deadline):
    while True:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except BlockingIOError:
            if not blocking:
                raise _RollupLockUnavailable(
                    errno.EWOULDBLOCK,
                    "rollup lock is already held",
                    normalized_path,
                )
            if time.monotonic() >= deadline:
                raise TimeoutError("timed out acquiring rollup lock for %s" % normalized_path)
            sleep_for = 0.05
            sleep_for = min(sleep_for, max(0, deadline - time.monotonic()))
            time.sleep(sleep_for)


if hasattr(os, "register_at_fork"):
    os.register_at_fork(
        before=_prepare_rollup_locks_for_fork,
        after_in_parent=_resume_rollup_locks_after_fork_parent,
        after_in_child=_reset_rollup_locks_after_fork,
    )
