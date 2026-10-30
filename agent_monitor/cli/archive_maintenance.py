"""Explicit, bounded physical maintenance of the retained usage archive.

Run as a dedicated CLI process: main configures SQLite temporary storage before
opening any SQLite connection. This module never restores a backup over live data.
"""
import argparse
from contextlib import closing
import json
import math
import os
from pathlib import Path
import shutil
import sqlite3
import stat
import sys
import tempfile
import time

from agent_monitor.paths import state_path


class BudgetExpired(TimeoutError):
    pass


class Budget:
    def __init__(self, seconds):
        if not math.isfinite(seconds) or seconds <= 0:
            raise ValueError("budget seconds must be finite and greater than zero")
        self.started = time.monotonic()
        self.deadline = self.started + seconds

    def remaining(self):
        value = self.deadline - time.monotonic()
        if value <= 0:
            raise BudgetExpired("maintenance time budget expired")
        return value

    def progress(self):
        return int(time.monotonic() >= self.deadline)

    def backup_progress(self, status, remaining, total):
        self.remaining()


def _connect(path, mode, budget):
    conn = sqlite3.connect(path.as_uri() + "?mode=" + mode + "&vfs=unix",
                           uri=True, timeout=min(1.0, budget.remaining()))
    conn.set_progress_handler(budget.progress, 1000)
    return conn


def _quick_check(conn, budget):
    budget.remaining()
    rows = conn.execute("PRAGMA quick_check").fetchall()
    if rows != [("ok",)]:
        raise sqlite3.DatabaseError("SQLite quick_check did not return ok: " + repr(rows[:3]))
    budget.remaining()


def _stats(conn, archive):
    # Reject unrelated databases without initializing or upgrading their schema.
    conn.execute("SELECT dedup_key, entry FROM usage_events LIMIT 0")
    conn.execute("SELECT key, value FROM archive_meta LIMIT 0")
    page_size = conn.execute("PRAGMA page_size").fetchone()[0]
    pages = conn.execute("PRAGMA page_count").fetchone()[0]
    file_bytes = archive.stat().st_size
    wal = Path(str(archive) + "-wal")
    return dict(file_bytes=file_bytes, wal_bytes=wal.stat().st_size if wal.exists() else 0,
                logical_bytes=max(file_bytes, pages * page_size), page_size=page_size,
                page_count=pages, free_pages=conn.execute("PRAGMA freelist_count").fetchone()[0],
                journal_mode=conn.execute("PRAGMA journal_mode").fetchone()[0])


def _space(archive, backup, logical_bytes):
    same_volume = archive.parent.stat().st_dev == backup.parent.stat().st_dev
    source_free = shutil.disk_usage(archive.parent).free
    backup_free = shutil.disk_usage(backup.parent).free
    source_required = logical_bytes * (3 if same_volume else 2)
    backup_required = logical_bytes if not same_volume else source_required
    result = dict(same_volume=same_volume, source_free_bytes=source_free,
                  backup_free_bytes=backup_free, source_required_bytes=source_required,
                  backup_required_bytes=backup_required)
    if source_free < source_required or backup_free < backup_required:
        raise OSError("insufficient space: " + json.dumps(result, sort_keys=True))
    return result


def _paths(archive, rollup):
    archive = Path(archive).expanduser().resolve(strict=True)
    rollup = Path(rollup).expanduser().resolve()
    if archive.name != "usage_archive.sqlite3" or rollup.name != "rollup.db":
        raise ValueError("expected usage_archive.sqlite3 and rollup.db in one state directory")
    if archive.parent != rollup.parent:
        raise ValueError("archive and rollup lock must belong to the same state directory")
    if not archive.is_file():
        raise ValueError("archive must be an existing regular file")
    return archive, rollup


def _file_identity(value):
    return (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns)


def _check_identity(path, expected):
    value = path.lstat()
    if not stat.S_ISREG(value.st_mode) or _file_identity(value) != expected:
        raise OSError("backup file identity changed: " + str(path))


def maintain(archive, rollup, *, compact=False, backup=None, budget_seconds=300):
    """Called by the dedicated CLI after SQLITE_TMPDIR has been configured."""
    result = dict(status="failed", action="compact" if compact else "stats", stage="preflight",
                  archive=str(archive), backup=str(backup) if backup else None,
                  backup_state="not_created", source_state="not_modified_by_maintenance")
    budget = None
    working_fd = None
    verified_identity = None
    try:
        budget = Budget(budget_seconds)
        archive, rollup = _paths(archive, rollup)
        result["archive"] = str(archive)
        if sys.platform not in ("darwin", "linux"):
            raise ValueError("archive maintenance supports macOS/Linux standard Unix VFS only")
        if compact:
            if not backup:
                raise ValueError("compact requires a new persistent --backup path")
            if not rollup.is_file():
                raise ValueError("existing same-directory rollup.db is required for compact")
            if os.environ.get("SQLITE_TMPDIR") != str(archive.parent):
                raise ValueError("run compact through the dedicated archive CLI to pin SQLite temporary files")
            if not os.access(archive.parent, os.W_OK | os.X_OK):
                raise PermissionError("archive directory must be writable and searchable for SQLite temporary files")
            backup = Path(backup).expanduser().absolute()
            # lexists also rejects dangling symlinks; never overwrite any target.
            if os.path.lexists(backup):
                raise FileExistsError("backup target already exists; choose a new persistent path")
            backup = backup.parent.resolve(strict=True) / backup.name
            live_paths = {Path(str(path) + suffix) for path in (archive, rollup)
                          for suffix in ("", "-wal", "-shm", "-journal", ".lock")}
            if backup in live_paths:
                raise ValueError("backup must be separate from live state files")
            if any(os.path.lexists(str(backup) + suffix) for suffix in ("-wal", "-shm", "-journal")):
                raise FileExistsError("backup sidecar already exists; choose a new persistent path")
            result["backup"] = str(backup)
        elif backup:
            raise ValueError("--backup is only valid with compact")

        if not compact:
            result["stage"] = "stats"
            with closing(_connect(archive, "ro", budget)) as conn:
                result["before"] = _stats(conn, archive)
            budget.remaining()
            result["status"] = "ok"
            return result

        # Imported after main sets SQLITE_TMPDIR; this reuses the writer's lock
        # registry and filesystem identity, and does not create missing locks.
        from agent_monitor.storage import rollup_lock
        result["stage"] = "lock"
        with rollup_lock(rollup, timeout=budget.remaining(), create=False):
            budget.remaining()
            with closing(_connect(archive, "rw", budget)) as conn:
                result["stage"] = "preflight"
                result["before"] = _stats(conn, archive)
                result["space"] = _space(archive, backup, result["before"]["logical_bytes"])
                result["stage"] = "backup"
                # SQLite never opens the caller-visible destination. Its only
                # writable backup is inside a newly owned private directory.
                work_dir = Path(tempfile.mkdtemp(prefix=".archive-backup-", dir=backup.parent))
                work_file = work_dir / "snapshot.sqlite3"
                result["backup_work_path"] = str(work_file)
                working_fd = os.open(work_file, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
                result["backup_state"] = "created_unverified"
                with closing(_connect(work_file, "rw", budget)) as target:
                    _check_identity(work_file, _file_identity(os.fstat(working_fd)))
                    conn.backup(target, pages=256, progress=budget.backup_progress, sleep=0.05)
                    result["stage"] = "backup_check"
                    _quick_check(target, budget)
                saved_stat = os.fstat(working_fd)
                verified_identity = _file_identity(saved_stat)
                _check_identity(work_file, verified_identity)
                os.fsync(working_fd)
                work_directory_fd = os.open(work_dir, os.O_RDONLY)
                try:
                    os.fsync(work_directory_fd)
                finally:
                    os.close(work_directory_fd)
                result["backup_state"] = "working_copy_verified_not_published"
                result["stage"] = "backup_publish"
                # link() atomically refuses an occupied destination, including
                # a symlink introduced since preflight. Never reopen it to write.
                os.link(work_file, backup, follow_symlinks=False)
                result["backup_state"] = "published_unverified"
                _check_identity(backup, verified_identity)
                directory_fd = os.open(backup.parent, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
                result["backup_state"] = "verified"
                result["backup_bytes"] = saved_stat.st_size
                result["backup_identity"] = dict(device=saved_stat.st_dev, inode=saved_stat.st_ino,
                                                 mtime_ns=saved_stat.st_mtime_ns)
                # Recheck the source after backup; non-cooperating writers may
                # have committed since the earlier space measurement.
                latest = _stats(conn, archive)
                required = 2 * latest["logical_bytes"]
                if shutil.disk_usage(archive.parent).free < required:
                    raise OSError("source volume no longer has the required temporary space: %d bytes" % required)
                budget.remaining()
                _check_identity(work_file, verified_identity)
                _check_identity(backup, verified_identity)
                result["stage"] = "vacuum"
                result["source_state"] = "vacuum_attempted_not_verified"
                conn.execute("VACUUM")
                result["source_state"] = "vacuum_completed_not_verified"
                if latest["journal_mode"] == "wal":
                    result["stage"] = "checkpoint"
                    budget.remaining()
                    checkpoint = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
                    if checkpoint[0] != 0:
                        raise sqlite3.OperationalError("WAL checkpoint blocked by another reader/writer")
                result["stage"] = "source_check"
                _quick_check(conn, budget)
                result["after"] = _stats(conn, archive)
                result["source_state"] = "quick_check_ok"
                result["archive_file_reduction_bytes"] = result["before"]["file_bytes"] - result["after"]["file_bytes"]
                result["archive_and_wal_reduction_bytes"] = (result["before"]["file_bytes"] + result["before"]["wal_bytes"]
                    - result["after"]["file_bytes"] - result["after"]["wal_bytes"])
                budget.remaining()
                result["stage"] = "backup_identity_check"
                _check_identity(work_file, verified_identity)
                _check_identity(backup, verified_identity)
        result["stage"] = "complete"
        result["status"] = "ok"
    except (OSError, ValueError, sqlite3.Error, KeyboardInterrupt) as exc:
        result["error"] = {"type": type(exc).__name__, "message": str(exc)}
        if budget is not None and budget.progress():
            result["budget_expired"] = True
    finally:
        if budget is not None:
            result["elapsed_seconds"] = round(time.monotonic() - budget.started, 3)
        if working_fd is not None:
            result["backup_bytes"] = os.fstat(working_fd).st_size
            if verified_identity is not None:
                try:
                    _check_identity(Path(result["backup_work_path"]), verified_identity)
                    _check_identity(Path(result["backup"]), verified_identity)
                except OSError as exc:
                    result["status"] = "failed"
                    if result["backup_state"] in ("verified", "published_unverified"):
                        result["backup_state"] = "published_identity_lost"
                    result.setdefault("error", {"type": type(exc).__name__, "message": str(exc)})
            os.close(working_fd)
    return result


def _render(result, stream):
    if result["status"] != "ok":
        print("Archive maintenance failed during %s: %s" % (result["stage"], result["error"]["message"]), file=stream)
        print("Archive: %s; state: %s." % (result["archive"], result["source_state"]), file=stream)
        print("Backup: %s; state: %s; bytes: %s." % (result["backup"] or "none", result["backup_state"], result.get("backup_bytes", "unknown")), file=stream)
        if result.get("backup_work_path"):
            print("Retained private backup work file: %s." % result["backup_work_path"], file=stream)
        print("No backup was restored over live data. Resolve the reported cause; inspect the archive and retained backup before retrying compact with a new backup path.", file=stream)
        return
    before = result["before"]
    if result["action"] == "stats":
        print("Archive storage statistics (read-only; integrity and consumer results not checked):", file=stream)
        print("%s: file %d bytes; WAL %d bytes; logical budget size %d bytes; reusable pages %d × %d bytes." %
              (result["archive"], before["file_bytes"], before["wal_bytes"], before["logical_bytes"], before["free_pages"], before["page_size"]), file=stream)
        print("No compaction performed. Physical compaction is optional and requires compact --backup NEW_PERSISTENT_PATH.", file=stream)
    else:
        print("Archive compaction completed; archive and backup quick_check passed.", file=stream)
        print("%s: %d → %d bytes (file reduction %d bytes; file + WAL reduction %d bytes)." %
              (result["archive"], before["file_bytes"], result["after"]["file_bytes"], result["archive_file_reduction_bytes"], result["archive_and_wal_reduction_bytes"]), file=stream)
        print("Retained backup: %s (%d bytes), verified; represents the backup completion point, not later writes." % (result["backup"], result["backup_bytes"]), file=stream)
        print("Private recovery link: %s (same file, not a second copy). Retain/remove both paths together." % result["backup_work_path"], file=stream)
        print("Archive reduction is not whole-machine net space reclaimed. No history was intentionally deleted; no automatic restore or backup removal is scheduled.", file=stream)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Inspect or explicitly compact the retained usage archive; no history expiry.")
    parser.add_argument("action", nargs="?", choices=("stats", "compact"), default="stats")
    parser.add_argument("--archive", type=Path, default=state_path("usage_archive.sqlite3"))
    parser.add_argument("--rollup-db", type=Path, help="must be rollup.db in the archive's state directory")
    parser.add_argument("--backup", type=Path, help="new persistent file, required for compact; never overwritten")
    parser.add_argument("--budget-seconds", type=float, default=300, help="total cooperative time budget (default 300); filesystem calls may overrun")
    parser.add_argument("--json", action="store_true", help="emit machine-readable result")
    args = parser.parse_args(argv)
    # This CLI is a separate process. Set before its first SQLite connection;
    # explicit Unix VFS and W_OK|X_OK enforce the documented temp-dir route.
    os.environ["SQLITE_TMPDIR"] = str(args.archive.expanduser().resolve().parent)
    result = maintain(args.archive, args.rollup_db or args.archive.with_name("rollup.db"),
                      compact=args.action == "compact", backup=args.backup, budget_seconds=args.budget_seconds)
    if args.json:
        print(json.dumps(result, sort_keys=True))
    else:
        _render(result, sys.stdout if result["status"] == "ok" else sys.stderr)
    return 0 if result["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
