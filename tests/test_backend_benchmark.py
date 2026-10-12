"""Exercise the CLI and distinguish real hashing from cached/view reads."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class BackendBenchmarkTests(unittest.TestCase):
    def test_two_sizes_volume_and_generation_change(self):
        script = Path(__file__).resolve().parents[1] / "scripts" / "benchmark_backend.py"
        with tempfile.TemporaryDirectory() as home:
            env = {**os.environ, "HOME": home}
            result = subprocess.run([sys.executable, str(script), "--rows", "2", "1000",
                                     "--padding-mib", "0", "1", "--repeat", "2"],
                                    env=env, text=True, capture_output=True, timeout=60, check=True)
            self.assertEqual(list(Path(home).iterdir()), [])
        report = json.loads(result.stdout)
        self.assertEqual(len(report["cases"]), 4)
        sizes = {}
        for case in report["cases"]:
            rows, padding = case["aggregate_rows"], case["padding_mib"]
            sizes[rows, padding] = case["snapshot_bytes"]
            for phase, samples in case["phases"].items():
                self.assertEqual(len(samples), 1 if phase == "new_generation_prepare" else 2)
                for sample in samples:
                    with self.subTest(rows=rows, padding=padding, phase=phase):
                        self.assertGreaterEqual(sample["seconds"], 0)
                        if phase in ("cold_admission_query", "cold_prepare", "new_generation_prepare"):
                            self.assertEqual(sample["admission_calls"], 1)
                            self.assertEqual(sample["validation_calls"], 1)
                            self.assertEqual(sample["digest_calls"], 1)
                            expected_bytes = case["new_generation_snapshot_bytes"] if phase == "new_generation_prepare" else case["snapshot_bytes"]
                            self.assertEqual(sample["digest_bytes"], expected_bytes)
                        else:
                            self.assertEqual(sample["admission_calls"], int(phase == "hot_admission_query"))
                            self.assertEqual(sample["validation_calls"], 0)
                            self.assertEqual(sample["digest_bytes"], 0)
                        if phase.endswith("query"):
                            self.assertEqual(sample["input_tokens"], rows * (2 if phase == "new_generation_query" else 1))
            self.assertIn("prepared_query", case["phases"])
        for rows in (2, 1000):
            self.assertGreaterEqual(sizes[rows, 1] - sizes[rows, 0], 1048576)
