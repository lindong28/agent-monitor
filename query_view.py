"""Prepared, leased admissions for small aggregate queries.

Full snapshot validation belongs to preparation, never to an HTTP reader.
Daily aggregate SQL remains the single accounting implementation.
"""
import logging
import threading
import time
from contextlib import contextmanager

import generation


logger = logging.getLogger(__name__)


class ViewUnavailable(RuntimeError):
    pass


class _View:
    def __init__(self, context, admission):
        self.context = context
        self.admission = admission
        self.readers = 0
        self.retired = False

    def close(self):
        self.context.__exit__(None, None, None)


class QueryView:
    def __init__(self, *, config_path=generation.DEFAULT_CONFIG_PATH,
                 root=generation.DEFAULT_GENERATIONS_ROOT):
        self.config_path = config_path
        self.root = root
        self._lock = threading.Lock()
        self._prepare_lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._view = None
        self._error = None
        self._thread = None

    def refresh(self):
        with self._prepare_lock:
            if self._stop.is_set():
                return
            started = time.monotonic()
            try:
                context = generation.generation_admission_snapshot(
                    config_path=self.config_path, root=self.root)
                candidate = _View(context, context.__enter__())
            except Exception as exc:
                with self._lock:
                    self._error = exc
                raise
            with self._lock:
                if self._stop.is_set():
                    candidate.close()
                    return
                old = self._view
                self._view, self._error = candidate, None
                if old is not None:
                    old.retired = True
                    if not old.readers:
                        old.close()
            elapsed = time.monotonic() - started
            if elapsed >= 0.1:
                logger.info("query view prepared: sources=%d duration=%.3fs",
                            len(candidate.admission.admitted), elapsed)

    @contextmanager
    def snapshot(self):
        try:
            config = generation.load_machine_config(self.config_path, retirement_root=self.root)
            with self._lock:
                view = self._view
                if self._error is not None or view is None or self._stop.is_set():
                    raise ViewUnavailable("统计读取视图暂不可用，请稍后重试。")
                if config != view.admission.config:
                    raise ViewUnavailable("机器配置已更新，正在准备统计读取视图。")
                for current in view.admission.admitted:
                    if generation._generation_file_signature(current.generation_dir) != current._signature:
                        raise ViewUnavailable("统计快照已变化，正在重新校验。")
                view.readers += 1
        except Exception as exc:
            self._wake.set()
            if isinstance(exc, ViewUnavailable):
                raise
            raise ViewUnavailable("统计读取视图暂不可用，请稍后重试。") from exc
        try:
            yield view.admission
        finally:
            with self._lock:
                view.readers -= 1
                if view.retired and not view.readers:
                    view.close()

    def start(self, interval=10):
        # Startup pays the cold admission before accepting HTTP traffic.
        self.refresh()

        def prepare():
            while not self._stop.is_set():
                self._wake.wait(interval)
                self._wake.clear()
                if self._stop.is_set():
                    break
                try:
                    self.refresh()
                except Exception:
                    logger.exception("query view preparation failed; retrying next interval")

        self._thread = threading.Thread(target=prepare, name="agent-monitor-query-view", daemon=True)
        self._thread.start()

    def close(self):
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
        with self._lock:
            view, self._view = self._view, None
            if view is not None:
                view.retired = True
                if not view.readers:
                    view.close()
