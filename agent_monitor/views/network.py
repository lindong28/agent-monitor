"""Bounded network report execution and coalesced instance cache."""
import json
import math
import os
import socket
import subprocess
import threading
import time

import diagnostics
import network_policy


class NetworkReport:
    def __init__(self, *, runner=None, child_environment=None, clock=None, hostname=None, ttl=60.0):
        self.runner = runner or subprocess.run
        self.child_environment = child_environment or network_policy.child_environment
        self.clock = clock or time.monotonic
        self.hostname = hostname or socket.gethostname
        self.ttl = ttl
        self._cache = {"ts": 0.0, "data": None}
        self._lock = threading.Lock()

    def read(self):
        return {**self.report(), "checked_host": self.hostname()}

    def report(self):
        # A refresh click shares the same request budget as polling. Hold the lock
        # through the bounded child call so concurrent clients share failures too.
        with self._lock:
            now = self.clock()
            if (self._cache["data"] is not None
                    and now - self._cache["ts"] < self._cache.get("ttl", self.ttl)):
                return self._cache["data"]
            data = self.probe()
            delay = data.get("retry_after_seconds")
            ttl = self.ttl
            if (not isinstance(delay, bool) and isinstance(delay, (int, float))
                    and math.isfinite(delay) and delay >= 0):
                ttl = max(ttl, delay)
            self._cache.update(ts=self.clock(), data=data, ttl=ttl)
            return data


    def probe(self):
        try:
            result = self.runner(["ip-check", "--json"], capture_output=True, text=True, timeout=30,
                                    env=self.child_environment(os.environ))
            if result.returncode != 0:
                return {
                    "error": result.stderr or "ip-check exited non-zero",
                    "installed": True,
                    "verdict": "unknown",
                }
            data = json.loads(result.stdout)
            if not isinstance(data, dict):
                return {"error": "ip-check returned an invalid report", "installed": True, "verdict": "unknown"}
            return data
        except network_policy.NetworkPolicyError as exc:
            return {"error": str(exc), "installed": True, "verdict": "unknown"}
        except FileNotFoundError:
            return {
                "error": "PATH 里找不到 ip-check 命令",
                "installed": False,
                "verdict": "unknown",
                "hint": "运行 ./install.sh 安装",
            }
        except subprocess.TimeoutExpired:
            return {
                "error": "ip-check 超时（>30s），外部 API 可能较慢",
                "installed": True,
                "verdict": "unknown",
            }
        except json.JSONDecodeError as exc:
            return {
                "error": f"ip-check returned invalid JSON: {exc}",
                "installed": True,
                "verdict": "unknown",
            }
        except OSError as exc:
            return {"error": "无法启动 ip-check：" + diagnostics.failure_summary(
                diagnostics.failure_details(exc, stage="network_start")), "installed": True, "verdict": "unknown"}
