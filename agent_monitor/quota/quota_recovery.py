"""Explicit, account-bound Codex recovery. No retries or model turns."""

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from agent_monitor import diagnostics
from agent_monitor.network import network_policy


def validate_exclusions(value):
    if (not isinstance(value, dict) or set(value) - {"claude", "codex"}
            or any(not isinstance(items, list) or len(items) > 256
                   or any(not isinstance(item, str) or not re.fullmatch(r"\*|[0-9a-f]{64}", item)
                          for item in items) for items in value.values())):
        raise ValueError("无效的本轮账号暂停范围")


def validate_request(value):
    fields = {"machine", "account_id", "account_label", "pause_id"}
    if (not isinstance(value, dict) or set(value) - {"profile", "mode", "operation_id", "pause_scope", "source_digest"} != fields
            or any(not isinstance(value[k], str) or not value[k] or len(value[k]) > 256 for k in fields)
            or not re.fullmatch(r"[0-9a-f]{32}", value["pause_id"])):
        raise ValueError("请指定机器、完整账号身份和当前暂停标识。")
    if "profile" in value and (not isinstance(value["profile"], str) or (value["profile"] and not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}", value["profile"]))):
        raise ValueError("账号来源无效。")
    if "mode" in value and (not isinstance(value["mode"], str) or value["mode"] not in {"renew_auth", "reauthorize"}
            or value["mode"] == "reauthorize" and not value.get("profile", "").startswith("monitor-")):
        raise ValueError("认证修复模式或账号来源无效。")
    if "source_digest" in value and (value.get("mode") != "renew_auth" or not isinstance(value["source_digest"], str)
            or not re.fullmatch(r"[0-9a-f]{64}", value["source_digest"])):
        raise ValueError("认证来源标识无效。")
    if value.get("mode") == "renew_auth" and not value.get("profile", "").startswith("monitor-") and not value.get("source_digest"):
        raise ValueError("认证修复必须绑定原凭据来源，请更新状态。")
    if value.get("mode") == "reauthorize" or "operation_id" in value and value.get("profile", "").startswith("monitor-"):
        try:
            if str(uuid.UUID(value["operation_id"])) != value["operation_id"]:
                raise ValueError()
        except (KeyError, ValueError, TypeError):
            raise ValueError("重新授权必须绑定原配额任务。") from None
    elif "operation_id" in value:
        raise ValueError("原配额任务只适用于 Monitor 账号恢复。")
    if "pause_scope" in value:
        validate_selection(value.get("profile"), value["pause_scope"])
    return value


def validate_selection(profile, pause_scope):
    if pause_scope != "account" or not isinstance(profile, str) or not profile.startswith("monitor-"):
        raise ValueError("账号恢复范围无效。")
    try:
        if str(uuid.UUID(profile[8:])) != profile[8:]:
            raise ValueError()
    except (ValueError, TypeError):
        raise ValueError("账号来源无效。") from None


def approval_scope(value):
    scope = [value["machine"], value["account_id"]]
    scope = scope + [value["account_label"], value["profile"] or "native"] if "profile" in value else scope
    scope = scope + [value["mode"]] if "mode" in value else scope
    scope = scope + [value["operation_id"]] if "operation_id" in value else scope
    scope = scope + ["account"] if value.get("pause_scope") == "account" else scope
    return scope + [value["source_digest"]] if "source_digest" in value else scope


def managed_network_context(value, supplied):
    """Validate the failed task and safe prior context before selecting a route."""
    from agent_monitor.accounts import codex_accounts, account_holds
    manager = codex_accounts.manager
    profile = value.get("profile", "")
    if not profile.startswith("monitor-"):
        raise ValueError("此网络选择只适用于 Monitor 账号。")
    profile_id = profile[8:]
    record = manager.read(manager.directory(profile_id))
    operation = record.get("operation") or {}
    context = manager.recovery_network_context(profile_id)
    controls = (account_holds.controls(manager.root.parent, record.get("account_id"), record["email"])
                if value.get("pause_scope") == "account" else manager.controls)
    state = controls.read("codex")
    if (record.get("account_id") != value["account_id"] or record.get("email", "").casefold() != value["account_label"].casefold()
            or operation.get("id") != value.get("operation_id") or supplied != context
            or not operation.get("refresh_only") or operation.get("login_only")
            or operation.get("stage") not in {"failed", "interrupted"} or operation.get("message_status") != "not_sent"
            or state.get("active") or (state.get("pause") or {}).get("id") != value["pause_id"]
            or (state.get("pause") or {}).get("probe_attempt")):
        raise codex_accounts.ActionError("原配额任务、暂停或网络上下文已变化，请更新状态后重新确认。", 409)
    return context


def approval_action(value):
    return {"renew_auth": "quota-auth-recovery", "reauthorize": "quota-login-recovery"}.get(value.get("mode"), "quota-recovery")


def local_status(*, profile=None, pause_scope=None):
    """Read local files and machine identity, without initializing a provider."""
    from agent_monitor.snapshots import exporter, generation
    from agent_monitor.accounts.upstream_controls import Controls
    from parsers import accounts
    shared = Controls(exporter.ROOT / "state/upstream_controls").read("codex")
    state = shared
    from agent_monitor.quota import quota_service, quota_refresh
    from agent_monitor.accounts import account_holds
    if pause_scope is not None:
        validate_selection(profile, pause_scope)
        from agent_monitor import hub_config
        if not hub_config.is_hub():
            raise ValueError("账号恢复只支持账号所在 Hub。")
        home = quota_service.recovery_home(profile, exporter.ROOT)
        record = json.loads((home.parent / "profile.json").read_text())
        if record.get("id") != profile[8:] or not record.get("account_id"):
            raise ValueError("账号来源已变化。")
        state = account_holds.controls(exporter.ROOT / "state", record["account_id"], record["email"]).read("codex")
    failure = (state.get("pause") or {}).get("failure") or {}
    profile, auth, account, resolution_reason = quota_service.resolve_recovery_source(
        {**failure, **({"account_profile": profile} if pause_scope == "account" else {})}, exporter.ROOT)
    auth = auth or accounts.CODEX_AUTH
    home = auth.parent
    source = "default" if home == Path.home() / ".codex" else "configured"
    if (home.parent / "profile.json").is_file() and home.name == "codex":
        source = "managed"
    from agent_monitor.accounts import account_holds
    blocked_reason = account_holds.blocked(exporter.ROOT / "state", account.account_id, account.label, auth_path=auth,
        probe_pause_id=(state.get("pause") or {}).get("id") if pause_scope == "account" else None) if account else None
    if pause_scope == "account" and (shared.get("pause") or shared.get("active")):
        blocked_reason = "本机共享通道仍有暂停或在办操作，请先核对共享保护。"
    auth_recovery_available = False
    source_digest = None
    login_recovery = None
    local_commit = None
    from agent_monitor.quota import codex_quota
    if source == "managed" and profile and profile.startswith("monitor-"):
        from agent_monitor.quota.codex_quota import authentication_quota_failure
        try:
            record = json.loads((home.parent / "profile.json").read_text())
            from agent_monitor import hub_config
            from agent_monitor.quota.codex_quota import reauthorization_context
            if account and not blocked_reason and hub_config.is_hub():
                login_recovery = reauthorization_context(record, state)
                from agent_monitor.quota.codex_quota import local_commit_context
                local_commit = local_commit_context(record, state)
                if pause_scope == "account":
                    for binding in (login_recovery, local_commit):
                        if binding:
                            binding["pause_scope"] = "account"
            operation = record.get("operation") or {}
            auth_recovery_available = bool(account and not blocked_reason and authentication_quota_failure(failure)
                and record.get("account_id") == account.account_id
                and record.get("email", "").casefold() == (account.label or "").casefold()
                and operation.get("stage") in {"failed", "interrupted"} and operation.get("refresh_only")
                and not operation.get("login_only") and operation.get("message_status") == "not_sent")
        except (OSError, ValueError, AttributeError):
            pass  # A capability cannot be granted from unreadable local state.
    elif (account and not blocked_reason and not resolution_reason
            and codex_quota.authentication_quota_failure(failure)
            and failure.get("account_fingerprint") == quota_refresh.account_fingerprint("codex", account)):
        try:
            codex_quota.file_auth_recovery_preflight(auth)
            source_digest = codex_quota.recovery_source_digest(auth)
            auth_recovery_available = True
        except codex_quota.ReadingError:
            pass
    context = {}
    if source == "managed" and profile and profile.startswith("monitor-"):
        from agent_monitor.accounts import codex_accounts
        try:
            context["network_context"] = codex_accounts.manager.recovery_network_context(profile[8:])
        except (codex_accounts.ActionError, OSError, ValueError):
            context["network_context"] = None
    return {**context, **({"pause_scope": "account", "shared_pause": shared.get("pause")} if pause_scope == "account" else {}),
            "source_host_identity": generation.self_certified_host_identity(),
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "pause": state.get("pause"), "active": state.get("active"),
            "account": {"account_id": account.account_id if account else None,
                        "account_label": account.label if account else None,
                        "source": source, "profile": home.parent.name if source == "managed" else None,
                        "auth_recovery_available": auth_recovery_available,
                        "source_digest": source_digest,
                        "login_recovery": login_recovery,
                        "local_commit": local_commit,
                        "account_quota_pause": (account_holds.controls(exporter.ROOT / "state", account.account_id,
                            account.label).read("codex").get("pause") if account else None),
                        "blocked_reason": blocked_reason,
                        "resolution_reason": resolution_reason,
                        "credential_path": str(auth), "quota_profile": profile or ""},
            "last_quota_retry": state.get("last_quota_retry"),
            "last_recovery": state.get("last_recovery")}


def read_status(machine, *, profile=None, pause_scope=None):
    if machine.is_self:
        return local_status(**({"profile": profile, "pause_scope": pause_scope} if pause_scope is not None else {}))
    if pause_scope is not None:
        raise ValueError("账号恢复只支持账号所在 Hub 本机。")
    from agent_monitor.snapshots import sync
    result = subprocess.run([*sync._SSH_PREFIX, machine.ssh_host,
                             "~/.local/bin/agent-monitor quota-recover --status"],
                            capture_output=True, text=True, timeout=15, check=True)
    value = json.loads(result.stdout)
    if not isinstance(value, dict) or not value.get("source_host_identity"):
        raise ValueError("目标机器未返回可核验的状态。")
    return value


class EgressPreviewError(ValueError):
    """A target machine did not return a bounded route-bridge preview."""


def preview_egress(machine):
    """Preview the provider egress on the machine that owns the account."""
    from agent_monitor.network import account_egress
    if machine.is_self:
        return account_egress.preview()
    from agent_monitor.snapshots import sync
    try:
        result = subprocess.run(
            [*sync._SSH_PREFIX, machine.ssh_host,
             "~/.local/bin/agent-monitor quota-recover --egress-preview"],
            capture_output=True, text=True, timeout=25, check=True,
        )
        value = json.loads(result.stdout)
    except (subprocess.SubprocessError, OSError, ValueError, TypeError) as exc:
        raise EgressPreviewError("目标机器未返回可核验的路由桥出口预览。") from exc
    if (not isinstance(value, dict) or not isinstance(value.get("checked_host"), str)
            or not value["checked_host"] or not isinstance(value.get("targets"), list)
            or len(value["targets"]) != len(account_egress.TARGETS)):
        raise EgressPreviewError("目标机器未返回可核验的路由桥出口预览。")
    return value


def safe_network(value):
    """Keep bounded bridge facts, excluding capabilities and full target URLs."""
    result = {"policy": "chromium-system", "coverage": "unavailable", "events": []}
    if not isinstance(value, dict) or value.get("coverage") != "recent_connections_only":
        return result
    result["coverage"] = "recent_connections_only"
    rows = value.get("events", [])
    if not isinstance(rows, list):
        return result
    for row in rows[-64:]:
        if not isinstance(row, dict):
            continue
        try:
            target = urlsplit(str(row.get("target", "")))
            if target.scheme not in ("http", "https") or not target.hostname:
                continue
            origin = target.scheme + "://" + target.hostname
        except ValueError:
            continue
        route = str(row.get("route", ""))
        if not re.fullmatch(r"(?:DIRECT|(?:PROXY|HTTPS|SOCKS|SOCKS4|SOCKS5) [a-zA-Z0-9.\[\]:_-]+)", route):
            route = "unavailable"
        outcome = row.get("result")
        item = {"target": origin, "route": route,
                "result": outcome if isinstance(outcome, str) and re.fullmatch(r"[a-z_]{1,64}", outcome) else "unknown"}
        if type(row.get("generation")) is int and row["generation"] >= 0:
            item["generation"] = row["generation"]
        if isinstance(row.get("at"), str):
            try:
                item["at"] = datetime.fromisoformat(row["at"].replace("Z", "+00:00")).isoformat()
            except ValueError:
                pass
        result["events"].append(item)
    return result


def local_recovery(value, *, network_route=None):
    from agent_monitor.snapshots import exporter
    from agent_monitor.quota import quota_service
    from agent_monitor.snapshots import generation
    from agent_monitor.quota import quota_refresh
    from agent_monitor.accounts.upstream_controls import Controls
    validate_request(value)
    if value.get("mode") == "reauthorize":
        raise ValueError("重新授权需要账号主机的异步 Web 入口；未发出请求。")
    # Establish the implementation identity before the irreversible provider read.
    commit = exporter.exporter_version()
    route = None
    admitted_probe = {}
    selected_controls = Controls(exporter.ROOT / "state/upstream_controls")
    if value.get("pause_scope") == "account":
        from agent_monitor.accounts import account_holds
        from agent_monitor import hub_config
        if not hub_config.is_hub():
            raise ValueError("账号恢复只支持账号所在 Hub。")
        selected_controls = account_holds.controls(exporter.ROOT / "state", value["account_id"], value["account_label"])
    try:
        # Scope observations without choosing a different endpoint or proxy policy.
        if network_route:
            network_route.require()
            with network_policy.using_route(network_route.endpoint):
                route = network_policy.child_environment(os.environ, scope=uuid.uuid4().hex)[network_policy.INTERNAL]
        else:
            route = network_policy.child_environment(os.environ, scope=uuid.uuid4().hex)[network_policy.INTERNAL]
        with network_policy.using_route(route):
            options = {"renew_auth": True} if value.get("mode") == "renew_auth" else {}
            if "source_digest" in value:
                options["source_digest"] = value["source_digest"]
            if value.get("pause_scope") == "account":
                options["pause_scope"] = "account"
            block = quota_service.recover_codex_quota(value["account_label"], value["pause_id"], expected_id=value["account_id"], root=exporter.ROOT, admitted_probe=admitted_probe, profile=value.get("profile"), **options)
    except (quota_refresh.RefreshError, OSError, RuntimeError) as exc:
        pause = selected_controls.read("codex").get("pause")
        receipt = {"ok": False, "error": "恢复未完成，暂停保留；请查看诊断后再决定下一步。",
                "failure": diagnostics.failure_details(exc), "pause_id": (pause or {}).get("id")}
    else:
        receipt = {"ok": True, "block": block, "source_host_identity": generation.self_certified_host_identity(),
                   "exporter_commit": commit}
    # Loopback bridge evidence only; unavailable observations do not replay or
    # change the provider outcome. Empty events do not establish no request.
    receipt["network"] = safe_network(network_policy.observations(route))
    from agent_monitor.quota.codex_quota import safe_auth_recovery
    auth_recovery = safe_auth_recovery(admitted_probe.get("authentication_recovery"))
    if auth_recovery:
        receipt["authentication_recovery"] = auth_recovery
    historical = {"ok": receipt["ok"], "request": dict(value),
                  "observed_at": datetime.now(timezone.utc).isoformat(),
                  "pause_id": receipt.get("pause_id"),
                  "failure": diagnostics.safe_failure(receipt.get("failure")), "network": receipt["network"]}
    if network_route:
        context = {"device": {"id": network_route.id, "label": network_route.label},
                   "preview": network_route.approval_context}
        receipt["network_context"] = context
        historical["network_context"] = context
    if auth_recovery:
        historical["authentication_recovery"] = auth_recovery
    if admitted_probe.get("id"):
        try:
            selected_controls.record_recovery(historical, attempt_id=admitted_probe["id"])
        except (OSError, RuntimeError):
            receipt["record_error"] = "本次诊断记录未保存；请保留当前回执，重新读取状态核对，勿重复探测。"
    return receipt


def run(machine, value, *, network_route=None):
    """Configured machine only; uncertain remote outcomes are never retried."""
    from agent_monitor.snapshots import sync
    validate_request(value)
    if machine.name != value["machine"]:
        raise ValueError("目标机器不一致")
    if machine.is_self:
        return local_recovery(value, **({"network_route": network_route} if network_route else {}))
    if network_route:
        raise ValueError("配对设备恢复只支持账号所在 Hub 本机。")
    if value.get("pause_scope") == "account":
        raise ValueError("账号恢复只支持账号所在 Hub 本机。")
    command = "~/.local/bin/agent-monitor quota-recover --request " + shlex.quote(json.dumps(value))
    try:
        result = subprocess.run([*sync._SSH_PREFIX, machine.ssh_host, command],
                                capture_output=True, text=True, timeout=70, check=True)
        receipt = json.loads(result.stdout)
        if not isinstance(receipt, dict) or type(receipt.get("ok")) is not bool:
            raise ValueError("invalid receipt")
        return receipt
    except (subprocess.SubprocessError, OSError, ValueError):
        return {"ok": False, "error": "恢复回执未取得，执行结果未知；请先核对目标机器的暂停记录，勿重复探测。"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--request", help="JSON: machine, account_id, account_label, pause_id; authorizes one probe")
    mode.add_argument("--status", action="store_true", help="Read local state without querying a provider")
    mode.add_argument("--egress-preview", action="store_true", help="Preview this machine's configured route bridge")
    args = parser.parse_args()
    try:
        if args.egress_preview:
            from agent_monitor.network import account_egress
            result = account_egress.preview()
        else:
            result = local_status() if args.status else local_recovery(json.loads(args.request))
    except (ValueError, RuntimeError, OSError) as exc:
        result = {"ok": False, "error": "恢复未开始或未完成，请核对请求与本机部署。", "failure": diagnostics.failure_details(exc)}
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
