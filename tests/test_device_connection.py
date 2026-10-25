"""Browser pairing state-machine checks; all transports are isolated fakes."""
from pathlib import Path
import subprocess
import unittest


class DeviceConnectionTests(unittest.TestCase):
    def test_browser_connection_flow(self):
        result = subprocess.run(
            ["node", "--test", "tests/test_device_connection.cjs"],
            cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=15,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
