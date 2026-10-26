"""Account applicability uses isolated files and never accesses providers."""
import dataclasses
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock
from datetime import datetime, timedelta, timezone

import exporter
import quota_applicability as qa
import quota_refresh
import server
import upstream_controls
from parsers import RateLimits
from parsers.accounts import Account


class QuotaApplicabilityTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.store = qa.Store(self.root / "state")
        for module in (exporter, quota_refresh):
            patcher = mock.patch.object(module, "ROOT", self.root)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = mock.patch.object(server, "_ACCOUNT_MEMORY_PATH", self.root / "state/account_memory.json")
        patcher.start()
        self.addCleanup(patcher.stop)
        self.now = datetime.now(timezone.utc)
        self.confirmed = (self.now - timedelta(hours=1)).isoformat()
        self.account = Account("fixture-account", "fixture@example.test")

    def entry(self, source="active_query", **updates):
        value = dict(account_id="fixture-account", account_label="fixture@example.test",
                     account_state="known", account_plan="default_claude_max_20x", machines=["fixture"],
                     reading_from="fixture", five_hour_used_pct=7, seven_day_used_pct=8,
                     five_hour_resets_at=(self.now + timedelta(hours=2)).timestamp(),
                     seven_day_resets_at=(self.now + timedelta(days=2)).timestamp(),
                     updated_at=self.now.isoformat(), refresh_error=None,
                     quota_observation={"source": source, "observed_at": self.now.isoformat()})
        value.update(updates)
        return value

    def project(self, entry):
        data = {"claude": {"accounts": [entry], "refresh_errors": []}}
        server._apply_quota_applicability(data)
        return entry

    def test_confirmed_no_subscription_survives_reload_and_passive_new_reading(self):
        self.store.confirm("claude", self.account.account_id, self.confirmed)
        self.assertIsNotNone(qa.Store(self.root / "state").get("claude", self.account.account_id))
        entry = self.project(self.entry("claude_statusline"))
        self.assertEqual(entry["quota_status"], "no_subscription")
        self.assertIsNone(entry["seven_day_used_pct"])
        self.assertIsNone(entry["updated_at"])
        self.assertEqual(entry["last_observation"]["seven_day_used_pct"], 8)

    def test_legacy_time_never_looks_like_recent_quota(self):
        entry = self.project(self.entry(quota_observation=None))
        self.assertEqual(entry["quota_status"], "unverified")
        self.assertIsNone(entry["updated_at"])
        self.assertEqual(entry["last_observation"]["recorded_at"], self.now.isoformat())
        self.assertIsNone(entry["last_observation"]["observed_at"])

    def test_403_is_not_subscription_evidence(self):
        entry = self.project(self.entry(quota_access_refused=True, refresh_error="refused"))
        self.assertEqual(entry["quota_status"], "access_denied")
        self.assertIsNone(self.store.get("claude", self.account.account_id))

    def test_new_active_current_query_restores_but_old_or_expired_does_not(self):
        self.store.confirm("claude", self.account.account_id, self.confirmed)
        for kind in ("old", "expired", "empty", "passive", "failed"):
            entry = self.entry()
            if kind == "old":
                entry["quota_observation"]["observed_at"] = (self.now - timedelta(days=1)).isoformat()
            elif kind == "expired":
                entry.update(five_hour_resets_at=1, seven_day_resets_at=2)
            elif kind == "empty":
                entry.update(five_hour_used_pct=None, seven_day_used_pct=None)
            elif kind == "passive":
                entry["quota_observation"]["source"] = "claude_statusline"
            else:
                entry["refresh_error"] = "refused"
            with self.subTest(kind=kind):
                self.assertFalse(self.store.restore("claude", self.account.account_id, entry))
                self.assertEqual(self.project(entry)["quota_status"], "no_subscription")
        entry = self.entry()
        self.assertEqual(self.project(dict(entry))["quota_status"], "no_subscription")
        self.assertTrue(self.store.restore("claude", self.account.account_id, entry))
        self.assertEqual(self.project(dict(entry))["quota_status"], "current")

    def test_confirmed_account_skips_child_without_clearing_provider_pause(self):
        self.store.confirm("claude", self.account.account_id, self.confirmed)
        controls = upstream_controls.Controls(self.root / "state/upstream_controls")
        lease = controls.acquire("claude")
        lease.pause("quotaRefreshFailed")
        lease.close()
        pause = controls.read("claude")["pause"]
        with mock.patch.object(quota_refresh.subprocess, "Popen") as spawn:
            with self.assertRaises(quota_refresh.NoSubscription):
                quota_refresh.load_rate_limits("claude", self.account)
            with self.assertRaises(quota_refresh.RefreshError):
                quota_refresh.load_rate_limits("claude", Account("other"))
        spawn.assert_not_called()
        self.assertEqual(controls.read("claude")["pause"], pause)

    def test_export_does_not_query_confirmed_account(self):
        self.store.confirm("claude", self.account.account_id, self.confirmed)
        with mock.patch.object(exporter.accounts, "claude_account", return_value=self.account), \
                mock.patch.object(exporter.accounts, "codex_account", return_value=None), \
                mock.patch.object(exporter.claude_status, "load_rate_limits", return_value=RateLimits(7, 1, 8, 2)), \
                mock.patch.object(exporter.codex, "load_rate_limits", return_value=None), \
                mock.patch.object(exporter.quota_refresh, "load_rate_limits", side_effect=quota_refresh.NotSignedIn("fixture")) as query:
            value = exporter._rate_limits(refresh=True)["claude"]
        self.assertEqual(value["subscription_confirmation"]["state"], "no_subscription")
        self.assertIsNone(value["refresh_error"])
        self.assertEqual([call.args[0] for call in query.call_args_list], ["codex"])

    def test_expired_single_window_is_not_current_but_other_window_can_be(self):
        entry = self.project(self.entry(five_hour_resets_at=1))
        self.assertEqual(entry["quota_status"], "current")
        self.assertIsNone(entry["five_hour_used_pct"])
        self.assertEqual(entry["seven_day_used_pct"], 8)

    def test_bad_state_is_not_silently_replaced(self):
        self.store.path.parent.mkdir(parents=True)
        self.store.path.write_text("bad")
        with self.assertRaises(ValueError):
            self.store.confirm("claude", self.account.account_id)
        self.assertEqual(self.store.path.read_text(), "bad")
        self.assertEqual(self.project(self.entry())["quota_status"], "unverified")

    def test_trusted_observation_replaces_false_recent_memory_preserving_legacy(self):
        legacy = self.entry(updated_at=(self.now + timedelta(days=1)).isoformat(), quota_observation=None)
        server._upsert_account_memory({"claude": {"accounts": [legacy]}}, expected_delete_epoch=0)
        new = self.entry()
        server._upsert_account_memory({"claude": {"accounts": [new]}}, expected_delete_epoch=0)
        state, payload = server._load_account_memory()
        self.assertEqual(state, "valid")
        record = payload["accounts"]["claude:fixture-account"]
        self.assertEqual(record["observed_at"], self.now.isoformat())
        self.assertEqual(record["legacy_observation"]["observed_at"], legacy["updated_at"])
        server._upsert_account_memory({"claude": {"accounts": [legacy]}}, expected_delete_epoch=0)
        self.assertEqual(server._load_account_memory()[1], payload)

    def test_recovery_receipt_survives_expiry_passive_replacement_and_reload(self):
        self.store.confirm("claude", self.account.account_id, self.confirmed)
        self.assertTrue(self.store.restore("claude", self.account.account_id, self.entry()))
        self.assertEqual(self.project(self.entry())["quota_status"], "current")
        receipt = self.store.get("claude", self.account.account_id)
        self.assertEqual(receipt["restored_at"], self.now.isoformat())
        self.assertEqual(self.project(self.entry("claude_statusline", five_hour_resets_at=1, seven_day_resets_at=2))["quota_status"], "historical")
        with self.assertRaises(ValueError):
            self.store.confirm("claude", self.account.account_id, self.confirmed)
        self.store.confirm("claude", self.account.account_id, (self.now + timedelta(seconds=1)).isoformat())
        self.assertEqual(self.project(self.entry())["quota_status"], "no_subscription")

    def test_confirmation_moves_expected_403_to_history_only(self):
        self.store.confirm("claude", self.account.account_id, self.confirmed)
        entry = self.entry("claude_statusline")
        expected = {"machine": "fixture", "account_id": self.account.account_id,
                    "reason": "paused", "failure": {"http_status": 403}}
        unrelated = {"machine": "other", "account_id": "other", "reason": "timeout"}
        data = {"claude": {"accounts": [entry], "refresh_errors": [expected, unrelated]}}
        server._apply_quota_applicability(data)
        self.assertEqual(entry["historical_query_failures"], [expected])
        self.assertEqual(data["claude"]["refresh_errors"], [unrelated])

    def test_active_metadata_clock_wins_over_conflicting_receipt_time(self):
        entry = self.entry(updated_at=(self.now + timedelta(days=1)).isoformat())
        record = server._account_memory_entry("claude", entry)[1]
        self.assertEqual(record["observed_at"], self.now.isoformat())
        self.assertEqual(exporter._quota_time(entry)[1], self.now.timestamp())

    def test_recovery_probe_only_clears_pause_after_valid_matching_result(self):
        controls = upstream_controls.Controls(self.root / "state/upstream_controls")
        lease = controls.acquire("claude")
        lease.pause("quotaRefreshFailed")
        lease.close()
        pause_id = controls.read("claude")["pause"]["id"]
        self.store.confirm("claude", self.account.account_id, self.confirmed)
        def result(kind):
            now = datetime.now(timezone.utc)
            return RateLimits(7, (now + timedelta(hours=2)).timestamp() if kind == "valid" else 1,
                              8, (now + timedelta(days=2)).timestamp() if kind == "valid" else 2,
                              updated_at=now.isoformat(), quota_observation={"source": "active_query", "observed_at": now.isoformat()})
        with mock.patch.object(exporter.accounts, "claude_account", return_value=self.account), \
                mock.patch.object(exporter.quota_refresh, "load_rate_limits", side_effect=lambda *a, **k: result("expired")):
            with self.assertRaises(quota_refresh.RefreshError):
                exporter.recover_claude_quota(self.account.account_id, self.account.label, pause_id)
        self.assertEqual(controls.read("claude")["pause"]["id"], pause_id)
        self.assertTrue(qa.unavailable(self.store.get("claude", self.account.account_id)))
        with mock.patch.object(exporter.accounts, "claude_account", side_effect=[self.account, Account("other")]), \
                mock.patch.object(exporter.quota_refresh, "load_rate_limits", side_effect=lambda *a, **k: result("valid")):
            with self.assertRaises(quota_refresh.RefreshError):
                exporter.recover_claude_quota(self.account.account_id, self.account.label, pause_id)
        self.assertEqual(controls.read("claude")["pause"]["id"], pause_id)
        with mock.patch.object(exporter.accounts, "claude_account", return_value=self.account), \
                mock.patch.object(exporter.quota_refresh, "load_rate_limits", side_effect=lambda *a, **k: result("valid")) as query:
            block = exporter.recover_claude_quota(self.account.account_id, self.account.label, pause_id)
        self.assertEqual(query.call_count, 1)
        self.assertIsNone(controls.read("claude").get("pause"))
        self.assertFalse(qa.unavailable(self.store.get("claude", self.account.account_id)))
        self.assertIn("restored_at", block["subscription_confirmation"])
        self.assertEqual(exporter._read_quota_cache(self.root / "state/quota_snapshot.json")["claude"], block)

    def test_ordinary_active_result_cannot_retire_confirmation(self):
        self.store.confirm("claude", self.account.account_id, self.confirmed)
        entry = self.project(self.entry())
        self.assertEqual(entry["quota_status"], "no_subscription")
        self.assertTrue(qa.unavailable(self.store.get("claude", self.account.account_id)))

    def test_explicit_recovery_admission_without_pause_and_with_new_pause(self):
        self.store.confirm("claude", self.account.account_id, self.confirmed)
        # Stop at the pinned process boundary: reaching it proves admission,
        # while never launching a real provider subprocess.
        for paused in (False, True):
            if paused:
                controls = upstream_controls.Controls(self.root / "state/upstream_controls")
                lease = controls.acquire("claude")
                lease.pause("quotaRefreshFailed")
                lease.close()
            with self.subTest(paused=paused), \
                    mock.patch.object(quota_refresh.network_policy, "endpoint", return_value="http://agent-monitor:synthetic-test-capability@127.0.0.1:9"), \
                    mock.patch.object(quota_refresh.subprocess, "Popen", side_effect=OSError("fixture child")) as spawn:
                with self.assertRaises((OSError, quota_refresh.RefreshError)):
                    quota_refresh.load_rate_limits("claude", self.account, recovery_account_id=self.account.account_id)
                self.assertEqual(spawn.call_count, 0 if paused else 1)

    def test_new_passive_claude_reading_keeps_403_and_withholds_current_values(self):
        import hub
        from types import SimpleNamespace
        older = (self.now - timedelta(minutes=30)).isoformat()
        cached = {"account_id": self.account.account_id, "account_label": self.account.label,
                  "updated_at": older, "five_hour_pct": 10, "five_hour_resets_at": self.entry()["five_hour_resets_at"],
                  "seven_day_pct": 10, "seven_day_resets_at": self.entry()["seven_day_resets_at"],
                  "quota_observation": {"source": "active_query", "observed_at": older},
                  "refresh_error": "access refused", "refresh_failure": {"http_status": 403}}
        exporter._save_quota_cache(self.root / "state/quota_snapshot.json", {"claude": cached})
        passive = RateLimits(80, cached["five_hour_resets_at"], 80, cached["seven_day_resets_at"],
                             updated_at=self.now.isoformat(), quota_observation={"source": "claude_statusline", "observed_at": self.now.isoformat()})
        with mock.patch.object(hub, "enabled", return_value=True), \
                mock.patch.object(exporter.accounts, "claude_account", return_value=self.account), \
                mock.patch.object(exporter.accounts, "codex_account", return_value=None), \
                mock.patch.object(exporter.claude_status, "load_rate_limits", return_value=passive), \
                mock.patch.object(exporter.codex, "load_rate_limits", return_value=None):
            result = exporter._rate_limits(False)
        self.assertEqual(result["claude"]["refresh_failure"]["http_status"], 403)
        admission = SimpleNamespace(admitted=[SimpleNamespace(host="fixture", meta={"rate_limits": result})])
        with mock.patch.object(hub, "enabled", return_value=False):
            projected = server._live_rate_limits_from_admission(admission)
        server._apply_quota_applicability(projected)
        entry = projected["claude"]["accounts"][0]
        self.assertEqual(entry["quota_status"], "access_denied")
        self.assertIsNone(entry["seven_day_used_pct"])
        self.assertEqual(entry["last_observation"]["seven_day_used_pct"], 80)

    def test_codex_newer_event_outranks_older_active_cache_and_memory(self):
        import hub
        from types import SimpleNamespace
        old = self.entry()
        new = self.entry(updated_at=(self.now + timedelta(minutes=1)).isoformat(), quota_observation=None, seven_day_used_pct=80)
        server._upsert_account_memory({"codex": {"accounts": [old]}}, expected_delete_epoch=0)
        server._upsert_account_memory({"codex": {"accounts": [new]}}, expected_delete_epoch=0)
        self.assertEqual(next(iter(server._load_account_memory()[1]["accounts"].values()))["seven_day_used_pct"], 80)
        def raw(entry):
            return {**entry, "five_hour_pct": entry["five_hour_used_pct"], "seven_day_pct": entry["seven_day_used_pct"]}
        exporter._save_quota_cache(self.root / "state/quota_snapshot.json", {"codex": raw(old)})
        exporter._save_quota_cache(self.root / "state/quota_snapshot.json", {"codex": raw(new)})
        self.assertEqual(exporter._read_quota_cache(self.root / "state/quota_snapshot.json")["codex"]["seven_day_pct"], 80)
        admission = SimpleNamespace(admitted=[SimpleNamespace(host=host, meta={"rate_limits": {"codex": raw(value)}}) for host, value in (("old", old), ("new", new))])
        with mock.patch.object(hub, "enabled", return_value=False):
            result = server._live_rate_limits_from_admission(admission)
        self.assertEqual(result["codex"]["accounts"][0]["seven_day_used_pct"], 80)

    def test_new_user_confirmation_wins_over_remote_recovery_for_older_confirmation(self):
        older = {"state": "no_subscription", "source": "user_confirmed", "confirmed_at": self.confirmed,
                 "restored_at": (self.now + timedelta(seconds=1)).isoformat()}
        self.store.confirm("claude", self.account.account_id, self.now.isoformat())
        entry = self.project(self.entry(subscription_confirmation=older))
        self.assertEqual(entry["quota_status"], "no_subscription")
        self.assertTrue(qa.unavailable(self.store.get("claude", self.account.account_id)))

    def test_recovery_without_pause_saves_success_and_receipt(self):
        self.store.confirm("claude", self.account.account_id, self.confirmed)
        def result(*args, **kwargs):
            self.assertEqual(kwargs["recovery_account_id"], self.account.account_id)
            now = datetime.now(timezone.utc).isoformat()
            return RateLimits(7, self.entry()["five_hour_resets_at"], 8, self.entry()["seven_day_resets_at"],
                              updated_at=now, quota_observation={"source": "active_query", "observed_at": now})
        with mock.patch.object(exporter.accounts, "claude_account", return_value=self.account), \
                mock.patch.object(exporter.quota_refresh, "load_rate_limits", side_effect=result), \
                mock.patch.object(upstream_controls.Controls, "resume") as resume:
            exporter.recover_claude_quota(self.account.account_id, self.account.label, None)
        resume.assert_not_called()
        self.assertFalse(qa.unavailable(self.store.get("claude", self.account.account_id)))
