"""Read machine-local account holds and privately compare credential digests.

The account profile is the authority. Readers run under provider admission;
operator changes take the same lock, so a disable cannot race a new request.
"""

import json
import hashlib
from pathlib import Path
from parsers.accounts import quota_identity
from agent_monitor.accounts.upstream_controls import Controls
from agent_monitor.quota.codex_quota import credential_digest


def unreadable_credential_holds(state_root):
    for path in (Path(state_root) / "upstream_controls/accounts").glob("*/codex.json"):
        state = Controls(path.parent).read("codex")
        if (state.get("pause") or {}).get("id") == "unreadable":
            return True
        rejected = state.get("rejected_credentials", {})
        if not isinstance(rejected, dict) or any(not isinstance(key, str) or len(key) != 64
                or any(c not in "0123456789abcdef" for c in key) for key in rejected):
            return True
    return False


def controls(state_root, account_id, label):
    fingerprint = hashlib.sha256(quota_identity("codex", account_id, label).encode()).hexdigest()
    return Controls(Path(state_root) / "upstream_controls/accounts" / fingerprint)


def rejected_credential(state_root, auth_path, *, skip_identity=None):
    paths = list((Path(state_root) / "upstream_controls/accounts").glob("*/codex.json"))
    if not paths:
        return False
    digest = credential_digest(auth_path) if auth_path is not None else None
    if not digest:
        return False
    for path in paths:
        if path.parent.name == skip_identity:
            continue
        rejected = Controls(path.parent).read("codex").get("rejected_credentials", {})
        if isinstance(rejected, dict) and digest in rejected:
            return True
    return False


def acquire(state_root, account_id, label, *, pause_id, **options):
    """Keep the shared lane authoritative while probing one account pause."""
    shared = Controls(Path(state_root) / "upstream_controls").acquire("codex", **options)
    try:
        local = controls(state_root, account_id, label).acquire("codex", probe_pause_id=pause_id, **options)
    except BaseException:
        shared.close()
        raise
    return ScopedLease(local, shared)


class ScopedLease:
    def __init__(self, local, shared):
        self.local, self.shared = local, shared
        self.state, self.controls = local.state, local.controls

    def pause(self, reason="unknown", *, failure=None, source=None):
        self.local.pause(reason, failure=failure, source=source)
        if reason != "localRecoveryCommitFailed":
            self.shared.pause(reason, failure=failure, source=source)

    def pause_local(self, reason, *, failure=None, source=None):
        self.local.pause(reason, failure=failure, source=source)

    def close(self, *, completed=True):
        try:
            self.local.close(completed=completed)
        finally:
            # A committed local recovery already removed its own active marker;
            # the shared lease still must close normally.
            self.shared.close(completed=completed or not self.local.state.get("active"))


def blocked(state_root, account_id, label, *, auth_path=None, probe_pause_id=None, allow_rejected_login=False):
    if unreadable_credential_holds(state_root):
        return "账号凭据保护记录无法核实，请先修复本机保护状态；未发出上游请求。"
    for path in (Path(state_root) / "codex-accounts").glob("*/profile.json"):
        record = json.loads(path.read_text())
        if (record.get("account_id") == account_id
                and str(record.get("email", "")).casefold() == str(label).casefold()):
            if record.get("disabled"):
                return "此账号已由用户停用，未发出上游请求。"
            if any(key in record.get("restrictions", {})
                   for key in ("authentication", "unknown", "rate_limit", "login_rate_limit")):
                return "此账号仍有本地限制，请在账号操作中核对；未发出上游请求。"
    local = controls(state_root, account_id, label)
    state = local.read("codex")
    pause = state.get("pause")
    selected = bool(probe_pause_id and pause and pause.get("id") == probe_pause_id)
    if (pause or state.get("active")) and not selected:
        return "此账号的配额认证已暂停，请先修复登录；未发出上游请求。"
    if not allow_rejected_login and rejected_credential(state_root, auth_path,
            skip_identity=local.root.name if selected else None):
        return "此凭据已被拒绝，请重新登录取得新凭据；未发出上游请求。"
    return None
