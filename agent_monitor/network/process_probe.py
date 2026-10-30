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
from urllib.parse import urlsplit

from agent_monitor.network import process_network as pn
from agent_monitor.network.account_egress import parse_trace
from agent_monitor.runtime.python_entrypoint import module_command

TARGETS = {'codex': 'chatgpt.com', 'claude': 'api.anthropic.com'}


class RouteUnavailable(ValueError):
    """Only fixed operator-facing messages, never raw OS/client exceptions."""


def route_for(pid, instance, backend=None):
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
    if kind not in TARGETS:
        raise RouteUnavailable('无法确认进程类型，未发出探测。')
    if any(env.get(key) for key in pn.ENDPOINT_KEYS):
        raise RouteUnavailable('进程设置了自定义服务地址；未向默认诊断目标发请求。')
    # Do not guess precedence between clients or override a bypass policy.
    if any(env.get(key) for key in ('NO_PROXY', 'no_proxy')):
        raise RouteUnavailable('进程设置了绕过规则，目标选路未核实；未发出探测。')
    values = {env[key] for key in ('HTTPS_PROXY', 'https_proxy', 'ALL_PROXY', 'all_proxy') if env.get(key)}
    if len(values) != 1:
        raise RouteUnavailable('未取得唯一的 HTTPS/ALL 代理配置，未发出探测。')
    proxy = values.pop()
    parsed = urlsplit(proxy)
    if parsed.scheme not in ('http', 'https', 'socks5', 'socks5h', 'socks4') or parsed.path not in ('', '/') or parsed.query or parsed.fragment:
        raise RouteUnavailable('代理格式不支持，未发出探测。')
    host = parsed.hostname
    # Avoid fresh DNS attribution: require an observed literal endpoint.
    if host == 'localhost':
        addresses = ('127.0.0.1', '::1')
    else:
        try: addresses = (str(ipaddress.ip_address(host)),)
        except ValueError: raise RouteUnavailable('代理使用主机名，当前连接归属未核实；未发出探测。') from None
    port = parsed.port or {'http': 80, 'https': 443, 'socks5': 1080, 'socks5h': 1080, 'socks4': 1080}[parsed.scheme]
    peers = {('[' + address + ']' if ':' in address else address) + ':' + str(port) for address in addresses}
    matches = [conn for conn in backend.connections(pid) if conn['peer'] in peers]
    if not matches or f'{pid}:{backend.identity(pid)}' != instance or backend.executable(pid) != executable:
        raise RouteUnavailable('未观察到此实例到配置代理的当前连接；未发出探测。')
    # Pin localhost to the address actually observed, keeping userinfo private.
    if host == 'localhost':
        peer = matches[0]['peer']
        auth = parsed.netloc.rsplit('@', 1)[0] + '@' if '@' in parsed.netloc else ''
        proxy = parsed.scheme + '://' + auth + peer
    return {'host': TARGETS[kind], 'proxy': proxy, 'instance': instance,
            'proxy_address': pn.safe_address(proxy)['address']}


def request_trace(route):
    import requests
    from ip_check.http_response import require_success
    result = {'host': route['host'], 'ip': None, 'country_code': None, 'observed_at': time.time()}
    delays = []
    try:
        with requests.Session() as session:
            session.trust_env = False  # no netrc, shell proxy, cookie or CA overrides
            with session.get('https://' + route['host'] + '/cdn-cgi/trace',
                             proxies={'https': route['proxy']}, timeout=(3, 5),
                             allow_redirects=False, stream=True,
                             headers={'Accept-Encoding': 'identity'}) as response:
                require_success(response, delays.append)
                raw = response.raw.read(8193, decode_content=False)
                if len(raw) > 8192: raise ValueError('oversized trace')
                result.update(parse_trace(raw, route['host']))
    except (requests.RequestException, ValueError, KeyError, UnicodeError) as exc:
        result['error'] = ('目标返回 HTTP ' + str(response.status_code) if 'response' in locals() and response.status_code != 200
                           else '未取得有效目标回执：' + type(exc).__name__) + '；未重试。'
    if delays: result['retry_after_seconds'] = delays[0]
    return result


class ProbeCache:
    """Owned by the connector; caller serializes scan/probe work with its lock."""
    def __init__(self):
        self.entries = {}

    def read(self, pid, instance):
        try:
            route = route_for(pid, instance)
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            return {'instance': instance, 'error': str(exc) if isinstance(exc, RouteUnavailable) else '进程读取失败：' + type(exc).__name__,
                    'ip': None, 'observed_at': time.time()}
        key = hashlib.sha256(json.dumps(route, sort_keys=True).encode()).hexdigest()
        now = time.monotonic()
        if key in self.entries and self.entries[key][0] > now:
            until, result = self.entries[key]
            return dict(result, cached=True, retry_after_seconds=math.ceil(until - now))
        if key not in self.entries and len(self.entries) >= 128:
            self.entries = {k: v for k, v in self.entries.items() if v[0] > now}
            if len(self.entries) >= 128:
                return {'instance': instance, 'error': '本机探测冷却记录已满，请稍后重试。', 'ip': None}
        try:
            child = subprocess.run(module_command('agent_monitor.network.process_probe'),
                                   input=json.dumps(route), capture_output=True, text=True, timeout=10, check=True)
            result = json.loads(child.stdout)
            if result.get('host') != route['host']: raise ValueError('wrong probe target')
        except (OSError, ValueError, subprocess.SubprocessError):
            result = {'host': route['host'], 'ip': None, 'country_code': None,
                      'error': '目标探测未完成或超时，未重试。', 'observed_at': time.time()}
        result.update(instance=instance, proxy_address=route['proxy_address'],
                      evidence='fixed_target_probe_via_observed_proxy')
        # Recheck the process after the request: its exit does not erase a probe
        # receipt, but invalidates any present-tense connection attribution.
        try:
            current = route_for(pid, instance)
            result['process_still_matches'] = current == route
        except (OSError, ValueError, subprocess.SubprocessError):
            result['process_still_matches'] = False
        delay = result.get('retry_after_seconds', 0)
        if not isinstance(delay, (int, float)) or not math.isfinite(delay): delay = 0
        cooldown = max(60, delay)
        self.entries[key] = time.monotonic() + cooldown, result
        return dict(result, cached=False, retry_after_seconds=math.ceil(cooldown))


if __name__ == '__main__':
    route = json.loads(sys.stdin.read(pn.MAX_BYTES))
    if route.get('host') not in TARGETS.values(): raise SystemExit(2)
    print(json.dumps(request_trace(route)))
