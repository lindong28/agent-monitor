"""Exercise real HTTP admission; no provider or credential-store access."""
import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest import mock

import codex_accounts
import server


class AccountHttpTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.manager = codex_accounts.Manager(Path(temporary.name) / "profiles")
        patch = mock.patch.object(codex_accounts, "manager", self.manager)
        patch.start()
        self.addCleanup(patch.stop)
        self.http = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        thread = threading.Thread(target=self.http.serve_forever, daemon=True)
        thread.start()
        def close():
            self.http.shutdown()
            self.http.server_close()
            thread.join(2)
            self.manager.close()
        self.addCleanup(close)
        self.host = "127.0.0.1:%s" % self.http.server_port

    def request(self, path="", body=None, headers=None, method=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.http.server_port, timeout=3)
        try:
            default = {"X-Agent-Monitor": "codex-accounts", "Origin": "http://" + self.host, "Content-Type": "application/json"}
            default.update(headers or {})
            default = {k: v for k, v in default.items() if v is not None}
            connection.request(method or ("POST" if body is not None else "GET"), "/api/codex-accounts" + path,
                               body=json.dumps(body) if body is not None else None, headers=default)
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    def test_add_list_and_forget_use_private_store(self):
        status, account = self.request("/add", {"email": "one@example.com", "account_id": "workspace-one"})
        self.assertEqual(status, 200)
        self.assertEqual(self.request()[1]["accounts"][0]["id"], account["id"])
        self.assertEqual(self.request("/forget-login", {"id": account["id"]})[0], 200)
        self.assertEqual(self.request("/start", {"id": "../../outside"})[0], 400)

    def test_cross_origin_missing_header_and_rebinding_are_refused(self):
        for headers in ({"Origin": "https://other.example"}, {"Origin": None},
                        {"X-Agent-Monitor": None}, {"Sec-Fetch-Site": "cross-site"},
                        {"Host": "attacker.example", "Origin": "http://attacker.example"}):
            with self.subTest(headers=headers):
                self.assertEqual(self.request("/add", {"email": "one@example.com"}, headers)[0], 403)
        self.assertEqual(self.request(headers={"X-Agent-Monitor": None})[0], 403)
        self.assertEqual(self.manager.list()["accounts"], [])

    def test_invalid_payloads_do_not_create_profiles_or_leak_errors(self):
        for body in ([], {"email": "invalid"}, {"email": "x" * 4097}):
            self.assertEqual(self.request("/add", body)[0], 400)
        self.assertEqual(self.request("/add", {}, {"Content-Type": "text/plain"})[0], 400)
        with mock.patch.object(self.manager, "list", side_effect=ValueError("SECRET_FIXTURE_TOKEN")):
            status, value = self.request()
        self.assertEqual(status, 500)
        self.assertNotIn("SECRET_FIXTURE_TOKEN", json.dumps(value))
