"""Deploy one fixed release across the selected topology, with resumable receipts."""
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import argparse
import copy
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor

from agent_monitor import hub_config
from agent_monitor.paths import PROJECT_ROOT
from agent_monitor.runtime import deploy, deployment_guard, rollout_node
from agent_monitor.snapshots.machine_config import load_machine_config

ROOT = PROJECT_ROOT
check_reservation = rollout_node.check_reservation


def topology():
    config = load_machine_config()
    members = sorted(m.name for m in config.machines)
    hub = hub_config.configuration()['hub'] if hub_config.enabled() else None
    if hub is not None and members != sorted(hub_config.configuration()['machines']):
        raise deploy.PreflightError('Hub and machine topology differ; reconcile the selected configurations before deployment.')
    if hub is not None and config.self_machine.name != hub:
        raise deploy.PreflightError(f'This is a Hub client. Run agent-monitor service deploy on the configured Hub ({hub}); use --local only for an explicit single-node repair.')
    if hub is None and len(members) > 1:
        raise deploy.PreflightError('Multi-node deployment requires a configured Hub coordinator; configure the Hub or use --local for an explicit single-node repair.')
    return config


def node(machine, action, *, revision=None, release_id=None, observed=None):
    request = {'action': action, 'revision': revision, 'id': release_id}
    if observed is not None:
        request['observed'] = observed
        request['root'] = observed['root']
    if machine.is_self:
        request['root'] = str(ROOT)
    script = Path(rollout_node.__file__).read_text()
    encoded = json.dumps(request)
    if machine.is_self:
        command = [sys.executable, '-c', script, encoded]
    else:
        if machine.ssh_host.startswith('-') or not re.fullmatch(r'(?:[A-Za-z0-9._-]+@)?[A-Za-z0-9._-]+', machine.ssh_host):
            raise ValueError('Invalid SSH destination in machine configuration.')
        command = ['ssh', '-T', '-n', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10',
                   '-o', 'ServerAliveInterval=10', '-o', 'ServerAliveCountMax=2', machine.ssh_host,
                   shlex.join(['python3', '-c', script, encoded])]
    try:
        result = subprocess.run(command, capture_output=True, text=True,
                                stdin=subprocess.DEVNULL, timeout=1900 if action == 'apply' else 60)
        lines = [line[len(rollout_node.RESULT_PREFIX):] for line in result.stdout.splitlines()
                 if line.startswith(rollout_node.RESULT_PREFIX)]
        if len(lines) != 1:
            raise ValueError('Node returned no unique release receipt; inspect SSH access and the installed Monitor entry. A timed-out operation may still hold its node lock.')
        receipt = json.loads(lines[0])
        if result.returncode or not receipt.get('ok'):
            raise ValueError(receipt.get('error') or 'Node command failed; inspect its deployment state.')
        return receipt['observed']
    except (OSError, subprocess.TimeoutExpired):
        raise ValueError('Node connection or command timed out; inspect its deployment lock and state before resuming. No automatic retry was sent.') from None


@contextmanager
def release_lock():
    (ROOT / 'state').mkdir(exist_ok=True)
    path = ROOT / 'state' / 'release.lock'
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise deploy.PreflightError('Another topology release is running on this Hub; wait for its result before resuming.') from None
        yield
    finally:
        os.close(fd)


def inspect_all(config):
    def inspect(machine):
        try:
            value = node(machine, 'inspect')
            if value['hub'] is not None:
                if value['machine'] != machine.name or value['members'] != sorted(config.by_name):
                    raise ValueError('Installed Hub identity or topology differs from the coordinator configuration.')
                if value['hub'] != hub_config.configuration()['hub']:
                    raise ValueError('Node is configured for another Hub.')
            # Standalone exporters (including Linux) need no Hub role. Their
            # target binding is the configured SSH destination + installed root;
            # their local collection labels and topology remain their own.
            return machine.name, {'observed': value, 'status': 'inspected'}
        except (ValueError, OSError) as error:
            return machine.name, {'status': 'unverified', 'error': str(error)}
    # Read-only SSH probes operate on distinct hosts; no shared mutation.
    with ThreadPoolExecutor(max_workers=min(8, len(config.machines))) as pool:
        return dict(pool.map(inspect, config.machines))


def show_identity(record):
    print(f"Release ID: {record['id']}")
    print(f"Revision: {record['revision']}")
    print("Targets: " + json.dumps(record['targets'], sort_keys=True), flush=True)


def show(record):
    print(f"Topology release {record['revision']}:")
    for name, value in record['nodes'].items():
        error = value.get('cleanup_error') or value.get('error')
        print(f"  {name}: {value['status']}" + (f" — {error}" if error else ''))
    if record['status'] == 'installed':
        print(f"All configured nodes installed the fixed release; verified at {record['verified_at']}. Browser workflows and provider availability were not checked.")
        if record.get('cleanup_pending'):
            print('Reservation cleanup pending: ' + ', '.join(record['cleanup_pending']) + '. Installation will not repeat.')
            print(f"Resume: service release resume --id {record['id']} --expect {record['revision']}")
    else:
        print('Topology release incomplete. Retained node results are in state/release.json; resolve the listed failure before resuming.')
        print(f"Resume: service release resume --id {record['id']} --expect {record['revision']}")


def cleanup(config, path, record):
    """Release completed reservations without rewriting the verified result."""
    for name in list(record['cleanup_pending']):
        value = record['nodes'][name]
        try:
            node(config.by_name[name], 'release', revision=record['revision'],
                 release_id=record['id'], observed=value['observed'])
        except (ValueError, OSError) as error:
            value['cleanup_error'] = str(error)
            rollout_node.write_record(path, record)
            show(record)
            return 1
        value.pop('cleanup_error', None)
        record['cleanup_pending'].remove(name)
        rollout_node.write_record(path, record)
    show(record)
    return 0


def execute(config, expected, *, new_only=False, receipt=False, strict_resume=False):
    path = ROOT / 'state' / 'release.json'
    previous = rollout_node.read_record(path)
    if new_only and previous and (previous.get('status') not in ('installed', 'abandoned') or previous.get('cleanup_pending')):
        raise deploy.PreflightError('A release is unfinished; use service release status, then resume or abandon its exact ID before starting a new release.')
    if previous and previous.get('status') == 'abandoning':
        raise deploy.PreflightError('Release abandonment is unfinished; resume service release abandon first.')
    targets = {m.name: m.ssh_host for m in config.machines}
    if previous and previous.get('status') == 'installed' and previous.get('cleanup_pending'):
        if (expected not in (None, previous['revision']) or previous['targets'] != targets):
            raise deploy.PreflightError('Completed release reservations still need cleanup; rerun its original topology and --expect SHA before starting another release.')
        return cleanup(config, path, previous)
    if previous and previous.get('status') == 'abandoned':
        validate_abandonment(previous)
    active = previous if previous and previous.get('status') not in ('installed', 'abandoned') else None
    if active is not None and expected is None:
        expected = active['revision']
    revision = deploy.preflight(expected)
    if active is not None and (active.get('revision') != revision or active.get('targets') != targets):
        raise deploy.PreflightError('An incomplete release has a different version or topology; preserve state/release.json and reconcile that release before starting another.')
    record = active or {'id': uuid.uuid4().hex, 'revision': revision, 'targets': targets,
                        'status': 'incomplete', 'nodes': {}}
    fresh = inspect_all(config)
    if strict_resume:
        verify_original_nodes(record, fresh)
    completed = set()
    for name, value in fresh.items():
        old = record['nodes'].get(name, {}).get('observed')
        current = value.get('observed')
        if old is not None and current is not None:
            if (record['nodes'][name]['status'] == 'installed' and old == current
                    and current['revision'] == revision and False not in current['runtime'].values()):
                completed.add(name)
            if any(old[k] != current[k] for k in ('root', 'machine', 'members', 'hub', 'config')):
                value = {'status': 'unverified', 'error': 'Node identity or configuration changed since this release; reconcile before resuming.', 'observed': old}
        # Keep the original binding even when the fresh read failed.
        if old is not None and 'observed' not in value:
            value['observed'] = old
        value['required_runtime'] = record['nodes'].get(name, {}).get('required_runtime',
            [k for k, v in (current or {}).get('runtime', {}).items() if v is not None])
        if name in completed:
            value['status'] = 'installed'
        record['nodes'][name] = value
    if active is None and previous and previous.get('status') == 'installed':
        archive_installed(previous)
    rollout_node.write_record(path, record)
    if receipt:
        show_identity(record)
    if any(n['status'] == 'unverified' for n in record['nodes'].values()):
        show(record)
        return 1

    def operation(machine, action):
        value = record['nodes'][machine.name]
        try:
            value['observed'] = node(machine, action, revision=revision, release_id=record['id'],
                                     observed=value['observed'])
            if action == 'apply':
                value['observed'] = node(machine, 'inspect')
                if (value['observed']['revision'] != revision or
                        any(value['observed']['runtime'].get(k) is not True for k in value['required_runtime'])):
                    raise ValueError('Installed source or required runtime identity did not converge; inspect service status and resume.')
            value['status'] = ('installed' if action == 'reserve' and machine.name in completed
                               else {'reserve': 'reserved', 'apply': 'installed', 'release': 'installed'}[action])
            value.pop('error', None)
            ok = True
        except (ValueError, OSError) as error:
            value['status'] = 'unverified'
            value['error'] = str(error)
            ok = False
        rollout_node.write_record(path, record)
        return ok

    # Reserve the entire set before changing source on any node. Keep each
    # reservation on failure so an independent repair cannot insert a new SHA.
    for machine in config.machines:
        if not operation(machine, 'reserve'):
            show(record)
            return 1
    # The snapshot contract requires upgrading readers before exporters.
    # A failure stops the transition and preserves its remaining work.
    ordered = sorted(config.machines, key=lambda m: not m.is_self)
    for machine in ordered:
        if machine.name in completed:
            record['nodes'][machine.name]['status'] = 'installed'
            rollout_node.write_record(path, record)
            continue
        if not operation(machine, 'apply'):
            show(record)
            return 1
    # Re-read every node, including those successful on earlier attempts.
    current = inspect_all(config)
    for name, value in current.items():
        observed = value.get('observed')
        target = record['nodes'][name]['observed']
        if observed is None or observed != target or observed['revision'] != revision:
            record['nodes'][name].update(status='unverified', error='Final node identity was not confirmed; resume this release.')
    if any(n['status'] != 'installed' for n in record['nodes'].values()):
        rollout_node.write_record(path, record)
        show(record)
        return 1
    # Commit the verification while every node still holds this release's
    # reservation. Later repairs do not retroactively change this observation.
    record['status'] = 'installed'
    record['verified_at'] = datetime.now(timezone.utc).isoformat()
    record['cleanup_pending'] = [m.name for m in config.machines]
    rollout_node.write_record(path, record)
    return cleanup(config, path, record)


def run(expected=None, *, check_only=False, installer_args=(), new_only=False):
    if new_only:
        if not expected or check_only or installer_args:
            raise deploy.PreflightError('--new requires --expect and cannot be combined with --check-only or installer arguments.')
        with release_lock():
            config = topology()
            if len(config.machines) < 2:
                raise deploy.PreflightError('--new requires a persistent multi-node topology release; standalone installations use service deploy without --new.')
            return execute(config, expected, new_only=True, receipt=True)
    previous = rollout_node.read_record(ROOT / 'state' / 'release.json')
    if previous and previous.get('status') == 'abandoning':
        raise deploy.PreflightError('Release abandonment is unfinished; resume service release abandon with its original ID, revision and reason.')
    if previous and previous.get('status') == 'abandoned':
        validate_abandonment(previous)
    config = topology()
    if len(config.machines) == 1:
        def admission():
            head = deploy.preflight(expected)
            check_reservation(ROOT, head)
            return head
        if check_only:
            print(f'Source verified: published release {admission()}')
            print('Source check only; installation and service health were not checked.')
            return 0
        return deployment_guard.run_install(ROOT, installer_args, preflight=admission)
    if installer_args:
        raise deploy.PreflightError('Topology deployment updates all installed components. Use --local for a scoped repair or install.sh --no-services for staging.')
    if check_only:
        revision = deploy.preflight(expected)
        values = inspect_all(config)
        print(f'Topology source inventory for release {revision}:')
        for name, value in values.items():
            print(f"  {name}: {value.get('observed', {}).get('revision', 'unverified')}" +
                  (f" — {value['error']}" if value.get('error') else ''))
        print('Inventory only; no reservations, installation, service health or browser workflows were checked.')
        return int(any(v['status'] == 'unverified' for v in values.values()))
    with release_lock():
        return execute(config, expected)


# The current receipt owns resumption; history preserves the original record.
def history_path(identity):
    if not isinstance(identity, str) or not re.fullmatch(r"[0-9a-f]{32}", identity):
        raise deploy.PreflightError('Release ID must be the exact 32-character ID shown by service release status.')
    return ROOT / 'state' / 'release-history' / (identity + '.json')


def validate_release(record):
    if not isinstance(record, dict):
        raise deploy.PreflightError('Release record is unavailable or invalid; preserve it for inspection.')
    history_path(record.get('id'))
    if not isinstance(record.get('revision'), str) or not re.fullmatch(r'[0-9a-f]{40}', record['revision']):
        raise deploy.PreflightError('Release revision is invalid; no reservations were released.')
    targets, nodes = record.get('targets'), record.get('nodes')
    if not isinstance(targets, dict) or not targets or not isinstance(nodes, dict) or set(targets) != set(nodes):
        raise deploy.PreflightError('Release target/node records are incomplete; no reservations were released.')
    missing = any(isinstance(value, dict) and value.get('observed') is None for value in nodes.values())
    if missing and any(isinstance(value, dict) and value.get('status') in ('reserved', 'installed') for value in nodes.values()):
        raise deploy.PreflightError('Missing observations conflict with recorded reservation history; nothing was released.')
    for name, value in nodes.items():
        if not isinstance(value, dict) or value.get('status') not in ('inspected', 'unverified', 'reserved', 'installed'):
            raise deploy.PreflightError('Unknown node release state; no reservations were released.')
        observed = value.get('observed')
        if observed is None:
            # execute() preserves every previous observation, and reserves only
            # after all first inspections succeed. Missing observations cannot
            # therefore be evidence of a reservation from this coordinator.
            if value['status'] != 'unverified' or not value.get('error') or value.get('required_runtime'):
                raise deploy.PreflightError('Node reservation history is inconsistent; no reservations were released.')
        elif not isinstance(observed, dict) or not isinstance(observed.get('root'), str) or not Path(observed['root']).is_absolute():
            raise deploy.PreflightError('Original node installation is unverified; no reservations were released.')


def archive_installed(record):
    """Preserve the old receipt before a new release replaces current."""
    validate_release(record)
    if record.get('status') != 'installed' or record.get('cleanup_pending'):
        raise deploy.PreflightError('Only a completed, cleaned release can be archived for replacement.')
    path = history_path(record['id'])
    if path.parent.is_symlink() or (path.parent.exists() and not path.parent.is_dir()) or path.is_symlink():
        raise deploy.PreflightError('Release history must be a real directory with regular receipt files.')
    view = {'original': record, 'abandonment': None}
    existing = rollout_node.read_record(path)
    if existing is not None and existing != view:
        raise deploy.PreflightError('Release history conflicts with the completed receipt; current was preserved.')
    if existing is None:
        path.parent.mkdir(parents=True, exist_ok=True)
        rollout_node.write_record(path, view)


def resume(identity, expected):
    with release_lock():
        record = rollout_node.read_record(ROOT / 'state' / 'release.json')
        validate_release(record)
        if record['id'] != identity or record['revision'] != expected:
            raise deploy.PreflightError('Release ID or revision does not match the current receipt; nothing was resumed.')
        if record.get('status') not in ('incomplete', 'installed'):
            raise deploy.PreflightError('Only an incomplete or installed release can be resumed; abandonment cannot resume installation.')
        deploy.check_local()
        config = topology()
        if len(config.machines) < 2 or record['targets'] != {m.name: m.ssh_host for m in config.machines}:
            raise deploy.PreflightError('Restore the original multi-node topology before resuming this release.')
        if record['status'] == 'installed':
            verify_original_nodes(record, inspect_all(config))
            show_identity(record)
            if record.get('cleanup_pending'):
                return cleanup(config, ROOT / 'state' / 'release.json', record)
            show(record)
            return 0
        return execute(config, expected, strict_resume=True, receipt=True)


def verify_original_nodes(record, fresh):
    """Refuse an unknown or changed binding before rewriting any progress."""
    for name, value in fresh.items():
        current = value.get('observed')
        old = record['nodes'][name].get('observed')
        if current is None or (old is not None and any(old.get(k) != current.get(k) for k in ('root', 'machine', 'members', 'hub', 'config'))):
            raise deploy.PreflightError('Original node identity or configuration could not be confirmed; current receipt was preserved.')


def validate_progress(progress, record):
    if not isinstance(progress, dict) or not isinstance(progress.get('reason'), str) or not progress['reason'].strip() or not isinstance(progress.get('started_at'), str):
        raise deploy.PreflightError('Abandonment identity fields are invalid; preserve its records.')
    pending, results, errors = (progress.get(key) for key in ('pending', 'results', 'errors'))
    if not isinstance(pending, list) or not all(isinstance(name, str) for name in pending) or not isinstance(results, dict) or not isinstance(errors, dict):
        raise deploy.PreflightError('Abandonment progress fields are invalid; preserve its records.')
    names = set(record['targets'])
    if len(set(pending)) != len(pending) or set(pending) & set(results) or set(pending) | set(results) != names or not set(errors) <= set(pending):
        raise deploy.PreflightError('Abandonment progress does not cover the exact original nodes.')
    for name, result in results.items():
        expected = 'not_reserved' if record['nodes'][name].get('observed') is None else 'released'
        if result != expected:
            raise deploy.PreflightError('Abandonment result conflicts with the original node history.')
    if progress.get('finished_at') and pending:
        raise deploy.PreflightError('Completed abandonment still has pending nodes.')


def validate_abandonment(record):
    validate_release(record)
    archive = rollout_node.read_record(history_path(record['id']))
    progress = record.get('abandonment')
    if not archive or not isinstance(archive.get('original'), dict) or not isinstance(progress, dict):
        raise deploy.PreflightError('Abandonment archive is missing or invalid; preserve the current receipt.')
    original = copy.deepcopy(record)
    original.pop('abandonment', None)
    original['status'] = archive['original'].get('status')
    if archive['original'] != original:
        raise deploy.PreflightError('Abandonment archive conflicts with the original release; no reservations were released.')
    validate_progress(progress, record)
    pending = progress['pending']
    # History can be one write ahead after a failed current-receipt write.
    archived = archive.get('abandonment', {})
    validate_progress(archived, record)
    if any(archived.get(key) != progress.get(key) for key in ('reason', 'started_at')):
        raise deploy.PreflightError('Abandonment archive identity conflicts with the current receipt.')
    if not set(archived.get('pending', [])) <= set(pending):
        raise deploy.PreflightError('Abandonment archive progress conflicts with the current receipt.')
    if record['status'] == 'abandoned' and (pending or not progress.get('finished_at') or archived != progress):
        raise deploy.PreflightError('Abandonment is not durably complete; preserve its records.')
    return archive


def release_status(identity=None):
    record = rollout_node.read_record(ROOT / 'state' / 'release.json')
    if identity is not None and (not record or record.get('id') != identity):
        archive = rollout_node.read_record(history_path(identity))
        if archive is None:
            raise deploy.PreflightError('Release ID was not found in the current receipt or history.')
        return archive
    if record is None:
        return {'original': None, 'abandonment': None}
    original = copy.deepcopy(record)
    progress = original.pop('abandonment', None)
    return {'original': original, 'abandonment': progress}


def show_release(view):
    record, progress = view['original'], view.get('abandonment')
    if record is None:
        print('No topology release receipt is recorded. Service health was not checked.')
        return
    state = ('abandoned' if progress.get('finished_at') else 'abandoning') if progress else record['status']
    if record.get('cleanup_pending'):
        state += ' (reservation cleanup pending)'
    print(f"Release {record['id']} @ {record['revision']}: {state}")
    print('Targets: ' + json.dumps(record['targets'], sort_keys=True))
    if progress:
        print('Reason: ' + progress['reason'])
        pending = set(progress['pending'])
        for name in record['targets']:
            result = 'pending' if name in pending else progress['results'].get(name, 'released')
            print(f"  {name}: {result}" + (f" — {progress['errors'][name]}" if name in progress['errors'] else ''))
        if pending:
            print(f"Resume: service release abandon --id {record['id']} --expect {record['revision']} --reason " + shlex.quote(progress['reason']))
        else:
            print('Reservations released; use service deploy for the next published version. Source and services were not rolled back.')
        print('History: ' + str(history_path(record['id'])))
    else:
        for name, value in record.get('nodes', {}).items():
            error = value.get('cleanup_error') or value.get('error')
            print(f"  {name}: {value['status']}" + (f" — {error}" if error else ''))
        if record.get('cleanup_pending'):
            print('Reservation cleanup pending: ' + ', '.join(record['cleanup_pending']))
            print(f"Resume cleanup: service release resume --id {record['id']} --expect {record['revision']}; installation will not repeat.")
    print('Recorded release state only; current service health and browser workflows were not checked.')


def abandon(identity, revision, reason):
    if not reason.strip():
        raise deploy.PreflightError('A nonempty abandonment reason is required.')
    with release_lock():
        path = ROOT / 'state' / 'release.json'
        record = rollout_node.read_record(path)
        validate_release(record)
        if record['id'] != identity or record['revision'] != revision:
            raise deploy.PreflightError('Release ID/revision changed; inspect service release status before acting.')
        if record['status'] not in ('incomplete', 'abandoning', 'abandoned'):
            raise deploy.PreflightError('Only incomplete releases can be abandoned; completed reservation cleanup uses service deploy with the original revision.')
        archive_path = history_path(identity)
        if record['status'] in ('abandoning', 'abandoned'):
            archive = validate_abandonment(record)
            if record['abandonment']['reason'] != reason:
                raise deploy.PreflightError('Resume with the original abandonment reason.')
            if record['status'] == 'abandoned':
                show_release(release_status())
                return 0
        else:
            archive = {'original': copy.deepcopy(record), 'abandonment': None}
            existing = rollout_node.read_record(archive_path)
            if existing is not None and existing.get('original') != record:
                raise deploy.PreflightError('History already contains a different release record; nothing was released.')
            if existing is not None:
                validate_progress(existing.get('abandonment'), record)
                if existing['abandonment']['reason'] != reason or existing['abandonment']['results']:
                    raise deploy.PreflightError('History conflicts with the original abandonment intent.')
        deploy.check_local()  # Primary clean main; its SHA may be ahead of the old release.
        config = topology()
        if {m.name: m.ssh_host for m in config.machines} != record['targets']:
            raise deploy.PreflightError('Restore the original target/SSH topology before abandoning this release; nothing was released.')
        if record['status'] == 'incomplete':
            record['abandonment'] = {'reason': reason, 'started_at': datetime.now(timezone.utc).isoformat(),
                'pending': list(record['targets']), 'results': {}, 'errors': {}}
            if existing is not None:
                record['abandonment'] = existing['abandonment']
            record['status'] = 'abandoning'
        progress = record['abandonment']
        archive_path.parent.mkdir(exist_ok=True)
        if archive_path.parent.is_symlink():
            raise deploy.PreflightError('Release history must be a real directory, not a symlink.')
        def persist():
            archive['abandonment'] = copy.deepcopy(progress)
            rollout_node.write_record(archive_path, archive)
            rollout_node.write_record(path, record)
        persist()  # Both durable records precede the first reservation release.
        for name in list(progress['pending']):
            observed = record['nodes'][name].get('observed')
            try:
                if observed is not None:
                    node(config.by_name[name], 'release', revision=revision, release_id=identity, observed=observed)
                progress['results'][name] = 'released' if observed is not None else 'not_reserved'
                progress['pending'].remove(name)
                progress['errors'].pop(name, None)
            except (ValueError, OSError) as error:
                progress['errors'][name] = str(error)
            persist()
        if not progress['pending']:
            progress['finished_at'] = datetime.now(timezone.utc).isoformat()
            record['status'] = 'abandoned'
            persist()
        show_release(release_status())
        return int(bool(progress['pending']))


def main(argv=None):
    parser = argparse.ArgumentParser(description='Inspect, resume or explicitly abandon a topology release without rolling back source or services.')
    commands = parser.add_subparsers(dest='action', required=True)
    status = commands.add_parser('status', help='read a current or historical local release receipt')
    status.add_argument('--id', help='read a retained historical release ID')
    status.add_argument('--json', action='store_true', help='print the complete receipt as JSON')
    continuing = commands.add_parser('resume', help='resume only the matching current release, or finish its cleanup')
    continuing.add_argument('--id', required=True, help='exact current release ID')
    continuing.add_argument('--expect', required=True, help='exact current release revision')
    cancel = commands.add_parser('abandon', help='archive an incomplete release and release only its matching reservations')
    cancel.add_argument('--id', required=True, help='exact current release ID')
    cancel.add_argument('--expect', required=True, help='exact current release revision')
    cancel.add_argument('--reason', required=True, help='record why this release is being abandoned')
    args = parser.parse_args(argv)
    try:
        if args.action == 'resume':
            return resume(args.id, args.expect)
        if args.action == 'abandon':
            return abandon(args.id, args.expect, args.reason)
        view = release_status(args.id)
        if args.json:
            print(json.dumps(view, sort_keys=True))
        else:
            show_release(view)
        return 0
    except (ValueError, OSError) as error:
        print(f'Release coordination failed: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
