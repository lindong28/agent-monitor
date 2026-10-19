import json
import sqlite3
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from aggregators import LoadedEntries
from parsers import UsageEntry, codex
from session_identity import SessionIndex, codex_metadata, merge_metadata
from usage_archive import UsageArchive
import server
import statistics_snapshot


def identity(sid, parent=None, **kwargs):
    source = {"subagent": {"thread_spawn": {"parent_thread_id": parent}}} if parent else "cli"
    return codex_metadata(sid, source, **kwargs)


class ConversationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.base = UsageEntry(datetime(2026, 1, 1, tzinfo=timezone.utc), "root", "root", "r", "A", 10, 2, 0, 0, 1., "repo", "codex")
        self.metadata = [identity("root", title="A <title>", created_at="2025-12-01T00:00:00Z", project="repo"),
                         identity("child", "root"), identity("nested", "child"), identity("independent")]
        self.entries = LoadedEntries([self.base,
            replace(self.base, timestamp=datetime(2026, 1, 2, tzinfo=timezone.utc), session_id="child", message_id="child", model="B", cost_usd=2.),
            replace(self.base, timestamp=datetime(2026, 1, 3, tzinfo=timezone.utc), session_id="nested", message_id="nested", model="C", cost_usd=None),
            replace(self.base, session_id="independent", message_id="independent", cost_usd=0.)], session_metadata=self.metadata)

    def test_recursive_groups_preserve_every_fact_and_keep_agents_separate(self):
        other = replace(self.base, agent_id="claude-code", message_id="other")
        entries = [*self.entries, other]
        groups = SessionIndex(self.metadata).group(entries)
        self.assertEqual(set(groups), {("codex", "root"), ("codex", "independent"), ("claude-code", "root")})
        self.assertEqual([e.session_id for e in groups[("codex", "root")]], ["root", "child", "nested"])
        flattened = [e for rows in groups.values() for e in rows]
        self.assertCountEqual(flattened, entries)
        self.assertEqual(sum(e.input_tokens for e in flattened), sum(e.input_tokens for e in entries))
        self.assertEqual(sum(e.cost_usd or 0 for e in flattened), sum(e.cost_usd or 0 for e in entries))

    def test_missing_conflicting_and_cyclic_parents_never_hide_or_merge_usage(self):
        variants = [
            [identity("child", "missing"), identity("nested", "child")],
            merge_metadata([identity("child", "root")], [identity("child", "other"), identity("root"), identity("nested", "child")]),
            [identity("child", "nested"), identity("nested", "child")],
        ]
        for rows in variants:
            with self.subTest(rows=rows):
                index = SessionIndex(rows)
                self.assertEqual(index.root(("codex", "child")), ("codex", "child"))
                self.assertEqual(index.root(("codex", "nested")), ("codex", "nested"))
                self.assertEqual(len(index.group(self.entries)), 4)

    def test_filtered_child_resolves_parent_and_default_detail_matches(self):
        query = {"range": ["custom"], "start": ["2026-01-02"], "end": ["2026-01-02"], "model": ["B"]}
        with mock.patch("server.hub.enabled", return_value=False), mock.patch("server.load_all_entries", return_value=self.entries):
            rows = server.sessions_endpoint(query)
            self.assertEqual(len(rows), 1)
            row = rows[0]
            self.assertEqual((row["session_id"], row["tokens"], row["cost_usd"], row["title"]), ("root", 12, 2., "A <title>"))
            self.assertEqual(row["created_at"], "2025-12-01T00:00:00+00:00")
            detail = server.session_detail("root", query=query)
            self.assertEqual((detail["meta"]["tokens"], detail["meta"]["cost_usd"]), (row["tokens"], row["cost_usd"]))
            self.assertEqual([e["session_id"] for e in detail["entries"]], ["child"])
            all_detail = server.session_detail("nested", query={**query, "scope": ["all"]})
            self.assertEqual(all_detail["meta"]["session_id"], "root")
            self.assertEqual((len(all_detail["entries"]), all_detail["meta"]["cost_usd"], all_detail["meta"]["unpriced_records"]), (3, 3., 1))
            self.assertEqual(len(all_detail["meta"]["executions"]), 3)

    def test_latest_usage_and_unknown_zero_partial_cost(self):
        with mock.patch("server.hub.enabled", return_value=False), mock.patch("server.load_all_entries", return_value=self.entries):
            rows = server.sessions_endpoint({"range": ["all"]})
            self.assertEqual([r["session_id"] for r in rows], ["root", "independent"])
            self.assertEqual([r["cost_usd"] for r in rows], [3., 0.])
            unknown = server.session_detail("root", query={"range": ["all"], "cost_status": ["unknown"]})
            self.assertIsNone(unknown["meta"]["cost_usd"])
            self.assertEqual(len(unknown["entries"]), 1)

    def test_owner_header_not_filename_or_inherited_metadata_and_forks_stay_separate(self):
        path = self.root / "physical-id.jsonl"
        rows = [{"type": "session_meta", "payload": {"id": "logical", "source": "cli", "forked_from_id": "root"}},
                {"type": "session_meta", "payload": {"id": "copied-parent", "source": "cli"}}]
        path.write_text("\n".join(json.dumps(r) for r in rows))
        metadata = codex.session_metadata(codex.HistoryResolver([path]), {})
        self.assertEqual([r["session_id"] for r in metadata], ["logical"])
        self.assertIsNone(metadata[0]["parent_session_id"])
        self.assertEqual(SessionIndex(metadata).root(("codex", "logical")), ("codex", "logical"))

    def test_archive_metadata_survives_absent_sources_and_updates_title(self):
        archive = UsageArchive(self.root / "archive.sqlite3")
        archive.collect([], None, None, [], timezone.utc, session_metadata=self.metadata)
        archive.collect([], None, None, [], timezone.utc, session_metadata=[identity("root", title="renamed")])
        reopened = UsageArchive(archive.path)
        reopened.read()
        index = SessionIndex(reopened.session_metadata)
        self.assertEqual(index.metadata[("codex", "root")]["title"], "renamed")
        self.assertEqual(index.root(("codex", "nested")), ("codex", "root"))

    def test_snapshot_roundtrip_old_reader_contract_and_metadata_retention(self):
        path = self.root / "new.db"
        statistics_snapshot.write_snapshot(path, self.entries)
        result = statistics_snapshot.read_snapshot(path)
        self.assertEqual(result["session_metadata"], self.metadata)
        self.assertEqual(result["usage_entries"], self.entries)
        old = self.root / "old.db"
        statistics_snapshot.write_snapshot(old, self.entries)
        with sqlite3.connect(old) as conn:
            meta = json.loads(conn.execute("SELECT payload FROM statistics_meta").fetchone()[0])
            meta["version"] = 3
            conn.execute("UPDATE statistics_meta SET payload=?", (json.dumps(meta),))
            conn.execute("DROP TABLE statistics_sessions")
        legacy = statistics_snapshot.read_snapshot(old)
        self.assertEqual(legacy["usage_entries"], self.entries)
        self.assertEqual(legacy["session_metadata"], [])
        with self.assertRaises(statistics_snapshot.StatisticsSnapshotError):
            statistics_snapshot.ensure_retained(path, old)

    def test_thread_database_metadata_is_optional_and_whitelisted(self):
        path = self.root / "threads.db"
        with sqlite3.connect(path) as conn:
            conn.execute("CREATE TABLE threads (id TEXT, model TEXT, source TEXT, title TEXT, name TEXT, created_at_ms INTEGER, first_user_message TEXT)")
            conn.execute("INSERT INTO threads VALUES (?, ?, ?, ?, ?, ?, ?)", ("root", "A", "cli", "title", "name", 1000, "PRIVATE_BODY"))
        models = codex._load_thread_models(path)
        self.assertEqual(models, {"root": "A"})
        self.assertEqual(models.session_metadata[0]["title"], "name")
        self.assertNotIn("PRIVATE_BODY", json.dumps(models.session_metadata))


if __name__ == "__main__":
    unittest.main()
