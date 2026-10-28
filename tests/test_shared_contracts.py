"""Shared models retain identity without importing their implementation consumers."""
import os
from pathlib import Path
import subprocess
import sys
import unittest

from agent_monitor.contracts import usage
import parsers


class SharedContractTests(unittest.TestCase):
    def test_legacy_model_imports_are_the_same_objects(self):
        for name in ("UsageEntry", "RateLimits", "validate_usage_metadata",
                     "complete_usage_observation", "usage_quality_counts"):
            with self.subTest(name=name):
                self.assertIs(getattr(parsers, name), getattr(usage, name))

    def test_loaded_history_metadata_is_not_shared_between_instances(self):
        first, second = usage.LoadedEntries(), usage.LoadedEntries()
        for field in ("blocked_sources", "persisted_blockers", "current_blockers",
                      "unattributed_entries", "source_errors", "session_metadata"):
            with self.subTest(field=field):
                getattr(first, field).append({"fixture": field})
                self.assertEqual([], getattr(second, field))

    def test_contracts_and_admission_do_not_load_rollup_or_collectors(self):
        script = '''
import importlib, sys
class ForbiddenImplementation:
    def find_spec(self, fullname, *args):
        if fullname in {"agent_monitor.accounting.rollup", "agent_monitor.aggregations", "agent_monitor.http_server"}:
            raise AssertionError("implementation dependency: " + fullname)
sys.meta_path.insert(0, ForbiddenImplementation())
import agent_monitor.contracts.usage, agent_monitor.contracts.rollup
from agent_monitor.snapshots import generation
# Positive control: the guard actually rejects an implementation import.
try:
    importlib.import_module("agent_monitor.accounting.rollup")
except AssertionError:
    pass
else:
    raise AssertionError("import guard did not run")
'''
        result = subprocess.run(
            [sys.executable, "-c", script], cwd=Path(__file__).resolve().parents[1],
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
            text=True, capture_output=True, timeout=10,
        )
        self.assertEqual(0, result.returncode, result.stderr)
