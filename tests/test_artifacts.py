"""Exercise artifact lifetime through unittest's public execution interface."""
import contextlib
import io
import os
import unittest
from unittest.mock import patch

from tests.artifacts import ArtifactTestCase


class ArtifactLifetimeTests(ArtifactTestCase):
    def setUp(self):
        self.root = self.artifact_directory(prefix="artifact-lifetime-")

    def case(self, stage="success"):
        root = self.root

        class Case(ArtifactTestCase):
            def setUp(self):
                self.directory = self.artifact_directory(root=root)
                (self.directory / "evidence").write_text("synthetic evidence")
                if stage == "setup":
                    self.fail("setup failure")
                if stage == "cleanup":
                    self.addCleanup(self.fail, "cleanup failure")

            def tearDown(self):
                if stage == "teardown":
                    self.fail("teardown failure")

            def runTest(self):
                if stage == "body":
                    self.fail("body failure")
                elif stage == "subtest":
                    with self.subTest(value=1):
                        self.fail("subtest failure")
                elif stage == "interrupt":
                    raise KeyboardInterrupt()
                elif stage == "skip":
                    self.skipTest("deliberately skipped")

        return Case()

    def execute(self, case, result=None):
        output = io.StringIO()
        with contextlib.redirect_stderr(output):
            result = case.run(result)
        return result, output.getvalue()

    def test_success_removes_only_its_new_directory(self):
        sibling = self.root / "existing-evidence"
        sibling.write_text("keep")
        case = self.case()
        result, _ = self.execute(case)
        self.assertTrue(result.wasSuccessful())
        self.assertFalse(case.directory.exists())
        self.assertEqual(sibling.read_text(), "keep")

    def test_failures_and_skip_keep_evidence(self):
        for stage in ("setup", "body", "teardown", "cleanup", "subtest", "skip"):
            with self.subTest(stage=stage):
                case = self.case(stage)
                result, output = self.execute(case)
                self.assertEqual(len(result.skipped) if stage == "skip" else
                                 len(result.failures) + len(result.errors), 1)
                self.assertEqual((case.directory / "evidence").read_text(),
                                 "synthetic evidence")
                self.assertIn(str(case.directory), output)

    def test_previous_failure_does_not_keep_successful_case(self):
        result = unittest.TestResult()
        failure, success = self.case("body"), self.case()
        self.execute(failure, result)
        self.execute(success, result)
        self.assertEqual(len(result.failures), 1)
        self.assertTrue(failure.directory.exists())
        self.assertFalse(success.directory.exists())

    def test_explicit_keep(self):
        case = self.case()
        with patch.dict(os.environ, {"AGENT_MONITOR_KEEP_TEST_ARTIFACTS": "1"}):
            result, output = self.execute(case)
        self.assertTrue(result.wasSuccessful())
        self.assertTrue(case.directory.exists())
        self.assertIn(str(case.directory), output)

    def test_interrupt_keeps_evidence(self):
        case = self.case("interrupt")
        with self.assertRaises(KeyboardInterrupt):
            self.execute(case)
        self.assertTrue((case.directory / "evidence").exists())

    def test_replaced_directory_is_not_removed(self):
        case = self.case()
        original_body = case.runTest

        def replace():
            original_body()
            case.directory.rename(self.root / "original")
            case.directory.mkdir()
            (case.directory / "other-owner").write_text("keep")

        case.runTest = replace
        result, _ = self.execute(case)
        self.assertFalse(result.wasSuccessful())
        self.assertEqual((case.directory / "other-owner").read_text(), "keep")
        self.assertTrue((self.root / "original" / "evidence").exists())

    def test_child_symlink_does_not_delete_target(self):
        target = self.root / "external-evidence"
        target.mkdir()
        (target / "keep").write_text("keep")
        case = self.case()
        case.runTest = lambda: (case.directory / "external").symlink_to(target)
        result, _ = self.execute(case)
        self.assertTrue(result.wasSuccessful())
        self.assertFalse(case.directory.exists())
        self.assertEqual((target / "keep").read_text(), "keep")

    def test_archive_modules_honor_root_overrides(self):
        from tests.test_incremental_archive import IncrementalArchiveTests
        from tests.test_normalized_observation_paths import NormalizedObservationPathTests

        for case_type in (IncrementalArchiveTests, NormalizedObservationPathTests):
            for legacy_override in (False, True):
                with self.subTest(module=case_type.__module__, legacy=legacy_override):
                    general, legacy = self.root / "general", self.root / "legacy"
                    env = dict(os.environ, AGENT_MONITOR_TEST_ARTIFACTS=str(general))
                    env.pop("ARCHIVE_TEST_ROOT", None)
                    env.pop("AGENT_MONITOR_KEEP_TEST_ARTIFACTS", None)
                    if legacy_override:
                        env["ARCHIVE_TEST_ROOT"] = str(legacy)
                    case = case_type()
                    expected = legacy if legacy_override else general
                    case.runTest = lambda: self.assertEqual(case.root.parent, expected)
                    with patch.dict(os.environ, env, clear=True):
                        result, _ = self.execute(case)
                    self.assertTrue(result.wasSuccessful(), (result.errors, result.failures))
                    self.assertFalse(case.root.exists())


if __name__ == "__main__":
    unittest.main()
