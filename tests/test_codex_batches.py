"""Real manager/stdio with synthetic accounts; no provider credentials or network."""
import concurrent.futures
from contextlib import contextmanager
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

import codex_accounts as ca
from tests.test_codex_accounts import seed_auth

ROOT = Path(__file__).resolve().parents[1]


class BatchTests(unittest.TestCase):
    def setUp(self):
        for patch in (mock.patch.object(ca, "START_INTERVAL_SECONDS", 0),
                      mock.patch.object(ca.shutil, "which", return_value=sys.executable)):
            patch.start()
            self.addCleanup(patch.stop)
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.manager = ca.Manager(Path(temp.name) / "profiles")
        self.scenarios = {}
        real_popen = subprocess.Popen

        def spawn(command, **kwargs):
            directory = Path(kwargs["env"]["CODEX_HOME"]).parent
            record = self.manager.read(directory)
            kwargs["env"].update(FAKE_EMAIL=record["email"], FAKE_ACCOUNT=record["account_id"],
                                 FAKE_SCENARIO=self.scenarios.get(record["id"], "success"))
            return real_popen([sys.executable, str(ROOT / "tests/fixtures/codex_account_server.py")], **kwargs)

        patch = mock.patch.object(ca.subprocess, "Popen", side_effect=spawn)
        patch.start()
        self.addCleanup(patch.stop)
        self.addCleanup(self.manager.close)

    def add(self, index, scenario="success", signed_in=True):
        account = self.manager.add(f"account{index}@example.com", f"workspace-{index}")
        if signed_in:
            seed_auth(self.manager, account)
        self.scenarios[account["id"]] = scenario
        return account["id"]

    def wait(self, predicate=None):
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            data = self.manager.list()
            if predicate(data) if predicate else not any(a["busy"] for a in data["accounts"]):
                return data
            time.sleep(.015)
        self.fail("batch did not reach expected state")

    def calls(self, pid, method="turn/start"):
        path = self.manager.directory(pid) / "calls.jsonl"
        return sum(json.loads(line).get("method") == method for line in path.read_text().splitlines()) if path.exists() else 0

    def test_all_accounts_once_duplicate_requests_and_explicit_next_round(self):
        ids = [self.add(i) for i in range(3)]
        batch_id = self.manager.start_batch(None)["batch"]["id"]
        data = self.wait()
        self.assertEqual({i["operation"]["message_status"] for i in data["batch"]["items"]}, {"succeeded"})
        self.assertEqual(self.manager.start_batch(None)["batch"]["id"], batch_id)
        self.manager.retry_batch(batch_id)
        for pid in ids:
            self.assertEqual(self.calls(pid), 1)
            with self.assertRaises(ca.ActionError):
                self.manager.start(pid)
        self.manager.start_batch(batch_id)
        self.wait()
        for pid in ids:
            self.assertEqual(self.calls(pid), 2)

    def test_entire_account_and_peer_cleanup_finish_before_next_account_starts(self):
        first, second = self.add(1), self.add(2)
        cleanup_started = threading.Event()
        permit_cleanup = threading.Event()
        second_started = threading.Event()
        original = self.manager.rpc_factory
        order = []

        def factory(directory, cancelled):
            pid = self.manager.read(directory)["id"]
            order.append(pid)
            if pid == second:
                second_started.set()
            rpc = original(directory, cancelled)
            close = rpc.close
            if pid == first:
                def hold_cleanup():
                    cleanup_started.set()
                    if not permit_cleanup.wait(3):
                        raise AssertionError("test did not release peer cleanup")
                    close()
                    self.assertIsNotNone(rpc.process.poll())
                rpc.close = hold_cleanup
            return rpc

        self.manager.rpc_factory = factory
        try:
            self.manager.start_batch(None)
            self.assertTrue(cleanup_started.wait(3))
            self.assertFalse(second_started.wait(.1))
            self.assertEqual(self.calls(second, "initialize"), 0)
        finally:
            permit_cleanup.set()
        self.wait()
        self.assertEqual(order, [first, second])
        self.assertEqual([self.calls(first), self.calls(second)], [1, 1])

    def test_subset_round_retains_excluded_account_result_through_quota_refresh(self):
        first, second = self.add(1), self.add(2)
        old = self.manager.start_batch(None)["batch"]["id"]
        self.wait()
        self.manager.start_batch(old, {second})
        self.wait()
        self.manager.start(first, refresh_only=True)
        self.wait()
        record = self.manager.view(self.manager.directory(first))
        self.assertEqual(record["batch_result"]["batch_id"], old)
        self.assertEqual(record["batch_result"]["message_status"], "succeeded")
        self.assertEqual(self.calls(first), 1)
        self.assertEqual(self.calls(second), 2)

    def test_subset_preservation_failure_does_not_publish_or_send(self):
        first, second = self.add(1), self.add(2)
        old = self.manager.start_batch(None)["batch"]["id"]
        self.wait()
        original_save = self.manager.save
        def fail_preservation(directory, record):
            if record["id"] == first and record.get("batch_result", {}).get("batch_id") == old:
                raise OSError("fixture retention failure")
            return original_save(directory, record)
        with mock.patch.object(self.manager, "save", side_effect=fail_preservation):
            with self.assertRaises(OSError):
                self.manager.start_batch(old, {second})
        self.assertEqual(self.manager.read_batch()["id"], old)
        self.assertEqual(self.calls(first), 1)
        self.assertEqual(self.calls(second), 1)

    def test_first_failure_stops_remaining_accounts_and_preserves_send_result(self):
        good = self.add(1)
        partial = self.add(2, "quota_failure")
        unknown = self.add(3, "disconnect")
        failed = self.add(4, "failed_turn")
        batch_id = self.manager.start_batch(None)["batch"]["id"]
        data = self.wait()
        statuses = {item["profile_id"]: item["operation"]["message_status"] for item in data["batch"]["items"]}
        self.assertEqual(statuses, {good: "succeeded", partial: "succeeded", unknown: "not_sent", failed: "not_sent"})
        self.assertEqual([self.calls(pid) for pid in (good, partial, unknown, failed)], [1, 1, 0, 0])
        self.scenarios[partial] = "success"
        original = next(a for a in data["accounts"] if a["id"] == partial)
        self.manager.resume(partial, original["operation"]["id"], data["upstream_pause"]["id"])
        self.manager.start(partial, refresh_only=True)
        data = self.wait()
        statuses = {item["profile_id"]: item["operation"]["message_status"] for item in data["batch"]["items"]}
        self.assertEqual(statuses[partial], "succeeded")
        self.assertEqual(self.calls(partial), 1)
        self.assertNotIn("SECRET_FIXTURE_TOKEN", json.dumps(data))

    def test_missing_authorizations_are_skipped_without_device_codes(self):
        missing = [self.add(i, "deferred_login", signed_in=False) for i in range(3)]
        good = self.add(4)
        self.manager.start_batch(None)
        data = self.wait()
        for pid in missing:
            account = next(a for a in data["accounts"] if a["id"] == pid)
            self.assertEqual(account["operation"]["failure"]["category"], "needsLogin")
            self.assertEqual(self.calls(pid, "account/login/start"), 0)
            self.assertEqual(self.calls(pid, "initialize"), 0)
        self.assertEqual(self.calls(good), 1)
        self.assertIsNone(data["upstream_pause"])

    def test_explicit_login_rate_limit_pauses_other_saved_accounts(self):
        for scenario in ("login_start_rate_limit", "login_poll_rate_limit"):
            with self.subTest(scenario=scenario):
                pending = self.add(scenario, scenario, signed_in=False)
                good = self.add(scenario + "-saved")
                self.manager.start(pending, login_only=True)
                data = self.wait()
                result = next(a for a in data["accounts"] if a["id"] == pending)
                self.assertEqual(self.calls(pending, "account/login/start"), 1)
                self.assertEqual(self.calls(pending), 0)
                self.assertIsNotNone(data["upstream_pause"])
                self.manager.start(good)
                self.wait()
                self.assertEqual(self.calls(good, "initialize"), 0)
                self.manager.resume(pending, result["operation"]["id"], data["upstream_pause"]["id"])
                self.scenarios[pending] = "success"
                self.manager.start(pending, login_only=True)
                self.wait()
                self.assertEqual(self.calls(pending), 0)
                self.manager.start(good)
                self.wait()
                self.assertEqual(self.calls(good), 1)

    def test_cancel_queued_account_never_starts_peer_and_unknown_stops_successor(self):
        active = self.add(1, "wait_completion")
        queued = self.add(2)
        successor = self.add(3)
        self.manager.start_batch(None)
        self.wait(lambda data: next(a for a in data["accounts"] if a["id"] == active)["operation"]["stage"] == "sending")
        self.manager.cancel(queued)
        self.wait(lambda data: next(a for a in data["accounts"] if a["id"] == queued)["operation"]["stage"] == "cancelled")
        self.assertEqual(self.calls(queued, "initialize"), 0)
        self.manager.cancel(active)
        data = self.wait()
        self.assertEqual(self.calls(active), 1)
        self.assertEqual(self.calls(successor, "initialize"), 0)
        self.assertIsNotNone(data["upstream_pause"])

    def test_retry_only_definitely_unsent_accounts_after_separate_login(self):
        good = self.add(1)
        pending = self.add(2, signed_in=False)
        batch_id = self.manager.start_batch(None)["batch"]["id"]
        self.wait()
        self.assertEqual(self.calls(good), 1)
        self.assertEqual(self.calls(pending), 0)
        self.manager.start(pending, login_only=True)
        self.wait()
        self.assertEqual(self.calls(pending), 0)
        self.manager.retry_batch(batch_id)
        self.wait()
        self.assertEqual(self.calls(good), 1)
        self.assertEqual(self.calls(pending), 1)

    def test_restart_reads_results_without_dispatch(self):
        pid = self.add(1, "disconnect")
        batch_id = self.manager.start_batch(None)["batch"]["id"]
        self.wait()
        other = ca.Manager(self.manager.root)
        self.addCleanup(other.close)
        self.assertEqual(other.list()["batch"]["items"][0]["operation"]["message_status"], "unknown")
        other.start_batch(None)
        other.retry_batch(batch_id)
        self.assertEqual(self.calls(pid), 1)
        self.assertFalse(other.jobs)

    def test_restart_marks_waiting_authorizations_interrupted_without_replay(self):
        for index, stage in enumerate(("login_queued", "login")):
            pid = self.add(index)
            directory = self.manager.directory(pid)
            record = self.manager.read(directory)
            record["operation"] = {"stage": stage, "message_status": "not_sent"}
            self.manager.save(directory, record)
        other = ca.Manager(self.manager.root)
        self.addCleanup(other.close)
        for account in other.list()["accounts"]:
            self.assertEqual(account["operation"]["stage"], "interrupted")
            self.assertFalse(account["busy"])
            self.assertNotIn("user_code", account["operation"])
            self.assertEqual(self.calls(account["id"], "account/login/start"), 0)

    def test_concurrent_managers_create_one_batch(self):
        ids = [self.add(i) for i in range(2)]
        other = ca.Manager(self.manager.root)
        self.addCleanup(other.close)
        barrier = threading.Barrier(2)
        def start(manager):
            barrier.wait(timeout=3)
            try:
                return manager.start_batch(None)["batch"]["id"]
            except ca.ActionError as error:
                self.assertEqual(error.status, 409)
                return None
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            results = list(pool.map(start, [self.manager, other]))
        self.wait()
        batch_id = self.manager.list()["batch"]["id"]
        self.assertEqual({r for r in results if r}, {batch_id})
        for pid in ids:
            self.assertEqual(self.calls(pid), 1)

    def test_repeated_preparation_failure_preserves_published_results(self):
        ids = [self.add(i) for i in range(3)]
        batch_id = self.manager.start_batch(None)["batch"]["id"]
        self.wait()
        original = self.manager.save
        for attempt in range(2):
            writes = []
            def fail(directory, record):
                writes.append(record["id"])
                if len(writes) == 2:
                    raise OSError("fixture interrupted preparation")
                original(directory, record)
            with mock.patch.object(self.manager, "save", side_effect=fail):
                with self.assertRaises(OSError):
                    self.manager.start_batch(batch_id)
            data = self.manager.list()
            self.assertEqual(data["batch"]["id"], batch_id)
            self.assertEqual([i["operation"]["message_status"] for i in data["batch"]["items"]], ["succeeded"] * 3)
            self.assertFalse(self.manager.jobs)
            for pid in ids:
                self.assertEqual(self.calls(pid), 1)

    def test_published_queue_after_restart_requires_explicit_retry(self):
        ids = [self.add(i) for i in range(2)]
        # Model a process ending after manifest publication but before dispatch.
        with mock.patch.object(self.manager, "launch", side_effect=lambda prepared: prepared[-1].close()):
            batch_id = self.manager.start_batch(None)["batch"]["id"]
        data = self.manager.list()
        self.assertEqual([i["operation"]["stage"] for i in data["batch"]["items"]], ["interrupted"] * 2)
        for pid in ids:
            self.assertEqual(self.calls(pid), 0)
        self.manager.retry_batch(batch_id)
        self.wait()
        for pid in ids:
            self.assertEqual(self.calls(pid), 1)

    def test_new_accounts_join_next_round_not_existing_manifest(self):
        first = self.add(1)
        batch_id = self.manager.start_batch(None)["batch"]["id"]
        self.wait()
        second = self.add(2)
        self.manager.retry_batch(batch_id)
        self.assertEqual(len(self.manager.list()["batch"]["items"]), 1)
        self.assertEqual(self.calls(second), 0)
        self.manager.start_batch(batch_id)
        self.wait()
        self.assertEqual(self.calls(first), 2)
        self.assertEqual(self.calls(second), 1)

    def test_observer_rereads_after_worker_releases_lock(self):
        pid = self.add(1)
        directory = self.manager.directory(pid)
        record = self.manager.read(directory)
        record["operation"] = {"stage": "sending", "message_status": "unknown"}
        self.manager.save(directory, record)
        original = ca.lock_file
        @contextmanager
        def worker_finishes(path):
            record["operation"].update(stage="succeeded", message_status="succeeded")
            self.manager.save(directory, record)
            with original(path) as handle:
                yield handle
        with mock.patch.object(ca, "lock_file", side_effect=worker_finishes):
            observed = self.manager.view(directory)
        self.assertEqual(observed["operation"]["message_status"], "succeeded")
        self.assertEqual(self.manager.read(directory)["operation"]["stage"], "succeeded")


if __name__ == "__main__":
    unittest.main()
