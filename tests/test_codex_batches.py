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

ROOT = Path(__file__).resolve().parents[1]


class BatchTests(unittest.TestCase):
    def setUp(self):
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

    def add(self, index, scenario="success"):
        account = self.manager.add(f"account{index}@example.com", f"workspace-{index}")
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

    def test_mixed_outcomes_and_quota_refresh_keep_message_results(self):
        good = self.add(1)
        partial = self.add(2, "quota_failure")
        unknown = self.add(3, "disconnect")
        failed = self.add(4, "failed_turn")
        batch_id = self.manager.start_batch(None)["batch"]["id"]
        self.wait()
        self.scenarios[partial] = "success"
        self.manager.start(partial, refresh_only=True)
        data = self.wait()
        statuses = {item["profile_id"]: item["operation"]["message_status"] for item in data["batch"]["items"]}
        self.assertEqual(statuses, {good: "succeeded", partial: "succeeded", unknown: "unknown", failed: "failed"})
        self.manager.retry_batch(batch_id)
        for pid in statuses:
            self.assertEqual(self.calls(pid), 1)
            with self.assertRaises(ca.ActionError):
                self.manager.start(pid)
        self.assertNotIn("SECRET_FIXTURE_TOKEN", json.dumps(data))

    def test_authorization_waits_release_slots_and_resume_without_extra_click(self):
        self.manager.slots = threading.BoundedSemaphore(2)
        pending = [self.add(i, "deferred_login") for i in range(3)]
        good = self.add(4)
        self.manager.start_batch(None)
        self.wait(lambda data: sum(a["operation"]["stage"] == "login" for a in data["accounts"]) == 3
                  and any(a["id"] == good and not a["busy"] for a in data["accounts"]))
        self.assertEqual(self.calls(good), 1)
        for pid in pending:
            self.assertEqual(self.calls(pid), 0)
            (self.manager.directory(pid) / "authorize").touch()
        self.wait()
        for pid in pending:
            self.assertEqual(self.calls(pid), 1)

    def test_cancel_retry_only_definitely_unsent_accounts(self):
        good = self.add(1)
        pending = self.add(2, "wait_login")
        batch_id = self.manager.start_batch(None)["batch"]["id"]
        self.wait(lambda data: any(a["id"] == pending and a["operation"]["stage"] == "login" for a in data["accounts"]))
        self.manager.cancel(pending)
        self.wait()
        self.scenarios[pending] = "success"
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
