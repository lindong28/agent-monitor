import json
import copy
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import exporter
import generation
import rollup
from machine_config import DEFAULT_CONFIG_PATH, load_machine_config, machine_config_fingerprint


_SSH_HOST = re.compile(r"^(?:[A-Za-z0-9._-]+@)?[A-Za-z0-9._-]+$")
_REMOTE_TEMP = re.compile(r"^/tmp/agent-monitor-export\.[A-Za-z0-9]+$")
_SSH_PREFIX = ("ssh", "-n", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10")
_RSYNC_PREFIX = ("rsync", "-rzc", "-e", "ssh -T -o BatchMode=yes -o ConnectTimeout=10")
_REMOTE_REAPER = (
    "find /tmp -maxdepth 1 -type d -user \"$(id -u)\" "
    "-name 'agent-monitor-export.*' -mmin +60 -exec rm -rf -- {} +"
)
REMOTE_CLEANUP_TIMEOUT = 10


class SyncError(RuntimeError):
    pass


class TransferValidationError(SyncError):
    pass


@dataclass(frozen=True)
class SyncResult:
    generation: object = None
    error: str = None


def sync_machine(
    machine,
    *,
    db_path=rollup.DEFAULT_DB_PATH,
    root=generation.DEFAULT_GENERATIONS_ROOT,
    timeout=180,
    runner=subprocess.run,
    export_kwargs=None,
    quota_refresh=True,
    accept_first_use_ssh_target=False,
):
    root = Path(root)
    root.parent.mkdir(parents=True, exist_ok=True)
    generation.recover_generation_state(machine.name, root=root)
    export_kwargs = dict(export_kwargs or {})
    if not quota_refresh:
        export_kwargs["quota_refresh"] = False
    with tempfile.TemporaryDirectory(
        prefix=".sync-%s-" % machine.name,
        dir=root.parent,
    ) as temporary:
        local_bundle = Path(temporary) / "bundle"
        if machine.is_self:
            local_identity = generation.self_certified_host_identity()
            configured_identity = export_kwargs.pop("source_host_identity", None)
            if configured_identity not in (None, local_identity):
                raise SyncError(
                    "self export source identity does not match this owning machine"
                )
            if accept_first_use_ssh_target:
                raise SyncError(
                    "self machine does not use SSH trust on first use"
                )
            source_manifest = exporter.export_bundle(
                db_path,
                local_bundle,
                source_host_identity=local_identity,
                **export_kwargs,
            )
            accept_first_use = True
        else:
            current = generation.read_current_generation(machine.name, root=root)
            if current is not None:
                try:
                    local_bundle.mkdir()
                    shutil.copyfile(current.db_path, local_bundle / "snapshot.db")
                finally:
                    current.close()
            source_manifest = _pull_remote_export(
                machine.ssh_host,
                local_bundle,
                timeout=timeout,
                runner=runner,
                quota_refresh=quota_refresh,
            )
            accept_first_use = accept_first_use_ssh_target
        return install_export_bundle(
            machine,
            local_bundle,
            expected_manifest=source_manifest,
            accept_first_use=accept_first_use,
            root=root,
        )


def sync_all(
    *,
    config_path=DEFAULT_CONFIG_PATH,
    root=generation.DEFAULT_GENERATIONS_ROOT,
    db_path=rollup.DEFAULT_DB_PATH,
    timeout=180,
    runner=subprocess.run,
    accept_first_use_ssh_targets=None,
    quota_refresh=True,
    on_complete=None,
    progressive=False,
    on_statistics=None,
):
    config = load_machine_config(config_path, retirement_root=root)
    results = {}
    def collect(machine):
        try:
            current = sync_machine(
                machine,
                db_path=db_path,
                root=root,
                timeout=timeout,
                runner=runner,
                quota_refresh=quota_refresh and not progressive,
                accept_first_use_ssh_target=machine.name
                in (accept_first_use_ssh_targets or ()),
            )
            if progressive and quota_refresh:
                try:
                    if on_statistics is not None:
                        on_statistics(machine.name, SyncResult(generation=current))
                    updated = sync_quota(machine, current, root=root, timeout=timeout, runner=runner)
                finally:
                    current.close()
                current = updated
        except subprocess.TimeoutExpired:
            return SyncResult(error="timeout")
        except subprocess.CalledProcessError as exc:
            return SyncResult(error=_command_failure_reason(exc))
        except (
            OSError,
            ValueError,
            subprocess.SubprocessError,
            SyncError,
            exporter.ExportError,
            generation.GenerationError,
        ) as exc:
            return SyncResult(error=str(exc))
        else:
            return SyncResult(generation=current)
    # Each worker targets a distinct host and publishes only that host's generation.
    with ThreadPoolExecutor(max_workers=min(8, len(config.machines))) as pool:
        futures = {pool.submit(collect, machine): machine.name for machine in config.machines}
        for future in as_completed(futures):
            name = futures[future]
            results[name] = future.result()
            if on_complete is not None:
                on_complete(name, results[name])
    return {machine.name: results[machine.name] for machine in config.machines}


def sync_quota(machine, current, *, root=generation.DEFAULT_GENERATIONS_ROOT,
               timeout=180, runner=subprocess.run):
    """Publish new quota against the statistics just admitted for this machine."""
    import hub
    try:
        if machine.is_self:
            payload = exporter.export_quota()
        else:
            result = _run(runner, [*_SSH_PREFIX, machine.ssh_host,
                "~/.local/bin/agent-monitor export --quota-only" + (" --gui-quota" if hub.enabled() else "")],
                time.monotonic() + timeout)
            payload = json.loads(result.stdout)
        if (not isinstance(payload, dict)
                or set(payload) != {"schema_version", "source_host_identity", "exporter_commit", "rate_limits"}
                or payload["schema_version"] != 1
                or payload["source_host_identity"] != current.meta["source_host_identity"]
                or not isinstance(payload["exporter_commit"], str)
                or not re.fullmatch(r"[0-9a-f]{40}", payload["exporter_commit"])
                or not isinstance(payload["rate_limits"], dict)
                or set(payload["rate_limits"]) != {"claude", "codex"}
                or not all(isinstance(block, dict) for block in payload["rate_limits"].values())):
            raise SyncError("quota response identity or schema does not match this machine")
        limits = payload["rate_limits"]
    except (OSError, ValueError, subprocess.SubprocessError, SyncError, exporter.ExportError) as exc:
        reason = _command_failure_reason(exc) if isinstance(exc, subprocess.CalledProcessError) else str(exc)
        limits = copy.deepcopy(current.meta.get("rate_limits") or {})
        for provider in ("claude", "codex"):
            block = limits.get(provider)
            if not isinstance(block, dict):
                block = {}
                limits[provider] = block
            block["refresh_error"] = "配额更新失败：" + reason
            block["signed_out"] = False
    # Keep statistics provenance and its observation time. Quota has its own
    # per-provider updated_at; only the immutable generation identity changes.
    meta = copy.deepcopy(current.meta)
    meta["rate_limits"] = limits
    meta["published_at"] = None
    meta["generation_id"] = generation._generation_id(meta)
    return generation.publish_generation(machine.name, current.db_path, meta, root=root)


def _command_failure_reason(exc):
    """The reason a remote export failed is what it printed last, not its argv.

    `str(CalledProcessError)` is the command line plus an exit status; the
    exporter's refusal ("Export refused: ... dependency pin") or the last line
    of its traceback is on stderr. That line is what the reader can act on.
    """
    lines = [line.strip() for line in (exc.stderr or "").splitlines() if line.strip()]
    if lines:
        return lines[-1]
    return "%s exited with status %s" % (
        Path(exc.cmd[0]).name if isinstance(exc.cmd, (list, tuple)) and exc.cmd else "command",
        exc.returncode,
    )


def _pull_remote_export(ssh_host, local_bundle, *, timeout, runner, quota_refresh=True):
    import hub
    if not isinstance(timeout, (int, float)) or timeout <= 0:
        raise ValueError("sync timeout must be positive")
    if not isinstance(ssh_host, str) or not _SSH_HOST.fullmatch(ssh_host):
        raise SyncError("ssh_host is not a safe SSH destination")
    deadline = time.monotonic() + timeout
    remote_temp = None
    try:
        _run(
            runner,
            [*_SSH_PREFIX, ssh_host, _REMOTE_REAPER],
            deadline,
        )
        created = _run(
            runner,
            [*_SSH_PREFIX, ssh_host, "mktemp -d /tmp/agent-monitor-export.XXXXXXXX"],
            deadline,
        )
        remote_temp = created.stdout.strip()
        if not _REMOTE_TEMP.fullmatch(remote_temp):
            raise SyncError("remote mktemp returned an unsafe path")
        exported = _run(
            runner,
            [
                *_SSH_PREFIX,
                ssh_host,
                "~/.local/bin/agent-monitor export --out %s/bundle%s" % (
                    remote_temp, (" --gui-quota" if hub.enabled() else "") if quota_refresh else " --cached-quota"
                ),
            ],
            deadline,
        )
        _run(
            runner,
            [*_RSYNC_PREFIX, "%s:%s/bundle/" % (ssh_host, remote_temp), str(local_bundle) + "/"],
            deadline,
        )
        try:
            source_manifest = json.loads(exported.stdout)
        except (TypeError, ValueError) as exc:
            raise SyncError("remote exporter did not return one JSON manifest") from exc
        if not isinstance(source_manifest, dict):
            raise SyncError("remote exporter did not return one JSON manifest")
        return source_manifest
    finally:
        if remote_temp is not None and _REMOTE_TEMP.fullmatch(remote_temp):
            primary_failed = sys.exc_info()[0] is not None
            try:
                runner(
                    [*_SSH_PREFIX, ssh_host, "rm -rf -- %s" % remote_temp],
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=REMOTE_CLEANUP_TIMEOUT,
                )
            except (OSError, subprocess.SubprocessError) as exc:
                if not primary_failed:
                    raise SyncError("remote export cleanup failed") from exc


def _run(runner, args, deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise subprocess.TimeoutExpired(args, 0)
    return runner(
        args,
        check=True,
        capture_output=True,
        text=True,
        timeout=remaining,
    )


def install_export_bundle(
    machine,
    bundle_path,
    *,
    expected_manifest,
    accept_first_use=False,
    root=generation.DEFAULT_GENERATIONS_ROOT,
):
    bundle_path = Path(bundle_path)
    snapshot_path = bundle_path / "snapshot.db"
    manifest_path = bundle_path / "export.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        exporter.validate_export_manifest(expected_manifest, snapshot_path)
        if manifest != expected_manifest:
            exporter.validate_export_manifest(manifest, snapshot_path)
    except (OSError, ValueError, exporter.ExportError) as exc:
        raise TransferValidationError(str(exc)) from exc
    if manifest != expected_manifest:
        raise TransferValidationError(
            "received export manifest does not match the sending exporter's manifest"
        )
    try:
        generation.bind_source_identity(
            machine.name,
            manifest["source_host_identity"],
            accept_first_use=accept_first_use,
            root=root,
        )
    except generation.GenerationError as exc:
        raise TransferValidationError(str(exc)) from exc

    meta = generation.build_generation_meta(
        snapshot_path,
        machine_config_fingerprint=machine_config_fingerprint(machine),
        source_host_identity=manifest["source_host_identity"],
        aliases=manifest["aliases"],
        rate_limits=manifest["rate_limits"],
        exporter_commit=manifest["exporter_commit"],
        generated_at=manifest["generated_at"],
    )
    if meta["transfer_digest"] != manifest["transfer_digest"]:
        raise TransferValidationError("transfer_digest changed while installing export")
    for field in (
        "data_start_date",
        "bucket_timezone",
        "row_count",
        "metric_totals",
        "logical_digest",
    ):
        if meta[field] != manifest[field]:
            raise TransferValidationError("%s changed while installing export" % field)
    return generation.publish_generation(machine.name, snapshot_path, meta, root=root)
