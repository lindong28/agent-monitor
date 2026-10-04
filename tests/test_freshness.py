import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]


class SourceSignatureTests(unittest.TestCase):
    def test_signature_is_deterministic_and_matches_boot(self):
        import server

        self.assertTrue(server.BOOT_SIGNATURE)
        self.assertEqual(server._source_signature(), server._source_signature())
        self.assertEqual(server._source_signature(), server.BOOT_SIGNATURE)

    def test_source_files_are_python_modules_only(self):
        import server

        files = server._source_files()
        self.assertIn(ROOT / "server.py", files)
        self.assertTrue(files)
        self.assertTrue(all(path.suffix == ".py" for path in files))
        self.assertFalse(any("tests" in path.parts for path in files))

    def test_signature_changes_when_file_set_changes(self):
        import server

        full = server._source_signature()
        original = server._source_files
        try:
            server._source_files = lambda: original()[:-1]
            reduced = server._source_signature()
        finally:
            server._source_files = original
        self.assertNotEqual(full, reduced)


class HealthStaleTests(unittest.TestCase):
    def test_health_tracks_shared_gateway_reader_changes(self):
        import server

        loaded_funding = Path(server.llm_attempts._funding.__file__)
        loaded_vertex = Path(server.llm_attempts._vertex.__file__)
        dependencies = (
            (loaded_funding, '"company_subscription": ("company", "subscription")',
             '"company_subscription": ("personal", "subscription")'),
            (loaded_vertex, '"vertex_request_mode",', '"vertex_request_mode", "future_control",'),
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            web_root = root / "agent-monitor"
            web_root.mkdir()
            (web_root / "server.py").write_text("# unchanged server\n", encoding="utf-8")
            shared = root / "llm-gateway" / "src" / "llm_gateway"
            shared.mkdir(parents=True)
            for loaded, _, _ in dependencies:
                (shared / loaded.name).write_text(loaded.read_text(encoding="utf-8"), encoding="utf-8")
            with mock.patch.object(server, "ROOT", web_root), mock.patch.object(server.gateway_dependency, "PACKAGE", shared):
                boot = server._source_signature()
                with mock.patch.object(server, "BOOT_SIGNATURE", boot):
                    self.assertFalse(server.health({"asset_watch": ["1"]})["stale"])
                    for loaded, before, after in dependencies:
                        path = shared / loaded.name
                        original = path.read_text(encoding="utf-8")
                        changed = original.replace(before, after)
                        self.assertNotEqual(original, changed)
                        with self.subTest(dependency=loaded.name):
                            path.write_text(changed, encoding="utf-8")
                            self.assertNotEqual(server._source_signature(), boot)
                            self.assertTrue(server.health({"asset_watch": ["1"]})["stale"])
                            path.write_text(original, encoding="utf-8")
                            self.assertEqual(server._source_signature(), boot)
                            self.assertFalse(server.health({"asset_watch": ["1"]})["stale"])
        self.assertIn(loaded_funding, server._source_files())
        self.assertIn(loaded_vertex, server._source_files())

    def test_health_reports_signature_and_not_stale_at_boot(self):
        import server

        payload = server.health({"asset_watch": ["1"]})
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["signature"], server.BOOT_SIGNATURE)
        self.assertTrue(payload["web_signature"])
        self.assertFalse(payload["stale"])

    def test_health_marks_legacy_asset_watchers_stale(self):
        import server

        self.assertTrue(server.health({})["stale"])

    def test_health_reports_stale_when_boot_signature_diverges(self):
        import server

        original = server.BOOT_SIGNATURE
        try:
            server.BOOT_SIGNATURE = "deadbeefdeadbeef"
            self.assertTrue(server.health({})["stale"])
        finally:
            server.BOOT_SIGNATURE = original


class FreshnessWiringTests(unittest.TestCase):
    WARNING = "未能请求最新用量快照；将打开现有数据。请在页面中点 Refresh 重试。"

    def _run_launcher_open(self, overview_json, overview_exit=0, running=True):
        with tempfile.TemporaryDirectory() as tmp:
            sandbox = Path(tmp)
            launcher_root = sandbox / "agent-monitor"
            launcher_root.mkdir()
            launcher = launcher_root / "agent-monitor"
            shutil.copy2(ROOT / "agent-monitor", launcher)
            shutil.copy2(ROOT / "hub.py", launcher_root / "hub.py")
            shutil.copy2(ROOT / "hub.json", launcher_root / "hub.json")

            state = launcher_root / "state"
            state.mkdir()
            if running:
                (state / "pid").write_text(str(os.getpid()), encoding="utf-8")
                (state / "port").write_text("39123", encoding="utf-8")

            fake_bin = sandbox / "bin"
            fake_bin.mkdir()
            os.symlink(sys.executable, fake_bin / "python3")
            curl_log = sandbox / "curl.log"
            open_log = sandbox / "open.log"
            (fake_bin / "curl").write_text(
                """#!/usr/bin/env bash
printf '%s\\n' "$*" >> "$AGENT_MONITOR_TEST_CURL_LOG"
url="${!#}"
case "$url" in
  */api/health*) printf '%s' '{"stale": false}' ;;
  */api/overview*)
    printf '%s' "$AGENT_MONITOR_TEST_OVERVIEW_JSON"
    exit "$AGENT_MONITOR_TEST_OVERVIEW_EXIT"
    ;;
  *) exit 64 ;;
esac
""",
                encoding="utf-8",
            )
            (fake_bin / "open").write_text(
                """#!/usr/bin/env bash
printf '%s\\n' "$*" >> "$AGENT_MONITOR_TEST_OPEN_LOG"
""",
                encoding="utf-8",
            )
            (fake_bin / "nc").write_text(
                """#!/usr/bin/env bash
exit 1
""",
                encoding="utf-8",
            )
            (fake_bin / "curl").chmod(0o755)
            (fake_bin / "open").chmod(0o755)
            (fake_bin / "nc").chmod(0o755)

            env = os.environ.copy()
            env.update(
                {
                    "PATH": str(fake_bin) + os.pathsep + env["PATH"],
                    "AGENT_MONITOR_TEST_CURL_LOG": str(curl_log),
                    "AGENT_MONITOR_TEST_OPEN_LOG": str(open_log),
                    "AGENT_MONITOR_TEST_OVERVIEW_JSON": overview_json,
                    "AGENT_MONITOR_TEST_OVERVIEW_EXIT": str(overview_exit),
                    "AGENT_MONITOR_PORT": "39123",
                }
            )
            result = subprocess.run(
                [str(launcher), "open"],
                capture_output=True,
                text=True,
                env=env,
                timeout=10,
            )
            curl_calls = curl_log.read_text(encoding="utf-8").splitlines()
            open_calls = open_log.read_text(encoding="utf-8").splitlines()
            return result, curl_calls, open_calls

    def test_open_requests_fresh_generation_without_using_external_proxy(self):
        result, curl_calls, open_calls = self._run_launcher_open(
            '{"sync":{"refresh_pending":true,"syncing":false}}'
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        overview_calls = [call for call in curl_calls if "/api/overview" in call]
        self.assertEqual(len(overview_calls), 1, curl_calls)
        overview_call = overview_calls[0]
        self.assertIn("--noproxy *", overview_call)
        self.assertIn("force=1", overview_call)
        self.assertTrue(all("--noproxy *" in call for call in curl_calls), curl_calls)
        self.assertNotIn(self.WARNING, result.stderr)
        self.assertEqual(open_calls, ["http://127.0.0.1:39123"])

    def test_open_requests_fresh_generation_after_cold_start(self):
        result, curl_calls, open_calls = self._run_launcher_open(
            '{"sync":{"refresh_pending":true,"syncing":true}}',
            running=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("agent-monitor started: http://127.0.0.1:39123", result.stdout)
        self.assertEqual(
            len([call for call in curl_calls if "/api/overview" in call]),
            1,
            curl_calls,
        )
        self.assertEqual(open_calls, ["http://127.0.0.1:39123"])

    def test_open_reuses_an_already_running_sync(self):
        result, curl_calls, open_calls = self._run_launcher_open(
            '{"sync":{"refresh_pending":false,"syncing":true}}'
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            len([call for call in curl_calls if "/api/overview" in call]),
            1,
            curl_calls,
        )
        self.assertNotIn(self.WARNING, result.stderr)
        self.assertEqual(open_calls, ["http://127.0.0.1:39123"])

    def test_open_warns_but_still_opens_when_freshness_request_fails(self):
        cases = (
            ("transport failure", "", 22),
            ("invalid response", "not-json", 0),
            (
                "no refresh pending",
                '{"sync":{"refresh_pending":false,"syncing":false}}',
                0,
            ),
        )
        for name, response, exit_code in cases:
            with self.subTest(name=name):
                result, _curl_calls, open_calls = self._run_launcher_open(
                    response, overview_exit=exit_code
                )

                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(self.WARNING, result.stderr)
                self.assertEqual(open_calls, ["http://127.0.0.1:39123"])

    def test_app_js_polls_health_and_renders_stale_banner(self):
        js = (ROOT / "web" / "app.js").read_text()

        self.assertIn("/api/health", js)
        self.assertIn("stale-banner", js)
        self.assertIn("startFreshnessWatch", js)
        self.assertIn("/api/restart", js)
        self.assertIn("asset_watch", js)
        self.assertIn("web_signature", js)

    def test_static_responses_disable_browser_cache(self):
        import server

        headers = {}
        handler = SimpleNamespace(
            send_response=lambda _status: None,
            send_header=lambda name, value: headers.__setitem__(name, value),
            end_headers=lambda: None,
            send_error=lambda _status: self.fail("unexpected static response error"),
            wfile=BytesIO(),
        )

        server.Handler._serve_static(handler, "/", send_body=False)

        self.assertEqual(headers.get("Cache-Control"), "no-store")

    def _static_handler(self):
        state = {"status": None, "body": BytesIO()}
        handler = SimpleNamespace(
            send_response=lambda status: state.__setitem__("status", status),
            send_header=lambda name, value: None,
            end_headers=lambda: None,
            send_error=lambda status: state.__setitem__("status", status),
            wfile=state["body"],
        )
        return handler, state

    def test_static_serves_a_vendor_asset_through_a_symlink_outside_root(self):
        """Hub deployments symlink web/vendor to the checkout that ran
        install.sh. Judging containment on the resolved target sent every asset
        behind that link to 404 — the production reading was three blank charts
        with no message — so containment is judged on the request path."""
        import server

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "deployment"
            (root / "web").mkdir(parents=True)
            vendor = Path(tmp) / "checkout-vendor"
            vendor.mkdir()
            (vendor / "chart.umd.min.js").write_bytes(b"window.Chart = 1;")
            (root / "web" / "vendor").symlink_to(vendor)
            handler, state = self._static_handler()
            with mock.patch.object(server, "ROOT", root), mock.patch.object(
                server, "WEB_ROOT", root / "web"
            ):
                server.Handler._serve_static(handler, "/web/vendor/chart.umd.min.js")

        self.assertEqual(state["status"], 200)
        self.assertEqual(state["body"].getvalue(), b"window.Chart = 1;")

    def test_static_rejects_traversal_out_of_the_web_directory(self):
        import server

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "deployment"
            (root / "web").mkdir(parents=True)
            (root / "secret.txt").write_text("no")
            for path in ("/web/../secret.txt", "/web/vendor/../../secret.txt", "/web/%2e%2e/secret.txt"):
                handler, state = self._static_handler()
                with mock.patch.object(server, "ROOT", root), mock.patch.object(
                    server, "WEB_ROOT", root / "web"
                ):
                    server.Handler._serve_static(handler, path)
                self.assertEqual(state["status"], 404, path)
                self.assertEqual(state["body"].getvalue(), b"", path)

    def test_styles_define_stale_banner(self):
        css = (ROOT / "web" / "styles.css").read_text()

        self.assertIn(".stale-banner", css)

    def test_dispatcher_refreshes_when_stale(self):
        script = (ROOT / "agent-monitor").read_text()

        self.assertIn("refresh_if_stale", script)
        self.assertIn("/api/health", script)
        self.assertIn("asset_watch=1", script)
        self.assertIn("compile_ok", script)

    def test_server_exposes_post_restart_endpoint(self):
        src = (ROOT / "server.py").read_text()

        self.assertIn("def do_POST", src)
        self.assertIn("/api/restart", src)
        self.assertIn("_compile_check", src)


if __name__ == "__main__":
    unittest.main()
