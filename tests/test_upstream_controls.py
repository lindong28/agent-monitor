"""Local admission and recovery tests; no provider or credentials involved."""
import contextlib
import io
import json
import multiprocessing
from pathlib import Path
import tempfile
import threading
import unittest

from upstream_controls import Busy, Controls, Paused, main


def hold(root, ready, finish):
    lease = Controls(root).acquire("codex")
    ready.set()
    finish.wait(5)
    lease.close()


class UpstreamControlsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.controls = Controls(self.root)

    def test_same_provider_serial_different_provider_independent(self):
        lease = self.controls.acquire("codex")
        try:
            with self.assertRaises(Busy):
                Controls(self.root).acquire("codex")
            other = self.controls.acquire("claude")
            other.close()
        finally:
            lease.close()
        self.controls.acquire("codex").close()

    def test_cross_process_lock_owns_whole_operation(self):
        context = multiprocessing.get_context("fork")
        ready, finish = context.Event(), context.Event()
        child = context.Process(target=hold, args=(self.root, ready, finish))
        child.start()
        try:
            self.assertTrue(ready.wait(3))
            with self.assertRaises(Busy):
                self.controls.acquire("codex")
        finally:
            finish.set()
            child.join(5)
            if child.is_alive():
                child.kill()
                child.join()
        self.assertEqual(child.exitcode, 0)
        self.controls.acquire("codex").close()

    def test_pause_survives_new_instance_and_compare_and_swap_resume(self):
        lease = self.controls.acquire("codex")
        lease.pause("operationFailed")
        lease.close()
        restarted = Controls(self.root)
        pause = restarted.read("codex")["pause"]
        with self.assertRaises(Paused):
            restarted.acquire("codex")
        with self.assertRaises(Paused):
            restarted.resume("codex", "stale")
        restarted.resume("codex", pause["id"])
        self.assertEqual(restarted.read("codex")["last_pause"], pause)
        restarted.acquire("codex").close()

    def test_unclosed_and_corrupt_state_fail_closed(self):
        lease = self.controls.acquire("codex")
        lease.close(completed=False)
        with self.assertRaises(Paused):
            self.controls.acquire("codex")
        self.assertEqual(self.controls.read("codex")["pause"]["reason"], "interrupted")
        for payload in ("broken", '{"version": 1, "released_at": "bad"}'):
            with self.subTest(payload=payload):
                (self.root / "codex.json").write_text(payload)
                with self.assertRaises(Paused):
                    self.controls.acquire("codex")
                with self.assertRaises(Paused):
                    self.controls.resume("codex", "unreadable")

    def test_cancelled_admission_and_bounded_wait_do_not_mark_active(self):
        cancelled = threading.Event()
        cancelled.set()
        with self.assertRaises(Busy):
            self.controls.acquire("codex", cancelled=cancelled)
        self.assertNotIn("active", self.controls.read("codex"))
        lease = self.controls.acquire("codex")
        try:
            with self.assertRaises(Busy):
                self.controls.acquire("codex", timeout=.01)
        finally:
            lease.close()

    def test_cli_status_and_resume_are_local_only(self):
        lease = self.controls.acquire("claude")
        lease.pause("quotaRefreshFailed")
        lease.close()
        args = ["--state-root", str(self.root)]
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(main(["status", "claude", *args]), 0)
        self.assertIn("本机请求已暂停", output.getvalue())
        pause = self.controls.read("claude")["pause"]["id"]
        with contextlib.redirect_stdout(output):
            self.assertEqual(main(["resume", "claude", "--pause-id", pause, *args]), 0)
        self.assertIn("未发出任何上游请求", output.getvalue())
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main(["resume", "claude", "--pause-id", pause, *args]), 1)
        self.assertNotIn("active", self.controls.read("claude"))

    def test_pre_control_failed_operation_survives_new_round(self):
        import codex_accounts as ca
        manager = ca.Manager(self.root / "profiles")
        self.addCleanup(manager.close)
        account = manager.add("legacy@example.test", "legacy-workspace")
        directory = manager.directory(account["id"])
        record = manager.read(directory)
        original = {"id": "original", "stage": "failed", "message_status": "failed",
                    "failure": {"category": "unauthorized", "http_status": 401},
                    "detail": "original refusal", "refresh_only": False}
        record["operation"] = original
        record["restrictions"] = {"authentication": {"category": "unauthorized"}}
        manager.save(directory, record)
        prepared = manager.prepare(account["id"])
        try:
            self.assertEqual(prepared[1]["last_failure"], original)
            self.assertEqual(prepared[1]["operation"]["message_status"], "not_sent")
        finally:
            prepared[3].close()

    def test_cancel_before_turn_preserves_original_paused_operation(self):
        import codex_accounts as ca
        class CancelPeer:
            def initialize(self):
                raise ca.Cancelled("fixture cancelled during initialization")

            def close(self):
                pass

        manager = ca.Manager(self.root / "profiles", rpc_factory=lambda *args: CancelPeer())
        self.addCleanup(manager.close)
        account = manager.add("cancel@example.test", "cancel-workspace")
        directory = manager.directory(account["id"])
        (directory / "codex").mkdir(exist_ok=True)
        (directory / "codex/auth.json").write_text("synthetic local prerequisite")
        prepared = manager.prepare(account["id"], refresh_only=True)
        operation_id = prepared[1]["operation"]["id"]
        manager.run(*prepared)
        record = manager.read(directory)
        self.assertEqual(record["last_failure"]["id"], operation_id)
        self.assertEqual(record["last_failure"]["stage"], "cancelled")
        self.assertEqual(record["last_failure"]["message_status"], "not_sent")
        replacement = manager.prepare(account["id"], refresh_only=True)
        try:
            self.assertEqual(replacement[1]["last_failure"]["id"], operation_id)
        finally:
            replacement[3].close()


if __name__ == "__main__":
    unittest.main()
