from agent_monitor import hub_config
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import time
import uuid
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from agent_monitor import file_io
from agent_monitor.snapshots import generation
from agent_monitor.accounting import rollup
from agent_monitor.snapshots import statistics_snapshot
from agent_monitor.quota import quota_service; from agent_monitor import storage
from agent_monitor.source_inventory import authority_pathspecs

from agent_monitor.paths import PROJECT_ROOT


ROOT = PROJECT_ROOT
EXPORT_SCHEMA_VERSION = 2
SUPPORTED_EXPORT_SCHEMA_VERSIONS = {1, EXPORT_SCHEMA_VERSION}
_MANIFEST_FIELDS = {
    "schema_version",
    "source_host_identity",
    "aliases",
    "rate_limits",
    "exporter_commit",
    "generated_at",
    "transfer_digest",
    "data_start_date",
    "bucket_timezone",
    "row_count",
    "metric_totals",
    "logical_digest",
    "manifest_digest",
}
_MANIFEST_V2_FIELDS = {
    "legacy_fallback_bucket_dimensions",
    "legacy_fallback_bucket_count",
    "metric_totals_basis",
    "project_row_count",
    "usage_row_count",
}
_RUNTIME_AUTHORITY_PATHS = authority_pathspecs()


class ExportError(RuntimeError):
    pass


class ExportSpaceError(ExportError):
    pass


def exporter_version():
    head = subprocess.run(
        ["git", "-C", str(ROOT), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
        timeout=5,
    )
    commit = head.stdout.strip()
    if len(commit) != 40 or any(character not in "0123456789abcdef" for character in commit):
        raise ExportError("exporter checkout did not report a full commit SHA")
    status = subprocess.run(
        [
            "git",
            "-C",
            str(ROOT),
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
            "--",
            *_RUNTIME_AUTHORITY_PATHS,
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=5,
    )
    if status.stdout:
        raise ExportError(
            "exporter runtime authority differs from HEAD; commit or remove those changes before export"
        )
    return commit


def export_quota(*, gui_quota=False, blocked_accounts=None):
    """Collect quota without rebuilding or transferring a statistics snapshot."""
    commit = exporter_version()
    limits = (_gui_rate_limits(blocked_accounts=blocked_accounts) if gui_quota and hub_config.enabled()
              else _rate_limits(refresh=True, blocked_accounts=blocked_accounts))
    return {
        "schema_version": 1,
        "source_host_identity": generation.self_certified_host_identity(),
        "exporter_commit": commit,
        "rate_limits": limits,
    }


def export_bundle(
    db_path=rollup.DEFAULT_DB_PATH,
    output_path=None,
    *,
    refresh=True,
    entries_loader=None,
    source_host_identity=None,
    generated_at=None,
    rate_limits=None,
    rollup_now=None,
    quota_refresh=True,
    gui_quota=False,
):
    db_path = Path(db_path)
    if output_path is None:
        raise ValueError("output_path is required")
    output_path = Path(output_path)
    if output_path.exists():
        raise FileExistsError(output_path)
    resolved_exporter_commit = exporter_version()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    _cleanup_export_staging(output_path)
    _preflight_space(db_path, output_path.parent)
    staging = output_path.with_name(".%s.staging-%s" % (output_path.name, uuid.uuid4().hex))
    staging.mkdir(mode=0o700)
    try:
        snapshot_path = staging / "snapshot.db"
        loaded_entries = None
        with rollup.rollup_lock(db_path):
            if refresh:
                loaded_entries = (entries_loader or rollup.load_all_entries)()
                kwargs = {"entries_loader": lambda: loaded_entries}
                if rollup_now is not None:
                    kwargs["now"] = rollup_now
                rollup.run(db_path=db_path, **kwargs)
            _vacuum_into_locked(db_path, snapshot_path)

        if loaded_entries is not None:
            statistics_snapshot._write_snapshot_content(
                snapshot_path, loaded_entries,
                observed_at=generated_at,
            )

        signature = _snapshot_signature(snapshot_path)
        transfer_digest = _file_digest(snapshot_path)
        stats = _validated_snapshot_stats(snapshot_path)
        _require_snapshot_unchanged(snapshot_path, signature)
        if rate_limits is None and gui_quota and refresh and quota_refresh:
            if hub_config.enabled():
                rate_limits = _gui_rate_limits()
        manifest = {
            "schema_version": stats["schema_version"],
            "source_host_identity": source_host_identity or generation.self_certified_host_identity(),
            # daily_rollup has no source-path column. Current repository observations
            # cannot prove which historical aggregate row came from a path, so v1
            # exports no alias coverage rather than self-declaring it.
            "aliases": [],
            "rate_limits": _rate_limits(refresh=refresh and quota_refresh) if rate_limits is None else rate_limits,
            "exporter_commit": resolved_exporter_commit,
            "generated_at": generated_at or _timestamp(),
            "transfer_digest": transfer_digest,
            **stats,
        }
        manifest["manifest_digest"] = manifest_digest(manifest)
        _validate_manifest_shape(manifest)
        _validate_manifest_facts(manifest, stats, transfer_digest)
        _write_json(staging / "export.json", manifest)
        file_io.fsync_file(snapshot_path)
        file_io.fsync_directory(staging)
        _require_snapshot_unchanged(snapshot_path, signature)
        os.replace(staging, output_path)
        file_io.fsync_directory(output_path.parent)
        return manifest
    except BaseException:
        if staging.exists():
            shutil.rmtree(staging)
            file_io.fsync_directory(staging.parent)
        raise


def validate_export_manifest(manifest, snapshot_path):
    _validate_manifest_shape(manifest)
    transfer_digest = _file_digest(snapshot_path)
    if transfer_digest != manifest["transfer_digest"]:
        raise ExportError("transfer_digest does not match snapshot.db")
    stats = _validated_snapshot_stats(snapshot_path)
    return _validate_manifest_facts(manifest, stats, transfer_digest)


def _validate_manifest_shape(manifest):
    if not isinstance(manifest, dict):
        raise ExportError("export manifest fields do not match schema")
    schema_version = manifest.get("schema_version")
    if schema_version not in SUPPORTED_EXPORT_SCHEMA_VERSIONS:
        raise ExportError("unsupported export schema_version")
    expected_fields = (
        _MANIFEST_FIELDS | _MANIFEST_V2_FIELDS
        if schema_version == EXPORT_SCHEMA_VERSION
        else _MANIFEST_FIELDS
    )
    if set(manifest) != expected_fields:
        raise ExportError("export manifest fields do not match schema")
    if manifest["manifest_digest"] != manifest_digest(manifest):
        raise ExportError("manifest_digest does not match export metadata")
    if manifest["aliases"] != []:
        raise ExportError("export schema cannot substantiate alias row lineage")
    if not isinstance(manifest["rate_limits"], dict):
        raise ExportError("rate_limits must be an object")


def _validated_snapshot_stats(snapshot_path):
    try:
        statistics_snapshot.validate_snapshot(snapshot_path)
    except statistics_snapshot.StatisticsSnapshotError as exc:
        raise ExportError("statistics extension is invalid") from exc
    return generation.snapshot_stats(snapshot_path)


def _validate_manifest_facts(manifest, stats, transfer_digest):
    if manifest["schema_version"] != stats["schema_version"]:
        raise ExportError("schema_version does not match snapshot.db")
    fields = [
        "data_start_date",
        "bucket_timezone",
        "row_count",
        "metric_totals",
        "logical_digest",
    ]
    if manifest["schema_version"] == EXPORT_SCHEMA_VERSION:
        fields.extend(sorted(_MANIFEST_V2_FIELDS))
    for field in fields:
        if manifest[field] != stats[field]:
            raise ExportError("%s does not match snapshot.db" % field)
    generation._build_generation_meta(
        stats, transfer_digest,
        machine_config_fingerprint="0" * 64,
        source_host_identity=manifest["source_host_identity"],
        aliases=manifest["aliases"],
        rate_limits=manifest["rate_limits"],
        exporter_commit=manifest["exporter_commit"],
        generated_at=manifest["generated_at"],
    )
    return manifest


def _snapshot_signature(snapshot_path):
    """Local identity only: never a substitute for reading snapshot contents."""
    signature = []
    for path in (Path(snapshot_path), Path(str(snapshot_path) + "-wal")):
        try:
            stat = path.stat()
        except FileNotFoundError:
            signature.append(None)
        else:
            if path != Path(snapshot_path) and stat.st_size:
                raise ExportError("private export snapshot has a nonempty WAL")
            signature.append((stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns))
    if signature[0] is None:
        raise ExportError("private export snapshot is missing")
    return tuple(signature)


def _require_snapshot_unchanged(snapshot_path, signature):
    if _snapshot_signature(snapshot_path) != signature:
        raise ExportError("private export snapshot changed during validation")


def manifest_digest(manifest):
    payload = {
        key: value
        for key, value in manifest.items()
        if key != "manifest_digest"
    }
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _vacuum_into_locked(db_path, output_path):
    db_path = Path(db_path)
    output_path = Path(output_path)
    storage.assert_lock_held(db_path)
    if output_path.exists():
        raise FileExistsError(output_path)
    uri = db_path.resolve().as_uri() + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True, timeout=30)) as conn:
        storage.require_bucket_timezone(conn, db_path)
        conn.execute("VACUUM INTO ?", (str(output_path),))
    file_io.fsync_file(output_path)


def _preflight_space(db_path, destination_parent):
    required = max(_database_bytes(db_path), 1) + 1024 * 1024
    available = shutil.disk_usage(destination_parent).free
    if available < required:
        raise ExportSpaceError(
            "export needs at least %d bytes but only %d are free" % (required, available)
        )


def _database_bytes(db_path):
    total = 0
    for suffix in ("", "-wal"):
        try:
            total += Path(str(db_path) + suffix).stat().st_size
        except FileNotFoundError:
            pass
    return total


def _cleanup_export_staging(output_path):
    prefix = ".%s.staging-" % output_path.name
    changed = False
    for child in output_path.parent.iterdir():
        if not child.name.startswith(prefix):
            continue
        if child.is_symlink() or not child.is_dir():
            child.unlink()
        else:
            shutil.rmtree(child)
        changed = True
    if changed:
        file_io.fsync_directory(output_path.parent)


def _gui_rate_limits(*, blocked_accounts=None):
    from agent_monitor.runtime import hub
    return quota_service.gui_rate_limits(
        requester=hub.request_quota_refresh, request_error=hub.QuotaRequestError,
        blocked_accounts=blocked_accounts, root=ROOT,
    )


def _rate_limits(refresh=False, *, blocked_accounts=None):
    return quota_service.rate_limits(
        refresh, blocked_accounts=blocked_accounts, use_cache=hub_config.enabled(), root=ROOT,
    )


def recover_codex_quota(expected_label, pause_id, *, expected_id=None):
    """Compatibility entrypoint for the account-owning machine's recovery CLI."""
    return quota_service.recover_codex_quota(expected_label, pause_id, expected_id=expected_id, root=ROOT)


def recover_claude_quota(expected_id, expected_label, pause_id):
    """Compatibility entrypoint for callers of the former exporter service."""
    return quota_service.recover_claude_quota(expected_id, expected_label, pause_id, root=ROOT)


def _timestamp():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _file_digest(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path, payload):
    with Path(path).open("x", encoding="utf-8") as handle:
        json.dump(payload, handle, sort_keys=True, separators=(",", ":"))
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
