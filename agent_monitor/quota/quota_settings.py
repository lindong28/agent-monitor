"""Machine-owned opt-in for discovered Codex profiles; never contacts a provider."""
import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import json
import shlex
import subprocess
import uuid
from pathlib import Path

from agent_monitor.paths import PROJECT_ROOT
from agent_monitor.quota.quota_messages import write_message


class RenewalSourceUnavailable(ValueError):
    def __init__(self):
        super().__init__('此账号的凭据来源未通过核验。请在所选配置中用官方文件 OAuth 登录同一账号，更新设置后再授权自动续期。')


def _digest(parts):
    return hashlib.sha256(json.dumps(parts, ensure_ascii=False).encode()).hexdigest()


def _renewal_key(key, auth_path):
    from agent_monitor.quota.codex_quota import recovery_source_digest
    try:
        return _digest([key, recovery_source_digest(auth_path)])
    except (OSError, RuntimeError):
        return None


def catalog(*, activity_records=False, include_activity=True):
    from parsers import codex_profiles, codex_activity
    result = []
    for profile in codex_profiles.discover():
        if not profile.account:
            continue
        account = profile.account
        identity = [account['name'], account['account_id'], account['email']]
        key = _digest(identity)
        activity = codex_activity.observe(profile) if include_activity else {}
        if not activity_records:
            activity.pop('_records', None)
        result.append({'key': key, 'profile': identity[0], 'account_id': identity[1], 'account_label': identity[2],
                       'renewal_key': _renewal_key(key, profile.home / 'auth.json'), **activity})
    return sorted(result, key=lambda item: item['key'])


def _load(root):
    path = Path(root) / 'state/quota-settings.json'
    try:
        state = json.loads(path.read_text())
    except FileNotFoundError:
        return {'version': 1, 'revision': 'initial', 'selected': None, 'renewals': []}
    if (not isinstance(state, dict) or state.get('version') != 1 or not isinstance(state.get('revision'), str)
            or not isinstance(state.get('selected'), list) or not isinstance(state.get('renewals', []), list)
            or any(not isinstance(key, str) for key in state['selected'])
            or any(not isinstance(key, str) for key in state.get('renewals', []))
            or type(state.get('follow_running', False)) is not bool):
        raise ValueError('自动查询设置文件无效。')
    return state


def read(root=None, *, activity_records=False, include_activity=True):
    from agent_monitor import hub_config
    state = _load(PROJECT_ROOT if root is None else root)
    rows = catalog(activity_records=activity_records, include_activity=include_activity)
    # An explicit empty selection overrides legacy operator configuration.
    selected = state['selected']
    if selected is None:
        selected = [row['key'] for row in rows] if hub_config.managed_quota_auto_refresh() else []
    follow = state.get('follow_running', False)
    for row in rows:
        row['enabled'] = row['key'] in selected
        row['renewal_enabled'] = bool(row.get('renewal_key') and row['renewal_key'] in state.get('renewals', []))
        row['effective_enabled'] = row['enabled'] or (follow and row.get('running_tasks', 0) > 0)
    # Live activity changes must not invalidate a user's pending settings edit.
    grants = set(state.get('renewals', []))
    unavailable_renewals = len(grants - {row.get('renewal_key') for row in rows})
    configuration = [(row['key'], row.get('renewal_key'), row['enabled'], row['renewal_enabled']) for row in rows]
    fingerprint = hashlib.sha256(json.dumps([state['revision'], configuration, unavailable_renewals, follow, hub_config.quota_interval()], sort_keys=True).encode()).hexdigest()
    return {'revision': fingerprint, 'accounts': rows, 'follow_running': follow,
            'unavailable_renewals': unavailable_renewals, 'interval_seconds': hub_config.quota_interval()}


def selected(profile, root=None):
    if not profile.account:
        return False
    return any(row['enabled'] and (row['profile'], row['account_id'], row['account_label']) ==
               (profile.account['name'], profile.account['account_id'], profile.account['email'])
               for row in read(root)['accounts'])


def dispatch_selected(profile, records, root=None):
    """Settings can change while a prior account query is still running."""
    from parsers import codex_activity
    current = read(root, include_activity=False)
    row = next((row for row in current['accounts'] if
                (row['profile'], row['account_id'], row['account_label']) ==
                (profile.account['name'], profile.account['account_id'], profile.account['email'])), None)
    return bool(row and (row['enabled'] or (current['follow_running'] and codex_activity.live(records))))


def renewal_authorized(root, profile, account, auth_path, *, activity_records=()):
    """Recheck the owning machine's grant immediately before automatic credential renewal."""
    from parsers import codex_activity
    key = _digest([profile, account.account_id, account.label])
    renewal_key = _renewal_key(key, auth_path)
    try:
        state = _load(root if root is not None else PROJECT_ROOT)
    except (OSError, ValueError, RecursionError):
        return False
    return bool(renewal_key and renewal_key in state.get('renewals', [])
                and (key in (state['selected'] or []) or (state.get('follow_running') and codex_activity.live(activity_records))))


@contextmanager
def renewal_dispatch(root, profile, account, auth_path, *, activity_records=()):
    """Serialize handing one RPC to Codex with saving or revoking its grant."""
    try:
        lock = (Path(root) / "state/quota-settings.lock").open("rb")
    except OSError:
        yield False
        return
    with lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_SH)
        yield renewal_authorized(root, profile, account, auth_path, activity_records=activity_records)


def settings_selection(request, current):
    keys = request.get('selected')
    rows = current['accounts']
    if (request.get('revision') != current['revision'] or not isinstance(keys, list)
            or any(not isinstance(key, str) for key in keys) or len(set(keys)) != len(keys)
            or not set(keys).issubset({row['key'] for row in rows})):
        raise ValueError('账号或设置已变化，请更新状态后重新选择。')
    follow = request.get('follow_running', current.get('follow_running', False))
    if type(follow) is not bool:
        raise ValueError('运行账号跟随设置必须为布尔值。')
    eligible = {row['renewal_key'] for row in rows if row.get('renewal_key') and (row['key'] in keys or follow)}
    previous = {row['renewal_key'] for row in rows if row.get('renewal_enabled')}
    renewals = request.get('renewals', sorted(previous & eligible))
    if (not isinstance(renewals, list) or any(not isinstance(key, str) for key in renewals)
            or len(set(renewals)) != len(renewals) or not set(renewals).issubset(eligible)):
        raise ValueError('请为已启用自动查询的账号选择当前凭据来源，然后更新设置。')
    return sorted(keys), follow, sorted(renewals)


def change(request, root=None):
    root = Path(root) if root is not None else PROJECT_ROOT
    path = root / 'state/quota-settings.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_suffix('.lock').open('a') as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        current = read(root)
        keys, follow, renewals = settings_selection(request, current)
        added = set(renewals) - {row['renewal_key'] for row in current['accounts'] if row.get('renewal_enabled')}
        if added:
            from parsers import codex_profiles
            from agent_monitor.quota import codex_quota
            for profile in codex_profiles.discover():
                if not profile.account:
                    continue
                name, aid, email = (profile.account[k] for k in ('name', 'account_id', 'email'))
                path_to_auth = profile.home / 'auth.json'
                renewal = _renewal_key(_digest([name, aid, email]), path_to_auth)
                if renewal in added:
                    try:
                        codex_quota.check_identity(path_to_auth, aid, email)
                        codex_quota.file_auth_recovery_preflight(path_to_auth)
                    except codex_quota.ReadingError:
                        raise RenewalSourceUnavailable() from None
                    added.remove(renewal)
            if added:
                raise ValueError('凭据来源已变化，请更新设置后重新选择。')
        write_message(path, {'version': 1, 'revision': uuid.uuid4().hex, 'selected': keys,
                             'follow_running': follow, 'renewals': renewals})
        return read(root)


def on_machine(machine, request=None):
    if machine.is_self:
        return read() if request is None else change(request)
    from agent_monitor.snapshots import sync
    command = '~/.local/bin/agent-monitor quota-settings'
    if request is not None:
        command += ' --request ' + shlex.quote(json.dumps(request))
    result = subprocess.run([*sync._SSH_PREFIX, machine.ssh_host, command], capture_output=True,
                            text=True, timeout=15, check=True)
    value = json.loads(result.stdout)
    if isinstance(value, dict) and value.get('code') == 'renewal_source_unavailable':
        raise RenewalSourceUnavailable()
    if not isinstance(value, dict) or 'revision' not in value or not isinstance(value.get('accounts'), list):
        raise ValueError('目标机器未返回有效设置；请先更新状态核对，不要重复提交。')
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--request')
    args = parser.parse_args()
    try:
        value = read() if args.request is None else change(json.loads(args.request))
    except RenewalSourceUnavailable as exc:
        value = {'code': 'renewal_source_unavailable', 'error': str(exc)}
    except (OSError, ValueError, TypeError) as exc:
        value = {'error': str(exc) if isinstance(exc, ValueError) else '设置未能读取或保存，请核对本机状态。'}
    print(json.dumps(value, ensure_ascii=False))


if __name__ == '__main__':
    main()
