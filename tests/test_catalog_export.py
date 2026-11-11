"""Public offline projection with explicit fixtures and denied provider/network access."""
import json
import hashlib
from pathlib import Path
import sqlite3
import subprocess

from tests.artifacts import ArtifactTestCase
import test_cached_export

ROOT = Path(__file__).resolve().parents[1]


class CatalogExportTests(ArtifactTestCase):
    setUp = test_cached_export.CachedExportTests.setUp

    def export_bundle(self):
        bundle = self.root / "bundle"
        result = test_cached_export.CachedExportTests.invoke(self, "--cached-state", "--out", str(bundle))
        self.assertEqual(result.returncode, 0, result.stderr)
        return bundle

    def invoke(self, bundle, out, *extra):
        result = subprocess.run([str(ROOT / "agent-monitor"), "catalog-export", str(bundle),
            "--out", str(out), "--instance-id", "personal-lake", "--organization", "personal",
            "--project-id", "project:monitor", "--source-uri", "ssh://fixture/monitor",
            "--material-uri", "ssh://fixture/exports/observation", *extra],
            env=self.env, capture_output=True, text=True, timeout=30)
        self.assertNotIn("Error in sitecustomize", result.stderr)
        return result

    def test_public_projection_is_deterministic_private_and_minimal(self):
        bundle = self.export_bundle()
        before = {p.name: p.read_bytes() for p in bundle.iterdir()}
        first, second = self.root / "one.json", self.root / "two.json"
        for out in (first, second):
            result = self.invoke(bundle, out)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(out.stat().st_mode & 0o777, 0o600)
        self.assertEqual(first.read_bytes(), second.read_bytes())
        self.assertEqual(before, {p.name: p.read_bytes() for p in bundle.iterdir()})
        data = json.loads(first.read_text())
        self.assertEqual(set(data), {"version", "instance_id", "organization", "entities"})
        self.assertEqual([e["kind"] for e in data["entities"]], ["runtime_source", "runtime_family", "runtime_record"])
        self.assertTrue(all(e["visibility"] == "private" for e in data["entities"]))
        manifest = json.loads((bundle / "export.json").read_text())
        record = data["entities"][2]["payload"]
        self.assertEqual(record["observed_at"], manifest["generated_at"])
        self.assertEqual(record["source_version"], manifest["exporter_commit"])
        self.assertIn("unknown", record["summary"])
        for private in ("fixture-account", "fixture failure", "rate_limits", "metric_totals"):
            self.assertNotIn(private, first.read_text())
        self.assertEqual(self.invoke(bundle, first).returncode, 2)
        self.assertEqual(first.read_bytes(), second.read_bytes())
        third = self.root / "three.json"
        self.assertEqual(self.invoke(bundle, third, "--project-id", "project:other").returncode, 0)
        self.assertNotEqual(data["entities"][0]["id"], json.loads(third.read_text())["entities"][0]["id"])

    def test_invalid_manifest_and_material_refuse_without_sensitive_errors(self):
        bundle = self.export_bundle()
        manifest_path = bundle / "export.json"
        original = manifest_path.read_bytes()
        snapshot = bundle / "snapshot.db"
        for content in (b"not-json SECRET-MARKER", b"[]", original.replace(b'"row_count":', b'"bad_count":')):
            manifest_path.write_bytes(content)
            out = self.root / "refused.json"
            result = self.invoke(bundle, out)
            self.assertEqual(result.returncode, 2)
            self.assertFalse(out.exists())
            self.assertNotIn("SECRET-MARKER", result.stderr)
        from agent_monitor.snapshots import exporter
        for key, value in (("exporter_commit", None), ("exporter_commit", ""),
                           ("generated_at", "2026-11-01T00:00:00")):
            changed = json.loads(original)
            changed[key] = value
            changed["manifest_digest"] = exporter.manifest_digest(changed)
            manifest_path.write_text(json.dumps(changed))
            out = self.root / "invalid-version-time.json"
            self.assertEqual(self.invoke(bundle, out).returncode, 2)
            self.assertFalse(out.exists())
        manifest_path.write_bytes(original)
        snapshot.write_bytes(b"corrupt SECRET-MARKER")
        out = self.root / "corrupt.json"
        self.assertEqual(self.invoke(bundle, out).returncode, 2)
        self.assertFalse(out.exists())
        snapshot.unlink()
        self.assertEqual(self.invoke(bundle, out).returncode, 2)
        self.assertFalse(out.exists())

    def test_scope_and_credential_uris_are_rejected(self):
        bundle = self.export_bundle()
        for flag, value in (("--instance-id", "bad id"), ("--organization", ""),
                            ("--project-id", "../project"), ("--material-uri", "relative/file"),
                            ("--material-uri", "https://host/file?token=SECRET-MARKER"),
                            ("--source-uri", "https://user:SECRET-MARKER@host/")):
            out = self.root / "bad-scope.json"
            result = self.invoke(bundle, out, flag, value)
            self.assertEqual(result.returncode, 2)
            self.assertFalse(out.exists())
            self.assertNotIn("SECRET-MARKER", result.stdout + result.stderr)

    def test_export_identity_tracks_manifest_version_time_and_content(self):
        from agent_monitor.snapshots import exporter
        bundle = self.export_bundle()
        manifest_path = bundle / "export.json"
        manifest = json.loads(manifest_path.read_text())
        ids = []
        for index, (version, stamp) in enumerate((("a" * 40, "2026-11-01T00:00:00Z"),
                                                 ("a" * 40, "2026-11-02T00:00:00Z"),
                                                 ("b" * 40, "2026-11-02T00:00:00Z"))):
            manifest.update(exporter_commit=version, generated_at=stamp)
            manifest["manifest_digest"] = exporter.manifest_digest(manifest)
            manifest_path.write_text(json.dumps(manifest))
            out = self.root / ("version-%s.json" % index)
            self.assertEqual(self.invoke(bundle, out).returncode, 0)
            entities = json.loads(out.read_text())["entities"]
            ids.append([e["id"] for e in entities])
            self.assertEqual(entities[2]["payload"]["observed_at"], stamp)
        self.assertEqual(ids[0][:2], ids[1][:2])
        self.assertNotEqual(ids[0][2], ids[1][2])
        self.assertNotEqual(ids[1][0], ids[2][0])

    def test_symlink_material_and_output_are_not_followed(self):
        bundle = self.export_bundle()
        out = self.root / "output-link"
        target = self.root / "untouched"
        target.write_text("keep")
        out.symlink_to(target)
        self.assertEqual(self.invoke(bundle, out).returncode, 2)
        self.assertEqual(target.read_text(), "keep")
        snapshot = bundle / "snapshot.db"
        real = self.root / "snapshot-copy.db"
        snapshot.rename(real)
        snapshot.symlink_to(real)
        self.assertEqual(self.invoke(bundle, self.root / "new-output").returncode, 2)
        self.assertFalse((self.root / "new-output").exists())

    def refresh_manifest(self, bundle, **changes):
        from agent_monitor.snapshots import exporter, generation
        path, snapshot = bundle / "export.json", bundle / "snapshot.db"
        manifest = json.loads(path.read_text())
        stats = generation.snapshot_stats(snapshot)
        for key in exporter._MANIFEST_V2_FIELDS:
            manifest.pop(key, None)
        manifest.update(stats, transfer_digest=hashlib.sha256(snapshot.read_bytes()).hexdigest(), **changes)
        manifest["manifest_digest"] = exporter.manifest_digest(manifest)
        path.write_text(json.dumps(manifest))
        return manifest

    def projected_record(self, bundle, name, *extra):
        output = self.root / (name + ".json")
        result = self.invoke(bundle, output, *extra)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(output.read_text())["entities"][2]

    def test_comparison_ignores_capture_quota_uri_and_physical_snapshot_bytes(self):
        bundle = self.export_bundle()
        first = self.projected_record(bundle, "original")
        snapshot = bundle / "snapshot.db"
        physical_before = snapshot.read_bytes()
        with sqlite3.connect(snapshot) as connection:
            connection.execute("PRAGMA user_version=7")
        self.assertNotEqual(snapshot.read_bytes(), physical_before)
        self.refresh_manifest(bundle, generated_at="2026-11-12T00:00:00Z", exporter_commit="b" * 40,
                              rate_limits={"codex": {"updated_at": "2026-11-12T00:00:00Z", "primary_used_percent": 42}})
        second = self.projected_record(bundle, "recaptured", "--material-uri", "urn:fixture:second-material",
                                       "--source-uri", "urn:fixture:other-source-entry")
        self.assertEqual(first["payload"]["comparison"], second["payload"]["comparison"])
        self.assertNotEqual(first["id"], second["id"])
        self.assertNotEqual(first["payload"]["source_version"], second["payload"]["source_version"])
        self.assertEqual(set(first["payload"]["comparison"]), {"contract", "scope", "digest"})
        self.assertEqual(first["payload"]["comparison"]["contract"], "agent-monitor.rollup.v1")

    def test_comparison_detects_two_business_changes_and_returns_to_prior_value(self):
        bundle = self.export_bundle()
        original = self.projected_record(bundle, "baseline")["payload"]["comparison"]
        for field in ("input_tokens", "output_tokens"):
            with self.subTest(field=field):
                with sqlite3.connect(bundle / "snapshot.db") as connection:
                    connection.execute("UPDATE daily_usage_rollup SET " + field + "=" + field + "+10")
                self.refresh_manifest(bundle)
                changed = self.projected_record(bundle, field)["payload"]["comparison"]
                self.assertEqual(changed["scope"], original["scope"])
                self.assertNotEqual(changed["digest"], original["digest"])
                with sqlite3.connect(bundle / "snapshot.db") as connection:
                    connection.execute("UPDATE daily_usage_rollup SET " + field + "=" + field + "-10")
                self.refresh_manifest(bundle)
                self.assertEqual(self.projected_record(bundle, field + "-restored")["payload"]["comparison"], original)

    def test_comparison_scope_uses_target_and_real_host_and_keeps_schema_basis(self):
        bundle = self.export_bundle()
        first = self.projected_record(bundle, "scope-original")["payload"]["comparison"]
        for flag in ("--instance-id", "--organization", "--project-id"):
            changed = self.projected_record(bundle, flag[2:], flag, "other")["payload"]["comparison"]
            self.assertNotEqual(changed["scope"], first["scope"])
            self.assertEqual(changed["digest"], first["digest"])
        manifest = json.loads((bundle / "export.json").read_text())
        self.refresh_manifest(bundle, source_host_identity="host-v1:" + "9" * 64)
        other_host = self.projected_record(bundle, "other-host")["payload"]["comparison"]
        self.assertNotEqual(other_host["scope"], first["scope"])
        self.assertEqual(other_host["digest"], first["digest"])
        with sqlite3.connect(bundle / "snapshot.db") as connection:
            connection.execute("DROP TABLE daily_usage_rollup")
            connection.execute("DELETE FROM rollup_meta WHERE key='usage_rollup_schema'")
        self.refresh_manifest(bundle, source_host_identity=manifest["source_host_identity"])
        legacy = self.projected_record(bundle, "legacy")["payload"]["comparison"]
        self.assertEqual(legacy["scope"], first["scope"])
        self.assertEqual(legacy["contract"], first["contract"])
        self.assertNotEqual(legacy["digest"], first["digest"])
