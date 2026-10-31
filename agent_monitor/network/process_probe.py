"""Explicit anonymous probe through an observed process proxy, never a fallback.

The receipt belongs to a fixed diagnostic target, not to an earlier agent request.
Raw proxy credentials travel only over the owned child's stdin.
"""
import hashlib
import ipaddress
import json
import math
import subprocess
import sys
import time

from agent_monitor.network import process_network as pn
from agent_monitor.network.account_egress import parse_trace
from agent_monitor.runtime.python_entrypoint import module_command
from agent_monitor.network.process_route import TARGETS, PROBE_KINDS, RouteUnavailable, from_snapshot, target_for

def route_for(pid, instance, backend=None, probe_kind='provider'):
    """Require a unique proxy address with a matching established socket."""
    backend = backend or (pn.Darwin() if sys.platform == 'darwin' else pn.Linux())
    executable = next((exe for candidate, exe in backend.candidates() if candidate == pid), None)
    if not executable or f'{pid}:{backend.identity(pid)}' != instance:
        raise RouteUnavailable('进程已退出或实例已改变，请重新读取。')
    executable = backend.executable(pid)
    if isinstance(backend, pn.Darwin):
        argv, env = backend.args_environment(pid)
    else:
        argv, env = backend.arguments(pid), backend.environment(pid)
    kind = pn.agent_kind(executable, argv)
    route = from_snapshot(kind, env, backend.connections(pid), instance, probe_kind)
    if f'{pid}:{backend.identity(pid)}' != instance or backend.executable(pid) != executable:
        raise RouteUnavailable('进程已退出或实例已改变，请重新读取。')
    return route


def parse_public(raw):
    data = json.loads(raw)
    if not isinstance(data, dict) or data.get('status') != 'success':
        raise ValueError('unsuccessful IP response')
    if not isinstance(data.get('query'), str):
        raise ValueError('invalid IP field')
    address = ipaddress.ip_address(data['query'])
    if not address.is_global:
        raise ValueError('not a public IP')
    country = data.get('countryCode')
    if not isinstance(country, str) or len(country) != 2 or any(c not in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ' for c in country) or country == 'XX':
        country = None
    result = {'ip': str(address), 'country_code': country}
    for source, target in (('country', 'country'), ('regionName', 'region'), ('city', 'city')):
        value = data.get(source)
        if isinstance(value, str) and len(value) <= 256 and not any(ord(c) < 32 for c in value):
            result[target] = value
    return result


def request_trace(route):
    import requests
    from ip_check.http_response import require_success
    kind = route.get('probe_kind', 'provider')
    target = route.get('target', 'https://' + route['host'] + '/cdn-cgi/trace')
    result = {'host': route['host'], 'target': target, 'probe_kind': kind, 'ip': None, 'country_code': None, 'observed_at': time.time()}
    delays = []
    try:
        with requests.Session() as session:
            session.trust_env = False  # no netrc, shell proxy, cookie or CA overrides
            with session.get(target,
                             proxies={'https': route['proxy'], 'http': route['proxy']}, timeout=(3, 5),
                             allow_redirects=False, stream=True,
                             headers={'Accept-Encoding': 'identity'}) as response:
                if kind == 'third_party' and response.headers.get('X-Rl') == '0':
                    try:
                        ttl = int(response.headers.get('X-Ttl', '60'))
                        delays.append(max(60, ttl))
                    except ValueError:
                        delays.append(60)
                require_success(response, delays.append)
                raw = response.raw.read(8193, decode_content=False)
                if len(raw) > 8192: raise ValueError('oversized trace')
                result.update(parse_public(raw) if kind == 'third_party' else parse_trace(raw, route['host']))
    except (requests.RequestException, ValueError, KeyError, UnicodeError) as exc:
        result['error'] = ('目标返回 HTTP ' + str(response.status_code) if 'response' in locals() and response.status_code != 200
                           else '未取得有效目标回执：' + type(exc).__name__) + '；未重试。'
    if delays: result['retry_after_seconds'] = max(delays)
    return result


class ProbeCache:
    """Owned by the connector; caller serializes scan/probe work with its lock."""
    def __init__(self):
        self.entries = {}
        self.target_until = {}

    def read(self, pid, instance, probe_kind='provider'):
        try:
            route = route_for(pid, instance, probe_kind=probe_kind)
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            return {'instance': instance, 'probe_kind': probe_kind, 'error': str(exc) if isinstance(exc, RouteUnavailable) else '进程读取失败：' + type(exc).__name__,
                    'ip': None, 'observed_at': time.time()}
        key = hashlib.sha256(json.dumps(route, sort_keys=True).encode()).hexdigest()
        now = time.monotonic()
        if key in self.entries and self.entries[key][0] > now:
            until, result = self.entries[key]
            return dict(result, cached=True, retry_after_seconds=math.ceil(until - now))
        shared_until = self.target_until.get(route['host'], 0)
        if shared_until > now:
            return {'instance': instance, 'probe_kind': probe_kind, 'host': route['host'],
                    'target': route.get('target'), 'ip': None, 'observed_at': time.time(),
                    'error': '此诊断目标处于共享冷却期，未发送请求；切换进程不会绕过等待。',
                    'retry_after_seconds': math.ceil(shared_until - now)}
        if key not in self.entries and len(self.entries) >= 128:
            self.entries = {k: v for k, v in self.entries.items() if v[0] > now}
            if len(self.entries) >= 128:
                return {'instance': instance, 'probe_kind': probe_kind, 'error': '本机探测冷却记录已满，请稍后重试。', 'ip': None}
        try:
            child = subprocess.run(module_command('agent_monitor.network.process_probe'),
                                   input=json.dumps(route), capture_output=True, text=True, timeout=10, check=True)
            result = json.loads(child.stdout)
            if not isinstance(result, dict) or result.get('host') != route['host']: raise ValueError('wrong probe target')
        except (OSError, ValueError, subprocess.SubprocessError):
            result = {'host': route['host'], 'ip': None, 'country_code': None,
                      'error': '目标探测未完成或超时，未重试。', 'observed_at': time.time()}
        result.update(instance=instance, probe_kind=probe_kind, target=route.get('target'), proxy_address=route['proxy_address'],
                      evidence='fixed_target_probe_via_observed_proxy')
        # Recheck the process after the request: its exit does not erase a probe
        # receipt, but invalidates any present-tense connection attribution.
        try:
            current = route_for(pid, instance, probe_kind=probe_kind)
            result['process_still_matches'] = current == route
        except (OSError, ValueError, subprocess.SubprocessError):
            result['process_still_matches'] = False
        delay = result.get('retry_after_seconds', 0)
        if not isinstance(delay, (int, float)) or not math.isfinite(delay): delay = 0
        cooldown = max(60, delay)
        if probe_kind == 'third_party' or delay:
            self.target_until[route['host']] = time.monotonic() + cooldown
        self.entries[key] = time.monotonic() + cooldown, result
        return dict(result, cached=False, retry_after_seconds=math.ceil(cooldown))


if __name__ == '__main__':
    route = json.loads(sys.stdin.read(pn.MAX_BYTES))
    kind = route.get('probe_kind', 'provider')
    if kind not in PROBE_KINDS or route.get('target') not in {target_for(agent, kind) for agent in TARGETS}: raise SystemExit(2)
    print(json.dumps(request_trace(route)))
