"""One product-owned system proxy route, independent of account HOME.

Importing this module performs no I/O. Only an admitted outbound operation starts
the helper. Descendants inherit the capability rather than creating an engine.
"""
import atexit
import base64
from contextlib import contextmanager
from contextvars import ContextVar
import json
import os
import platform
import re
from pathlib import Path
import secrets
import selectors
import signal
import subprocess
import tempfile
import threading
import time
import urllib.parse
import urllib.request

from proxy_runtime.install import executable
from proxy_runtime.system_changes import SystemChanges

ROOT = Path(__file__).resolve().parent
INTERNAL = "AGENT_MONITOR_PROXY_INTERNAL"
_lock = threading.RLock()
_owner = None


class NetworkPolicyError(RuntimeError):
    def __init__(self, kind):
        self.kind = kind
        super().__init__("系统代理不可用（" + kind + "）；请检查服务主机的系统代理和 agent-monitor 运行依赖。")


def _validate_endpoint(value):
    try:
        parsed = urllib.parse.urlsplit(value)
        valid = (parsed.scheme == "http" and parsed.hostname == "127.0.0.1" and parsed.port
                 and re.fullmatch(r"agent-monitor(?:\.[a-f0-9]{32})?", parsed.username or "") and parsed.password
                 and not parsed.path and not parsed.query and not parsed.fragment)
        if not valid:
            raise ValueError()
    except (TypeError, ValueError):
        raise NetworkPolicyError("invalid_internal_route") from None
    return value


class _Runtime:
    def __init__(self):
        self.process = None
        self.observer = None
        self.directory = tempfile.TemporaryDirectory(prefix="agent-monitor-proxy-")
        self.writes = threading.Lock()
        self.token = secrets.token_urlsafe(32)
        binary = executable()
        if not binary.is_file():
            self.directory.cleanup()
            raise NetworkPolicyError("runtime_missing")
        env = dict(os.environ, AGENT_MONITOR_PROXY_DATA=self.directory.name)
        env.pop("ELECTRON_RUN_AS_NODE", None)
        try:
            flags = ["--ozone-platform=headless", "--disable-gpu"] if platform.system() == "Linux" else []
            self.process = subprocess.Popen([str(binary), *flags, str(ROOT / "proxy_runtime/main.cjs")],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                env=env, start_new_session=True)
            self._write({"token": self.token})
            with selectors.DefaultSelector() as selector:
                selector.register(self.process.stdout, selectors.EVENT_READ)
                deadline = time.monotonic() + 10
                line = b""
                while b"\n" not in line:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0 or not selector.select(remaining):
                        raise NetworkPolicyError("runtime_start_timeout")
                    part = os.read(self.process.stdout.fileno(), 4096)
                    if not part or len(line) + len(part) > 4096:
                        raise NetworkPolicyError("runtime_protocol_failed")
                    line += part
            ready = json.loads(line)
            if ready.get("policy") != "chromium-system" or not isinstance(ready.get("port"), int):
                raise NetworkPolicyError("runtime_protocol_failed")
            self.endpoint = _validate_endpoint(f"http://agent-monitor:{self.token}@127.0.0.1:{ready['port']}")
            self.versions = {key: ready[key] for key in ("electron", "chromium")}
            self.observer = SystemChanges(lambda: self._write({"op": "invalidate"}), self._observer_failed)
        except NetworkPolicyError:
            self.close()
            raise
        except Exception:
            self.close()
            raise NetworkPolicyError("runtime_start_failed") from None

    def _write(self, value):
        with self.writes:
            if self.process is None or self.process.poll() is not None:
                return
            try:
                self.process.stdin.write((json.dumps(value) + "\n").encode())
                self.process.stdin.flush()
            except (OSError, ValueError):
                pass

    def _observer_failed(self):
        # Descendants already hold the capability: stop the bridge too, so they
        # cannot keep opening sockets with a stale configuration generation.
        with self.writes:
            if self.process and self.process.stdin:
                try:
                    self.process.stdin.close()
                except (OSError, ValueError):
                    pass

    def close(self):
        if self.observer:
            self.observer.close()
            self.observer = None
        process = self.process
        if process:
            if process.stdin:
                try:
                    process.stdin.close()
                except (OSError, ValueError):
                    pass
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
            if process.stdout:
                process.stdout.close()
            self.process = None
        self.directory.cleanup()


_selected_route = ContextVar("product_network_route", default=None)


@contextmanager
def using_route(route):
    """Bind only this execution context; never mutate process-global proxy env."""
    token = _selected_route.set(_validate_endpoint(route) if route else None)
    try:
        yield
    finally:
        _selected_route.reset(token)


def endpoint():
    if _selected_route.get():
        return _selected_route.get()
    inherited = os.environ.get(INTERNAL)
    if inherited:
        return _validate_endpoint(inherited)
    global _owner
    with _lock:
        if _owner is None:
            _owner = _Runtime()
        elif _owner.process.poll() is not None or _owner.observer.error:
            # Never silently choose another route or replay an unknown operation.
            raise NetworkPolicyError("runtime_exited")
        return _owner.endpoint


def close():
    global _owner
    with _lock:
        if _owner:
            _owner.close()
            _owner = None


atexit.register(close)


def child_environment(base, scope=None):
    env = dict(base)
    route = endpoint()
    if scope is not None:
        if not re.fullmatch(r"[a-f0-9]{32}", scope):
            raise ValueError("invalid network operation scope")
        address = urllib.parse.urlsplit(route)
        route = f"http://agent-monitor.{scope}:{address.password}@127.0.0.1:{address.port}"
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        env[name] = route
    # Bypass belongs to Chromium, not to each library's NO_PROXY interpretation.
    env["NO_PROXY"] = env["no_proxy"] = ""
    env.pop("AGENT_PROXY_ADDR", None)
    env[INTERNAL] = route
    return env


def observations(route):
    """Read bounded connection facts from our loopback helper, never the provider."""
    try:
        address = urllib.parse.urlsplit(_validate_endpoint(route))
        auth = base64.b64encode((address.username + ":" + address.password).encode()).decode()
        request = urllib.request.Request(f"http://127.0.0.1:{address.port}/status",
            headers={"Proxy-Authorization": "Basic " + auth})
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request, timeout=.5) as response:
            return json.loads(response.read(65536))
    except Exception:
        return {"policy": "chromium-system", "coverage": "unavailable", "events": []}


def session():
    import requests
    result = requests.Session()
    result.trust_env = False
    for name in ("REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE"):
        if os.environ.get(name):
            result.verify = os.environ[name]
            break
    route = endpoint()
    result.proxies.update(http=route, https=route)
    return result


class _ProductProxy(urllib.request.ProxyHandler):
    """urllib's stock ProxyHandler consults ambient bypass even with a proxy map."""
    def __init__(self):
        super().__init__({"http": "product", "https": "product"})

    def proxy_open(self, req, _proxy, type):
        parsed = urllib.parse.urlsplit(endpoint())
        encoded = base64.b64encode((parsed.username + ":" + parsed.password).encode()).decode()
        req.add_unredirected_header("Proxy-Authorization", "Basic " + encoded)
        req.set_proxy(parsed.netloc.rsplit("@", 1)[1], "http")
        return None


def opener(*handlers):
    return urllib.request.build_opener(_ProductProxy(), *handlers)
