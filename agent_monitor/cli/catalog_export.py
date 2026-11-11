"""Offline, metadata-only projection of a validated Monitor export bundle."""
import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from urllib.parse import urlsplit

from agent_monitor.snapshots import exporter


class CatalogExportError(ValueError):
    pass


def _identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9:._@+-]{0,199}", value):
        raise CatalogExportError("invalid catalog identifier")
    return value


def _uri(value):
    parsed = urlsplit(value)
    if (not value or len(value) > 2048 or not parsed.scheme or
            parsed.username or parsed.password or parsed.query or parsed.fragment or
            any(ord(c) < 33 for c in value)):
        raise CatalogExportError("material/source URI must be absolute and contain no credentials, query or fragment")
    return value


def _id(kind, values):
    digest = hashlib.sha256(json.dumps(values, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return "monitor:" + kind + ":" + digest


def project_bundle(bundle, *, instance_id, organization, project_id, source_uri, material_uri):
    for value in (instance_id, organization, project_id):
        _identifier(value)
    _uri(source_uri)
    _uri(material_uri)
    bundle = Path(bundle)
    manifest_path, snapshot_path = bundle / "export.json", bundle / "snapshot.db"
    if any(p.is_symlink() or not p.is_file() for p in (manifest_path, snapshot_path)):
        raise CatalogExportError("bundle must contain regular export.json and snapshot.db files")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        exporter.validate_export_manifest(manifest, snapshot_path)
        version = _identifier(manifest["exporter_commit"])
        observed = manifest["generated_at"]
        if len(observed) > 64 or datetime.fromisoformat(observed.replace("Z", "+00:00")).utcoffset() is None:
            raise ValueError("timezone required")
    except Exception as exc:
        # Validators can contain material values in errors; do not echo those.
        raise CatalogExportError("bundle manifest or snapshot validation failed") from exc
    identity = [instance_id, organization, project_id, manifest["source_host_identity"], source_uri, version]
    source_id = _id("source", identity)
    family_id = _id("family", [source_id, "statistics-export", manifest["schema_version"]])
    record_id = _id("record", [family_id, manifest["manifest_digest"], manifest["transfer_digest"], material_uri])
    common = {"visibility": "private", "schema_version": 1, "confirmation": "proposal",
              "description": "metadata_only; original export materials remain owned and authorized by Agent Monitor."}
    def entity(identifier, kind, name, payload, uri):
        return dict(common, id=identifier, kind=kind, name=name, payload=payload,
                    sources=[{"uri": uri, "note": "metadata_only"}])
    basis = ("project rows + usage rows" if manifest["schema_version"] == 2 else "legacy daily rollup rows")
    summary = ("Validated saved statistics export: %s %s since %s; bucket timezone %s; "
               "snapshot schema %s. Export time is not a provider refresh time. "
               "Provider coverage, current quota and actual billing are unknown; no account/session details copied.") % (
                   manifest["row_count"], basis, manifest["data_start_date"], manifest["bucket_timezone"], manifest["schema_version"])
    return {"version": 1, "instance_id": instance_id, "organization": organization, "entities": [
        entity(source_id, "runtime_source", "Agent Monitor saved statistics", {
            "project_id": project_id, "locator": source_uri, "source_version": version}, source_uri),
        entity(family_id, "runtime_family", "Saved statistics exports", {
            "project_id": project_id, "source_id": source_id, "source_version": version,
            "purpose": "metadata_only: validated export inventory; statistics and original material are read at source."}, source_uri),
        entity(record_id, "runtime_record", "Saved statistics observation", {
            "project_id": project_id, "family_id": family_id, "category": "observation",
            "source_version": version, "observed_at": observed, "summary": summary,
            "availability": {"state": "available", "reason": "Local bundle validated; remote URI access is not verified."},
            "locator": material_uri, "input_ids": []}, material_uri)]}


def write_projection(output, payload):
    data = (json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()
    try:
        fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except OSError as exc:
        raise CatalogExportError("output must be a new writable file") from exc
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
    except OSError as exc:
        Path(output).unlink(missing_ok=True)
        raise CatalogExportError("output write failed; no complete projection created") from exc


def main():
    parser = argparse.ArgumentParser(prog="agent-monitor catalog-export", description=__doc__)
    parser.add_argument("bundle", help="existing export bundle directory")
    for name in ("out", "instance-id", "organization", "project-id", "source-uri", "material-uri"):
        parser.add_argument("--" + name, required=True)
    args = vars(parser.parse_args())
    output = args.pop("out")
    try:
        payload = project_bundle(**args)
        write_projection(output, payload)
    except (CatalogExportError, OSError, ValueError):
        print("Catalog export refused: invalid bundle, scope, URI or output; no import performed.", file=sys.stderr)
        raise SystemExit(2)
    print("Created private metadata-only catalog file (3 entities); no import performed.")


if __name__ == "__main__":
    main()
