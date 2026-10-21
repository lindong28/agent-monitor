"""Bounded local UI fixture using the real account routes and a pinned stdio peer.

Run under an OS network sandbox allowing loopback only. HOME/state are temporary;
no production server, credential store, collector or actual Codex is started.
"""
import base64
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def main():
    with tempfile.TemporaryDirectory(prefix="codex-controls-ui-") as temporary:
        os.environ["HOME"] = temporary
        os.environ["CODEX_HOME"] = temporary + "/codex"
        import codex_accounts as ca
        import server
        real_popen = subprocess.Popen
        starts = []
        scenarios = {}

        def factory(directory, cancelled):
            record = manager.read(directory)
            def spawn(_command, **kwargs):
                kwargs["env"].update(FAKE_EMAIL=record["email"], FAKE_ACCOUNT=record["account_id"],
                                     FAKE_SCENARIO=scenarios.get(record["email"], "quota_allowed"))
                return real_popen([sys.executable, str(ROOT / "tests/fixtures/codex_account_server.py")], **kwargs)
            with mock.patch.object(ca.subprocess, "Popen", side_effect=spawn), \
                    mock.patch.object(ca.shutil, "which", return_value=sys.executable):
                starts.append(time.monotonic())
                return ca.Rpc(directory, cancelled)

        manager = ca.Manager(Path(temporary) / "profiles", rpc_factory=factory)
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

        ca.manager = manager
        server._codex_action_catalog = lambda: [{"account_state": "known", "account_label": a["email"],
            "account_id": a["account_id"]} for a in manager.list()["accounts"]]
        source = (ROOT / "web/index.html").read_text()
        panel = source.split('<details id="codex-account-actions"', 1)[1].split('</section>', 1)[0]
        html = ('<!doctype html><html lang="zh"><meta charset="utf-8"><meta name="viewport" content="width=device-width">'
                '<link rel="stylesheet" href="/web/styles.css"><body><main><h1>本地模拟账号操作</h1>'
                '<p>全部为合成账号；不连接 OpenAI。</p><details id="codex-account-actions" open' + panel + '''
                </main><script>
                window.AgentMonitor = {pageScope:()=>()=>true, ensureTimezone:async()=>{},
                  formatDate:x=>new Date(x).toLocaleString(),onPageCleanup:()=>{},pageInterval:()=>{},
                  latestCodexQuotaReading:a=>[a.operation?.after,a.operation?.before,a.batch_result?.after,a.last_quota]
                    .filter(Boolean).sort((a,b)=>Date.parse(b.observed_at)-Date.parse(a.observed_at))[0]};
                </script><script type="module">import {init} from '/web/codex-accounts.js';init();</script></body></html>''')

        class Handler(server.Handler):
            def do_GET(self):
                if self.path == "/":
                    body, content_type = html.encode(), "text/html; charset=utf-8"
                elif self.path in ("/web/styles.css", "/web/codex-accounts.js"):
                    body = (ROOT / self.path.lstrip("/")).read_bytes()
                    content_type = "text/css" if self.path.endswith("css") else "text/javascript"
                elif self.path == "/fixture/stats":
                    counts = {}
                    for account in manager.list()["accounts"]:
                        path = manager.directory(account["id"]) / "calls.jsonl"
                        calls = [json.loads(line).get("method") for line in path.read_text().splitlines()] if path.exists() else []
                        counts[account["email"]] = {method: calls.count(method) for method in
                            ("turn/start", "account/read", "account/login/start", "account/rateLimits/read")}
                    self._send_json({"counts": counts, "starts": starts})
                    return
                elif self.path.startswith("/api/codex-accounts"):
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

        http = server.ThreadingHTTPServer(("0.0.0.0", 0), Handler)
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


if __name__ == "__main__":
    main()
