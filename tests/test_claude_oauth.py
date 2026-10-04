"""`claude_oauth.py` must find the OAuth token on every supported platform.

Claude Code keeps the same credential blob in two different places: a file on
Linux, the login keychain on macOS. A reader that knows only one of them works
on one platform and silently shows nothing on the other — the failure is
invisible because every failure path in that script is deliberately silent.

These tests drive `read_tokens` against both stores. The keychain probe is
exercised through a stub standing in for `/usr/bin/security` rather than a
mocked `subprocess`, so the exit-status and missing-binary paths are the real
ones.
"""

import importlib.util
import json
import os
import stat
import tempfile
import time
import unittest
import urllib.error
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
USAGE_SCRIPT = REPO_ROOT / "claude_oauth.py"


def load_module():
    """A fresh instance of the hyphenated script as an importable module."""
    spec = importlib.util.spec_from_file_location("claude_oauth", USAGE_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def blob(token="sk-ant-oat01-live", expires_in_s=3600):
    """A credentials payload shaped the way Claude Code writes it."""
    return json.dumps(
        {
            "claudeAiOauth": {
                "accessToken": token,
                "expiresAt": int((time.time() + expires_in_s) * 1000),
                "subscriptionType": "max",
            }
        }
    )


class ReadTokenTests(unittest.TestCase):
    def setUp(self):
        self.module = load_module()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        # Point both stores somewhere this test owns. Absent an override each
        # would reach the real host: the developer's own credentials file, and
        # a keychain read that can raise a GUI approval dialog mid-suite.
        self.module.CREDENTIALS = str(self.home / ".credentials.json")
        self.module.KEYCHAIN_TOOL = str(self.home / "absent-security")

    def write_file_store(self, contents):
        Path(self.module.CREDENTIALS).write_text(contents, encoding="utf-8")

    def install_keychain_stub(self, script):
        """Put a stub at KEYCHAIN_TOOL and return the path it logs argv to."""
        tool = self.home / "security"
        argv_log = self.home / "argv.log"
        tool.write_text(
            "#!/bin/sh\n"
            f'printf "%s\\n" "$@" >> "{argv_log}"\n' + script,
            encoding="utf-8",
        )
        tool.chmod(tool.stat().st_mode | stat.S_IXUSR)
        self.module.KEYCHAIN_TOOL = str(tool)
        return argv_log

    def test_reads_live_token_from_credentials_file(self):
        """The Linux store still works — this is the path that shipped."""
        self.write_file_store(blob(token="sk-ant-oat01-from-file"))
        self.assertEqual(list(self.module.read_tokens()), ["sk-ant-oat01-from-file"])

    def test_reads_live_token_from_keychain_when_file_is_absent(self):
        """The macOS store: no credentials file exists there at all."""
        self.install_keychain_stub(
            f"printf '%s' '{blob(token='sk-ant-oat01-from-keychain')}'\n"
        )
        self.assertFalse(os.path.exists(self.module.CREDENTIALS))
        self.assertEqual(list(self.module.read_tokens()), ["sk-ant-oat01-from-keychain"])

    def test_offers_the_keychain_token_even_when_the_file_looks_fresh(self):
        """A file token can be unexpired and still be refused by the server.

        Revoked, superseded, or issued for another account all read as valid
        here, so stopping at the first unexpired token would let a leftover
        file permanently shadow the live keychain entry. Both must be offered,
        file first, and the caller decides by asking.
        """
        self.write_file_store(blob(token="sk-ant-oat01-looks-fine"))
        self.install_keychain_stub(f"printf '%s' '{blob(token='sk-ant-oat01-real')}'\n")
        self.assertEqual(
            list(self.module.read_tokens()),
            ["sk-ant-oat01-looks-fine", "sk-ant-oat01-real"],
        )

    def test_identical_blobs_in_both_stores_yield_one_token(self):
        """Nothing is gained by re-offering a credential already refused."""
        same = blob(token="sk-ant-oat01-same")
        self.write_file_store(same)
        self.install_keychain_stub(f"printf '%s' '{same}'\n")
        self.assertEqual(list(self.module.read_tokens()), ["sk-ant-oat01-same"])

    def test_skips_the_expired_file_token_and_keeps_the_keychain_one(self):
        """An unusable store is dropped, not treated as the answer."""
        self.write_file_store(blob(token="sk-ant-oat01-stale", expires_in_s=-60))
        self.install_keychain_stub(
            f"printf '%s' '{blob(token='sk-ant-oat01-fresh')}'\n"
        )
        self.assertEqual(list(self.module.read_tokens()), ["sk-ant-oat01-fresh"])

    def test_skips_a_malformed_file_and_keeps_the_keychain_one(self):
        self.write_file_store("{ not json")
        self.install_keychain_stub(f"printf '%s' '{blob(token='sk-ant-oat01-ok')}'\n")
        self.assertEqual(list(self.module.read_tokens()), ["sk-ant-oat01-ok"])

    def test_a_token_that_cannot_be_a_header_is_not_a_candidate(self):
        """Rejected here, or urllib raises `ValueError` before sending anything.

        The caller cannot tell that apart from the network being down, so it
        would stop rather than fall through — and the store that does hold a
        working credential would never be reached.
        """
        self.write_file_store(blob(token="sk-ant-oat01\nInjected: header"))
        self.install_keychain_stub(f"printf '%s' '{blob(token='sk-ant-oat01-ok')}'\n")
        self.assertEqual(list(self.module.read_tokens()), ["sk-ant-oat01-ok"])

    def test_a_non_ascii_token_is_not_a_candidate(self):
        self.write_file_store(blob(token="sk-ant-oat01-café"))
        self.assertEqual(list(self.module.read_tokens()), [])

    def test_expired_keychain_token_yields_nothing(self):
        self.install_keychain_stub(f"printf '%s' '{blob(expires_in_s=-1)}'\n")
        self.assertEqual(list(self.module.read_tokens()), [])

    def test_missing_keychain_binary_is_not_an_error(self):
        """The non-macOS case: nothing lives at KEYCHAIN_TOOL."""
        self.assertFalse(os.path.exists(self.module.KEYCHAIN_TOOL))
        self.assertEqual(list(self.module.read_tokens()), [])

    def test_failed_keychain_lookup_yields_nothing(self):
        """No such item in the keychain — `security` exits non-zero."""
        self.install_keychain_stub("echo 'not found' >&2\nexit 44\n")
        self.assertEqual(list(self.module.read_tokens()), [])

    def test_blocked_keychain_read_gives_up_instead_of_hanging(self):
        """A keychain that stops to ask a human must not wedge the refresher.

        The real stall is a GUI approval dialog. `sleep` stands in for it: what
        matters is that the call returns on its own within the timeout rather
        than holding the refresher — and its flock — until the outer deadline.
        """
        self.install_keychain_stub("sleep 30\n")
        self.module.KEYCHAIN_TIMEOUT_S = 1
        started = time.monotonic()
        self.assertEqual(list(self.module.read_tokens()), [])
        self.assertLess(time.monotonic() - started, 10)

    def test_token_never_appears_in_the_keychain_command_line(self):
        """argv is world-readable via `ps`; the secret may only ride stdout."""
        secret = "sk-ant-oat01-must-not-leak"
        argv_log = self.install_keychain_stub(f"printf '%s' '{blob(token=secret)}'\n")
        self.assertEqual(list(self.module.read_tokens()), [secret])
        self.assertNotIn(secret, argv_log.read_text(encoding="utf-8"))

    def test_no_store_yields_nothing(self):
        self.assertEqual(list(self.module.read_tokens()), [])

    def test_an_accepted_first_candidate_never_opens_the_keychain(self):
        """Laziness is the contract, not an optimisation.

        Probing the keychain is what can cost seconds or raise a dialog, so a
        host whose file credential works must never pay for it.
        """
        self.write_file_store(blob(token="sk-ant-oat01-from-file"))
        self.install_keychain_stub(f"printf '%s' '{blob(token='unused')}'\n")
        argv_log = self.home / "argv.log"
        candidates = self.module.read_tokens()
        self.assertEqual(next(candidates), "sk-ant-oat01-from-file")
        self.assertFalse(argv_log.exists(), "keychain was consulted anyway")


class OAuthTransportTests(unittest.TestCase):
    def test_fetch_keeps_token_in_headers_and_refuses_redirect(self):
        import http.server
        import threading
        import urllib.request
        module = load_module()
        observed = []

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                observed.append((self.path, self.headers.get("Authorization")))
                self.send_response(302)
                self.send_header("Location", "/redirected")
                self.end_headers()

            def log_message(self, *args):
                pass

        server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            module.USAGE_URL = "http://127.0.0.1:%d/usage" % server.server_port
            module._OPENER = urllib.request.build_opener(
                urllib.request.ProxyHandler({}), module._RefuseRedirect)
            with self.assertRaises(urllib.error.HTTPError) as failure:
                module.fetch("fixture-only-token")
            self.assertEqual(failure.exception.code, 302)
            self.assertEqual(observed, [("/usage", "Bearer fixture-only-token")])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(2)
        self.assertFalse(thread.is_alive())

    def test_reset_dates_preserve_rounding_and_utc(self):
        module = load_module()
        self.assertEqual(module.to_epoch("2026-01-01T00:00:00.1Z"), 1767225601)
        self.assertEqual(module.to_epoch("2026-01-01T00:00:00"), 1767225600)
        self.assertEqual(module.to_epoch("invalid"), 0)
        self.assertEqual(module.to_epoch(None), 0)


if __name__ == "__main__":
    unittest.main()
