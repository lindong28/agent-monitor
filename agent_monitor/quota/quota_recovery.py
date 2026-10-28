"""Explicit, account-bound Codex recovery. No retries or model turns."""

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import uuid

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
    try:
        # Scope observations without choosing a different endpoint or proxy policy.
        route = network_policy.child_environment(os.environ, scope=uuid.uuid4().hex)[network_policy.INTERNAL]
        with network_policy.using_route(route):
            block = quota_service.recover_codex_quota(value["account_label"], value["pause_id"], expected_id=value["account_id"], root=exporter.ROOT)
    except (quota_refresh.RefreshError, OSError, RuntimeError) as exc:
        pause = Controls(exporter.ROOT / "state/upstream_controls").read("codex").get("pause")
        receipt = {"ok": False, "error": "恢复未完成，暂停保留；请查看诊断后再决定下一步。",
                "failure": diagnostics.failure_details(exc), "pause_id": (pause or {}).get("id")}
    else:
        receipt = {"ok": True, "block": block, "source_host_identity": generation.self_certified_host_identity(),
                   "exporter_commit": commit}
    # Loopback bridge evidence only; unavailable observations do not replay or
    # change the provider outcome. Empty events do not establish no request.
    receipt["network"] = network_policy.observations(route)
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
    parser.add_argument("--request", required=True, help="JSON: machine, account_id, account_label, pause_id; authorizes one probe")
    args = parser.parse_args()
    try:
        result = local_recovery(json.loads(args.request))
    except (ValueError, RuntimeError, OSError) as exc:
        result = {"ok": False, "error": "恢复未开始或未完成，请核对请求与本机部署。", "failure": diagnostics.failure_details(exc)}
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
