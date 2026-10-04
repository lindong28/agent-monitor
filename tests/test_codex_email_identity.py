"""Different Codex emails sharing a workspace must retain separate readings."""

import json
import unittest
from unittest import mock

import exporter
import server
from parsers import RateLimits, accounts
import test_account_memory as fixtures


class CodexEmailIdentityTests(unittest.TestCase):
    # Reuse the isolated memory and admitted-snapshot fixtures.
    setUp = fixtures.AccountMemoryTests.setUp
    current = staticmethod(fixtures.AccountMemoryTests.current)
    limit = staticmethod(fixtures.AccountMemoryTests.limit)
    admission = staticmethod(fixtures.AccountMemoryTests.admission)
    write_memory = fixtures.AccountMemoryTests.write_memory
    remembered_record = staticmethod(fixtures.AccountMemoryTests.remembered_record)

    def member(self, email, observed="2026-09-24T06:00:00Z", used=20):
        block = self.limit(observed, used, "shared-workspace")
        block["account_label"] = email
        return block

    def read_members(self, *members):
        return server._rate_limits(admission=self.admission(*members))["codex"]["accounts"]

    def test_switch_preserves_old_email_and_its_reading(self):
        self.read_members(self.current("studio", codex=self.member("first@example.com")))
        rows = self.read_members(self.current("studio", codex=self.member(
            "second@example.com", "2026-09-24T06:01:00Z", 80)))
        by_email = {row["account_label"]: row for row in rows}
        self.assertEqual(set(by_email), {"first@example.com", "second@example.com"})
        self.assertEqual(by_email["first@example.com"]["presence"], "remembered")
        self.assertEqual(by_email["first@example.com"]["five_hour_used_pct"], 20)
        self.assertEqual(by_email["second@example.com"]["presence"], "in_use")
        self.assertEqual(by_email["second@example.com"]["five_hour_used_pct"], 80)
        self.assertEqual(len(self.read_members()), 2)

    def test_remove_one_member_does_not_remove_or_block_another(self):
        self.read_members(self.current("studio", codex=self.member("first@example.com")))
        live = self.admission(self.current("mini", codex=self.member("second@example.com")))
        rows = server._rate_limits(admission=live)["codex"]["accounts"]
        old = next(row for row in rows if row["presence"] == "remembered")
        live_limits = server._live_rate_limits_from_admission(live)
        code, _ = server._remove_account_memory("codex", "shared-workspace", old["updated_at"], live_limits)
        self.assertEqual(code, 409, "Legacy selector must reject ambiguous identities")
        code, _ = server._remove_account_memory("codex", "shared-workspace", old["updated_at"],
                                                live_limits, account_label="second@example.com")
        self.assertEqual(code, 409, "The live member remains protected")
        code, _ = server._remove_account_memory("codex", "shared-workspace", old["updated_at"],
                                                live_limits, account_label="first@example.com")
        self.assertEqual(code, 200)
        self.assertEqual([row["account_label"] for row in self.read_members()], ["second@example.com"])

    def test_missing_email_is_separate_and_null_removal_is_not_wildcard(self):
        self.read_members(self.current("studio", codex=self.member(None)),
                          self.current("mini", codex=self.member("first@example.com")))
        rows = self.read_members()
        self.assertEqual(len(rows), 2)
        unknown = next(row for row in rows if row["account_label"] is None)
        code, _ = server._remove_account_memory("codex", "shared-workspace", unknown["updated_at"],
                                                {}, account_label=None)
        self.assertEqual(code, 200)
        self.assertEqual([row["account_label"] for row in self.read_members()], ["first@example.com"])

    def test_cache_does_not_cross_email_on_passive_or_active_export(self):
        for refresh in (False, True):
            for same_email in (False, True):
                with self.subTest(refresh=refresh, same_email=same_email):
                    cache = self.memory_path.parent / "quota_snapshot.json"
                    cache.parent.mkdir(parents=True, exist_ok=True)
                    old = self.member("first@example.com", "2026-09-24T06:02:00Z", 80)
                    cache.write_text(json.dumps({"codex": old}))
                    email = "first@example.com" if same_email else "second@example.com"
                    reading = RateLimits(20, 100, 21, 200, updated_at="2026-09-24T06:01:00Z")
                    with mock.patch.object(exporter, "ROOT", self.memory_path.parent.parent), \
                         mock.patch("hub.enabled", return_value=True), \
                         mock.patch.object(exporter.accounts, "codex_account", return_value=accounts.Account("shared-workspace", email)), \
                         mock.patch.object(exporter.accounts, "claude_account", return_value=None), \
                         mock.patch.object(exporter.codex, "load_rate_limits", return_value=reading), \
                         mock.patch.object(exporter.claude_status, "load_rate_limits", return_value=None), \
                         mock.patch.object(exporter.quota_refresh, "load_rate_limits", return_value=reading):
                        result = exporter._rate_limits(refresh=refresh)
                    block = json.loads(cache.read_text())["codex"] if refresh else result["codex"]
                    self.assertEqual(block["account_label"], email)
                    self.assertEqual(block["five_hour_pct"], 80 if same_email else 20)

    def test_live_grouping_separates_members_but_merges_same_member(self):
        rows = self.read_members(
            self.current("studio", codex=self.member("first@example.com")),
            self.current("mini", codex=self.member("second@example.com")),
            self.current("book", codex=self.member("first@example.com", "2026-09-24T06:01:00Z", 30)),
        )
        self.assertEqual(len(rows), 2)
        by_email = {row["account_label"]: row for row in rows}
        self.assertEqual(set(by_email["first@example.com"]["machines"]), {"studio", "book"})
        self.assertEqual(by_email["first@example.com"]["five_hour_used_pct"], 30)

    def test_v1_record_survives_switch_and_v2_reload(self):
        old = self.remembered_record("codex", "shared-workspace", "2026-09-24T06:00:00Z", 20,
                                     label="first@example.com")
        self.write_memory({"codex:shared-workspace": old})
        rows = self.read_members(self.current("studio", codex=self.member(
            "second@example.com", "2026-09-24T06:01:00Z", 80)))
        self.assertEqual({row["account_label"] for row in rows}, {"first@example.com", "second@example.com"})
        self.assertEqual(json.loads(self.memory_path.read_text())["version"], 2)
        state, _ = server._load_account_memory()
        self.assertEqual(state, "valid")
        self.assertEqual(len(self.read_members()), 2)
