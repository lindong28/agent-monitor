"""Machine-owned opt-in for discovered Codex profiles; never contacts a provider."""
import argparse
import fcntl
import hashlib
import json
import shlex
import subprocess
import uuid
from pathlib import Path

from agent_monitor.paths import PROJECT_ROOT
from agent_monitor.quota.quota_messages import write_message


def catalog():
    from parsers import codex_profiles
    result = []
    for profile in codex_profiles.discover():
        if not profile.account:
            continue
        account = profile.account
        identity = [account['name'], account['account_id'], account['email']]
        key = hashlib.sha256(json.dumps(identity, ensure_ascii=False).encode()).hexdigest()
        result.append({'key': key, 'profile': identity[0], 'account_id': identity[1], 'account_label': identity[2]})
    return sorted(result, key=lambda item: item['key'])


def read(root=None):
    from agent_monitor import hub_config
    root = Path(root) if root is not None else PROJECT_ROOT
    path = root / 'state/quota-settings.json'
    try:
        state = json.loads(path.read_text())
        if (state.get('version') != 1 or not isinstance(state.get('selected'), list)
                or not isinstance(state.get('revision'), str)):
            raise ValueError('invalid quota settings')
    except FileNotFoundError:
        state = {'version': 1, 'revision': 'initial', 'selected': None}
    rows = catalog()
    # An explicit empty selection overrides legacy operator configuration.
    selected = state['selected']
    if selected is None:
        selected = [row['key'] for row in rows] if hub_config.managed_quota_auto_refresh() else []
    for row in rows:
        row['enabled'] = row['key'] in selected
    fingerprint = hashlib.sha256(json.dumps([state['revision'], rows, hub_config.quota_interval()], sort_keys=True).encode()).hexdigest()
    return {'revision': fingerprint, 'accounts': rows, 'interval_seconds': hub_config.quota_interval()}


def selected(profile, root=None):
    if not profile.account:
        return False
    return any(row['enabled'] and (row['profile'], row['account_id'], row['account_label']) ==
               (profile.account['name'], profile.account['account_id'], profile.account['email'])
               for row in read(root)['accounts'])


def change(request, root=None):
    root = Path(root) if root is not None else PROJECT_ROOT
    path = root / 'state/quota-settings.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_suffix('.lock').open('a') as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        current = read(root)
        keys = request.get('selected')
        if (request.get('revision') != current['revision'] or not isinstance(keys, list)
                or any(not isinstance(key, str) for key in keys) or len(set(keys)) != len(keys)
                or not set(keys).issubset({row['key'] for row in current['accounts']})):
            raise ValueError('账号或设置已变化，请更新状态后重新选择。')
        write_message(path, {'version': 1, 'revision': uuid.uuid4().hex, 'selected': sorted(keys)})
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
    if not isinstance(value, dict) or 'revision' not in value or not isinstance(value.get('accounts'), list):
        raise ValueError('目标机器未返回有效设置；请先更新状态核对，不要重复提交。')
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--request')
    args = parser.parse_args()
    try:
        value = read() if args.request is None else change(json.loads(args.request))
    except (OSError, ValueError, TypeError) as exc:
        value = {'error': str(exc) if isinstance(exc, ValueError) else '设置未能读取或保存，请核对本机状态。'}
    print(json.dumps(value, ensure_ascii=False))


if __name__ == '__main__':
    main()
