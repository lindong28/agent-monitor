import copy
import json
import shutil
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import aggregators
import rollup


class ReviewedIdentityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.identity = self.root / "project_identity.db"
        self.rollup = self.root / "rollup.db"
        self.archive = self.root / "usage_archive.sqlite3"
        self.patch = mock.patch.object(aggregators, "PROJECT_IDENTITY_DB", self.identity)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.cache = mock.patch.object(aggregators, "_PROJECT_CACHE", {})
        self.cache.start()
        self.addCleanup(self.cache.stop)
        with sqlite3.connect(self.rollup) as conn:
            conn.executescript(rollup.SCHEMA)
            conn.execute("INSERT INTO rollup_meta VALUES ('bucket_timezone', ?)", (rollup.BUCKET_TIMEZONE_NAME,))
            conn.execute("INSERT INTO daily_rollup (date,agent_id,project,model,input_tokens) VALUES ('2026-08-12','codex','github.com/owner/repo-0','gpt-5',100)")
        with rollup.rollup_lock(self.rollup):
            pass
        with sqlite3.connect(self.archive) as conn:
            conn.execute("CREATE TABLE retained_events (id TEXT PRIMARY KEY, input_tokens INTEGER)")
            conn.execute("INSERT INTO retained_events VALUES ('unchanged-event', 100)")
        self.manifest = {"version": 1, "assignments": []}
        with sqlite3.connect(self.identity) as conn:
            conn.execute(aggregators.PROJECT_IDENTITY_SCHEMA)
            conn.execute(aggregators.PROJECT_IDENTITY_BLOCKER_SCHEMA)
            # 41 distinct paths / five projects mirror the operational batch shape.
            for i in range(41):
                source = "/deleted/worktree-%d" % i
                expected = {"reason": "source_unavailable" if i % 2 else "unreconciled_remote",
                            "resolved_candidate": None if i % 2 else "github.com/owner/repo-%d" % (i % 5),
                            "pin_candidate": None, "first_seen": "2026-08-12T00:00:00+00:00"}
                self.manifest["assignments"].append({"source_path": source, "project": "github.com/owner/repo-%d" % (i % 5), "expected_blocker": expected})
                conn.execute("INSERT INTO project_identity_blocker VALUES (?,?,?,?,?,?,?)",
                             (source, expected["reason"], expected["resolved_candidate"], None,
                              expected["first_seen"], "2026-09-28T00:00:00+00:00", "active"))

    def state_bytes(self):
        return {p.name: p.read_bytes() for p in self.root.iterdir() if p.is_file()}

    def identity_rows(self):
        with sqlite3.connect(self.identity) as conn:
            return conn.execute("SELECT * FROM project_identity ORDER BY source_path").fetchall()

    def test_preview_is_read_only_then_apply_preserves_history_and_archive(self):
        before = self.state_bytes()
        result = aggregators.recover_reviewed_project_identities(self.manifest)
        self.assertEqual(len(result), 41)
        self.assertEqual(self.state_bytes(), before)
        self.assertEqual(aggregators._PROJECT_CACHE, {})
        aggregators.recover_reviewed_project_identities(self.manifest, apply=True)
        self.assertEqual(len(self.identity_rows()), 41)
        self.assertEqual(len(aggregators._PROJECT_CACHE), 41)
        for path in (self.rollup, self.archive):
            self.assertEqual(path.read_bytes(), before[path.name])
        with sqlite3.connect(self.identity) as conn:
            self.assertEqual(conn.execute("SELECT DISTINCT status,pin_candidate FROM project_identity_blocker").fetchall(), [("resolved_pinned", None)])
            for row in conn.execute("SELECT source_path,reason,resolved_candidate,first_seen FROM project_identity_blocker"):
                expected = next(x["expected_blocker"] for x in self.manifest["assignments"] if x["source_path"] == row[0])
                self.assertEqual(row[1:], (expected["reason"], expected["resolved_candidate"], expected["first_seen"]))
        with self.assertRaisesRegex(aggregators.ProjectIdentityRecoveryError, "no active"):
            aggregators.recover_reviewed_project_identities(self.manifest, apply=True)

    def test_all_preconditions_are_checked_before_any_write(self):
        for field, value in [("reason", "changed"), ("resolved_candidate", "other"), ("pin_candidate", "other"), ("first_seen", "later")]:
            manifest = copy.deepcopy(self.manifest)
            manifest["assignments"][-1]["expected_blocker"][field] = value
            for apply in (False, True):
                with self.subTest(field=field, apply=apply), mock.patch.object(aggregators, "_upsert_project_identity", side_effect=AssertionError("write started")):
                    with self.assertRaisesRegex(aggregators.ProjectIdentityRecoveryError, field + " changed"):
                        aggregators.recover_reviewed_project_identities(manifest, apply=apply)
                self.assertEqual(self.identity_rows(), [])

    def test_existing_identity_and_inactive_or_missing_blocker_refuse_batch(self):
        source = self.manifest["assignments"][-1]["source_path"]
        for mutation in ("identity", "inactive", "missing"):
            with self.subTest(mutation=mutation):
                with sqlite3.connect(self.identity) as conn:
                    if mutation == "identity":
                        conn.execute("INSERT INTO project_identity VALUES (?, 'existing', 'legacy')", (source,))
                    elif mutation == "inactive":
                        conn.execute("DELETE FROM project_identity")
                        conn.execute("UPDATE project_identity_blocker SET status='resolved_pinned' WHERE source_path=?", (source,))
                    else:
                        conn.execute("DELETE FROM project_identity_blocker WHERE source_path=?", (source,))
                before = self.state_bytes()
                for apply in (False, True):
                    with self.assertRaises(aggregators.ProjectIdentityRecoveryError):
                        aggregators.recover_reviewed_project_identities(self.manifest, apply=apply)
                self.assertEqual(self.state_bytes(), before)

    def test_mid_batch_database_failure_rolls_back_and_keeps_cache(self):
        with sqlite3.connect(self.identity) as conn:
            conn.execute("CREATE TRIGGER reject_second BEFORE INSERT ON project_identity WHEN NEW.source_path='/deleted/worktree-1' BEGIN SELECT RAISE(ABORT, 'injected second row failure'); END")
        before = self.state_bytes()
        with self.assertRaisesRegex(aggregators.ProjectIdentityRecoveryError, "injected second row failure"):
            aggregators.recover_reviewed_project_identities(self.manifest, apply=True)
        self.assertEqual(self.state_bytes(), before)
        self.assertEqual(aggregators._PROJECT_CACHE, {})

    def test_malformed_manifests_refuse_before_lock_or_connection(self):
        bad = [None, {}, {"version": True, "assignments": []}, {"version": 2, "assignments": []}, {"version": 1, "assignments": []}]
        for mutation in ("duplicate", "missing", "relative", "wrong_type", "empty_project", "unknown_field", "missing_pin"):
            manifest = copy.deepcopy(self.manifest)
            item = manifest["assignments"][0]
            if mutation == "duplicate":
                manifest["assignments"].append(copy.deepcopy(item))
            elif mutation == "missing":
                del item["project"]
            elif mutation == "relative":
                item["source_path"] = "relative/path"
            elif mutation == "wrong_type":
                item["expected_blocker"]["first_seen"] = 1
            elif mutation == "empty_project":
                item["project"] = " "
            elif mutation == "unknown_field":
                item["apply"] = True
            else:
                del item["expected_blocker"]["pin_candidate"]
            bad.append(manifest)
        with mock.patch.object(rollup, "rollup_lock", side_effect=AssertionError("lock opened")):
            for manifest in bad:
                with self.subTest(manifest=manifest), self.assertRaises(aggregators.ProjectIdentityRecoveryError):
                    aggregators.recover_reviewed_project_identities(manifest, apply=True)

    def test_preview_does_not_create_missing_lock_or_identity_database(self):
        self.identity.unlink()
        self.rollup.with_suffix(".db.lock").unlink()
        before = self.state_bytes()
        with self.assertRaises(aggregators.ProjectIdentityRecoveryError):
            aggregators.recover_reviewed_project_identities(self.manifest)
        self.assertEqual(self.state_bytes(), before)

    def test_legacy_schema_preview_does_not_upgrade_but_apply_does(self):
        with sqlite3.connect(self.identity) as conn:
            conn.execute("ALTER TABLE project_identity_blocker DROP COLUMN pin_candidate")
        before = self.state_bytes()
        aggregators.recover_reviewed_project_identities(self.manifest)
        self.assertEqual(self.state_bytes(), before)
        aggregators.recover_reviewed_project_identities(self.manifest, apply=True)
        self.assertEqual(len(self.identity_rows()), 41)

    def test_original_strict_pin_still_refuses_null_candidate(self):
        item = self.manifest["assignments"][0]
        with mock.patch.object(aggregators, "_resolve_project", return_value=(item["project"], "remote")):
            with self.assertRaisesRegex(aggregators.ProjectIdentityRecoveryError, "no unique historical identity"):
                aggregators.pin_project_identity(item["source_path"], item["project"])
        self.assertEqual(self.identity_rows(), [])

    def test_cli_launcher_preview_apply_and_refusal(self):
        src = Path(__file__).resolve().parents[1]
        cli = self.root / "cli"
        cli.mkdir()
        for name in ("agent-monitor", "rollup_identity.py", "aggregators.py", "rollup.py"):
            shutil.copy2(src / name, cli / name)
        shutil.copytree(src / "parsers", cli / "parsers")
        (cli / "state").symlink_to(self.root, target_is_directory=True)
        manifest = self.root / "reviewed.json"
        manifest.write_text(json.dumps(self.manifest))
        command = [str(cli / "agent-monitor"), "rollup", "recover-reviewed", "--manifest", str(manifest)]
        before = self.state_bytes()
        preview = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(preview.returncode, 0, preview.stderr)
        self.assertIn("PREVIEW: 41", preview.stdout)
        self.assertIn("does not backfill all", preview.stdout)
        self.assertEqual(self.state_bytes(), before)
        applied = subprocess.run(command + ["--apply"], capture_output=True, text=True)
        self.assertEqual(applied.returncode, 0, applied.stderr)
        self.assertIn("RECOVERED: committed 41", applied.stdout)
        refused = subprocess.run(command + ["--apply"], capture_output=True, text=True)
        self.assertEqual(refused.returncode, 2)
        self.assertIn("RECOVERY REFUSED:", refused.stderr)
        self.assertEqual(refused.stdout, "")


if __name__ == "__main__":
    unittest.main()
