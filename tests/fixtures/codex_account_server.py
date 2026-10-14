"""Deterministic stdio peer; synthetic accounts only, never talks to a provider."""
import base64
import json
import os
from pathlib import Path
import sys
import time

home = Path(os.environ["CODEX_HOME"])
scenario = os.environ.get("FAKE_SCENARIO", "success")
email = os.environ.get("FAKE_EMAIL", "one@example.com")
account_id = os.environ.get("FAKE_ACCOUNT", "workspace-one")
calls = home.parent / "calls.jsonl"
quota_reads = 0
recovery = calls.exists() and any(json.loads(line).get("method") == "turn/start" for line in calls.read_text().splitlines())


def emit(value):
    print(json.dumps(value), flush=True)


def authorize():
    claims = {"email": "wrong@example.com" if scenario == "wrong_email" else email,
              "https://api.openai.com/auth": {"chatgpt_account_id": "wrong-workspace" if scenario == "wrong_workspace" else account_id}}
    token = "fixture." + base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=") + ".fixture"
    (home / "auth.json").write_text(json.dumps({"tokens": {"account_id": claims["https://api.openai.com/auth"]["chatgpt_account_id"],
                                                         "id_token": token, "access_token": "SECRET_FIXTURE_TOKEN"}}))


for line in sys.stdin:
    message = json.loads(line)
    method = message.get("method")
    with calls.open("a") as out:
        out.write(json.dumps(message) + "\n")
    if "id" not in message:
        continue
    result = {}
    if method == "account/read":
        result = {"account": {"type": "chatgpt", "email": "wrong@example.com" if scenario == "wrong_email" or (recovery and scenario == "recovery_identity") else email}
                  if (home / "auth.json").exists() else None}
    elif method == "account/login/start":
        if scenario == "login_start_rate_limit":
            emit({"id": message["id"], "error": {"code": -32603, "message":
                  "failed to request device code: device code request failed with status 429 Too Many Requests"}})
            continue
        if scenario == "login_error":
            emit({"id": message["id"], "error": {"message": "SECRET_FIXTURE_TOKEN upstream error"}})
            continue
        result = {"loginId": "fixture-login", "verificationUrl": "https://auth.openai.com/codex/device", "userCode": "TEST-CODE"}
        if scenario == "bad_url":
            result["verificationUrl"] = "https://evil.example/steal"
        if scenario in ("login_poll_rate_limit", "login_unknown_failure"):
            emit({"id": message["id"], "result": result})
            emit({"method": "account/login/completed", "params": {"loginId": "fixture-login", "success": False,
                  "error": "device auth failed with status 429 Too Many Requests" if scenario == "login_poll_rate_limit"
                  else "SECRET_FIXTURE_TOKEN unknown upstream failure"}})
            continue
        if scenario == "deferred_login":
            emit({"id": message["id"], "result": result})
            deadline = time.monotonic() + 15
            while not (home.parent / "authorize").exists() and time.monotonic() < deadline:
                time.sleep(.025)
            if not (home.parent / "authorize").exists():
                sys.exit(1)
            authorize()
            emit({"method": "account/login/completed", "params": {"loginId": "fixture-login", "success": True}})
            continue
        if scenario not in ("wait_login", "bad_url"):
            authorize()
            emit({"method": "account/login/completed", "params": {"loginId": "fixture-login", "success": True}})
    elif method == "account/rateLimits/read":
        quota_reads += 1
        if (scenario == "quota_failure" and quota_reads == 2) or (recovery and scenario == "recovery_failure"):
            emit({"id": message["id"], "error": {"message": "SECRET_FIXTURE_TOKEN"}})
            continue
        result = {"accountId": account_id, "rateLimits": {"secondary": {"windowDurationMins": 10080, "usedPercent": 0,
                  "resetsAt": None if scenario == "no_reset" else 1900000000 + quota_reads}}}
    elif method == "config/read":
        # Producer-shaped fixture, frozen to the audited 0.162.1 feature names.
        flags = "apps code_mode code_mode_only code_mode_host context_management current_time_reminder deferred_executor enable_fanout goals hooks image_generation memories multi_agent multi_agent_v2 plugins request_permissions_tool shell_snapshot shell_tool standalone_web_search token_budget tool_suggest unified_exec view_image sleep_tool stable_environment_tools send_message_to_user_async".split()
        result = {"config": {"features": dict.fromkeys(flags, False), "web_search": "disabled", "mcp_servers": {"fixture": {"enabled": True}}},
                  "layers": [{"name": {"type": "sessionFlags"}, "config": {"tools": {"update_plan": {"enabled": False}, "experimental_request_user_input": {"enabled": False}}}}]}
        if scenario == "unsafe_config":
            result["config"]["features"]["shell_tool"] = True
        if scenario in ("custom_profile", "wrong_profile"):
            result["config"]["default_permissions"] = "managed-fixture"
    elif method == "thread/start":
        assert message["params"]["environments"] == []
        assert message["params"]["ephemeral"] is True
        result = {"thread": {"id": "thread-fixture"}, "sandbox": {"type": "workspaceWrite" if scenario == "unsafe_sandbox" else "readOnly"}}
        if scenario in ("custom_profile", "wrong_profile"):
            assert message["params"]["permissions"] == "managed-fixture"
            assert "sandbox" not in message["params"]
            result["activePermissionProfile"] = {"id": "other" if scenario == "wrong_profile" else "managed-fixture"}
    elif method == "turn/start":
        assert message["params"]["environments"] == []
        if scenario == "disconnect":
            sys.exit(0)
        if scenario == "tool_request":
            emit({"id": "tool-fixture", "method": "item/commandExecution/requestApproval", "params": {}})
            continue
        if scenario == "wait_turn":
            time.sleep(20)
        result = {"turn": {"id": "turn-fixture"}}
        emit({"method": "turn/completed", "params": {"threadId": "thread-fixture", "turn": {"id": "turn-fixture", "status": "failed" if scenario in ("failed_turn", "usage_limit", "stream_error", "unsafe_error", "recovery_failure", "recovery_identity") else "completed", "error": {"message": "SECRET_FIXTURE_TOKEN", "additionalDetails": "SECRET_FIXTURE_TOKEN", "codexErrorInfo": {"usage_limit": "usageLimitExceeded", "recovery_failure": "usageLimitExceeded", "stream_error": {"responseStreamDisconnected": {"httpStatusCode": 503, "secret": "SECRET_FIXTURE_TOKEN"}}, "unsafe_error": {"SECRET_FIXTURE_TOKEN": {"httpStatusCode": 503}}}.get(scenario)}}}})
    emit({"id": message["id"], "result": result})
