"""Bounded node operations, also sent to older installations during upgrade.

Only the installed Monitor checkout and its deployment lock are used. No
account, quota, public IP or provider probe is performed here.
"""
import json
import os
from pathlib import Path
import sys
import subprocess


RESERVATION = 'release-reservation.json'
RESULT_PREFIX = 'AGENT_MONITOR_RELEASE_RESULT='


def read_record(path):
    if not path.exists():
        return None
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError('Invalid release record; preserve it and inspect the deployment.')
    return value


def write_record(path, value):
    import tempfile
    fd, temporary = tempfile.mkstemp(prefix='.release-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(value, stream, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def check_reservation(root, revision, rollout_id=None):
    value = read_record(Path(root) / 'state' / RESERVATION)
    if value is not None and (value.get('revision') != revision or
                              (rollout_id is not None and value.get('id') != rollout_id)):
        raise ValueError('Another topology release reserves this node; resume that release before deploying another version.')
    if rollout_id is not None and (value is None or value.get('id') != rollout_id):
        raise ValueError('Topology release reservation is missing; resume from the coordinator.')


def modules(root):
    """Use the installed layout; pre-package releases retain their own owners."""
    import importlib
    packaged = (root / 'agent_monitor/runtime/deploy.py').is_file()
    names = ('deploy', 'deployment_guard', 'service_runtime', 'local_web', 'process_identity', 'connector_service')
    values = {name: importlib.import_module(('agent_monitor.runtime.' if packaged else '') + name) for name in names}
    values['hub_config'] = importlib.import_module('agent_monitor.hub_config' if packaged else 'hub')
    values['machine_config'] = importlib.import_module('agent_monitor.snapshots.machine_config' if packaged else 'machine_config')
    if packaged:
        values['configuration_path'] = importlib.import_module('agent_monitor.paths').configuration_path
    else:
        def configuration_path(name):
            local = root / (name + '.local.json')
            return local if local.exists() or local.is_symlink() else root / (name + '.json')
        values['configuration_path'] = configuration_path
    return values


def configuration_signature(configuration_path, local_machine, normalize):
    """Bind operator settings, not the paths/defaults owned by a source release."""
    import hashlib
    overrides = {}
    for name in ('machines', 'hub'):
        path = configuration_path(name)
        overrides[name] = (json.loads(normalize(path.read_text()))
                           if path.name.endswith('.local.json') else None)
    overrides['local_machine'] = local_machine
    return hashlib.sha256(json.dumps(overrides, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def snapshot(root):
    owners = modules(root)
    hub_config, deploy, service_runtime = (owners[k] for k in ('hub_config', 'deploy', 'service_runtime'))
    load_machine_config = owners['machine_config'].load_machine_config
    configuration_path = owners['configuration_path']
    config = load_machine_config()
    names = sorted(m.name for m in config.machines)
    if hub_config.enabled() and names != sorted(hub_config.configuration()['machines']):
        raise ValueError('Hub and machine topology differ; reconcile the selected configurations.')
    signature_config = configuration_signature(configuration_path, hub_config.local_machine(),
                                               owners['machine_config']._config_json)
    local_web, process_identity, connector_service = (owners[k] for k in ('local_web', 'process_identity', 'connector_service'))
    signature = service_runtime.signature(root)
    runtime = {}
    if hub_config.is_hub():
        health = service_runtime.read_health(f"http://127.0.0.1:{hub_config.configuration()['port']}/api/health?asset_watch=1")
        runtime['web'] = service_runtime.matches(health, root, signature)
    elif process_identity.read_owned(root / 'state', root) is not None:
        runtime['web'] = service_runtime.matches(local_web.health(root), root, signature)
    else:
        runtime['web'] = None
    if sys.platform == 'darwin':
        managed = connector_service.owned_definition(connector_service.paths()[0])
        if managed:
            argv = managed['ProgramArguments']
            expected = connector_service.definition(argv[-3], argv[-1])['EnvironmentVariables']['AGENT_MONITOR_DEPLOYMENT_SIGNATURE']
            runtime['connector'] = service_runtime.matches(connector_service.health(), root, expected)
        else:
            runtime['connector'] = None
    return {'root': str(root), 'revision': deploy.check_local(),
            'machine': config.self_machine.name, 'members': names,
            'hub': hub_config.configuration()['hub'] if hub_config.enabled() else None,
            'config': signature_config, 'signature': signature, 'runtime': runtime}


def perform(request):
    root = Path(request.get('root') or Path('~/.local/bin/agent-monitor').expanduser().resolve().parent).resolve()
    if not (root / 'agent-monitor').is_file():
        raise ValueError('Installed agent-monitor entry is unavailable; inspect this node installation.')
    sys.path.insert(0, str(root))
    os.chdir(root)
    owners = modules(root)
    guard, deploy = owners['deployment_guard'], owners['deploy']
    action = request['action']
    if action == 'inspect':
        return snapshot(root)
    with guard.deployment_lock(root):
        path = root / 'state' / RESERVATION
        if action == 'release':
            # Completion was durably recorded before cleanup. A lost cleanup
            # receipt must be retryable, including after a later local repair.
            if read_record(path) is not None:
                check_reservation(root, request['revision'], request['id'])
                path.unlink()
            return request['observed']
        observed = snapshot(root)
        for key in ('root', 'machine', 'members', 'hub', 'config'):
            if observed[key] != request['observed'][key]:
                raise ValueError('Node identity or configuration changed; inspect and resume the release.')
        reservation = {'id': request['id'], 'revision': request['revision']}
        if action == 'reserve':
            current = read_record(path)
            if current is not None and current != reservation:
                raise ValueError('Node already belongs to another active release; resume its coordinator.')
            write_record(path, reservation)
            return observed
        check_reservation(root, request['revision'], request['id'])
        if action != 'apply':
            raise ValueError('Unknown release action.')
        # Fetch objects without changing another writer's working tree. Only a
        # clean primary main checkout can then move forward to the fixed SHA.
        prepare_source(deploy, request['revision'])
        if deploy.check_local() != request['revision']:
            raise ValueError('Checkout is ahead of the fixed release; refusing to roll it back.')
        # Capture installer diagnostics privately; transport output contains
        # only the bounded receipt, never child logs or credential-bearing URLs.
        log_path = root / 'state' / 'release-install.log'
        fd = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        os.fchmod(fd, 0o600)
        saved = [os.dup(1), os.dup(2)]
        try:
            sys.stdout.flush()
            sys.stderr.flush()
            os.dup2(fd, 1)
            os.dup2(fd, 2)
            result = guard._run([str(root / 'agent-monitor'), 'service', 'deploy', '--local',
                                 '--expect', request['revision'], '--rollout-id', request['id']], root)
        finally:
            for target, old in zip((1, 2), saved):
                os.dup2(old, target)
                os.close(old)
            os.close(fd)
        if result:
            raise ValueError('Local deployment failed; inspect state/release-install.log on this node, then resume the fixed release.')
        if deploy.check_local() != request['revision']:
            raise ValueError('Source changed after installation; release remains incomplete.')
        # The coordinator inspects in a fresh process, so layout migrations
        # never combine already-imported old modules with new source files.
        observed['revision'] = request['revision']
        return observed


def git_predicate(deploy, *args):
    """Keep false (exit 1) separate from an unavailable check on old helpers too."""
    try:
        result = subprocess.run(['git', '-C', str(deploy.ROOT), *args],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                stdin=subprocess.DEVNULL, timeout=30,
                                env={**os.environ, 'GIT_TERMINAL_PROMPT': '0'})
    except subprocess.TimeoutExpired:
        raise ValueError(f'Git {args[0]} timed out after 30 seconds; result is unverified.') from None
    except OSError:
        raise ValueError(f'Git {args[0]} could not start; result is unverified.') from None
    if result.returncode not in (0, 1):
        raise ValueError(f'Git {args[0]} exited {result.returncode}; result is unverified.')
    return result.returncode == 0


def prepare_source(deploy, revision):
    """Verify the live publication separately from downloading missing objects."""
    stage = 'reading live origin/main'
    try:
        rows = deploy.git('ls-remote', '--exit-code', 'origin', 'refs/heads/main').splitlines()
        if len(rows) != 1 or len(rows[0].split()) != 2 or rows[0].split()[1] != 'refs/heads/main':
            raise ValueError('Live origin/main identity is unverified.')
        published = rows[0].split()[0]
        stage = 'obtaining release objects'
        if not all(git_predicate(deploy, 'rev-parse', '--verify', '--quiet', value + '^{commit}')
                   for value in (published, revision)):
            deploy.git('fetch', '--no-tags', 'origin', 'main')
        stage = 'verifying published ancestry'
        deploy.git('merge-base', '--is-ancestor', revision, published)
        stage = 'checking checkout ancestry'
        head = deploy.check_local()
        if not git_predicate(deploy, 'merge-base', '--is-ancestor', head, revision):
            raise ValueError('Checkout is not an ancestor of the fixed release. Preserve local history; see docs/public-usage.md, Transition an existing installation. No source was replaced.') from None
        stage = 'fast-forwarding checkout'
        deploy.git('merge', '--ff-only', revision)
    except (deploy.PreflightError, ValueError) as error:
        raise ValueError(f'{stage}: {error}') from None


def main():
    try:
        result = {'ok': True, 'observed': perform(json.loads(sys.argv[1]))}
    except Exception as error:
        # Only our known diagnostics are safe to return. Third-party exception
        # strings (including Git/SSH failures) can contain credentials.
        safe = type(error) is ValueError or error.__class__.__name__ in {'PreflightError', 'GuardError'}
        result = {'ok': False, 'error': str(error) if safe else
                  f'Node operation could not be verified ({type(error).__name__}); inspect its checkout, configuration and deployment log.'}
    print(RESULT_PREFIX + json.dumps(result), flush=True)
    return 0 if result['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
