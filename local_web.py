"""Converge an already-running, owned manual dashboard; never enable a service."""
from contextlib import contextmanager, ExitStack
import ctypes
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

import deployment_guard
import process_identity
import service_runtime

from project_paths import PROJECT_ROOT

ROOT = PROJECT_ROOT


@contextmanager
def account_quiescence(root):
    """Existing admission and job locks exclude queued as well as running sends."""
    directory = Path(root) / 'state/codex-accounts'
    if directory.is_symlink():
        raise ValueError('Account directory ownership is unverified; maintenance was not started.')
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    with ExitStack() as stack:
        def lock(path):
            stream = stack.enter_context(os.fdopen(os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600), 'a+'))
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ValueError('Account operations are active; wait for completion before service maintenance. Nothing was stopped.') from None
        lock(directory / '.catalog.lock')
        for profile in sorted(directory.glob('*/profile.json')):
            if profile.parent.is_symlink():
                raise ValueError('Account profile ownership is unverified; maintenance was not started.')
            lock(profile.parent / '.lock')
        yield


def process_context(pid):
    """Read argv and the full environment without printing either (same uid only)."""
    if sys.platform == 'linux':
        base = Path('/proc') / str(pid)
        argv = base.joinpath('cmdline').read_bytes().rstrip(b'\0').split(b'\0')
        values = base.joinpath('environ').read_bytes().rstrip(b'\0').split(b'\0')
    elif sys.platform == 'darwin':
        mib = (ctypes.c_int * 3)(1, 49, pid)  # CTL_KERN, KERN_PROCARGS2
        buffer = ctypes.create_string_buffer(os.sysconf('SC_ARG_MAX'))
        size = ctypes.c_size_t(len(buffer))
        libc = ctypes.CDLL(None, use_errno=True)
        libc.sysctl.argtypes = [ctypes.POINTER(ctypes.c_int), ctypes.c_uint, ctypes.c_void_p,
                               ctypes.POINTER(ctypes.c_size_t), ctypes.c_void_p, ctypes.c_size_t]
        if libc.sysctl(mib, 3, buffer, ctypes.byref(size), None, 0):
            raise ValueError('Could not capture the local Web launch environment; incumbent preserved.')
        raw = buffer.raw[:size.value]
        argc = ctypes.c_int.from_buffer_copy(raw[:4]).value
        if not 1 <= argc <= 4096:
            raise ValueError('Local Web argument count is unverified; incumbent preserved.')
        offset = raw.index(b'\0', 4) + 1
        while offset < len(raw) and raw[offset] == 0:
            offset += 1
        argv = []
        for _ in range(argc):
            end = raw.index(b'\0', offset)
            argv.append(raw[offset:end])
            offset = end + 1
        values = raw[offset:].rstrip(b'\0').split(b'\0')
    else:
        raise ValueError('Launch environment capture is unsupported on this platform.')
    env = dict(os.fsdecode(value).split('=', 1) for value in values if b'=' in value)
    if not env.get('HOME') or not env.get('PATH'):
        raise ValueError('Local Web launch environment is incomplete; incumbent preserved.')
    for key in (deployment_guard.LOCK_ENV, deployment_guard.SOURCE_ENV):
        env.pop(key, None)
    return [os.fsdecode(arg) for arg in argv], env


def capture(root=ROOT):
    root = Path(root)
    owned = process_identity.read_owned(root / 'state', root)
    if owned is None:
        return None
    argv, env = process_context(owned[0])
    if len(argv) != 6 or argv[1] != str(root / 'server.py') or argv[2] != '--host' or argv[4] != '--port':
        raise ValueError('Local Web startup arguments are unverified; incumbent preserved.')
    port = int(argv[5])
    if not 1 <= port <= 65535 or process_identity.read_owned(root / 'state', root) != owned:
        raise ValueError('Local Web identity changed during capture; incumbent preserved.')
    recipe = {'host': argv[3], 'port': port, 'environment': env}
    path = root / 'state/local-web-launch.json'
    fd, temporary = tempfile.mkstemp(prefix='.local-web-launch-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(recipe, stream)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return recipe


def health(root=ROOT):
    port = int((Path(root) / 'state/port').read_text().strip())
    return service_runtime.read_health(f'http://127.0.0.1:{port}/api/health?asset_watch=1')


def saved_recipe(root):
    path = Path(root) / 'state/local-web-launch.json'
    if path.is_symlink() or not path.is_file():
        raise ValueError('No saved local Web startup conditions; use agent-monitor local start to enable it.')
    info = path.stat()
    if info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ValueError('Local Web startup record must be private and owned by the current user.')
    recipe = json.loads(path.read_text())
    if (not isinstance(recipe, dict) or not isinstance(recipe.get('host'), str)
            or not isinstance(recipe.get('port'), int) or not 1 <= recipe['port'] <= 65535
            or not isinstance(recipe.get('environment'), dict)
            or not all(isinstance(k, str) and isinstance(v, str) for k, v in recipe['environment'].items())):
        raise ValueError('Saved local Web startup conditions are invalid; inspect the private record.')
    return recipe


def run_cli(root, action, recipe=None):
    previous = dict(os.environ)
    try:
        if recipe is not None:
            env = dict(recipe['environment'])
            env.update(AGENT_MONITOR_BIND=recipe['host'], AGENT_MONITOR_PORT=str(recipe['port']),
                       AGENT_MONITOR_EXACT_PORT='1',
                       AGENT_MONITOR_PYTHON=str(root / '.venv/bin/python'))
            for name in (deployment_guard.LOCK_ENV, deployment_guard.SOURCE_ENV):
                if name in previous:
                    env[name] = previous[name]
            os.environ.clear()
            os.environ.update(env)
        result = deployment_guard._run([str(root / 'agent-monitor'), 'local', action], root, timeout=60)
        if result:
            raise ValueError(f'Local Web {action} failed; inspect state/server.log and service status.')
    finally:
        os.environ.clear()
        os.environ.update(previous)


def restore(root, recipe):
    run_cli(root, 'start', recipe)
    if int((root / 'state/port').read_text()) != recipe['port']:
        raise ValueError('Local Web changed port; inspect service status before continuing.')
    service_runtime.wait_for_identity(lambda: health(root), root, service_runtime.signature(root))


def converge(root=ROOT):
    import hub
    root = Path(root)
    if hub.is_hub():
        print('Local Web: central Hub owns this checkout; no separate dashboard.')
        return
    with deployment_guard.mutation_guard(root):
        if process_identity.read_owned(root / 'state', root) is None:
            print('Local Web: not running; preserved its disabled state.')
            return
        if service_runtime.matches(health(root), root, service_runtime.signature(root)):
            print('Local Web: target source/configuration already serving; process retained.')
            return
        with account_quiescence(root):
            recipe = capture(root)
            if recipe is None:
                raise ValueError('Local Web exited before capture; inspect service status.')
            run_cli(root, 'stop')
            restore(root, recipe)
        print('Local Web: updated; target source/configuration confirmed through local HTTP.')


if __name__ == '__main__':
    try:
        converge()
    except (OSError, ValueError, process_identity.IdentityUnknown, subprocess.SubprocessError) as error:
        print(f'Local Web maintenance failed: {error}', file=sys.stderr)
        raise SystemExit(1)
