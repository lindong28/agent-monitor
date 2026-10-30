"""Read-only, same-user process observations. Never loads provider credentials.

The OS exposes startup environment and current sockets, not a receipt of the
public source address seen by a provider. Keep those evidence levels separate.
"""
import argparse
import ctypes
import datetime
import ipaddress
import json
import os
from pathlib import Path
import socket
import struct
import subprocess
import sys
import time
from urllib.parse import urlsplit

MAX_BYTES = 1024 * 1024
PROXY_KEYS = ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'http_proxy', 'https_proxy', 'all_proxy')
ENDPOINT_KEYS = ('OPENAI_BASE_URL', 'ANTHROPIC_BASE_URL', 'OPENAI_API_BASE')
FLAG_KEYS = ('NODE_USE_ENV_PROXY', 'NODE_TLS_REJECT_UNAUTHORIZED')


def safe_address(value):
    """Only scheme/host/port cross the diagnostic boundary, never URL secrets."""
    try:
        parsed = urlsplit(value if '://' in value else 'http://' + value)
        if parsed.scheme not in ('http', 'https', 'socks5', 'socks5h', 'socks4') or not parsed.hostname:
            raise ValueError()
        host = parsed.hostname
        if ':' in host:
            host = '[' + host + ']'
        return {'address': parsed.scheme + '://' + host + (':' + str(parsed.port) if parsed.port else ''),
                'credentials_present': bool(parsed.username or parsed.password)}
    except (ValueError, UnicodeError):
        return {'address': None, 'invalid': True}


def network_environment(env):
    result = {key: safe_address(env[key]) for key in PROXY_KEYS + ENDPOINT_KEYS if env.get(key)}
    for key in ('NO_PROXY', 'no_proxy'):
        if key in env:
            # Bypass lists have no URL credentials; malformed entries are not echoed.
            entries = env[key].split(',')
            def valid_entry(item):
                if len(item) > 255 or any(c in item for c in '@?#\\\n\r'):
                    return False
                if '/' in item:
                    try:
                        ipaddress.ip_network(item.strip(), strict=False)
                    except ValueError:
                        return False
                return True
            valid = all(valid_entry(item) for item in entries)
            result[key] = {'value': env[key] if valid and len(env[key]) <= 4096 else None,
                           'invalid': not valid or len(env[key]) > 4096}
    for key in FLAG_KEYS:
        if key in env:
            result[key] = {'value': env[key] if env[key] in ('0', '1') else 'other'}
    return result


def agent_kind(executable, argv):
    name = Path(executable).name
    if name in ('codex', 'claude'):
        return name
    if Path(executable).parent.resolve() == (Path.home() / '.local/share/claude/versions').resolve():
        return 'claude'
    # Native installers can keep version-named binaries behind the public link.
    # Resolve only the two entrypoints, never scan provider configuration roots.
    for kind in ('codex', 'claude'):
        entry = Path.home() / '.local' / 'bin' / kind
        if entry.is_symlink() and entry.resolve() == Path(executable).resolve():
            return kind
    if name not in ('node', 'nodejs', 'bun'):
        return None
    # Check the entrypoint, never prompt text or arbitrary later arguments.
    if len(argv) > 1:
        entry = argv[1].replace('\\', '/')
        if entry.endswith('/@openai/codex/bin/codex.js'):
            return 'codex'
        if entry.endswith('/@anthropic-ai/claude-code/cli.js'):
            return 'claude'
    return None


def parse_darwin_args(raw):
    if len(raw) < 4:
        raise ValueError('missing argc')
    argc = struct.unpack_from('i', raw)[0]
    if not 0 < argc < 65536:
        raise ValueError('invalid argc')
    pos = raw.index(b'\0', 4) + 1
    while pos < len(raw) and raw[pos] == 0:
        pos += 1
    argv = []
    for _ in range(argc):
        end = raw.index(b'\0', pos)
        argv.append(raw[pos:end].decode('utf-8', 'replace'))
        pos = end + 1
    env = dict(item.decode('utf-8', 'replace').split('=', 1)
               for item in raw[pos:].split(b'\0') if b'=' in item)
    return argv, env


def read_limited(path):
    with path.open('rb') as stream:
        raw = stream.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError('observation too large')
    return raw


class Linux:
    def __init__(self, root=Path('/proc')):
        self.root = root
        self.discovery_errors = []

    def candidates(self):
        for path in self.root.iterdir():
            if path.name.isdecimal():
                try:
                    if path.stat().st_uid == os.getuid():
                        exe = os.readlink(path / 'exe').removesuffix(' (deleted)')
                        if agent_kind(exe, []) or Path(exe).name in ('node', 'nodejs', 'bun'):
                            yield int(path.name), exe
                except PermissionError as exc:
                    self.discovery_errors.append({'stage': 'discovery', 'pid': int(path.name), **failure(exc)})
                except (OSError, ValueError):
                    continue

    def identity(self, pid):
        path = self.root / str(pid)
        if path.stat().st_uid != os.getuid():
            raise PermissionError()
        # stat comm may itself contain spaces and parentheses.
        fields = read_limited(path / 'stat').rsplit(b') ', 1)[1].split()
        if len(fields) < 20:
            raise ValueError('incomplete process identity')
        return fields[19].decode()  # field 22: starttime, clock ticks after boot

    def executable(self, pid):
        return os.readlink(self.root / str(pid) / 'exe').removesuffix(' (deleted)')

    def arguments(self, pid):
        raw = read_limited(self.root / str(pid) / 'cmdline')
        return [p.decode('utf-8', 'replace') for p in raw.rstrip(b'\0').split(b'\0')]

    def environment(self, pid):
        raw = read_limited(self.root / str(pid) / 'environ')
        return dict(p.decode('utf-8', 'replace').split('=', 1) for p in raw.split(b'\0') if b'=' in p)

    def connections(self, pid):
        path = self.root / str(pid)
        inodes = set()
        for fd in (path / 'fd').iterdir():
            try:
                link = os.readlink(fd)
                if link.startswith('socket:['):
                    inodes.add(link[8:-1])
            except FileNotFoundError:
                continue
        result = []
        for family in ('tcp', 'tcp6'):
            try:
                lines = read_limited(path / 'net' / family).decode().splitlines()[1:]
            except FileNotFoundError:
                continue
            for line in lines:
                row = line.split()
                if len(row) > 9 and row[9] in inodes and row[3] == '01':
                    result.append({'local': linux_address(row[1]), 'peer': linux_address(row[2]), 'state': 'ESTABLISHED'})
        return result


def linux_address(value):
    host, port = value.split(':')
    raw = bytes.fromhex(host)
    raw = b''.join(int.from_bytes(raw[i:i+4], 'big').to_bytes(4, sys.byteorder) for i in range(0, len(raw), 4))
    ip = str(ipaddress.ip_address(raw))
    return ('[' + ip + ']' if len(raw) == 16 else ip) + ':' + str(int(port, 16))


class Darwin:
    def __init__(self):
        self.lib = ctypes.CDLL(None, use_errno=True)
        self.proc = ctypes.CDLL('/usr/lib/libproc.dylib', use_errno=True)

    def candidates(self):
        result = subprocess.run(['ps', '-axo', 'pid=,uid=,comm='], capture_output=True, text=True, timeout=3, check=True)
        for line in result.stdout.splitlines():
            fields = line.split(None, 2)
            if len(fields) == 3 and int(fields[1]) == os.getuid() and (agent_kind(fields[2], []) or Path(fields[2]).name in ('node', 'nodejs', 'bun')):
                yield int(fields[0]), fields[2]

    def identity(self, pid):
        # proc_bsdinfo from sys/proc_info.h: uid at offset20, start timeval at120.
        buf = ctypes.create_string_buffer(136)
        if self.proc.proc_pidinfo(pid, 3, 0, buf, 136) != 136:
            raise ProcessLookupError()
        if struct.unpack_from('I', buf, 20)[0] != os.getuid():
            raise PermissionError()
        return '%s.%06d' % struct.unpack_from('QQ', buf, 120)

    def executable(self, pid):
        buf = ctypes.create_string_buffer(4096)
        if self.proc.proc_pidpath(pid, buf, len(buf)) <= 0:
            raise ProcessLookupError()
        return os.fsdecode(buf.value)

    def args_environment(self, pid):
        mib = (ctypes.c_int * 3)(1, 49, pid)  # CTL_KERN, KERN_PROCARGS2
        size = ctypes.c_size_t(262144)
        buf = ctypes.create_string_buffer(size.value)
        if self.lib.sysctl(mib, 3, buf, ctypes.byref(size), None, 0):
            raise OSError(ctypes.get_errno(), 'process environment unavailable')
        return parse_darwin_args(buf.raw[:size.value])

    def connections(self, pid):
        result = subprocess.run(['lsof', '-nP', '-a', '-p', str(pid), '-iTCP', '-F', 'fnT'],
                                capture_output=True, text=True, timeout=3)
        if result.returncode not in (0, 1) or result.stderr.strip():
            raise OSError('socket observation unavailable')
        connections, current = [], {}
        for line in result.stdout.splitlines() + ['f']:
            if line.startswith('f'):
                if current.get('state') == 'ESTABLISHED' and 'peer' in current:
                    connections.append(current)
                current = {}
            elif line.startswith('n') and '->' in line:
                current['local'], current['peer'] = line[1:].split('->', 1)
            elif line.startswith('TST='):
                current['state'] = line[4:]
        return connections


def failure(exc):
    return {'type': type(exc).__name__, 'errno': getattr(exc, 'errno', None)}


def capture(backend=None):
    began = time.monotonic()
    report = {'schema_version': 1, 'host': socket.gethostname(), 'platform': sys.platform,
              'observed_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
              'scope': 'same_user_local_processes', 'processes': [], 'errors': []}
    try:
        backend = backend or (Darwin() if sys.platform == 'darwin' else Linux() if sys.platform.startswith('linux') else None)
        if backend is None:
            raise NotImplementedError()
        for pid, executable in backend.candidates():
            if time.monotonic() - began > 8:
                report['errors'].append({'stage': 'discovery', 'type': 'TimeBudgetExceeded'})
                break
            item = {'pid': pid, 'agent': Path(executable).name, 'environment': {}, 'connections': [], 'errors': []}
            try:
                before = backend.identity(pid)
                executable = backend.executable(pid)
                env, environment_error = None, None
                try:
                    if isinstance(backend, Darwin):
                        argv, env = backend.args_environment(pid)
                    else:
                        argv = backend.arguments(pid)
                except (OSError, ValueError) as exc:
                    if not agent_kind(executable, []):
                        raise
                    argv, environment_error = [], exc
                kind = agent_kind(executable, argv)
                if not kind:
                    continue
                item.update(agent=kind, instance=f'{pid}:{before}')
                try:
                    if environment_error:
                        raise environment_error
                    if env is None:
                        env = backend.environment(pid)
                    item['environment'] = network_environment(env)
                    # macOS may omit all env vars under SIP. Empty is not proof
                    # of absence; neither OS exposes post-start client internals.
                    item['environment_status'] = 'startup_snapshot' if env else 'unavailable_or_empty'
                except (OSError, ValueError) as exc:
                    item['environment_status'] = 'unavailable'
                    item['errors'].append({'stage': 'environment', **failure(exc)})
                try:
                    item['connections'] = backend.connections(pid)
                    item['connections_status'] = 'observed'
                except (OSError, ValueError, subprocess.SubprocessError) as exc:
                    item['connections_status'] = 'unavailable'
                    item['errors'].append({'stage': 'connections', **failure(exc)})
                if backend.identity(pid) != before or backend.executable(pid) != executable:
                    raise ProcessLookupError()
                item['public_egress'] = {'status': 'unverified', 'reason': 'No destination receipt; socket peer is not public egress.'}
            except (OSError, ValueError, subprocess.SubprocessError) as exc:
                # Never bind an old PID snapshot to a replaced process.
                item = {'pid': pid, 'agent': Path(executable).name, 'status': 'unavailable',
                        'errors': [{'stage': 'process', **failure(exc)}]}
            report['processes'].append(item)
        discovery_errors = getattr(backend, 'discovery_errors', [])
        if isinstance(discovery_errors, list):
            report['errors'].extend(discovery_errors)
    except (OSError, ValueError, subprocess.SubprocessError, NotImplementedError) as exc:
        report['errors'].append({'stage': 'discovery', **failure(exc)})
    report['elapsed_ms'] = round((time.monotonic() - began) * 1000)
    return report


def read_report():
    """Bound collection away from the connector's account transport threads."""
    try:
        result = subprocess.run([sys.executable, '-m', __name__, '--json'],
                                capture_output=True, text=True, timeout=12,
                                cwd=Path(__file__).resolve().parents[2])
        if result.returncode not in (0, 1) or len(result.stdout) > MAX_BYTES:
            raise ValueError('collection failed')
        return json.loads(result.stdout)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        return {'schema_version': 1, 'host': socket.gethostname(), 'processes': [],
                'errors': [{'stage': 'collector', **failure(exc)}]}


def main():
    parser = argparse.ArgumentParser(description='读取本用户 Codex/Claude 的启动网络配置和当前 TCP 连接；不发送公网探测。')
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args()
    report = capture()
    if args.json:
        print(json.dumps(report, ensure_ascii=False))
    else:
        print(f"本机 {report['host']} · {report['observed_at']}")
        print('仅观测本用户本机进程；启动配置不等于实际选路，公网出口未核实。')
        for item in report['processes']:
            print(f"{item['agent']} PID {item['pid']} · {item.get('instance', '身份未取得')}")
            for key, value in item.get('environment', {}).items():
                print(f"  {key}: {value.get('address') or value.get('value') or '无值/未核实'}")
            for conn in item.get('connections', []):
                print(f"  TCP {conn['local']} → {conn['peer']}")
            if item.get('errors'):
                print('  部分采集失败：' + ', '.join(e['stage'] + '/' + e['type'] for e in item['errors']))
        if not report['processes']:
            print('未发现可识别进程。请在当前操作系统用户下启动 Codex/Claude 后重试。')
        if report['errors']:
            print('采集不完整：' + ', '.join(e['stage'] + '/' + e['type'] for e in report['errors']))
    return 1 if report['errors'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
