"""Passive OS network facts. No resolver queries, PAC fetches or public sockets."""
import datetime
import ipaddress
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .process_network import network_environment, safe_address


def capture(*, platform=None, run=None, read_text=None, environment=None):
    platform = platform or sys.platform
    run = run or subprocess.run
    read_text = read_text or (lambda path: Path(path).read_text())
    environment = os.environ if environment is None else environment
    now = datetime.datetime.now().astimezone()
    result = {'observed_at': now.isoformat(), 'addresses': [], 'dns': [],
              'timezone': {'name': None, 'offset': None},
              'system_proxy': {'status': 'unavailable', 'entries': []},
              'monitor_environment': network_environment(environment), 'errors': []}
    try:
        try:
            link = os.readlink('/etc/localtime')
        except OSError:
            link = ''
        name = link.split('zoneinfo/', 1)[1] if 'zoneinfo/' in link else None
        if name:
            zone = ZoneInfo(name)
        else:
            with open('/etc/localtime', 'rb') as source:
                zone = ZoneInfo.from_file(source)
        system_now = now.astimezone(zone)
        result['timezone'] = {'name': name or system_now.tzname(), 'offset': system_now.strftime('%z')}
    except (OSError, ValueError, ZoneInfoNotFoundError) as exc:
        result['errors'].append({'stage': 'timezone', 'type': type(exc).__name__})

    def command(argv):
        return run(argv, capture_output=True, text=True, timeout=3, check=True).stdout

    try:
        if platform == 'darwin':
            interface = None
            for line in command(['ifconfig', '-a']).splitlines():
                if line and not line[0].isspace():
                    interface = line.split(':', 1)[0]
                fields = line.split()
                if len(fields) > 1 and fields[0] in ('inet', 'inet6'):
                    address = str(ipaddress.ip_address(fields[1].split('%')[0]))
                    result['addresses'].append({'interface': interface, 'address': address})
        elif platform.startswith('linux'):
            for interface in json.loads(command(['ip', '-j', 'address', 'show'])):
                for entry in interface.get('addr_info', []):
                    address = str(ipaddress.ip_address(entry['local']))
                    result['addresses'].append({'interface': interface['ifname'], 'address': address})
        else:
            raise NotImplementedError()
    except (OSError, ValueError, KeyError, subprocess.SubprocessError, NotImplementedError) as exc:
        result['errors'].append({'stage': 'addresses', 'type': type(exc).__name__})
    try:
        if platform == 'darwin':
            candidates = re.findall(r'nameserver\[\d+\]\s*:\s*(\S+)', command(['scutil', '--dns']))
        else:
            candidates = [line.split()[1] for line in read_text('/etc/resolv.conf').splitlines()
                          if len(line.split()) > 1 and line.split()[0] == 'nameserver']
        result['dns'] = list(dict.fromkeys(str(ipaddress.ip_address(ip.split('%')[0])) for ip in candidates))
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        result['errors'].append({'stage': 'dns', 'type': type(exc).__name__})
    try:
        if platform == 'darwin':
            values = dict(re.findall(r'^\s*([A-Za-z]+)\s*:\s*([^\r\n]+)', command(['scutil', '--proxy']), re.M))
            entries = []
            for protocol in ('HTTP', 'HTTPS', 'SOCKS'):
                enabled = values.get(protocol + 'Enable') == '1'
                entry = {'protocol': protocol, 'enabled': enabled}
                if enabled and values.get(protocol + 'Proxy'):
                    scheme = 'socks5' if protocol == 'SOCKS' else 'http'
                    entry.update(safe_address(scheme + '://' + values[protocol + 'Proxy'] + ':' + values.get(protocol + 'Port', '0')))
                entries.append(entry)
            result['system_proxy'] = {'status': 'observed', 'source': 'macOS scutil --proxy', 'entries': entries,
                'pac_enabled': values.get('ProxyAutoConfigEnable') == '1',
                'auto_discovery_enabled': values.get('ProxyAutoDiscoveryEnable') == '1'}
        else:
            result['system_proxy']['reason'] = '当前平台未接入系统代理配置观察；下方环境设置不代表系统或浏览器的实际选路。'
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        result['errors'].append({'stage': 'system_proxy', 'type': type(exc).__name__})
    return result
