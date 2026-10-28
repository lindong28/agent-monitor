"""Local HTTP admission must not depend on reverse DNS for known hosts."""
import unittest
from unittest import mock

from agent_monitor import http_server as server


class BrowserDnsTests(unittest.TestCase):
    def check(self, host, **headers):
        handler = object.__new__(server.Handler)
        handler.headers = {"Host": host, "Origin": "http://" + host,
                           "X-Agent-Monitor": "action", **headers}
        handler._send_json = mock.Mock()
        return handler._check_browser_boundary("action", require_origin=True)

    def test_binding_local_addresses_does_not_resolve_dns(self):
        for host in ("127.0.0.1", "localhost"):
            with self.subTest(host=host), mock.patch.object(server.socket, "getfqdn", side_effect=AssertionError("DNS used")):
                http = server.ThreadingHTTPServer((host, 0), server.Handler)
                try:
                    self.assertGreater(http.server_port, 0)
                    self.assertEqual(http.server_name, http.server_address[0])
                finally:
                    http.server_close()

    def test_known_hosts_do_not_resolve_dns(self):
        with mock.patch.object(server.socket, "gethostname", return_value="monitor.example"),\
                mock.patch.object(server.socket, "getfqdn", side_effect=AssertionError("DNS used")):
            for host in ("127.0.0.1:39001", "[::1]:39001", "localhost:39001", "monitor:39001", "monitor.example:39001"):
                with self.subTest(host=host):
                    self.assertTrue(self.check(host))
                    self.assertFalse(self.check(host, Origin="https://attacker.example"))

    def test_fqdn_aliases_and_foreign_hosts_keep_existing_admission(self):
        with mock.patch.object(server.socket, "gethostname", return_value="monitor"),\
                mock.patch.object(server.socket, "getfqdn", return_value="alias.example"):
            for host, accepted in (("alias.example:39001", True), ("alias:39001", True), ("attacker.example", False)):
                with self.subTest(host=host):
                    self.assertEqual(self.check(host), accepted)

