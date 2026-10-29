"""Persistent test workspaces: release successful cases, keep diagnostic state."""
import os
from pathlib import Path
import shutil
import stat
import sys
import tempfile
import unittest


class _SuccessRecorder:
    def __init__(self, result, case):
        self.result = result
        self.case = case
        self.passed = False

    def __getattr__(self, name):
        return getattr(self.result, name)

    def addSuccess(self, test):
        self.result.addSuccess(test)
        if test is self.case:
            self.passed = True


class ArtifactTestCase(unittest.TestCase):
    """Only this case's newly created directories are eligible for removal.

    unittest reports success after tearDown and all cleanup callbacks. Waiting
    for run() to return also leaves interrupted and unknown outcomes intact.
    """

    def artifact_directory(self, *, root=None, prefix="case-"):
        root = Path(root if root is not None else os.environ.get(
            "AGENT_MONITOR_TEST_ARTIFACTS",
            Path.home() / "task-artifacts" / "agent-monitor-tests"))
        root.mkdir(parents=True, exist_ok=True)
        path = Path(tempfile.mkdtemp(prefix=prefix, dir=root.resolve()))
        info = path.stat()
        if not hasattr(self, "_artifact_directories"):
            self._artifact_directories = []
        self._artifact_directories.append((path, (info.st_dev, info.st_ino)))
        return path

    def run(self, result=None):
        own_result = result is None
        result = self.defaultTestResult() if own_result else result
        recorder = _SuccessRecorder(result, self)
        complete = False
        if own_result and hasattr(result, "startTestRun"):
            result.startTestRun()
        try:
            super().run(recorder)
            complete = True
        finally:
            keep = os.environ.get("AGENT_MONITOR_KEEP_TEST_ARTIFACTS") == "1"
            for path, identity in getattr(self, "_artifact_directories", ()):
                if complete and recorder.passed and not keep:
                    try:
                        current = path.lstat()
                        if (not stat.S_ISDIR(current.st_mode) or
                                (current.st_dev, current.st_ino) != identity):
                            raise RuntimeError("test workspace identity changed: %s" % path)
                        shutil.rmtree(path)
                    except Exception:
                        result.addError(self, sys.exc_info())
                        print("Test artifacts retained or partially removed: %s" % path,
                              file=sys.stderr)
                else:
                    print("Test artifacts retained: %s" % path, file=sys.stderr)
            self._artifact_directories = []
            if own_result and hasattr(result, "stopTestRun"):
                result.stopTestRun()
        return result
