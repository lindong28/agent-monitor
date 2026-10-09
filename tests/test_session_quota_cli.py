import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from parsers import codex

ROOT = Path(__file__).resolve().parents[1]
SID = "11111111-1111-4111-8111-111111111111"
OTHER = "22222222-2222-4222-8222-222222222222"


class SessionQuotaTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.sessions = self.home / ".codex/sessions"
        self.sessions.mkdir(parents=True)
        self.path = self.sessions / ("rollout-2026-01-01-" + SID + ".jsonl")
        self.write(self.path, SID, 4)
        self.write(self.sessions / ("rollout-2026-01-02-" + OTHER + ".jsonl"), OTHER, 100)

    def write(self, path, ident, percentage):
        rows = [{"type": "session_meta", "payload": {"id": ident}}]
        if percentage is not None:
            rows.append({"type": "event_msg", "timestamp": "2026-01-01T00:00:00Z",
                         "payload": {"type": "token_count", "rate_limits": {"primary": {
                             "window_minutes": 10080, "used_percent": percentage, "resets_at": 1}}}})
        path.write_text("".join(json.dumps(row) + "\n" for row in rows))

    def query(self, ident=SID, *args):
        # Only synthetic rollouts are reachable; no provider credentials or live HOME.
        env = {"HOME": str(self.home), "PATH": "/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1",
               "AGENT_MONITOR_PYTHON": sys.executable}
        return subprocess.run(["/bin/bash", str(ROOT / "agent-monitor"), "session-quota", ident, *args],
                              cwd=self.home, env=env, text=True, capture_output=True, timeout=10)

    def test_public_reader_targets_full_id_and_preserves_elapsed_observation(self):
        result = codex.load_session_rate_limits(SID, self.sessions)
        self.assertEqual(result["seven_day_pct"], 4)
        self.assertEqual(result["session_id"], SID)
        self.assertEqual(result["seven_day_resets_at"], 1)
        self.assertEqual(result["updated_at"], "2026-01-01T00:00:00Z")
        self.assertEqual(result["account_identity"], "unknown")
        # The legacy global reader keeps its existing expiry semantics.
        self.assertEqual(codex._extract_latest_rate_limits(self.path, {}).seven_day_pct, 0)

    def test_cli_json_and_human_disclose_stored_scope(self):
        result = self.query(SID, "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["seven_day_pct"], 4)
        human = self.query()
        self.assertEqual(human.returncode, 0, human.stderr)
        self.assertIn("current quota and account identity are unverified", human.stdout)

    def test_partial_id_is_rejected(self):
        self.assertEqual(self.query(SID[:8]).returncode, 2)

    def test_missing_rollout_does_not_substitute_other_session(self):
        self.path.unlink()
        result = self.query(SID, "--json")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(json.loads(result.stdout)["status"], "no_rollout")

    def test_no_weekly_reading_is_not_zero(self):
        self.write(self.path, SID, None)
        result = self.query(SID, "--json")
        self.assertEqual(result.returncode, 1)
        self.assertIsNone(json.loads(result.stdout)["seven_day_pct"])

    def test_mismatched_metadata_is_rejected(self):
        self.write(self.path, OTHER, 4)
        self.assertEqual(self.query().returncode, 2)

    def test_duplicate_rollouts_are_rejected(self):
        self.write(self.sessions / ("rollout-copy-" + SID + ".jsonl"), SID, 4)
        self.assertEqual(self.query().returncode, 2)

    def test_missing_session_directory_is_unverified(self):
        with self.assertRaises(ValueError):
            codex.load_session_rate_limits(SID, self.home / "missing")


if __name__ == "__main__":
    unittest.main()
