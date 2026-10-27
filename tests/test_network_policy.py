"""Product routing regressions; synthetic identities and loopback peers only."""
import os
import platform
import base64
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import codex_accounts
import network_policy


class IsolatedAccountRoutingTests(unittest.TestCase):
    def test_bridge_transport_contract(self):
        result = subprocess.run(["node", "--test", str(Path(__file__).with_name("test_system_proxy.cjs"))],
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_private_home_keeps_product_route_instead_of_personal_proxy(self):
        endpoint = "http://agent-monitor:synthetic-test-capability@127.0.0.1:45678"
        with tempfile.TemporaryDirectory() as directory, mock.patch.dict(os.environ, {
            "AGENT_MONITOR_PROXY_INTERNAL": endpoint,
            "HTTPS_PROXY": "http://127.0.0.1:49999",
            "NO_PROXY": "*",
        }, clear=True):
            env = codex_accounts.child_environment(Path(directory))
        self.assertEqual(env["HTTPS_PROXY"], endpoint)
        self.assertEqual(env["HTTP_PROXY"], endpoint)
        self.assertEqual(env["NO_PROXY"], "")
        self.assertEqual(env["HOME"], str(Path(directory) / "home"))
        self.assertNotIn("AGENT_PROXY_ADDR", env)


@unittest.skipUnless(network_policy.executable().is_file(), "install the pinned system resolver with ./install.sh --no-services")
class RuntimeConsumerTests(unittest.TestCase):
    def test_runtime_installer_stdout_is_a_ledger_state(self):
        with tempfile.TemporaryDirectory() as home:
            result = subprocess.run([sys.executable, str(network_policy.ROOT / 'proxy_runtime/install.py')],
                                    env=dict(os.environ, HOME=home), capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(result.stdout.strip(), ('installed', 'present'))

    def test_chromium_reference_and_bridge_choose_same_local_proxy(self):
        with tempfile.TemporaryDirectory() as home:
            env = dict(os.environ, HOME=home, AGENT_MONITOR_PROXY_DATA=home)
            env.pop("ELECTRON_RUN_AS_NODE", None)
            flags = ["--ozone-platform=headless", "--disable-gpu"] if platform.system() == "Linux" else []
            result = subprocess.run([str(network_policy.executable()), *flags,
                str(Path(__file__).with_name("chromium_proxy_fixture.cjs"))],
                env=env, capture_output=True, text=True, timeout=25)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertTrue(json.loads(result.stdout)["ok"])

    def test_real_engine_python_clients_and_private_child_share_one_bridge(self):
        seen = []
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_GET(self):
                seen.append(self.headers.get("Proxy-Authorization"))
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"fixture")
        source = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=source.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(source.server_close)
        self.addCleanup(source.shutdown)
        network_policy.close()
        self.addCleanup(network_policy.close)
        sandbox_env = {key: os.environ[key] for key in ("CHROME_DEVEL_SANDBOX",) if key in os.environ}
        with tempfile.TemporaryDirectory() as home, mock.patch.dict(os.environ, {
            "HOME": home, "PATH": "/usr/bin:/bin", "NO_PROXY": "*",
            "HTTPS_PROXY": "http://127.0.0.1:1",
            **sandbox_env,
        }, clear=True):
            for host in ("127.0.0.1", "localhost"):
                url = f"http://{host}:{source.server_port}/fixture"
                with network_policy.session() as client:
                    self.assertEqual(client.get(url, timeout=5).text, "fixture")
                with network_policy.opener().open(url, timeout=5) as response:
                    self.assertEqual(response.read(), b"fixture")
            owner = network_policy._owner
            child_home = Path(home) / "account"
            child_home.mkdir()
            child = subprocess.run([sys.executable, "-c",
                "import requests,sys; assert requests.get(sys.argv[1],timeout=5).text=='fixture'", url],
                env=codex_accounts.child_environment(child_home), capture_output=True, timeout=10)
            self.assertEqual(child.returncode, 0, "synthetic child request failed")
            address = urllib.parse.urlsplit(owner.endpoint)
            request = urllib.request.Request(f"http://127.0.0.1:{address.port}/status", headers={
                "Proxy-Authorization": "Basic " + base64.b64encode(("agent-monitor:" + owner.token).encode()).decode()})
            direct = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with direct.open(request, timeout=2) as response:
                status = json.load(response)
            self.assertEqual(len(status["events"]), 5)
            self.assertEqual({row["route"] for row in status["events"]}, {"DIRECT"})
            self.assertEqual(seen, [None] * 5)
            self.assertIs(network_policy._owner, owner)
            self.assertFalse(owner.observer.error)
            process, observer = owner.process, owner.observer
            network_policy.close()
            self.assertIsNotNone(process.poll())
            self.assertFalse(observer.thread.is_alive())

    def test_missing_engine_does_not_send_direct(self):
        network_policy.close()
        with mock.patch.dict(os.environ, {}, clear=True), \
                mock.patch.object(network_policy, "executable", return_value=Path("/missing/fixture/electron")):
            with self.assertRaises(network_policy.NetworkPolicyError) as caught:
                network_policy.session()
        self.assertEqual(caught.exception.kind, "runtime_missing")
