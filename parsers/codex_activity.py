"""Read local launcher activity, never shared logs or provider credentials."""
from datetime import datetime, timezone
import json
import os

from agent_monitor.runtime.process_identity import IdentityUnknown, identity


def live(records):
    """Recheck captured task identities immediately before a follow-only dispatch."""
    for record in records:
        try:
            if identity(record['pid']) == record['process_identity']:
                return True
        except (OSError, ValueError, IdentityUnknown):
            continue
    return False


def observe(profile):
    result = {'running_tasks': 0, 'activity_checked_at': datetime.now(timezone.utc).isoformat(),
              'activity_error': None, '_records': []}
    root = profile.home.parent
    directory = root / 'running-tasks'
    try:
        if directory.is_symlink():
            raise ValueError('symlink activity directory')
        paths = list(directory.glob('*.json')) if directory.exists() else []
        if not paths:
            return result
        bound = json.loads((root / 'profile.json').read_text())['identity']
        for path in paths:
            try:
                if path.is_symlink():
                    raise ValueError('symlink activity record')
                record = json.loads(path.read_text())
                if (record.get('version') != 1 or record.get('profile') != profile.account['name']
                        or not isinstance(record.get('account'), dict)
                        or record['account'] != bound
                        or any(not isinstance(bound.get(k), str) or not bound[k]
                               for k in ('account_id', 'email', 'subject'))
                        or bound['account_id'] != profile.account['account_id']
                        or bound['email'] != profile.account['email']
                        or not isinstance(record.get('process_identity'), dict)
                        or record['process_identity'].get('uid') != os.getuid()):
                    raise ValueError('invalid activity identity')
                if identity(record.get('pid')) == record['process_identity']:
                    result['_records'].append(record)
            except FileNotFoundError:
                continue  # Normal concurrent launcher exit.
            except (OSError, ValueError, KeyError, TypeError, AttributeError, IdentityUnknown):
                result['activity_error'] = '部分运行记录无效或无法核实；仅跟随已核实的任务。'
    except (OSError, ValueError, KeyError, TypeError):
        result['activity_error'] = '运行状态无法读取；未据此自动选择账号。'
    result['running_tasks'] = len({(r['pid'], json.dumps(r['process_identity'], sort_keys=True)) for r in result['_records']})
    return result
