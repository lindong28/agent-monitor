"""Independent ingestion owners retain their own persistence and synchronization."""
import tempfile
import threading
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from agent_monitor.contracts import usage
from agent_monitor.ingestion import ProjectIdentityStore, UsageLoader, project_identity, usage_loader


class IngestionInstanceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.stores = [ProjectIdentityStore(self.root / name / "identity.db")
                       for name in ("first", "second")]
        for patch in (
            mock.patch("pricing_fetcher.get_pricing", return_value={}),
            mock.patch("pricing_fetcher.with_calculated_cost", side_effect=lambda entry, **_: entry),
        ):
            patch.start()
            self.addCleanup(patch.stop)

    def entry(self, name):
        return usage.UsageEntry(
            timestamp=datetime(2026, 4, 15, tzinfo=timezone.utc),
            session_id=name, message_id=name, request_id=name, model="fixture",
            input_tokens=1, output_tokens=2, cache_creation_tokens=0,
            cache_read_tokens=0, cost_usd=1.0, project=name, agent_id="codex",
        )

    def test_project_caches_and_persisted_identities_are_independent(self):
        first, second = self.stores
        source = "/fixture/repo"
        for store, remote in ((first, "example/first"), (second, "example/second")):
            with mock.patch.object(store, "resolve_project", return_value=(remote, "remote")):
                self.assertEqual(store.identify_project(source), remote)
        self.assertEqual(first.project_cache, {source: "example/first"})
        self.assertEqual(second.project_cache, {source: "example/second"})
        first.project_cache.clear()
        for store, remote in ((first, "example/first"), (second, "example/second")):
            restarted = ProjectIdentityStore(store.path)
            with mock.patch.object(restarted, "resolve_project", side_effect=AssertionError("must use persisted identity")):
                self.assertEqual(restarted.identify_project(source), remote)
        self.assertEqual(first.project_cache, {})
        self.assertEqual(second.project_cache, {source: "example/second"})

    def test_reload_and_collection_do_not_share_cache_or_lock(self):
        caches = [mock.Mock(), mock.Mock()]
        for cache, name in zip(caches, ("first", "second")):
            cache.load.return_value = [self.entry(name)]
        first, second = [UsageLoader(store, cache=cache)
                         for store, cache in zip(self.stores, caches)]
        self.assertEqual(first.load_all_entries(force_reload=True)[0].project, "first")
        caches[0].clear.assert_called_once_with()
        caches[1].clear.assert_not_called()
        caches[1].load.assert_not_called()
        completed = threading.Event()
        results = []

        def collect_second():
            try:
                results.append(second.load_all_entries())
            except Exception as exc:
                results.append(exc)
            finally:
                completed.set()

        # Both first-instance locks remain held while the second completes.
        with first.identity_store.locked(), first._lock:
            worker = threading.Thread(target=collect_second, daemon=True)
            worker.start()
            independent = completed.wait(3)
        worker.join(3)
        self.assertTrue(independent, "second owner waited for first owner's locks")
        self.assertFalse(worker.is_alive())
        self.assertEqual(results[0][0].project, "second")
        caches[0].load.assert_called_once_with()
        caches[1].load.assert_called_once_with()
        caches[1].clear.assert_not_called()

    def test_archive_fingerprint_tracks_extracted_behavior_and_contract(self):
        loader = UsageLoader(self.stores[0])
        baseline = loader.parser_fingerprint()
        for module in (usage_loader, project_identity, usage):
            with self.subTest(owner=module.__name__):
                source = Path(module.__file__).read_bytes()
                copied = self.root / (module.__name__ + ".py")
                copied.write_bytes(source)
                with mock.patch.object(module, "__file__", str(copied)):
                    self.assertEqual(loader.parser_fingerprint(), baseline)
                    copied.write_bytes(source + b"\n# changed parser behavior\n")
                    self.assertNotEqual(loader.parser_fingerprint(), baseline)
                self.assertEqual(loader.parser_fingerprint(), baseline)


if __name__ == "__main__":
    unittest.main()
