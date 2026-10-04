"""Local retained usage statistics; never stores transcript content."""

import json
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

from parsers import UsageEntry


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

    def read(self):
        if not self.path.exists():
            return [], None
        with closing(sqlite3.connect(self.path.resolve().as_uri() + "?mode=ro", uri=True)) as conn:
            return self._read(conn)

    @staticmethod
    def _read(conn):
        entries = [_decode(json.loads(row[0])) for row in conn.execute(
            "SELECT entry FROM usage_events ORDER BY dedup_key"
        )]
        row = conn.execute(
            "SELECT value FROM archive_meta WHERE key = 'history_authoritative_from'"
        ).fetchone()
        return entries, row[0] if row else None

    def collect(self, paths, parser, signature_for, source_errors, bucket_timezone,
                force_reload=False, now=None):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path, timeout=30)) as conn:
            with conn:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute("CREATE TABLE IF NOT EXISTS archive_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
                conn.execute("CREATE TABLE IF NOT EXISTS usage_events (dedup_key TEXT PRIMARY KEY, entry TEXT NOT NULL)")
                # Per-file statistics preserve the existing sorted-file, first-call
                # dedup winner even when only a later transcript copy changes.
                conn.execute("CREATE TABLE IF NOT EXISTS parsed_files (path TEXT PRIMARY KEY, signature TEXT, entries TEXT NOT NULL)")
                current = now or datetime.now(timezone.utc)
                authority = (current.astimezone(bucket_timezone).date() + timedelta(days=1)).isoformat()
                conn.execute(
                    "INSERT OR IGNORE INTO archive_meta VALUES ('history_authoritative_from', ?)",
                    (authority,),
                )
                retained, authority = self._read(conn)
                retained_by_key = {entry.dedup_key: entry for entry in retained}
                seen = set()
                for path in sorted({Path(path) for path in paths}):
                    key = str(path)
                    try:
                        signature = signature_for(path)
                        cached = conn.execute("SELECT signature, entries FROM parsed_files WHERE path = ?", (key,)).fetchone()
                        if not force_reload and signature is not None and cached and cached[0] == signature:
                            entries = [_decode(item) for item in json.loads(cached[1])]
                        else:
                            error_count = len(source_errors)
                            entries = list(parser(path))
                            after = signature_for(path)
                            if len(source_errors) != error_count or after != signature:
                                signature = None
                            conn.execute(
                                "INSERT INTO parsed_files VALUES (?, ?, ?) ON CONFLICT(path) DO UPDATE SET signature=excluded.signature, entries=excluded.entries",
                                (key, signature, json.dumps([_encode(entry) for entry in entries])),
                            )
                    except Exception as exc:
                        source_errors.append({"path": key, "stage": "archive_parse", "error": "%s: %s" % (type(exc).__name__, exc)})
                        conn.execute("UPDATE parsed_files SET signature = NULL WHERE path = ?", (key,))
                        continue
                    for entry in entries:
                        if entry.dedup_key in seen:
                            continue
                        seen.add(entry.dedup_key)
                        if retained_by_key.get(entry.dedup_key) == entry:
                            continue
                        retained_by_key[entry.dedup_key] = entry
                        conn.execute(
                            "INSERT INTO usage_events VALUES (?, ?) ON CONFLICT(dedup_key) DO UPDATE SET entry=excluded.entry",
                            (entry.dedup_key, json.dumps(_encode(entry))),
                        )
                result = ([retained_by_key[key] for key in sorted(retained_by_key)], authority)
            return result
