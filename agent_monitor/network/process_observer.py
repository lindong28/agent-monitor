"""Optional, passive Mihomo Unix-controller observations.

No configuration reads, controller mutations or internet requests. A controller
snapshot is reused only during one process scan; associations require both ends
of a currently observed TCP connection, not just a process name or source port.
"""
import datetime
import http.client
import ipaddress
import json
import os
from pathlib import Path
import socket
import stat
import subprocess
import time


def endpoint(value):
    host, port = value.rsplit(':', 1)
    return str(ipaddress.ip_address(host.strip('[]'))), int(port)


class UnixConnection(http.client.HTTPConnection):
    def __init__(self, path, timeout):
        super().__init__('localhost', timeout=timeout)
        self.path = path

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self.path)


def read_connections(path, timeout=1):
    if not stat.S_ISSOCK(os.stat(path).st_mode):
        raise ValueError('not a Unix socket')
    conn = UnixConnection(path, timeout)
    try:
        conn.request('GET', '/connections', headers={'Accept': 'application/json'})
        response = conn.getresponse()
        if response.status != 200:
            raise ValueError('controller HTTP ' + str(response.status))
        raw = response.read(2 * 1024 * 1024 + 1)
        if len(raw) > 2 * 1024 * 1024:
            raise ValueError('controller response too large')
        data = json.loads(raw)
        if not isinstance(data, dict) or not isinstance(data.get('connections'), list):
            raise ValueError('invalid controller response')
        return data['connections']
    finally:
        conn.close()


def discover(peer):
    """Discover only sockets owned by the known local proxy listener."""
    host, port = endpoint(peer)
    if not ipaddress.ip_address(host).is_loopback:
        return []
    found = subprocess.run(['lsof', '-nP', '-iTCP:' + str(port), '-sTCP:LISTEN', '-Fp'],
                           capture_output=True, text=True, timeout=1, check=False)
    pids = {line[1:] for line in found.stdout.splitlines() if line.startswith('p') and line[1:].isdigit()}
    paths = set()
    for pid in sorted(pids)[:4]:
        name = subprocess.run(['ps', '-p', pid, '-o', 'comm='], capture_output=True, text=True, timeout=1, check=False)
        if Path(name.stdout.strip()).name not in ('mihomo', 'verge-mihomo', 'clash', 'clash-meta'):
            continue
        result = subprocess.run(['lsof', '-nP', '-a', '-p', pid, '-U', '-Fn'],
                                capture_output=True, text=True, timeout=1, check=False)
        paths.update(line[1:] for line in result.stdout.splitlines() if line.startswith('n/'))
    return sorted(paths)[:4]


def text_field(value, limit=256):
    # Controller names/hosts are local diagnostic data, never markup or URLs.
    if not isinstance(value, str) or len(value) > limit or any(ord(c) < 32 for c in value):
        return None
    return value


def associate(rows, connections, executable, observed_at):
    result = []
    for connection in connections:
        try:
            local, peer = endpoint(connection['local']), endpoint(connection['peer'])
        except (KeyError, ValueError, TypeError):
            continue
        matches = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            meta = row.get('metadata')
            if not isinstance(meta, dict) or meta.get('network') != 'tcp':
                continue
            try:
                source = (str(ipaddress.ip_address(meta['sourceIP'])), int(meta['sourcePort']))
                inbound = (str(ipaddress.ip_address(meta['inboundIP'])), int(meta['inboundPort']))
            except (KeyError, ValueError, TypeError):
                continue
            if source != local or inbound != peer:
                continue
            if meta.get('processPath') and meta['processPath'] != executable:
                continue
            matches.append(row)
        if len(matches) != 1:
            continue
        row = matches[0]; meta = row['metadata']
        host = text_field(meta.get('host')) or text_field(meta.get('destinationIP'))
        try:
            port = int(meta['destinationPort'])
            if not 0 < port < 65536 or not host or any(c in host for c in '/@?#'):
                continue
        except (KeyError, ValueError, TypeError):
            continue
        chains = row.get('chains', [])
        if not isinstance(chains, list) or len(chains) > 32:
            chains = []
        result.append({'local': connection['local'], 'peer': connection['peer'], 'host': host, 'port': port,
                       'chains': [v for v in (text_field(v) for v in chains) if v is not None],
                       'rule': text_field(row.get('rule')), 'observed_at': observed_at})
    return result


class MihomoObserver:
    def __init__(self, deadline=None):
        self.deadline = deadline or time.monotonic() + 4
        self.override = os.environ.get('AGENT_MONITOR_MIHOMO_SOCKET')
        self.paths, self.snapshots = {}, {}

    def observe(self, connections, executable):
        next_step = '可选：为本机 Mihomo 配置可读取的 external-controller-unix；连接器可设置 AGENT_MONITOR_MIHOMO_SOCKET。无需安装 Mihomo 来使用基础观察。'
        output = {'status': 'not_configured', 'reason': '未发现可关联的本机 Mihomo 控制器。',
                  'next_step': next_step, 'connections': []}
        candidates = set()
        try:
            if self.override:
                candidates.add(self.override)
            else:
                for conn in connections:
                    peer = conn.get('peer', '')
                    if peer not in self.paths:
                        if time.monotonic() >= self.deadline:
                            raise TimeoutError()
                        self.paths[peer] = discover(peer)
                    candidates.update(self.paths[peer])
            for path in sorted(candidates):
                if path not in self.snapshots:
                    if time.monotonic() >= self.deadline:
                        raise TimeoutError()
                    try:
                        rows = read_connections(path, min(1, max(.05, self.deadline - time.monotonic())))
                        self.snapshots[path] = (rows, datetime.datetime.now(datetime.timezone.utc).isoformat(), None)
                    except (OSError, ValueError, http.client.HTTPException) as exc:
                        self.snapshots[path] = ([], None, type(exc).__name__)
                rows, at, error = self.snapshots[path]
                if error:
                    output.update(status='unavailable', reason='本机控制器读取失败：' + error)
                    continue
                output.update(status='unmatched', reason='控制器已读取，但未取得唯一、完整且匹配的 TCP 两端信息。')
                output['connections'].extend(associate(rows, connections, executable, at))
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            output.update(status='unavailable', reason='可选控制器发现未完成：' + type(exc).__name__)
        # Multiple candidate controllers must not produce conflicting claims.
        unique = {}
        for row in output['connections']:
            key = (row['local'], row['peer'])
            if key in unique and unique[key] != row:
                unique[key] = None
            elif key not in unique:
                unique[key] = row
        output['connections'] = [row for row in unique.values() if row is not None]
        if output['connections']:
            output.update(status='observed', reason='Mihomo 现有连接按本地 TCP 两端关联；链名不证明公网出口。')
        return output
