"""Codex reset-credit semantics, using no provider homes or network calls."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from agent_monitor.quota import codex_quota, quota_view, reset_credits
from agent_monitor.quota.account_memory import AccountMemory
from agent_monitor.quota import quota_service, quota_sync
from parsers.accounts import Account


class ResetCreditsTests(unittest.TestCase):
    def response(self, summary):
        return {"accountId": "workspace-a", "rateLimitResetCredits": summary,
                "rateLimits": {"primary": {"windowDurationMins": 300, "usedPercent": 12}}}

    def test_count_is_authoritative_and_expiry_states_remain_distinct(self):
        # Official schema: null expiry means no expiry; omitted is not that claim.
        reading = codex_quota.decode(self.response({"availableCount": 5, "credits": [
            {"status": "available", "expiresAt": 1900000000},
            {"status": "available", "expiresAt": None},
            {"status": "available"},
        ]}), "workspace-a")
        self.assertEqual(reading["reset_credits"], {"available_count": 5, "credits": [
            {"expires_at": 1900000000}, {"expires_at": None}, {}]})
        self.assertEqual(reading["five_hour_used_pct"], 12)

    def test_missing_summary_zero_and_count_only_are_distinct(self):
        for summary, expected in [(None, None),
                ({"availableCount": 0, "credits": []}, {"available_count": 0, "credits": []}),
                ({"availableCount": 2, "credits": None}, {"available_count": 2, "credits": None})]:
            with self.subTest(summary=summary):
                self.assertEqual(codex_quota.decode(self.response(summary), "workspace-a")["reset_credits"], expected)

    def test_invalid_optional_summary_does_not_destroy_window(self):
        for summary in [{}, {"availableCount": -1}, {"availableCount": True}, {"availableCount": "3"}]:
            with self.subTest(summary=summary):
                reading = codex_quota.decode(self.response(summary), "workspace-a")
                self.assertIsNone(reading["reset_credits"])
                self.assertEqual(reading["five_hour_used_pct"], 12)
        projected = reset_credits.normalize({"availableCount": 3, "credits": [
            {"status": "redeemed", "expiresAt": 1900000000},
            {"status": "available", "expiresAt": float("nan")},
            {"status": "available", "expiresAt": "invalid"},
        ]}, wire=True)
        self.assertEqual(projected, {"available_count": 3, "credits": [{}, {}]})

    def test_view_and_memory_fallback_do_not_mix_observation_times(self):
        summary = {"available_count": 2, "credits": [{"expires_at": 1900000000}]}
        block = {"account_id": "workspace-a", "account_label": "fixture@example.test",
                 "updated_at": "2026-11-11T00:00:00+00:00", "five_hour_pct": 12,
                 "reset_credits": summary}
        entry = quota_view.account_entry(block, ["fixture-node"])
        self.assertEqual(entry["reset_credits"], summary)
        with tempfile.TemporaryDirectory() as directory:
            memory = AccountMemory(Path(directory) / "memory.json")
            memory.upsert({"codex": {"accounts": [entry]}}, expected_delete_epoch=0)
            state, payload = memory.read()
            self.assertEqual(state, "valid")
            record = next(iter(payload["accounts"].values()))
            self.assertNotIn("reset_credits", record)
            self.assertIsNone(quota_view.remembered_rate_limit_entry(record)["reset_credits"])
            old = quota_view.account_entry({**block, "updated_at": "2026-11-10T00:00:00Z"}, ["fixture-node"])
            limits = {"codex": {"accounts": [old], "refresh_errors": [{"machine": "fixture-node"}]}}
            quota_view.QuotaView(memory, None).merge_remembered(limits)
            self.assertEqual(old["updated_at"], entry["updated_at"])
            self.assertIsNone(old["reset_credits"])

    def test_unknown_new_observation_never_borrows_old_credit_details(self):
        current = quota_view.account_entry({"account_id": "workspace-a", "updated_at": "2026-11-11T00:00:00Z"}, [])
        self.assertIsNone(current["reset_credits"])

    def test_account_action_publishes_native_and_managed_observations(self):
        reading = codex_quota.decode(self.response({"availableCount": 1, "credits": [
            {"status": "available", "expiresAt": None}]}), "workspace-a")
        identity = {"account_id": "workspace-a", "account_label": "fixture@example.test"}
        previous = {"codex": {**identity, "managed_accounts": [{**identity, "account_profile": "fixture-profile"}]}}
        with mock.patch.object(quota_service, "read_cache", return_value=previous), \
                mock.patch.object(quota_service, "save_cache") as save:
            quota_service.record_codex_reading(Path("/unused-fixture-cache"), Account("workspace-a", "fixture@example.test"), reading)
        block = save.call_args.args[1]["codex"]
        self.assertEqual(block["reset_credits"], reading["reset_credits"])
        self.assertEqual(block["managed_accounts"][0]["reset_credits"], reading["reset_credits"])
        self.assertEqual(block["updated_at"], reading["observed_at"])

    def test_remote_merge_selects_credit_details_with_the_window_observation(self):
        identity = {"account_id": "workspace-a", "account_label": "fixture@example.test"}
        newer = "2026-11-11T00:00:00+00:00"
        older = "2026-11-10T00:00:00+00:00"
        summary = {"available_count": 3, "credits": None}
        machine = SimpleNamespace(name="fixture-node", ssh_host="fixture-node", is_self=False)
        with tempfile.TemporaryDirectory() as directory:
            manager = quota_sync.Manager(Path(directory) / "queries.json")
            for incoming_time, incoming_summary, expected in [(newer, summary, summary), (newer, None, None), (older, None, summary)]:
                with self.subTest(incoming_time=incoming_time, incoming_summary=incoming_summary):
                    base_time = older if incoming_time == newer else newer
                    current = SimpleNamespace(host=machine.name, meta={"source_host_identity": "fixture-source",
                        "generated_at": older, "rate_limits": {"codex": {**identity, "updated_at": base_time, "reset_credits": summary}}})
                    manager.records[machine.name] = {"ssh_host": machine.ssh_host, "error": None, "attempted_at": newer,
                        "payload": {"source_host_identity": "fixture-source", "rate_limits": {"codex": {
                            **identity, "updated_at": incoming_time, "reset_credits": incoming_summary}}}}
                    self.assertEqual(manager.merge(current, machine)["codex"]["reset_credits"], expected)


if __name__ == "__main__":
    unittest.main()
