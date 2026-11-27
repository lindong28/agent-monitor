"""Release verifier tests. The explicit browser exercises a synthetic loopback dashboard."""
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from scripts import verify_overview_release as verifier


PAGE = '''<meta charset=utf-8><select id=range><option value=7d>7</option><option value=30d>30</option></select>
<p id=range-label></p><details id=daily-cost-data><summary>Trend</summary><table><tbody></tbody></table></details>
<script>
const selector = document.querySelector('#range');
async function update() {
  const range = selector.value;
  history.replaceState(null, '', '/?range=' + range);
  document.querySelector('#range-label').textContent = '近 ' + parseInt(range) + ' 天成本';
  const data = await (await fetch('/api/overview?range=' + range)).json();
  if (range === '30d' && data.tryRefresh) await fetch('/api/refresh', {method:'POST'});
  if (range === '30d' && data.keepOldRows) return;
  document.querySelector('tbody').innerHTML = data.tokens_over_time.rows.map(row =>
    '<tr><th>' + row.x + '</th><td>' + row.values.codex + ' tokens</td></tr>').join('');
}
selector.onchange = update; update();
</script>'''.encode()


class RangeTests(unittest.TestCase):
    def test_sparse_window_and_invalid_calendar(self):
        end = date(2026, 11, 27)
        observed = {"dates": ["2026-11-22", "2026-11-26"], "start": "2026-11-21", "end": end.isoformat()}
        self.assertEqual(verifier.report_range("7d", observed, end, end)["rows"], 2)
        for invalid in ({"dates": []}, {"dates": ["2026-11-20"]},
                        {"dates": ["2026-11-22"] * 2}, {"start": "2026-10-29"}, {"end": "2026-11-23"}):
            with self.subTest(invalid=invalid), self.assertRaises(verifier.CheckError):
                verifier.report_range("7d", {**observed, **invalid}, end, end)

    def test_only_numeric_loopback_dashboard_origin(self):
        self.assertEqual(verifier.local_origin("http://[::1]:39001/"), "http://[::1]:39001")
        for invalid in ("http://device:39001", "http://127.0.0.1", "http://127.0.0.1:39001/api/refresh",
                        "http://user@127.0.0.1:39001", "http://127.0.0.1:39001/?force=1"):
            with self.subTest(url=invalid), self.assertRaises(ValueError):
                verifier.local_origin(invalid)


    def test_invalid_daemon_pid_still_reaps_private_chrome(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            def invoke(argv, **kwargs):
                if "get" in argv:
                    session = argv[argv.index("--session") + 1]
                    (root / (session + ".pid")).write_text("")
                    (root / "chrome.pid").write_text("4567")
                return subprocess.CompletedProcess(argv, 0, stdout=b'{"success":true,"data":{}}')

            with patch.object(verifier, "process_identity", return_value=b"start chrome"), \
                    patch.object(subprocess, "run", side_effect=invoke), \
                    patch.object(verifier, "reap_owned") as reap, \
                    self.assertRaisesRegex(verifier.CheckError, "process identity unavailable"):
                verifier.run_check("http://127.0.0.1:39001", "agent-browser", "chrome", root, root)
            reap.assert_called_once_with(4567, b"start chrome")


@unittest.skipUnless(os.environ.get("AGENT_MONITOR_TEST_RELEASE_BROWSER") == "1",
                     "explicit isolated browser acceptance")
class BrowserTests(unittest.TestCase):
    def run_dashboard(self, mode):
        requests = []

        class Fixture(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                requests.append(("POST", self.path))
                self.send_error(405)

            def do_GET(self):
                requests.append(("GET", self.path))
                url = urlsplit(self.path)
                if url.path == "/" and mode == "redirect":
                    self.send_response(302)
                    self.send_header("Location", "/other?range=7d")
                    self.end_headers()
                    return
                if url.path in ("/", "/other"):
                    body, kind = PAGE, "text/html"
                    if url.path == "/other":
                        body = body.replace(b"'/?range='", b"'/other?range='")
                elif url.path == "/api/overview":
                    window = int(parse_qs(url.query)["range"][0][:-1])
                    end = datetime.now(verifier.BUCKET_ZONE).date()
                    start = end - timedelta(days=window - 1)
                    days = [(end - timedelta(days=n)).isoformat() for n in (29, 14, 1, 0)]
                    rows = [{"x": day, "values": {"codex": i + 3}} for i, day in enumerate(days)
                            if day >= start.isoformat()]
                    payload = {"tokens_over_time": {"rows": rows, "columns": ["codex"]},
                               "daily_cost_30d": [{"date": day} for day in days],
                               "rollup_coverage": {"range_start": start.isoformat()},
                               "attribution_windows": {"week": {"end": end.isoformat()}}, mode: True}
                    body, kind = json.dumps(payload).encode(), "application/json"
                else:
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header("Content-Type", kind)
                self.end_headers()
                self.wfile.write(body)

        with ThreadingHTTPServer(("127.0.0.1", 0), Fixture) as server:
            thread = threading.Thread(target=server.serve_forever)
            thread.start()
            try:
                url = f"http://127.0.0.1:{server.server_port}"
                result = subprocess.run([sys.executable, verifier.__file__, "--url", url],
                                        capture_output=True, text=True, timeout=145)
            finally:
                server.shutdown()
                thread.join()
        self.assertFalse(any(method == "POST" for method, _ in requests), requests)
        if mode != "redirect":
            self.assertTrue(any("range=30d" in path for _, path in requests), result.stderr)
        return result

    def test_sparse_success(self):
        result = self.run_dashboard("normal")
        self.assertEqual(result.returncode, 0, result.stderr)
        ranges = json.loads(result.stdout)["ranges"]
        self.assertEqual([ranges[name]["rows"] for name in ("30d", "7d")], [4, 2])

    def test_stale_rows_under_new_heading_fail(self):
        result = self.run_dashboard("keepOldRows")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("30d: did not render", result.stderr)

    def test_redirect_to_another_page_fails(self):
        result = self.run_dashboard("redirect")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unexpected dashboard location", result.stderr)

    def test_write_attempt_blocked_before_dispatch(self):
        result = self.run_dashboard("tryRefresh")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("outside the dashboard reads", result.stderr)
