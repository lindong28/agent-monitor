"""Bounded static UI fixture; all API responses are synthetic, no upstream."""
import json
from pathlib import Path
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

ROOT = Path(__file__).resolve().parents[2]
counter = 0


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def log_message(self, *args):
        pass

    def do_GET(self):
        if self.path.startswith('/api/'):
            value = {"ok": True, "instance_id": "fixture", "stale": False, "timezone": "UTC"}
            if self.path.startswith('/api/network'):
                value = {"installed": True, "checked_host": "fixture-server", "verdict": "unknown",
                    "timestamp": "2026-10-23T00:00:00Z", "local": {"lan_ip": "127.0.0.1"},
                    "public": {"ip": "192.0.2.123", "ok": True}, "conclusions": [],
                    "proxy_effective": {"source": "published" if counter else "system"}}
                if not counter:
                    value["public"]["probe_url"] = "http://ip-api.com/json/"
            raw = json.dumps(value).encode()
            self.send_response(200); self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(raw))); self.end_headers(); self.wfile.write(raw)
            return
        if self.path.split('?')[0] in ('/network', '/'):
            self.path = '/web/network.html'
        super().do_GET()

    def do_POST(self):
        global counter
        if self.path.startswith('/api/network'):
            counter += 1
            self.do_GET()
        else:
            self.send_error(405)


if __name__ == '__main__':
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    timer = threading.Timer(180, server.shutdown); timer.daemon = True; timer.start()
    print(json.dumps({'port': server.server_port, 'timeout_seconds': 180}), flush=True)
    try:
        server.serve_forever()
    finally:
        timer.cancel(); server.server_close()
