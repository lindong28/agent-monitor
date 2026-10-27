"""Run production browser modules through their unmodified public interfaces."""
import subprocess
import unittest
from pathlib import Path


class WebModuleTests(unittest.TestCase):
    def test_browser_loading_and_page_lifetime(self):
        result = subprocess.run(
            ["node", "tests/web_modules.cjs"], cwd=Path(__file__).resolve().parents[1],
            text=True, capture_output=True, timeout=15,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
