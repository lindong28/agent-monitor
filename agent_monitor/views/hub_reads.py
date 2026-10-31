"""Fixed-origin read adapter; account and collection actions stay at the Hub."""
from urllib.parse import urlencode, unquote, quote

from agent_monitor import hub_config
from agent_monitor.network import network_policy

PATHS = frozenset(('/api/overview', '/api/pivot', '/api/pivot-filters', '/api/sync-status',
                   '/api/collection', '/api/cost-diagnostics', '/api/sessions', '/api/timezone'))
QUERY_KEYS = frozenset(('range', 'start', 'end', 'machine', 'agent', 'project', 'model',
                       'x', 'group', 'group_by', 'metric', 'compare', 'with_filters',
                       'cost_status', 'sort', 'order', 'scope', 'session_agent', 'sync'))
# Existing Web navigation carries these local presentation fields into API reads.
NAVIGATION_KEYS = frozenset(('session', 'session_machine', 'page', 'usage_page', 'usage_model', 'return'))


def origin():
    return hub_config.origin() if hub_config.enabled() and not hub_config.is_hub() else None


def supports(path):
    if path in PATHS:
        return True
    if not path.startswith('/api/session/'):
        return False
    identifier = unquote(path[len('/api/session/'):])
    return bool(identifier and identifier not in ('.', '..') and
                not any(c in identifier for c in '/\\?#%') and
                not any(ord(c) < 32 for c in identifier))


def read(path, query, *, target=None, session_factory=None):
    if not supports(path) or set(query) - QUERY_KEYS - NAVIGATION_KEYS:
        raise ValueError('Hub 只读接口不支持此路径或参数。')
    target = target or origin()
    if not target:
        raise ValueError('未配置远程 Hub。')
    params = {key: value for key, value in query.items() if key in QUERY_KEYS}
    params['sync'] = ['0']
    if path.startswith('/api/session/'):
        path = '/api/session/' + quote(unquote(path[len('/api/session/'):]), safe='')
    # This is the project's existing system-proxy path, not a new personal proxy.
    with (session_factory or network_policy.session)() as session:
        response = session.get(target + path + '?' + urlencode(params, doseq=True),
                               headers={'X-Agent-Monitor-Read-Hop': '1'}, timeout=(3, 20), allow_redirects=False)
        if response.status_code != 200:
            raise ValueError(f'Hub 读取返回 HTTP {response.status_code}；未改用本机统计。')
        result = response.json()
    if not isinstance(result, dict):
        raise ValueError('Hub 返回的数据格式不兼容；未改用本机统计。')
    return result
