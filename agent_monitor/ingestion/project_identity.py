"""Project identity persistence, conflict handling and explicit recovery."""
import json
import logging
import os
import sqlite3
import subprocess
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from project_paths import state_path

logger = logging.getLogger(__name__)


PROJECT_IDENTITY_SCHEMA = """
CREATE TABLE IF NOT EXISTS project_identity (
  source_path TEXT PRIMARY KEY,
  project TEXT NOT NULL,
  resolution TEXT NOT NULL CHECK (resolution IN ('remote', 'fallback', 'legacy'))
)
"""


PROJECT_IDENTITY_BLOCKER_SCHEMA = """
CREATE TABLE IF NOT EXISTS project_identity_blocker (
  source_path TEXT PRIMARY KEY,
  reason TEXT NOT NULL,
  resolved_candidate TEXT,
  pin_candidate TEXT,
  first_seen TEXT NOT NULL,
  last_seen TEXT NOT NULL,
  status TEXT NOT NULL CHECK (
    status IN ('active', 'resolved_pinned')
  )
)
"""


class ProjectIdentityBlocked(RuntimeError):
    def __init__(
        self,
        message,
        source_path,
        reason,
        resolved_project=None,
        pin_candidate=None,
    ):
        super().__init__(message)
        self.source_path = source_path
        self.reason = reason
        self.resolved_project = resolved_project
        self.pin_candidate = pin_candidate

    def as_dict(self):
        return {
            "source_path": self.source_path,
            "reason": self.reason,
            "resolved_project": self.resolved_project,
            "pin_candidate": self.pin_candidate,
        }


class ProjectIdentityUnavailable(ProjectIdentityBlocked):
    pass


class ProjectIdentityConflict(ProjectIdentityBlocked):
    pass


class ProjectIdentityRecoveryError(RuntimeError):
    pass


@dataclass(frozen=True)
class _IdentityRollupLockToken:
    identity_db: str
    rollup_db: str
    pid: int


def normalize_remote(remote):
    remote = remote.strip()
    if remote.startswith("git@"):
        remote = remote[4:].replace(":", "/", 1)
    elif remote.startswith("ssh://git@"):
        remote = remote[len("ssh://git@") :].replace(":", "/", 1)
    elif "://" in remote:
        remote = remote.split("://", 1)[1]
    if remote.endswith(".git"):
        remote = remote[:-4]
    return remote.strip("/")


def _sole_project(projects):
    return next(iter(projects)) if len(projects) == 1 else None


class ProjectIdentityStore:
    """One identity database, project cache and in-process mutation lock."""

    def __init__(self, db_path=None):
        self.path = Path(db_path) if db_path is not None else state_path("project_identity.db")
        self.project_cache = {}
        self._lock = threading.RLock()

    @contextmanager
    def locked(self, *, create=True):
        # Keep the process/file identity lock before the in-process load lock.
        from agent_monitor.storage import rollup_lock
        with rollup_lock(self.rollup_db(), create=create), self._lock:
            yield

    def identify_project(self, path, cache=None):
        cache = self.project_cache if cache is None else cache
        with self.rollup_lock():
            with self._lock:
                return self.resolve(path, cache)

    def resolve(self, path, cache, persist=True, ignore_persisted_blocker=False):
        if not ignore_persisted_blocker:
            active_blocker = self._project_identity_blocker(path)
            if active_blocker is not None:
                raise ProjectIdentityBlocked(
                    "Project identity for %s is blocked pending explicit recovery" % path,
                    source_path=path,
                    reason=active_blocker["reason"],
                    resolved_project=active_blocker["resolved_candidate"],
                    pin_candidate=active_blocker["pin_candidate"],
                )
        if path in cache:
            return cache[path]
        stored = self.stored_identity(path, immutable=ignore_persisted_blocker)
        if stored is not None:
            cache[path] = stored
            return stored

        project, resolution = self.resolve_project(path)
        legacy_project, unclaimed_projects = self._legacy_project_identity(
            path,
            project,
            immutable=ignore_persisted_blocker,
        )
        if legacy_project is not None:
            project = legacy_project
            resolution = "legacy"
        elif resolution == "unavailable" and unclaimed_projects:
            raise ProjectIdentityUnavailable(
                "Cannot assign a stable project identity for %s while git resolution is unavailable" % path,
                source_path=path,
                reason="source_unavailable",
                pin_candidate=_sole_project(unclaimed_projects),
            )
        elif resolution == "remote" and unclaimed_projects:
            raise ProjectIdentityUnavailable(
                "Resolved project %s for %s cannot be reconciled with existing rollup history"
                % (project, path),
                source_path=path,
                reason="unreconciled_remote",
                resolved_project=project,
                pin_candidate=_sole_project(unclaimed_projects),
            )
        if resolution in {"no_remote", "unavailable"}:
            resolution = "fallback"
        if not persist:
            return project
        project, stored_resolution, created = self._store_project_identity(path, project, resolution)
        if created and stored_resolution == "fallback":
            logger.warning(
                "Could not resolve git remote for %s; persisted the source path as its stable project identity",
                path,
            )
        cache[path] = project
        return project

    def resolve_project(self, path):
        try:
            result = subprocess.run(
                ["git", "-C", path, "config", "--get", "remote.origin.url"],
                capture_output=True,
                text=True,
                timeout=2,
            )
        except (subprocess.TimeoutExpired, OSError):
            return path, "unavailable"
        if result.returncode == 0 and result.stdout.strip():
            return normalize_remote(result.stdout.strip()), "remote"
        if result.returncode == 1 and not result.stdout.strip() and not result.stderr.strip():
            return path, "no_remote"
        return path, "unavailable"

    def stored_identity(self, path, immutable=False):
        identity_db = Path(self.path)
        if not identity_db.exists():
            return None

        uri = self._read_only_uri(identity_db, immutable=immutable)
        conn = sqlite3.connect(uri, uri=True, timeout=30)
        try:
            conn.execute("PRAGMA query_only=ON")
            table_exists = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'project_identity'"
            ).fetchone()
            if table_exists is None:
                return None
            row = conn.execute(
                "SELECT project FROM project_identity WHERE source_path = ?",
                (path,),
            ).fetchone()
            return row[0] if row else None
        finally:
            conn.close()

    def _legacy_project_identity(self, path, resolved_project, immutable=False):
        from agent_monitor.storage import RollupBucketTimezoneMigrationRequired, read_connection

        rollup_db = Path(self.path).with_name("rollup.db")
        if not rollup_db.exists():
            return None, set()

        try:
            with read_connection(rollup_db, immutable=immutable) as conn:
                if conn is None:
                    return None, set()
                table_exists = conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'daily_rollup'"
                ).fetchone()
                if table_exists is None:
                    return None, set()
                all_projects = {
                    row[0]
                    for row in conn.execute("SELECT DISTINCT project FROM daily_rollup")
                }
                matching_projects = all_projects.intersection({path, resolved_project})
                if len(matching_projects) > 1:
                    raise ProjectIdentityConflict(
                        "Existing rollup rows already contain conflicting project identities for %s"
                        % path,
                        source_path=path,
                        reason="conflicting_history",
                        resolved_project=resolved_project,
                    )
                claimed_projects = self.stored_projects(immutable=immutable)
                return next(iter(matching_projects), None), all_projects - claimed_projects
        except RollupBucketTimezoneMigrationRequired:
            return None, set()

    def stored_projects(self, immutable=False):
        identity_db = Path(self.path)
        if not identity_db.exists():
            return set()

        uri = self._read_only_uri(identity_db, immutable=immutable)
        conn = sqlite3.connect(uri, uri=True, timeout=30)
        try:
            conn.execute("PRAGMA query_only=ON")
            table_exists = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'project_identity'"
            ).fetchone()
            if table_exists is None:
                return set()
            return {row[0] for row in conn.execute("SELECT DISTINCT project FROM project_identity")}
        finally:
            conn.close()

    def list_blockers(self, status="active", immutable=False):
        with self.rollup_lock():
            return self._list_project_identity_blockers(status, immutable=immutable)

    def _list_project_identity_blockers(self, status="active", immutable=False):
        identity_db = Path(self.path)
        if not identity_db.exists():
            return []

        uri = self._read_only_uri(identity_db, immutable=immutable)
        conn = sqlite3.connect(uri, uri=True, timeout=30)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA query_only=ON")
            table_exists = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'project_identity_blocker'"
            ).fetchone()
            if table_exists is None:
                return []
            columns = {row[1] for row in conn.execute("PRAGMA table_info(project_identity_blocker)")}
            pin_candidate = "pin_candidate" if "pin_candidate" in columns else "NULL AS pin_candidate"
            sql = """
                SELECT source_path, reason, resolved_candidate, {pin_candidate},
                       first_seen, last_seen, status
                FROM project_identity_blocker
            """.format(pin_candidate=pin_candidate)
            params = ()
            if status is not None:
                sql += " WHERE status = ?"
                params = (status,)
            sql += " ORDER BY source_path"
            return [self._blocker_dict(row) for row in conn.execute(sql, params)]
        finally:
            conn.close()

    def _read_only_uri(self, db_path, immutable=False):
        db_path = Path(db_path)
        if immutable and Path(str(db_path) + "-wal").exists():
            raise sqlite3.OperationalError(
                "cannot take a non-writing snapshot while an uncheckpointed WAL exists"
            )
        query = "?mode=ro&immutable=1" if immutable else "?mode=ro"
        return db_path.resolve().as_uri() + query

    def _project_identity_blocker(self, path):
        return next(
            (row for row in self.list_blockers(status="active") if row["source_path"] == path),
            None,
        )

    def record_blocker(self, blocker):
        with self.rollup_lock():
            return self._record_project_identity_blocker_locked(blocker)

    def _record_project_identity_blocker_locked(self, blocker):
        ownership_token = self.assert_lock_held()
        identity_db = Path(self.path)
        identity_db.parent.mkdir(parents=True, exist_ok=True)
        observed_at = datetime.now(timezone.utc).isoformat()
        conn = sqlite3.connect(identity_db, timeout=30)
        conn.row_factory = sqlite3.Row
        try:
            self._assert_identity_connection_matches(conn, ownership_token)
            conn.execute("PRAGMA busy_timeout=30000")
            conn.execute("BEGIN IMMEDIATE")
            self._ensure_project_identity_blocker_schema(conn, ownership_token)
            conn.execute(
                """
                INSERT INTO project_identity_blocker (
                  source_path, reason, resolved_candidate, pin_candidate,
                  first_seen, last_seen, status
                ) VALUES (?, ?, ?, ?, ?, ?, 'active')
                ON CONFLICT(source_path) DO UPDATE SET
                  reason = excluded.reason,
                  resolved_candidate = excluded.resolved_candidate,
                  pin_candidate = excluded.pin_candidate,
                  last_seen = excluded.last_seen,
                  status = 'active'
                """,
                (
                    blocker.source_path,
                    blocker.reason,
                    blocker.resolved_project,
                    blocker.pin_candidate,
                    observed_at,
                    observed_at,
                ),
            )
            row = self._select_blocker(conn, blocker.source_path)
            conn.commit()
            return self._blocker_dict(row)
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def pin(self, source_path, existing_project):
        with self.rollup_lock():
            with self._lock:
                resolved_project, resolution = self.resolve_project(source_path)
                conn, ownership_token = self._begin_identity_recovery()
                try:
                    blocker = self._require_active_blocker(conn, source_path)
                    pin_candidate = self._derive_pin_candidate(
                        conn,
                        source_path,
                        blocker,
                        resolved_project,
                        resolution,
                    )
                    if existing_project != pin_candidate:
                        raise ProjectIdentityRecoveryError(
                            "Cannot pin %s to %s: the only identity directly derivable for this source is %s"
                            % (source_path, existing_project, pin_candidate)
                        )
                    self._upsert_project_identity(
                        conn,
                        source_path,
                        existing_project,
                        "legacy",
                        ownership_token,
                    )
                    self._resolve_blocker(conn, source_path, "resolved_pinned", ownership_token)
                    recovered = self._blocker_dict(self._select_blocker(conn, source_path))
                    conn.commit()
                except Exception:
                    conn.rollback()
                    raise
                finally:
                    conn.close()
                self.project_cache[source_path] = existing_project
                return recovered

    def _validate_reviewed_assignments(self, manifest):
        if (not isinstance(manifest, dict) or set(manifest) != {"version", "assignments"}
                or type(manifest["version"]) is not int or manifest["version"] != 1):
            raise ProjectIdentityRecoveryError("Manifest requires version 1 and assignments")
        assignments = manifest["assignments"]
        if not isinstance(assignments, list) or not assignments:
            raise ProjectIdentityRecoveryError("Manifest assignments must be a nonempty array")
        seen = set()
        expected_fields = {"reason", "resolved_candidate", "pin_candidate", "first_seen"}
        for item in assignments:
            if not isinstance(item, dict) or set(item) != {"source_path", "project", "expected_blocker"}:
                raise ProjectIdentityRecoveryError("Each assignment requires source_path, project and expected_blocker")
            for field in ("source_path", "project"):
                value = item[field]
                if not isinstance(value, str) or not value.strip() or value != value.strip() or "\x00" in value:
                    raise ProjectIdentityRecoveryError("Assignment %s must be a nonempty string without surrounding whitespace or NUL" % field)
            source = item["source_path"]
            if not Path(source).is_absolute():
                raise ProjectIdentityRecoveryError("Assignment source_path must be an absolute path: %s" % source)
            if source in seen:
                raise ProjectIdentityRecoveryError("Duplicate assignment source_path: %s" % source)
            seen.add(source)
            expected = item["expected_blocker"]
            if not isinstance(expected, dict) or set(expected) != expected_fields:
                raise ProjectIdentityRecoveryError("Expected blocker requires reason, resolved_candidate, pin_candidate and first_seen: %s" % source)
            for field, value in expected.items():
                if value is None and field in {"resolved_candidate", "pin_candidate"}:
                    continue
                if not isinstance(value, str) or not value.strip():
                    raise ProjectIdentityRecoveryError("Invalid expected blocker %s: %s" % (field, source))
        return assignments

    def _check_reviewed_assignments(self, conn, assignments):
        # Old blocker schemas have no pin_candidate; preview must not migrate them.
        columns = {row[1] for row in conn.execute("PRAGMA table_info(project_identity_blocker)")}
        pin_column = "pin_candidate" if "pin_candidate" in columns else "NULL AS pin_candidate"
        for item in assignments:
            source = item["source_path"]
            blocker = conn.execute(
                "SELECT reason, resolved_candidate, %s, first_seen, status "
                "FROM project_identity_blocker WHERE source_path = ?" % pin_column,
                (source,),
            ).fetchone()
            if blocker is None or blocker["status"] != "active":
                raise ProjectIdentityRecoveryError("Cannot assign %s: no active project identity blocker exists" % source)
            for field, value in item["expected_blocker"].items():
                if blocker[field] != value:
                    raise ProjectIdentityRecoveryError("Cannot assign %s: blocker %s changed; review a fresh blocker snapshot" % (source, field))
            if conn.execute("SELECT 1 FROM project_identity WHERE source_path = ?", (source,)).fetchone():
                raise ProjectIdentityRecoveryError("Cannot assign %s: a project identity already exists" % source)

    def recover_reviewed(self, manifest, apply=False):
        """Assign explicitly reviewed identities without rewriting usage or rollup history."""
        assignments = self._validate_reviewed_assignments(manifest)
        from agent_monitor.storage import RollupBucketTimezoneMigrationRequired, read_connection, rollup_lock

        try:
            # Use the same lock as collection; preview never creates the lock file.
            with rollup_lock(self.rollup_db(), create=apply), self._lock:
                if not apply:
                    with read_connection(self.rollup_db(), immutable=True) as rollup_conn:
                        if rollup_conn is None or not rollup_conn.execute(
                            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='daily_rollup'"
                        ).fetchone():
                            raise ProjectIdentityRecoveryError("Cannot preview identities: daily_rollup table does not exist")
                    conn = sqlite3.connect(self._read_only_uri(self.path, immutable=True), uri=True)
                    conn.row_factory = sqlite3.Row
                    try:
                        self._check_reviewed_assignments(conn, assignments)
                    finally:
                        conn.close()
                else:
                    conn, ownership_token = self._begin_identity_recovery()
                    try:
                        self._check_reviewed_assignments(conn, assignments)
                        for item in assignments:
                            self._upsert_project_identity(conn, item["source_path"], item["project"], "legacy", ownership_token)
                            self._resolve_blocker(conn, item["source_path"], "resolved_pinned", ownership_token)
                        conn.commit()
                    except Exception:
                        conn.rollback()
                        raise
                    finally:
                        conn.close()
                    self.project_cache.update({item["source_path"]: item["project"] for item in assignments})
        except (OSError, sqlite3.Error, RollupBucketTimezoneMigrationRequired) as exc:
            raise ProjectIdentityRecoveryError("Reviewed identity recovery failed; no assignments committed: %s" % exc) from exc
        return [{"source_path": item["source_path"], "project": item["project"]} for item in assignments]

    def _begin_identity_recovery(self):
        ownership_token = self.assert_lock_held()
        identity_db = Path(self.path)
        rollup_db = identity_db.with_name("rollup.db")
        if not rollup_db.exists():
            raise ProjectIdentityRecoveryError("Cannot recover identity: daily rollup database does not exist")
        from agent_monitor.storage import RollupBucketTimezoneMigrationRequired, read_connection

        try:
            # The caller holds the sibling rollup lock, so the marker cannot change
            # between this preflight and the ATTACH below.
            with read_connection(rollup_db):
                pass
        except RollupBucketTimezoneMigrationRequired as exc:
            raise ProjectIdentityRecoveryError(
                "Cannot recover identity: rollup history failed the bucket_timezone contract. "
                "Complete explicit rollup adoption before retrying; identity and blocker state "
                "were not changed. (%s)" % exc
            ) from exc
        identity_db.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(identity_db, timeout=30)
        conn.row_factory = sqlite3.Row
        try:
            self._assert_identity_connection_matches(conn, ownership_token)
            conn.execute("PRAGMA busy_timeout=30000")
            conn.execute("ATTACH DATABASE ? AS rollup", (str(rollup_db),))
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(PROJECT_IDENTITY_SCHEMA)
            self._ensure_project_identity_blocker_schema(conn, ownership_token)
            table_exists = conn.execute(
                "SELECT 1 FROM rollup.sqlite_master WHERE type = 'table' AND name = 'daily_rollup'"
            ).fetchone()
            if table_exists is None:
                raise ProjectIdentityRecoveryError("Cannot recover identity: daily_rollup table does not exist")
            return conn, ownership_token
        except Exception:
            conn.close()
            raise

    def _ensure_project_identity_blocker_schema(self, conn, ownership_token):
        self._assert_identity_connection_matches(conn, ownership_token)
        conn.execute(PROJECT_IDENTITY_BLOCKER_SCHEMA)
        columns = {row[1] for row in conn.execute("PRAGMA table_info(project_identity_blocker)")}
        if "pin_candidate" not in columns:
            conn.execute("ALTER TABLE project_identity_blocker ADD COLUMN pin_candidate TEXT")

    def _require_active_blocker(self, conn, source_path):
        row = self._select_blocker(conn, source_path)
        if row is None or row["status"] != "active":
            raise ProjectIdentityRecoveryError(
                "Cannot recover %s: no active project identity blocker exists" % source_path
            )
        return row

    def _derive_pin_candidate(self, conn, source_path, blocker, resolved_project, resolution):
        if blocker["reason"] == "conflicting_history":
            raise ProjectIdentityRecoveryError(
                "Cannot pin %s: conflicting historical identities cannot be resolved without source-path lineage"
                % source_path
            )
        if resolution == "unavailable":
            raise ProjectIdentityRecoveryError(
                "Cannot pin %s: restore the source path and git metadata before retrying" % source_path
            )

        direct_candidates = {source_path}
        if resolution == "remote":
            direct_candidates.add(resolved_project)
        placeholders = ", ".join("?" for _candidate in direct_candidates)
        rows = conn.execute(
            "SELECT DISTINCT project FROM rollup.daily_rollup WHERE project IN (%s)" % placeholders,
            tuple(sorted(direct_candidates)),
        ).fetchall()
        matching_projects = {row[0] for row in rows}
        if len(matching_projects) != 1:
            raise ProjectIdentityRecoveryError(
                "Cannot pin %s: current path/remote does not identify exactly one existing rollup project"
                % source_path
            )

        candidate = next(iter(matching_projects))
        if blocker["pin_candidate"] is None:
            raise ProjectIdentityRecoveryError(
                "Cannot pin %s: no unique historical identity was derivable when this blocker was recorded"
                % source_path
            )
        if candidate != blocker["pin_candidate"]:
            raise ProjectIdentityRecoveryError(
                "Cannot pin %s: current path/remote matches %s, but this blocker's unique historical "
                "identity is %s"
                % (source_path, candidate, blocker["pin_candidate"])
            )
        return candidate

    def _upsert_project_identity(self, conn, source_path, project, resolution, ownership_token):
        self._assert_identity_connection_matches(conn, ownership_token)
        conn.execute(
            """
            INSERT INTO project_identity (source_path, project, resolution)
            VALUES (?, ?, ?)
            ON CONFLICT(source_path) DO UPDATE SET
              project = excluded.project,
              resolution = excluded.resolution
            """,
            (source_path, project, resolution),
        )

    def _resolve_blocker(self, conn, source_path, status, ownership_token):
        self._assert_identity_connection_matches(conn, ownership_token)
        cursor = conn.execute(
            "UPDATE project_identity_blocker SET status = ? WHERE source_path = ? AND status = 'active'",
            (status, source_path),
        )
        if cursor.rowcount != 1:
            raise ProjectIdentityRecoveryError(
                "Cannot recover %s: active blocker changed during recovery" % source_path
            )

    def _select_blocker(self, conn, source_path):
        return conn.execute(
            """
            SELECT source_path, reason, resolved_candidate, pin_candidate,
                   first_seen, last_seen, status
            FROM project_identity_blocker
            WHERE source_path = ?
            """,
            (source_path,),
        ).fetchone()

    def _blocker_dict(self, row):
        candidate = row["resolved_candidate"]
        return {
            "source_path": row["source_path"],
            "reason": row["reason"],
            "resolved_candidate": candidate,
            "resolved_project": candidate,
            "pin_candidate": row["pin_candidate"],
            "first_seen": row["first_seen"],
            "last_seen": row["last_seen"],
            "status": row["status"],
        }

    def _store_project_identity(self, path, project, resolution):
        ownership_token = self.assert_lock_held()
        identity_db = Path(self.path)
        identity_db.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(identity_db, timeout=30)
        try:
            self._assert_identity_connection_matches(conn, ownership_token)
            conn.execute("PRAGMA busy_timeout=30000")
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(PROJECT_IDENTITY_SCHEMA)
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO project_identity (source_path, project, resolution)
                VALUES (?, ?, ?)
                """,
                (path, project, resolution),
            )
            row = conn.execute(
                "SELECT project, resolution FROM project_identity WHERE source_path = ?",
                (path,),
            ).fetchone()
            conn.commit()
            return row[0], row[1], cursor.rowcount == 1
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def rollup_lock(self):
        from agent_monitor.storage import rollup_lock

        return rollup_lock(self.rollup_db())

    def rollup_db(self):
        return Path(self.path).with_name("rollup.db")

    def assert_lock_held(self, ownership_token=None):
        from agent_monitor.storage import RollupLockNotHeld, assert_lock_held, normalize_db_path

        identity_db = Path(self.path)
        rollup_db = self.rollup_db()
        normalized_identity_db = normalize_db_path(identity_db)
        normalized_rollup_db = normalize_db_path(rollup_db)
        if ownership_token is not None and (
            ownership_token.pid != os.getpid()
            or ownership_token.identity_db != normalized_identity_db
            or ownership_token.rollup_db != normalized_rollup_db
        ):
            raise RollupLockNotHeld(
                "project identity mutation does not own sibling rollup lock for %s"
                % normalized_rollup_db
            )
        assert_lock_held(rollup_db)
        return _IdentityRollupLockToken(
            normalized_identity_db,
            normalized_rollup_db,
            os.getpid(),
        )

    def _assert_identity_connection_matches(self, conn, ownership_token):
        from agent_monitor.storage import RollupLockNotHeld, normalize_db_path

        self.assert_lock_held(ownership_token)
        if type(conn) is not sqlite3.Connection:
            raise RollupLockNotHeld(
                "project identity mutation requires a native sqlite3.Connection"
            )
        main_rows = [
            row
            for row in sqlite3.Connection.execute(conn, "PRAGMA database_list")
            if row[1] == "main"
        ]
        main_path = main_rows[0][2] if len(main_rows) == 1 else ""
        if not main_path or normalize_db_path(main_path) != ownership_token.identity_db:
            raise RollupLockNotHeld(
                "project identity connection does not match protected database %s"
                % ownership_token.identity_db
            )
