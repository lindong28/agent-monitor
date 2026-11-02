"""Deploy one fixed release across the selected topology, with resumable receipts."""
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
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


def show(record):
    print(f"Topology release {record['revision']}:")
    for name, value in record['nodes'].items():
        error = value.get('cleanup_error') or value.get('error')
        print(f"  {name}: {value['status']}" + (f" — {error}" if error else ''))
    if record['status'] == 'installed':
        print(f"All configured nodes installed the fixed release; verified at {record['verified_at']}. Browser workflows and provider availability were not checked.")
        if record.get('cleanup_pending'):
            print('Reservation cleanup pending: ' + ', '.join(record['cleanup_pending']) + '. Rerun the same release to finish cleanup; installation will not repeat.')
    else:
        print('Topology release incomplete. Retained node results are in state/release.json; resolve the listed failure and rerun service deploy with the same --expect SHA.')


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


def execute(config, expected):
    path = ROOT / 'state' / 'release.json'
    previous = rollout_node.read_record(path)
    targets = {m.name: m.ssh_host for m in config.machines}
    if previous and previous.get('status') == 'installed' and previous.get('cleanup_pending'):
        if (expected not in (None, previous['revision']) or previous['targets'] != targets):
            raise deploy.PreflightError('Completed release reservations still need cleanup; rerun its original topology and --expect SHA before starting another release.')
        return cleanup(config, path, previous)
    active = previous if previous and previous.get('status') != 'installed' else None
    if active is not None and expected is None:
        expected = active['revision']
    revision = deploy.preflight(expected)
    if active is not None and (active.get('revision') != revision or active.get('targets') != targets):
        raise deploy.PreflightError('An incomplete release has a different version or topology; preserve state/release.json and reconcile that release before starting another.')
    record = active or {'id': uuid.uuid4().hex, 'revision': revision, 'targets': targets,
                        'status': 'incomplete', 'nodes': {}}
    fresh = inspect_all(config)
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
        record['nodes'][name] = value
    rollout_node.write_record(path, record)
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
            value['status'] = {'reserve': 'reserved', 'apply': 'installed', 'release': 'installed'}[action]
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


def run(expected=None, *, check_only=False, installer_args=()):
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
