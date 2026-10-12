import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class ProductBoundaryTests(unittest.TestCase):
    def test_monitor_imports_and_exports_when_gateway_import_is_forbidden(self):
        script = '''
import sys
class NoGateway:
    def find_spec(self, fullname, *args):
        if fullname.split('.')[0] in {'llm_gateway', 'llm_attempts', 'gateway_dependency'}:
            raise AssertionError('Gateway dependency used')
sys.meta_path.insert(0, NoGateway())
import server, exporter, statistics_snapshot
from pathlib import Path
from unittest.mock import patch
assert not any('/llm-call' in path for path in server.ROUTES)
with patch.object(exporter, 'exporter_version', return_value='a' * 40):
    bundle = exporter.export_bundle(Path.home() / 'rollup.db', Path.home() / 'bundle',
        entries_loader=lambda: [], rate_limits={},
        source_host_identity='host-v1:' + '1' * 64)
assert statistics_snapshot.read_snapshot(Path.home() / 'bundle/snapshot.db')['version'] == 2
'''
        with tempfile.TemporaryDirectory() as home:
            env = {**os.environ, "HOME": home, "LLM_GATEWAY_ROOT": home + "/absent", "PYTHONPATH": "."}
            result = subprocess.run([sys.executable, "-c", script], env=env,
                cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_gateway_navigation_and_assets_are_retired(self):
        root = Path(__file__).resolve().parents[1]
        for page in (root / "web").glob("*.html"):
            self.assertNotIn('/llm-calls', page.read_text(), page.name)
        self.assertFalse((root / "web/llm-calls.js").exists())
