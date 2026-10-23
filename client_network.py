"""Ephemeral browser-device routes. Provider TLS is relayed, never terminated.

The hub remains a trusted personal-network service. Control capabilities belong
to the connector; browser capabilities can only select an existing route.
"""
import atexit
import base64
import contextlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import queue
import re
import secrets
import select
import socket
import ssl
import threading
import time
from urllib.parse import urlsplit

# Transport setup must not impose a shorter cutoff than the existing 120s
# account send phase. Original RPC/probe deadlines remain authoritative.
CONNECT_SECONDS = 120
POLL_SECONDS = 15
CONTROL_SECONDS = CONNECT_SECONDS + POLL_SECONDS
LEASE_SECONDS = CONTROL_SECONDS + 5


class NetworkError(Exception):
    def __init__(self, message, status=409):
        super().__init__(message)
        self.status = status


def authority(value):
    """Validate CONNECT syntax, preserving the existing target/port range."""
    if not isinstance(value, str) or len(value) > 512 or re.search(r"[\s/@?#]", value):
        raise NetworkError("连接目标格式无效。", 400)
    try:
        parsed = urlsplit("//" + value)
        if not parsed.hostname or not parsed.port or parsed.username:
            raise ValueError()
        host = parsed.hostname.encode("idna").decode("ascii")
        if not re.fullmatch(r"[a-zA-Z0-9.:%_-]+", host):
            raise ValueError()
        return host, parsed.port
    except (ValueError, UnicodeError):
        raise NetworkError("连接目标格式无效。", 400) from None


def close_socket(sock):
    with contextlib.suppress(OSError):
        sock.shutdown(socket.SHUT_RDWR)
    with contextlib.suppress(OSError):
        sock.close()


def relay(left, right, stopped):
    """Bounded buffers/backpressure; EOF or route closure ends this tunnel."""
    sockets = (left, right)
    pending = {left: bytearray(), right: bytearray()}
    readers = set(sockets)
    half_closed = set()
    for sock in sockets:
        sock.setblocking(False)
    while not stopped.is_set() and (readers or any(pending.values())):
        for source, destination in ((left, right), (right, left)):
            if source not in readers and not pending[destination] and destination not in half_closed:
                with contextlib.suppress(OSError):
                    destination.shutdown(socket.SHUT_WR)
                half_closed.add(destination)
        eligible = [sock for sock in readers if len(pending[sockets[1 - sockets.index(sock)]]) < 65536]
        buffered = [sock for sock in eligible if isinstance(sock, ssl.SSLSocket) and sock.pending()]
        readable, writable, _ = select.select(
            eligible, [sock for sock in sockets if pending[sock]], [], 0 if buffered else .5)
        for sock in writable:
            try:
                sent = sock.send(pending[sock])
            except (BlockingIOError, ssl.SSLWantReadError, ssl.SSLWantWriteError):
                continue
            if not sent:
                return
            del pending[sock][:sent]
        for sock in set(readable + buffered):
            try:
                data = sock.recv(16384)
            except (BlockingIOError, ssl.SSLWantReadError, ssl.SSLWantWriteError):
                continue
            if not data:
                readers.discard(sock)
                continue
            pending[sockets[1 - sockets.index(sock)]].extend(data)


class Pending:
    def __init__(self, target, scope):
        self.id = secrets.token_hex(16)
        self.target, self.scope = target, scope
        self.ready, self.done = threading.Event(), threading.Event()
        self.socket = None


class Route:
    def __init__(self, label):
        self.id = secrets.token_hex(16)
        self.label = label
        self.control, self.browser, self.secret = (secrets.token_urlsafe(32) for _ in range(3))
        self.stopped = threading.Event()
        self.guard = threading.RLock()
        self.last_seen = time.monotonic()
        self.pending = {}
        self.queue = queue.Queue(maxsize=16)
        self.sockets = set()
        self.receipts = {}
        self.egress_cache, self.egress_lock = {}, threading.Lock()
        self.facts = {"policy": "chromium-system", "coverage": "unavailable", "events": []}
        route = self

        class Proxy(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def scope(self):
                try:
                    value = self.headers.get("Proxy-Authorization", "")
                    if not value.startswith("Basic "):
                        raise ValueError()
                    name, password = base64.b64decode(value[6:], validate=True).decode().split(":", 1)
                    if not re.fullmatch(r"agent-monitor(?:\.[a-f0-9]{32})?", name):
                        raise ValueError()
                    if not secrets.compare_digest(password, route.secret):
                        raise ValueError()
                    return name.partition(".")[2] or None
                except (ValueError, UnicodeError):
                    raise NetworkError("代理连接未授权。", 407) from None

            def do_GET(self):
                try:
                    scope = self.scope()
                    if self.path != "/status":
                        raise NetworkError("未找到接口。", 404)
                    facts = dict(route.facts)
                    facts["events"] = [e for e in facts.get("events", []) if not scope or e.get("scope") == scope]
                    body = json.dumps(facts).encode()
                    self.send_response(200)
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                except NetworkError as exc:
                    self.send_error(exc.status)

            def do_CONNECT(self):
                item = None
                try:
                    scope = self.scope()
                    authority(self.path)
                    item = route.enqueue(self.path, scope, self.connection)
                    deadline = time.monotonic() + CONNECT_SECONDS
                    while not item.ready.wait(.2) and time.monotonic() < deadline:
                        route.require()
                        if select.select([self.connection], [], [], 0)[0] and not self.connection.recv(1, socket.MSG_PEEK):
                            return
                    if not item.socket:
                        raise NetworkError("浏览器设备未能建立连接。", 502)
                    route.require()
                    self.send_response(200, "Connection Established")
                    self.end_headers()
                    self.wfile.flush()
                    relay(self.connection, item.socket, route.stopped)
                except NetworkError as exc:
                    if item is None or not item.socket:
                        with contextlib.suppress(OSError):
                            self.send_error(exc.status)
                except (OSError, ValueError):
                    pass
                finally:
                    if item:
                        route.finish(item, self.connection)
                    self.close_connection = True

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Proxy)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def endpoint(self):
        self.require()
        return f"http://agent-monitor:{self.secret}@127.0.0.1:{self.server.server_port}"

    def require(self):
        if self.stopped.is_set() or time.monotonic() - self.last_seen > LEASE_SECONDS:
            raise NetworkError("浏览器所在设备的连接已断开。请在该设备运行连接命令并重新配对；不会改用服务器出口。")

    def enqueue(self, target, scope, sock):
        with self.guard:
            self.require()
            if len(self.pending) >= 16:
                raise NetworkError("设备转发连接已满，请等待当前操作结束。", 503)
            item = Pending(target, scope)
            try:
                self.queue.put_nowait(item)
            except queue.Full:
                raise NetworkError("设备待连接请求已满，请稍候再试。", 503) from None
            self.pending[item.id] = item
            self.sockets.add(sock)
            return item

    def finish(self, item, sock):
        with self.guard:
            self.pending.pop(item.id, None)
            self.sockets.discard(sock)
            if item.socket:
                self.sockets.discard(item.socket)
                close_socket(item.socket)
            item.done.set()

    def next(self):
        self.require()
        self.last_seen = time.monotonic()
        deadline = time.monotonic() + POLL_SECONDS
        while not self.stopped.is_set() and time.monotonic() < deadline:
            try:
                item = self.queue.get(timeout=.5)
            except queue.Empty:
                continue
            if not item.done.is_set():
                return {"channel": item.id, "target": item.target, "scope": item.scope}
        return {}

    def attach(self, channel, handler):
        with self.guard:
            self.require()
            item = self.pending.get(channel)
            if not item or item.socket or item.done.is_set():
                raise NetworkError("连接请求已失效。", 410)
            handler.send_response(200, "Connection Established")
            handler.end_headers()
            handler.wfile.flush()
            item.socket = handler.connection
            self.sockets.add(item.socket)
            item.ready.set()
        while not item.done.wait(.5) and not self.stopped.is_set():
            pass
        handler.close_connection = True

    def issue(self, selected, expected):
        with self.guard:
            self.require()
            now = time.monotonic()
            self.receipts = {k: v for k, v in self.receipts.items() if v[0] > now}
            if len(self.receipts) >= 32:
                raise NetworkError("待确认的预览过多，请关闭旧窗口后稍候再试。", 429)
            token = secrets.token_urlsafe(32)
            self.receipts[token] = (now + 120, frozenset(selected), expected)
            return token

    def consume(self, token, selected, expected):
        with self.guard:
            self.require()
            receipt = self.receipts.pop(token, None) if isinstance(token, str) else None
            if not receipt or receipt[0] <= time.monotonic() or receipt[1:] != (frozenset(selected), expected):
                raise NetworkError("出口确认已失效或与本轮账号不符，请重新查看出口并确认。")

    def close(self):
        if self.stopped.is_set():
            return
        self.stopped.set()
        with self.guard:
            for sock in self.sockets:
                close_socket(sock)
            self.sockets.clear()
            for item in self.pending.values():
                item.ready.set()
                item.done.set()
            self.receipts.clear()
        self.server.shutdown()
        self.server.server_close()


class Hub:
    def __init__(self):
        self.routes = {}
        self.guard = threading.RLock()

    def register(self, label):
        if not isinstance(label, str) or not label.strip() or len(label) > 80 or any(ord(c) < 32 for c in label):
            raise NetworkError("设备名称无效。", 400)
        with self.guard:
            for key, route in list(self.routes.items()):
                if route.stopped.is_set() or time.monotonic() - route.last_seen > LEASE_SECONDS:
                    route.close()
                    del self.routes[key]
            if len(self.routes) >= 8:
                raise NetworkError("已连接设备过多，请先关闭不用的连接端。", 503)
            route = Route(label.strip())
            self.routes[route.id] = route
            # Ends when the route is closed or its heartbeat lease expires.
            threading.Thread(target=self._watch, args=(route,), daemon=True).start()
            return route

    def _watch(self, route):
        while not route.stopped.wait(1):
            if time.monotonic() - route.last_seen > LEASE_SECONDS:
                route.close()
                return

    def get(self, key, token, *, control=False):
        with self.guard:
            route = self.routes.get(key) if isinstance(key, str) else None
        secret = (route.control if control else route.browser) if route else ""
        if not secret or not isinstance(token, str) or not secrets.compare_digest(secret, token):
            raise NetworkError("尚未连接浏览器所在设备，请先连接本机网络。", 403)
        route.require()
        return route

    def browser_route(self, connection):
        if not isinstance(connection, dict):
            raise NetworkError("请先连接浏览器所在设备的网络。", 403)
        return self.get(connection.get("id"), connection.get("capability"))

    def close(self):
        for route in tuple(self.routes.values()):
            route.close()


hub = Hub()
atexit.register(hub.close)


def handle(handler):
    """Connector-only APIs. Never trust browser-originated control calls."""
    path = urlsplit(handler.path).path
    if not path.startswith("/api/client-network/"):
        return False
    handler.close_connection = True
    try:
        if handler.headers.get("Origin") or handler.headers.get("Sec-Fetch-Site"):
            raise NetworkError("此接口仅供本机连接端使用。", 403)
        if handler.command == "POST" and path == "/api/client-network/register":
            if handler.headers.get("X-Agent-Monitor") != "connector":
                raise NetworkError("连接端标识缺失。", 403)
            length = int(handler.headers.get("Content-Length", "0"))
            if not 0 < length <= 512 or handler.headers.get_content_type() != "application/json":
                raise NetworkError("连接请求无效。", 400)
            data = json.loads(handler.rfile.read(length))
            route = hub.register(data.get("label") if isinstance(data, dict) else None)
            handler._send_json({"id": route.id, "control": route.control,
                                "connection": {"id": route.id, "capability": route.browser, "label": route.label}})
            return True
        authorization = handler.headers.get("Authorization", "")
        if not authorization.startswith("Bearer "):
            raise NetworkError("连接端未授权。", 403)
        route = hub.get(handler.headers.get("X-Connection-ID"), authorization[7:], control=True)
        if handler.command == "POST" and path == "/api/client-network/next":
            handler._send_json(route.next())
        elif handler.command == "POST" and path == "/api/client-network/report":
            length = int(handler.headers.get("Content-Length", "0"))
            if not 0 < length <= 32768:
                raise NetworkError("连接报告过大。", 400)
            data = json.loads(handler.rfile.read(length))
            if not isinstance(data, dict):
                raise NetworkError("连接报告无效。", 400)
            scope, facts = data.get("scope"), data.get("facts")
            if scope is not None and not re.fullmatch(r"[a-f0-9]{32}", scope):
                raise NetworkError("连接报告标识无效。", 400)
            if not isinstance(facts, dict) or not isinstance(facts.get("events"), list):
                raise NetworkError("连接报告无效。", 400)
            with route.guard:
                events = route.facts["events"] + [dict(event, scope=scope) for event in facts["events"][-16:] if isinstance(event, dict)]
                route.facts = {"policy": "chromium-system", "coverage": "recent_connections_only",
                               "generation": facts.get("generation"), "events": events[-64:]}
            handler._send_json({"received": True})
        elif handler.command == "CONNECT" and path == "/api/client-network/channel":
            route.attach(handler.headers.get("X-Channel-ID"), handler)
        elif handler.command == "POST" and path == "/api/client-network/close":
            route.close()
            handler._send_json({"closed": True})
        else:
            raise NetworkError("未找到连接端接口。", 404)
    except NetworkError as exc:
        handler._send_json({"error": str(exc)}, status=exc.status)
    except (ValueError, UnicodeError):
        handler._send_json({"error": "连接端请求无效。"}, status=400)
    return True
