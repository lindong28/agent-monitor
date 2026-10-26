"""Deployment identity regressions with synthetic source and configuration only."""
from pathlib import Path
import tempfile
import unittest

import service_runtime


class ServiceSignatureTests(unittest.TestCase):
    def test_proxy_sources_invalidate_hub_and_connector_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for relative in ('proxy_runtime/client.py', 'proxy_runtime/main.cjs',
                             'proxy_runtime/nested/helper.py', 'proxy_runtime/nested/helper.cjs'):
                with self.subTest(source=relative):
                    path = root / relative
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text('before\n')
                    before = service_runtime.signature(root)
                    settings = ['http://synthetic.invalid', str(root / 'state.json')]
                    old_connector = service_runtime.configured_signature(before, settings)
                    self.assertEqual(before, service_runtime.signature(root))
                    path.write_text('after\n')
                    after = service_runtime.signature(root)
                    self.assertNotEqual(before, after)
                    for old, new in ((before, after), (old_connector, service_runtime.configured_signature(after, settings))):
                        self.assertFalse(service_runtime.matches({'source_root': str(root), 'deployment_signature': old}, root, new))
                        self.assertTrue(service_runtime.matches({'source_root': str(root), 'deployment_signature': new}, root, new))
                    path.unlink()
                    self.assertNotEqual(after, service_runtime.signature(root))

    def test_generated_cache_does_not_change_signature(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cache = root / 'proxy_runtime/__pycache__/client.pyc'
            cache.parent.mkdir(parents=True)
            before = service_runtime.signature(root)
            cache.write_bytes(b'generated cache')
            self.assertEqual(before, service_runtime.signature(root))
