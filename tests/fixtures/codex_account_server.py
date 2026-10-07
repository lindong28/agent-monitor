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
        result = {"account": {"type": "chatgpt", "email": "wrong@example.com" if scenario == "wrong_email" else email}
                  if (home / "auth.json").exists() else None}
    elif method == "account/login/start":
        if scenario == "login_error":
            emit({"id": message["id"], "error": {"message": "SECRET_FIXTURE_TOKEN upstream error"}})
            continue
        result = {"loginId": "fixture-login", "verificationUrl": "https://auth.openai.com/codex/device", "userCode": "TEST-CODE"}
        if scenario == "bad_url":
            result["verificationUrl"] = "https://evil.example/steal"
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
        if scenario == "quota_failure" and quota_reads == 2:
            emit({"id": message["id"], "error": {"message": "SECRET_FIXTURE_TOKEN"}})
            continue
        result = {"accountId": account_id, "rateLimits": {"secondary": {"windowDurationMins": 10080, "usedPercent": 0,
                  "resetsAt": None if scenario == "no_reset" else 1900000000 + quota_reads}}}
    elif method == "thread/start":
        assert message["params"]["environments"] == []
        assert message["params"]["ephemeral"] is True
        result = {"thread": {"id": "thread-fixture"}}
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
        emit({"method": "turn/completed", "params": {"threadId": "thread-fixture", "turn": {"id": "turn-fixture", "status": "failed" if scenario == "failed_turn" else "completed"}}})
    emit({"id": message["id"], "result": result})
