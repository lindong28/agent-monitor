"""Rebuild .venv at its original path, retaining a recoverable runtime backup."""
from agent_monitor import hub_config
import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import plistlib
import signal
import subprocess
import sys
import tempfile
import time

import deploy
import deployment_guard
import local_web
import process_identity
import service_runtime

from project_paths import PROJECT_ROOT

ROOT = PROJECT_ROOT
LABELS = ('com.agent-monitor.rollup', 'com.agent-monitor.hub', 'com.agent-monitor.connector')


def launch(*args):
    return subprocess.run(['launchctl', *args], capture_output=True, text=True, timeout=15)


def loaded(job):
    result = launch('print', f'gui/{os.getuid()}/{job["label"]}')
    if result.returncode == 113:
        return None
    programs = [line.strip().partition(' = ')[2] for line in result.stdout.splitlines()
                if line.strip().startswith('program = ')]
    expected_program = job['definition']['ProgramArguments'][0] if job['definition']['ProgramArguments'] else None
    if (result.returncode or job['definition'].get('Program', expected_program) != expected_program
            or programs != [expected_program]
            or service_runtime.arguments(result.stdout) != job['definition']['ProgramArguments']):
        raise ValueError(f'{job["label"]}: loaded ownership/state is unverified; preserved.')
    return result.stdout


def capture_jobs():
    if sys.platform != 'darwin':
        return []
    import connector_service
    import rollup_service
    jobs = []
    for label in LABELS:
        path = Path.home() / 'Library/LaunchAgents' / (label + '.plist')
        if label == rollup_service.LABEL:
            definition = rollup_service.owned_definition(path)
        elif label == connector_service.LABEL:
            definition = connector_service.owned_definition(path)
        else:
            definition = plistlib.loads(path.read_bytes()) if path.exists() else None
            if definition and (definition.get('Label') != label or definition.get('ProgramArguments') !=
                               [str(ROOT / '.venv/bin/python'), str(ROOT / 'hub.py'), 'serve']):
                raise ValueError('Hub startup definition ownership is unverified; preserved.')
        job = {'label': label, 'path': str(path), 'definition': definition or {'ProgramArguments': []}}
        info = loaded(job)
        if info is not None:
            if label == rollup_service.LABEL and any(line.strip().startswith('pid = ') for line in info.splitlines()):
                raise ValueError('Collection is running; wait for it to finish before rebuilding. Nothing was stopped.')
            jobs.append(job)
    return jobs


def stop_job(job):
    if loaded(job) is None:
        return
    if launch('bootout', f'gui/{os.getuid()}/{job["label"]}').returncode:
        raise ValueError(f'{job["label"]}: stopping failed; definition retained.')
    if loaded(job) is not None:
        raise ValueError(f'{job["label"]}: still loaded; runtime was not moved.')


def start_job(job):
    path = Path(job['path'])
    if plistlib.loads(path.read_bytes()) != job['definition']:
        raise ValueError(f'{job["label"]}: startup definition changed; inspect before restoring.')
    if loaded(job) is None and launch('bootstrap', f'gui/{os.getuid()}', str(path)).returncode:
        raise ValueError(f'{job["label"]}: restoring startup failed; definition retained.')
    if loaded(job) is None:
        raise ValueError(f'{job["label"]}: startup is unverified.')
    if job['label'] == 'com.agent-monitor.hub':
        reader = lambda: service_runtime.read_health(f'http://127.0.0.1:{hub_config.configuration()["port"]}/api/health?asset_watch=1')
    elif job['label'] == 'com.agent-monitor.connector':
        import connector_service
        reader = connector_service.health
    else:
        return  # A loaded periodic schedule is not a completed collection.
    signature = job['definition'].get('EnvironmentVariables', {}).get('AGENT_MONITOR_DEPLOYMENT_SIGNATURE')
    service_runtime.wait_for_identity(reader, ROOT, signature or service_runtime.signature(ROOT))


def stop_components(manifest):
    errors = []
    for job in manifest['jobs']:
        try:
            stop_job(job)
        except (OSError, ValueError, subprocess.SubprocessError):
            errors.append(job['label'])
    if manifest['local_web']:
        try:
            # The runtime may be missing or corrupt. Only the external controller
            # and recorded OS process identity may be dependencies of rollback.
            state = ROOT / 'state'
            process_identity.stop(state, ROOT)
            for name in ('pid', 'port', 'pid-identity.json'):
                (state / name).unlink(missing_ok=True)
        except (OSError, ValueError, subprocess.SubprocessError):
            errors.append('local-web')
    if errors:
        raise ValueError('Could not stop: ' + ', '.join(errors) + '; runtime retained.')


def restore_components(manifest):
    errors = []
    # Start each original component even if another fails; do not strand siblings.
    for job in manifest['jobs']:
        try:
            start_job(job)
        except (OSError, ValueError, subprocess.SubprocessError):
            errors.append(job['label'])
    if manifest['local_web']:
        try:
            local_web.restore(ROOT, manifest['local_web'])
        except (OSError, ValueError, subprocess.SubprocessError):
            errors.append('local-web')
    if errors:
        raise ValueError('Restoration not confirmed for: ' + ', '.join(errors) + '; inspect service status and logs.')


def no_runtime_users():
    result = subprocess.run(['ps', '-axo', 'pid=,uid=,comm='], capture_output=True, text=True, timeout=10)
    if result.returncode:
        raise ValueError('Cannot verify remaining runtime users; runtime retained.')
    # comm identifies candidates without guessing quoting in ps command text.
    # Read argv/environment privately, then resolve relative argv against that
    # process's cwd, or a bare executable against its captured PATH.
    runtime = ROOT / '.venv'
    def inside(path):
        return Path(os.path.abspath(path)).is_relative_to(runtime)
    for line in result.stdout.splitlines():
        fields = line.strip().split(None, 2)
        if len(fields) != 3 or not fields[0].isdigit() or not fields[1].lstrip('-').isdigit():
            raise ValueError('Process inventory is unverified; runtime retained.')
        pid, uid, program = int(fields[0]), int(fields[1]), fields[2]
        if pid == os.getpid() or uid != os.getuid():
            continue
        if not (inside(program) or Path(program).name.lower().startswith('python')):
            continue
        try:
            argv, env = local_web.process_context(pid)
            first = Path(argv[0])
            if first.is_absolute():
                executable = first
            else:
                if sys.platform == 'linux':
                    cwd = Path(os.readlink(f'/proc/{pid}/cwd'))
                else:
                    directory = subprocess.run(['/usr/sbin/lsof', '-a', '-p', str(pid), '-d', 'cwd', '-Fn'], capture_output=True, text=True, timeout=10)
                    paths = [row[1:] for row in directory.stdout.splitlines() if row.startswith('n/')]
                    if directory.returncode or len(paths) != 1:
                        raise ValueError('Process working directory is unverified; runtime retained.')
                    cwd = Path(paths[0])
                if '/' in argv[0]:
                    executable = cwd / first
                else:
                    candidates = [(Path(part) if Path(part).is_absolute() else cwd / part) / first
                                  for part in env['PATH'].split(os.pathsep)]
                    executable = next((path for path in candidates if os.access(path, os.X_OK)), None)
                    if executable is None:
                        raise ValueError('Process interpreter path is unverified; runtime retained.')
            # Framework Python replaces argv[0]; its launcher preserves the
            # original venv path in the kernel's initial environment.
            launcher = env.get('__PYVENV_LAUNCHER__') if sys.platform == 'darwin' else None
            if (inside(executable) or (env.get('VIRTUAL_ENV') and inside(env['VIRTUAL_ENV']))
                    or (launcher and inside(launcher))):
                raise ValueError('Processes still use this runtime; stop the owning CLI/foreground task and retry. Runtime retained.')
        except (OSError, ValueError, subprocess.SubprocessError):
            if process_identity.identity(pid) is not None:
                raise


def save(directory, manifest, phase):
    manifest['phase'] = phase
    fd, temporary = tempfile.mkstemp(dir=directory, prefix='.transaction-')
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(manifest, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, directory / 'transaction.json')
    finally:
        Path(temporary).unlink(missing_ok=True)


@contextmanager
def selected_python(python):
    previous = os.environ.get('AGENT_MONITOR_PYTHON')
    os.environ['AGENT_MONITOR_PYTHON'] = str(python)
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop('AGENT_MONITOR_PYTHON', None)
        else:
            os.environ['AGENT_MONITOR_PYTHON'] = previous


def rollback(directory, manifest):
    backup = directory / 'previous-venv'
    if backup.exists():
        stop_components(manifest)
        no_runtime_users()
        current = ROOT / '.venv'
        if current.exists():
            current.rename(directory / ('failed-venv-' + str(time.time_ns())))
        backup.rename(current)
    restore_components(manifest)
    save(directory, manifest, 'rolled-back')


def rebuild(python, expected=None):
    runtime = ROOT / '.venv'
    if runtime.is_symlink() or not runtime.is_dir():
        raise ValueError('Expected an existing, non-symlink .venv; use ./install.sh for a fresh installation.')
    with deployment_guard.deployment_lock(ROOT), local_web.account_quiescence(ROOT):
        head = deploy.preflight(expected)
        baseline = deployment_guard.source_snapshot(ROOT)
        jobs = capture_jobs()
        recipe = local_web.capture(ROOT)
        parent = ROOT / '.runtime/runtime-backups'
        if (ROOT / '.runtime').is_symlink() or parent.is_symlink():
            raise ValueError('Runtime backup directory must not be a symlink; nothing was stopped.')
        parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if parent.resolve().stat().st_dev != runtime.stat().st_dev:
            raise ValueError('Runtime backup must be on the same filesystem; nothing was stopped.')
        directory = Path(tempfile.mkdtemp(prefix='rebuild-', dir=parent))
        manifest = {'root': str(ROOT), 'revision': head, 'jobs': jobs, 'local_web': recipe}
        save(directory, manifest, 'prepared')
        print(f'Runtime backup/recovery record: {directory}', flush=True)
        try:
            stop_components(manifest)
            no_runtime_users()
            deployment_guard.verify_source(ROOT, baseline)
            runtime.rename(directory / 'previous-venv')
            save(directory, manifest, 'building')
            with selected_python(python):
                result = deployment_guard.run_install(ROOT, ['--no-services'], preflight=lambda: deploy.preflight(head))
            if result:
                raise ValueError('Runtime installer failed.')
            deployment_guard.verify_source(ROOT, baseline)
            save(directory, manifest, 'restoring')
            restore_components(manifest)
            save(directory, manifest, 'complete')
        except (Exception, KeyboardInterrupt) as original:
            try:
                rollback(directory, manifest)
            except (Exception, KeyboardInterrupt):
                raise ValueError(f'Rebuild failed; restoration is incomplete. Preserve {directory}; run this command with --restore {directory.name}, then service status.') from None
            raise ValueError(f'Rebuild failed; original runtime/services restored. Record: {directory}. Inspect installer output before retrying.') from original
        print('Runtime rebuilt at its original path; original running components restored. Collection, browser interaction and provider calls were not tested.')
        print(f'Previous runtime retained for rollback: {directory}; it is not runnable at the backup path.')
        return 0


def recover(name):
    if Path(name).name != name or not name.startswith('rebuild-'):
        raise ValueError('--restore requires the printed rebuild directory name.')
    directory = ROOT / '.runtime/runtime-backups' / name
    if directory.is_symlink():
        raise ValueError('Recovery directory must not be a symlink.')
    manifest = json.loads((directory / 'transaction.json').read_text())
    if manifest.get('root') != str(ROOT):
        raise ValueError('Recovery record belongs to another checkout.')
    with deployment_guard.deployment_lock(ROOT), local_web.account_quiescence(ROOT):
        deploy.preflight(manifest['revision'])
        rollback(directory, manifest)
    print('Original runtime/services restored; run service status and verify your normal dashboard workflow.')
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--python', required=True, help='absolute external Python >=3.10 executable')
    parser.add_argument('--expect')
    parser.add_argument('--restore', help='printed rebuild directory name to restore')
    args = parser.parse_args(argv)
    python = Path(args.python)
    try:
        if sys.version_info < (3, 10) or not python.is_absolute() or not os.access(python, os.X_OK):
            raise ValueError('Select an executable absolute Python >=3.10 path.')
        if (Path(sys.prefix).resolve() == (ROOT / '.venv').resolve() or python.is_relative_to(ROOT / '.venv')
                or python.resolve().is_relative_to((ROOT / '.venv').resolve())):
            raise ValueError('Run the controller with the selected external interpreter, outside project .venv.')
        result = subprocess.run([str(python), '-c', 'import sys; sys.exit(sys.version_info < (3, 10))'], timeout=10)
        if result.returncode:
            raise ValueError('Selected Python must be >=3.10.')
        def interrupted(signum, frame):
            raise KeyboardInterrupt
        signal.signal(signal.SIGTERM, interrupted)
        return recover(args.restore) if args.restore else rebuild(python, args.expect)
    except (OSError, ValueError, deploy.PreflightError, subprocess.SubprocessError) as error:
        print(f'Runtime rebuild failed: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
