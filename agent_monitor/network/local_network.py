"""Instance-owned local observations and explicit fixed-target probe cache."""
import hashlib
import os
import socket
import threading
import time

from agent_monitor.paths import PROJECT_ROOT
from . import host_network, process_network
from .process_probe import ProbeCache


class LocalNetwork:
    def __init__(self):
        self.lock = threading.Lock()
        self.cache = None
        self.cached_at = 0
        self.probes = ProbeCache()
        self.subject_id = hashlib.sha256(f'{socket.gethostname()}\0{os.getuid()}\0{PROJECT_ROOT}'.encode()).hexdigest()

    def read(self):
        # Coalesce adjacent tab/navigation reads; never periodically poll the OS.
        with self.lock:
            if self.cache is not None and time.monotonic() - self.cached_at < 2:
                return self.cache
            host = host_network.capture()
            report = process_network.read_report()
            self.cache = {**report, 'subject_id': self.subject_id, 'host_network': host}
            self.cached_at = time.monotonic()
            return self.cache

    def probe(self, pid, instance, kind):
        with self.lock:
            return self.probes.read(pid, instance, kind)
