"""One admission policy for process-report previews and explicit probes.

Raw environment/proxy values stay inside the collector/probe process.
"""
import ipaddress
from urllib.parse import urlsplit

TARGETS = {'codex': 'chatgpt.com', 'claude': 'api.anthropic.com'}
PROBE_KINDS = ('provider', 'third_party')
ENDPOINT_KEYS = ('OPENAI_BASE_URL', 'ANTHROPIC_BASE_URL', 'OPENAI_API_BASE')


class RouteUnavailable(ValueError):
    def __init__(self, message, code='route_unverified', next_step='重新读取进程；保留现有配置，不要为探测改变出口。'):
        super().__init__(message)
        self.code, self.next_step = code, next_step


def target_for(agent, probe_kind):
    if probe_kind not in PROBE_KINDS or agent not in TARGETS:
        raise RouteUnavailable('无法确认诊断类型，未发出探测。')
    return ('http://ip-api.com/json/?fields=status,query,countryCode,country,regionName,city,isp,org,as,timezone,proxy,hosting'
            if probe_kind == 'third_party' else 'https://' + TARGETS[agent] + '/cdn-cgi/trace')


def from_snapshot(agent, env, connections, instance, probe_kind='provider'):
    target = target_for(agent, probe_kind)
    if env is None:
        raise RouteUnavailable('启动环境未取得，无法核实探测路径。', 'environment_unavailable')
    if any(env.get(key) for key in ENDPOINT_KEYS):
        raise RouteUnavailable('进程设置了自定义服务地址，当前探测适配尚不支持。', 'custom_endpoint',
                               '可查看现有连接和代理链路；不要删除自定义地址来启用探测。')
    if any(env.get(key) for key in ('NO_PROXY', 'no_proxy')):
        raise RouteUnavailable('进程设置了绕过规则，诊断目标的选路尚未核实。', 'bypass_unverified',
                               '可查看被动连接证据；当前适配不解析客户端绕过规则，不要为此清空 NO_PROXY。')
    values = {env[key] for key in ('HTTPS_PROXY', 'https_proxy', 'ALL_PROXY', 'all_proxy') if env.get(key)}
    if len(values) != 1:
        raise RouteUnavailable('未取得唯一的 HTTPS/ALL 代理配置。', 'proxy_ambiguous')
    proxy = values.pop()
    try:
        parsed = urlsplit(proxy)
        if parsed.scheme not in ('http', 'https', 'socks5', 'socks5h', 'socks4') or parsed.path not in ('', '/') or parsed.query or parsed.fragment:
            raise ValueError()
        host = parsed.hostname
        addresses = ('127.0.0.1', '::1') if host == 'localhost' else (str(ipaddress.ip_address(host)),)
        port = parsed.port or {'http': 80, 'https': 443, 'socks5': 1080, 'socks5h': 1080, 'socks4': 1080}[parsed.scheme]
    except (ValueError, TypeError):
        raise RouteUnavailable('代理须为支持的协议和已观察的 IP 地址或 localhost；当前格式或主机名归属未核实。', 'proxy_unsupported') from None
    peers = {('[' + address + ']' if ':' in address else address) + ':' + str(port) for address in addresses}
    matches = [conn for conn in connections if conn.get('peer') in peers]
    if not matches:
        raise RouteUnavailable('本次未观察到进程连接此代理。', 'connection_missing',
                               '在 Agent 正常使用期间重新读取；没有当前连接不能证明直连。')
    if host == 'localhost':
        # Multiple loopback endpoints are not interchangeable for this probe.
        observed = {conn['peer'] for conn in matches}
        if len(observed) != 1:
            raise RouteUnavailable('localhost 同时对应多个已用地址，无法选定唯一探测路径。', 'proxy_ambiguous')
        auth = parsed.netloc.rsplit('@', 1)[0] + '@' if '@' in parsed.netloc else ''
        proxy = parsed.scheme + '://' + auth + observed.pop()
    safe = urlsplit(proxy)
    host_port = ('[' + safe.hostname + ']' if ':' in safe.hostname else safe.hostname) + (':' + str(safe.port) if safe.port else '')
    return {'host': urlsplit(target).hostname, 'target': target, 'probe_kind': probe_kind,
            'proxy': proxy, 'instance': instance, 'proxy_address': safe.scheme + '://' + host_port}


def options(agent, env, connections, instance):
    result = {}
    for kind in PROBE_KINDS:
        entry = {'target': target_for(agent, kind)}
        try:
            route = from_snapshot(agent, env, connections, instance, kind)
            entry.update(status='available', proxy_address=route['proxy_address'])
        except RouteUnavailable as exc:
            entry.update(status='blocked', reason=str(exc), code=exc.code, next_step=exc.next_step)
        result[kind] = entry
    return result
