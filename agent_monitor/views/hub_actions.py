"""Same-origin dashboard actions, executed once by the configured account owner."""
import logging
from urllib.parse import urlencode

from agent_monitor.network import network_policy

logger = logging.getLogger(__name__)
ACCOUNT_ACTIONS = frozenset(('egress-preview', 'network-status', 'add', 'batch-start',
                            'batch-retry', 'refresh', 'login', 'resume', 'cancel',
                            'forget-login', 'release-rate-limit'))
POST_PATHS = frozenset('/api/codex-accounts/' + name for name in ACCOUNT_ACTIONS) | {
    '/api/account-memory/remove', '/api/quota-recovery', '/api/refresh'}
GET_PATHS = frozenset(('/api/codex-accounts', '/api/quota-recovery'))
HOP_HEADER = 'X-Agent-Monitor-Action-Hop'


def supports(method, path):
    return path in (POST_PATHS if method == 'POST' else GET_PATHS if method in ('GET', 'HEAD') else ())


def forward(method, path, query, payload, *, target, session_factory=None):
    """No redirects, ambient credentials, retries or local fallback.

    The caller validates the browser boundary before reaching this transport.
    Existing application capabilities and identity fields remain in the payload;
    browser Cookie/Authorization headers are never accepted by this interface.
    """
    method = 'GET' if method == 'HEAD' else method
    if not supports(method, path):
        return 404, {'error': '未找到此操作。'}
    keys = {'ack'} if path == '/api/refresh' else {'machine'} if method == 'GET' and path == '/api/quota-recovery' else set()
    if set(query) - keys:
        return 400, {'error': '操作参数无效；未发出请求。'}
    headers = {'X-Agent-Monitor': 'codex-accounts' if path.startswith('/api/codex-accounts') else 'action',
               'Origin': target, HOP_HEADER: '1'}
    url = target + path + ('?' + urlencode(query, doseq=True) if query else '')
    # Recovery already owns a 70s subprocess budget; statistics use a separate
    # short read adapter. A lost write receipt never authorizes another attempt.
    timeout = 90 if path == '/api/quota-recovery' and method == 'POST' else 30
    try:
        with (session_factory or network_policy.session)() as session:
            response = session.request(method, url, json=payload if method == 'POST' else None,
                                       headers=headers, timeout=(3, timeout), allow_redirects=False)
            if 300 <= response.status_code < 400:
                raise ValueError('unexpected_redirect')
            value = response.json()
            if not isinstance(value, dict):
                raise ValueError('unexpected_response_shape')
            return response.status_code, value
    except Exception as exc:
        # Exception strings can contain credentials/URLs. Keep stage and type,
        # while existing action handlers retain their own detailed receipts.
        logger.warning('Action forwarding failed: method=%s path=%s type=%s', method, path, type(exc).__name__)
        message = ('操作回执未取得，执行结果未知；请先更新状态核对，不要重复提交。'
                   if method == 'POST' else '账号服务暂不可用；请稍后更新状态。')
        return 502, {'error': message}
