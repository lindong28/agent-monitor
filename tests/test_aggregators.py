import contextlib
import multiprocessing
import os
import shutil
import sqlite3
import subprocess
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import agent_monitor.aggregations as aggregators

from agent_monitor.ingestion import IDENTITY_STORE, project_identity
from agent_monitor.aggregations import extract_dim, pivot
from agent_monitor.ingestion.project_identity import normalize_remote
identify_project = IDENTITY_STORE.identify_project
from parsers import UsageEntry


def _project_identity_race_worker(identity_db, project, resolution, ready, start, result):
    IDENTITY_STORE.path = Path(identity_db)
    original_resolve = IDENTITY_STORE.resolve_project

    def proposed_resolution(_path):
        return project, resolution

    IDENTITY_STORE.resolve_project = proposed_resolution
    try:
        ready.put(project)
        if not start.wait(10):
            raise TimeoutError("parent did not release project identity race")
        result.put(("ok", IDENTITY_STORE.identify_project("/race/repo", {})))
    except Exception as exc:
        result.put(("error", "%s: %s" % (type(exc).__name__, exc)))
    finally:
        IDENTITY_STORE.resolve_project = original_resolve


class AggregatorTests(unittest.TestCase):
    def setUp(self):
        base = datetime(2026, 5, 19, 8, 0, tzinfo=timezone.utc)
        self.entries = [
            self.entry(base, "s1", "repo-a", "claude-opus-4-7", "claude-code", 1.0, 100, 20, 5, 10, 1),
            self.entry(base + timedelta(hours=2), "s2", "repo-b", "gpt-5", "codex", 2.0, 50, 30, 7, 0, 3),
            self.entry(base + timedelta(days=1), "s3", "repo-a", "claude-sonnet-4-6", "claude-code", 3.0, 70, 10, 0, 4, 2),
            self.entry(base + timedelta(days=8), "s4", "repo-c", "fake-model-xyz", "claude-code", None, 10, 1, 0, 0, 1),
        ]

    def test_pivot_day_group_none_cost(self):
        result = pivot(self.entries, "day", "none", "cost")
        self.assertEqual(result["columns"], ["value"])
        self.assertEqual(result["rows"][0]["values"]["value"], 3.0)

    def test_pivot_day_group_project_input(self):
        result = pivot(self.entries, "day", "project", "input")
        row = result["rows"][0]
        self.assertEqual(set(result["columns"]), {"repo-a", "repo-b", "repo-c"})
        self.assertEqual(row["values"]["repo-a"], 100)
        self.assertEqual(row["values"]["repo-b"], 50)

    def test_pivot_project_group_model_output(self):
        result = pivot(self.entries, "project", "model", "output")
        repo_a = next(row for row in result["rows"] if row["x"] == "repo-a")
        self.assertEqual(repo_a["values"]["claude-opus-4-7"], 20)
        self.assertEqual(repo_a["values"]["claude-sonnet-4-6"], 10)

    def test_pivot_model_group_none_cache_read(self):
        result = pivot(self.entries, "model", "none", "cache_read")
        row = next(row for row in result["rows"] if row["x"] == "gpt-5")
        self.assertEqual(row["values"]["value"], 7)

    def test_pivot_agent_group_project_cache_creation(self):
        result = pivot(self.entries, "agent", "project", "cache_creation")
        claude = next(row for row in result["rows"] if row["x"] == "claude-code")
        self.assertEqual(claude["values"]["repo-a"], 14)

    def test_pivot_metric_total_and_messages(self):
        total = pivot(self.entries, "project", "none", "total")
        messages = pivot(self.entries, "project", "none", "messages")
        repo_a_total = next(row for row in total["rows"] if row["x"] == "repo-a")
        repo_a_messages = next(row for row in messages["rows"] if row["x"] == "repo-a")
        self.assertEqual(repo_a_total["values"]["value"], 219)
        self.assertEqual(repo_a_messages["values"]["value"], 3)

    def test_filters_and_time_range_are_applied_before_pivot(self):
        start = self.entries[0].timestamp
        end = start + timedelta(days=2)
        result = pivot(
            self.entries,
            "day",
            "agent",
            "cost",
            agents={"claude-code"},
            projects={"repo-a"},
            time_range=(start, end),
        )
        self.assertEqual(len(result["rows"]), 2)
        self.assertEqual(result["columns"], ["claude-code"])

    def test_extract_dim_uses_fixed_shanghai_day_and_month_boundaries(self):
        with self.system_timezone("America/Los_Angeles"):
            ts = datetime(2026, 4, 30, 16, 30, tzinfo=timezone.utc)
            entry = self.entry(ts, "s5", "repo-z", "gpt-5", "codex", 1.0, 1, 1, 0, 0, 1)
            self.assertEqual(extract_dim(entry, "day"), "2026-05-01")
            self.assertEqual(extract_dim(entry, "month"), "2026-05")

    def test_identify_project_prefers_normalized_remote(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = {}
            identity_db = Path(tmp) / "project-identity.db"
            completed = mock.Mock(returncode=0, stdout="git@github.com:owner/repo.git\n")
            with mock.patch.object(IDENTITY_STORE, "path", identity_db), mock.patch(
                "subprocess.run", return_value=completed
            ):
                self.assertEqual(identify_project("/tmp/repo", cache), "github.com/owner/repo")
            self.assertEqual(cache["/tmp/repo"], "github.com/owner/repo")

    def test_identify_project_reuses_persistent_value_for_all_resolution_failures(self):
        failures = {
            "nonzero": mock.Mock(returncode=1, stdout="", stderr="not available"),
            "empty": mock.Mock(returncode=0, stdout="", stderr=""),
            "timeout": subprocess.TimeoutExpired(cmd=["git"], timeout=2),
            "missing-git": FileNotFoundError("git"),
        }
        for name, failure in failures.items():
            with self.subTest(failure=name), tempfile.TemporaryDirectory() as tmp:
                identity_db = Path(tmp) / "project-identity.db"
                with mock.patch.object(IDENTITY_STORE, "path", identity_db, create=True):
                    success = mock.Mock(returncode=0, stdout="git@github.com:owner/repo.git\n", stderr="")
                    with mock.patch("agent_monitor.ingestion.project_identity.subprocess.run", return_value=success):
                        self.assertEqual(identify_project("/tmp/repo", {}), "github.com/owner/repo")

                    patch_args = (
                        {"side_effect": failure}
                        if isinstance(failure, Exception)
                        else {"return_value": failure}
                    )
                    with mock.patch("agent_monitor.ingestion.project_identity.subprocess.run", **patch_args):
                        self.assertEqual(identify_project("/tmp/repo", {}), "github.com/owner/repo")

    def test_identify_project_persists_and_warns_on_first_resolution_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            identity_db = Path(tmp) / "project-identity.db"
            with mock.patch.object(IDENTITY_STORE, "path", identity_db, create=True):
                with mock.patch(
                    "agent_monitor.ingestion.project_identity.subprocess.run",
                    side_effect=subprocess.TimeoutExpired(cmd=["git"], timeout=2),
                ), self.assertLogs("agent_monitor.ingestion.project_identity", level="WARNING"):
                    self.assertEqual(identify_project("/tmp/repo", {}), "/tmp/repo")

                success = mock.Mock(returncode=0, stdout="git@github.com:owner/repo.git\n", stderr="")
                with mock.patch("agent_monitor.ingestion.project_identity.subprocess.run", return_value=success):
                    self.assertEqual(identify_project("/tmp/repo", {}), "/tmp/repo")
                conn = sqlite3.connect(identity_db)
                try:
                    stored = conn.execute(
                        "SELECT project, resolution FROM project_identity WHERE source_path = '/tmp/repo'"
                    ).fetchone()
                finally:
                    conn.close()
                self.assertEqual(stored, ("/tmp/repo", "fallback"))

    def test_identify_project_defers_ambiguous_first_failure_when_rollup_has_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "temporarily-unresolved"
            source.mkdir()
            identity_db = root / "project-identity.db"
            self.seed_rollup_project(root / "rollup.db", "github.com/owner/repo")
            with mock.patch.object(IDENTITY_STORE, "path", identity_db), mock.patch(
                "agent_monitor.ingestion.project_identity.subprocess.run",
                side_effect=subprocess.TimeoutExpired(cmd=["git"], timeout=2),
            ):
                with self.assertRaises(project_identity.ProjectIdentityUnavailable):
                    identify_project(str(source), {})

    def test_identify_project_allows_existing_new_path_without_remote_amid_unrelated_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "new-project"
            source.mkdir()
            identity_db = root / "project-identity.db"
            self.seed_rollup_project(root / "rollup.db", "github.com/owner/existing")
            with mock.patch.object(IDENTITY_STORE, "path", identity_db):
                self.assertEqual(identify_project(str(source), {}), str(source))

    def test_identify_project_blocks_unreconciled_remote_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "repo"
            source.mkdir()
            identity_db = root / "project-identity.db"
            self.seed_rollup_project(root / "rollup.db", "github.com/owner/old-name")
            changed = mock.Mock(
                returncode=0,
                stdout="git@github.com:owner/new-name.git\n",
                stderr="",
            )
            with mock.patch.object(IDENTITY_STORE, "path", identity_db), mock.patch(
                "agent_monitor.ingestion.project_identity.subprocess.run", return_value=changed
            ):
                with self.assertRaises(project_identity.ProjectIdentityUnavailable):
                    identify_project(str(source), {})

    def test_identify_project_bootstraps_existing_raw_path_before_remote(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            identity_db = root / "project-identity.db"
            self.seed_rollup_project(root / "rollup.db", "/tmp/repo")
            success = mock.Mock(returncode=0, stdout="git@github.com:owner/repo.git\n", stderr="")
            with mock.patch.object(IDENTITY_STORE, "path", identity_db), mock.patch(
                "agent_monitor.ingestion.project_identity.subprocess.run", return_value=success
            ):
                self.assertEqual(identify_project("/tmp/repo", {}), "/tmp/repo")

    def test_identify_project_does_not_bootstrap_from_unmarked_rollup_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            identity_db = root / "project-identity.db"
            self.seed_rollup_project(
                root / "rollup.db",
                "/tmp/repo",
                bucket_timezone=None,
            )
            success = mock.Mock(
                returncode=0,
                stdout="git@github.com:owner/repo.git\n",
                stderr="",
            )

            with mock.patch.object(
                IDENTITY_STORE, "path", identity_db
            ), mock.patch("agent_monitor.ingestion.project_identity.subprocess.run", return_value=success):
                self.assertEqual(
                    identify_project("/tmp/repo", {}),
                    "github.com/owner/repo",
                )

            with contextlib.closing(sqlite3.connect(identity_db)) as conn:
                stored = conn.execute(
                    "SELECT project, resolution FROM project_identity "
                    "WHERE source_path = '/tmp/repo'"
                ).fetchone()
            self.assertEqual(stored, ("github.com/owner/repo", "remote"))

    def test_identify_project_first_writer_is_stable_across_processes(self):
        with tempfile.TemporaryDirectory() as tmp:
            identity_db = Path(tmp) / "project-identity.db"
            ctx = multiprocessing.get_context("spawn")
            ready = ctx.Queue()
            start = ctx.Event()
            result = ctx.Queue()
            processes = [
                ctx.Process(
                    target=_project_identity_race_worker,
                    args=(str(identity_db), project, resolution, ready, start, result),
                )
                for project, resolution in (
                    ("/race/repo", "fallback"),
                    ("github.com/owner/repo", "remote"),
                )
            ]
            try:
                for process in processes:
                    process.start()
                self.assertEqual({ready.get(timeout=10) for _ in processes}, {
                    "/race/repo",
                    "github.com/owner/repo",
                })
                start.set()
                observed = [result.get(timeout=15) for _ in processes]
            finally:
                start.set()
                for process in processes:
                    if process.pid is not None:
                        process.join(5)
                    if process.is_alive():
                        process.terminate()
                        process.join(5)
                result.close()
                result.join_thread()
                ready.close()
                ready.join_thread()

            self.assertTrue(all(process.exitcode == 0 for process in processes))
            self.assertEqual({status for status, _value in observed}, {"ok"})
            self.assertEqual(len({value for _status, value in observed}), 1)
            conn = sqlite3.connect(identity_db)
            try:
                rows = conn.execute(
                    "SELECT source_path, project, resolution FROM project_identity"
                ).fetchall()
            finally:
                conn.close()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0][0], "/race/repo")
            self.assertIn(rows[0][1:], {("/race/repo", "fallback"), ("github.com/owner/repo", "remote")})

    def test_identify_project_rejects_conflicting_legacy_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            identity_db = root / "project-identity.db"
            self.seed_rollup_project(root / "rollup.db", "/tmp/repo")
            self.seed_rollup_project(root / "rollup.db", "github.com/owner/repo")
            success = mock.Mock(returncode=0, stdout="git@github.com:owner/repo.git\n", stderr="")
            with mock.patch.object(IDENTITY_STORE, "path", identity_db), mock.patch(
                "agent_monitor.ingestion.project_identity.subprocess.run", return_value=success
            ):
                with self.assertRaises(project_identity.ProjectIdentityConflict):
                    identify_project("/tmp/repo", {})

    def test_pin_project_identity_resolves_only_to_existing_rollup_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            identity_db = root / "project_identity.db"
            source = str(root / "repo")
            old_project = "github.com/owner/old-name"
            new_project = "github.com/owner/new-name"
            self.seed_recovery_rollup(root / "rollup.db", old_project, 100)

            with mock.patch.object(IDENTITY_STORE, "path", identity_db):
                IDENTITY_STORE.record_blocker(
                    project_identity.ProjectIdentityUnavailable(
                        "remote changed",
                        source_path=source,
                        reason="unreconciled_remote",
                        resolved_project=new_project,
                        pin_candidate=old_project,
                    )
                )
                current_remote = mock.Mock(
                    returncode=0,
                    stdout="git@github.com:owner/new-name.git\n",
                    stderr="",
                )
                with mock.patch("agent_monitor.ingestion.project_identity.subprocess.run", return_value=current_remote):
                    with self.assertRaises(project_identity.ProjectIdentityRecoveryError):
                        IDENTITY_STORE.pin(source, "github.com/owner/missing")

                restored_remote = mock.Mock(
                    returncode=0,
                    stdout="git@github.com:owner/old-name.git\n",
                    stderr="",
                )
                with mock.patch("agent_monitor.ingestion.project_identity.subprocess.run", return_value=restored_remote):
                    recovered = IDENTITY_STORE.pin(source, old_project)
                changed = mock.Mock(
                    returncode=0,
                    stdout="git@github.com:owner/new-name.git\n",
                    stderr="",
                )
                with mock.patch("agent_monitor.ingestion.project_identity.subprocess.run", return_value=changed):
                    self.assertEqual(identify_project(source, {}), old_project)

                self.assertEqual(recovered["status"], "resolved_pinned")
                self.assertEqual(IDENTITY_STORE.list_blockers(), [])
                all_blockers = IDENTITY_STORE.list_blockers(status=None)
                self.assertEqual(all_blockers[0]["status"], "resolved_pinned")

    def test_pin_project_identity_refuses_unrelated_existing_project_and_keeps_blocker_active(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            identity_db = root / "project_identity.db"
            source = str(root / "repo")
            old_project = "github.com/owner/old-name"
            new_project = "github.com/owner/new-name"
            unrelated_project = "github.com/other/unrelated"
            self.seed_recovery_rollup(root / "rollup.db", old_project, 100)
            self.seed_recovery_rollup(root / "rollup.db", unrelated_project, 20)
            restored = mock.Mock(
                returncode=0,
                stdout="git@github.com:owner/old-name.git\n",
                stderr="",
            )

            with mock.patch.object(IDENTITY_STORE, "path", identity_db), mock.patch(
                "agent_monitor.ingestion.project_identity.subprocess.run", return_value=restored
            ):
                IDENTITY_STORE.record_blocker(
                    project_identity.ProjectIdentityUnavailable(
                        "remote changed",
                        source_path=source,
                        reason="unreconciled_remote",
                        resolved_project=new_project,
                        pin_candidate=old_project,
                    )
                )
                with self.assertRaises(project_identity.ProjectIdentityRecoveryError):
                    IDENTITY_STORE.pin(source, unrelated_project)

                self.assertIsNone(IDENTITY_STORE.stored_identity(source))
                active = IDENTITY_STORE.list_blockers()
                self.assertEqual(len(active), 1)
                self.assertEqual(active[0]["source_path"], source)
                self.assertEqual(active[0]["status"], "active")

                self.seed_recovery_rollup(root / "rollup.db", new_project, 30)
                unresolved = mock.Mock(
                    returncode=0,
                    stdout="git@github.com:owner/new-name.git\n",
                    stderr="",
                )
                with mock.patch("agent_monitor.ingestion.project_identity.subprocess.run", return_value=unresolved):
                    with self.assertRaises(project_identity.ProjectIdentityRecoveryError):
                        IDENTITY_STORE.pin(source, new_project)
                self.assertIsNone(IDENTITY_STORE.stored_identity(source))
                self.assertEqual(
                    IDENTITY_STORE.list_blockers()[0]["status"],
                    "active",
                )
                with mock.patch("agent_monitor.ingestion.project_identity.subprocess.run", return_value=restored):
                    recovered = IDENTITY_STORE.pin(source, old_project)
                self.assertEqual(recovered["status"], "resolved_pinned")

    def test_legacy_persisted_blocker_without_pin_candidate_stays_active_and_refuses_pin(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            identity_db = root / "project_identity.db"
            source = str(root / "repo")
            old_project = "github.com/owner/old-name"
            self.seed_recovery_rollup(root / "rollup.db", old_project, 100)
            with sqlite3.connect(identity_db) as conn:
                conn.execute(
                    """
                    CREATE TABLE project_identity_blocker (
                      source_path TEXT PRIMARY KEY,
                      reason TEXT NOT NULL,
                      resolved_candidate TEXT,
                      first_seen TEXT NOT NULL,
                      last_seen TEXT NOT NULL,
                      status TEXT NOT NULL
                    )
                    """
                )
                conn.execute(
                    """
                    INSERT INTO project_identity_blocker
                      (source_path, reason, resolved_candidate, first_seen, last_seen, status)
                    VALUES (?, 'unreconciled_remote', 'github.com/owner/new-name',
                            '2026-08-03T00:00:00+00:00', '2026-08-03T00:00:00+00:00', 'active')
                    """,
                    (source,),
                )

            restored = mock.Mock(
                returncode=0,
                stdout="git@github.com:owner/old-name.git\n",
                stderr="",
            )
            with mock.patch.object(IDENTITY_STORE, "path", identity_db), mock.patch(
                "agent_monitor.ingestion.project_identity.subprocess.run", return_value=restored
            ):
                active = IDENTITY_STORE.list_blockers()
                self.assertIsNone(active[0]["pin_candidate"])
                with self.assertRaises(project_identity.ProjectIdentityRecoveryError):
                    IDENTITY_STORE.pin(source, old_project)
                self.assertIsNone(IDENTITY_STORE.stored_identity(source))
                self.assertEqual(
                    IDENTITY_STORE.list_blockers()[0]["status"],
                    "active",
                )

    def test_rollup_blockers_cli_exposes_only_strict_pin_recovery(self):
        source_root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            cli_root = Path(tmp) / "agent-monitor"
            cli_root.mkdir()
            for name in ("agent-monitor",):
                shutil.copy2(source_root / name, cli_root / name)
            shutil.copytree(source_root / "parsers", cli_root / "parsers")
            shutil.copytree(source_root / "agent_monitor", cli_root / "agent_monitor")
            shutil.copy2(source_root / "project_paths.py", cli_root / "project_paths.py")
            source_path = cli_root / "repo"
            source = str(source_path)
            candidate = "github.com/owner/new-name"
            existing = "github.com/owner/existing"
            (cli_root / "state").mkdir()
            self.seed_recovery_rollup(cli_root / "state" / "rollup.db", existing, 30)
            source_path.mkdir()
            subprocess.run(["git", "init", "-q", str(source_path)], check=True)
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(source_path),
                    "remote",
                    "add",
                    "origin",
                    "git@github.com:owner/existing.git",
                ],
                check=True,
            )
            with mock.patch.object(
                IDENTITY_STORE,
                "path",
                cli_root / "state" / "project_identity.db",
            ):
                IDENTITY_STORE.record_blocker(
                    project_identity.ProjectIdentityUnavailable(
                        "remote changed",
                        source_path=source,
                        reason="unreconciled_remote",
                        resolved_project=candidate,
                        pin_candidate=existing,
                    )
                )

            result = subprocess.run(
                [str(cli_root / "agent-monitor"), "rollup", "blockers"],
                capture_output=True,
                text=True,
                check=False,
            )
            output = result.stdout + result.stderr
            self.assertEqual(result.returncode, 1)
            self.assertIn("ACTIVE project identity blocker", output)
            self.assertIn(source, output)
            self.assertIn("unreconciled_remote", output)
            self.assertIn(candidate, output)
            self.assertIn("Available recovery command:", output)
            self.assertIn(
                "--pin-existing %s" % existing,
                output,
            )
            self.assertIn("--pin-existing", output)
            self.assertNotIn("--migrate", output)
            self.assertNotIn("--source-coverage-complete", output)

            pinned = subprocess.run(
                [
                    str(cli_root / "agent-monitor"),
                    "rollup",
                    "recover",
                    "--path",
                    source,
                    "--pin-existing",
                    existing,
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(pinned.returncode, 0, pinned.stderr)
            self.assertIn("status=resolved_pinned", pinned.stdout)

            help_result = subprocess.run(
                [str(cli_root / "agent-monitor"), "rollup", "recover", "--help"],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(help_result.returncode, 0)
            self.assertNotIn("--migrate", help_result.stdout)
            self.assertNotIn("--source-coverage-complete", help_result.stdout)

    def test_rollup_recovery_cli_refuses_unmarked_history_and_keeps_blocker_active(self):
        source_root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            cli_root = Path(tmp) / "agent-monitor"
            cli_root.mkdir()
            for name in ("agent-monitor",):
                shutil.copy2(source_root / name, cli_root / name)
            shutil.copytree(source_root / "parsers", cli_root / "parsers")
            shutil.copytree(source_root / "agent_monitor", cli_root / "agent_monitor")
            shutil.copy2(source_root / "project_paths.py", cli_root / "project_paths.py")
            state = cli_root / "state"
            state.mkdir()
            identity_db = state / "project_identity.db"
            source_path = cli_root / "repo"
            source = str(source_path)
            existing = "github.com/owner/existing"
            self.seed_recovery_rollup(
                state / "rollup.db",
                existing,
                30,
                bucket_timezone=None,
            )
            source_path.mkdir()
            subprocess.run(["git", "init", "-q", str(source_path)], check=True)
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(source_path),
                    "remote",
                    "add",
                    "origin",
                    "git@github.com:owner/existing.git",
                ],
                check=True,
            )
            with mock.patch.object(IDENTITY_STORE, "path", identity_db):
                IDENTITY_STORE.record_blocker(
                    project_identity.ProjectIdentityUnavailable(
                        "remote changed",
                        source_path=source,
                        reason="unreconciled_remote",
                        resolved_project="github.com/owner/new-name",
                        pin_candidate=existing,
                    )
                )

            recovered = subprocess.run(
                [
                    str(cli_root / "agent-monitor"),
                    "rollup",
                    "recover",
                    "--path",
                    source,
                    "--pin-existing",
                    existing,
                ],
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(recovered.returncode, 2)
            self.assertIn("RECOVERY REFUSED", recovered.stderr)
            self.assertIn("bucket_timezone", recovered.stderr)
            self.assertIn("explicit rollup adoption", recovered.stderr)
            self.assertIn("identity and blocker state were not changed", recovered.stderr)
            with mock.patch.object(IDENTITY_STORE, "path", identity_db):
                self.assertIsNone(IDENTITY_STORE.stored_identity(source))
                blockers = IDENTITY_STORE.list_blockers()
            self.assertEqual(len(blockers), 1)
            self.assertEqual(blockers[0]["status"], "active")

    def test_rollup_blockers_cli_without_pin_candidate_prints_no_failing_command(self):
        source_root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            cli_root = Path(tmp) / "agent-monitor"
            cli_root.mkdir()
            for name in ("agent-monitor",):
                shutil.copy2(source_root / name, cli_root / name)
            shutil.copytree(source_root / "parsers", cli_root / "parsers")
            shutil.copytree(source_root / "agent_monitor", cli_root / "agent_monitor")
            shutil.copy2(source_root / "project_paths.py", cli_root / "project_paths.py")
            state = cli_root / "state"
            state.mkdir()
            with sqlite3.connect(state / "project_identity.db") as conn:
                conn.execute(
                    """
                    CREATE TABLE project_identity_blocker (
                      source_path TEXT PRIMARY KEY,
                      reason TEXT NOT NULL,
                      resolved_candidate TEXT,
                      first_seen TEXT NOT NULL,
                      last_seen TEXT NOT NULL,
                      status TEXT NOT NULL
                    )
                    """
                )
                conn.execute(
                    """
                    INSERT INTO project_identity_blocker
                      (source_path, reason, resolved_candidate, first_seen, last_seen, status)
                    VALUES ('/work/legacy-repo', 'unreconciled_remote',
                            'github.com/owner/new-name', '2026-08-03T00:00:00+00:00',
                            '2026-08-03T00:00:00+00:00', 'active')
                    """
                )

            result = subprocess.run(
                [str(cli_root / "agent-monitor"), "rollup", "blockers"],
                capture_output=True,
                text=True,
                check=False,
            )
            output = result.stdout + result.stderr
            self.assertEqual(result.returncode, 1)
            self.assertIn("pin_candidate: unavailable", output)
            self.assertIn("Automatic pinning is not possible", output)
            self.assertIn("explicitly reviewed assignment manifest", output)
            self.assertIn("recover-reviewed --manifest", output)
            self.assertIn("archived usage is retained", output)
            self.assertIn(
                "Migrating historical project keys is not supported by this tooling.",
                output,
            )
            self.assertNotIn("--pin-existing", output)

    def test_normalize_remote_handles_https_and_ssh(self):
        self.assertEqual(normalize_remote("https://github.com/owner/repo.git"), "github.com/owner/repo")
        self.assertEqual(normalize_remote("git@github.com:owner/repo.git"), "github.com/owner/repo")

    @staticmethod
    def entry(ts, sid, project, model, agent, cost, input_tokens, output_tokens, cache_read, cache_creation, messages):
        return UsageEntry(
            timestamp=ts,
            session_id=sid,
            message_id=f"{sid}-msg",
            request_id=f"{sid}-req",
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_creation_tokens=cache_creation,
            cache_read_tokens=cache_read,
            cost_usd=cost,
            project=project,
            agent_id=agent,
            message_count=messages,
        )

    @staticmethod
    @contextlib.contextmanager
    def system_timezone(name):
        previous = os.environ.get("TZ")
        try:
            os.environ["TZ"] = name
            time.tzset()
            yield
        finally:
            if previous is None:
                os.environ.pop("TZ", None)
            else:
                os.environ["TZ"] = previous
            time.tzset()

    @staticmethod
    def seed_rollup_project(db_path, project, bucket_timezone="Asia/Shanghai"):
        conn = sqlite3.connect(db_path)
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS daily_rollup (
                  date TEXT NOT NULL,
                  agent_id TEXT NOT NULL,
                  project TEXT NOT NULL,
                  model TEXT NOT NULL,
                  input_tokens INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            conn.execute(
                """
                INSERT INTO daily_rollup (date, agent_id, project, model, input_tokens)
                VALUES ('2026-06-13', 'codex', ?, 'gpt-5', 100)
                """,
                (project,),
            )
            if bucket_timezone is not None:
                conn.execute(
                    "CREATE TABLE IF NOT EXISTS rollup_meta "
                    "(key TEXT PRIMARY KEY, value TEXT NOT NULL)"
                )
                conn.execute(
                    "INSERT OR REPLACE INTO rollup_meta (key, value) VALUES (?, ?)",
                    ("bucket_timezone", bucket_timezone),
                )
            conn.commit()
        finally:
            conn.close()

    @staticmethod
    def seed_recovery_rollup(
        db_path,
        project,
        input_tokens,
        bucket_timezone="Asia/Shanghai",
    ):
        conn = sqlite3.connect(db_path)
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS daily_rollup (
                  date TEXT NOT NULL,
                  agent_id TEXT NOT NULL,
                  project TEXT NOT NULL,
                  model TEXT NOT NULL,
                  input_tokens INTEGER NOT NULL DEFAULT 0,
                  PRIMARY KEY (date, agent_id, project, model)
                )
                """
            )
            conn.execute(
                """
                INSERT INTO daily_rollup (date, agent_id, project, model, input_tokens)
                VALUES ('2026-06-13', 'codex', ?, 'gpt-5', ?)
                """,
                (project, input_tokens),
            )
            if bucket_timezone is not None:
                conn.execute(
                    "CREATE TABLE IF NOT EXISTS rollup_meta "
                    "(key TEXT PRIMARY KEY, value TEXT NOT NULL)"
                )
                conn.execute(
                    "INSERT OR REPLACE INTO rollup_meta (key, value) VALUES (?, ?)",
                    ("bucket_timezone", bucket_timezone),
                )
            conn.commit()
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
