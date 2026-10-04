import dataclasses
import fcntl
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import time
import uuid
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import generation
import rollup
import quota_refresh
import statistics_snapshot
from parsers import RateLimits
from parsers import accounts, claude_status, codex


ROOT = Path(__file__).resolve().parent
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
_RUNTIME_AUTHORITY_PATHS = (
    ":(glob)*.py",
    ":(glob)parsers/**",
    "pricing.json",
    "agent-monitor",
    "install.sh",
    "requirements.txt",
    ":(glob)lib/**",
)


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


def export_quota(*, gui_quota=False):
    """Collect quota without rebuilding or transferring a statistics snapshot."""
    commit = exporter_version()
    import hub
    limits = _gui_rate_limits() if gui_quota and hub.enabled() else _rate_limits(refresh=True)
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
    ledger_path=None,
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
            # An injected usage source must not accidentally read the host's real
            # gateway ledger. Tests can explicitly inject a synthetic ledger.
            gateway_path = ledger_path
            if gateway_path is None and entries_loader is not None:
                gateway_path = staging / "not-collected-gateway.sqlite3"
            statistics_snapshot.write_snapshot(
                snapshot_path, loaded_entries, ledger_path=gateway_path,
                observed_at=generated_at,
            )

        stats = generation.snapshot_stats(snapshot_path)
        if rate_limits is None and gui_quota and refresh and quota_refresh:
            import hub
            if hub.enabled():
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
            "transfer_digest": _file_digest(snapshot_path),
            **stats,
        }
        manifest["manifest_digest"] = manifest_digest(manifest)
        validate_export_manifest(manifest, snapshot_path)
        _write_json(staging / "export.json", manifest)
        generation._fsync_file(snapshot_path)
        generation._fsync_directory(staging)
        os.replace(staging, output_path)
        generation._fsync_directory(output_path.parent)
        return manifest
    except BaseException:
        if staging.exists():
            shutil.rmtree(staging)
            generation._fsync_directory(staging.parent)
        raise


def validate_export_manifest(manifest, snapshot_path):
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
    if _file_digest(snapshot_path) != manifest["transfer_digest"]:
        raise ExportError("transfer_digest does not match snapshot.db")
    try:
        statistics_snapshot.validate_snapshot(snapshot_path)
    except statistics_snapshot.StatisticsSnapshotError as exc:
        raise ExportError("statistics extension is invalid") from exc
    stats = generation.snapshot_stats(snapshot_path)
    if manifest["schema_version"] != stats["schema_version"]:
        raise ExportError("schema_version does not match snapshot.db")
    fields = [
        "data_start_date",
        "bucket_timezone",
        "row_count",
        "metric_totals",
        "logical_digest",
    ]
    if schema_version == EXPORT_SCHEMA_VERSION:
        fields.extend(sorted(_MANIFEST_V2_FIELDS))
    for field in fields:
        if manifest[field] != stats[field]:
            raise ExportError("%s does not match snapshot.db" % field)
    generation._validate_meta_shape(
        generation.build_generation_meta(
            snapshot_path,
            machine_config_fingerprint="0" * 64,
            source_host_identity=manifest["source_host_identity"],
            aliases=manifest["aliases"],
            rate_limits=manifest["rate_limits"],
            exporter_commit=manifest["exporter_commit"],
            generated_at=manifest["generated_at"],
        )
    )
    return manifest


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
    rollup._assert_lock_held(db_path)
    if output_path.exists():
        raise FileExistsError(output_path)
    uri = db_path.resolve().as_uri() + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True, timeout=30)) as conn:
        rollup._require_bucket_timezone(conn, db_path)
        conn.execute("VACUUM INTO ?", (str(output_path),))
    generation._fsync_file(output_path)


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
        generation._fsync_directory(output_path.parent)


def _gui_rate_limits():
    import hub
    try:
        return hub.request_quota_refresh()
    except hub.QuotaRequestError as exc:
        cached = _read_quota_cache(ROOT / "state" / "quota_snapshot.json")
        result = {}
        for provider, account in (("claude", accounts.claude_account()), ("codex", accounts.codex_account())):
            previous = cached.get(provider)
            if previous and account and accounts.quota_identity(
                provider, previous.get("account_id"), previous.get("account_label")
            ) == accounts.quota_identity(provider, account.account_id, account.label):
                block = dict(previous)
            else:
                block = _rate_limit_block(RateLimits(None, None, None, None), account)
            block["refresh_error"] = str(exc)
            # A block never says "nothing to fix here" and "here is what went
            # wrong" at once. The restored cache can carry a signed-out verdict
            # from an earlier round, and stamping this round's error beside it
            # produces exactly that contradiction — which a consumer has to
            # resolve, and one resolution silently drops the error.
            block["signed_out"] = False
            result[provider] = block
        return result


def _rate_limits(refresh=False):
    import hub
    cache_path = ROOT / "state" / "quota_snapshot.json"
    cached = _read_quota_cache(cache_path) if hub.enabled() else {}

    def read(provider):
        source = claude_status if provider == "claude" else codex
        account = accounts.claude_account() if provider == "claude" else accounts.codex_account()
        if not refresh:
            block = _rate_limit_block(source.load_rate_limits(), account)
            if block is None and account is None:
                # The one case that has to stop returning None. A machine with no
                # credential file and no reading used to publish nothing at all
                # for the provider, and an absent block cannot say which kind of
                # nothing it is — so the server read it as "this exporter is too
                # old to confirm quota refresh" and sent the reader to update an
                # exporter that was already current. An empty block can say it.
                #
                # Only this case. A machine that is signed in but has no reading
                # yet still publishes None, exactly as before: that block would
                # carry no fact the server does not already have, and the shape
                # is one other readers rely on.
                block = _rate_limit_block(RateLimits(None, None, None, None), None)
            previous = cached.get(provider)
            if previous and account and accounts.quota_identity(
                provider, previous.get("account_id"), previous.get("account_label")
            ) == accounts.quota_identity(provider, account.account_id, account.label):
                if block is None or _quota_time(previous) > _quota_time(block):
                    block = previous
                elif "refresh_error" in previous:
                    # A newer local reading does not make another active query.
                    block["refresh_error"] = previous["refresh_error"]
            if block is not None:
                # `refresh_error` is deliberately not defaulted alongside it.
                # Absent means "this export ran no active query"; null means "it
                # ran one and it succeeded" — the exact claim the server reads
                # that key for, and one a passive export must not make.
                #
                # `signed_out` is different: without an active query this path
                # can still read the credential file, so it reports the one form
                # of "not signed in" that is knowable from here. A lapsed
                # credential looks signed in from here, and saying otherwise
                # would be a claim this path cannot support.
                block.setdefault("signed_out", account is None)
            return provider, block
        error = None
        signed_out = False
        try:
            for attempt in range(2):
                try:
                    value = quota_refresh.load_rate_limits(provider, account)
                    break
                except quota_refresh.NotSignedIn:
                    raise
                except (quota_refresh.RefreshError, OSError):
                    if attempt:
                        raise
                    time.sleep(2)
        except quota_refresh.NotSignedIn:
            # A state, not a failure: this machine does not use this provider, or
            # its sign-in has lapsed. It is published as a state so the server can
            # say so, and with no `refresh_error` so nothing downstream reports a
            # fault for the reader to chase.
            #
            # The account stamp, when there is one, stays. It says which account
            # the machine was last signed into, and dropping it would make a
            # lapsed sign-in indistinguishable from a machine that never had one
            # — two facts the reader may well want told apart later, even though
            # neither is a failure today.
            signed_out = True
            value = source.load_rate_limits() or RateLimits(None, None, None, None)
        except (quota_refresh.RefreshError, OSError) as exc:
            error = str(exc) if isinstance(exc, quota_refresh.RefreshError) else "配额读取进程无法启动，请检查这台机器的安装。"
            value = source.load_rate_limits() or RateLimits(None, None, None, None)
        block = _rate_limit_block(value, account)
        block["refresh_error"] = error
        block["signed_out"] = signed_out
        return provider, block

    # There are two independent provider endpoints, each with one bounded child.
    with ThreadPoolExecutor(max_workers=2) as pool:
        result = dict(pool.map(read, ("claude", "codex")))
    if refresh and hub.enabled():
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        with cache_path.with_suffix(".lock").open("a") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            previous = _read_quota_cache(cache_path)
            for provider, block in result.items():
                old = previous.get(provider)
                if block is not None and (old is None or accounts.quota_identity(
                        provider, block.get("account_id"), block.get("account_label")) != accounts.quota_identity(
                        provider, old.get("account_id"), old.get("account_label"))
                                          or _quota_time(block) >= _quota_time(old)):
                    previous[provider] = block
                elif block is not None:
                    # Keep the newer reading, but report this query's outcome —
                    # both halves of it. `{**old, ...}` alone carried the old
                    # `signed_out` forward under the new error, so a machine
                    # that timed out once went on being republished as "not
                    # signed in" every round while its error was dropped.
                    previous[provider] = {
                        **old,
                        "refresh_error": block["refresh_error"],
                        "signed_out": block.get("signed_out", False),
                    }
            staging = cache_path.with_name(cache_path.name + "." + uuid.uuid4().hex)
            try:
                with staging.open("x") as handle:
                    os.chmod(staging, 0o600)
                    json.dump(previous, handle, allow_nan=False)
                    handle.flush()
                    os.fsync(handle.fileno())
                staging.replace(cache_path)
            finally:
                staging.unlink(missing_ok=True)
    return result


def _read_quota_cache(path):
    try:
        data = json.loads(path.read_text())
        if isinstance(data, dict) and set(data) <= {"claude", "codex"}:
            return {key: block for key, block in data.items() if isinstance(block, dict)}
    except (FileNotFoundError, ValueError):
        pass
    return {}


def _quota_time(block):
    try:
        return datetime.fromisoformat(block["updated_at"].replace("Z", "+00:00")).timestamp()
    except (KeyError, TypeError, ValueError, AttributeError):
        return 0


def _rate_limit_block(value, account=None):
    """A quota reading, stamped with the account this machine is signed in as.

    The stamp is what lets the dashboard group readings by account instead of
    picking whichever machine reported last. It is an assumption, not a proof —
    see ADR-024 for the window it is wrong in and why that was accepted.

    Two plans travel here, and they are two different facts, not one fact
    twice: `reading_plan` is the plan the quota reading itself reported, and
    `credential_plan` is what this machine's credential file says right now.
    They disagree whenever the two clocks have drifted — a plan upgrade the
    credential file has not refreshed through, or a reading left behind by a
    previous sign-in — and the page says so rather than picking one and calling
    it the account's plan. Which of the two is the stale one is not knowable
    from the pair, so nothing here claims it. See ADR 20260822-586a.

    Both are published raw, and separately, because the derived value alone
    cannot be undone: a machine whose reading carries no plan falls back to the
    credential one, and the result is then byte-identical to a machine where
    two independent sources happen to agree. Those are not the same state —
    one has a second source and the other has none — and a consumer that
    receives only the derived value has no way back to the difference.

    `account_plan` is the derived display value, co-published for one reason
    only: a server older than these fields reads it and has nothing else to
    show. It is not the authority — this is one writer among several, since
    every machine runs its own exporter at its own version and nothing between
    them checks that a published `account_plan` agrees with the pair beside it.
    The server therefore re-derives it on the way in and falls back to this
    value only when the pair is absent; see `_account_entry`. Once no exporter
    predates the pair, this field has no remaining reason to exist.
    """
    if value is None:
        return None
    block = dataclasses.asdict(value)
    # Parser-side carrier, not a wire field: it exists to reach the lines below
    # under the name the wire uses.
    reading_plan = _text(block.pop("plan", None))
    credential_plan = _text(account.plan) if account else None
    block["account_id"] = account.account_id if account else None
    block["account_label"] = account.label if account else None
    block["account_plan"] = reading_plan or credential_plan
    block["reading_plan"] = reading_plan
    block["credential_plan"] = credential_plan
    return block


def _text(value):
    """A usable string, or None. The last guard before a value reaches the wire.

    Both plan sources already narrow to this, each in its own parser. Repeating
    it at the one place that writes the contract means a future parser cannot
    quietly widen it: a truthy non-string would survive `or`, be published, and
    end up both in a rendered label and in the equality that decides whether the
    two sources disagree.
    """
    return value if isinstance(value, str) and value else None


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
