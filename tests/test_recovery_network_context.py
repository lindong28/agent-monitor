"""Recovery stays attached to its quota task; synthetic accounts only."""
import copy
import unittest
from unittest import mock
import test_codex_accounts
from agent_monitor.accounts import codex_accounts as ca


class RecoveryContextTests(unittest.TestCase):
    setUp = test_codex_accounts.AccountActionsTests.setUp
    account = test_codex_accounts.AccountActionsTests.account

    def test_batch_retains_each_operations_network_when_later_batch_replaces_it(self):
        row = self.account()
        route = mock.Mock(id="a" * 32, label="device A", approval_context={
            "generation": 2, "targets": [{"host": "chatgpt.com", "ip": "1.1.1.1", "country_code": "US", "observed_at": 1}],
            "routes": {"https://chatgpt.com": "PROXY 127.0.0.1:1234"}})
        with mock.patch.object(self.manager, "launch_sequence", side_effect=lambda items: [item[3].close() for item in items]):
            self.manager.start_refresh_batch([row["id"]], network_route=route)
        operation = self.manager.read(self.manager.directory(row["id"]))["operation"]
        self.assertEqual(operation["network_context"]["device"], {"id": route.id, "label": route.label})
        self.assertEqual(operation["network_context"]["preview"]["generation"], 2)
        self.assertNotIn("password", str(operation["network_context"]))
        original = copy.deepcopy(operation["network_context"])
        ca.write_record(self.manager.root / "quota-batch.json", {"id": "later", "profile_ids": []})
        self.assertEqual(self.manager.recovery_network_context(row["id"])["device"], original["device"])

    def test_legacy_operation_reads_matching_batch_but_never_guesses_other_batch(self):
        row = self.account()
        directory = self.manager.directory(row["id"])
        record = self.manager.read(directory)
        record["operation"] = {"id": "original", "quota_batch_id": "first", "stage": "failed", "refresh_only": True}
        self.manager.save(directory, record)
        device = {"id": "b" * 32, "label": "legacy device"}
        ca.write_record(self.manager.root / "quota-batch.json", {"id": "first", "profile_ids": [row["id"]], "network_device": device})
        self.assertEqual(self.manager.recovery_network_context(row["id"])["device"], device)
        ca.write_record(self.manager.root / "quota-batch.json", {"id": "different", "profile_ids": [row["id"]], "network_device": device})
        context = self.manager.recovery_network_context(row["id"])
        self.assertIsNone(context["device"])
        self.assertEqual(context["operation_id"], "original")


    def test_explicit_new_connection_is_cas_bound_without_clearing_pause(self):
        from agent_monitor.accounts import account_holds
        row = self.account()
        directory = self.manager.directory(row["id"])
        record = self.manager.read(directory)
        operation_id = "11111111-1111-4111-8111-111111111111"
        record["operation"] = {"id": operation_id, "stage": "failed", "refresh_only": True,
                               "login_only": False, "message_status": "not_sent", "quota_status": "not_read"}
        self.manager.save(directory, record)
        controls = account_holds.controls(self.manager.root.parent, row["account_id"], row["email"])
        lease = controls.acquire("codex")
        lease.pause("quotaAuthenticationFailed")
        lease.close()
        pause = controls.read("codex")["pause"]
        context = self.manager.recovery_network_context(row["id"])
        route = mock.Mock(id="c" * 32, label="explicit device", approval_context={"generation": 3, "routes": {}})
        for wrong in ({**context, "operation_id": "stale"}, {**context, "device": {"id": "wrong"}}):
            with self.assertRaises(ca.ActionError):
                self.manager.bind_recovery_network(row["id"], operation_id, pause["id"], wrong, route, pause_scope="account")
        chosen = self.manager.bind_recovery_network(row["id"], operation_id, pause["id"], context, route, pause_scope="account")
        self.assertEqual(chosen["device"]["id"], route.id)
        self.assertEqual(self.manager.recovery_network_context(row["id"]), chosen)
        self.assertEqual(controls.read("codex")["pause"], pause)
        self.assertEqual(self.spawned, [])
        with self.assertRaises(ca.ActionError):
            self.manager.bind_recovery_network(row["id"], operation_id, pause["id"], context, route, pause_scope="account")


if __name__ == "__main__":
    unittest.main()
