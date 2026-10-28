"""Local retained usage statistics; never stores transcript content."""

import json
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

from agent_monitor.contracts.usage import UsageEntry, complete_usage_observation
from agent_monitor.contracts.session import merge_metadata


def _encode(entry):
    # UsageEntry contains scalar statistics plus the timestamp. Avoid asdict's
    # recursive deepcopy of every field on every collection.
    value = vars(entry).copy()
    value["timestamp"] = entry.timestamp.isoformat()
    return value


def _decode(value):
    return UsageEntry(**dict(value, timestamp=datetime.fromisoformat(value["timestamp"])))


class UsageArchive:
    def __init__(self, path):
        self.path = Path(path)
        self.session_metadata = []

    def read(self):
        if not self.path.exists():
            return [], None
        with closing(sqlite3.connect(self.path.resolve().as_uri() + "?mode=ro", uri=True)) as conn:
            self.session_metadata = self._read_sessions(conn)
            return self._read(conn)

    @staticmethod
    def _read_sessions(conn):
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='usage_sessions'").fetchone():
            return []
        return merge_metadata(observed=[json.loads(row[0]) for row in conn.execute("SELECT metadata FROM usage_sessions")])

    @staticmethod
    def _read(conn):
        entries = [_decode(json.loads(row[0])) for row in conn.execute(
            "SELECT entry FROM usage_events ORDER BY dedup_key"
        )]
        row = conn.execute(
            "SELECT value FROM archive_meta WHERE key = 'history_authoritative_from'"
        ).fetchone()
        return entries, row[0] if row else None

    @staticmethod
    def _initialize_incremental(conn):
        from . import usage_models
        usage_models.initialize(conn)
        conn.execute("CREATE TABLE IF NOT EXISTS archive_dirty (dedup_key TEXT PRIMARY KEY)")
        # Separate from projection dirtiness: ordinary full-read callers do not
        # consume archive_dirty, and downgrade writers must invalidate replay.
        conn.execute("CREATE TABLE IF NOT EXISTS archive_pending_merge (dedup_key TEXT PRIMARY KEY)")
        conn.execute("CREATE TABLE IF NOT EXISTS usage_project_counts (project TEXT PRIMARY KEY, event_count INTEGER NOT NULL)")
        conn.execute("CREATE TABLE IF NOT EXISTS file_observations (path TEXT NOT NULL, position INTEGER NOT NULL, dedup_key TEXT NOT NULL, entry TEXT NOT NULL, PRIMARY KEY(path, position))")
        conn.execute("CREATE INDEX IF NOT EXISTS file_observations_key ON file_observations(dedup_key, path, position)")
        conn.execute("CREATE TABLE IF NOT EXISTS archive_observation_errors (dedup_key TEXT NOT NULL, path TEXT NOT NULL, position INTEGER NOT NULL, error TEXT NOT NULL, PRIMARY KEY(dedup_key, path, position))")
        if not conn.execute("SELECT 1 FROM archive_meta WHERE key=?", ("incremental_observations_v1",)).fetchone():
            conn.execute("INSERT OR IGNORE INTO archive_dirty SELECT dedup_key FROM usage_events")
            conn.execute("INSERT OR IGNORE INTO archive_pending_merge SELECT dedup_key FROM usage_events")
            conn.execute("DELETE FROM usage_project_counts")
            conn.execute("INSERT INTO usage_project_counts SELECT json_quote(json_extract(entry, '$.project')), count(*) FROM usage_events GROUP BY json_quote(json_extract(entry, '$.project'))")
            conn.execute("INSERT INTO archive_meta VALUES ('incremental_observations_v1', '1')")
        for operation in ("INSERT", "UPDATE", "DELETE"):
            old = "" if operation == "INSERT" else """
                INSERT INTO archive_dirty VALUES (OLD.dedup_key) ON CONFLICT(dedup_key) DO NOTHING;
                INSERT INTO archive_pending_merge VALUES (OLD.dedup_key) ON CONFLICT(dedup_key) DO NOTHING;
                UPDATE usage_project_counts SET event_count=event_count-1
                    WHERE project=json_quote(json_extract(OLD.entry, '$.project'));
                DELETE FROM usage_project_counts WHERE project=json_quote(json_extract(OLD.entry, '$.project')) AND event_count=0;
            """
            new = "" if operation == "DELETE" else """
                INSERT INTO archive_dirty VALUES (NEW.dedup_key) ON CONFLICT(dedup_key) DO NOTHING;
                INSERT INTO archive_pending_merge VALUES (NEW.dedup_key) ON CONFLICT(dedup_key) DO NOTHING;
                INSERT INTO usage_project_counts VALUES (json_quote(json_extract(NEW.entry, '$.project')), 1)
                    ON CONFLICT(project) DO UPDATE SET event_count=event_count+1;
            """
            conn.execute("CREATE TRIGGER IF NOT EXISTS usage_events_incremental_%s AFTER %s ON usage_events BEGIN %s %s END" % (operation.lower(), operation, old, new))

    @staticmethod
    def _forget_observations(conn, path, affected):
        affected.update(row[0] for row in conn.execute(
            "SELECT dedup_key FROM file_observations WHERE path=?", (path,)))
        conn.execute("DELETE FROM file_observations WHERE path=?", (path,))

    @staticmethod
    def _replay(existing, observations):
        errors = []
        seen = False
        for path, position, entry in observations:
            if entry.agent_id == "claude-code":
                diagnostics = []
                entry = complete_usage_observation(existing, entry, diagnostics)
                errors.extend((path, position, error) for error in diagnostics)
            elif seen:
                continue
            seen = True
            existing = entry
        return existing, errors

    def collect(self, paths, parser, signature_for, source_errors, bucket_timezone,
                force_reload=False, now=None, session_metadata=(), session_metadata_transform=None,
                *, prepare=None):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path, timeout=30)) as conn:
            with conn:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute("CREATE TABLE IF NOT EXISTS archive_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
                conn.execute("CREATE TABLE IF NOT EXISTS usage_events (dedup_key TEXT PRIMARY KEY, entry TEXT NOT NULL)")
                conn.execute("CREATE TABLE IF NOT EXISTS usage_sessions (agent_id TEXT NOT NULL, session_id TEXT NOT NULL, metadata TEXT NOT NULL, PRIMARY KEY(agent_id, session_id))")
                conn.execute("CREATE TABLE IF NOT EXISTS parsed_files (path TEXT PRIMARY KEY, signature TEXT, entries TEXT NOT NULL)")
                self._initialize_incremental(conn)
                current = now or datetime.now(timezone.utc)
                authority = (current.astimezone(bucket_timezone).date() + timedelta(days=1)).isoformat()
                conn.execute("INSERT OR IGNORE INTO archive_meta VALUES ('history_authoritative_from', ?)", (authority,))
                authority = conn.execute("SELECT value FROM archive_meta WHERE key='history_authoritative_from'").fetchone()[0]
                affected = {row[0] for row in conn.execute("SELECT dedup_key FROM archive_pending_merge")}
                active_paths = sorted({Path(path) for path in paths})
                active = {str(path) for path in active_paths}
                cached_files = dict(conn.execute("SELECT path, signature FROM parsed_files"))
                for path in cached_files.keys() - active:
                    self._forget_observations(conn, path, affected)
                    conn.execute("DELETE FROM parsed_files WHERE path=?", (path,))
                parse_errors = []
                for path in active_paths:
                    key = str(path)
                    error_count = len(source_errors)
                    try:
                        signature = signature_for(path)
                        # Old code must reparse rather than trust the empty
                        # compatibility payload; old-format writes invalidate us.
                        tagged = "observations-v1:" + signature if signature is not None else None
                        if not force_reload and tagged is not None and cached_files.get(key) == tagged:
                            continue
                        entries = list(parser(path))
                        after = signature_for(path)
                        if len(source_errors) != error_count or after != signature:
                            tagged = None
                    except Exception as exc:
                        source_errors.append({"path": key, "stage": "archive_parse", "error": "%s: %s" % (type(exc).__name__, exc)})
                        self._forget_observations(conn, key, affected)
                        conn.execute("UPDATE parsed_files SET signature=NULL WHERE path=?", (key,))
                    else:
                        self._forget_observations(conn, key, affected)
                        rows = [(key, position, entry.dedup_key, json.dumps(_encode(entry)))
                                for position, entry in enumerate(entries)]
                        affected.update(row[2] for row in rows)
                        conn.executemany("INSERT INTO file_observations VALUES (?, ?, ?, ?)", rows)
                        conn.execute("INSERT INTO parsed_files VALUES (?, ?, '[]') ON CONFLICT(path) DO UPDATE SET signature=excluded.signature, entries=excluded.entries", (key, tagged))
                    parse_errors.extend((key, -1, error) for error in source_errors[error_count:])
                    del source_errors[error_count:]
                round_errors = []
                for key in sorted(affected):
                    row = conn.execute("SELECT entry FROM usage_events WHERE dedup_key=?", (key,)).fetchone()
                    existing = _decode(json.loads(row[0])) if row else None
                    observations = [(path, position, _decode(json.loads(payload))) for path, position, payload in
                                    conn.execute("SELECT path, position, entry FROM file_observations WHERE dedup_key=?", (key,))]
                    observations.sort(key=lambda item: (Path(item[0]), item[1]))
                    merged, errors = self._replay(existing, observations)
                    round_errors.extend(errors)
                    if merged != existing:
                        conn.execute("INSERT INTO usage_events VALUES (?, ?) ON CONFLICT(dedup_key) DO UPDATE SET entry=excluded.entry", (key, json.dumps(_encode(merged))))
                    conn.execute("DELETE FROM archive_pending_merge WHERE dedup_key=?", (key,))
                    # Replay need not be idempotent (e.g. equal token vectors
                    # with differing context). Cache only the stable next pass.
                    next_merged, next_errors = self._replay(merged, observations)
                    if next_merged != merged:
                        conn.execute("INSERT OR IGNORE INTO archive_pending_merge VALUES (?)", (key,))
                    conn.execute("DELETE FROM archive_observation_errors WHERE dedup_key=?", (key,))
                    conn.executemany("INSERT INTO archive_observation_errors VALUES (?, ?, ?, ?)",
                                     [(key, path, position, json.dumps(error)) for path, position, error in next_errors])
                round_errors.extend((path, position, json.loads(error)) for key, path, position, error in
                                    conn.execute("SELECT dedup_key, path, position, error FROM archive_observation_errors") if key not in affected)
                source_errors.extend(error for _, _, error in sorted(parse_errors + round_errors, key=lambda item: (Path(item[0]), item[1])))
                self.session_metadata = merge_metadata(self._read_sessions(conn), session_metadata)
                if session_metadata_transform is not None:
                    self.session_metadata = session_metadata_transform(self.session_metadata)
                conn.executemany("INSERT INTO usage_sessions VALUES (?, ?, ?) ON CONFLICT(agent_id, session_id) DO UPDATE SET metadata=excluded.metadata WHERE metadata != excluded.metadata",
                                 [(row["agent_id"], row["session_id"], json.dumps(row, sort_keys=True)) for row in self.session_metadata])
                result = (prepare(conn), authority) if prepare is not None else self._read(conn)
            return result
