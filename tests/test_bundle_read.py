"""Saved material through the public CLI, with real fixture SQLite and denied providers."""
import copy
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
from unittest import mock

from tests.artifacts import ArtifactTestCase
import test_cached_export
from agent_monitor.cli import bundle_read
from agent_monitor.contracts.usage import LoadedEntries, UsageEntry
from agent_monitor.snapshots import exporter, generation, statistics_snapshot
from agent_monitor.views.projections import entry_json

ROOT = Path(__file__).resolve().parents[1]


class BundleReadTests(ArtifactTestCase):
    def setUp(self):
        test_cached_export.CachedExportTests.setUp(self)
        self.temporary = self.root / "temporary"
        self.temporary.mkdir()
        self.env["TMPDIR"] = str(self.temporary)
        with (self.guard_dir / "sitecustomize.py").open("a") as stream:
            stream.write('''
if os.environ.get("BUNDLE_READER_GUARD") == "1":
    from agent_monitor.runtime import service_logs
    service_logs.initialize = forbidden
    def saved_guard(event, args):
        if event == "open" and args and isinstance(args[0], (str, bytes, os.PathLike)):
            target = Path(os.fsdecode(args[0])).resolve()
            if target == fixture / "state" or fixture / "state" in target.parents:
                raise RuntimeError("SOURCE_GUARD: state read or write rejected")
            checkout_state = Path(os.environ["BUNDLE_READER_ROOT"]) / "state"
            if target == checkout_state or checkout_state in target.parents:
                raise RuntimeError("SOURCE_GUARD: checkout state read or write rejected")
            if fixture / "bundle" in target.parents and args[2] & (os.O_WRONLY | os.O_RDWR):
                raise RuntimeError("SOURCE_GUARD: saved input write rejected")
    sys.addaudithook(saved_guard)
    if os.environ.get("BUNDLE_READER_CANCEL") == "1":
        import signal
        def cancel_read(*args):
            os.kill(os.getpid(), signal.SIGTERM)
        exporter.validate_export_manifest = cancel_read
''')

    def make_bundle(self, *, statistics=True, empty=False):
        bundle = self.root / "bundle"
        result = test_cached_export.CachedExportTests.invoke(self, "--cached-state", "--out", str(bundle))
        self.assertEqual(result.returncode, 0, result.stderr)
        if statistics:
            entries = []
            for index, (project, cost) in enumerate((("project-a", None), ("project-a", 0), ("project-b", 1.25))):
                entries.append(UsageEntry(timestamp=datetime(2026, 11, 11, 0, index, tzinfo=timezone.utc),
                    session_id="s1" if index < 2 else "s2", message_id=f"m{index}", request_id=f"r{index}",
                    model="fixture-model", input_tokens=10, output_tokens=3, cache_creation_tokens=5,
                    cache_read_tokens=1, cost_usd=cost, project=project, agent_id="claude-code",
                    message_count=2, cache_creation_1h_tokens=4, usage_quality="incomplete" if cost is None else "observed",
                    service_context={"speed": "standard"}, cost_provenance={"basis": "unpriced", "sources": [],
                        "verified_at": None, "time_basis": "unknown", "reason": "usage_incomplete"} if cost is None else None))
            sessions = [{"agent_id": "claude-code", "session_id": sid, "parent_session_id": None,
                         "relation": "root", "title": "Saved title " + sid, "created_at": None, "project": project,
                         "account": {"name": "fixture", "account_id": "account", "email": "fixture@example.invalid"}}
                        for sid, project in (("s1", "project-a"), ("s2", "project-b"))]
            loaded = LoadedEntries([] if empty else entries, scan_complete=False, blocked_sources=["fixture"],
                                   source_errors=["fixture"], session_metadata=[] if empty else sessions)
            statistics_snapshot.write_snapshot(bundle / "snapshot.db", loaded, observed_at="2026-11-11T01:00:00Z")
            self.refresh_manifest(bundle)
        return bundle

    def make_rollup_bundle(self, *, statistics=False):
        bundle = self.make_bundle(statistics=statistics)
        target = "github.com/lindong28/agent-monitor"
        rows = [
            ("2026-08-04", "codex", target, "fixture-model", 10, 2, 3, 4, 0.0, 0, 2, 3),
            ("2026-08-05", "codex", target, "fixture-model", 11, 2, 3, 4, 0.0, 2, 2, 3),
            ("2026-08-05", "claude-code", target, "fixture-model", 12, 2, 3, 4, 1.25, 1, 2, 3),
            ("2026-08-04", "codex", "github.com/other/agent-monitor", "fixture-model", 91, 2, 3, 4, 4.0, 1, 1, 1),
            ("2026-08-04", "codex", "/fixture/agent-monitor", "fixture-model", 92, 2, 3, 4, 4.0, 1, 1, 1),
            ("2026-08-04", "codex", "github.com/other/unrelated", "fixture-model", 93, 2, 3, 4, 4.0, 1, 1, 1),
        ]
        with sqlite3.connect(bundle / "snapshot.db") as db:
            db.execute("DELETE FROM daily_rollup")
            db.executemany("INSERT INTO daily_rollup VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", rows)
            # This global quantity has no project scope and must never be returned.
            db.execute("UPDATE daily_usage_rollup SET input_tokens=987654321")
        self.refresh_manifest(bundle)
        return bundle, target

    def refresh_manifest(self, bundle, **changes):
        path = bundle / "export.json"
        manifest = json.loads(path.read_text())
        manifest.update(generation.snapshot_stats(bundle / "snapshot.db"))
        manifest.update(changes)
        manifest["transfer_digest"] = hashlib.sha256((bundle / "snapshot.db").read_bytes()).hexdigest()
        manifest["manifest_digest"] = exporter.manifest_digest(manifest)
        path.write_text(json.dumps(manifest))
        return manifest

    def invoke(self, bundle, *args, json_mode=True):
        result = subprocess.run([str(ROOT / "agent-monitor"), "bundle-read", str(bundle),
                                 *(["--json"] if json_mode else []), *args],
                                env=dict(self.env, BUNDLE_READER_GUARD="1", BUNDLE_READER_ROOT=str(ROOT)),
                                capture_output=True, text=True, timeout=30)
        self.assertNotIn("Error in sitecustomize", result.stderr)
        self.assertEqual(list(self.temporary.iterdir()), [], "temporary capture must be reclaimed")
        return result

    def successful(self, bundle, *args):
        result = self.invoke(bundle, *args)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def rejected(self, bundle, code, *args):
        result = self.invoke(bundle, *args)
        self.assertEqual(result.returncode, 2, result.stderr)
        value = json.loads(result.stdout)
        self.assertEqual(value["status"], "error")
        self.assertEqual(value["error"]["code"], code)
        self.assertNotIn("items", value)
        self.assertLessEqual(len(result.stdout.encode()), 256)
        self.assertNotIn(str(self.root), result.stdout + result.stderr)
        return result

    def test_rollup_pages_preserve_native_columns_unknown_cost_and_excluded_global_rows(self):
        bundle, target = self.make_rollup_bundle()
        before = {p.name: p.read_bytes() for p in bundle.iterdir()}
        with sqlite3.connect(bundle / "snapshot.db") as db:
            db.row_factory = sqlite3.Row
            expected = [dict(row) for row in db.execute(
                "SELECT * FROM daily_rollup ORDER BY date, agent_id, project, model")]
        pages = [self.successful(bundle, "--kind", "rollup", "--limit", "2")]
        while pages[-1]["page"]["has_more"]:
            pages.append(self.successful(bundle, "--kind", "rollup", "--limit", "2",
                                        "--cursor", pages[-1]["page"]["next_cursor"]))
        self.assertEqual(len(pages), 3)
        self.assertEqual([row for page in pages for row in page["items"]], expected)
        self.assertEqual(pages[0]["data_state"], "available")
        self.assertEqual(pages[0]["coverage"]["state"], "not_collected")
        coverage = pages[0]["coverage"]["rollup"]
        self.assertEqual(coverage["bucket_timezone"], "Asia/Shanghai")
        self.assertEqual(coverage["cost_usd_basis"], "known_cost_subtotal")
        self.assertEqual(coverage["cost_coverage_fields"], ["cost_known_count", "entry_count"])
        self.assertEqual(coverage["row_quality"], "not_collected")
        self.assertEqual(coverage["cost_provenance"], "not_collected")
        self.assertEqual(coverage["daily_usage_rollup"], "excluded_no_project_dimension")
        selected = [row for row in expected if row["project"] == target]
        self.assertEqual({(r["cost_usd"], r["cost_known_count"], r["entry_count"]) for r in selected},
                         {(0, 0, 2), (0, 2, 2), (1.25, 1, 2)})
        self.assertNotIn("987654321", json.dumps(pages))
        self.assertEqual(before, {p.name: p.read_bytes() for p in bundle.iterdir()})

    def test_rollup_exact_project_agent_and_unsupported_session_filter(self):
        bundle, target = self.make_rollup_bundle()
        result = self.successful(bundle, "--kind", "rollup", "--project", target)
        self.assertEqual(result["page"]["total_matching"], 3)
        self.assertEqual(result["page"]["total_in_section"], 6)
        self.assertEqual({row["project"] for row in result["items"]}, {target})
        first = self.successful(bundle, "--kind", "rollup", "--project", target,
                                "--agent", "codex", "--limit", "1")
        self.assertEqual(first["page"]["total_matching"], 2)
        self.assertTrue(first["page"]["has_more"])
        for project in ("agent-monitor", "project:agent-monitor", "absent"):
            with self.subTest(project=project):
                empty = self.successful(bundle, "--kind", "rollup", "--project", project)
                self.assertEqual(empty["items"], [])
                self.assertEqual(empty["data_state"], "available")
        self.rejected(bundle, "invalid_request", "--kind", "rollup", "--session-id", "s1")
        self.rejected(bundle, "invalid_cursor", "--kind", "usage", "--project", target,
                      "--agent", "codex", "--cursor", first["page"]["next_cursor"])
        self.rejected(bundle, "output_limit", "--kind", "rollup", "--max-output-bytes", "256")

    def test_legacy_rollup_and_old_statistics_are_read_without_contract_migration(self):
        bundle, target = self.make_rollup_bundle(statistics=True)
        with sqlite3.connect(bundle / "snapshot.db") as db:
            db.execute("DROP TABLE daily_usage_rollup")
            db.execute("DELETE FROM rollup_meta WHERE key IN ('usage_rollup_schema', 'usage_rollup_authoritative_from')")
            db.execute("DROP TABLE statistics_sessions")
            meta = json.loads(db.execute("SELECT payload FROM statistics_meta").fetchone()[0])
            meta["version"] = 3
            db.execute("UPDATE statistics_meta SET payload=?", (json.dumps(meta),))
        manifest = self.refresh_manifest(bundle)
        for field in exporter._MANIFEST_V2_FIELDS:
            manifest.pop(field, None)
        manifest["manifest_digest"] = exporter.manifest_digest(manifest)
        (bundle / "export.json").write_text(json.dumps(manifest))
        result = self.successful(bundle, "--kind", "rollup", "--project", target)
        self.assertEqual(result["bundle"]["schema_version"], 1)
        self.assertEqual(result["coverage"]["version"], 3)
        self.assertFalse(result["coverage"]["scan_complete"])
        self.assertEqual(len(result["items"]), 3)
        self.assertEqual(len(self.successful(bundle, "--kind", "usage")["items"]), 3)
        self.assertEqual(self.successful(bundle, "--kind", "sessions")["data_state"], "not_collected")

    def test_empty_project_rollup_never_falls_back_to_global_usage(self):
        bundle, _ = self.make_rollup_bundle()
        with sqlite3.connect(bundle / "snapshot.db") as db:
            db.execute("DELETE FROM daily_rollup")
        self.refresh_manifest(bundle)
        result = self.successful(bundle, "--kind", "rollup")
        self.assertEqual(result["data_state"], "available")
        self.assertEqual(result["items"], [])
        self.assertEqual(result["page"]["total_in_section"], 0)
        self.assertFalse(result["page"]["has_more"])

    def test_normal_pages_equal_existing_reader_without_losing_source_fields(self):
        bundle = self.make_bundle()
        before = {p.name: p.read_bytes() for p in bundle.iterdir()}
        source = statistics_snapshot.read_snapshot(bundle / "snapshot.db")
        first = self.successful(bundle, "--limit", "2")
        self.assertTrue(first["page"]["has_more"])
        second = self.successful(bundle, "--limit", "2", "--cursor", first["page"]["next_cursor"])
        self.assertFalse(second["page"]["has_more"])
        self.assertIsNone(second["page"]["next_cursor"])
        rows = first["items"] + second["items"]
        for actual, entry in zip(rows, source["usage_entries"]):
            expected = asdict(entry); expected["timestamp"] = entry.timestamp.isoformat()
            expected.update(entry_json(entry))
            self.assertEqual(actual, expected)
        self.assertEqual(len(rows), len(source["usage_entries"]))
        self.assertIsNone(rows[0]["cost_usd"])
        self.assertEqual(rows[1]["cost_usd"], 0)
        self.assertEqual(rows[0]["cache_creation_1h_tokens"], 4)
        self.assertEqual(first["coverage"], {k: v for k, v in source.items() if k not in ("usage_entries", "session_metadata")})
        self.assertFalse(first["coverage"]["scan_complete"])
        self.assertEqual(first["bundle"], second["bundle"])
        self.assertEqual(before, {p.name: p.read_bytes() for p in bundle.iterdir()})

    def test_session_pages_preserve_full_metadata(self):
        bundle = self.make_bundle()
        source = statistics_snapshot.read_snapshot(bundle / "snapshot.db")
        first = self.successful(bundle, "--kind", "sessions", "--limit", "1")
        second = self.successful(bundle, "--kind", "sessions", "--cursor", first["page"]["next_cursor"])
        self.assertEqual(first["items"] + second["items"], source["session_metadata"])
        self.assertEqual(second["data_state"], "available")

    def test_exact_filters_and_zero_matches_do_not_claim_complete_source(self):
        bundle = self.make_bundle()
        all_rows = self.successful(bundle)
        filtered = self.successful(bundle, "--project", "project-a", "--agent", "claude-code", "--session-id", "s1")
        self.assertEqual(filtered["page"]["total_matching"], 2)
        self.assertEqual(filtered["page"]["total_in_section"], 3)
        self.assertEqual(filtered["coverage"], all_rows["coverage"])
        zero = self.successful(bundle, "--project", "absent")
        self.assertEqual(zero["items"], [])
        self.assertEqual(zero["data_state"], "available")
        self.assertEqual(zero["page"]["total_in_section"], 3)
        self.assertEqual(zero["coverage"], all_rows["coverage"])
        sessions = self.successful(bundle, "--kind", "sessions", "--session-id", "s2")
        self.assertEqual([row["session_id"] for row in sessions["items"]], ["s2"])

    def test_empty_collected_statistics_remain_distinct_from_not_collected(self):
        bundle = self.make_bundle(empty=True)
        for kind in ("usage", "sessions"):
            value = self.successful(bundle, "--kind", kind)
            self.assertEqual(value["data_state"], "available")
            self.assertEqual(value["page"]["total_matching"], 0)
            self.assertFalse(value["coverage"]["scan_complete"])

    def test_legacy_without_statistics_reports_not_collected(self):
        bundle = self.make_bundle(statistics=False)
        for kind in ("usage", "sessions"):
            value = self.successful(bundle, "--kind", kind)
            self.assertEqual(value["data_state"], "not_collected")
            self.assertEqual(value["coverage"]["state"], "not_collected")
            self.assertEqual(value["items"], [])

    def test_legacy_usage_does_not_claim_session_metadata_collected(self):
        bundle = self.make_bundle()
        with sqlite3.connect(bundle / "snapshot.db") as conn:
            meta = json.loads(conn.execute("SELECT payload FROM statistics_meta").fetchone()[0])
            meta["version"] = 3
            conn.execute("UPDATE statistics_meta SET payload=?", (json.dumps(meta),))
            conn.execute("DROP TABLE statistics_sessions")
        self.refresh_manifest(bundle)
        self.assertEqual(self.successful(bundle)["data_state"], "available")
        self.assertEqual(self.successful(bundle, "--kind", "sessions")["data_state"], "not_collected")

    def test_input_and_output_limits_reject_instead_of_truncating(self):
        bundle = self.make_bundle()
        size = sum((bundle / name).stat().st_size for name in ("export.json", "snapshot.db"))
        self.successful(bundle, "--max-input-bytes", str(size))
        self.rejected(bundle, "input_limit", "--max-input-bytes", str(size - 1))
        original = self.invoke(bundle)
        count = len(original.stdout.encode())
        exact = self.invoke(bundle, "--max-output-bytes", str(count))
        self.assertEqual(exact.returncode, 0, exact.stderr)
        self.assertEqual(exact.stdout, original.stdout)
        self.rejected(bundle, "output_limit", "--max-output-bytes", str(count - 1))

    def test_identity_pin_and_cursor_refuse_changed_material_and_selection(self):
        bundle = self.make_bundle()
        first = self.successful(bundle, "--limit", "1")
        digest, cursor = first["bundle"]["manifest_digest"], first["page"]["next_cursor"]
        self.successful(bundle, "--expect-manifest-digest", digest)
        self.rejected(bundle, "identity_mismatch", "--expect-manifest-digest", "0" * 64)
        self.rejected(bundle, "invalid_cursor", "--cursor", cursor, "--project", "project-a")
        self.rejected(bundle, "invalid_cursor", "--cursor", cursor, "--kind", "sessions")
        self.refresh_manifest(bundle, generated_at="2026-11-12T00:00:00Z")
        self.rejected(bundle, "identity_mismatch", "--cursor", cursor)
        self.rejected(bundle, "identity_mismatch", "--expect-manifest-digest", digest)

    def test_corrupt_members_symlink_and_wal_are_rejected_without_echo(self):
        bundle = self.make_bundle()
        path = bundle / "export.json"; saved = path.read_bytes()
        path.write_text("private diagnostic text")
        failed = self.rejected(bundle, "invalid_bundle")
        self.assertNotIn("private diagnostic text", failed.stdout + failed.stderr)
        path.write_bytes(saved)
        snapshot = bundle / "snapshot.db"; original = snapshot.read_bytes()
        snapshot.write_bytes(b"broken sqlite")
        self.rejected(bundle, "invalid_bundle")
        snapshot.write_bytes(original)
        alias = bundle / "alias.db"; snapshot.rename(alias); snapshot.symlink_to(alias)
        self.rejected(bundle, "invalid_bundle")
        snapshot.unlink(); alias.rename(snapshot)
        (bundle / "snapshot.db-wal").write_bytes(b"uncheckpointed")
        self.rejected(bundle, "invalid_bundle")

    def test_bad_requests_have_stable_errors_and_do_not_accept_sql(self):
        bundle = self.make_bundle()
        for flags in (("--limit", "0"), ("--limit", "501"), ("--max-input-bytes", "0"),
                      ("--max-output-bytes", "255"), ("--sql", "SELECT secret FROM private"),
                      ("--project", "x" * 1025)):
            with self.subTest(flags=flags[:1]):
                self.rejected(bundle, "invalid_request", *flags)
        self.rejected(bundle, "invalid_cursor", "--cursor", "not-a-cursor")
        self.rejected(self.root / "missing", "not_found")

    def test_access_denied_and_cancelled_reads_reclaim_capture(self):
        bundle = self.make_bundle()
        path = bundle / "snapshot.db"
        original = path.stat().st_mode & 0o777
        path.chmod(0)
        try:
            self.rejected(bundle, "access_denied")
        finally:
            path.chmod(original)
        self.env["BUNDLE_READER_CANCEL"] = "1"
        self.rejected(bundle, "cancelled")

    def test_capture_checks_growth_during_read_and_detects_source_replacement(self):
        source = self.root / "growing"; source.write_bytes(b"0123456789")
        info = copy.copy(source.stat())
        with mock.patch.object(bundle_read.os, "fstat", return_value=mock.Mock(st_mode=info.st_mode, st_size=0)):
            with self.assertRaises(bundle_read.BundleReadError) as failure:
                bundle_read._capture(source, self.root / "capture", 3)
        self.assertEqual(failure.exception.code, "input_limit")
        self.assertEqual((self.root / "capture").stat().st_size, 0)
        bundle = self.make_bundle()
        original = bundle_read._capture
        def replacing(source, destination, remaining):
            count = original(source, destination, remaining)
            if source.name == "snapshot.db":
                (bundle / "export.json").write_text("replacement")
            return count
        with mock.patch.object(bundle_read, "_capture", side_effect=replacing):
            with self.assertRaises(bundle_read.BundleReadError) as failure:
                bundle_read.read_bundle(bundle)
        self.assertEqual(failure.exception.code, "bundle_changed")

    def test_temporary_capture_permissions_and_human_entry(self):
        bundle = self.make_bundle()
        original = exporter.validate_export_manifest
        inspected = []
        def validate(manifest, path):
            inspected.append(path.parent)
            self.assertEqual(path.parent.stat().st_mode & 0o777, 0o700)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual((path.parent / "export.json").stat().st_mode & 0o777, 0o600)
            return original(manifest, path)
        with mock.patch.object(exporter, "validate_export_manifest", side_effect=validate):
            bundle_read.read_bundle(bundle)
        self.assertTrue(inspected)
        self.assertTrue(all(not path.exists() for path in inspected))
        result = self.invoke(bundle, json_mode=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Source coverage is separate", result.stdout)
