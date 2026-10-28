"""Anonymous, target-specific exit previews; never load account credentials."""
from concurrent.futures import ThreadPoolExecutor
import ipaddress
import json
import math
import os
import re
import socket
import subprocess
import sys
import threading
import time

from agent_monitor.network import network_policy
from agent_monitor.runtime.python_entrypoint import module_command

# ChatGPT-authenticated Codex defaults, verified against rust-v0.162.1.
TARGETS = ("chatgpt.com", "auth.openai.com")
_lock = threading.Lock()
_cache = {}


def unknown(host, reason):
    return {"host": host, "ip": None, "country_code": None,
            "observed_at": time.time(), "error": reason}


def parse_trace(body, host):
    fields = {}
    for line in body.decode("ascii").splitlines():
        key, separator, value = line.partition("=")
        if not separator or key in fields:
            raise ValueError("invalid trace")
        fields[key] = value
    if fields.get("h") != host:
        raise ValueError("wrong target")
    address = ipaddress.ip_address(fields["ip"])
    if not address.is_global:
        raise ValueError("not a public address")
    country = fields.get("loc", "")
    if not re.fullmatch(r"[A-Z]{2}", country) or country == "XX":
        country = None
    return {"host": host, "ip": str(address), "country_code": country,
            "observed_at": time.time(), "error": None if country else "未取得地区信息"}


def probe(host):
    # No netrc, cookies, account headers, redirects, or HTTP retries. The parent
    # supplies the shared bridge; the entire child also has a wall-clock budget.
    from ip_check.http_response import require_success
    import requests
    delay = []
    try:
        with network_policy.session() as client:
            with client.get("https://" + host + "/cdn-cgi/trace", timeout=(4, 6),
                            allow_redirects=False, stream=True,
                            headers={"Accept-Encoding": "identity"}) as response:
                require_success(response, on_retry_after=delay.append)
                body = response.raw.read(8193, decode_content=False)
                if len(body) > 8192:
                    raise ValueError("response too large")
                result = parse_trace(body, host)
    except requests.Timeout:
        result = unknown(host, "出口探测超时，未重试")
    except requests.RequestException:
        result = unknown(host, "无法连接探测目标，未重试")
    except (ValueError, KeyError, UnicodeError):
        status = response.status_code if 'response' in locals() else None
        result = unknown(host, f"探测目标返回 HTTP {status}，未重试" if status and status != 200
                         else "探测目标未返回有效的出口信息")
    if delay:
        result["retry_after_seconds"] = delay[0]
    return result


def _run_probe(host, env):
    try:
        completed = subprocess.run(module_command("agent_monitor.network.account_egress", host),
                                   env=env, capture_output=True, timeout=15, check=True)
        result = json.loads(completed.stdout)
        if not isinstance(result, dict) or result.get("host") != host:
            raise ValueError("invalid child report")
        return result
    except subprocess.TimeoutExpired:
        return unknown(host, "出口探测超过15秒，已停止，未重试")
    except (OSError, ValueError, subprocess.CalledProcessError):
        return unknown(host, "无法完成出口探测，未重试")


def preview(route=None):
    # Share successful and failed observations across tabs/users. A preview
    # never clears provider pauses and has no path to the account manager.
    cache = route.egress_cache if route else _cache
    lock = route.egress_lock if route else _lock
    if route:
        route.require()
    with lock:
        now = time.monotonic()
        due = [host for host in TARGETS if host not in cache or cache[host][0] <= now]
        if due:
            try:
                with network_policy.using_route(route.endpoint if route else None):
                    env = network_policy.child_environment(os.environ)
                with ThreadPoolExecutor(max_workers=len(due)) as pool:
                    results = list(pool.map(lambda host: _run_probe(host, env), due))
            except network_policy.NetworkPolicyError:
                results = [unknown(host, "系统代理不可用，未发出探测") for host in due]
            for host, result in zip(due, results):
                delay = result.get("retry_after_seconds", 0)
                if not isinstance(delay, (int, float)) or isinstance(delay, bool) or not math.isfinite(delay):
                    delay = 0
                cache[host] = (time.monotonic() + max(60, delay), result)
        if route:
            route.require()
        return {"checked_host": route.label if route else socket.gethostname(), "targets": [
            dict(cache[host][1], cached=host not in due,
                 retry_after_seconds=max(0, math.ceil(cache[host][0] - time.monotonic())))
            for host in TARGETS]}


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in TARGETS:
        raise SystemExit(2)
    print(json.dumps(probe(sys.argv[1])))
