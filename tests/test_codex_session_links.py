"""Cross-account selections must not become per-account usage/quota claims."""
import json
from pathlib import Path
import unittest
from unittest import mock

from agent_monitor.contracts.usage import LoadedEntries
import exporter
from agent_monitor import quota_service
import quota_applicability
from parsers import codex, codex_profiles
import server
import statistics_snapshot
from tests import test_managed_codex_profiles as fixtures
from usage_archive import UsageArchive

SID = "11111111-1111-4111-8111-111111111111"
OTHER = "22222222-2222-4222-8222-222222222222"


class SessionLinkTests(unittest.TestCase):
    setUp = fixtures.ManagedCodexTests.setUp
    profile = fixtures.ManagedCodexTests.profile
    session = fixtures.ManagedCodexTests.session
    collect = fixtures.ManagedCodexTests.collect

    def link(self, names=("alpha", "beta"), sid=SID):
        root = self.registry / "session-links"
        root.mkdir(parents=True, exist_ok=True)
        path = root / (sid + ".json")
        path.write_text(json.dumps({"schema_version": 1, "storage_account": names[0],
                                   "last_selected_account": names[-1], "account_names": list(names)}))
        return path

    def test_registry_change_invalidates_account_without_usage_reparse(self):
        self.session(self.profile("alpha"), SID, 10, 21)
        before, errors = self.collect()
        self.assertFalse(errors)
        self.assertEqual(before.session_metadata[0]["account"]["name"], "alpha")
        self.link()
        with mock.patch.object(self.loader, "parse_usage_file", side_effect=AssertionError("usage cache should remain valid")):
            after, errors = self.collect()
        self.assertFalse(errors)
        self.assertEqual(list(before), list(after))
        self.assertIsNone(after.session_metadata[0]["account"])
        snapshot = statistics_snapshot.write_snapshot(self.root / "snapshot.sqlite3", after)
        loaded = LoadedEntries(snapshot["usage_entries"], session_metadata=snapshot["session_metadata"])
        views = server.create_views(usage_source=self.loader)
        with mock.patch("server.hub.enabled", return_value=False), mock.patch.object(self.loader, "load_all_entries", return_value=loaded):
            self.assertIsNone(server.sessions_endpoint({"range": ["all"]}, views=views)[0]["account"])
            self.assertIsNone(server.session_detail(SID, query={"range": ["all"]}, views=views)["meta"]["account"])

    def test_retained_and_readonly_metadata_are_invalidated_after_rollout_removal(self):
        home = self.profile("alpha")
        self.session(home, SID, 10, 21)
        before, _ = self.collect()
        (home / "sessions" / (SID + ".jsonl")).unlink()
        (home / "state_5.sqlite").unlink()
        self.link()
        for read_only in (True, False):
            archive = UsageArchive(self.root / "archive.sqlite3")
            entries, _ = self.loader.load_archived_entries(archive, [], False, read_only)
            self.assertEqual(list(before), list(entries))
            self.assertIsNone(archive.session_metadata[0]["account"])
        persisted = UsageArchive(self.root / "archive.sqlite3")
        persisted.read()
        self.assertIsNone(persisted.session_metadata[0]["account"])

    def test_default_managed_and_archived_quota_cache_excludes_mixed_uuid(self):
        for name, archived in (("default", False), ("alpha", False), ("beta", True)):
            with self.subTest(name=name):
                home = self.default if name == "default" else self.profile(name)
                self.session(home, SID, 10, 82, archived=archived)
                folder = home / ("archived_sessions" if archived else "sessions")
                path = self.registry / "session-links" / (SID + ".json")
                if path.exists():
                    path.unlink()
                self.assertEqual(codex.load_rate_limits(folder, home / "state_5.sqlite").five_hour_pct, 82)
                self.link((name, "selected"))
                self.assertIsNone(codex.load_rate_limits(folder, home / "state_5.sqlite"))
                self.session(home, OTHER, 4, 21, archived=archived)
                self.assertEqual(codex.load_rate_limits(folder, home / "state_5.sqlite").five_hour_pct, 21)

    def test_invalid_record_and_registry_are_unknown_with_source_errors(self):
        self.session(self.profile("alpha"), SID, 10, 21)
        path = self.link()
        path.write_text("{broken")
        entries, errors = self.collect()
        self.assertIsNone(entries.session_metadata[0]["account"])
        self.assertTrue(any(e["stage"] == "session_links" for e in errors))
        self.assertIsNone(quota_service.managed_codex_rate_limits()[0]["five_hour_pct"])
        self.assertEqual(quota_service.managed_codex_rate_limits()[0]["query_state"], "unknown")
        path.unlink()
        path.parent.rmdir()
        path.parent.write_text("not a directory")
        links = codex_profiles.session_links()
        self.assertTrue(links.unavailable)
        self.assertTrue(links.errors)
        self.assertIsNone(codex.load_rate_limits(self.registry / "alpha/codex/sessions"))

    def test_single_selection_allows_quota_and_registry_read_once_per_export(self):
        self.session(self.profile("alpha"), SID, 10, 21)
        self.session(self.profile("beta"), OTHER, 20, 82, archived=True)
        self.link(("alpha",))
        with mock.patch("parsers.codex_profiles.session_links", wraps=codex_profiles.session_links) as read, \
             mock.patch("agent_monitor.quota_service.quota_refresh.load_rate_limits", side_effect=AssertionError("no provider")):
            blocks = quota_service.managed_codex_rate_limits()
        self.assertEqual(read.call_count, 1)
        self.assertEqual([b["five_hour_pct"] for b in blocks], [21, 82])

    def test_cached_passive_is_removed_but_active_identity_survives(self):
        self.link()
        links = codex_profiles.session_links()
        cases = [({}, False), ({"source": "codex_session", "session_id": SID}, False),
                 ({"source": "codex_session", "session_id": OTHER}, True),
                 ({"source": "active_query"}, True)]
        for observation, expected in cases:
            with self.subTest(observation=observation):
                block = {"account_id": "alpha", "quota_observation": observation, "five_hour_pct": 82}
                self.assertEqual(links.accepts_cached_quota(block), expected)
                cache = self.root / "quota.json"
                cache.write_text(json.dumps({"codex": block}))
                quota_service.save_cache(cache, {}, session_links=links)
                stored = json.loads(cache.read_text())
                self.assertEqual("codex" in stored, expected)
                if expected:
                    self.assertEqual(stored["codex"], block)

    def test_default_export_does_not_restore_old_passive_snapshot(self):
        self.session(self.default, SID, 10, 82)
        self.link(("default", "alpha"))
        account = quota_service.accounts.Account("workspace", "default@example.test", None)
        cache = {"codex": {"account_id": account.account_id, "account_label": account.label,
                          "five_hour_pct": 82, "updated_at": "2099-01-01T00:00:00Z"}}
        with mock.patch("hub.enabled", return_value=True), mock.patch.object(exporter, "ROOT", self.root), \
             mock.patch("agent_monitor.quota_service.read_cache", return_value=cache), \
             mock.patch("agent_monitor.quota_service.accounts.codex_account", return_value=account), \
             mock.patch("agent_monitor.quota_service.accounts.claude_account", return_value=None), \
             mock.patch("agent_monitor.quota_service.claude_status.load_rate_limits", return_value=None), \
             mock.patch("agent_monitor.quota_service.quota_refresh.load_rate_limits", side_effect=AssertionError("no provider")):
            block = exporter._rate_limits(refresh=False)["codex"]
        self.assertTrue(block is None or block.get("five_hour_pct") is None)

    def test_session_cli_reader_excludes_mixed_uuid(self):
        self.session(self.default, SID, 10, 82)
        self.link(("default", "alpha"))
        with self.assertRaisesRegex(ValueError, "mixed or unverified"):
            codex.load_session_rate_limits(SID)

    def test_observation_keeps_uuid_and_gui_preserves_active_reading(self):
        self.session(self.profile("alpha"), SID, 10, 21)
        block = quota_service.managed_codex_rate_limits()[0]
        self.assertEqual(quota_applicability.observation(block["quota_observation"])["session_id"], SID)
        self.link().write_text("invalid")
        active = {"account_id": "workspace", "account_label": "alpha@example.test", "five_hour_pct": 42,
                  "quota_observation": {"source": "active_query", "observed_at": "2026-10-01T00:00:00Z"}}
        with mock.patch("hub.request_quota_refresh", return_value={"codex": active}):
            result = exporter._gui_rate_limits()["codex"]
        for key, value in active.items():
            self.assertEqual(result[key], value)
        self.assertNotIn("refresh_error", result)
        passive = dict(active, quota_observation={"source": "codex_session", "session_id": SID})
        with mock.patch("hub.request_quota_refresh", return_value={"codex": passive}):
            result = exporter._gui_rate_limits()["codex"]
        self.assertIsNone(result["five_hour_pct"])
        self.assertEqual(result["account_id"], passive["account_id"])
        self.assertEqual(result["account_label"], passive["account_label"])
