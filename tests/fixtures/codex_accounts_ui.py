"""Bounded local UI fixture using the real account routes and a pinned stdio peer.

Run under an OS network sandbox allowing loopback only. HOME/state are temporary;
no production server, credential store, collector or actual Codex is started.
"""
import argparse
import base64
import contextlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--post-send", action="store_true")
    args = parser.parse_args()
    from recovery_network_ui import account_page, loopback_only  # also installs the provider-root guard
    sys.addaudithook(loopback_only)
    with tempfile.TemporaryDirectory(prefix="codex-controls-ui-") as temporary:
        os.environ["HOME"] = temporary
        os.environ["CODEX_HOME"] = temporary + "/codex"
        from agent_monitor.accounts import codex_accounts as ca
        from agent_monitor import http_server as server
        from agent_monitor.network import client_network, account_egress
        from agent_monitor.snapshots import exporter, generation
        from agent_monitor.quota import quota_refresh, quota_service
        real_popen = subprocess.Popen
        starts = []
        scenarios = {}

        def factory(directory, cancelled):
            record = manager.read(directory)
            if args.post_send and not ca.network_policy._selected_route.get():
                raise AssertionError("fixture expected the confirmed device bridge")
            def spawn(_command, **kwargs):
                kwargs["env"].update(FAKE_EMAIL=record["email"], FAKE_ACCOUNT=record["account_id"],
                                     FAKE_SCENARIO=scenarios.get(record["email"], "quota_allowed"))
                return real_popen([sys.executable, str(ROOT / "tests/fixtures/codex_account_server.py")], **kwargs)
            with mock.patch.object(ca.subprocess, "Popen", side_effect=spawn),\
                    mock.patch.object(ca.shutil, "which", return_value=sys.executable):
                starts.append(time.monotonic())
                return ca.Rpc(directory, cancelled)

        root = Path(temporary)
        manager = ca.Manager(root / "state/codex-accounts", rpc_factory=factory)
        for name, reason, category in (("healthy", None, None), ("quota", "quota", "usageLimitExceeded"),
                                        ("rate", "rate_limit", "rateLimitExceeded"),
                                        ("auth", "authentication", "unauthorized")):
            account = manager.add(name + "@example.test", "workspace-" + name)
            directory = manager.directory(account["id"])
            ca.private_dir(directory / "codex")
            claims = {"email": account["email"], "https://api.openai.com/auth": {"chatgpt_account_id": account["account_id"]}}
            token = "fixture." + base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=") + ".fixture"
            ca.write_record(directory / "codex/auth.json", {"tokens": {"account_id": account["account_id"],
                             "id_token": token, "access_token": "SECRET_FIXTURE_TOKEN"}})
            if reason:
                record = manager.read(directory)
                record["restrictions"] = {reason: {"category": category, "created_at": ca.now()}}
                record["operation"] = {"id": name, "stage": "failed", "started_at": ca.now(), "refresh_only": False,
                    "message_status": "failed", "quota_status": "not_read", "detail": "本地合成失败记录。",
                    "failure": {"category": category, "stage": "sending"}}
                manager.save(directory, record)

        # An uncredentialed synthetic profile exercises the separate login UI.
        manager.add("unsigned@example.test", "workspace-unsigned")

        def methods(profile_id):
            path = manager.directory(profile_id) / "calls.jsonl"
            return [json.loads(line)["method"] for line in path.read_text().splitlines()] if path.exists() else []

        profiles = {}
        if args.post_send:
            from tests.test_quota_transport_retry import seed_fresh
            from tests.test_codex_accounts import seed_auth
            for name in ("stable", "changed"):
                row = manager.add(name + "@example.test", "workspace-" + name)
                profiles[name] = row["id"]
                seed_auth(manager, row)
                seed_fresh(manager.directory(row["id"]) / "codex/auth.json")
                scenarios[row["email"]] = "post_bounded_once"

        route = client_network.hub.register("fixture-device")
        def observe(**kwargs):
            # The connector changes its business-domain route only after the
            # second account's pre-send and initial post-send quota RPCs.
            routes = dict.fromkeys(client_network.PREVIEW_ORIGINS, "DIRECT")
            if "changed" in profiles and methods(profiles["changed"]).count("account/rateLimits/read") >= 2:
                routes["https://chatgpt.com"] = "PROXY changed.example.test:8080"
            return {"generation": 1, "routes": routes}

        route.observe = observe
        account_egress.preview = lambda selected: {**selected.observe(), "checked_host": selected.label,
            "targets": [{"host": host, "ip": "192.0.2.1", "country_code": "SG"} for host in account_egress.TARGETS]}
        machine = SimpleNamespace(name="fixture-machine", ssh_host="self", is_self=True)
        exporter.ROOT = quota_service.ROOT = quota_refresh.ROOT = root
        exporter.exporter_version = lambda: "a" * 40
        generation.self_certified_host_identity = lambda: "fixture-host"
        generation.generation_admission_snapshot = lambda: contextlib.nullcontext(SimpleNamespace(
            admitted=[SimpleNamespace(host=machine.name, meta={"source_host_identity": "fixture-host"})]))
        server.sync.load_machine_config = lambda: SimpleNamespace(by_name={machine.name: machine}, self_machine=machine)
        from agent_monitor import hub_config
        from agent_monitor.runtime import hub
        hub.enabled = lambda: False
        hub.is_hub = hub_config.is_hub = lambda: True

        ca.manager = manager
        server.QUOTA_VIEW.action_catalog = lambda: [{"account_state": "known", "account_label": a["email"],
            "account_id": a["account_id"]} for a in manager.list()["accounts"]]
        html = account_page()

        class Handler(server.Handler):
            def do_GET(self):
                if self.path in ("/", "/accounts"):
                    body, content_type = html.encode(), "text/html; charset=utf-8"
                elif self.path == "/fixture/connection":
                    return self._send_json({"id": route.id, "capability": route.browser, "label": route.label})
                elif self.path in ("/web/styles.css", "/web/quota.js", "/web/codex-accounts.js", "/web/device-connection.js", "/web/account-egress.js"):
                    body = (ROOT / self.path.lstrip("/")).read_bytes()
                    content_type = "text/css" if self.path.endswith("css") else "text/javascript"
                elif self.path == "/fixture/stats":
                    if args.post_send:
                        return self._send_json({"profiles": profiles, "methods": {
                            name: methods(pid) for name, pid in profiles.items()}})
                    counts = {}
                    for account in manager.list()["accounts"]:
                        path = manager.directory(account["id"]) / "calls.jsonl"
                        calls = [json.loads(line).get("method") for line in path.read_text().splitlines()] if path.exists() else []
                        counts[account["email"]] = {method: calls.count(method) for method in
                            ("turn/start", "account/read", "account/login/start", "account/rateLimits/read")}
                    self._send_json({"counts": counts, "starts": starts})
                    return
                elif self.path.startswith(("/api/codex-accounts", "/api/quota-recovery?")):
                    return super().do_GET()
                else:
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                if self.path == "/fixture/stop":
                    self._send_json({"stopping": True})
                    threading.Thread(target=http.shutdown, daemon=True).start()
                elif self.path.startswith("/api/codex-accounts/"):
                    super().do_POST()
                else:
                    self.send_error(404)

        http = server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        timer = threading.Timer(600, http.shutdown)
        timer.daemon = True
        timer.start()
        print(json.dumps({"url": "http://127.0.0.1:" + str(http.server_port), "lifetime_seconds": 600}), flush=True)
        try:
            http.serve_forever()
        finally:
            timer.cancel()
            http.server_close()
            manager.close()
            route.close()


if __name__ == "__main__":
    main()
