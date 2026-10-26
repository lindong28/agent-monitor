import contextlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("benchmark_background", ROOT / "scripts/benchmark_background.py")
benchmark = importlib.util.module_from_spec(spec)
spec.loader.exec_module(benchmark)


class BackgroundBenchmarkTests(unittest.TestCase):
    def test_actual_cli_two_scales_cache_invalidation_and_accounting(self):
        result = subprocess.run([sys.executable, str(ROOT / "scripts/benchmark_backend.py"),
                                 "background", "--rows", "10", "30", "--repeat", "2", "--json"],
                                capture_output=True, text=True, check=True, timeout=30)
        report = json.loads(result.stdout)
        self.assertEqual(report["status"], "completed")
        self.assertEqual([c["initial_entries"] for c in report["cases"]], [10, 30])
        for case in report["cases"]:
            self.assertEqual([s["parsed_files"] for s in case["samples"]], [10, 0, 1, 0, 1])
            self.assertEqual([s["entries"] for s in case["samples"]],
                             [case["initial_entries"] + n for n in (0, 0, 1, 1, 2)])
            for sample in case["samples"]:
                self.assertEqual(sample["input_tokens"], sample["entries"] * 100)
                self.assertEqual(sample["output_tokens"], sample["entries"] * 2)
                for metric in ("cpu_seconds", "wall_seconds"):
                    self.assertGreaterEqual(sample["total"][metric],
                                            sample["collection"][metric] + sample["export"][metric])
            self.assertGreater(case["process_peak_rss_bytes"], 0)
        self.assertIn("statistics_snapshot.py", report["source"]["files"])

    def test_clock_separates_cpu_from_wait_and_preserves_return(self):
        _, idle = benchmark.measured(lambda: time.sleep(0.03))
        def busy():
            end = time.process_time() + 0.03
            while time.process_time() < end:
                pass
            return 42
        value, active = benchmark.measured(busy)
        self.assertEqual(value, 42)
        self.assertGreaterEqual(active["cpu_seconds"], 0.03)
        self.assertGreaterEqual(idle["wall_seconds"], 0.03)
        self.assertLess(idle["cpu_seconds"], active["cpu_seconds"])

    def test_output_token_corruption_is_rejected(self):
        code = '''
import os, sys, tempfile
from pathlib import Path
from dataclasses import replace
from unittest import mock
sys.path.insert(0, str(Path.cwd() / "scripts"))
import benchmark_background as benchmark
with tempfile.TemporaryDirectory() as temp:
    root = Path(temp)
    os.environ["HOME"] = temp
    import aggregators
    parse = aggregators._parse_usage_file
    def corrupted(*args, **kwargs):
        return [replace(e, output_tokens=0) for e in parse(*args, **kwargs)]
    with mock.patch.object(aggregators, "_parse_usage_file", side_effect=corrupted):
        try:
            benchmark.run_case(root, 10, 1)
        except AssertionError as exc:
            assert "accounting mismatch" in str(exc)
        else:
            raise AssertionError("corruption was accepted")
'''
        subprocess.run([sys.executable, "-c", code], cwd=ROOT, check=True,
                       capture_output=True, text=True, timeout=30)

    def test_timeout_retains_completed_cases_and_cleans_worker_root(self):
        benchmark.platform.platform()  # Populate platform's optional subprocess cache before mocking run.
        roots = []
        def child(args, **kwargs):
            root = Path(args[args.index("--case-root") + 1])
            roots.append(root)
            (root / "partial").write_text("synthetic")
            if len(roots) == 1:
                return subprocess.CompletedProcess(args, 0, '{"initial_entries": 10}', '')
            raise subprocess.TimeoutExpired(args, 1)
        output = io.StringIO()
        with mock.patch.object(benchmark.subprocess, "run", side_effect=child), contextlib.redirect_stdout(output):
            with self.assertRaises(SystemExit) as raised:
                benchmark.main(["--rows", "10", "20", "--timeout", "1", "--json"])
        self.assertEqual(raised.exception.code, 1)
        report = json.loads(output.getvalue())
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["cases"], [{"initial_entries": 10}])
        self.assertEqual(report["failure"]["initial_entries"], 20)
        self.assertTrue(all(not root.exists() for root in roots))

    def test_source_identity_detects_dirty_content(self):
        with tempfile.TemporaryDirectory() as temp, mock.patch.object(benchmark, "ROOT", Path(temp)):
            p = Path(temp) / "module.py"
            p.write_text("x = 1\n")
            before = benchmark.source_identity()
            self.assertEqual(before, benchmark.source_identity())
            p.write_text("x = 2\n")
            self.assertNotEqual(before["sha256"], benchmark.source_identity()["sha256"])


if __name__ == "__main__":
    unittest.main()
