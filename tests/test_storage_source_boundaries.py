"""Application SQLite and rollup access points stay within their storage owners."""
import ast
from pathlib import Path
import tempfile
import unittest

from agent_monitor import source_inventory
from agent_monitor.paths import PROJECT_ROOT

ROOT = PROJECT_ROOT
EXTRA_ROLLUP_SOURCES = (
    "scripts/benchmark_backend.py",
    "tests/check_rollup_source_coverage.py",
)


def census_sources(root, *, connections=False):
    """Reuse registered runtime sources; opt in only the operational SQL tools.

    Connection ownership covers application persistence, not provider database
    readers. The broader rollup census also checks those readers for bypasses.
    """
    root = Path(root)
    paths = source_inventory.python_sources(root)
    if connections:
        return [path for path in paths
                if path.parent == root or path.relative_to(root).parts[0] == "agent_monitor"]
    return sorted(set(paths) | {root / name for name in EXTRA_ROLLUP_SOURCES
                              if (root / name).is_file()})


class StorageSourceBoundaryTests(unittest.TestCase):
    def test_production_sqlite_connect_sites_match_explicit_census(self):
        expected = [
            ("agent_monitor/ingestion/project_identity.py", "stored_identity", "project_identity_read"),
            ("agent_monitor/ingestion/project_identity.py", "stored_projects", "project_identity_read"),
            ("agent_monitor/ingestion/project_identity.py", "_list_project_identity_blockers", "project_identity_read"),
            ("agent_monitor/ingestion/project_identity.py", "_record_project_identity_blocker_locked", "project_identity_write"),
            ("agent_monitor/ingestion/project_identity.py", "_begin_identity_recovery", "project_identity_write"),
            ("agent_monitor/ingestion/project_identity.py", "recover_reviewed", "reviewed_identity_preview_read_only"),
            ("agent_monitor/ingestion/project_identity.py", "_store_project_identity", "project_identity_write"),
            ("agent_monitor/snapshots/exporter.py", "_vacuum_into_locked", "locked_read_only_vacuum_snapshot"),
            ("agent_monitor/snapshots/generation.py", "snapshot_stats", "immutable_generation_validation"),
            ("agent_monitor/accounting/rollup.py", "adopt_bucket_timezone", "explicit_legacy_adoption_write"),
            ("agent_monitor/accounting/rollup.py", "_connect", "rollup_write_chokepoint"),
            # _read_connection still owns the marker contract and the choice of
            # read mode; it delegates only the opening itself, so this is the
            # same chokepoint rather than a second way in.
            ("agent_monitor/storage.py", "_connect_read_only", "rollup_read_chokepoint"),
            ("agent_monitor/storage.py", "_private_read_copy", "locked_source_private_copy_recovery"),
            ("agent_monitor/ingestion/usage_archive.py", "read", "retained_usage_read_only"),
            ("agent_monitor/ingestion/usage_archive.py", "collect", "retained_usage_transaction_write"),
            ("agent_monitor/snapshots/statistics_snapshot.py", "_write_snapshot_content", "unpublished_statistics_write"),
            # Read-only stats, locked live compaction, and the private backup
            # target share this CLI-only opener; lifecycle tests cover each mode.
            ("agent_monitor/cli/archive_maintenance.py", "_connect", "explicit_archive_maintenance_and_backup"),
            ("agent_monitor/snapshots/statistics_snapshot.py", "read_snapshot", "admitted_statistics_read_only"),
            ("agent_monitor/snapshots/statistics_snapshot.py", "read_metadata", "admitted_statistics_metadata_read_only"),
            ("agent_monitor/snapshots/statistics_snapshot.py", "_admitted_connection", "admitted_statistics_read_only"),
        ]
        dispositions = {
            (file_name, function_name): disposition
            for file_name, function_name, disposition in expected
        }
        actual = []
        root = ROOT
        for path in census_sources(root, connections=True):
            tree = ast.parse(path.read_text())
            parents = {
                child: node
                for node in ast.walk(tree)
                for child in ast.iter_child_nodes(node)
            }
            for node in ast.walk(tree):
                if not (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "sqlite3"
                    and node.func.attr == "connect"
                ):
                    continue
                owner = node
                while owner in parents and not isinstance(
                    owner, (ast.FunctionDef, ast.AsyncFunctionDef)
                ):
                    owner = parents[owner]
                function_name = owner.name if hasattr(owner, "name") else "<module>"
                key = (path.relative_to(root).as_posix(), function_name)
                actual.append(
                    (path.relative_to(root).as_posix(), function_name, dispositions.get(key, "UNCLASSIFIED"))
                )

        self.assertCountEqual(actual, expected)


    def test_production_daily_rollup_and_attach_sites_match_explicit_census(self):
        expected_daily_rollup = [
            ("scripts/benchmark_backend.py", "make_snapshot", "isolated_benchmark_fixture"),
            ("scripts/benchmark_backend.py", "make_snapshot", "isolated_benchmark_fixture"),
            ("agent_monitor/ingestion/project_identity.py", "recover_reviewed", "reviewed_identity_preview_read_only"),
            ("agent_monitor/ingestion/project_identity.py", "recover_reviewed", "reviewed_identity_preview_read_only"),
            ("agent_monitor/ingestion/project_identity.py", "_legacy_project_identity", "rollup_read_chokepoint"),
            ("agent_monitor/ingestion/project_identity.py", "_legacy_project_identity", "rollup_read_chokepoint"),
            ("agent_monitor/ingestion/project_identity.py", "_begin_identity_recovery", "recovery_prechecked_attach"),
            ("agent_monitor/ingestion/project_identity.py", "_begin_identity_recovery", "recovery_prechecked_attach"),
            ("agent_monitor/ingestion/project_identity.py", "_derive_pin_candidate", "recovery_prechecked_attach"),
            ("agent_monitor/snapshots/generation.py", "snapshot_stats", "immutable_generation_validation"),
            ("agent_monitor/snapshots/generation.py", "snapshot_stats", "immutable_generation_validation"),
            ("agent_monitor/snapshots/generation.py", "snapshot_stats", "immutable_generation_validation"),
            ("agent_monitor/snapshots/generation.py", "snapshot_stats", "immutable_generation_validation"),
            ("agent_monitor/snapshots/generation.py", "snapshot_stats", "immutable_generation_validation"),
            (
                "tests/check_rollup_source_coverage.py",
                "_read_rows",
                "operational_coverage_read_chokepoint",
            ),
            (
                "tests/check_rollup_source_coverage.py",
                "_read_rows",
                "operational_coverage_read_chokepoint",
            ),
        ]
        expected_attach = [
            ("agent_monitor/ingestion/project_identity.py", "_begin_identity_recovery", "recovery_prechecked_attach")
        ]
        expected_read_gates = [
            ("agent_monitor/ingestion/project_identity.py", "recover_reviewed"),
            ("agent_monitor/ingestion/project_identity.py", "_legacy_project_identity"),
            ("agent_monitor/ingestion/project_identity.py", "_begin_identity_recovery"),
            ("tests/check_rollup_source_coverage.py", "_read_rows"),
        ]
        dispositions = {
            (file_name, function_name): disposition
            for file_name, function_name, disposition in (
                expected_daily_rollup + expected_attach
            )
        }
        daily_rollup = []
        attach = []
        read_gates = []
        lines_by_kind = {}
        root = ROOT
        explicitly_excluded = {
            "agent_monitor/storage.py": "shared read chokepoint and bucket timezone checks",
            "agent_monitor/accounting/rollup.py": (
                "daily_rollup helpers receive chokepoint connections except the named explicit adoption transaction"
            ),
        }
        scanned_paths = []
        for path in census_sources(root):
            relative_path = path.relative_to(root).as_posix()
            if relative_path in explicitly_excluded:
                continue
            scanned_paths.append(relative_path)
            tree = ast.parse(path.read_text())
            parents = {
                child: node
                for node in ast.walk(tree)
                for child in ast.iter_child_nodes(node)
            }
            for node in ast.walk(tree):
                owner = node
                while owner in parents and not isinstance(
                    owner, (ast.FunctionDef, ast.AsyncFunctionDef)
                ):
                    owner = parents[owner]
                function_name = owner.name if hasattr(owner, "name") else "<module>"
                key = (relative_path, function_name)
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    if "daily_rollup" in node.value:
                        daily_rollup.append(
                            (relative_path, function_name, dispositions.get(key, "UNCLASSIFIED"))
                        )
                        lines_by_kind.setdefault(
                            (relative_path, function_name, "daily_rollup"), []
                        ).append(node.lineno)
                    if "ATTACH DATABASE" in node.value.upper():
                        attach.append(
                            (relative_path, function_name, dispositions.get(key, "UNCLASSIFIED"))
                        )
                        lines_by_kind.setdefault(
                            (relative_path, function_name, "attach"), []
                        ).append(node.lineno)
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id in {"_read_connection", "read_connection"}
                ):
                    read_gates.append((relative_path, function_name))
                    lines_by_kind.setdefault(
                        (relative_path, function_name, "read_gate"), []
                    ).append(node.lineno)

        self.assertTrue(
            {
                "parsers/codex.py",
                "ip_check/cli.py",
                "tests/check_rollup_source_coverage.py",
            }.issubset(scanned_paths),
            scanned_paths,
        )
        self.assertCountEqual(daily_rollup, expected_daily_rollup)
        self.assertCountEqual(attach, expected_attach)
        self.assertCountEqual(read_gates, expected_read_gates)
        self.assertLess(
            max(lines_by_kind[("agent_monitor/ingestion/project_identity.py", "_begin_identity_recovery", "read_gate")]),
            min(lines_by_kind[("agent_monitor/ingestion/project_identity.py", "_begin_identity_recovery", "attach")]),
        )
        self.assertLess(
            max(lines_by_kind[("agent_monitor/ingestion/project_identity.py", "_legacy_project_identity", "read_gate")]),
            min(lines_by_kind[("agent_monitor/ingestion/project_identity.py", "_legacy_project_identity", "daily_rollup")]),
        )
        self.assertLess(
            max(
                lines_by_kind[
                    ("tests/check_rollup_source_coverage.py", "_read_rows", "read_gate")
                ]
            ),
            min(
                lines_by_kind[
                    ("tests/check_rollup_source_coverage.py", "_read_rows", "daily_rollup")
                ]
            ),
        )


class CensusScopeTests(unittest.TestCase):
    def test_registered_sources_and_explicit_tools_exclude_runtime_and_other_tests(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            included = ["bootstrap.py", *(name + "/nested/source.py" for name in source_inventory.PACKAGE_ROOTS),
                        *EXTRA_ROLLUP_SOURCES]
            excluded = [".venv/installed.py", ".runtime/helper.py", "state/generated.py",
                        "web/vendor/helper.py", "tests/test_unrelated.py", "scripts/unrelated.py"]
            for name in included + excluded:
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("value = 1\n" if name in included else "not valid Python !!!")
            paths = census_sources(root)
            self.assertEqual({path.relative_to(root).as_posix() for path in paths}, set(included))
            for path in paths:
                ast.parse(path.read_text())
            self.assertEqual({path.relative_to(root).as_posix() for path in census_sources(root, connections=True)},
                             {"bootstrap.py", "agent_monitor/nested/source.py"})

    def test_new_registered_source_is_scanned_and_invalid_python_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for package in source_inventory.PACKAGE_ROOTS:
                with self.subTest(package=package):
                    path = root / package / "nested/new.py"
                    path.parent.mkdir(parents=True, exist_ok=True)
                    self.assertNotIn(path, census_sources(root))
                    path.write_text("import sqlite3\nconnection = sqlite3.connect('fixture.db')\n")
                    self.assertIn(path, census_sources(root))
                    tree = ast.parse(path.read_text())
                    self.assertTrue(any(isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                                        and node.func.attr == "connect" for node in ast.walk(tree)))
                    path.write_text("not valid Python !!!")
                    with self.assertRaises(SyntaxError):
                        for source in census_sources(root):
                            ast.parse(source.read_text())
                    path.unlink()
