import contextlib
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from agent_monitor.ingestion import IDENTITY_STORE, USAGE_LOADER, project_identity, usage_loader
from agent_monitor.accounting import rollup
from parsers import UsageEntry
from agent_monitor.ingestion.usage_archive import UsageArchive


class UsageArchiveTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.sessions = self.root / "sessions"
        self.sessions.mkdir()
        self.archive_path = self.root / "usage_archive.sqlite3"
        self.db = self.root / "rollup.db"
        self.metadata = self.root / "metadata.sqlite3"
        self.now = datetime(2026, 4, 15, tzinfo=timezone.utc)
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        for patch in (
            mock.patch.object(IDENTITY_STORE, "path", self.root / "project_identity.db"),
            mock.patch.object(USAGE_LOADER, "cache", None),
            mock.patch.object(IDENTITY_STORE, "project_cache", {}),
            mock.patch.object(USAGE_LOADER, "usage_paths", side_effect=lambda **kwargs: list(self.sessions.glob("*.jsonl"))),
            mock.patch.object(usage_loader.codex, "SESSIONS_DIR", str(self.sessions)),
            mock.patch.object(usage_loader.codex, "STATE_DB", str(self.metadata)),
            mock.patch("agent_monitor.accounting.pricing_fetcher.get_pricing", return_value={}),
            mock.patch("agent_monitor.accounting.pricing_fetcher.calculate_cost", return_value=1.0),
        ):
            self.stack.enter_context(patch)

    def seed(self):
        UsageArchive(self.archive_path).collect(
            [], None, None, [], usage_loader.USAGE_TIMEZONE,
            now=datetime(2026, 1, 1, 18, tzinfo=timezone.utc),
        )

    def session(self, name="one", tokens=100, timestamp="2026-02-10T12:00:00+00:00", model="gpt-5"):
        path = self.sessions / (name + ".jsonl")
        rows = [
            {"type": "session_meta", "payload": {"id": name, "cwd": "project", "model": model}},
            {"timestamp": timestamp, "type": "event_msg", "payload": {"type": "token_count", "info": {"total_token_usage": {"input_tokens": tokens, "output_tokens": 2, "cached_input_tokens": 0}}}},
            {"type": "response_item", "payload": {"content": "PRIVATE_TRANSCRIPT_MUST_NOT_BE_ARCHIVED"}},
        ]
        path.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
        return path

    def load(self, **kwargs):
        return USAGE_LOADER.load_all_entries(**kwargs)

    def run_rollup(self):
        return rollup.run(db_path=self.db, entries_loader=self.load, now=lambda: self.now)

    def rows(self, table="daily_usage_rollup"):
        with sqlite3.connect(self.db) as conn:
            return conn.execute("SELECT date, model, input_tokens FROM " + table + " ORDER BY date, model").fetchall()

    def models_db(self, model="model-a", wal=False):
        conn = sqlite3.connect(self.metadata)
        if wal:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA wal_autocheckpoint=0")
        conn.execute("CREATE TABLE threads (id TEXT PRIMARY KEY, model TEXT)")
        conn.execute("INSERT INTO threads VALUES ('one', ?)", (model,))
        conn.commit()
        return conn

    def test_independent_restart_delete_then_add_and_repeat(self):
        self.seed()
        first_path = self.session()
        script = """
import json, sys
from pathlib import Path
from unittest import mock
from agent_monitor.ingestion import IDENTITY_STORE, USAGE_LOADER, project_identity, usage_loader
root = Path(sys.argv[1])
IDENTITY_STORE.path = root / 'project_identity.db'
usage_loader.codex.SESSIONS_DIR = str(root / 'sessions')
usage_loader.codex.STATE_DB = str(root / 'missing.sqlite3')
with mock.patch.object(USAGE_LOADER, 'usage_paths', side_effect=lambda **kwargs: list((root / 'sessions').glob('*.jsonl'))), mock.patch('agent_monitor.accounting.pricing_fetcher.get_pricing', return_value={}), mock.patch('agent_monitor.accounting.pricing_fetcher.calculate_cost', return_value=1.0):
    entries = USAGE_LOADER.load_all_entries()
print(json.dumps([sum(entry.input_tokens for entry in entries), len(entries), entries.history_authoritative_from]))
"""
        def child():
            result = subprocess.run([sys.executable, "-c", script, str(self.root)], cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, check=True)
            return json.loads(result.stdout)
        self.assertEqual(child(), [100, 1, "2026-01-03"])
        first_path.unlink()
        self.session("two", 70)
        self.assertEqual(child(), [170, 2, "2026-01-03"])
        self.assertEqual(child(), [170, 2, "2026-01-03"])
        self.assertNotIn(b"PRIVATE_TRANSCRIPT_MUST_NOT_BE_ARCHIVED", self.archive_path.read_bytes())

    def test_reparse_repairs_fork_and_identity_without_removing_archive_keys(self):
        from tests.test_codex_forks import event, usage
        from parsers import codex
        self.seed()
        missing = self.session("missing", tokens=71)
        retained = codex.parse_file(missing)[0]
        missing.unlink()
        for name, last in (("positive", usage(100, 0, 10)), ("zero", usage(0, 0, 0))):
            path = self.sessions / (name + ".jsonl")
            rows = [{"type": "session_meta", "payload": {
                "id": name, "forked_from_id": "parent", "cwd": "project",
                "subagent_history_start_ordinal": 2}},
                event(usage(1100 - last["input_tokens"], 900 - last["cached_input_tokens"],
                            110 - last["output_tokens"])),
                event(usage(1100, 900, 110), last)]
            path.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
            old = replace(retained, session_id="parent", message_id=name,
                          request_id="token-count:3", input_tokens=200,
                          cache_read_tokens=900, output_tokens=110)
            UsageArchive(self.archive_path).collect(
                [path], lambda _: [old, replace(old, request_id="token-count:2"), retained], lambda _: "old-parser", [],
                usage_loader.USAGE_TIMEZONE)
        before = {e.dedup_key for e in UsageArchive(self.archive_path).read()[0]}
        self.run_rollup()
        repaired = UsageArchive(self.archive_path).read()[0]
        self.assertEqual({e.dedup_key for e in repaired}, before)
        by_session = {e.session_id: e for e in repaired if e.session_id == "missing" or e.request_id == "token-count:3"}
        self.assertEqual(set(by_session), {"missing", "positive", "zero"})
        self.assertEqual(by_session["missing"], retained)
        self.assertEqual(by_session["positive"].input_tokens, 100)
        self.assertEqual(by_session["zero"].message_count, 0)
        self.assertEqual(sum(row[2] for row in self.rows()), 171)
        USAGE_LOADER.cache = None
        self.run_rollup()
        self.assertEqual(sum(row[2] for row in self.rows()), 171)

    def test_old_day_backfill_replays_after_archive_commit_rollup_failure(self):
        self.seed()
        self.session(tokens=100)
        self.run_rollup()
        self.session("two", 70)
        with mock.patch.object(rollup, "_connect", side_effect=RuntimeError("rollup failed")):
            with self.assertRaisesRegex(RuntimeError, "rollup failed"):
                self.run_rollup()
        self.assertEqual(sum(e.input_tokens for e in UsageArchive(self.archive_path).read()[0]), 170)
        for path in self.sessions.glob("*.jsonl"):
            path.unlink()
        USAGE_LOADER.cache = None
        self.now = datetime(2026, 8, 15, tzinfo=timezone.utc)
        self.run_rollup()
        self.assertEqual(self.rows(), [("2026-02-10", "gpt-5", 170)])
        self.assertEqual(self.rows("daily_rollup"), [("2026-02-10", "gpt-5", 170)])

    def test_db_and_wal_changes_do_not_replace_historical_models(self):
        self.seed()
        self.session()
        for wal in (False, True):
            with self.subTest(wal=wal):
                if self.metadata.exists():
                    self.metadata.unlink()
                conn = self.models_db(wal=wal)
                try:
                    self.run_rollup()
                    before = usage_loader.codex._metadata_signature(self.metadata)
                    conn.execute("UPDATE threads SET model='model-b'")
                    conn.commit()
                    after = usage_loader.codex._metadata_signature(self.metadata)
                    self.assertNotEqual(before, after)
                    self.run_rollup()
                    self.assertEqual(self.rows(), [("2026-02-10", "gpt-5", 100)])
                    self.assertEqual(self.rows("daily_rollup"), [("2026-02-10", "gpt-5", 100)])
                finally:
                    conn.close()

    def test_cache_survives_restart_and_parser_change_invalidates(self):
        self.session()
        self.models_db().close()
        self.load()
        USAGE_LOADER.cache = None
        with mock.patch.object(USAGE_LOADER, "parse_usage_file", wraps=USAGE_LOADER.parse_usage_file) as parse:
            self.load()
            self.assertEqual(parse.call_count, 0)
            with mock.patch.object(USAGE_LOADER, "parser_fingerprint", return_value="new-parser"):
                self.load()
            self.assertEqual(parse.call_count, 1)

    def test_non_model_db_and_wal_changes_reuse_cached_statistics(self):
        self.session()
        for wal in (False, True):
            with self.subTest(wal=wal):
                if self.metadata.exists():
                    self.metadata.unlink()
                conn = self.models_db(wal=wal)
                try:
                    conn.execute("CREATE TABLE activity (counter INTEGER)")
                    conn.execute("INSERT INTO activity VALUES (1)")
                    conn.commit()
                    original = self.load()
                    before = usage_loader.codex._metadata_signature(self.metadata)
                    conn.execute("UPDATE activity SET counter=2")
                    conn.commit()
                    after = usage_loader.codex._metadata_signature(self.metadata)
                    self.assertNotEqual(before, after)
                    USAGE_LOADER.cache = None
                    with mock.patch.object(USAGE_LOADER, "parse_usage_file", wraps=USAGE_LOADER.parse_usage_file) as parse:
                        self.assertEqual(self.load(), original)
                        self.assertEqual(parse.call_count, 0)
                finally:
                    conn.close()

    def test_mapping_add_change_delete_across_session_files(self):
        self.session("one")
        self.session("two", tokens=170)
        conn = self.models_db("mapped-one")
        self.addCleanup(conn.close)
        with mock.patch.object(USAGE_LOADER, "parse_usage_file", wraps=USAGE_LOADER.parse_usage_file) as parse:
            self.assertEqual({entry.model for entry in self.load()}, {"gpt-5"})
            conn.execute("INSERT INTO threads VALUES ('two', 'mapped-two')")
            conn.commit()
            self.assertEqual({entry.model for entry in self.load()}, {"gpt-5"})
            conn.execute("UPDATE threads SET model='updated-two' WHERE id='two'")
            conn.commit()
            self.assertEqual({entry.model for entry in self.load()}, {"gpt-5"})
            conn.execute("DELETE FROM threads WHERE id='one'")
            conn.commit()
            self.assertEqual({entry.model for entry in self.load()}, {"gpt-5"})
            self.assertEqual(parse.call_count, 2)

    def test_valid_empty_mapping_is_cacheable_but_missing_is_not(self):
        self.session()
        conn = self.models_db()
        conn.execute("DELETE FROM threads")
        conn.commit()
        conn.close()
        with mock.patch.object(USAGE_LOADER, "parse_usage_file", wraps=USAGE_LOADER.parse_usage_file) as parse:
            self.assertEqual(self.load()[0].model, "gpt-5")
            self.load()
            self.assertEqual(parse.call_count, 1)
            self.metadata.unlink()
            self.load()
            self.load()
            self.assertEqual(parse.call_count, 3)
            self.models_db("restored-model").close()
            self.assertEqual(self.load()[0].model, "gpt-5")
            self.assertEqual(parse.call_count, 4)

    def test_source_change_reparses_with_unchanged_mapping(self):
        self.session(tokens=100)
        self.models_db().close()
        self.load()
        self.session(tokens=200)
        with mock.patch.object(USAGE_LOADER, "parse_usage_file", wraps=USAGE_LOADER.parse_usage_file) as parse:
            self.assertEqual(self.load()[0].input_tokens, 200)
            self.assertEqual(parse.call_count, 1)

    def test_unrelated_mapping_change_preserves_usage_cache(self):
        self.session("one")
        self.session("two")
        conn = self.models_db()
        self.addCleanup(conn.close)
        original = self.load()
        conn.execute("INSERT INTO threads VALUES ('unrelated-session', 'another-model')")
        conn.commit()
        with mock.patch.object(USAGE_LOADER, "parse_usage_file", wraps=USAGE_LOADER.parse_usage_file) as parse:
            self.assertEqual(self.load(), original)
            self.assertEqual(parse.call_count, 0)

    def test_cache_binds_consumed_mapping_when_database_changes_during_parse(self):
        for change_model in (False, True):
            with self.subTest(change_model=change_model):
                self.session()
                if self.metadata.exists():
                    self.metadata.unlink()
                conn = self.models_db("model-a")
                try:
                    conn.execute("CREATE TABLE activity (counter INTEGER)")
                    conn.execute("INSERT INTO activity VALUES (1)")
                    conn.commit()
                    original_parse = USAGE_LOADER.parse_usage_file

                    def parse_then_update(path, **kwargs):
                        entries = original_parse(path, **kwargs)
                        if change_model:
                            conn.execute("UPDATE threads SET model='model-b'")
                        else:
                            conn.execute("UPDATE activity SET counter=2")
                        conn.commit()
                        return entries

                    with mock.patch.object(USAGE_LOADER, "parse_usage_file", side_effect=parse_then_update):
                        self.assertEqual(self.load(force_reload=True)[0].model, "gpt-5")
                    with sqlite3.connect(self.archive_path) as archive:
                        self.assertEqual(archive.execute("SELECT count(*) FROM parsed_files WHERE signature IS NULL").fetchone()[0], 0)
                    with mock.patch.object(USAGE_LOADER, "parse_usage_file", wraps=original_parse) as parse:
                        self.assertEqual(self.load()[0].model, "gpt-5")
                        self.assertEqual(parse.call_count, 0)
                finally:
                    conn.close()

    def test_metadata_change_during_read_cannot_become_success_cache(self):
        self.session()
        self.models_db().close()
        self.load()
        real_signature = usage_loader.codex._metadata_signature
        calls = []

        def unstable_signature(path):
            calls.append(path)
            database, wal = real_signature(path)
            return (database[:3] + (database[3] + len(calls),), wal)

        with mock.patch.object(usage_loader.codex, "_metadata_signature", side_effect=unstable_signature), mock.patch.object(
            USAGE_LOADER, "parse_usage_file", wraps=USAGE_LOADER.parse_usage_file
        ) as parse:
            self.assertFalse(self.load().scan_complete)
            self.load()
            self.assertEqual(parse.call_count, 0)

    def test_closed_wal_metadata_reads_private_snapshot_without_touching_source(self):
        conn = self.models_db("model-a")
        conn.execute("PRAGMA journal_mode=WAL")
        conn.close()
        before = {p.name: p.read_bytes() for p in self.root.glob("metadata.sqlite3*")}
        errors = []
        models = usage_loader.codex._load_thread_models(
            self.metadata, immutable=True, source_errors=errors)
        self.assertEqual(errors, [])
        self.assertEqual(set(models.values()), {"model-a"})
        self.assertEqual({p.name: p.read_bytes() for p in self.root.glob("metadata.sqlite3*")}, before)

    def test_broken_metadata_preserves_retained_model_until_recovery(self):
        self.session()
        self.models_db("model-a").close()
        before = self.load()
        self.metadata.write_text("not sqlite")
        self.session(tokens=200)
        for kwargs in ({}, {"read_only": True}, {"diagnostic": True}):
            loaded = self.load(**kwargs)
            self.assertFalse(loaded.scan_complete)
            self.assertEqual([(e.model, e.input_tokens) for e in loaded],
                             [(e.model, e.input_tokens) for e in before])
        self.metadata.unlink()
        self.models_db("model-b").close()
        recovered = self.load()
        self.assertTrue(recovered.scan_complete)
        self.assertEqual([(e.model, e.input_tokens) for e in recovered], [("gpt-5", 200)])

    def test_missing_and_broken_metadata_retry_existing_source(self):
        self.session()
        with mock.patch.object(USAGE_LOADER, "parse_usage_file", wraps=USAGE_LOADER.parse_usage_file) as parse:
            self.load()
            self.load()
            self.assertEqual(parse.call_count, 2)
            self.metadata.write_text("not sqlite")
            self.assertFalse(self.load().scan_complete)
            self.load()
            self.assertEqual(parse.call_count, 2)
            self.metadata.unlink()
            self.models_db("resolved-model").close()
            self.assertEqual(self.load()[0].model, "gpt-5")
            self.assertEqual(parse.call_count, 3)

    def test_read_only_and_diagnostic_do_not_write_archive(self):
        path = self.session()
        for kwargs in ({"read_only": True}, {"diagnostic": True}):
            self.assertEqual(len(self.load(**kwargs)), 1)
            self.assertFalse(self.archive_path.exists())
        self.load()
        before = self.archive_path.read_bytes()
        path.unlink()
        self.session("two", 70)
        for kwargs in ({"read_only": True}, {"diagnostic": True}):
            self.assertEqual(sum(entry.input_tokens for entry in self.load(**kwargs)), 170)
            self.assertEqual(self.archive_path.read_bytes(), before)
        self.assertEqual(sum(entry.input_tokens for entry in UsageArchive(self.archive_path).read()[0]), 100)

    def test_deleted_source_keeps_last_model_after_metadata_changes(self):
        path = self.session()
        conn = self.models_db()
        self.load()
        path.unlink()
        conn.execute("UPDATE threads SET model='model-b'")
        conn.commit()
        conn.close()
        self.assertEqual(self.load()[0].model, "gpt-5")

    def test_explicit_archive_path_and_injected_cache_are_isolated(self):
        path = self.session()
        custom = self.root / "isolated" / "archive.sqlite3"
        loaded = self.load(archive_path=custom)
        self.assertEqual(len(loaded), 1)
        self.assertTrue(custom.exists())
        self.assertFalse(self.archive_path.exists())
        path.unlink()
        self.assertEqual(len(self.load(archive_path=custom)), 1)
        cache = mock.Mock()
        cache.load.return_value = []
        with mock.patch.object(USAGE_LOADER, "cache", cache):
            self.assertEqual(self.load(force_reload=True), [])
        cache.clear.assert_called_once_with()
        cache.load.assert_called_once_with()
        self.assertFalse(self.archive_path.exists())

    def test_unchanged_collection_does_not_rewrite_archive(self):
        self.session()
        self.models_db().close()
        original = self.load()
        before = self.archive_path.read_bytes()
        before_mtime = self.archive_path.stat().st_mtime_ns
        reloaded = self.load()
        self.assertEqual(original, reloaded)
        self.assertEqual(self.archive_path.read_bytes(), before)
        self.assertEqual(self.archive_path.stat().st_mtime_ns, before_mtime)

    def test_metadata_title_refresh_does_not_reparse_or_change_usage(self):
        self.session()
        conn = self.models_db()
        with conn:
            conn.execute("ALTER TABLE threads ADD COLUMN title TEXT")
            conn.execute("ALTER TABLE threads ADD COLUMN source TEXT")
            conn.execute("UPDATE threads SET title='before', source='cli'")
        original = self.load()
        self.assertEqual(original.session_metadata[0]["title"], "before")
        with conn:
            conn.execute("UPDATE threads SET title='after'")
        conn.close()
        with mock.patch.object(USAGE_LOADER, "parse_usage_file", wraps=USAGE_LOADER.parse_usage_file) as parse:
            refreshed = self.load()
            self.assertEqual(parse.call_count, 0)
        self.assertEqual(original, refreshed)
        self.assertEqual(refreshed.session_metadata[0]["title"], "after")
        self.assertNotIn("PRIVATE_TRANSCRIPT", json.dumps(refreshed.session_metadata))

    def test_parse_errors_do_not_become_success_cache(self):
        path = self.session()
        self.models_db().close()
        with path.open("a") as handle:
            handle.write("invalid-json\n")
        with mock.patch.object(USAGE_LOADER, "parse_usage_file", wraps=USAGE_LOADER.parse_usage_file) as parse:
            self.assertFalse(self.load().scan_complete)
            self.load()
            self.assertEqual(parse.call_count, 2)
        self.assertEqual(sum(entry.input_tokens for entry in UsageArchive(self.archive_path).read()[0]), 100)

    def test_legacy_model_rename_is_not_added_beside_previous_bucket(self):
        self.session(model="old-model", timestamp="2026-04-14T12:00:00+00:00")
        self.run_rollup()
        self.session(model="new-model", timestamp="2026-04-14T12:00:00+00:00")
        self.run_rollup()
        self.assertEqual(self.load()[0].model, "new-model")
        self.assertEqual(self.rows(), [("2026-04-14", "old-model", 100)])
        self.assertEqual(self.rows("daily_rollup"), [("2026-04-14", "old-model", 100)])

    def test_blocker_preserves_project_rows_then_recovery_replays(self):
        self.seed()
        self.session()
        self.run_rollup()
        entry = replace(self.load()[0], model="new-model")
        blocked = usage_loader.LoadedEntries([], blocked_sources=[{"source_path": "project"}], unattributed_entries=[entry], history_authoritative_from="2026-01-03")
        rollup.run(db_path=self.db, entries_loader=lambda: blocked, now=lambda: self.now)
        self.assertEqual(self.rows("daily_rollup"), [("2026-02-10", "gpt-5", 100)])
        self.assertEqual(self.rows(), [("2026-02-10", "new-model", 100)])
        recovered = usage_loader.LoadedEntries([entry], history_authoritative_from="2026-01-03")
        rollup.run(db_path=self.db, entries_loader=lambda: recovered, now=lambda: self.now)
        self.assertEqual(self.rows("daily_rollup"), [("2026-02-10", "new-model", 100)])

    def test_unrelated_blocker_allows_healthy_old_day_replay_and_model_migration(self):
        self.seed()
        source = str(self.root / "healthy")
        with contextlib.closing(sqlite3.connect(IDENTITY_STORE.path)) as conn, conn:
            conn.execute(project_identity.PROJECT_IDENTITY_SCHEMA)
            conn.execute("INSERT INTO project_identity VALUES (?, 'healthy', 'remote')", (source,))
        def session(name, tokens, model):
            path = self.session(name, tokens, model=model)
            rows = [json.loads(line) for line in path.read_text().splitlines()]
            rows[0]["payload"]["cwd"] = source
            path.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
        session("one", 100, "old-model")
        self.run_rollup()
        blocker = {"source_path": str(self.root / "unrelated"), "reason": "source_unavailable"}
        with mock.patch.object(IDENTITY_STORE, "list_blockers", return_value=[blocker]):
            session("two", 70, "old-model")
            self.run_rollup()
            self.assertEqual(self.rows(), [("2026-02-10", "old-model", 170)])
            self.assertEqual(self.rows("daily_rollup"), [("2026-02-10", "old-model", 170)])
            session("one", 100, "new-model")
            session("two", 70, "new-model")
            check = rollup.check_rollup(self.db, lambda: self.load(diagnostic=True), now=lambda: self.now)
            self.assertEqual(check["would_write"]["count"], 1)
            self.run_rollup()
            self.assertEqual(self.rows("daily_rollup"), [("2026-02-10", "new-model", 170)])
            self.assertEqual(self.rows(), [("2026-02-10", "new-model", 170)])

    def test_authority_excludes_union_of_persisted_and_current_blocker_candidates(self):
        source = str(self.root / "blocked")
        known = {source, "stored", "old-resolved", "old-alias", "old-pin", "new-resolved", "new-pin", "healthy"}
        with contextlib.closing(sqlite3.connect(IDENTITY_STORE.path)) as conn, conn:
            conn.execute(project_identity.PROJECT_IDENTITY_SCHEMA)
            conn.executemany("INSERT INTO project_identity VALUES (?, ?, 'remote')",
                             [(source if project == "stored" else str(self.root / str(i)), project)
                              for i, project in enumerate(sorted(known))])
        persisted = {"source_path": source, "resolved_candidate": "old-resolved",
                     "resolved_project": "old-alias", "pin_candidate": "old-pin"}
        current = project_identity.ProjectIdentityConflict(
            "conflict", source, "conflicting_history",
            resolved_project="new-resolved", pin_candidate="new-pin")
        entry = UsageEntry(datetime(2026, 2, 10, tzinfo=timezone.utc), "s", "m", "r", "model", 100, 0, 0, 0, None, source, "codex")
        for diagnostic in (False, True):
            with self.subTest(diagnostic=diagnostic),\
                 mock.patch.object(IDENTITY_STORE, "list_blockers", return_value=[persisted]),\
                 mock.patch.object(IDENTITY_STORE, "resolve", side_effect=current),\
                 mock.patch.object(IDENTITY_STORE, "record_blocker", return_value=USAGE_LOADER.unpersisted_blocker_dict(current)),\
                 mock.patch.object(IDENTITY_STORE, "stored_projects", wraps=IDENTITY_STORE.stored_projects) as stored:
                loaded = USAGE_LOADER.enrich_entries([entry], read_only=diagnostic, diagnostic=diagnostic)
                self.assertEqual(loaded.history_authoritative_projects, {"healthy"})
                stored.assert_called_once_with(immutable=diagnostic)

    def test_blocked_alias_excludes_whole_project_and_unknown_history_is_preserved(self):
        self.seed()
        self.session()
        entry = self.load()[0]
        initial = [replace(entry, session_id=project, project=project) for project in ("healthy", "shared", "unknown")]
        rollup.run(db_path=self.db, entries_loader=lambda: usage_loader.LoadedEntries(initial, history_authoritative_from="2026-01-03"), now=lambda: self.now)
        healthy_path, good_alias, blocked_alias = [str(self.root / name) for name in ("healthy", "good-alias", "blocked-alias")]
        with contextlib.closing(sqlite3.connect(IDENTITY_STORE.path)) as conn, conn:
            conn.execute(project_identity.PROJECT_IDENTITY_SCHEMA)
            conn.executemany("INSERT INTO project_identity VALUES (?, ?, 'remote')",
                             [(healthy_path, "healthy"), (good_alias, "shared"), (blocked_alias, "shared")])
        raw = [replace(entry, session_id=path, project=path, input_tokens=170, model="new-model")
               for path in (healthy_path, good_alias, "unknown")]
        blocker = {"source_path": blocked_alias, "reason": "source_unavailable"}
        with mock.patch.object(IDENTITY_STORE, "list_blockers", return_value=[blocker]):
            loaded = USAGE_LOADER.enrich_entries(raw)
        loaded.history_authoritative_from = "2026-01-03"
        self.assertEqual(loaded.history_authoritative_projects, {"healthy"})
        checked = rollup.check_rollup(self.db, lambda: loaded, now=lambda: self.now)
        self.assertEqual([row["key"][2] for row in checked["would_write"]["items"]], ["healthy"])
        rollup.run(db_path=self.db, entries_loader=lambda: loaded, now=lambda: self.now)
        with contextlib.closing(sqlite3.connect(self.db)) as conn:
            self.assertEqual(conn.execute("SELECT project, model, input_tokens FROM daily_rollup ORDER BY project").fetchall(),
                             [("healthy", "new-model", 170), ("shared", "gpt-5", 100), ("unknown", "gpt-5", 100)])
        self.assertEqual(self.rows(), [("2026-02-10", "new-model", 510)])

    def test_codex_parent_change_invalidates_unchanged_child_cache(self):
        self.models_db().close()
        parent = self.session("one", tokens=1000)
        child = self.session("child", tokens=1200)
        rows = child.read_text().splitlines()
        meta = json.loads(rows[0])
        meta["payload"]["history_base"] = {"thread_id": "one", "end_byte_offset": parent.stat().st_size}
        rows[0] = json.dumps(meta)
        child.write_text("\n".join(rows) + "\n")
        self.assertEqual(next(e.input_tokens for e in self.load() if e.session_id == "child"), 200)
        before = child.stat().st_mtime_ns
        parent.write_text(parent.read_text().replace('"input_tokens": 1000', '"input_tokens": 1100'))
        self.assertEqual(next(e.input_tokens for e in self.load() if e.session_id == "child"), 100)
        self.assertEqual(child.stat().st_mtime_ns, before)

    def test_duplicate_file_cache_keeps_first_sorted_winner(self):
        one, two = self.sessions / "a.jsonl", self.sessions / "b.jsonl"
        one.write_text("a")
        two.write_text("b")
        entry = UsageEntry(datetime(2026, 2, 10, tzinfo=timezone.utc), "s", "m", "r", "first", 100, 0, 0, 0, None, "project", "claude")
        archive = UsageArchive(self.archive_path)
        def parse(path):
            return [entry if path == one else replace(entry, model="second")]
        def collect():
            return archive.collect([two, one], parse, lambda p: str(p.stat().st_mtime_ns), [], usage_loader.USAGE_TIMEZONE)[0]
        self.assertEqual([e.model for e in collect()], ["first"])
        two.write_text("changed")
        self.assertEqual([e.model for e in collect()], ["first"])

    def test_authoritative_diagnostic_matches_shrink_rewrite(self):
        self.seed()
        self.session(tokens=100)
        self.run_rollup()
        self.session(tokens=70)
        result = rollup.check_rollup(self.db, lambda: self.load(diagnostic=True), now=lambda: self.now)
        self.assertEqual(result["would_skip"]["count"], 0)
        self.assertEqual(result["usage_would_write"]["count"], 1)
        self.run_rollup()
        self.assertEqual(self.rows(), [("2026-02-10", "gpt-5", 70)])


if __name__ == "__main__":
    unittest.main()
