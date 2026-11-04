"""Read machine-local account holds without opening provider credentials.

The account profile is the authority. Readers run under provider admission;
operator changes take the same lock, so a disable cannot race a new request.
"""

import json
from pathlib import Path


def blocked(state_root, account_id, label):
    for path in (Path(state_root) / "codex-accounts").glob("*/profile.json"):
        record = json.loads(path.read_text())
        if (record.get("account_id") == account_id
                and str(record.get("email", "")).casefold() == str(label).casefold()):
            if record.get("disabled"):
                return "此账号已由用户停用，未发出上游请求。"
            if any(key in record.get("restrictions", {})
                   for key in ("authentication", "unknown", "rate_limit", "login_rate_limit")):
                return "此账号仍有本地限制，请在账号操作中核对；未发出上游请求。"
    return None
