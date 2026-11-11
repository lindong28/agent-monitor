"""Isolated recovery consumer fixture. Provider/anonymous probes and pairing are synthetic.

Real Handler, account Manager, recovery service, quota persistence and Web modules
are used. Source guard rejects operator stores; no external socket is permitted.
"""
import base64
import contextlib
import json
import os
from pathlib import Path
import re
import socket
import sys
import threading
import time
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
# Reuse the provider-root guard (including its positive control).
from account_egress_ui import source_guard


def main():
    root = Path(sys.argv[1]).resolve()
    root.mkdir(parents=True, exist_ok=False)
    os.environ["HOME"] = str(root / "home")
    os.environ["CODEX_HOME"] = str(root / "home/codex")
    from agent_monitor import http_server as server
    from agent_monitor.accounts import codex_accounts as ca, account_holds
    from agent_monitor.network import client_network, account_egress, network_policy
    from agent_monitor.quota import quota_refresh, quota_recovery, quota_sync, quota_service
    from agent_monitor.snapshots import exporter, generation
    from parsers.accounts import Account
    from parsers.codex_profiles import SessionLinks
    from parsers import accounts
    from agent_monitor.contracts.usage import RateLimits
    import test_codex_accounts

    def socket_guard(event, args):
        if event == "socket.connect" and isinstance(args[1], tuple) and args[1][0] not in ("127.0.0.1", "::1"):
            raise RuntimeError("fixture forbids non-loopback connections")
    sys.addaudithook(socket_guard)
    manager = ca.Manager(root / "state/codex-accounts")
    quota = quota_sync.Manager(root / "state/machine_quota.json")
    machine = SimpleNamespace(name="credential-host", ssh_host="self", is_self=True)
    route = client_network.hub.register("original-device")
    replacement = client_network.hub.register("selected-device")
    counts = {"provider_reads": 0, "previews": 0, "route_checks": 0}
    facts = {"generation": 0, "routes": {"https://chatgpt.com": "DIRECT", "https://auth.openai.com": "DIRECT"},
        "events": [{"target": "https://" + host, "route": "DIRECT", "result": "connected", "generation": 0}
                   for host in account_egress.TARGETS]}
    route.observe = lambda scope=None: dict(facts)
    replacement.observe = lambda scope=None: dict(facts)
    row = manager.add("recover@example.test", "workspace-recover")
    test_codex_accounts.seed_auth(manager, row)
    record = manager.read(manager.directory(row["id"]))
    operation_id = "11111111-1111-4111-8111-111111111111"
    batch_id = "22222222-2222-4222-8222-222222222222"
    record["operation"] = {"id": operation_id, "quota_batch_id": batch_id, "stage": "failed", "started_at": ca.now(),
        "refresh_only": True, "login_only": False, "message_status": "not_sent", "quota_status": "not_read",
        "detail": "合成的 401/token_expired；不代表真实账号。"}
    manager.save(manager.directory(row["id"]), record)
    ca.write_record(manager.root / "quota-batch.json", {"id": batch_id, "revision": "fixture-revision",
        "created_at": ca.now(), "profile_ids": [row["id"]], "network_device": {"id": route.id, "label": route.label}})
    account = Account(row["account_id"], row["email"])
    profile = "monitor-" + row["id"]
    failure = {"kind": "rpc_error", "stage": "quota_read", "http_status": 401, "upstream_code": "token_expired",
        "account_profile": profile, "account_fingerprint": quota_refresh.account_fingerprint("codex", account)}
    controls = account_holds.controls(root / "state", account.account_id, account.label)
    lease = controls.acquire("codex")
    lease.pause("quotaAuthenticationFailed", failure=failure, source={"action": "quota", "operation_id": operation_id, "profile_id": row["id"]})
    lease.close()
    connection = {"id": route.id, "capability": route.browser, "label": route.label}
    replacement_connection = {"id": replacement.id, "capability": replacement.browser, "label": replacement.label}
    state = {"connection": connection}
    source = (ROOT / "web/accounts.html").read_text()
    # Literal HTML tag boundary; avoid depending on page-shell services.
    panel = re.search(r"<main\b[^>]*>(.*?)</main>", source, re.S).group(1)
    html = """<!doctype html><html lang=zh><meta charset=utf-8><link rel=stylesheet href=/web/styles.css>
<body><main>""" + panel + """</main><script>
window.AgentMonitor={pageScope:()=>()=>true,ensureTimezone:async()=>{},formatDate:x=>new Date(x).toLocaleString(),
onPageCleanup:()=>{},pageInterval:()=>{},latestCodexQuotaReading:a=>a.operation?.after||a.last_quota};
window.open=()=>{const frame=document.createElement("iframe");frame.hidden=true;document.body.append(frame);
const popup=frame.contentWindow;fetch("/fixture/connection").then(r=>r.json()).then(connection=>window.dispatchEvent(new MessageEvent("message",{
origin:"http://127.0.0.1:39002",source:popup,data:{type:"agent-monitor-network",nonce:window.fixtureNonce,connection}})));return popup;};
const oldRandom=crypto.getRandomValues.bind(crypto);crypto.getRandomValues=a=>{oldRandom(a);if(a.length===16)window.fixtureNonce=[...a].map(n=>n.toString(16).padStart(2,"0")).join("");return a;};
</script><script src=/web/quota.js></script><script type=module>
import {init} from "/web/codex-accounts.js";import {previewEgress} from "/web/account-egress.js";
window.AgentMonitor.previewUpstream=(targets,payload,signal)=>previewEgress(async(action,body)=>{
const r=await fetch("/api/codex-accounts/"+action,{method:"POST",headers:{"Content-Type":"application/json","X-Agent-Monitor":"codex-accounts"},body:JSON.stringify(body),signal});
const value=await r.json();if(!r.ok)throw Error(value.error);return value;},targets,signal,payload);
Object.assign(window.AgentMonitor,window.AgentMonitorModules.quota({platform:{window,document,fetch:(...a)=>fetch(...a)},qs:s=>document.querySelector(s),fmtAbs:String,formatDate:String,updateSyncOverviewLink:()=>{}}));init();
</script></html>"""
    def preview(selected):
        counts["previews"] += 1
        return {"checked_host": selected.label, "generation": 0, "routes": facts["routes"],
            "targets": [{"host": host, "ip": "1.1.1.1", "country_code": "US", "observed_at": time.time(),
                "cached": False, "retry_after_seconds": 0} for host in account_egress.TARGETS]}

    def read_provider(provider, selected_account, **kwargs):
        # Exercise the real recovery save/lease sequence with a synthetic boundary response.
        counts["provider_reads"] += 1
        address = network_policy.endpoint()
        if not any(address.split("@")[-1] == item.endpoint.split("@")[-1] for item in (route, replacement)):
            raise AssertionError("recovery did not inherit the selected device bridge")
        counts["route_checks"] += 1
        lease = controls.acquire("codex", probe_pause_id=kwargs["probe_pause_id"])
        kwargs["admitted_probe"]["id"] = lease.state["pause"]["probe_attempt"]["id"]
        kwargs["admitted_probe"]["authentication_recovery"] = {"outcome": "quota_confirmed"}
        lease.close()
        return RateLimits(17, int(time.time()) + 18000, 29, int(time.time()) + 604800, updated_at=ca.now())

    class Handler(server.Handler):
        def log_message(self, *_args): pass
        def _handle_request(self, send_body=True):
            # Synthetic pairing represents a connected device; local page reads
            # refresh its lease instead of running a real operator connector.
            route.last_seen = replacement.last_seen = time.monotonic()
            if self.path.split("?")[0] in ("/", "/accounts"):
                body = html.encode(); self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body))); self.end_headers()
                if send_body: self.wfile.write(body)
                return
            if self.path == "/fixture/connection": return self._send_json(state["connection"])
            if self.path == "/fixture/stats": return self._send_json({**counts, "batch": manager.quota_batch_view(),
                "paused": bool(controls.read("codex").get("pause")), "published": bool(quota.records),
                "last_recovery": controls.read("codex").get("last_recovery")})
            return super()._handle_request(send_body=send_body)
        def do_POST(self):
            if self.path == "/fixture/select-new-device":
                state["connection"] = replacement_connection
                return self._send_json({"ok": True})
            if self.path == "/fixture/stop":
                self._send_json({"ok": True}); threading.Thread(target=self.server.shutdown, daemon=True).start(); return
            return super().do_POST()

    with contextlib.ExitStack() as stack:
        def patch(*a, **kw): stack.enter_context(mock.patch(*a, **kw))
        patch("agent_monitor.accounts.codex_accounts.manager", manager)
        patch("agent_monitor.http_server.QUOTA_REFRESH", quota)
        patch("agent_monitor.snapshots.exporter.ROOT", root)
        patch("agent_monitor.quota.quota_service.ROOT", root)
        patch("agent_monitor.quota.quota_refresh.ROOT", root)
        patch("agent_monitor.snapshots.exporter.exporter_version", return_value="a" * 40)
        patch("agent_monitor.snapshots.generation.self_certified_host_identity", return_value="fixture-host")
        patch("agent_monitor.snapshots.generation.generation_admission_snapshot", side_effect=lambda: contextlib.nullcontext(
            SimpleNamespace(admitted=[SimpleNamespace(host=machine.name, meta={"source_host_identity": "fixture-host"})])))
        patch("agent_monitor.quota.quota_sync.Manager.merge", return_value={"claude": {}, "codex": {}})
        patch("agent_monitor.http_server.sync.load_machine_config", return_value=SimpleNamespace(by_name={machine.name: machine}, self_machine=machine))
        patch("agent_monitor.runtime.hub.enabled", return_value=False)
        patch("agent_monitor.runtime.hub.is_hub", return_value=True)
        patch("agent_monitor.hub_config.is_hub", return_value=True)
        patch("agent_monitor.http_server.QUOTA_VIEW.action_catalog", side_effect=lambda: [{"account_state": "known", "account_id": row["account_id"], "account_label": row["email"]}])
        patch("agent_monitor.network.account_egress.preview", side_effect=preview)
        patch("agent_monitor.network.network_policy.observations", return_value={"policy": "chromium-system", "coverage": "recent_connections_only", "generation": 0, "events": facts["events"]})
        patch("agent_monitor.quota.quota_refresh.load_rate_limits", side_effect=read_provider)
        patch("parsers.codex_profiles.discover", return_value=[])
        patch("parsers.codex_profiles.session_links", return_value=SessionLinks())
        http = server.ThreadingHTTPServer(("0.0.0.0", 0), Handler)
        http.views = server.create_views(); http.routes = server.create_routes(http.views)
        timer = threading.Timer(600, http.shutdown); timer.daemon = True; timer.start()
        print(json.dumps({"url": f"http://127.0.0.1:{http.server_port}", "profile_id": row["id"], "scope": "synthetic only"}), flush=True)
        try: http.serve_forever()
        finally: timer.cancel(); http.server_close(); manager.close(); route.close(); replacement.close()


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT / "tests"))
    main()
