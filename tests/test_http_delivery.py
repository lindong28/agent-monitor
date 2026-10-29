"""Exercise browser cache/encoding contracts on an isolated real HTTP socket."""
import gzip
import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest import mock

import agent_monitor.asset_delivery as asset_delivery
from agent_monitor import http_server as server


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "web"
        self.root.mkdir()
        self.asset = self.root / "app.js"
        self.asset.write_text("const example = 1;\n" * 300)
        self.html = b'<html><head><script src="/web/app.js"></script></head><body>fixture</body></html>'
        (self.root / "index.html").write_bytes(self.html)
        self.patch = mock.patch.object(server, "WEB_ROOT", self.root)
        self.patch.start()
        self.httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.client = http.client.HTTPConnection(*self.httpd.server_address, timeout=3)

    def tearDown(self):
        self.client.close()
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=3)
        self.patch.stop()
        self.temp.cleanup()

    def request(self, path, method="GET", **headers):
        self.client.request(method, path, headers=headers)
        response = self.client.getresponse()
        return response.status, dict(response.getheaders()), response.read()

    def test_html_version_precedes_scripts_and_matches_cache_identity(self):
        version = server._web_signature()
        status, headers, body = self.request("/")
        self.assertEqual(status, 200)
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertLess(body.index(b'"asset-version"'), body.index(b"<script"))
        self.assertIn(f'/web/app.js?v={version}'.encode(), body)
        for suffix, immutable in [("", False), ("?v=old", False), (f"?v={version}", True)]:
            with self.subTest(suffix=suffix):
                _, headers, data = self.request("/web/app.js" + suffix)
                self.assertEqual(data, self.asset.read_bytes())
                self.assertEqual("immutable" in headers["Cache-Control"], immutable)
        self.asset.write_text("const example = 2;\n" * 300)
        self.assertNotEqual(server._web_signature(), version)
        _, headers, data = self.request(f"/web/app.js?v={version}")
        self.assertNotIn("immutable", headers["Cache-Control"])
        self.assertEqual(data, self.asset.read_bytes())

    def test_gzip_head_conditional_get_and_identity_share_keepalive(self):
        version = server._web_signature()
        path = f"/web/app.js?v={version}"
        _, headers, body = self.request(path, **{"Accept-Encoding": "gzip"})
        self.assertEqual(gzip.decompress(body), self.asset.read_bytes())
        self.assertLess(len(body), len(self.asset.read_bytes()))
        self.assertEqual(headers["Vary"], "Accept-Encoding")
        sock = self.client.sock
        _, head, empty = self.request(path, "HEAD", **{"Accept-Encoding": "gzip"})
        self.assertEqual(empty, b"")
        self.assertEqual(head["Content-Length"], str(len(body)))
        status, conditional, empty = self.request(path, **{"Accept-Encoding": "gzip", "If-None-Match": headers["ETag"]})
        self.assertEqual(status, 304)
        self.assertEqual(empty, b"")
        self.assertIn("immutable", conditional["Cache-Control"])
        status, plain, data = self.request(path, **{"Accept-Encoding": "gzip;q=0", "If-None-Match": headers["ETag"]})
        self.assertEqual(status, 200)
        self.assertNotIn("Content-Encoding", plain)
        self.assertNotEqual(plain["ETag"], headers["ETag"])
        self.assertEqual(data, self.asset.read_bytes())
        self.assertIs(self.client.sock, sock)

    def test_overview_compression_preserves_payload_and_no_store(self):
        payload = {"rows": [{"tokens": i, "model": "fixture"} for i in range(300)]}
        with mock.patch.object(self.httpd.views.overview, "read", return_value=payload):
            for accept, encoded in [("gzip", True), ("gzip;q=0.5", True), ("gzip;q=0", False), ("gzip;q=bad", False), ("identity", False)]:
                with self.subTest(accept=accept):
                    status, headers, body = self.request("/api/overview", **{"Accept-Encoding": accept})
                    self.assertEqual(status, 200)
                    self.assertEqual(headers["Cache-Control"], "no-store")
                    self.assertEqual(headers.get("Content-Encoding") == "gzip", encoded)
                    self.assertEqual(json.loads(gzip.decompress(body) if encoded else body), payload)
                    self.assertEqual(headers["Content-Length"], str(len(body)))

    def test_signature_includes_symlink_vendor_edits_rename_and_replacement(self):
        vendor = Path(self.temp.name) / "vendor"
        vendor.mkdir()
        (self.root / "vendor").symlink_to(vendor, target_is_directory=True)
        item = vendor / "chart.js"
        item.write_text("original")
        (vendor / "cycle").symlink_to(self.root, target_is_directory=True)
        first = server._web_signature()
        item.write_text("modified")
        second = server._web_signature()
        self.assertNotEqual(first, second)
        renamed = vendor / "renamed.js"
        item.rename(renamed)
        third = server._web_signature()
        self.assertNotEqual(second, third)
        item.write_text("replaced")
        item.replace(renamed)
        self.assertNotEqual(third, server._web_signature())
        self.assertEqual(asset_delivery.read(renamed), b"replaced")

    def test_concurrent_removal_only_404s_the_requested_resource(self):
        real_read = asset_delivery.read
        for requested in (False, True):
            with self.subTest(requested=requested):
                target = self.root / "obsolete.js"
                target.write_text("obsolete")
                path = "/web/obsolete.js" if requested else "/"
                self.assertEqual(self.request(path)[0], 200)

                def remove_before_read(candidate):
                    if candidate == target:
                        target.unlink()
                    return real_read(candidate)

                with mock.patch.object(asset_delivery, "read", side_effect=remove_before_read):
                    status, _, body = self.request(path)
                self.assertEqual(status, 404 if requested else 200)
                if not requested:
                    self.assertIn(b"fixture", body)
                self.assertEqual(self.request(path)[0], 404 if requested else 200)


class PersistentHTTPTests(unittest.TestCase):
    def test_keepalive_head_conditional_assets_errors_and_post_framing(self):
        httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        client = http.client.HTTPConnection(*httpd.server_address, timeout=3)
        try:
            client.request("GET", "/web/pivot.js")
            response = client.getresponse()
            body = response.read()
            self.assertEqual(response.version, 11)
            self.assertEqual(response.status, 200)
            self.assertIn("app;dur=", response.getheader("Server-Timing"))
            etag = response.getheader("ETag")
            sock = client.sock
            client.request("GET", "/web/pivot.js", headers={"If-None-Match": etag})
            response = client.getresponse()
            self.assertEqual(response.status, 304)
            self.assertEqual(response.read(), b"")
            self.assertIs(client.sock, sock)
            client.request("HEAD", "/web/pivot.js")
            response = client.getresponse()
            self.assertEqual(response.getheader("Content-Length"), str(len(body)))
            self.assertEqual(response.read(), b"")
            client.request("GET", "/missing")
            response = client.getresponse()
            self.assertEqual(response.status, 404)
            response.read()
            client.request("POST", "/missing", body=b"unused-body")
            response = client.getresponse()
            self.assertEqual(response.getheader("Connection"), "close")
            response.read()
            self.assertIsNone(client.sock)
        finally:
            client.close()
            httpd.shutdown()
            httpd.server_close()
            thread.join(3)


if __name__ == "__main__":
    unittest.main()
