import concurrent.futures
import http.client
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock

import query_process
import server
import sync_process


ROOT = Path(__file__).resolve().parents[1]


class QueryProcessTests(unittest.TestCase):
    def setUp(self):
        self.popen = subprocess.Popen

    def launch(self, body, **kwargs):
        script = "import sys;sys.path.insert(0,%r)\n" % str(ROOT) + body
        return self.popen([sys.executable, "-c", script], **kwargs)

    def fixture(self, body):
        return mock.patch("sync_process.subprocess.Popen",
                          side_effect=lambda *a, **kw: self.launch(body, **kw))

    def test_all_routes_keep_query_and_large_json_encoding(self):
        body = '''
import datetime, server, query_process
def route(q):
    print('diagnostic must not enter response')
    return {'query': q, 'date': datetime.datetime(2026, 10, 8), 'large': 'x' * 1048576}
server.ROUTES = {path: route for path in query_process.PATHS}
query_process._worker()
'''
        for path in sorted(query_process.PATHS):
            with self.subTest(path=path), self.fixture(body):
                status, data = query_process.response(path, {"project": ["一", "two"]})
                self.assertEqual(status, 200)
                self.assertEqual(json.loads(data), {"query": {"project": ["一", "two"]},
                                 "date": "2026-10-08T00:00:00", "large": "x" * 1048576})
                self.assertFalse(sync_process._active)

    def test_projection_error_retains_status_and_payload(self):
        body = '''
import server, query_process
def route(q):
    raise server.llm_attempts.ProjectionError(409, 'ambiguous', 'choose a machine', machines=['a','b'])
server.ROUTES['/api/llm-calls'] = route
query_process._worker()
'''
        with self.fixture(body):
            status, data = query_process.response("/api/llm-calls", {})
        self.assertEqual(status, 409)
        self.assertEqual(json.loads(data), {"error": {"code": "ambiguous",
                         "message": "choose a machine", "machines": ["a", "b"]}})

    def test_failed_protocol_releases_worker_and_slots(self):
        cases = [(b'', 0), (b'{}\n', 0),
                 (b'{"status":200,"length":4}\n{}', 0),
                 (b'{"status":200,"length":2}\n{}', 7),
                 (b'{"status":200,"length":2}\n{}extra', 0)]
        for output, code in cases:
            with self.subTest(output=output, code=code):
                body = "sys.stdin.readline()\nsys.stdout.buffer.write(%r)\nsys.exit(%d)" % (output, code)
                with self.fixture(body), self.assertRaises((ValueError, RuntimeError)):
                    query_process.response("/api/llm-calls", {})
                self.assertFalse(sync_process._active)
                self.assertTrue(query_process._slots.acquire(blocking=False))
                self.assertTrue(query_process._slots.acquire(blocking=False))
                self.assertFalse(query_process._slots.acquire(blocking=False))
                query_process._slots.release()
                query_process._slots.release()

    def test_worker_exception_is_not_a_success_response(self):
        body = '''
import server, query_process
def route(q): raise RuntimeError('fixture failure')
server.ROUTES['/api/llm-calls'] = route
query_process._worker()
'''
        with self.fixture(body), self.assertRaises(ValueError):
            query_process.response("/api/llm-calls", {})
        self.assertFalse(sync_process._active)

    def test_two_running_queries_do_not_block_detail_or_head(self):
        ready = threading.Event()
        lock = threading.Lock()
        counts = {"peak": 0, "started": 0}
        processes = []
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        release = Path(temporary.name) / "release"
        body = '''
from pathlib import Path
import time
sys.stdin.readline()
deadline = time.monotonic() + 5
while not Path(%r).exists():
    if time.monotonic() > deadline: raise RuntimeError('test did not release workers')
    time.sleep(.01)
sys.stdout.buffer.write(b'{"status":200,"length":2}\\n{}')
''' % str(release)

        def spawn(*args, **kwargs):
            with lock:
                process = self.launch(body, **kwargs)
                processes.append(process)
                counts["started"] += 1
                active = sum(p.poll() is None for p in processes)
                counts["peak"] = max(counts["peak"], active)
                if active == 2:
                    ready.set()
                return process

        def request(path, method="GET"):
            conn = http.client.HTTPConnection("127.0.0.1", httpd.server_port, timeout=8)
            try:
                conn.request(method, path)
                response = conn.getresponse()
                return response.status, dict(response.getheaders()), response.read()
            finally:
                conn.close()

        httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            with mock.patch("server.hub.enabled", return_value=True), \
                 mock.patch.dict(server.ROUTES, {"/api/llm-call-request": lambda q: {"detail": True}}), \
                 mock.patch("sync_process.subprocess.Popen", side_effect=spawn), \
                 concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
                futures = [pool.submit(request, "/api/llm-calls") for _ in range(3)]
                try:
                    self.assertTrue(ready.wait(3))
                    status, headers, data = request("/api/llm-call-request", "HEAD")
                    self.assertEqual(status, 200)
                    self.assertEqual(data, b"")
                    self.assertEqual(int(headers["Content-Length"]), len(b'{"detail": true}'))
                    self.assertEqual(json.loads(request("/api/llm-call-request")[2]), {"detail": True})
                    self.assertEqual(counts["started"], 2)
                finally:
                    release.touch()
                self.assertEqual([f.result()[0] for f in futures], [200, 200, 200])
                self.assertEqual(counts["peak"], 2)
                status, headers, data = request("/api/llm-calls", "HEAD")
                self.assertEqual((status, headers["Content-Length"], data), (200, "2", b""))
                with mock.patch("server.hub.enabled", side_effect=ValueError("bad configuration")) as enabled, \
                     mock.patch.dict(server.ROUTES, {"/api/health": lambda q: {"ok": True},
                                                   "/api/timezone": lambda q: {"timezone": "UTC"}}):
                    self.assertEqual(json.loads(request("/api/health")[2]), {"ok": True})
                    self.assertEqual(json.loads(request("/api/timezone")[2]), {"timezone": "UTC"})
                    enabled.assert_not_called()
        finally:
            release.touch()
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=2)

    def test_shared_shutdown_gate_rejects_query_workers(self):
        with mock.patch.object(sync_process, "_stopping", True), \
             mock.patch("sync_process.subprocess.Popen") as spawn:
            with self.assertRaisesRegex(RuntimeError, "shutting down"):
                query_process.response("/api/llm-calls", {})
            spawn.assert_not_called()
