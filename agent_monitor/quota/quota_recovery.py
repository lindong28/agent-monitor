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
    if (not isinstance(value, dict) or set(value) != fields
            or any(not isinstance(value[k], str) or not value[k] or len(value[k]) > 256 for k in fields)
            or not re.fullmatch(r"[0-9a-f]{32}", value["pause_id"])):
        raise ValueError("请指定机器、完整账号身份和当前暂停标识。")
    return value


def local_status():
    """Read local files and machine identity, without initializing a provider."""
    from agent_monitor.snapshots import exporter, generation
    from agent_monitor.accounts.upstream_controls import Controls
    from parsers import accounts
    state = Controls(exporter.ROOT / "state/upstream_controls").read("codex")
    account = accounts.codex_account()
    auth = accounts.CODEX_AUTH
    home = auth.parent
    source = "default" if home == Path.home() / ".codex" else "configured"
    if (home.parent / "profile.json").is_file() and home.name == "codex":
        source = "managed"
    return {"source_host_identity": generation.self_certified_host_identity(),
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "pause": state.get("pause"), "active": state.get("active"),
            "account": {"account_id": account.account_id if account else None,
                        "account_label": account.label if account else None,
                        "source": source, "profile": home.parent.name if source == "managed" else None,
                        "credential_path": str(auth)},
            "last_recovery": state.get("last_recovery")}


def read_status(machine):
    if machine.is_self:
        return local_status()
    from agent_monitor.snapshots import sync
    result = subprocess.run([*sync._SSH_PREFIX, machine.ssh_host,
                             "~/.local/bin/agent-monitor quota-recover --status"],
                            capture_output=True, text=True, timeout=15, check=True)
    value = json.loads(result.stdout)
    if not isinstance(value, dict) or not value.get("source_host_identity"):
        raise ValueError("目标机器未返回可核验的状态。")
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


def local_recovery(value):
    from agent_monitor.snapshots import exporter
    from agent_monitor.quota import quota_service
    from agent_monitor.snapshots import generation
    from agent_monitor.quota import quota_refresh
    from agent_monitor.accounts.upstream_controls import Controls
    validate_request(value)
    # Establish the implementation identity before the irreversible provider read.
    commit = exporter.exporter_version()
    route = None
    admitted_probe = {}
    try:
        # Scope observations without choosing a different endpoint or proxy policy.
        route = network_policy.child_environment(os.environ, scope=uuid.uuid4().hex)[network_policy.INTERNAL]
        with network_policy.using_route(route):
            block = quota_service.recover_codex_quota(value["account_label"], value["pause_id"], expected_id=value["account_id"], root=exporter.ROOT, admitted_probe=admitted_probe)
    except (quota_refresh.RefreshError, OSError, RuntimeError) as exc:
        pause = Controls(exporter.ROOT / "state/upstream_controls").read("codex").get("pause")
        receipt = {"ok": False, "error": "恢复未完成，暂停保留；请查看诊断后再决定下一步。",
                "failure": diagnostics.failure_details(exc), "pause_id": (pause or {}).get("id")}
    else:
        receipt = {"ok": True, "block": block, "source_host_identity": generation.self_certified_host_identity(),
                   "exporter_commit": commit}
    # Loopback bridge evidence only; unavailable observations do not replay or
    # change the provider outcome. Empty events do not establish no request.
    receipt["network"] = safe_network(network_policy.observations(route))
    historical = {"ok": receipt["ok"], "request": dict(value),
                  "observed_at": datetime.now(timezone.utc).isoformat(),
                  "pause_id": receipt.get("pause_id"),
                  "failure": diagnostics.safe_failure(receipt.get("failure")), "network": receipt["network"]}
    if admitted_probe.get("id"):
        try:
            Controls(exporter.ROOT / "state/upstream_controls").record_recovery(historical, attempt_id=admitted_probe["id"])
        except (OSError, RuntimeError):
            receipt["record_error"] = "本次诊断记录未保存；请保留当前回执，重新读取状态核对，勿重复探测。"
    return receipt


def run(machine, value):
    """Configured machine only; uncertain remote outcomes are never retried."""
    from agent_monitor.snapshots import sync
    validate_request(value)
    if machine.name != value["machine"]:
        raise ValueError("目标机器不一致")
    if machine.is_self:
        return local_recovery(value)
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
    args = parser.parse_args()
    try:
        result = local_status() if args.status else local_recovery(json.loads(args.request))
    except (ValueError, RuntimeError, OSError) as exc:
        result = {"ok": False, "error": "恢复未开始或未完成，请核对请求与本机部署。", "failure": diagnostics.failure_details(exc)}
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
