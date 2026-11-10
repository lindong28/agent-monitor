"""Bounded network report execution and coalesced instance cache."""
import json
import logging
import math
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import threading
import time

from agent_monitor import diagnostics
from agent_monitor.network import network_policy

logger = logging.getLogger(__name__)


class NetworkReport:
    def __init__(self, *, runner=None, child_environment=None, clock=None, hostname=None, ttl=60.0,
                 state_path=None, wall_clock=None):
        self.runner = runner or subprocess.run
        self.child_environment = child_environment or network_policy.child_environment
        self.clock = clock or time.monotonic
        self.hostname = hostname or socket.gethostname
        self.ttl = ttl
        self.wall_clock = wall_clock or time.time
        self.state_path = Path(state_path) if state_path is not None else None
        self._cache = {"ts": 0.0, "data": None}
        self._last_good = None
        self._attempted_at = None
        self._persistence_error = None
        self._lock = threading.Lock()
        self._probe_lock = threading.Lock()
        self._restored = False

    def read(self):
        data, refresh = self._read()
        with self._lock:
            return self._response(data, refresh)

    def report(self):
        return self._read()[0]

    def cached(self):
        """Return dated evidence only. Expiry never starts a diagnostic here."""
        with self._lock:
            self._ensure_restored()
            data = self._cache['data']
            if data is None:
                return {'checked_host': self.hostname(), 'available': False,
                        'refresh': {'reused': True, 'retry_in_seconds': 0},
                        **({'persistence_error': self._persistence_error} if self._persistence_error else {})}
            return {**self._response(data, self._refresh(True)), 'available': True, 'historical': True}

    def _refresh(self, reused):
        remaining = max(0, self._cache.get('ttl', self.ttl) - (self.clock() - self._cache['ts']))
        return {'reused': reused, 'retry_in_seconds': math.ceil(remaining)}

    def _ensure_restored(self):
        if not self._restored:
            self._restored = True
            self._restore()

    def _response(self, data, refresh):
        failure = self._failure(data)
        failed = bool(failure)
        retained = failed and self._last_good is not None
        response = {**(self._last_good if retained else data), 'checked_host': self.hostname(),
                    'refresh': refresh, 'stale': retained or refresh['retry_in_seconds'] == 0}
        if retained:
            response['last_attempt'] = {**data, 'error': failure, 'attempted_at': self._attempted_at}
        if self._persistence_error:
            response['persistence_error'] = self._persistence_error
        return response

    @staticmethod
    def _failure(data):
        if data.get('error'):
            return data['error']
        if data.get('installed') is False:
            return 'ip-check 未安装'
        public = data.get('public')
        if isinstance(public, dict) and public.get('ok') is False:
            return public.get('error') or '公网出口检测失败'
        for error in data.get('errors') or []:
            if error.get('section') == 'public':
                return error.get('message') or '公网出口检测失败'
        return None

    @classmethod
    def _safe_report(cls, value):
        if isinstance(value, dict):
            return {key: cls._safe_report(item) for key, item in value.items()}
        if isinstance(value, list):
            return [cls._safe_report(item) for item in value]
        if isinstance(value, str):
            return diagnostics.safe_upstream_detail(value)
        return value

    def _restore(self):
        if self.state_path is None:
            return
        try:
            value = json.loads(self.state_path.read_text())
            if value['version'] != 1 or value['checked_host'] != self.hostname():
                return
            data, good = value['data'], value['last_good']
            if not isinstance(data, dict) or (good is not None and not isinstance(good, dict)):
                raise ValueError('invalid report')
            deadline = value['retry_at']
            if isinstance(deadline, bool) or not isinstance(deadline, (int, float)) or not math.isfinite(deadline):
                raise ValueError('invalid retry time')
            remaining = max(0, deadline - self.wall_clock())
            self._cache.update(ts=self.clock(), data=data, ttl=remaining)
            self._last_good = good
            self._attempted_at = value['attempted_at']
        except FileNotFoundError:
            pass
        except (OSError, ValueError, TypeError, KeyError) as exc:
            logger.warning('Network cache restore failed (%s)', type(exc).__name__)
            self._persistence_error = '上次检测记录无法读取；本次可重新检测。'

    def _persist(self):
        if self.state_path is None:
            return
        temporary = None
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            value = {'version': 1, 'checked_host': self.hostname(), 'data': self._cache['data'],
                     'last_good': self._last_good, 'attempted_at': self._attempted_at,
                     'retry_at': self.wall_clock() + self._refresh(True)['retry_in_seconds']}
            with tempfile.NamedTemporaryFile(mode='w', dir=self.state_path.parent,
                                             prefix='.network-report-', delete=False) as stream:
                temporary = stream.name
                json.dump(value, stream, ensure_ascii=False, allow_nan=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.state_path)
            self._persistence_error = None
        except (OSError, ValueError, TypeError) as exc:
            logger.warning('Network cache save failed (%s)', type(exc).__name__)
            self._persistence_error = '检测结果未能保存；服务重启后可能需要重新检测。'
        finally:
            if temporary is not None:
                try:
                    os.unlink(temporary)
                except FileNotFoundError:
                    pass

    def _read(self):
        # Serialize probes, but never hold the read-side lock during public I/O.
        with self._probe_lock:
            with self._lock:
                self._ensure_restored()
                if self._cache['data'] is not None and self._refresh(True)['retry_in_seconds'] > 0:
                    return self._cache['data'], self._refresh(True)
            data = self._safe_report(self.probe())
            delay = data.get("retry_after_seconds")
            ttl = self.ttl
            if (not isinstance(delay, bool) and isinstance(delay, (int, float))
                    and math.isfinite(delay) and delay >= 0):
                ttl = max(ttl, delay)
            with self._lock:
                self._cache.update(ts=self.clock(), data=data, ttl=ttl)
                self._attempted_at = self.wall_clock()
                if not self._failure(data):
                    self._last_good = data
                self._persist()
                return data, self._refresh(False)


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
