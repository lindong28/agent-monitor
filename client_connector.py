"""Foreground connector on the browser's machine; no account files are read."""
import argparse
import base64
import contextlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import queue
import re
import signal
import socket
import ssl
import sys
import threading
from urllib.parse import parse_qs, urlsplit

import client_network
import network_policy


def response_header(sock):
    data = bytearray()
    while not data.endswith(b"\r\n\r\n") and len(data) <= 16384:
        chunk = sock.recv(1)
        if not chunk:
            raise client_network.NetworkError("连接在握手完成前关闭。")
        data.extend(chunk)
    if len(data) > 16384 or data.split(b" ", 2)[1] != b"200":
        raise client_network.NetworkError("连接握手被拒绝；请检查设备代理和服务地址。")


def connect_via(route, target, *, scheme="https", scope=None):
    client_network.authority(target)
    parsed = urlsplit(route)
    name = "agent-monitor" + ("." + scope if scope else "")
    auth = base64.b64encode((name + ":" + parsed.password).encode()).decode()
    sock = socket.create_connection((parsed.hostname, parsed.port), timeout=8)
    try:
        sock.settimeout(client_network.CONNECT_SECONDS)
        sock.sendall((f"CONNECT {target} HTTP/1.1\r\nHost: {target}\r\n"
                      f"Proxy-Authorization: Basic {auth}\r\nX-Agent-Monitor-Target-Scheme: {scheme}\r\n\r\n").encode())
        response_header(sock)
        return sock
    except Exception:
        sock.close()
        raise


class Connector:
    def __init__(self, server, label, local_route, pair_port=39002):
        parsed = urlsplit(server)
        if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password or parsed.path not in ("", "/") or parsed.query or parsed.fragment:
            raise client_network.NetworkError("--server 必须是服务的 http(s) 根地址。", 400)
        self.server = server.rstrip("/")
        self.address = parsed
        self.local_route = local_route
        self.stopped = threading.Event()
        self.guard = threading.Lock()
        self.sockets, self.workers = set(), set()
        self.reports = queue.Queue(maxsize=16)
        self.registration = None
        self.pair_server = ThreadingHTTPServer(("127.0.0.1", pair_port), self.pair_handler())
        try:
            self.registration = self.request("register", {"label": label})
        except Exception:
            self.pair_server.server_close()
            raise

    def headers(self):
        if not self.registration:
            return {"X-Agent-Monitor": "connector"}
        return {"X-Connection-ID": self.registration["id"],
                "Authorization": "Bearer " + self.registration["control"]}

    def request(self, action, data=None):
        import requests
        try:
            with network_policy.using_route(self.local_route), network_policy.session() as session:
                response = session.post(self.server + "/api/client-network/" + action,
                                        json=data or {}, headers=self.headers(),
                                        timeout=(client_network.CONNECT_SECONDS, client_network.CONTROL_SECONDS), allow_redirects=False)
                if response.status_code != 200:
                    raise client_network.NetworkError("服务拒绝了设备连接，请检查服务版本与连接状态。")
                value = response.json()
                if not isinstance(value, dict):
                    raise ValueError()
                return value
        except (requests.RequestException, ValueError):
            raise client_network.NetworkError("无法连接服务；设备转发已停止，请检查网络后重新运行连接命令。") from None

    def pair_handler(self):
        connector = self

        class Pair(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_GET(self):
                query = parse_qs(urlsplit(self.path).query)
                origin, nonce = query.get("origin", [""])[0], query.get("nonce", [""])[0]
                expected_host = "127.0.0.1:" + str(self.server.server_port)
                if (urlsplit(self.path).path != "/pair" or self.headers.get("Host") != expected_host
                        or origin != connector.server or not re.fullmatch(r"[a-f0-9]{32}", nonce)
                        or connector.stopped.is_set() or not connector.registration):
                    self.send_error(403)
                    return
                # Exact configured origin; a hostile website cannot redirect the
                # capability to itself. Credentials never enter query parameters.
                payload = json.dumps({"type": "agent-monitor-network", "nonce": nonce,
                                      "connection": connector.registration["connection"]}).replace("<", "\\u003c")
                target = json.dumps(connector.server).replace("<", "\\u003c")
                body = ("<!doctype html><meta charset=utf-8><title>连接本机网络</title>"
                        "<p>正在将本机网络连接交回账号页面。完成后可关闭此窗口。</p>"
                        f"<script>if(window.opener){{window.opener.postMessage({payload},{target});window.close();}}</script>").encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header("Content-Security-Policy", "default-src 'none'; script-src 'unsafe-inline'; frame-ancestors 'none'")
                self.end_headers()
                self.wfile.write(body)
        return Pair

    def forward(self, task):
        upstream = tunnel = None
        try:
            target, scope = task["target"], task.get("scope")
            client_network.authority(target)
            if not isinstance(task.get("channel"), str) or not re.fullmatch(r"[a-f0-9]{32}", task["channel"]):
                raise ValueError()
            if scope is not None and not re.fullmatch(r"[a-f0-9]{32}", scope):
                raise ValueError()
            try:
                upstream = connect_via(self.local_route, target, scope=scope)
            finally:
                parsed = urlsplit(self.local_route)
                scoped = self.local_route if not scope else f"http://agent-monitor.{scope}:{parsed.password}@127.0.0.1:{parsed.port}"
                report = {"scope": scope, "facts": network_policy.observations(scoped)}
                # Diagnostics are a bounded recent tail, never a prerequisite
                # for the TLS channel. A slow report cannot delay its bytes.
                with self.guard:
                    if self.reports.full():
                        with contextlib.suppress(queue.Empty):
                            self.reports.get_nowait()
                    self.reports.put_nowait(report)
            host = self.address.hostname
            port = self.address.port or (443 if self.address.scheme == "https" else 80)
            target_hub = f"[{host}]:{port}" if ":" in host else f"{host}:{port}"
            tunnel = connect_via(self.local_route, target_hub, scheme=self.address.scheme)
            if self.address.scheme == "https":
                tunnel = ssl.create_default_context().wrap_socket(tunnel, server_hostname=host)
            headers = {"Host": self.address.netloc, **self.headers(), "X-Channel-ID": task["channel"]}
            wire = "CONNECT /api/client-network/channel HTTP/1.1\r\n"
            wire += "".join(f"{key}: {value}\r\n" for key, value in headers.items()) + "\r\n"
            tunnel.sendall(wire.encode())
            response_header(tunnel)
            with self.guard:
                self.sockets.update((upstream, tunnel))
            client_network.relay(upstream, tunnel, self.stopped)
        except (OSError, ValueError, KeyError, client_network.NetworkError):
            # No business replay; the originating client sees a closed tunnel.
            pass
        finally:
            with self.guard:
                for sock in (upstream, tunnel):
                    if sock:
                        self.sockets.discard(sock)
                        client_network.close_socket(sock)
                self.workers.discard(threading.current_thread())

    def report_loop(self):
        while not self.stopped.is_set():
            try:
                report = self.reports.get(timeout=.5)
            except queue.Empty:
                continue
            with contextlib.suppress(client_network.NetworkError):
                self.request("report", report)

    def run(self):
        threading.Thread(target=self.pair_server.serve_forever, daemon=True).start()
        threading.Thread(target=self.report_loop, daemon=True).start()
        print(f"设备网络已连接到 {self.server}。请回到账号页面点击「连接本机网络」。", flush=True)
        print("仅批次发送及其出口预览使用本机网络；保持此命令运行，Ctrl+C 断开。", flush=True)
        try:
            while not self.stopped.is_set():
                task = self.request("next")
                if not task:
                    continue
                with self.guard:
                    if len(self.workers) >= 16:
                        raise client_network.NetworkError("设备转发连接已满，已停止接收；请等待操作结束后重新连接。")
                    thread = threading.Thread(target=self.forward, args=(task,), daemon=True)
                    self.workers.add(thread)
                    thread.start()
        finally:
            self.close()

    def close(self):
        self.stopped.set()
        with self.guard:
            for sock in self.sockets:
                client_network.close_socket(sock)
        with contextlib.suppress(Exception):
            if self.registration:
                self.request("close")
        self.pair_server.shutdown()
        self.pair_server.server_close()


def main():
    parser = argparse.ArgumentParser(description="让账号页面的批次使用这台机器的系统代理规则。")
    parser.add_argument("--server", required=True, help="账号页面所在服务，例如 http://macstudio:39001")
    args = parser.parse_args()
    connector = None
    try:
        # This foreground process owns a local system resolver; never inherit a
        # server-side route accidentally from an invoking process.
        runtime = network_policy._Runtime()
        try:
            connector = Connector(args.server, socket.gethostname(), runtime.endpoint)
            signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
            connector.run()
        finally:
            runtime.close()
    except KeyboardInterrupt:
        print("设备连接已关闭；后续批次不会切换到服务器出口。")
        return 0
    except (OSError, network_policy.NetworkPolicyError, client_network.NetworkError):
        print("设备连接未能保持。请检查服务地址、系统代理及本机 39002 端口，再重新运行连接命令。账号请求不会自动重试。", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
