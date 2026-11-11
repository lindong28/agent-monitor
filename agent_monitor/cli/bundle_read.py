"""Read a bounded page of rollup, usage or session content from an existing saved bundle."""
import argparse
import base64
from dataclasses import fields
import json
import os
from pathlib import Path
import re
import signal
import stat
import sys
import tempfile

from agent_monitor.accounting import rollup
from agent_monitor.snapshots import exporter, statistics_snapshot
from agent_monitor.views.projections import entry_json

DEFAULT_INPUT_BYTES = 8 * 1024 * 1024
DEFAULT_OUTPUT_BYTES = 256 * 1024
MAX_INPUT_BYTES = 64 * 1024 * 1024
MAX_OUTPUT_BYTES = 4 * 1024 * 1024
MAX_PAGE = 500
MAX_CURSOR_BYTES = 16384


class BundleReadError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise BundleReadError("invalid_request", "Invalid arguments; use agent-monitor bundle-read --help.")


def _stamp(info):
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def _capture(source, destination, remaining):
    """Capture only a regular file, checking the bound during the actual read."""
    fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode):
            raise BundleReadError("invalid_bundle", "Bundle members must be regular files.")
        if before.st_size > remaining:
            raise BundleReadError("input_limit", "Bundle exceeds the input byte budget; no content was returned.")
        count = 0
        out_fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(out_fd, "wb") as output:
            while True:
                chunk = stream.read(min(65536, remaining - count + 1))
                if not chunk:
                    break
                count += len(chunk)
                if count > remaining:
                    raise BundleReadError("input_limit", "Bundle exceeds the input byte budget; no content was returned.")
                output.write(chunk)
        if (_stamp(before) != _stamp(os.fstat(stream.fileno()))
                or _stamp(before) != _stamp(source.lstat()) or count != before.st_size):
            raise BundleReadError("bundle_changed", "Bundle changed during capture; retry the pinned saved material.")
    return count, _stamp(before)


def _encode_cursor(manifest_digest, kind, filters, offset):
    value = {"manifest_digest": manifest_digest, "kind": kind, "filters": filters, "offset": offset}
    token = base64.urlsafe_b64encode(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                              separators=(",", ":")).encode()).decode()
    if len(token) > MAX_CURSOR_BYTES:
        raise BundleReadError("invalid_request", "Combined filters exceed the cursor byte budget.")
    return token


def _decode_cursor(cursor, manifest_digest, kind, filters):
    if cursor is None:
        return 0
    try:
        if not isinstance(cursor, str) or len(cursor.encode()) > MAX_CURSOR_BYTES:
            raise ValueError()
        value = json.loads(base64.b64decode(cursor, altchars=b"-_", validate=True))
        if (not isinstance(value, dict) or set(value) != {"manifest_digest", "kind", "filters", "offset"}
                or value["kind"] != kind or value["filters"] != filters
                or type(value["offset"]) is not int or value["offset"] < 0):
            raise ValueError()
        if value["manifest_digest"] != manifest_digest:
            raise BundleReadError("identity_mismatch", "Cursor belongs to a different saved bundle.")
        return value["offset"]
    except BundleReadError:
        raise
    except (ValueError, TypeError, UnicodeError):
        raise BundleReadError("invalid_cursor", "Cursor is invalid or belongs to another selection.") from None


def _usage_json(entry):
    row = entry_json(entry)
    # Keep the public view's names and every original fact, including TTL cost
    # inputs and message_count (the view calls it usage_event_count).
    row.update({field.name: getattr(entry, field.name) for field in fields(entry) if field.name not in row})
    return row


def read_bundle(bundle, *, kind="usage", project=None, session_id=None, agent=None, cursor=None,
                limit=100, expect_manifest_digest=None, max_input_bytes=DEFAULT_INPUT_BYTES):
    filters = {"project": project, "session_id": session_id, "agent": agent}
    if kind == "rollup" and session_id is not None:
        raise BundleReadError("invalid_request", "Rollup has no session dimension; use usage or sessions for session selection.")
    if (kind not in ("rollup", "usage", "sessions") or type(limit) is not int or not 1 <= limit <= MAX_PAGE
            or type(max_input_bytes) is not int or not 1 <= max_input_bytes <= MAX_INPUT_BYTES
            or any(value is not None and (not isinstance(value, str) or not value
                   or len(value.encode()) > 1024) for value in filters.values())
            or (cursor is not None and (not isinstance(cursor, str) or len(cursor.encode()) > MAX_CURSOR_BYTES))
            or (expect_manifest_digest is not None and (not isinstance(expect_manifest_digest, str)
                or not re.fullmatch(r"[0-9a-f]{64}", expect_manifest_digest)))):
        raise BundleReadError("invalid_request", "Invalid selection or input budget; use bundle-read --help.")
    bundle = Path(bundle)
    source_paths = [bundle / "export.json", bundle / "snapshot.db"]
    # A saved export has no pending WAL frames. Copying just its DB must not
    # hide an invalid source-side WAL from the existing validator.
    wal = bundle / "snapshot.db-wal"
    if wal.exists() and wal.stat().st_size:
        raise BundleReadError("invalid_bundle", "Saved bundle contains pending WAL data.")
    with tempfile.TemporaryDirectory(prefix="agent-monitor-bundle-read-") as directory:
        staging = Path(directory)
        signatures = []
        consumed = 0
        for source in source_paths:
            if source.is_symlink():
                raise BundleReadError("invalid_bundle", "Bundle members must be regular files.")
            count, signature = _capture(source, staging / source.name, max_input_bytes - consumed)
            consumed += count
            signatures.append(signature)
        try:
            manifest = json.loads((staging / "export.json").read_bytes())
            exporter.validate_export_manifest(manifest, staging / "snapshot.db")
            snapshot = statistics_snapshot.read_snapshot(staging / "snapshot.db")
            if kind == "rollup":
                # Read only project-attributed buckets from the validated private
                # copy. The native reader owns ordering and read-only DB access.
                native_rows = rollup._daily_rows_from_db(
                    staging / "snapshot.db", None, usage=False, immutable=True)
                columns = ("date", "agent_id", "project", "model") + rollup.BUCKET_FIELDS
                rollup_rows = [{key: row[key] for key in columns} for row in native_rows]
        except BundleReadError:
            raise
        except Exception:
            raise BundleReadError("invalid_bundle", "Saved manifest or snapshot failed validation; retain the original for diagnosis.") from None
        if (signatures != [_stamp(source.lstat()) for source in source_paths]
                or (wal.exists() and wal.stat().st_size)):
            raise BundleReadError("bundle_changed", "Bundle changed during validation; retry the pinned saved material.")

    digest = manifest["manifest_digest"]
    if expect_manifest_digest is not None and expect_manifest_digest != digest:
        raise BundleReadError("identity_mismatch", "Saved bundle does not match the expected manifest digest.")
    offset = _decode_cursor(cursor, digest, kind, filters)
    coverage = {key: value for key, value in snapshot.items() if key not in ("usage_entries", "session_metadata")}
    data_state = snapshot["state"]
    if kind == "sessions" and snapshot.get("version", 0) < 4:
        data_state = "not_collected"
    if kind == "rollup":
        data_state = "available"
        records = rollup_rows
        coverage["rollup"] = {"state": "available", "table": "daily_rollup",
            "bucket_timezone": manifest["bucket_timezone"], "cost_usd_basis": "known_cost_subtotal",
            "cost_coverage_fields": ["cost_known_count", "entry_count"],
            "row_quality": "not_collected", "cost_provenance": "not_collected",
            "daily_usage_rollup": "excluded_no_project_dimension"}
    else:
        records = snapshot["usage_entries"] if kind == "usage" else snapshot.get("session_metadata", [])
    selected = []
    for row in records:
        get = (lambda key: getattr(row, key)) if kind == "usage" else row.get
        if all(value is None or get("agent_id" if key == "agent" else key) == value
               for key, value in filters.items()):
            selected.append(row)
    if offset > len(selected):
        raise BundleReadError("invalid_cursor", "Cursor offset is outside this saved selection.")
    page = selected[offset:offset + limit]
    next_offset = offset + len(page)
    has_more = next_offset < len(selected)
    return {"version": 1, "status": "ok", "bundle": {key: manifest[key] for key in (
        "manifest_digest", "transfer_digest", "logical_digest", "source_host_identity", "exporter_commit",
        "generated_at", "schema_version")}, "coverage": coverage, "kind": kind, "data_state": data_state,
        "filters": filters, "page": {"offset": offset, "limit": limit, "returned": len(page),
            "total_matching": len(selected), "total_in_section": len(records), "has_more": has_more,
            "next_cursor": _encode_cursor(digest, kind, filters, next_offset) if has_more else None},
        "items": [_usage_json(row) for row in page] if kind == "usage" else page}


def _json_bytes(value, maximum):
    output = bytearray()
    for part in json.JSONEncoder(ensure_ascii=True, allow_nan=False, separators=(",", ":")).iterencode(value):
        chunk = part.encode()
        if len(output) + len(chunk) + 1 > maximum:
            raise BundleReadError("output_limit", "Page exceeds the output byte budget; reduce --limit or change the trusted budget.")
        output.extend(chunk)
    return bytes(output) + b"\n"


def _main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    json_mode = "--json" in argv
    parser = _Parser(prog="agent-monitor bundle-read", description=__doc__)
    parser.add_argument("bundle", help="saved directory containing export.json and snapshot.db")
    parser.add_argument("--kind", choices=("rollup", "usage", "sessions"), default="usage")
    parser.add_argument("--project", help="exact source project value; does not change coverage")
    parser.add_argument("--session-id", help="exact session ID; no ancestry expansion")
    parser.add_argument("--agent", help="exact source agent ID")
    parser.add_argument("--cursor", help="opaque next_cursor from the same bundle and filters")
    parser.add_argument("--limit", type=int, default=100, help="records per page, 1..500 (default: 100)")
    parser.add_argument("--expect-manifest-digest", help="trusted expected lowercase SHA-256 manifest digest")
    parser.add_argument("--max-input-bytes", type=int, default=DEFAULT_INPUT_BYTES,
                        help="combined input byte budget, 1..67108864 (default: 8388608)")
    parser.add_argument("--max-output-bytes", type=int, default=DEFAULT_OUTPUT_BYTES,
                        help="stdout byte budget, 256..4194304 (default: 262144)")
    parser.add_argument("--json", action="store_true", help="emit the full bounded content page as JSON")
    try:
        args = parser.parse_args(argv)
        if not 256 <= args.max_output_bytes <= MAX_OUTPUT_BYTES:
            raise BundleReadError("invalid_request", "Output budget must be between 256 and 4194304 bytes.")
        result = read_bundle(args.bundle, kind=args.kind, project=args.project, session_id=args.session_id,
                             agent=args.agent, cursor=args.cursor, limit=args.limit,
                             expect_manifest_digest=args.expect_manifest_digest, max_input_bytes=args.max_input_bytes)
        if args.json:
            output = _json_bytes(result, args.max_output_bytes)
        else:
            page = result["page"]
            output = (f"Saved {args.kind}: {result['data_state']}; selected page {page['returned']} of "
                      f"{page['total_matching']} matching records ({page['total_in_section']} in this section).\n"
                      f"Bundle manifest: {result['bundle']['manifest_digest']}\n"
                      f"More matching pages: {'yes' if page['has_more'] else 'no'}. "
                      "Source coverage is separate from page completion.\n"
                      "Use --json for preserved content, source coverage and the next cursor.\n").encode()
            if len(output) > args.max_output_bytes:
                raise BundleReadError("output_limit", "Response exceeds the output byte budget.")
        sys.stdout.buffer.write(output)
        return 0
    except FileNotFoundError:
        error = BundleReadError("not_found", "Saved bundle member was not found; no content was returned.")
    except PermissionError:
        error = BundleReadError("access_denied", "Saved bundle read was denied by filesystem permissions.")
    except BundleReadError as exc:
        error = exc
    except Exception:
        error = BundleReadError("read_failed", "Saved bundle could not be read; no content was returned.")
    if json_mode:
        sys.stdout.buffer.write(_json_bytes({"version": 1, "status": "error", "error": {
            "code": error.code, "message": str(error)}}, 256))
    print(f"bundle-read: {error}", file=sys.stderr)
    return 2


def main(argv=None):
    def cancelled(signum, frame):
        raise BundleReadError("cancelled", "Saved bundle read was cancelled; no content was returned.")
    previous = {sig: signal.signal(sig, cancelled) for sig in (signal.SIGTERM, signal.SIGINT)}
    try:
        return _main(argv)
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)


if __name__ == "__main__":
    raise SystemExit(main())
