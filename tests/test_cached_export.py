"""Cached export through the public CLI, with isolated state and denied sources."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest import mock

from tests.artifacts import ArtifactTestCase
import test_exporter
from agent_monitor.snapshots import exporter

ROOT = Path(__file__).resolve().parents[1]


class CachedExportTests(ArtifactTestCase):
    def setUp(self):
        self.root = self.artifact_directory(prefix="cached-export-")
        self.state = self.root / "state"
        self.state.mkdir()
        self.db = test_exporter.ExporterTests.make_db(self.state / "rollup.db", 17)
        self.cache = self.state / "quota_snapshot.json"
        self.saved = {"codex": {"account_id": "fixture-account", "updated_at": "2026-08-04T00:00:00Z",
                                "primary_used_percent": None, "query_state": "unknown",
                                "refresh_error": "fixture failure"}}
        self.cache.write_text(json.dumps(self.saved))
        self.guard_dir = self.root / "guard"
        self.guard_dir.mkdir()
        (self.guard_dir / "sitecustomize.py").write_text('''
import os
from pathlib import Path
import pwd
import sys
from urllib.parse import unquote, urlsplit
real_home = Path(pwd.getpwuid(os.getuid()).pw_dir)
fixture = Path(os.environ["CACHED_EXPORT_FIXTURE"])
protected = [real_home / ".codex", real_home / ".claude", real_home / ".local/share/codex-accounts",
             fixture / "home/.codex", fixture / "home/.claude", fixture / "home/.local/share/codex-accounts"]
protected = [p.resolve() for p in protected]
def guard(event, args):
    if event in ("socket.connect", "socket.getaddrinfo"):
        raise RuntimeError("SOURCE_GUARD: network rejected")
    if event in ("open", "os.listdir", "os.scandir", "sqlite3.connect") and args and isinstance(args[0], (str, bytes, os.PathLike)):
        raw = os.fsdecode(args[0])
        if raw.startswith("file:"):
            raw = unquote(urlsplit(raw).path)
        target = Path(raw).resolve()
        if any(target == root or root in target.parents for root in protected):
            raise RuntimeError("SOURCE_GUARD: provider root rejected")
sys.addaudithook(guard)
# Positive control covers native homes, managed accounts and session links.
for root in protected:
    try:
        open(root / "session-links/guard-control")
    except RuntimeError as error:
        assert "SOURCE_GUARD" in str(error)
    else:
        raise AssertionError("source guard did not reject")
# Negative control: an ordinary isolated local record is readable.
assert (fixture / "guard/allowed").read_text() == "allowed"
from agent_monitor.accounting import rollup
rollup.DEFAULT_DB_PATH = fixture / "state/rollup.db"
from agent_monitor.snapshots import exporter
# Existing exporter tests separately exercise real dirty/clean Git checks.
if os.environ.get("CACHED_EXPORT_REAL_VERSION") != "1":
    exporter.exporter_version = lambda: "a" * 40
def forbidden(*args, **kwargs):
    raise RuntimeError("SOURCE_GUARD: collection or quota path entered")
rollup.load_all_entries = forbidden
rollup.run = forbidden
exporter._rate_limits = forbidden
exporter._gui_rate_limits = forbidden
from parsers import codex_profiles
codex_profiles.discover = forbidden
codex_profiles.session_links = forbidden
''')
        (self.guard_dir / "allowed").write_text("allowed")
        self.env = dict(os.environ, CACHED_EXPORT_FIXTURE=str(self.root),
                        HOME=str(self.root / "home"),
                        CLAUDE_CONFIG_DIR=str(self.root / "home/.claude"),
                        CODEX_HOME=str(self.root / "home/.codex"),
                        AGENT_MONITOR_PYTHON=sys.executable,
                        PYTHONPATH=os.pathsep.join((str(self.guard_dir), str(ROOT))),
                        PYTHONDONTWRITEBYTECODE="1")

    def invoke(self, *args):
        result = subprocess.run([str(ROOT / "agent-monitor"), "export", *args],
                                env=self.env, capture_output=True, text=True, timeout=30)
        self.assertNotIn("Error in sitecustomize", result.stderr)
        return result

    def test_public_cli_preserves_records_without_source_or_network_access(self):
        before = test_exporter.ExporterTests.application_contract(self.db)
        cache_before = self.cache.read_bytes()
        before_digest = hashlib.sha256(self.db.read_bytes()).hexdigest()
        for index in range(2):
            out = self.root / f"bundle-{index}"
            result = self.invoke("--cached-state", "--out", str(out))
            self.assertEqual(result.returncode, 0, result.stderr)
            manifest = json.loads(result.stdout)
            self.assertEqual(manifest["rate_limits"], self.saved)
            self.assertEqual(manifest, json.loads((out / "export.json").read_text()))
            exporter.validate_export_manifest(manifest, out / "snapshot.db")
            self.assertEqual(test_exporter.ExporterTests.application_contract(out / "snapshot.db"), before)
            self.assertEqual(test_exporter.ExporterTests.application_contract(self.db), before)
            self.assertEqual(hashlib.sha256(self.db.read_bytes()).hexdigest(), before_digest)
            self.assertEqual(self.cache.read_bytes(), cache_before)
        self.assertTrue(Path(str(self.db) + ".lock").is_file())

    def test_absent_quota_stays_unrecorded(self):
        self.cache.unlink()
        out = self.root / "no-quota"
        result = self.invoke("--out", str(out), "--cached-state")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["rate_limits"], {})
        self.assertFalse(self.cache.exists())

    def test_invalid_cache_refuses_without_output_or_repair(self):
        for content in ("not-json", "[]", '{"other": {}}', '{"codex": null}'):
            with self.subTest(content=content):
                self.cache.write_text(content)
                out = self.root / "invalid"
                result = self.invoke("--cached-state", "--out", str(out))
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn("saved quota cache", result.stderr)
                self.assertFalse(out.exists())
                self.assertEqual(self.cache.read_text(), content)

    def test_missing_rollup_is_not_created(self):
        self.db.unlink()
        out = self.root / "missing"
        result = self.invoke("--cached-state", "--out", str(out))
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("saved rollup database is missing", result.stderr)
        self.assertFalse(self.db.exists())
        self.assertFalse(out.exists())

    def test_invalid_rollup_refuses_without_rebuilding(self):
        self.db.write_bytes(b"not a database")
        out = self.root / "bad-db"
        result = self.invoke("--cached-state", "--out", str(out))
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("saved rollup database could not be exported", result.stderr)
        self.assertEqual(self.db.read_bytes(), b"not a database")
        self.assertFalse(out.exists())

    def test_state_symlink_is_preserved(self):
        actual = self.root / "retained-state"
        self.state.rename(actual)
        self.state.symlink_to(actual, target_is_directory=True)
        out = self.root / "linked"
        result = self.invoke("--cached-state", "--out", str(out))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.state.is_symlink())
        self.assertEqual(self.state.resolve(), actual)
        self.assertEqual(json.loads(result.stdout)["rate_limits"], self.saved)

    def test_unreadable_cache_is_not_empty_success(self):
        with mock.patch.object(Path, "read_text", side_effect=PermissionError("private details")):
            with self.assertRaisesRegex(exporter.ExportError, "saved quota cache is unreadable") as raised:
                exporter.export_cached_bundle(self.db, self.root / "unreadable")
        self.assertNotIn("private details", str(raised.exception))

    def test_cached_state_rejects_incompatible_options_before_export(self):
        out = self.root / "bad-options"
        cases = [("--quota-only",), ("--version",),
                 *(("--out", str(out), option) for option in
                   ("--cached-quota", "--automatic-quota", "--gui-quota")),
                 ("--out", str(out), "--quota-exclusions", "{}")]
        for args in cases:
            with self.subTest(args=args):
                result = self.invoke("--cached-state", *args)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn("--cached-state", result.stderr)
                self.assertFalse(out.exists())

    def test_default_and_cached_quota_still_take_the_collection_path(self):
        for extra in ((), ("--cached-quota",)):
            with self.subTest(extra=extra):
                result = self.invoke("--out", str(self.root / "normal"), *extra)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("SOURCE_GUARD: collection or quota path entered", result.stderr)


if __name__ == "__main__":
    unittest.main()
