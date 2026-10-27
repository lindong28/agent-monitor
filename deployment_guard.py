"""Cooperative installation lock and source checkpoints, without a release copy.

The installer body is captured once. Other source stays in the checkout: checks
detect persistent changes at boundaries, not writes restored between checks.
"""
import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import signal
import stat
import subprocess
import sys
import tempfile
import time

LOCK_ENV = 'AGENT_MONITOR_DEPLOYMENT_LOCK_FD'
SOURCE_ENV = 'AGENT_MONITOR_DEPLOYMENT_SOURCE_FD'
BODY_MARKER = '# AGENT_MONITOR_INSTALL_BODY\n'
INSTALL_TIMEOUT = 1800


class GuardError(ValueError):
    pass


def _git(root, *args):
    try:
        result = subprocess.run(['git', '-C', str(root), *args], capture_output=True,
                                stdin=subprocess.DEVNULL, timeout=30,
                                env={**os.environ, 'GIT_TERMINAL_PROMPT': '0'})
    except (OSError, subprocess.TimeoutExpired):
        raise GuardError('Source check unavailable; inspect Git access before retrying.') from None
    if result.returncode:
        raise GuardError('Source check failed; inspect repository state before retrying.')
    return result.stdout


def _source_path(relative):
    parts = relative.parts
    return bool(parts) and parts[0] not in ('.git', '.venv', '.runtime', 'state') and '__pycache__' not in parts and parts[:2] != ('web', 'vendor')


def source_snapshot(root):
    root = Path(root).resolve()
    revision = None
    if (root / '.git').exists():
        revision = _git(root, 'rev-parse', 'HEAD').decode().strip()
        names = _git(root, 'ls-files', '--cached', '--others', '--exclude-standard', '-z')
        files = {Path(os.fsdecode(name)) for name in names.split(b'\0') if name}
    else:
        files = set()
        for directory, dirs, names in os.walk(root):
            relative = Path(directory).relative_to(root)
            dirs[:] = [name for name in dirs if _source_path(relative / name)]
            files.update(relative / name for name in names)
    files.update(Path(name + '.local.json') for name in ('machines', 'hub')
                 if (root / (name + '.local.json')).exists() or (root / (name + '.local.json')).is_symlink())
    digest = hashlib.sha256()
    for relative in sorted(path for path in files if _source_path(path)):
        path = root / relative
        digest.update(os.fsencode(relative) + b'\0')
        try:
            info = path.lstat()
        except FileNotFoundError:
            digest.update(b'missing\0')
            continue
        digest.update(str(stat.S_IMODE(info.st_mode)).encode() + b'\0')
        if path.is_symlink():
            digest.update(b'link\0' + os.fsencode(os.readlink(path)) + b'\0')
        if path.is_file():
            digest.update(path.read_bytes())
        elif not path.is_symlink():
            raise GuardError('Unsupported source object; preserve the checkout and inspect it before retrying.')
        digest.update(b'\0')
    return {'root': str(root), 'revision': revision, 'digest': digest.hexdigest()}


def verify_source(root, expected):
    if source_snapshot(root) != expected:
        raise GuardError('Source changed during installation; further changes were stopped. '
                         'Earlier installation steps may remain. Preserve WIP, inspect service status, '
                         'then rerun from the intended source version.')


@contextmanager
def deployment_lock(root, *, inherit=True):
    """Hold the same persistent inode; an inherited FD is checked and locked too."""
    root = Path(root).resolve()
    state = root / 'state'
    state.mkdir(exist_ok=True)
    path = state / 'deployment.lock'
    previous = os.environ.get(LOCK_ENV) if inherit else None
    fd = None
    try:
        if previous is not None:
            try:
                fd = os.dup(int(previous))
                held, target = os.fstat(fd), path.stat()
                if (held.st_dev, held.st_ino) != (target.st_dev, target.st_ino):
                    raise ValueError('different lock')
            except (OSError, ValueError):
                raise GuardError('Invalid inherited deployment lock; installation was not started.') from None
        else:
            fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise GuardError('Deployment lock is not a regular file; preserve it and inspect its owner.')
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise GuardError('Another installation or service change holds state/deployment.lock; '
                             'wait for that operation to finish, then retry. Installer was not started.') from None
        if previous is None:
            record = json.dumps({'pid': os.getpid(), 'source_root': str(root), 'started_at': time.time()}) + '\n'
            os.ftruncate(fd, 0)
            os.write(fd, record.encode())
        if inherit:
            os.environ[LOCK_ENV] = str(fd)
        yield fd
    finally:
        if inherit:
            if previous is None:
                os.environ.pop(LOCK_ENV, None)
            else:
                os.environ[LOCK_ENV] = previous
        # Do not LOCK_UN: the installer may still own an inherited descriptor.
        if fd is not None:
            os.close(fd)


def check_source(root, *, required=True):
    raw = os.environ.get(SOURCE_ENV)
    if raw is None and not required:
        return
    try:
        fd = int(raw)
        if fcntl.fcntl(fd, fcntl.F_GETFL) & os.O_ACCMODE != os.O_RDONLY:
            raise ValueError('writable manifest')
        expected = json.loads(os.pread(fd, 65536, 0))
        if expected.get('root') != str(Path(root).resolve()):
            raise ValueError('different root')
    except (OSError, TypeError, ValueError, AttributeError):
        raise GuardError('Installation source checkpoint is unavailable; stop and rerun the installer.') from None
    verify_source(root, expected)


@contextmanager
def mutation_guard(root):
    """For service mutations: reuse a real inherited lock, or acquire our own."""
    with deployment_lock(root):
        check_source(root, required=False)
        yield


def detach_guard():
    """Call only in a forked long-lived child, before execing its service."""
    for name in (LOCK_ENV, SOURCE_ENV):
        raw = os.environ.pop(name, None)
        if raw is not None:
            try:
                os.close(int(raw))
            except (OSError, ValueError):
                pass


def _run(command, root, *, timeout=INSTALL_TIMEOUT):
    try:
        inherited = tuple(int(os.environ[name]) for name in (LOCK_ENV, SOURCE_ENV, 'INSTALL_LEDGER_FD')
                          if name in os.environ)
    except ValueError:
        raise GuardError('Invalid inherited installer descriptor; inspect the parent invocation before retrying.') from None
    child = subprocess.Popen(command, cwd=root, pass_fds=inherited, start_new_session=True)
    try:
        return child.wait(timeout=timeout)
    except BaseException:
        # Only the process group started by this invocation is ours to stop.
        try:
            os.killpg(child.pid, signal.SIGTERM)
            child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        except ProcessLookupError:
            pass
        # The group can outlive its leader: a TERM-resistant descendant must
        # not retain the lock or keep installing after we report termination.
        try:
            os.killpg(child.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except PermissionError:
            # Darwin can report EPERM for a group containing only zombies.
            # Do not infer termination: require ESRCH in the bounded wait below.
            pass
        child.wait(timeout=5)
        deadline = time.monotonic() + 5
        while True:
            try:
                os.killpg(child.pid, 0)
            except ProcessLookupError:
                break
            except PermissionError:
                pass  # Still unverified; a live inaccessible group must fail.
            if time.monotonic() >= deadline:
                raise GuardError('Installation termination is unverified: its process group remains. '
                                 'Inspect the operation before retrying; do not remove deployment.lock.') from None
            time.sleep(.05)
        raise


def run_install(root, installer_args, *, preflight=None):
    root = Path(root).resolve()
    with deployment_lock(root):
        baseline = source_snapshot(root)
        if preflight is not None:
            head = preflight()
            verify_source(root, baseline)
            print(f'{root.name} source verified: main = origin/main = {head}', flush=True)
        source = (root / 'install.sh').read_text()
        if source.count(BODY_MARKER) != 1:
            raise GuardError('Installer guard entry is missing or ambiguous; restore the intended install.sh before retrying.')
        body = source.split(BODY_MARKER, 1)[1]
        verify_source(root, baseline)
        writable, filename = tempfile.mkstemp(prefix='.deployment-source-', dir=root / 'state')
        manifest = None
        previous = os.environ.get(SOURCE_ENV)
        try:
            with os.fdopen(writable, 'wb') as stream:
                stream.write(json.dumps(baseline).encode())
            manifest = os.open(filename, os.O_RDONLY)
            os.unlink(filename)
            os.environ[SOURCE_ENV] = str(manifest)
            print('Starting install.sh with source checkpoints; service health is reported by the installer.', flush=True)
            result = _run(['bash', '-c', body, str(root / 'install.sh'), *installer_args], root)
            verify_source(root, baseline)
            return result
        finally:
            if previous is None:
                os.environ.pop(SOURCE_ENV, None)
            else:
                os.environ[SOURCE_ENV] = previous
            if manifest is not None:
                os.close(manifest)
            if os.path.exists(filename):
                os.unlink(filename)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('install', 'check', 'held', 'run'))
    parser.add_argument('root')
    parser.add_argument('arguments', nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    root = Path(args.root).resolve()
    if root != Path(__file__).resolve().parent:
        parser.error('root must be the checkout containing deployment_guard.py')
    arguments = args.arguments[1:] if args.arguments[:1] == ['--'] else args.arguments
    try:
        if args.action == 'install':
            return run_install(root, arguments)
        if args.action == 'held' and LOCK_ENV not in os.environ:
            return 1
        with mutation_guard(root):
            if args.action == 'held':
                return 0
            if args.action == 'check':
                check_source(root)
                return 0
            if not arguments:
                parser.error('run requires a command after --')
            return _run(arguments, root)
    except (GuardError, OSError, subprocess.TimeoutExpired) as error:
        detail = str(error) if isinstance(error, GuardError) else 'Local guard operation failed or timed out; inspect local permissions and service status before retrying.'
        print(f'{root.name} deployment failed: {detail}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
