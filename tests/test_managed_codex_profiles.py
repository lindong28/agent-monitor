from agent_monitor.quota import quota_service
import contextlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from agent_monitor.contracts.usage import LoadedEntries
from agent_monitor.ingestion import ProjectIdentityStore, UsageLoader
from agent_monitor.snapshots import exporter
from parsers import codex, codex_profiles
from agent_monitor import http_server as server
from agent_monitor.snapshots import statistics_snapshot
from agent_monitor.contracts.session import SessionIndex, merge_metadata
from agent_monitor.ingestion.usage_archive import UsageArchive


class ManagedCodexTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.loader = UsageLoader(ProjectIdentityStore(self.root / "project_identity.db"))
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(mock.patch.dict(os.environ, {"HOME": str(self.root), "AGENT_MONITOR_EXTRA_JSONL": ""}))
        self.default = self.root / ".codex"
        for target, value in (("parsers.codex.SESSIONS_DIR", str(self.default / "sessions")),
                              ("parsers.codex.STATE_DB", str(self.default / "state_5.sqlite")),
                              ("parsers.claude._get_claude_dirs", None)):
            self.stack.enter_context(mock.patch(target, return_value=[]) if value is None else mock.patch(target, value))
        self.registry = self.root / ".local/share/codex-accounts"

    def profile(self, name, identity=True):
        home = self.registry / name
        home.mkdir(parents=True)
        (home / "profile.json").write_text(json.dumps({"schema_version": 1, "name": name,
            "identity": {"account_id": "shared-workspace", "email": name + "@example.test", "subject": name} if identity else None}))
        return home / "codex"

    def session(self, home, sid, tokens, used, model="gpt-5", archived=False):
        folder = home / ("archived_sessions" if archived else "sessions")
        folder.mkdir(parents=True, exist_ok=True)
        rows = [{"type": "session_meta", "payload": {"id": sid, "cwd": "repo", "source": "cli", "model": model}},
                {"timestamp": "2026-10-01T00:00:00Z", "type": "event_msg", "payload": {"type": "token_count",
                 "info": {"total_token_usage": {"input_tokens": tokens, "output_tokens": 2, "cached_input_tokens": 0}},
                 "rate_limits": {"primary": {"used_percent": used, "window_minutes": 300, "resets_at": 4102444800}}}}]
        (folder / (sid + ".jsonl")).write_text("\n".join(map(json.dumps, rows)) + "\n")
        with sqlite3.connect(home / "state_5.sqlite") as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS threads (id TEXT, model TEXT, title TEXT)")
            conn.execute("INSERT INTO threads VALUES (?, ?, ?)", (sid, model, "Task " + model))

    def collect(self):
        archive = UsageArchive(self.root / "archive.sqlite3")
        errors = []
        entries, _ = self.loader.load_archived_entries(archive, errors, False, False)
        return LoadedEntries(entries, session_metadata=archive.session_metadata), errors

    def test_two_profiles_default_archive_models_snapshot_and_session_api(self):
        self.session(self.default, "default", 3, 5, "default-model")
        for name, tokens, used in (("alpha", 10, 21), ("beta", 30, 82)):
            self.session(self.profile(name), name, tokens, used, name + "-model", archived=name == "beta")
        entries, errors = self.collect()
        self.assertEqual(errors, [])
        self.assertEqual(sum(e.input_tokens for e in entries), 43)
        self.assertEqual({e.session_id: e.model for e in entries}, {n: n + "-model" for n in ("default", "alpha", "beta")})
        snapshot = statistics_snapshot.write_snapshot(self.root / "snapshot.db", entries)
        self.assertEqual(snapshot["version"], 5)
        loaded = LoadedEntries(snapshot["usage_entries"], session_metadata=snapshot["session_metadata"])
        views = server.create_views(usage_source=self.loader)
        with mock.patch("agent_monitor.http_server.hub.enabled", return_value=False), mock.patch.object(self.loader, "load_all_entries", return_value=loaded):
            rows = {r["session_id"]: r for r in server.sessions_endpoint({"range": ["all"]}, views=views)}
            self.assertIsNone(rows["default"]["account"])
            self.assertEqual({sid: r["title"] for sid, r in rows.items()}, {n: "Task " + n + "-model" for n in ("default", "alpha", "beta")})
            for name in ("alpha", "beta"):
                self.assertEqual(rows[name]["account"]["email"], name + "@example.test")
                detail = server.session_detail(name, query={"range": ["all"]}, views=views)
                self.assertEqual(detail["meta"]["account"], rows[name]["account"])

    def test_passive_quota_reaches_existing_accounts_consumer(self):
        for name, used in (("alpha", 21), ("beta", 82)):
            self.session(self.profile(name), name, 10, used)
        with mock.patch("agent_monitor.quota.quota_service.quota_refresh.load_rate_limits", side_effect=AssertionError("no active queries")):
            blocks = quota_service.managed_codex_rate_limits()
        current = SimpleNamespace(host="fixture", meta={"rate_limits": {"codex": {"signed_out": True, "managed_accounts": blocks}}})
        admission = SimpleNamespace(admitted=[current])
        with mock.patch("agent_monitor.http_server.hub.enabled", return_value=False), mock.patch("agent_monitor.quota.quota_view.self_machine_name", return_value="fixture"):
            rows = server.QUOTA_VIEW.live_from_admission(admission)["codex"]["accounts"]
        self.assertEqual({r["account_label"]: r["five_hour_used_pct"] for r in rows}, {"alpha@example.test": 21, "beta@example.test": 82})
        self.assertEqual({r["account_profile"] for r in rows}, {"alpha", "beta"})

    def test_exporter_includes_profiles_without_refreshing_them(self):
        self.session(self.profile("alpha"), "alpha", 10, 21)
        with mock.patch("agent_monitor.hub_config.enabled", return_value=False), mock.patch.object(exporter, "ROOT", self.root),\
             mock.patch("agent_monitor.quota.quota_service.accounts.codex_account", return_value=None),\
             mock.patch("agent_monitor.quota.quota_service.accounts.claude_account", return_value=None),\
             mock.patch("agent_monitor.quota.quota_service.claude_status.load_rate_limits", return_value=None),\
             mock.patch("agent_monitor.quota.quota_service.quota_refresh.load_rate_limits", side_effect=AssertionError("no active queries")):
            limits = exporter._rate_limits(refresh=False)
        self.assertEqual(set(limits), {"claude", "codex"})
        self.assertEqual(limits["codex"]["managed_accounts"][0]["five_hour_pct"], 21)
        self.assertTrue(limits["codex"]["signed_out"])

    def test_default_and_managed_readings_share_one_machine_membership(self):
        older = {"account_id": "shared", "account_label": "member@example.test",
                 "updated_at": "2026-10-01T00:00:00Z", "five_hour_pct": 21}
        newer = {**older, "account_profile": "work", "five_hour_pct": 35,
                 "updated_at": "2026-10-02T00:00:00Z"}
        for hosts in (("first",), ("first", "second")):
            with self.subTest(hosts=hosts):
                admission = SimpleNamespace(admitted=[SimpleNamespace(host=host, meta={"rate_limits": {
                    "codex": {**older, "managed_accounts": [newer]}}}) for host in hosts])
                before = json.dumps([current.meta for current in admission.admitted])
                with mock.patch("agent_monitor.http_server.hub.enabled", return_value=False),\
                     mock.patch("agent_monitor.quota.quota_view.self_machine_name", return_value="first"):
                    rows = server.QUOTA_VIEW.live_from_admission(admission)["codex"]["accounts"]
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0]["machines"], list(hosts))
                self.assertEqual(rows[0]["five_hour_used_pct"], 35)
                self.assertEqual(rows[0]["reading_from"], "first")
                self.assertEqual(json.dumps([current.meta for current in admission.admitted]), before)

    def test_version_four_snapshot_remains_readable(self):
        self.session(self.default, "default", 3, 5)
        entries, _ = self.collect()
        path = self.root / "v4.db"
        statistics_snapshot.write_snapshot(path, entries)
        with sqlite3.connect(path) as conn:
            meta = json.loads(conn.execute("SELECT payload FROM statistics_meta").fetchone()[0])
            meta["version"] = 4
            conn.execute("UPDATE statistics_meta SET payload=?", (json.dumps(meta),))
            for position, payload in conn.execute("SELECT position,payload FROM statistics_sessions").fetchall():
                row = json.loads(payload)
                row.pop("account", None)
                conn.execute("UPDATE statistics_sessions SET payload=? WHERE position=?", (json.dumps(row), position))
        loaded = statistics_snapshot.read_snapshot(path)
        self.assertEqual(loaded["version"], 4)
        self.assertNotIn("account", loaded["session_metadata"][0])

    def test_ancestry_cannot_link_distinct_accounts(self):
        from agent_monitor.contracts.session import codex_metadata
        root = codex_metadata("root", "cli")
        child = codex_metadata("child", {"subagent": {"thread_spawn": {"parent_thread_id": "root"}}})
        for row, name in ((root, "alpha"), (child, "beta")):
            row["account"] = {"name": name, "email": name + "@example.test", "account_id": "workspace"}
        index = SessionIndex([root, child])
        self.assertEqual(index.root(("codex", "child")), ("codex", "child"))
        self.assertEqual(index.status(("codex", "child")), "conflict")

    def test_invalid_profile_unknown_identity_and_duplicate_session_are_not_rebound(self):
        self.profile("unbound", identity=False)
        bad = self.profile("bad")
        (bad.parent / "profile.json").write_text('{"schema_version": 9}')
        errors = []
        self.assertEqual([p.account for p in codex_profiles.discover(source_errors=errors)], [None])
        self.assertEqual(len(errors), 1)
        self.session(self.default, "copied", 10, 21)
        self.session(self.profile("alpha"), "copied", 10, 21)
        entries, _ = self.collect()
        row = next(r for r in entries.session_metadata if r["session_id"] == "copied")
        self.assertIsNone(row["account"])
        self.assertIsNone(merge_metadata([row], [{**row, "account": {"name": "alpha", "email": "a", "account_id": "a"}}])[0]["account"])

    def test_ui_renders_the_account_identity_and_unknown_boundary(self):
        script = r'''
const fs=require('fs'),assert=require('assert');
global.document={readyState:'loading',addEventListener(){}};
global.window={location:{origin:'http://fixture.invalid'}};
require('./web/app.js')();
const ui=window.AgentMonitor;
assert.equal(ui.codexSessionAccountLabel({name:'alpha',email:'alpha@example.test'}),'账号：alpha · alpha@example.test');
assert.equal(ui.codexSessionAccountLabel(null),'账号归属未记录或冲突');
for (const profile of [undefined, 'work-profile']) {
  assert.equal(ui.quotaAccountName({account_label:'beta@example.test',account_profile:profile}),'beta@example.test');
}
assert.equal(ui.quotaAccountName({account_id:'abcdefghijk',account_profile:'work-profile'}),'账号 abcdefgh');
'''
        result = subprocess.run(["node", "-e", script], cwd=Path(__file__).parents[1], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
